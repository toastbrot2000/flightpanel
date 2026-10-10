"""HTTP: Bild für den ESP32, Vorschau im Browser, Zustand und Statistik.

  GET  /              Vorschauseite (lädt /frame.png alle 2 s neu)
  GET  /frame         Binär für den ESP32: [1, Helligkeit, Breite, Höhe] + RGB
  GET  /frame.png     Vorschau; ?layout=AUTO|A|B|C, ?card=<id> erzwingt eine Leerlauf-Karte
                      (mit Beispieldaten, wenn sie gerade nichts zu sagen hat), ?scale=N, ?gap=0
  GET  /display.json  aktuelles Display-Modell, mit allen Flugzeugen im Radius (nearby)
  GET  /health        Zustand der Quellen, Logos, letzter ESP32-Abruf
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

from .render import Renderer

if TYPE_CHECKING:
    from .main import App

ESP32_GAP_S = 30        # so lange ohne /frame: als Lücke in /issues
LAYOUTS = ("AUTO", "A", "B", "C")

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
<script>
setInterval(() => {
  document.getElementById('f').src = '/frame.png?layout=%(layout)s&card=%(card)s&t=' + Date.now();
}, 2000);
</script>
"""


def make_web_app(panel: "App") -> web.Application:
    h = _Handlers(panel)
    app = web.Application()
    app.add_routes([
        web.get("/", h.index),
        web.get("/frame", h.frame),
        web.get("/frame.png", h.frame_png),
        web.get("/display.json", h.display),
        web.get("/health", h.health),
        web.get("/issues", h.issues),
        web.post("/issues/clear", h.issues_clear),
        web.get("/stats", h.stats),
        web.get("/idle", h.idle),
    ])
    return app


def _pretty(obj) -> web.Response:
    return web.json_response(obj, dumps=lambda o: json.dumps(o, ensure_ascii=False, indent=1))


class _Handlers:
    def __init__(self, panel: "App"):
        self.panel = panel

    def _render(self, layout: str | None = None, card: str | None = None) -> Image.Image:
        p = self.panel
        r = Renderer(replace(p.cfg, layout=layout.upper()), p.logos, idle=p.idle) if layout else p.renderer
        return r.render(p.model, time.time(), card=card or None)

    async def frame(self, req: web.Request) -> web.Response:
        p = self.panel
        now = time.time()
        if p.last_frame_fetch and now - p.last_frame_fetch > ESP32_GAP_S:
            p.issues.note("esp32_gap", "frames", gap_s=round(now - p.last_frame_fetch))
        p.last_frame_fetch = now
        p.frame_count += 1
        body = p.renderer.frame_bytes(self._render(), p.cfg.brightness(now))
        return web.Response(body=body, content_type="application/octet-stream", headers={"Cache-Control": "no-store"})

    async def frame_png(self, req: web.Request) -> web.Response:
        img = self._render(req.query.get("layout"), req.query.get("card"))
        try:
            scale = max(1, min(40, int(req.query.get("scale", "6"))))
        except ValueError:
            scale = 6
        gap = req.query.get("gap", "1") != "0"
        return web.Response(body=self.panel.renderer.png(img, scale, gap), content_type="image/png",
                            headers={"Cache-Control": "no-store"})

    async def index(self, req: web.Request) -> web.Response:
        p = self.panel
        layout = req.query.get("layout", p.cfg.layout).upper()
        if layout not in LAYOUTS:
            layout = "AUTO"
        card = req.query.get("card", "")
        if card not in p.idle.cards:
            card = ""
        links = "".join(f'<a href="/?layout={layout}&card={c}">{c}</a>' for c in p.idle.cards)
        return web.Response(text=PREVIEW_HTML % {"layout": layout, "card": card, "cards": links},
                            content_type="text/html")

    async def display(self, req: web.Request) -> web.Response:
        return web.Response(text=self.panel.model.to_json(), content_type="application/json")

    async def health(self, req: web.Request) -> web.Response:
        return web.json_response(self.panel.health())

    async def issues(self, req: web.Request) -> web.Response:
        return _pretty(self.panel.issues_report())

    async def issues_clear(self, req: web.Request) -> web.Response:
        self.panel.issues.clear()
        self.panel.issues.save()
        return web.json_response(self.panel.issues_report())

    async def stats(self, req: web.Request) -> web.Response:
        return _pretty(self.panel.stats.report())

    async def idle(self, req: web.Request) -> web.Response:
        return _pretty(self.panel.idle.status(self.panel.model, time.time()))
