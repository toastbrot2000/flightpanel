"""Einstiegspunkt: python -m flightpanel.main

App verbindet alles: Flugzeuge abrufen (adsb.lol, sonst OpenSky), daraus das
Display-Modell bauen (build.py), Statistik führen, Wetter und ISS nachladen,
Geändertes in die Datenbank schreiben (db.py: DATA_DIR/flightpanel.db). web.py liefert
Bilder und Zustand aus, gerendert wird bei jedem Abruf (render.py). Logos und
mitgelieferte Daten liegen in DATA_DIR (Docker: ./data)."""

import asyncio
import logging
import os
import signal
import sys
import time
import traceback
from collections.abc import Mapping
from dataclasses import replace

import aiohttp
from aiohttp import web

from . import USER_AGENT
from .adsbdb import AdsbDb
from .adsbim import AdsbIm
from .adsblol import AdsbLol, Paused
from .airlines import short_airline
from .airport import Recent
from .astro import Iss
from .build import build_model
from .cache import Cache
from .cards import CARDS
from .config import Config, panel_settings_from_env
from .data import load_airlines, load_airports, load_rare
from .db import Db, db_path
from .idle import IdleScreen
from .issues import Issues
from .logos import LogoStore, make_logo_store
from .metar import MetarSource
from .mock import mock_model
from .model import DisplayModel, State
from .opensky import OpenSky
from .origins import Origins
from .render import Renderer
from .stats import Stats
from .storage import read_json
from .web import make_web_app

log = logging.getLogger("main")

SAVE_EVERY_S = 60           # Geändertes in die Datenbank; auch die Nachtaufräumung hängt daran
METAR_EVERY_S = 20 * 60
ISS_EVERY_S = 10 * 60       # Bahndaten einmal am Tag, Überflüge stündlich (astro.py)
LANDED_S = 90               # Landezeit darunter zählt als Bewegung am Platz
DREAM_FALLBACK_MIN = 4 * 60  # ohne QUIET_HOURS wird um 4 Uhr aufgeräumt
OK_STALE_S = 15 * 60        # GET /ok: so lange ohne frische Flugdaten ist ein Problem
OK_ESP32_S = 10 * 60        # ... so lange ohne Abruf des ESP32, wenn er schon da war
OK_EXCEPTION_S = 3600       # ... Fehler im Code in dieser Zeit


def dream_due(cfg: Config, last_day: str | None, now: float) -> bool:
    """Einmal pro Nacht aufräumen (App.dream): im ersten Speicherlauf nach Beginn der
    Ruhezeit, wenn das Panel ohnehin dunkel ist; ohne Ruhezeit nach 4 Uhr."""
    t = time.localtime(now)
    start, end = cfg.quiet_hours
    return (time.strftime("%Y-%m-%d", t) != last_day
            and t.tm_hour * 60 + t.tm_min >= (start if start != end else DREAM_FALLBACK_MIN))


def absorb_legacy(data_dir: str, cache: Cache, issues: Issues, stats: Stats, logos: LogoStore,
                  iss: Iss | None) -> list[str]:
    """Zustand aus den JSON-Dateien früherer Fassungen (bis Oktober 2026) einmalig übernehmen.
    Gibt die übernommenen Dateien zurück; App benennt sie in *.imported um, sobald das
    Übernommene gespeichert ist. Eine unlesbare oder unbrauchbare Datei bleibt, wie sie ist,
    und steht im Log; der Start geht weiter."""
    taken = []
    for name, store in (("cache.json", cache), ("issues.json", issues), ("stats.json", stats),
                        (os.path.join("logos", ".missing.json"), logos)):
        path = os.path.join(data_dir, name)
        if not os.path.exists(path):
            continue
        raw = read_json(path)
        if not isinstance(raw, dict):                   # unlesbar (read_json hat es geloggt) oder kein Objekt
            log.warning("%s nicht übernommen, bleibt liegen", name)
            continue
        try:
            store.load_legacy(raw)
        except Exception as e:                          # von Hand verändert: lieber ohne als gar nicht starten
            log.warning("%s nicht übernommen (%s), bleibt liegen", name, e)
            continue
        taken.append(path)
    path = os.path.join(data_dir, "tle.txt")
    if iss is not None and os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as f:
                if not iss.set_tle_text(f.read(), os.path.getmtime(path)):
                    raise ValueError("keine Bahndaten darin")
        except Exception as e:                          # kein Text oder keine Bahndaten darin
            log.warning("tle.txt nicht übernommen (%s), bleibt liegen", e)
        else:
            taken.append(path)
    if taken:
        log.info("Aus früheren Fassungen übernommen: %s", ", ".join(os.path.relpath(p, data_dir) for p in taken))
    return taken


def check_data_dir(data_dir: str) -> str | None:
    """Kann der Server in DATA_DIR schreiben? Gibt den Satz fürs Log zurück, sonst None. Der Container läuft als
    Benutzer 1000: Ein ./data, das ein früherer Container als root angelegt hat, gehört root, und der Start stürbe
    mit nichts als "unable to open database file", bei restart: unless-stopped in einer Schleife. Geprüft werden
    DATA_DIR, logos/ und die Datenbank, soweit sie schon da sind; fehlt DATA_DIR ganz, legt der Start es an."""
    for path, mode in ((data_dir, os.W_OK | os.X_OK), (os.path.join(data_dir, "logos"), os.W_OK | os.X_OK),
                       (db_path(data_dir), os.W_OK)):
        if os.path.exists(path) and not os.access(path, mode):
            return (f"{path} ist nicht beschreibbar (der Container läuft als Benutzer 1000). "
                    "Auf dem Host im Projektordner: sudo chown -R 1000:1000 data")
    return None


class App:
    """Muss in der laufenden Event-Loop entstehen (aiohttp-Session). Die Datenbankverbindung gehört dem Thread
    der Loop: Nichts, was in asyncio.to_thread läuft, darf sie benutzen."""

    def __init__(self, cfg: Config, env: Mapping[str, str] | None = None):
        """cfg: die Server-Einstellungen (Config.from_env). Das Panel am LAN-Port (LAN_PANEL)
        bekommt dazu seine Einstellungen aus der Datenbank; eine ältere .env mit Standort und
        Sichtfeld (env, sonst os.environ) wird beim ersten Start einmalig übernommen."""
        self.server_cfg = cfg
        data = cfg.data_dir
        self.db = Db(db_path(data))
        leftover = panel_settings_from_env(os.environ if env is None else env)
        if self.db.panel_get(cfg.lan_panel) is None:
            Config.for_panel(cfg, leftover)     # prüft: Unsinn in der alten .env bricht ab, ohne gespeichert zu werden
            self.db.panel_add(cfg.lan_panel, settings=leftover)
            if leftover:
                log.info("Panel-Einstellungen aus .env übernommen: %s", ", ".join(sorted(leftover)))
        elif leftover:
            log.warning("%s in .env gilt nicht mehr, Panel-Einstellungen liegen in der Datenbank. Ändern mit: "
                        "python -m flightpanel.panels set %s KEY=WERT", ", ".join(sorted(leftover)), cfg.lan_panel)
        skipped: list[tuple[str, str]] = []
        self.cfg = cfg = Config.for_panel(cfg, self.db.panel_get(cfg.lan_panel)["settings"], skipped)
        self.session = aiohttp.ClientSession(headers={"User-Agent": USER_AGENT})
        self.issues = Issues(self.db)
        if skipped:
            self._note_skipped(cfg.lan_panel, skipped)
        self.cache = Cache(self.db)
        self.names = load_airlines(data)

        # Quellen
        self.adsblol = AdsbLol(self.session)
        self.opensky = OpenSky(self.session, cfg.opensky_client_id, cfg.opensky_client_secret)
        self.adsb = AdsbDb(self.session, cfg.adsbdb_base, self.cache, self.issues)
        self.adsbim = AdsbIm(self.session, self.cache, self.issues) if cfg.route_adsbim else None
        self.origins = (Origins(self.session, self.cache, load_airports(data), self.issues,
                                auth=None if self.opensky.anonymous else self.opensky.auth_headers)
                        if cfg.track_origin else None)
        self.metar = MetarSource(self.session, cfg.metar_station, self.issues) if cfg.metar else None
        self.iss = Iss(cfg, self.db, self.session, self.issues) if cfg.iss else None

        # Anzeige
        self.logos = make_logo_store(cfg, self.session, self.db)
        self.rare = load_rare(data, self.names)
        self.stats = Stats(self.db, cfg.lan_panel, cfg, self.names, self.rare)

        # Alte JSON-Dateien einmalig übernehmen. Das muss vor dem Modell geschehen: Dessen Landerichtung
        # zu Beginn kommt aus der Statistik.
        taken = absorb_legacy(data, self.cache, self.issues, self.stats, self.logos, self.iss)
        if taken:
            # Erst gespeichert, dann umbenannt: ein Absturz dazwischen verliert nichts.
            self.save()
            for path in taken:
                os.replace(path, path + ".imported")

        self.idle = IdleScreen(cfg, CARDS, stats=self.stats, metar=self.metar, astro=self.iss, logos=self.logos,
                               names=self.names)
        self.renderer = Renderer(cfg, self.logos, idle=self.idle)
        self.model = DisplayModel(empty=True, source="startup", landing_course=self.stats.course)

        # Zustand
        self.started = time.time()
        self.active_source = cfg.source         # woher der letzte Abruf kam
        self.adsblol_failing = False
        self.opensky_last_try = 0.0
        self.last_model_ok = 0.0
        self.last_airport_move = 0.0            # letzter Start oder letzte Landung am Platz
        self.recent = Recent()                  # wer zuletzt hier landete oder startete
        self.last_frame_fetch = 0.0             # setzt web.py bei jedem /frame
        self.frame_count = 0
        self.counters = {"polls": 0, "ok": 0, "adsblol": 0, "opensky": 0}
        self.last_dream: dict | None = None     # was das letzte nächtliche Aufräumen getan hat
        self._dream_day: str | None = None
        self._stop = asyncio.Event()
        self._tasks: list[asyncio.Task] = []

    def _note_skipped(self, pid: str, skipped: list[tuple[str, str]]) -> None:
        """Gespeicherte Panel-Einstellungen, die nicht (mehr) gelten: Eine spätere Fassung kennt sie nicht oder prüft
        strenger, oder jemand hat die Datenbank von Hand geändert. Der Server startet mit dem Rest, statt in einer
        Neustartschleife zu hängen; Log und /issues nennen sie und einen Befehl für alle zusammen (panels prüft beim
        Zurücksetzen des einen auch die anderen, einzeln ginge es nicht)."""
        fix = f"python -m flightpanel.panels unset {pid} {' '.join(key for key, _ in skipped)}"
        log.warning("Panel %s: übergangen, es gilt der Default: %s. Beheben: %s, danach Gewolltes neu setzen "
                    "(set %s KEY=WERT)", pid, "; ".join(why for _, why in skipped), fix, pid)
        for key, why in skipped:
            self.issues.note("setting", f"{pid} {key}", error=why, fix=fix)

    # ------------------------------------------------------------ Abrufen --
    def poll_interval(self) -> int:
        """Abstand zwischen zwei Abrufen. adsb.lol hat kein Tageslimit: POLL_INTERVAL,
        auch wenn es gerade ausfällt (OpenSky springt dann seltener ein, siehe
        fetch_states). Nur mit SOURCE=opensky gilt dessen Budget."""
        if self.cfg.mock or self.cfg.source == "adsblol":
            return self.cfg.poll_interval
        return self.opensky_interval()

    def refresh_period(self) -> int:
        """Wie oft das Modell tatsächlich neu wird: bei ausgefallenem adsb.lol so
        selten, wie OpenSky einspringen darf. Für Vorschau und Stale-Schwellen."""
        if self.cfg.source == "adsblol" and self.adsblol_failing:
            return self.opensky_interval()
        return self.poll_interval()

    def opensky_interval(self) -> int:
        """Mindestabstand zwischen OpenSky-Abrufen: nie kürzer als POLL_INTERVAL,
        aber so lang, dass das Tagesbudget für alle Stunden außerhalb der Quiet
        Hours reicht (anonym: 400 Credits -> rund alle 2,5 min). Wird es trotzdem
        knapp, doppelt so lang."""
        budget = self.opensky.daily_credits
        need = self.cfg.active_seconds_per_day() / (budget * 0.95)
        interval = max(self.cfg.poll_interval, int(need + 0.999))
        rem = self.opensky.credits_remaining
        if rem is not None and rem < budget * 0.1:
            interval *= 2
        return interval

    async def fetch_states(self) -> list[State]:
        """adsb.lol, wenn gewählt. Fällt es aus, springt OpenSky ein, aber nur so
        oft, wie dessen Tagesbudget erlaubt; adsb.lol wird weiter bei jedem Abruf
        probiert, ein kurzer Aussetzer kostet also nur einen Abruf."""
        if self.cfg.source == "adsblol":
            try:
                states = await self.adsblol.fetch(self.cfg.home_lat, self.cfg.home_lon, self.cfg.fetch_radius_km)
                self.active_source = "adsblol"
                self.adsblol_failing = False
                return states
            except Exception as e:
                self.adsblol_failing = True
                err = self.adsblol.last_error or "?"
                if not isinstance(e, Paused):     # die Pause selbst ist kein neuer Fehler
                    self.issues.source_error("adsblol", err)
                wait = self.opensky_interval() - (time.time() - self.opensky_last_try)
                if wait > 0:
                    raise RuntimeError(f"adsb.lol: {err}, OpenSky erst in {wait:.0f} s") from e
                log.warning("adsb.lol: %s, nehme OpenSky", err)
                self.issues.note("fallback", "opensky", reason=err)
        self.opensky_last_try = time.time()
        self.active_source = "opensky"
        return await self.opensky.fetch(self.cfg.bbox)

    async def poll_loop(self) -> None:
        while not self._stop.is_set():
            t0 = time.time()
            if self._quiet_hours(t0):
                await asyncio.sleep(60)
                continue
            self.counters["polls"] += 1
            try:
                await self._poll(t0)
                self.last_model_ok = t0
                self.counters["ok"] += 1
            except Exception as e:
                self._note_poll_error(e)
                self._age_model()
            await asyncio.sleep(max(5, self.poll_interval() - (time.time() - t0)))

    def _quiet_hours(self, now: float) -> bool:
        """Ruhezeit (QUIET_HOURS): Panel schwarz, nichts abrufen. Das letzte Lagebild
        gilt dann nicht mehr: Flüge weg, Daten als alt markiert, sonst zeigt die
        Vorschau bis zum Morgen dieselben Flüge."""
        if self.cfg.mock or self.cfg.brightness(now) > 0:
            return False
        if not self.model.stale:
            self.model = replace(self.model, flights=[], empty=True, stale=True)
        return True

    async def _poll(self, t0: float) -> None:
        """Ein Abruf: Flugzeuge holen, Modell bauen, Statistik nachführen."""
        if self.cfg.mock:
            self.model = mock_model(self.cfg, seed=int(t0 // 20))
            self.stats.observe([], self.model, t0)
        else:
            states = await self.fetch_states()
            self.model = await build_model(self.cfg, states, self.adsb, self.names,
                                           horizon_s=self.refresh_period(), prev_course=self.model.landing_course,
                                           issues=self.issues, adsbim=self.adsbim, origins=self.origins,
                                           source=self.active_source, recent=self.recent, rare=self.rare)
            self.counters[self.active_source] += 1
            if self.model.departures or any(a["eta_s"] <= LANDED_S for a in self.model.arrivals):
                self.last_airport_move = t0
            self.model.last_airport_move = self.last_airport_move
            await self._observe(states, t0)
            log.info("%s: %d empfangen, %d im Radius, %d im Sichtfeld, angezeigt: %s. Nächster Abruf in %d s",
                     self.active_source, len(states), len(self.model.nearby),
                     sum(1 for n in self.model.nearby if n["in_view"] != "no"),
                     ", ".join(f"{f.callsign}#{f.seq}" if f.seq else f.callsign for f in self.model.flights) or "-",
                     self.poll_interval())
        for f in self.model.flights:
            if f.kind == "airline":
                self.logos.ensure(f.airline_iata, f.airline_icao)   # holt fehlende Logos im Hintergrund

    def _age_model(self) -> None:
        """Ohne frische Daten erst dimmen, dann leeren. Die Schwellen sind nie kürzer
        als ein paar Abrufintervalle, sonst dimmt das Bild bei langsamem (anonymem)
        Abruf nach jedem Fehlversuch."""
        age = time.time() - self.last_model_ok
        period = self.refresh_period()
        if age > max(self.cfg.empty_after, 4 * period):
            if not self.model.empty:
                self.issues.note("stale", "empty", age_s=round(age))
            self.model = replace(self.model, flights=[], empty=True, stale=True)
        elif age > max(self.cfg.stale_after, 2 * period):
            if not self.model.stale:
                self.issues.note("stale", "dimmed", age_s=round(age))
            self.model = replace(self.model, stale=True)

    async def _observe(self, states: list[State], t0: float) -> None:
        """Statistik nachführen; neue Airlines bekommen ihr Logo, unbekannte Betreiber
        ihren Namen von adsbdb. Ein Fehler hier darf den Abruf nicht kosten."""
        try:
            for code in self.stats.observe(states, self.model, t0):
                if code in self.names:
                    self.logos.ensure(self.names[code]["iata"], code)
                else:
                    cs = next((s.callsign for s in states if s.callsign.startswith(code)), "")
                    route = await self.adsb.route(cs) if cs else {}
                    name = route.get("airline", "") or await self.adsb.airline(code)
                    self.stats.set_name(code, short_airline(name, code, self.names))
        except Exception as e:
            self._note_poll_error(e)

    def _note_poll_error(self, e: Exception) -> None:
        """Netz- und Quellenfehler als source_error/rate_limit, alles andere ist
        ein Bug: als exception mit Datei und Zeile, im Log mit Traceback."""
        msg = str(e) or type(e).__name__
        if isinstance(e, (aiohttp.ClientError, asyncio.TimeoutError, RuntimeError)):
            log.warning("Abruf fehlgeschlagen: %s", msg)
            if not msg.startswith("adsb.lol"):      # adsb.lol-Ausfälle zählt fetch_states schon
                self.issues.source_error("opensky" if "OpenSky" in msg else self.active_source, msg)
            return
        log.exception("Abruf fehlgeschlagen: %s", msg)
        tb = traceback.extract_tb(e.__traceback__)
        where = f"{os.path.basename(tb[-1].filename)}:{tb[-1].lineno}" if tb else "?"
        self.issues.note("exception", f"{type(e).__name__} {where}", error=msg[:300])

    # ---------------------------------------------------- Wetter, ISS, Platte --
    async def metar_loop(self) -> None:
        """Alle 20 min das METAR; in den Quiet Hours nur, solange noch keins da ist."""
        while not self._stop.is_set():
            if self.cfg.brightness(time.time()) > 0 or self.metar.current is None:
                try:
                    await self.metar.refresh()
                except Exception as e:
                    log.warning("METAR: %s", e)
            await asyncio.sleep(METAR_EVERY_S)

    async def astro_loop(self) -> None:
        while not self._stop.is_set():
            now = time.time()
            try:
                if self.iss.needs_tle(now):
                    await self.iss.refresh_tle()
                await self.iss.update_passes(now)
            except Exception as e:
                log.warning("ISS: %s", e)
            await asyncio.sleep(ISS_EVERY_S)

    async def save_loop(self) -> None:
        while not self._stop.is_set():
            await asyncio.sleep(SAVE_EVERY_S)
            try:
                if dream_due(self.cfg, self._dream_day, time.time()):
                    self.dream(time.time())
                self.save()
            except Exception as e:
                log.warning("Speichern: %s", e)

    def save(self) -> None:
        """Geändertes in die Datenbank, als eine Transaktion."""
        with self.db.transaction():
            self.cache.save()
            self.issues.save()
            self.stats.save()

    def dream(self, now: float) -> None:
        """Nachts aufräumen ("träumen"), damit der Server auch nach einem Jahr am Stück
        so läuft wie am ersten Tag: Einmaliges und Vergangenes in /issues vergessen
        (Issues.forget), Abgelaufenes aus dem Cache. Kostet Millisekunden."""
        forgotten = self.issues.forget(now)
        pruned = self.cache.prune()
        self._dream_day = time.strftime("%Y-%m-%d", time.localtime(now))
        self.last_dream = {"at": time.strftime("%Y-%m-%d %H:%M", time.localtime(now)),
                           "issues_forgotten": forgotten, "cache_expired": pruned, "cache_entries": len(self.cache)}
        log.info("Nachts aufgeräumt: %d Auffälligkeiten vergessen, %d Cache-Einträge abgelaufen, %d bleiben",
                 forgotten, pruned, len(self.cache))

    # ------------------------------------------------------------- Zustand --
    def issues_report(self) -> dict:
        return self.issues.report(uptime_h=round((time.time() - self.started) / 3600, 1),
                                  polls_since_start=self.counters, logos_missing=sorted(self.logos.missing()))

    def problems(self, now: float) -> list[str]:
        """Was gerade nicht stimmt, für GET /ok (Docker-Healthcheck, Uptime-Monitor);
        leer heißt alles gut. In der Ruhezeit sind alte Daten und ein dunkles Panel normal."""
        out = []
        if self.cfg.brightness(now) > 0:
            age = now - (self.last_model_ok or self.started)
            if age > OK_STALE_S:
                out.append(f"keine frischen Flugdaten seit {age / 60:.0f} min")
            if self.frame_count and now - self.last_frame_fetch > OK_ESP32_S:
                out.append(f"ESP32 holt seit {(now - self.last_frame_fetch) / 60:.0f} min keine Bilder")
        out += [f"Fehler im Code: {it['key']}" for it in self.issues.items.values()
                if it["kind"] == "exception" and now - it["last"] < OK_EXCEPTION_S]
        return out

    def health(self) -> dict:
        """Für GET /health: Quellen, Modell, Logos, ESP32."""
        now = time.time()
        esp_age = now - self.last_frame_fetch if self.last_frame_fetch else None
        cfg = self.cfg
        return {
            "ok": True,
            "problems": self.problems(now),
            "mock": cfg.mock,
            "layout": cfg.layout,
            "calm": cfg.calm,
            "display": {"page_seconds": cfg.page_seconds, "flight_seconds": cfg.flight_seconds,
                        "idle_seconds": cfg.idle_seconds, "blink": cfg.blink, "info_pages": list(cfg.info_pages),
                        "cards_off": sorted(cfg.cards_off)},
            "brightness_now": self.renderer.brightness(now),
            "model_age_s": round(now - self.last_model_ok) if self.last_model_ok else None,
            "model_stale": self.model.stale,
            "flights": len(self.model.flights),
            "source": None if cfg.mock else {
                "configured": cfg.source,
                "active": self.active_source,
                "poll_interval_s": self.poll_interval(),
                "adsblol_last_ok_age_s": round(now - self.adsblol.last_ok) if self.adsblol.last_ok else None,
                "adsblol_last_error": self.adsblol.last_error,
            },
            "opensky": None if cfg.mock else {
                "anonymous": self.opensky.anonymous,
                "min_interval_s": self.opensky_interval(),
                "last_ok_age_s": round(now - self.opensky.last_ok) if self.opensky.last_ok else None,
                "credits_remaining": self.opensky.credits_remaining,
                "last_error": self.opensky.last_error,
            },
            "adsbdb_last_error": self.adsb.last_error,
            "adsbim_last_error": self.adsbim.last_error if self.adsbim else None,
            "track_last_error": self.origins.last_error if self.origins else None,
            "cache_entries": len(self.cache),
            "issues": self.issues.report()["counts"],
            "dream": self.last_dream,
            "metar": None if not self.metar else {
                "age_s": self.metar.age_s(now),
                "last_error": self.metar.last_error,
                "raw": self.metar.current.raw if self.metar.current else None,
            },
            "iss": None if not self.iss else {
                "usable": self.iss.usable(now),
                "tle_fetched_age_h": round((now - self.iss.tle_fetched) / 3600, 1) if self.iss.tle_fetched else None,
                "last_error": self.iss.last_error,
                "visible_passes_24h": len(self.iss.passes),
            },
            "idle_card": self.idle.last_pick,
            "logos": self.logos.available(),
            "logos_missing": sorted(self.logos.missing()),
            "esp32": {
                "last_fetch_age_s": round(esp_age) if esp_age is not None else None,
                "fetches": self.frame_count,
                "alive": esp_age is not None and esp_age < 300,
            },
        }

    # ----------------------------------------------------------- Lebenslauf --
    async def start(self) -> web.AppRunner:
        if not self.cfg.mock and self.cfg.source == "opensky" and self.opensky.anonymous:
            log.warning("Keine OpenSky-Zugangsdaten: anonymer Zugriff (400 Credits/Tag), "
                        "Abruf nur alle %d s. Mit OPENSKY_CLIENT_ID/SECRET alle %d s.",
                        self.poll_interval(), self.cfg.poll_interval)
        runner = web.AppRunner(make_web_app(self), access_log=None)
        await runner.setup()
        await web.TCPSite(runner, "0.0.0.0", self.cfg.http_port).start()
        loops = [self.poll_loop(), self.save_loop()]
        if self.metar and not self.cfg.mock:
            loops.append(self.metar_loop())
        if self.iss and not self.cfg.mock:
            loops.append(self.astro_loop())
        self._tasks = [asyncio.create_task(c) for c in loops]
        for t in self._tasks:
            t.add_done_callback(self._task_ended)
        log.info("flightpanel läuft auf :%d (mock=%s, layout=%s, %dx%d)",
                 self.cfg.http_port, self.cfg.mock, self.cfg.layout, self.cfg.width, self.cfg.height)
        return runner

    def _task_ended(self, task: asyncio.Task) -> None:
        """Eine Schleife ist abgestürzt (sie fangen ihre Fehler sonst selbst): ins Log
        und nach /issues, statt still nichts mehr zu tun."""
        if task.cancelled() or task.exception() is None:
            return
        e = task.exception()
        log.error("Hintergrundschleife beendet: %s", e, exc_info=e)
        self.issues.note("exception", f"loop {task.get_coro().__name__}", error=(str(e) or type(e).__name__)[:300])

    async def stop(self, runner: web.AppRunner) -> None:
        self._stop.set()
        for t in self._tasks:
            t.cancel()
        self.save()
        await runner.cleanup()
        await self.session.close()
        self.db.close()


async def _main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    cfg = Config.from_env()             # Warnungen zur Konfiguration schon im richtigen Format
    logging.getLogger().setLevel(getattr(logging, cfg.log_level.upper(), logging.INFO))
    if problem := check_data_dir(cfg.data_dir):
        log.error("%s", problem)
        sys.exit(2)
    app = App(cfg)
    runner = await app.start()
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop.set)
        except NotImplementedError:     # Windows
            pass
    await stop.wait()
    await app.stop(runner)


def main() -> None:
    asyncio.run(_main())


if __name__ == "__main__":
    main()
