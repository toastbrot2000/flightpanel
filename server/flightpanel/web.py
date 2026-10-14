"""HTTP: Bild für den ESP32, Vorschau im Browser, Zustand und Statistik. Bedient wird das Panel am LAN-Port
(LAN_PANEL); /health, /ok und /issues gelten für den ganzen Server.

  GET  /              Vorschauseite (lädt /frame.png alle 2 s neu)
  GET  /frame         Binär für den ESP32: [1, Helligkeit, Breite, Höhe] + RGB
  GET  /frame.png     Vorschau; ?layout=A|B|C, ?card=<id> erzwingt eine Leerlauf-Karte
                      (mit Beispieldaten, wenn sie gerade nichts zu sagen hat), ?scale=N, ?gap=0
  GET  /display.json  aktuelles Display-Modell, mit allen Flugzeugen im Radius (nearby)
  GET  /health        Zustand der Quellen, Logos, letzter ESP32-Abruf
  GET  /ok            200 "ok", sonst 503 mit Gründen: für Docker-Healthcheck und Uptime-Monitore
  GET  /issues        gesammelte Auffälligkeiten (issues.py); POST /issues/clear leert
  GET  /stats         Statistik und Sammelalbum (stats.py)
  GET  /idle          Leerlauf-Karten: welche gerade etwas zu sagen haben, welche dran ist
"""

import json
import time
from dataclasses import replace
from typing import TYPE_CHECKING

from aiohttp import web
from PIL import Image

from .config import LAYOUTS, parse_layout
from .render import Renderer

if TYPE_CHECKING:
    from .main import App

ESP32_GAP_S = 30        # so lange ohne /frame: als Lücke in /issues

PREVIEW_HTML = """<!doctype html><meta charset="utf-8"><title>flightpanel</title>
<style>body{background:#111;color:#ccc;font:14px system-ui;margin:24px}
img{image-rendering:pixelated;display:block;margin:12px 0;border:1px solid #333}
a{color:#8ab;margin-right:14px}</style>
<h2>flightpanel</h2>
<img id="f" src="/frame.png?layout=%(layout)s&card=%(card)s" alt="frame">
<div>Layout:
%(layouts)s
| <a href="/display.json">display.json</a><a href="/health">health</a><a href="/issues">issues</a>
<a href="/stats">stats</a><a href="/idle">idle</a></div>
<p id="s" style="color:#d9a33a"></p>
<p>Leerlauf-Karte erzwingen (passt die Lage nicht, mit Beispieldaten): <a href="/">aus</a> %(cards)s</p>
<script>
setInterval(() => {
  document.getElementById('f').src = '/frame.png?layout=%(layout)s&card=%(card)s&t=' + Date.now();
}, 2000);
const status = () => fetch('/health').then(r => r.json()).then(h => {
  document.getElementById('s').textContent =
    h.brightness_now === 0 ? 'Ruhezeit (QUIET_HOURS): Das Panel ist dunkel, der Server ruft nichts ab.'
    : h.model_stale ? 'Keine frischen Flugdaten: Das Bild ist gedimmt, siehe /health und /issues.' : '';
}).catch(() => {});
status();
setInterval(status, 10000);
</script>
"""


def make_web_app(app: "App") -> web.Application:
    h = _Handlers(app)
    web_app = web.Application()
    web_app.add_routes([
        web.get("/", h.index),
        web.get("/frame", h.frame),
        web.get("/frame.png", h.frame_png),
        web.get("/display.json", h.display),
        web.get("/health", h.health),
        web.get("/ok", h.ok),
        web.get("/issues", h.issues),
        web.post("/issues/clear", h.issues_clear),
        web.get("/stats", h.stats),
        web.get("/idle", h.idle),
    ])
    return web_app


def _pretty(obj) -> web.Response:
    return web.json_response(obj, dumps=lambda o: json.dumps(o, ensure_ascii=False, indent=1))


class _Handlers:
    def __init__(self, app: "App"):
        self.app = app

    def _render(self, layout: str | None = None, card: str | None = None) -> Image.Image:
        p = self.app.lan
        r = (Renderer(replace(p.cfg, layout=layout), self.app.logos, idle=p.idle, rotation=p.renderer.rotation)
             if layout else p.renderer)
        return r.render(p.model, time.time(), card=card or None)

    @staticmethod
    def _layout(req: web.Request) -> str | None:
        """?layout=, wie LAYOUT in .env; Unbekanntes zählt wie nicht angegeben."""
        try:
            return parse_layout(req.query["layout"])
        except (KeyError, ValueError):
            return None

    async def frame(self, req: web.Request) -> web.Response:
        app, p = self.app, self.app.lan
        now = time.time()
        if p.last_frame_fetch and now - p.last_frame_fetch > ESP32_GAP_S:
            app.issues.note("esp32_gap", "frames", gap_s=round(now - p.last_frame_fetch))
        p.last_frame_fetch = now
        p.frame_count += 1
        body = p.renderer.frame_bytes(self._render(), p.renderer.brightness(now))
        return web.Response(body=body, content_type="application/octet-stream", headers={"Cache-Control": "no-store"})

    async def frame_png(self, req: web.Request) -> web.Response:
        img = self._render(self._layout(req), req.query.get("card"))
        try:
            scale = max(1, min(40, int(req.query.get("scale", "6"))))
        except ValueError:
            scale = 6
        gap = req.query.get("gap", "1") != "0"
        return web.Response(body=self.app.lan.renderer.png(img, scale, gap), content_type="image/png",
                            headers={"Cache-Control": "no-store"})

    async def index(self, req: web.Request) -> web.Response:
        p = self.app.lan
        layout = self._layout(req) or p.cfg.layout
        card = req.query.get("card", "")
        if card not in p.idle.cards:
            card = ""
        links = "".join(f'<a href="/?layout={layout}&card={c}">{c}</a>' for c in p.idle.cards)
        layouts = "".join(f'<a href="/?layout={x}">{x}</a>' for x in LAYOUTS)
        return web.Response(text=PREVIEW_HTML % {"layout": layout, "card": card, "cards": links,
                                                 "layouts": layouts}, content_type="text/html")

    async def display(self, req: web.Request) -> web.Response:
        return web.Response(text=self.app.lan.model.to_json(), content_type="application/json")

    async def ok(self, req: web.Request) -> web.Response:
        problems = self.app.problems(time.time())
        return web.Response(text="\n".join(problems or ["ok"]) + "\n", status=503 if problems else 200,
                            headers={"Cache-Control": "no-store"})

    async def health(self, req: web.Request) -> web.Response:
        return web.json_response(self.app.health())

    async def issues(self, req: web.Request) -> web.Response:
        return _pretty(self.app.issues_report())

    async def issues_clear(self, req: web.Request) -> web.Response:
        self.app.issues.clear()
        self.app.issues.save()
        return web.json_response(self.app.issues_report())

    async def stats(self, req: web.Request) -> web.Response:
        # Landerichtung: für alle Panels gleich
        return _pretty({**self.app.lan.stats.report(), "course": self.app.airfield.report()})

    async def idle(self, req: web.Request) -> web.Response:
        p = self.app.lan
        return _pretty(p.idle.status(p.model, time.time()))
