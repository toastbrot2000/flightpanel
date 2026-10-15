"""Einstiegspunkt: python -m flightpanel.main

App verbindet alles: Flugzeuge abrufen (adsb.lol, sonst OpenSky), einmal für alle Panels in einem Umkreis um alle
(panel.py), daraus je Panel das Display-Modell bauen (build.py), Statistik führen, Wetter und ISS nachladen,
Geändertes in die Datenbank schreiben (db.py: DATA_DIR/flightpanel.db). Was die Panels teilen (Quellen,
Zwischenspeicher, Logos, Wetter, ISS-Bahndaten, der Flughafen: airfield.py), hält App einmal. Die Panels und ihre
Einstellungen kommen aus der Datenbank, auch im laufenden Betrieb (panels.py). web.py liefert Bilder und Zustand
des Panels am LAN-Port aus, gerendert wird bei jedem Abruf (render.py). Logos und mitgelieferte Daten liegen in
DATA_DIR (Docker: ./data).

Nur mit den Schaltern für den Zugang von außen (Config.public) lädt start() access.py und öffnet PUBLIC_PORT
(web.make_public_app) mit dem Türsteher davor (access.Gate); dazu halten zwei Schleifen die Schlüssel von Access
aktuell (keys_loop) und prüfen täglich, ob die Vertrauensliste der Firmware noch zum Zertifikat passt (tls_loop,
selfcheck.py). Ohne die Schalter läuft nichts davon."""

import asyncio
import logging
import os
import shlex
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
from .airfield import Airfield
from .airlines import short_airline
from .astro import STARLINK_URL, Catalog
from .build import build_model
from .cache import Cache
from .config import Config, panel_settings_from_env, too_far
from .data import load_airlines, load_airports, load_rare
from .db import COPY, Aside, Db, db_path
from .issues import Issues, error_text
from .logos import LogoStore, make_logo_store
from .metar import MetarSource
from .mock import mock_model
from .model import State
from .opensky import OpenSky
from .origins import Origins
from .panel import Panel, active_seconds, fetch_circle, fw_channel, regions, union_bbox
from .stats import Stats
from .storage import read_json
from .web import make_public_app, make_web_app

log = logging.getLogger("main")

SAVE_EVERY_S = 60           # Geändertes in die Datenbank; auch die Nachtaufräumung hängt daran
KEYS_EVERY_S = 24 * 3600    # Zugang von außen: die Schlüssel von Access einmal am Tag neu
METAR_EVERY_S = 20 * 60
ISS_EVERY_S = 10 * 60       # Bahndaten einmal am Tag, Überflüge stündlich (astro.py)
REST_S = 60                 # ruhen alle Panels: so oft nachsehen, auch nach Änderungen aus panels.py
DREAM_FALLBACK_MIN = 4 * 60  # ohne QUIET_HOURS wird um 4 Uhr aufgeräumt
OK_STALE_S = 15 * 60        # GET /ok: so lange ohne frische Flugdaten ist ein Problem
OK_ESP32_S = 10 * 60        # ... so lange ohne Abruf des ESP32, wenn er schon da war
OK_EXCEPTION_S = 3600       # ... Fehler im Code in dieser Zeit
OK_REFUSED_S = 10 * 60      # ... eine 421 am LAN-Port in dieser Zeit und danach kein Abruf des Panels


def dream_due(cfg: Config, last_day: str | None, now: float) -> bool:
    """Einmal pro Nacht aufräumen (App.dream): im ersten Speicherlauf nach Beginn der
    Ruhezeit, wenn das Panel ohnehin dunkel ist; ohne Ruhezeit nach 4 Uhr. cfg: das Panel am LAN-Port
    (App.save_loop); aufgeräumt wird in der Nacht dessen, dem der Server gehört. Es kostet Millisekunden und stört
    kein anderes Panel."""
    t = time.localtime(now)
    start, end = cfg.quiet_hours
    return (time.strftime("%Y-%m-%d", t) != last_day
            and t.tm_hour * 60 + t.tm_min >= (start if start != end else DREAM_FALLBACK_MIN))


def absorb_legacy(data_dir: str, cache: Cache, issues: Issues, stats: Stats, airfield: Airfield, logos: LogoStore,
                  catalog: Catalog | None) -> list[str]:
    """Zustand aus den JSON-Dateien früherer Fassungen (bis Oktober 2026) einmalig übernehmen. Aus stats.json nimmt
    Stats Album und Statistik, Airfield die Landerichtung.
    Gibt die übernommenen Dateien zurück; App benennt sie in *.imported um, sobald das
    Übernommene gespeichert ist. Eine unlesbare oder unbrauchbare Datei bleibt, wie sie ist,
    und steht im Log; der Start geht weiter."""
    taken = []
    for name, stores in (("cache.json", (cache,)), ("issues.json", (issues,)), ("stats.json", (stats, airfield)),
                         (os.path.join("logos", ".missing.json"), (logos,))):
        path = os.path.join(data_dir, name)
        if not os.path.exists(path):
            continue
        raw = read_json(path)
        if not isinstance(raw, dict):                   # unlesbar (read_json hat es geloggt) oder kein Objekt
            log.warning("%s nicht übernommen, bleibt liegen", name)
            continue
        try:
            for store in stores:                        # Stats zuerst: Wirft es, nimmt auch Airfield nichts
                store.load_legacy(raw)
        except Exception as e:                          # von Hand verändert: lieber ohne als gar nicht starten
            log.warning("%s nicht übernommen (%s), bleibt liegen", name, e)
            continue
        taken.append(path)
    path = os.path.join(data_dir, "tle.txt")
    if catalog is not None and os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as f:
                if not catalog.set_tle_text(f.read(), os.path.getmtime(path)):
                    raise ValueError("keine Bahndaten darin")
        except Exception as e:                          # kein Text oder keine Bahndaten darin
            log.warning("tle.txt nicht übernommen (%s), bleibt liegen", e)
        else:
            taken.append(path)
    if taken:
        log.info("Aus früheren Fassungen übernommen: %s", ", ".join(os.path.relpath(p, data_dir) for p in taken))
    return taken


def check_data_dir(data_dir: str) -> str | None:
    """Kann der Server in DATA_DIR schreiben? Gibt den Satz fürs Log zurück, sonst None. Der Container läuft nicht
    als root, sondern als PUID:PGID aus der .env (compose.yml, ohne Angabe 1000:1000): Ein ./data, das ein früherer
    Container als root angelegt hat oder das einem anderen Benutzer gehört, ist ihm verschlossen, und der Start stürbe
    mit nichts als "unable to open database file", bei restart: unless-stopped in einer Schleife. Geprüft werden
    DATA_DIR, logos/ und die Datenbank, soweit sie schon da sind; fehlt DATA_DIR ganz, legt der Start es an."""
    for path, mode in ((data_dir, os.W_OK | os.X_OK), (os.path.join(data_dir, "logos"), os.W_OK | os.X_OK),
                       (db_path(data_dir), os.W_OK)):
        if os.path.exists(path) and not os.access(path, mode):
            return _not_writable(path)
    return None


def _not_writable(path: str) -> str:
    """Der Satz fürs Log: wer der Server ist und was zu tun ist. Der Weg der Wahl ist PUID und PGID in .env auf den
    Besitzer von data/ (compose.yml). Ein chown auf den Benutzer des Servers ist es nicht: Ohne PUID und PGID ist das
    1000, und ist der Benutzer des Hosts ein anderer, machte es die mitgelieferten Dateien in data/ für das git pull
    von tools/update.sh unbeschreibbar. chown steht darum nur für Reste von root (frühere Container) und auf den
    Benutzer des Hosts, wie im README; docker run ohne compose, wo es kein PUID gibt, bekommt --user."""
    fix = "Auf Linux in .env PUID und PGID auf den Besitzer von data/ setzen (id -u, id -g), dann docker compose up -d"
    if not hasattr(os, "getuid"):                   # Windows ohne Docker: keine Benutzer-IDs
        return f"{path} ist nicht beschreibbar. {fix}"
    who = f"{os.getuid()}:{os.getgid()}"
    chown = 'sudo chown -R "$(id -u):$(id -g)" data'        # der Benutzer des Hosts, nicht der des Servers (who)
    return (f"{path} ist nicht beschreibbar (der Server läuft als Benutzer {who}). {fix}. Gehört in data/ noch etwas "
            f"root (früherer Container): einmal {chown}. Ohne compose gehört --user UID:GID zu docker run")


def _seen(p: Panel) -> str:
    """Was ein Panel aus einem Abruf macht, für die Zeile im Log."""
    m = p.model
    shown = ", ".join(f"{f.callsign}#{f.seq}" if f.seq else f.callsign for f in m.flights) or "-"
    in_view = sum(1 for n in m.nearby if n["in_view"] != "no")
    return f"{p.id}: {len(m.nearby)} im Radius, {in_view} im Sichtfeld, angezeigt: {shown}"


class App:
    """Muss in der laufenden Event-Loop entstehen (aiohttp-Session). Die Datenbankverbindung gehört dem Thread
    der Loop: Nichts, was in asyncio.to_thread läuft, darf sie benutzen."""

    def __init__(self, cfg: Config, env: Mapping[str, str] | None = None):
        """cfg: die Server-Einstellungen (Config.from_env). Die Panels bekommen dazu ihre Einstellungen aus der
        Datenbank. Das am LAN-Port (LAN_PANEL) legt der erste Start an, mit Standort und Sichtfeld aus einer
        älteren .env (env, sonst os.environ), einmalig."""
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
                        "python -m flightpanel.panels set %s KEY=WERT. Die Zeilen aus .env entfernen, dann bleibt "
                        "die Warnung weg", ", ".join(sorted(leftover)), cfg.lan_panel)
        self.session = aiohttp.ClientSession(headers={"User-Agent": USER_AGENT})
        self.issues = Issues(self.db)
        if self.db.aside:
            self._note_aside(self.db.aside)
        self.cache = Cache(self.db)
        self.names = load_airlines(data)

        # Quellen, für alle Panels gemeinsam
        self.adsblol = AdsbLol(self.session)
        self.opensky = OpenSky(self.session, cfg.opensky_client_id, cfg.opensky_client_secret)
        self.adsb = AdsbDb(self.session, cfg.adsbdb_base, self.cache, self.issues)
        self.adsbim = AdsbIm(self.session, self.cache, self.issues) if cfg.route_adsbim else None
        self.origins = (Origins(self.session, self.cache, load_airports(data), self.issues,
                                auth=None if self.opensky.anonymous else self.opensky.auth_headers)
                        if cfg.track_origin else None)
        self.metars: dict[str, MetarSource] = {}       # je METAR-Station (metar_for)
        self.catalog = Catalog(self.db, self.session, self.issues, trains_url=STARLINK_URL) if cfg.iss else None

        # Anzeige, für alle Panels gemeinsam, und je Flughafen (airfield_for)
        self.logos = make_logo_store(cfg, self.session, self.db)
        self.rare = load_rare(data, self.names)
        self.airfields: dict[str, Airfield] = {}

        # Zustand
        self.started = time.time()
        self.active_source = cfg.source         # woher der letzte Abruf kam
        self.adsblol_failing = False
        self.opensky_last_try = 0.0
        self.last_model_ok = 0.0                # letzter gelungener Abruf, für alle Panels
        self.counters = {"polls": 0, "ok": 0, "fetches": 0, "adsblol": 0, "opensky": 0}
        self.refused = 0                        # Anfragen am LAN-Port, die web.lan_guard mit 421 abwies: nur die Zahl,
        self.refused_at = 0.0                   # und wann die letzte kam; der Host selbst wird nie gemerkt
        self.last_dream: dict | None = None     # was das letzte nächtliche Aufräumen getan hat
        self._dream_day: str | None = None
        self._stop = asyncio.Event()
        self._tasks: list[asyncio.Task] = []

        # Zugang von außen: entsteht erst in start(), und nur mit den Schaltern (_start_public)
        self.keys = None                        # access.KeySet
        self.gate = None                        # access.Gate
        self.tls = None                         # selfcheck.TlsCheck
        self._public: web.AppRunner | None = None

        # Die Panels: das am LAN-Port und die mit panels add angelegten
        self.panels: dict[str, Panel] = {}
        self._sync_panels()

        # Alte JSON-Dateien einmalig übernehmen: Album und Statistik ins Panel am LAN-Port, die Landerichtung an
        # den Flughafen (Startwert des ersten Abrufs)
        taken = absorb_legacy(data, self.cache, self.issues, self.lan.stats, self.airfield, self.logos, self.catalog)
        if taken:
            # Erst gespeichert, dann umbenannt: ein Absturz dazwischen verliert nichts.
            self.save()
            for path in taken:
                try:
                    os.replace(path, path + ".imported")
                except OSError as e:                    # etwa von einem Sync-Programm gehalten: weiter starten
                    log.warning("%s lässt sich nicht in *.imported umbenennen (%s): von Hand umbenennen, sonst "
                                "übernimmt der nächste Start sie noch einmal", os.path.relpath(path, data), e)

    @property
    def lan(self) -> Panel:
        """Das Panel am LAN-Port (LAN_PANEL): /panel/frame, die Vorschau, /stats und /idle auf :8080."""
        return self.panels[self.server_cfg.lan_panel]

    def airfield_for(self, iata: str) -> Airfield | None:
        """Der Flughafen eines Panels (Config.local_airport_iata), für alle Panels mit ihm derselbe; None ohne
        Flughafen (AIRPORT=-). Der des Servers übernimmt einmalig die Landerichtung aus der Zeit mit einem
        Flughafen für alle (Airfield legacy)."""
        if not iata:
            return None
        af = self.airfields.get(iata)
        if af is None:
            af = self.airfields[iata] = Airfield(self.db, iata, legacy=iata == self.server_cfg.local_airport_iata)
        return af

    def metar_for(self, cfg: Config) -> MetarSource | None:
        """Das Wetter eines Panels, je METAR-Station eines; None ohne Station oder mit METAR=0."""
        if not cfg.metar or not cfg.metar_station:
            return None
        m = self.metars.get(cfg.metar_station)
        if m is None:
            m = self.metars[cfg.metar_station] = MetarSource(self.session, cfg.metar_station, self.issues)
        return m

    @property
    def airfield(self) -> Airfield:
        """Der Flughafen des Servers (.env), der des Panels am LAN-Port ohne eigenes AIRPORT."""
        return self.airfield_for(self.server_cfg.local_airport_iata)

    @property
    def metar(self) -> MetarSource | None:
        """Das Wetter am Flughafen des Servers."""
        return self.metar_for(self.server_cfg)

    def _sync_panels(self) -> None:
        """Die Panels, wie sie in der Datenbank stehen. panels.py schreibt neben dem laufenden Server, und was es
        ändert, gilt so beim nächsten Durchgang der Abrufschleife (spätestens nach einer Minute), ohne Neustart:
        Neue Panels kommen dazu, geänderte bekommen ihre Einstellungen (Album und Statistik bleiben), gelöschte
        fallen weg. Ein gelöschtes und gleich wieder angelegtes Panel (anderes created) beginnt neu, statt das
        alte Album in die neue Zeile zu schreiben. Das Panel am LAN-Port bleibt immer: Fehlt seine Zeile (von Hand
        gelöscht), bedient der Server es mit dem, was er hat."""
        rows = {r["id"]: r for r in self.db.panel_list()}
        lan = self.server_cfg.lan_panel
        for pid in [pid for pid in self.panels if pid not in rows and pid != lan]:
            del self.panels[pid]
            log.info("Panel %s gelöscht", pid)
        for pid, row in rows.items():
            p = self.panels.get(pid)
            if p is not None and (p.created, p.updated) == (row["created"], row["updated"]):
                continue
            remote = pid != lan
            skipped: list[tuple[str, str]] = []
            cfg = Config.for_panel(self.server_cfg, row["settings"], skipped, remote=remote)
            if skipped:
                self._note_skipped(pid, skipped)
            off = too_far(self.server_cfg, cfg) if remote else None
            if off:
                self._note_off(pid, off)
            channel = row["fw_channel"]
            if fw_channel(channel) != channel:
                log.warning("Panel %s: Firmware-Kanal %r gibt es nicht, es gilt stable (panels channel)", pid, channel)
            if p is None or p.created != row["created"]:
                self.panels[pid] = Panel(self, pid, cfg, row["created"], row["updated"], off, channel)
                log.info("Panel %s: %d Einstellungen", pid, len(row["settings"]))
            else:
                p.configure(cfg, row["updated"], off, channel)
                log.info("Panel %s: Einstellungen übernommen", pid)

    def _note_off(self, pid: str, why: str) -> None:
        """Ein weiteres Panel, dessen Fenster zu weit vom Flughafen liegt (config.too_far): Es ruft nicht ab, statt
        den Abruf für alle zu vergrößern. panels.py lässt das nicht zu; so kommt es nur, wenn PANEL_MAX_KM später
        kleiner wurde oder jemand die Datenbank von Hand geändert hat."""
        fix = f"python -m flightpanel.panels set {pid} HOME_LAT=… HOME_LON=…"
        log.warning("Panel %s ruft nicht ab: %s. Beheben: %s, oder PANEL_MAX_KM in .env erhöhen", pid, why, fix)
        self.issues.note("setting", f"{pid} HOME", error=why, fix=fix)

    def _note_aside(self, aside: Aside) -> None:
        """Der Start hat eine kaputte Datenbank beiseitegelegt und leer begonnen (Db). Das Log sagt es schon; hier kommt
        es in /issues, damit /ok rot bleibt und /health die Datei nennt, bis jemand nachgesehen und /issues geleert
        hat. Der Eintrag liegt in der neuen Datenbank und übersteht Neustarts. Er wird sofort gespeichert, nicht erst
        vom nächsten save(): Käme der Server davor nicht weiter, fände der nächste Start eine gesunde leere Datenbank,
        /ok wäre grün, und die nächtliche Kopie ersetzte den Weg zurück zum Album."""
        self.issues.note("database", aside.file, error=aside.error, fix=aside.way_back)
        self.issues.save()      # außerhalb einer Transaktion ist es damit festgeschrieben (Db.after_commit)

    def _asides(self) -> list[str]:
        """Die beiseitegelegten Datenbankdateien, die /issues noch meldet."""
        return [it["key"] for it in self.issues.items.values() if it["kind"] == "database"]

    def _note_skipped(self, pid: str, skipped: list[tuple[str, str]]) -> None:
        """Gespeicherte Panel-Einstellungen, die nicht (mehr) gelten: Eine spätere Fassung kennt sie nicht oder prüft
        strenger, oder jemand hat die Datenbank von Hand geändert. Der Server startet mit dem Rest, statt in einer
        Neustartschleife zu hängen; Log und /issues nennen sie und einen Befehl für alle zusammen (panels prüft beim
        Zurücksetzen des einen auch die anderen, einzeln ginge es nicht). Die Schlüssel stehen so, wie die Shell sie
        liest (shlex.quote): Von Hand geschriebene haben mitunter Leerzeichen oder Sonderzeichen und blieben sonst nicht
        ein Argument. Klein oder groß geschrieben, panels findet sie in jedem Fall (panels._name)."""
        fix = f"python -m flightpanel.panels unset {pid} {' '.join(shlex.quote(key) for key, _ in skipped)}"
        log.warning("Panel %s: übergangen, es gilt der Default: %s. Beheben: %s, danach Gewolltes neu setzen "
                    "(set %s KEY=WERT)", pid, "; ".join(why for _, why in skipped), fix, pid)
        for key, why in skipped:
            self.issues.note("setting", f"{pid} {key}", error=why, fix=fix)

    # ------------------------------------------------------------ Abrufen --
    def poll_interval(self) -> int:
        """Abstand zwischen zwei Abrufen. adsb.lol hat kein Tageslimit: POLL_INTERVAL,
        auch wenn es gerade ausfällt (OpenSky springt dann seltener ein, siehe
        fetch_states). Nur mit SOURCE=opensky gilt dessen Budget."""
        cfg = self.server_cfg
        if cfg.mock or cfg.source == "adsblol":
            return cfg.poll_interval
        return self.opensky_interval()

    def refresh_period(self) -> int:
        """Wie oft das Modell tatsächlich neu wird: bei ausgefallenem adsb.lol so
        selten, wie OpenSky einspringen darf. Für Vorschau und Stale-Schwellen."""
        if self.server_cfg.source == "adsblol" and self.adsblol_failing:
            return self.opensky_interval()
        return self.poll_interval()

    def opensky_interval(self) -> int:
        """Mindestabstand zwischen OpenSky-Abrufen: nie kürzer als POLL_INTERVAL, aber so lang, dass das
        Tagesbudget für alle Stunden reicht, in denen ein Panel abruft (außerhalb seiner Ruhezeit; anonym: 400
        Credits -> rund alle 2,5 min), je Region ein Abruf. Wird es trotzdem knapp, doppelt so lang."""
        budget = self.opensky.daily_credits
        cfgs = [p.cfg for p in self.panels.values() if p.off is None]
        need = sum(active_seconds(r) for r in regions(cfgs)) / (budget * 0.95)     # ein Abruf je Region
        interval = max(self.server_cfg.poll_interval, int(need + 0.999))
        rem = self.opensky.credits_remaining
        if rem is not None and rem < budget * 0.1:
            interval *= 2
        return interval

    async def fetch_states(self, cfgs: list[Config]) -> list[State]:
        """Die Flugzeuge für diese Panels (cfgs): adsb.lol, wenn gewählt, in einem Umkreis um alle (fetch_circle).
        Fällt es aus, springt OpenSky ein (eine Box um alle, union_bbox), aber nur so oft, wie dessen Tagesbudget
        erlaubt; adsb.lol wird weiter bei jedem Abruf probiert, ein kurzer Aussetzer kostet also nur einen Abruf."""
        if self.server_cfg.source == "adsblol":
            try:
                states = await self.adsblol.fetch(*fetch_circle(cfgs))
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
        return await self.opensky.fetch(union_bbox(cfgs))

    async def poll_loop(self) -> None:
        while not self._stop.is_set():
            await asyncio.sleep(await self.poll_once(time.time()))

    async def poll_once(self, t0: float) -> float:
        """Ein Durchgang der Abrufschleife: Panels nachziehen (panels.py), die ruhenden schwarz, für die anderen
        ein Abruf. Gibt zurück, wie lange bis zum nächsten."""
        try:
            self._sync_panels()
        except Exception as e:              # etwa Datenbank gesperrt: mit den bisherigen Panels weiter
            self._note_poll_error(e)
        active = [p for p in self.panels.values() if p.active(t0)]
        for p in self.panels.values():
            if p not in active:
                p.rest()
        if not active:                      # alle in der Ruhezeit (oder aus): nichts abrufen
            return REST_S
        self.counters["polls"] += 1
        try:
            await self._poll(t0, active)
            self.last_model_ok = t0
            self.counters["ok"] += 1
        except Exception as e:
            self._note_poll_error(e)
            self._age_models(active, t0)
        return max(5, self.poll_interval() - (time.time() - t0))

    async def _poll(self, t0: float, active: list[Panel] | None = None) -> None:
        """Die Panels, die gerade nicht ruhen (ohne Angabe: alle solchen), je Region (panel.regions) ein Abruf,
        nacheinander: Flugzeuge holen, je Panel das Modell bauen, je Flughafen einmal nachführen, je Panel die
        Statistik. Fällt der Abruf einer Region aus, altern nur ihre Panels (_age_models); nur wenn keine Region
        gelang, gilt der Durchgang als gescheitert (poll_once)."""
        if active is None:
            active = [p for p in self.panels.values() if p.active(t0)]
        if not active:
            return
        if self.server_cfg.mock:
            for p in active:
                p.model = mock_model(p.cfg, seed=int(t0 // 20))
                p.last_ok = t0
            self._observe_airfields(active, t0)
            for p in active:
                p.stats.observe([], p.model, t0)
        else:
            by_cfg = {id(p.cfg): p for p in active}
            failed: list[Exception] = []
            ok = 0
            for cfgs in regions([p.cfg for p in active]):
                group = [by_cfg[id(c)] for c in cfgs]
                try:
                    states = await self.fetch_states(cfgs)
                except Exception as e:
                    failed.append(e)
                    self._note_poll_error(e)
                    self._age_models(group, t0)
                    continue
                ok += 1
                self.counters["fetches"] += 1
                noted: set[str] = set()         # jede Auffälligkeit einmal je Abruf, nicht je Panel
                for p in group:
                    af = p.airfield
                    p.model = await build_model(p.cfg, states, self.adsb, self.names,
                                                horizon_s=self.refresh_period(),
                                                prev_course=af.landing_course if af else None, issues=self.issues,
                                                adsbim=self.adsbim, origins=self.origins, source=self.active_source,
                                                recent=af.recent if af else None, rare=self.rare, noted=noted)
                    p.last_ok = t0
                self._observe_airfields(group, t0)
                self.counters[self.active_source] += 1
                for p in group:
                    await self._observe(p, states, t0)
                log.info("%s: %d empfangen. %s. Nächster Abruf in %d s", self.active_source, len(states),
                         "; ".join(_seen(p) for p in group), self.poll_interval())
            if failed and not ok:
                raise failed[0]
        for p in active:
            for f in p.model.flights:
                if f.kind == "airline":
                    self.logos.ensure(f.airline_iata, f.airline_icao)   # holt fehlende Logos im Hintergrund

    def _observe_airfields(self, panels: list[Panel], t0: float) -> None:
        """Je Flughafen einmal nachführen (Landefolge, Abflüge, Landerichtung: in allen seinen Panels gleich) und
        den Panels die letzte Bewegung am Platz mitgeben; ohne Flughafen, wann zuletzt etwas am Himmel war."""
        seen: set[int] = set()
        for p in panels:
            af = p.airfield
            if af is not None and id(af) not in seen:
                seen.add(id(af))
                af.observe(p.model, t0)
        for p in panels:
            if p.model.flights or p.model.nearby:
                p.last_traffic = t0
            p.model.last_airport_move = p.airfield.last_move if p.airfield else p.last_traffic

    def _age_models(self, panels: list[Panel], now: float | None = None) -> None:
        """Ohne frische Daten erst dimmen, dann leeren, in jedem dieser Panels (die einer Region ohne Abruf, oder
        alle). Die Schwellen sind nie kürzer als ein paar Abrufintervalle, sonst dimmt das Bild bei langsamem
        (anonymem) Abruf nach jedem Fehlversuch. In /issues einmal, nicht je Panel."""
        age = (now if now is not None else time.time()) - max((p.last_ok for p in panels), default=0.0)
        period = self.refresh_period()
        cfg = self.server_cfg
        if age > max(cfg.empty_after, 4 * period):
            if any(not p.model.empty for p in panels):
                self.issues.note("stale", "empty", age_s=round(age))
            for p in panels:
                p.model = replace(p.model, flights=[], empty=True, stale=True)
        elif age > max(cfg.stale_after, 2 * period):
            if any(not p.model.stale for p in panels):
                self.issues.note("stale", "dimmed", age_s=round(age))
            for p in panels:
                p.model = replace(p.model, stale=True)

    async def _observe(self, p: Panel, states: list[State], t0: float) -> None:
        """Statistik eines Panels nachführen, mit den Flugzeugen in seinem Abrufradius (der Abruf deckt alle Panels
        ab); neue Airlines bekommen ihr Logo, unbekannte Betreiber ihren Namen von adsbdb. Ein Fehler hier darf den
        Abruf nicht kosten."""
        try:
            mine = [s for s in states if p.covers(s)]
            for code in p.stats.observe(mine, p.model, t0):
                if code in self.names:
                    self.logos.ensure(self.names[code]["iata"], code)
                else:
                    cs = next((s.callsign for s in mine if s.callsign.startswith(code)), "")
                    route = await self.adsb.route(cs) if cs else {}
                    name = route.get("airline", "") or await self.adsb.airline(code)
                    p.stats.set_name(code, short_airline(name, code, self.names))
        except Exception as e:
            self._note_poll_error(e)

    def _note_poll_error(self, e: Exception) -> None:
        """Netz- und Quellenfehler als source_error/rate_limit, alles andere ist
        ein Bug: als exception mit Datei und Zeile, im Log mit Traceback."""
        msg = error_text(e)
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
        """Alle 20 min das METAR jeder Station, deren Panel abruft; ruhen alle, nur, solange noch keins da ist."""
        while not self._stop.is_set():
            now = time.time()
            for m in list(self.metars.values()):
                if m.current is None or any(p.active(now) and p.metar is m for p in self.panels.values()):
                    try:
                        await m.refresh()
                    except Exception as e:
                        log.warning("METAR %s: %s", m.station, e)
            await asyncio.sleep(METAR_EVERY_S)

    async def astro_loop(self) -> None:
        """Bahndaten der Satelliten einmal am Tag für alle (astro.Catalog), die Überflüge je Panel und Satellit
        stündlich (astro.Tracker)."""
        while not self._stop.is_set():
            now = time.time()
            try:
                if self.catalog.needs_tle(now):
                    await self.catalog.refresh()
                for p in list(self.panels.values()):
                    await p.update_passes(now)
            except Exception as e:
                log.warning("Satelliten: %s", e)
            await asyncio.sleep(ISS_EVERY_S)

    async def save_loop(self) -> None:
        while not self._stop.is_set():
            await asyncio.sleep(SAVE_EVERY_S)
            try:
                if dream_due(self.lan.cfg, self._dream_day, time.time()):
                    self.dream(time.time())
                self.save()
            except Exception as e:
                log.warning("Speichern: %s", e)

    def save(self) -> None:
        """Geändertes in die Datenbank, als eine Transaktion. Vorher die Panels nachziehen: Ein mit panels remove
        gelöschtes Panel schriebe sonst in Zeilen, die es nicht mehr gibt, und die Transaktion scheiterte für alle.
        Je Panel, dessen ESP32 schon abgerufen hat, auch wann zuletzt und was es meldete (panels list)."""
        self._sync_panels()
        with self.db.transaction():
            self.cache.save()
            self.issues.save()
            for af in self.airfields.values():
                af.save()
            for p in self.panels.values():
                if p.last_frame_fetch:
                    self.db.panel_seen(p.id, p.last_frame_fetch, p.diag)
                p.stats.save()

    def dream(self, now: float) -> None:
        """Nachts aufräumen ("träumen"), damit der Server auch nach einem Jahr am Stück
        so läuft wie am ersten Tag: Einmaliges und Vergangenes in /issues vergessen
        (Issues.forget), Abgelaufenes aus dem Cache, eine Kopie der Datenbank (_copy).
        Kostet Millisekunden."""
        forgotten = self.issues.forget(now)
        pruned = self.cache.prune()
        copy = self._copy()
        self._dream_day = time.strftime("%Y-%m-%d", time.localtime(now))
        self.last_dream = {"at": time.strftime("%Y-%m-%d %H:%M", time.localtime(now)),
                           "issues_forgotten": forgotten, "cache_expired": pruned, "cache_entries": len(self.cache),
                           "copy": copy}
        log.info("Nachts aufgeräumt: %d Auffälligkeiten vergessen, %d Cache-Einträge abgelaufen, %d bleiben; "
                 "Kopie: %s", forgotten, pruned, len(self.cache), copy)

    def _copy(self) -> str:
        """Eine stimmige Kopie der Datenbank nach flightpanel.db.bak, die die der Vornacht ersetzt (Db.backup). Ohne
        sie gäbe es keine: data/ liegt oft in einem synchronisierten Ordner (OneDrive, Dropbox …) und hat sonst keine
        Sicherung, und eine beiseitegelegte Datenbank kostet so einen Tag statt des Albums. Scheitert sie, steht es
        im Log und in /issues, das Aufräumen geht weiter. Solange /issues eine beiseitegelegte Datenbank meldet,
        ruht sie: Die Kopie wäre die der fast leeren neuen Datenbank und ersetzte den Weg zurück. Gibt zurück, was
        geschah, für /health."""
        name = os.path.basename(self.db.path + COPY)
        if self._asides():
            log.warning("Nächtliche Kopie ausgesetzt: %s bleibt, wie sie ist, solange /issues die beiseitegelegte "
                        "Datenbank meldet", name)
            return f"übersprungen: {name} bleibt, solange /issues die beiseitegelegte Datenbank meldet"
        try:
            self.db.backup(self.db.path + COPY)
        except Exception as e:                          # Platte voll, Rechte, Datenbank gesperrt ...
            log.warning("Nächtliche Kopie %s gescheitert: %s", name, e)
            self.issues.note("backup", name, error=str(e)[:300])
            return f"Fehler: {e}"
        return name

    def _copy_time(self) -> str | None:
        """Wann die letzte nächtliche Kopie entstand, aus der Datei selbst: Das übersteht auch einen Neustart."""
        try:
            return time.strftime("%Y-%m-%d %H:%M", time.localtime(os.path.getmtime(self.db.path + COPY)))
        except OSError:
            return None

    # ------------------------------------------------------------- Zustand --
    def issues_report(self) -> dict:
        return self.issues.report(uptime_h=round((time.time() - self.started) / 3600, 1),
                                  polls_since_start=self.counters, logos_missing=sorted(self.logos.missing()))

    def problems(self, now: float, panel: Panel | None = None) -> list[str]:
        """Was gerade nicht stimmt, für GET /ok (Docker-Healthcheck, Uptime-Monitor); leer heißt alles gut. Alte
        Flugdaten zählen, solange ein Panel hell ist; ein ESP32, das nicht mehr abruft, nur am LAN-Port: Ein
        Freund, der den Stecker zieht, macht den Server nicht ungesund. In der Ruhezeit ist dunkel normal. Mit
        panel (GET /ok?panel=<id>, ein Uptime-Monitor je Panel, auch eines weit weg) nur dieses eine: alte
        Flugdaten seiner Region und sein ESP32, solange es hell sein sollte. Eine 421
        am LAN-Port in den letzten OK_REFUSED_S ohne Abruf des Panels danach (falscher Hostname, LAN_HOSTS): Sonst
        bliebe /ok grün, obwohl das Panel nie ein Bild bekommt. Mit Zugang
        von außen auch eine veraltete Root-Liste der Firmware (selfcheck.py): Dann blieben alle Panels der Freunde
        dunkel."""
        out = []
        if panel is not None:
            if panel.off is None and panel.cfg.brightness(now) > 0:
                age = now - (panel.last_ok or self.started)
                if age > OK_STALE_S:
                    out.append(f"keine frischen Flugdaten seit {age / 60:.0f} min")
                if panel.frame_count and now - panel.last_frame_fetch > OK_ESP32_S:
                    out.append(f"ESP32 holt seit {(now - panel.last_frame_fetch) / 60:.0f} min keine Bilder")
            return out
        if any(p.off is None and p.cfg.brightness(now) > 0 for p in self.panels.values()):
            age = now - (self.last_model_ok or self.started)
            if age > OK_STALE_S:
                out.append(f"keine frischen Flugdaten seit {age / 60:.0f} min")
        lan = self.lan
        if lan.cfg.brightness(now) > 0 and lan.frame_count and now - lan.last_frame_fetch > OK_ESP32_S:
            out.append(f"ESP32 holt seit {(now - lan.last_frame_fetch) / 60:.0f} min keine Bilder")
        if now - self.refused_at < OK_REFUSED_S and now - lan.last_frame_fetch >= OK_REFUSED_S:
            # Das Panel (oder der Monitor) fragt unter einem Namen, den lan_host nicht kennt, und holt nie ein Bild
            out.append("Panel-Anfragen mit unbekanntem Hostnamen abgewiesen (421): Namen in LAN_HOSTS eintragen "
                       "oder die IP nutzen")
        out += [f"Fehler im Code: {it['key']}" for it in self.issues.items.values()
                if it["kind"] == "exception" and now - it["last"] < OK_EXCEPTION_S]
        out += [f"Datenbank beiseitegelegt, das Album startete leer: {name} (siehe /issues)"
                for name in self._asides()]
        if self.tls is not None and self.tls.stale:
            out.append(f"Root-Liste veraltet: Die Panels trauen dem Zertifikat von PUBLIC_HOST nicht mehr "
                       f"({self.tls.stale}), siehe /issues")
        return out

    def _regions_report(self, now: float) -> list[dict]:
        """Für /health: die Regionen der Panels, die gerade abrufen, je eine mit ihren Panels und ihrem Umkreis. Als
        Schlüssel der Flughafen oder -, nie region_key: Der wäre ohne Flughafen die Lage des Fensters."""
        active = [p for p in self.panels.values() if p.active(now)]
        by_cfg = {id(p.cfg): p.id for p in active}
        return [{"key": cfgs[0].local_airport_iata or "-", "panels": [by_cfg[id(c)] for c in cfgs],
                 "fetch_km": round(fetch_circle(cfgs)[2])} for cfgs in regions([p.cfg for p in active])]

    def health(self) -> dict:
        """Für GET /health: Quellen, Logos, das Panel am LAN-Port (Modell, Anzeige, ESP32) und eine Zeile je Panel."""
        now = time.time()
        lan = self.lan
        cfg = lan.cfg
        esp_age = now - lan.last_frame_fetch if lan.last_frame_fetch else None
        return {
            "ok": True,
            "problems": self.problems(now),
            "mock": cfg.mock,
            "layout": cfg.layout,
            "calm": cfg.calm,
            "display": {"page_seconds": cfg.page_seconds, "flight_seconds": cfg.flight_seconds,
                        "idle_seconds": cfg.idle_seconds, "blink": cfg.blink, "info_pages": list(cfg.info_pages),
                        "cards_off": sorted(cfg.cards_off)},
            "brightness_now": lan.renderer.brightness(now),
            "model_age_s": round(now - self.last_model_ok) if self.last_model_ok else None,
            "model_stale": lan.model.stale,
            "flights": len(lan.model.flights),
            "source": None if cfg.mock else {
                "configured": cfg.source,
                "active": self.active_source,
                "poll_interval_s": self.poll_interval(),
                "adsblol_last_ok_age_s": round(now - self.adsblol.last_ok) if self.adsblol.last_ok else None,
                "adsblol_last_error": self.adsblol.last_error,
                "regions": self._regions_report(now),
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
            "database": {"copy": self._copy_time(), "put_aside": self._asides()},
            "metar": {st: {"age_s": m.age_s(now), "last_error": m.last_error,
                           "raw": m.current.raw if m.current else None} for st, m in self.metars.items()} or None,
            "airfields": {iata: af.report() for iata, af in self.airfields.items()},
            "sats": None if not self.catalog else {
                "usable": sorted(o.name for o in self.catalog.orbits.values() if o.usable(now)),
                "tle_fetched_age_h": (round((now - self.catalog.fetched) / 3600, 1)
                                      if self.catalog.fetched else None),
                "last_error": self.catalog.last_error,
                "trains": [{"key": t.key, "n": t.n, "span_deg": t.span} for t in self.catalog.trains],
                "passes_24h": {cid: len(tr.passes) for cid, tr in lan.sats.items()},
            },
            "idle_card": lan.idle.last_pick,
            "logos": self.logos.available(),
            "logos_missing": sorted(self.logos.missing()),
            "esp32": {
                "last_fetch_age_s": round(esp_age) if esp_age is not None else None,
                "fetches": lan.frame_count,
                "alive": esp_age is not None and esp_age < 300,
            },
            # 421 am LAN-Port (Host nicht erlaubt, siehe LAN_HOSTS): wie viele seit dem Start, wann die letzte war
            "refused_host": {"count": self.refused,
                             "last_age_s": round(now - self.refused_at) if self.refused_at else None},
            "panels": {pid: p.summary(now) for pid, p in self.panels.items()},
            # Zugang von außen (nur mit den Schaltern): Schlüssel von Access, Absagen je Grund, Gebremstes, Uhr
            "access": self.gate.report(now) if self.gate is not None else None,
        }

    # ----------------------------------------------------------- Lebenslauf --
    async def start(self) -> web.AppRunner:
        cfg = self.server_cfg
        if not cfg.mock and cfg.source == "opensky" and self.opensky.anonymous:
            log.warning("Keine OpenSky-Zugangsdaten: anonymer Zugriff (400 Credits/Tag), "
                        "Abruf nur alle %d s. Mit OPENSKY_CLIENT_ID/SECRET alle %d s.",
                        self.poll_interval(), cfg.poll_interval)
        runner = web.AppRunner(make_web_app(self), access_log=None)
        await runner.setup()
        await web.TCPSite(runner, "0.0.0.0", cfg.http_port).start()
        loops = [self.poll_loop(), self.save_loop()]
        if cfg.metar and not cfg.mock:
            loops.append(self.metar_loop())
        if self.catalog and not cfg.mock:
            loops.append(self.astro_loop())
        if cfg.public:
            loops += await self._start_public()
        self._tasks = [asyncio.create_task(c) for c in loops]
        for t in self._tasks:
            t.add_done_callback(self._task_ended)
        log.info("flightpanel läuft auf :%d (mock=%s, %dx%d), Panels: %s, am LAN-Port %s (layout=%s)%s",
                 cfg.http_port, cfg.mock, cfg.width, cfg.height, ", ".join(self.panels), cfg.lan_panel,
                 self.lan.cfg.layout, f", Zugang von außen auf :{cfg.public_port}" if cfg.public else "")
        return runner

    async def _start_public(self) -> list:
        """Der Zugang von außen, nur mit den Schaltern (Config.public): Erst hier werden access.py und selfcheck.py
        geladen. Eine zweite Anwendung auf PUBLIC_PORT (web.make_public_app) mit dem Türsteher davor (access.Gate),
        die Schlüssel von Access aus der Datenbank (access.KeySet), der TLS-Selbsttest für den eigenen Hostnamen.
        Gibt die Schleifen dazu zurück."""
        from . import access, selfcheck
        cfg = self.server_cfg
        self.keys = access.KeySet(self.db, self.session, access.certs_url(cfg.access_team))
        self.gate = access.Gate(self.keys, cfg.access_team, cfg.access_aud, self.db)
        self.tls = selfcheck.TlsCheck(self.session, self.issues, f"https://{cfg.public_host}/panel/firmware.txt")
        self._public = web.AppRunner(make_public_app(self, self.gate), access_log=None)
        await self._public.setup()
        await web.TCPSite(self._public, "0.0.0.0", cfg.public_port).start()
        return [self.keys_loop(), self.tls_loop()]

    async def keys_loop(self) -> None:
        """Die Signaturschlüssel von Access: beim Start, wenn noch keine in der Datenbank liegen, dann einmal am Tag.
        Ein unbekannter kid holt sie außerdem sofort (access.Gate, höchstens einmal pro Minute). Scheitert ein
        Abruf, bleibt die letzte gute Liste (KeySet), /health nennt den Fehler."""
        if not self.keys.keys:
            await self.keys.refresh(time.time(), force=True)
        while not self._stop.is_set():
            await asyncio.sleep(KEYS_EVERY_S)
            await self.keys.refresh(time.time(), force=True)

    async def tls_loop(self) -> None:
        """Der TLS-Selbsttest (selfcheck.TlsCheck): etwa eine Minute nach dem Start, dann einmal am Tag. Sein Ergebnis
        steht in /ok (problems) und /issues."""
        await asyncio.sleep(self.tls.FIRST_S)
        while not self._stop.is_set():
            await self.tls.run(time.time())
            await asyncio.sleep(self.tls.EVERY_S)

    def _task_ended(self, task: asyncio.Task) -> None:
        """Eine Schleife ist abgestürzt (sie fangen ihre Fehler sonst selbst): ins Log
        und nach /issues, statt still nichts mehr zu tun."""
        if task.cancelled() or task.exception() is None:
            return
        e = task.exception()
        log.error("Hintergrundschleife beendet: %s", error_text(e), exc_info=e)
        self.issues.note("exception", f"loop {task.get_coro().__name__}", error=error_text(e)[:300])

    async def stop(self, runner: web.AppRunner) -> None:
        """runner: der des LAN-Ports (start); den des Zugangs von außen räumt stop() zuerst selbst ab."""
        self._stop.set()
        for t in self._tasks:
            t.cancel()
        self.save()
        for r in (self._public, runner):
            if r is not None:
                await r.cleanup()
        await self.session.close()
        self.db.close()


async def _main() -> None:
    os.umask(0o027)                     # Datenbank, Kopien und Logos nicht für alle lesbar: Standort und Album
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
