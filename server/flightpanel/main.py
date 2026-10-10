"""HTTP-Server und Poll-Schleife.

Endpoints:
  GET /              Vorschauseite (lädt /frame.png alle 2 s neu)
  GET /frame         Binär für den ESP32: [1, brightness, W, H] + RGB
  GET /frame.png     Vorschau, 6x vergrößert; ?layout=A|B|C&scale=N&gap=0
  GET /display.json  aktuelles Display-Modell
  GET /health        Zustand von OpenSky, adsbdb, Cache, ESP32-Abruf
  GET /issues        gesammelte Auffälligkeiten (siehe issues.py), POST /issues/clear leert
  GET /stats         Statistik und Sammelalbum (siehe stats.py)
  GET /idle          Leerlauf-Karten: welche gerade etwas zu sagen haben, welche dran ist
"""

import asyncio
import json
import logging
import os
import signal
import time
import traceback
from dataclasses import replace

import aiohttp
from aiohttp import web

from .adsbdb import AdsbDb, Cache
from .adsbim import AdsbIm
from .adsblol import AdsbLol, Paused
from .astro import Iss
from .cards import CARDS
from .config import CFG, Config
from .idle import IdleScreen
from .issues import Issues
from .origins import Origins, load_airports
from .logos import make_logo_store
from .metar import MetarSource
from .model import DisplayModel, build_model, load_airlines, mock_model
from .opensky import OpenSky
from .render import Renderer, active_seconds_per_day, brightness_for
from .stats import Stats, load_rare

log = logging.getLogger("main")

PREVIEW_HTML = """<!doctype html><meta charset="utf-8"><title>flightpanel</title>
<style>body{background:#111;color:#ccc;font:14px system-ui;margin:24px}
img{image-rendering:pixelated;display:block;margin:12px 0;border:1px solid #333}
a{color:#8ab;margin-right:14px}</style>
<h2>flightpanel</h2>
<img id="f" src="/frame.png?layout=%(layout)s&card=%(card)s" alt="frame">
<div>Layout:
<a href="/?layout=AUTO">auto</a><a href="/?layout=A">A</a><a href="/?layout=B">B</a><a href="/?layout=C">C</a>
| <a href="/display.json">display.json</a><a href="/health">health</a><a href="/issues">issues</a>
<a href="/stats">stats</a><a href="/idle">idle</a></div>
<p>Leerlauf-Karte erzwingen (passt die Lage nicht, mit Beispieldaten): <a href="/">aus</a> %(cards)s</p>
<script>setInterval(()=>{const i=document.getElementById('f');i.src='/frame.png?layout=%(layout)s&card=%(card)s&t='+Date.now()},2000)</script>
"""


def load_fun(data_dir: str) -> dict:
    """data/jokes.json für die Spaß-Karten; fehlt sie, gibt es eben keine Witze."""
    try:
        with open(os.path.join(data_dir, "jokes.json"), encoding="utf-8") as f:
            raw = json.load(f)
        return {"jokes": raw.get("jokes", []), "delays": raw.get("delays", [])}
    except FileNotFoundError:
        return {}
    except Exception as e:
        log.warning("jokes.json nicht lesbar: %s", e)
        return {}


class App:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.last_model_ok = 0.0
        self.last_frame_fetch = 0.0
        self.frame_count = 0
        self.session: aiohttp.ClientSession | None = None
        self.opensky: OpenSky | None = None
        self.adsblol: AdsbLol | None = None
        self.active_source = cfg.source     # woher der letzte Abruf kam
        self.adsblol_failing = False
        self.opensky_last_try = 0.0
        self.issues = Issues(os.path.join(cfg.data_dir, "issues.json"))
        self.started = time.time()
        self.counters = {"polls": 0, "ok": 0, "adsblol": 0, "opensky": 0}
        self.cache = Cache(os.path.join(cfg.data_dir, "cache.json"))
        self.adsb: AdsbDb | None = None
        self.names = load_airlines(cfg.data_dir)
        self.logos = make_logo_store(cfg)
        self.stats = Stats(os.path.join(cfg.data_dir, "stats.json"), cfg, self.names, load_rare(cfg.data_dir))
        self.model = DisplayModel(empty=True, source="startup", landing_course=self.stats.course)
        self.metar = MetarSource(None, cfg.metar_station, self.issues) if cfg.metar else None
        self.iss = Iss(cfg, os.path.join(cfg.data_dir, "tle.txt"), None, self.issues) if cfg.iss else None
        self.idle = IdleScreen(cfg, CARDS, stats=self.stats, metar=self.metar, astro=self.iss,
                               logos=self.logos, names=self.names, fun=load_fun(cfg.data_dir))
        self.renderer = Renderer(cfg, self.logos, idle=self.idle)
        self._stop = asyncio.Event()

    # ------------------------------------------------------------ Polling --
    def poll_interval(self) -> int:
        """Abstand zwischen zwei Abrufen. adsb.lol hat kein Tageslimit: POLL_INTERVAL,
        auch wenn es gerade ausfällt (OpenSky springt dann seltener ein, siehe
        fetch_states). Nur mit SOURCE=opensky gilt dessen Budget."""
        if self.cfg.mock or self.cfg.source == "adsblol":
            return self.cfg.poll_interval
        return self.opensky_interval()

    def refresh_period(self) -> int:
        """Wie oft das Modell tatsächlich neu wird: bei ausgefallenem adsb.lol so
        selten wie OpenSky einspringen darf. Für Vorschau und Stale-Schwellen."""
        if self.cfg.source == "adsblol" and self.adsblol_failing:
            return self.opensky_interval()
        return self.poll_interval()

    def opensky_interval(self) -> int:
        """Mindestabstand zwischen OpenSky-Abrufen: nie kürzer als POLL_INTERVAL,
        aber so lang, dass das Tagesbudget für alle Stunden außerhalb der Quiet
        Hours reicht (anonym: 400 Credits -> rund alle 2,5 min). Wird es trotzdem
        knapp, doppelt so lang."""
        budget = self.opensky.daily_credits
        need = active_seconds_per_day(self.cfg) / (budget * 0.95)
        interval = max(self.cfg.poll_interval, int(need + 0.999))
        rem = self.opensky.credits_remaining
        if rem is not None and rem < budget * 0.1:
            interval *= 2
        return interval

    async def fetch_states(self):
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
                    self.issues.note("rate_limit" if "429" in err else "source_error", "adsblol", error=err)
                wait = self.opensky_interval() - (time.time() - self.opensky_last_try)
                if wait > 0:
                    raise RuntimeError(f"adsb.lol: {self.adsblol.last_error}, OpenSky erst in {wait:.0f} s")
                log.warning("adsb.lol: %s, nehme OpenSky", self.adsblol.last_error)
                self.issues.note("fallback", "opensky", reason=err)
        self.opensky_last_try = time.time()
        self.active_source = "opensky"
        return await self.opensky.fetch(self.cfg.bbox)

    async def poll_loop(self) -> None:
        while not self._stop.is_set():
            t0 = time.time()
            interval = self.poll_interval()
            if not self.cfg.mock and brightness_for(self.cfg, t0) == 0:
                # Quiet Hours: Panel ist schwarz, Credits sparen
                await asyncio.sleep(60)
                continue
            self.counters["polls"] += 1
            try:
                if self.cfg.mock:
                    self.model = mock_model(self.cfg, seed=int(t0 // 20))
                    self.stats.observe([], self.model, t0)
                else:
                    states = await self.fetch_states()
                    interval = self.poll_interval()
                    self.model = await build_model(self.cfg, states, self.adsb, self.names,
                                                   horizon_s=self.refresh_period(),
                                                   prev_course=self.model.landing_course,
                                                   issues=self.issues, adsbim=self.adsbim, origins=self.origins)
                    self.counters[self.active_source] += 1
                    await self._observe(states, t0)
                    log.info("%s: %d empfangen, %d im Radius, %d im Sichtfeld, angezeigt: %s. "
                             "Nächster Abruf in %d s",
                             self.active_source, len(states), len(self.model.nearby),
                             sum(1 for n in self.model.nearby if n["in_view"] != "no"),
                             ", ".join(f"{f.callsign}#{f.seq}" if f.seq else f.callsign
                                       for f in self.model.flights) or "-", interval)
                for f in self.model.flights:
                    if f.kind == "airline":
                        self.logos.ensure(f.airline_iata, f.airline_icao)   # holt fehlende Logos im Hintergrund
                self.last_model_ok = t0
                self.counters["ok"] += 1
            except Exception as e:
                self._note_poll_error(e)
                age = time.time() - self.last_model_ok
                # Schwellen nie kürzer als ein paar Abrufintervalle, sonst dimmt
                # das Bild bei langsamem (anonymem) Polling nach jedem Fehlversuch.
                period = self.refresh_period()
                if age > max(self.cfg.empty_after, 4 * period):
                    if not self.model.empty:
                        self.issues.note("stale", "empty", age_s=round(age))
                    self.model = replace(self.model, flights=[], empty=True, stale=True)
                elif age > max(self.cfg.stale_after, 2 * period):
                    if not self.model.stale:
                        self.issues.note("stale", "dimmed", age_s=round(age))
                    self.model = replace(self.model, stale=True)
            await asyncio.sleep(max(5, interval - (time.time() - t0)))

    async def _observe(self, states, t0: float) -> None:
        """Statistik nachführen; neue Airlines bekommen ihr Logo, unbekannte Betreiber
        ihren Namen von adsbdb. Ein Fehler hier darf den Abruf nicht kosten."""
        try:
            for code in self.stats.observe(states, self.model, t0):
                if code in self.names:
                    self.logos.ensure(self.names[code]["iata"], code)
                else:
                    cs = next((s.callsign for s in states if s.callsign.startswith(code)), "")
                    route = await self.adsb.route(cs) if cs else {}
                    self.stats.set_name(code, route.get("airline", ""))
        except Exception as e:
            self._note_poll_error(e)

    async def metar_loop(self) -> None:
        """Alle 20 min das METAR; in den Quiet Hours nur, solange noch keins da ist."""
        while not self._stop.is_set():
            if brightness_for(self.cfg, time.time()) > 0 or self.metar.current is None:
                try:
                    await self.metar.refresh()
                except Exception as e:
                    log.warning("METAR: %s", e)
            await asyncio.sleep(20 * 60)

    async def astro_loop(self) -> None:
        """ISS-Bahnelemente einmal am Tag, Überflüge stündlich neu rechnen."""
        while not self._stop.is_set():
            now = time.time()
            try:
                if self.iss.needs_tle(now):
                    await self.iss.refresh_tle()
                await self.iss.update_passes(now)
            except Exception as e:
                log.warning("ISS: %s", e)
            await asyncio.sleep(10 * 60)

    def _note_poll_error(self, e: Exception) -> None:
        """Netz- und Quellenfehler als source_error/rate_limit, alles andere ist
        ein Bug: als exception mit Datei und Zeile, im Log mit Traceback."""
        msg = str(e) or type(e).__name__
        if isinstance(e, (aiohttp.ClientError, asyncio.TimeoutError, RuntimeError)):
            log.warning("Poll fehlgeschlagen: %s", msg)
            if not msg.startswith("adsb.lol"):      # adsb.lol-Ausfälle zählt fetch_states schon
                src = "opensky" if "OpenSky" in msg else self.active_source
                self.issues.note("rate_limit" if "429" in msg else "source_error", src, error=msg)
            return
        log.exception("Poll fehlgeschlagen: %s", msg)
        tb = traceback.extract_tb(e.__traceback__)
        where = f"{os.path.basename(tb[-1].filename)}:{tb[-1].lineno}" if tb else "?"
        self.issues.note("exception", f"{type(e).__name__} {where}", error=msg[:300])

    async def cache_loop(self) -> None:
        while not self._stop.is_set():
            await asyncio.sleep(600)
            try:
                self.cache.save()
                self.issues.save()
                self.stats.save()
            except Exception as e:
                log.warning("Cache/Issues/Statistik speichern: %s", e)

    # ----------------------------------------------------------- Handlers --
    def _render(self, layout: str | None = None, card: str | None = None):
        cfg = self.cfg if not layout else replace(self.cfg, layout=layout.upper())
        r = Renderer(cfg, self.logos, idle=self.idle) if layout else self.renderer
        return r.render(self.model, time.time(), card=card or None)

    async def h_frame(self, req: web.Request) -> web.Response:
        now = time.time()
        if self.last_frame_fetch and now - self.last_frame_fetch > 30:
            self.issues.note("esp32_gap", "frames", gap_s=round(now - self.last_frame_fetch))
        self.last_frame_fetch = now
        self.frame_count += 1
        img = self._render()
        body = self.renderer.frame_bytes(img, brightness_for(self.cfg, time.time()))
        return web.Response(body=body, content_type="application/octet-stream",
                            headers={"Cache-Control": "no-store"})

    async def h_frame_png(self, req: web.Request) -> web.Response:
        img = self._render(req.query.get("layout"), req.query.get("card"))
        scale = max(1, min(40, int(req.query.get("scale", "6"))))
        gap = req.query.get("gap", "1") != "0"
        return web.Response(body=self.renderer.png(img, scale, gap), content_type="image/png",
                            headers={"Cache-Control": "no-store"})

    async def h_index(self, req: web.Request) -> web.Response:
        layout = req.query.get("layout", self.cfg.layout).upper()
        if layout not in ("AUTO", "A", "B", "C"):
            layout = "AUTO"
        card = req.query.get("card", "")
        if card not in self.idle.cards:
            card = ""
        links = "".join(f'<a href="/?layout={layout}&card={c}">{c}</a>' for c in self.idle.cards)
        return web.Response(text=PREVIEW_HTML % {"layout": layout, "card": card, "cards": links},
                            content_type="text/html")

    async def h_display(self, req: web.Request) -> web.Response:
        return web.Response(text=self.model.to_json(), content_type="application/json")

    def _issues_report(self) -> dict:
        now = time.time()
        return self.issues.report(
            uptime_h=round((now - self.started) / 3600, 1),
            polls_since_start=self.counters,
            logos_missing=sorted(self.logos.missing()),
        )

    async def h_issues(self, req: web.Request) -> web.Response:
        return web.json_response(self._issues_report(),
                                 dumps=lambda o: json.dumps(o, ensure_ascii=False, indent=1))

    async def h_issues_clear(self, req: web.Request) -> web.Response:
        self.issues.clear()
        self.issues.save()
        return web.json_response(self._issues_report())

    async def h_stats(self, req: web.Request) -> web.Response:
        return web.json_response(self.stats.report(),
                                 dumps=lambda o: json.dumps(o, ensure_ascii=False, indent=1))

    async def h_idle(self, req: web.Request) -> web.Response:
        return web.json_response(self.idle.status(self.model, time.time()),
                                 dumps=lambda o: json.dumps(o, ensure_ascii=False, indent=1))

    async def h_health(self, req: web.Request) -> web.Response:
        now = time.time()
        esp_age = now - self.last_frame_fetch if self.last_frame_fetch else None
        body = {
            "ok": True,
            "mock": self.cfg.mock,
            "layout": self.cfg.layout,
            "brightness_now": brightness_for(self.cfg, now),
            "model_age_s": round(now - self.last_model_ok) if self.last_model_ok else None,
            "model_stale": self.model.stale,
            "flights": len(self.model.flights),
            "source": None if self.cfg.mock else {
                "configured": self.cfg.source,
                "active": self.active_source,
                "poll_interval_s": self.poll_interval(),
                "adsblol_last_ok_age_s": round(now - self.adsblol.last_ok) if self.adsblol and self.adsblol.last_ok else None,
                "adsblol_last_error": self.adsblol.last_error if self.adsblol else None,
            },
            "opensky": None if self.cfg.mock else {
                "anonymous": self.opensky.anonymous,
                "min_interval_s": self.opensky_interval(),
                "last_ok_age_s": round(now - self.opensky.last_ok) if self.opensky.last_ok else None,
                "credits_remaining": self.opensky.credits_remaining,
                "last_error": self.opensky.last_error,
            },
            "adsbdb_last_error": self.adsb.last_error if self.adsb else None,
            "adsbim_last_error": self.adsbim.last_error if getattr(self, "adsbim", None) else None,
            "track_last_error": self.origins.last_error if getattr(self, "origins", None) else None,
            "cache_entries": len(self.cache),
            "issues": self.issues.report()["counts"],
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
        return web.json_response(body)

    # ------------------------------------------------------------ Lifecycle --
    async def start(self) -> web.AppRunner:
        self.session = aiohttp.ClientSession(headers={"User-Agent": "flightpanel/1.0"})
        self.logos.session = self.session
        self.opensky = OpenSky(self.session, self.cfg.opensky_client_id, self.cfg.opensky_client_secret)
        self.adsb = AdsbDb(self.session, self.cfg.adsbdb_base, self.cache)
        self.adsblol = AdsbLol(self.session)
        self.adsb.issues = self.issues
        self.adsbim = AdsbIm(self.session, self.cache) if self.cfg.route_adsbim else None
        self.origins = (Origins(self.session, self.cache, load_airports(self.cfg.data_dir))
                        if self.cfg.track_origin else None)
        for src in (self.adsbim, self.origins):
            if src is not None:
                src.issues = self.issues
        if self.origins is not None and not self.opensky.anonymous:
            async def opensky_auth() -> dict:
                await self.opensky._ensure_token()
                return {"Authorization": f"Bearer {self.opensky._token}"} if self.opensky._token else {}
            self.origins.auth = opensky_auth
        if self.metar:
            self.metar.session = self.session
        if self.iss:
            self.iss.session = self.session
        if not self.cfg.mock and self.cfg.source == "opensky" and self.opensky.anonymous:
            log.warning("Keine OpenSky-Zugangsdaten: anonymer Zugriff (400 Credits/Tag), "
                        "Abruf nur alle %d s. Mit OPENSKY_CLIENT_ID/SECRET alle %d s.",
                        self.poll_interval(), self.cfg.poll_interval)
        app = web.Application()
        app.add_routes([
            web.get("/", self.h_index),
            web.get("/frame", self.h_frame),
            web.get("/frame.png", self.h_frame_png),
            web.get("/display.json", self.h_display),
            web.get("/health", self.h_health),
            web.get("/issues", self.h_issues),
            web.post("/issues/clear", self.h_issues_clear),
            web.get("/stats", self.h_stats),
            web.get("/idle", self.h_idle),
        ])
        runner = web.AppRunner(app, access_log=None)
        await runner.setup()
        await web.TCPSite(runner, "0.0.0.0", self.cfg.http_port).start()
        asyncio.create_task(self.poll_loop())
        asyncio.create_task(self.cache_loop())
        if self.metar and not self.cfg.mock:
            asyncio.create_task(self.metar_loop())
        if self.iss and not self.cfg.mock:
            asyncio.create_task(self.astro_loop())
        log.info("flightpanel läuft auf :%d (mock=%s, layout=%s, %dx%d)",
                 self.cfg.http_port, self.cfg.mock, self.cfg.layout, self.cfg.width, self.cfg.height)
        return runner

    async def stop(self, runner: web.AppRunner) -> None:
        self._stop.set()
        self.cache.save()
        self.issues.save()
        self.stats.save()
        await runner.cleanup()
        if self.session:
            await self.session.close()


async def _main() -> None:
    logging.basicConfig(level=getattr(logging, CFG.log_level.upper(), logging.INFO),
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    app = App(CFG)
    runner = await app.start()
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop.set)
        except NotImplementedError:
            pass
    await stop.wait()
    await app.stop(runner)


def main() -> None:
    asyncio.run(_main())


if __name__ == "__main__":
    main()
