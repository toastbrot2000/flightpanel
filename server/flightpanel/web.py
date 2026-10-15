"""HTTP: Bild für den ESP32, Vorschau im Browser, Zustand und Statistik.

Ein Satz Handler (Handlers) für jeden Port; welches Panel eine Anfrage meint, sagt ein Resolver (resolve(req) ->
Panel). Am LAN-Port (make_web_app, HTTP_PORT) ist es immer das Panel am LAN-Port (LAN_PANEL), ohne Anmeldung;
/health, /ok und /issues gelten für den ganzen Server. Am Zugang von außen (make_public_app, PUBLIC_PORT, nur mit
den Schaltern in .env) ist es das Panel der geprüften Anmeldung (access.Gate); dort gibt es nur, was
make_public_app mit seiner Identitätsklasse aufzählt, und /health, /ok, /issues und /frame nicht. Jede Route meldet
add() an und vermerkt sie samt Klasse (ROUTES).

  GET  /                    Vorschauseite (lädt /frame.png alle 2 s neu)
  GET  /panel/frame         Binär für den ESP32: [1, Helligkeit, Breite, Höhe] + RGB. ETag ist ein kurzer Hash der
                            Bytes, ein gleiches Bild kommt auf If-None-Match als 304 ohne Inhalt. Diagnose im Header
                            X-Panel (parse_diag). /frame ist dasselbe (ältere Firmware)
  GET  /panel/firmware.txt  Firmware-Manifest aus dem Kanal des Panels (DATA_DIR/firmware/<Kanal>/), nie gecacht
  GET  /panel/firmware.bin?v=<Version>
                            das Image dazu, nur zur Version im Manifest; einen Tag im Cache (Cloudflare)
  GET  /frame.png           Vorschau; ?layout=A|B|C, ?card=<id> erzwingt eine Leerlauf-Karte
                            (mit Beispieldaten, wenn sie gerade nichts zu sagen hat), ?scale=N, ?gap=0
  GET  /display.json        aktuelles Display-Modell, mit allen Flugzeugen im Radius (nearby)
  GET  /health              Zustand der Quellen, Logos, letzter ESP32-Abruf, eine Zeile je Panel
  GET  /ok                  200 "ok", sonst 503 mit Gründen: für Docker-Healthcheck und Uptime-Monitore
  GET  /issues              gesammelte Auffälligkeiten (issues.py); POST /issues/clear leert
  GET  /stats               Statistik und Sammelalbum (stats.py)
  GET  /idle                Leerlauf-Karten: welche gerade etwas zu sagen haben, welche dran ist
  GET  /settings            Einstellungsseite (settings.py), nur mit SETTINGS_PAGE=1; POST speichert
  GET  /static/<Name>       Skripte und Stile der Seiten: eine feste Liste von Dateien, nie ein Verzeichnis

Der LAN-Port hat keine Anmeldung und gehört nur ins LAN. Darum (lan_guard):
  - Er antwortet nur unter Namen aus dem LAN (lan_host): IP-Adressen, localhost, Namen ohne Punkt, Namen auf
    LAN_SUFFIXES (*.local, *.home.arpa, *.internal) und LAN_HOSTS; sonst 421. Gegen DNS-Rebinding: Eine fremde
    Website lässt ihren Namen auf den Server zeigen, dann schickt der Browser im LAN ihren Namen als Host. Namen, die
    ein Router vergibt (nas.fritz.box), stehen nur, wenn der Betreiber sie in LAN_HOSTS einträgt.
  - Ist der Zugang von außen eingerichtet (Config.public), sagt er 403 zu allem, was Cloudflare-Header trägt: Dann
    hängt der Tunnel am falschen Port.
  - Schreiben nur von den Seiten dieses Servers: Origin gleich dem Host der Anfrage (same_origin) und aus dem LAN
    (lan_origin); POST /issues/clear auch ganz ohne (curl in docs/configuration.md).
  - Jede Antwort trägt SECURITY_HEADERS, auch Fehler und statische Dateien: Skripte und Stile kommen nur als Dateien
    vom Server selbst, nie aus der Seite.
"""

import hashlib
import html
import ipaddress
import json
import logging
import os
import re
import time
from collections.abc import Awaitable, Callable
from dataclasses import replace
from time import monotonic
from typing import TYPE_CHECKING

from aiohttp import web
from PIL import Image

from .cards import ABOUT, GROUPS
from .config import LAYOUT_NAMES, LAYOUTS, parse_layout
from .model import ARR, DEP, DisplayModel, Flight
from .render import Renderer

if TYPE_CHECKING:
    from .access import Gate
    from .main import App
    from .panel import Panel

log = logging.getLogger("web")

Handler = Callable[[web.Request], Awaitable[web.StreamResponse]]
Resolver = Callable[[web.Request], "Panel | None"]

SECURITY_HEADERS = {
    "Content-Security-Policy": "default-src 'self'; frame-ancestors 'none'",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
}
CF_HEADERS = ("Cf-Ray", "Cf-Connecting-Ip", "Cf-Access-Jwt-Assertion")
# Endungen, die niemand öffentlich registrieren kann: .local (mDNS), .home.arpa (RFC 8375) und .internal (von ICANN
# für private Netze reserviert). Nicht dabei sind die Namen der Router: .box ist eine öffentliche Endung (wer
# fritz.box besitzt, könnte darunter jeden Namen auf den Server zeigen lassen), .lan und .home sind nicht reserviert.
# Solche Namen trägt der Betreiber bewusst in LAN_HOSTS ein.
LAN_SUFFIXES = (".local", ".home.arpa", ".internal")
LAN_LINKS = ("/display.json", "/health", "/issues", "/stats", "/idle")     # was die Vorschau am LAN-Port verlinkt
PUBLIC_LINKS = ("/display.json", "/stats", "/idle")     # ... am Zugang von außen: nur Seiten des eigenen Panels
LAN_MAX_SCALE = 40      # /frame.png?scale=
PUBLIC_MAX_SCALE = 12   # ... von außen: 1536 x 384 Pixel genügen, und jeder Maßstab bleibt im Speicher (render.png)
PUBLIC_MAX_BODY = 16 * 1024     # größte Anfrage am Zugang von außen (aiohttp client_max_size)
# Körper bleiben, wie sie kommen: aiohttp entpackt gzip/deflate sonst schon beim Empfang, vor jeder Prüfung und in der
# Ereignisschleife, und client_max_size zählt erst danach. Kein Panel und keine Seite schickt gepackte Körper; so
# einer ist dann einfach kein JSON. Für beide Ports.
HANDLER_ARGS = {"auto_decompress": False}
WARN_EVERY_S = 60       # Absagen des Schutzes stehen höchstens so oft im Log
# Die Seiten eines Panels gehören zu ihm, am Zugang von außen zu einer Anmeldung: Ein Cache davor (etwa eine Regel
# "Cache Everything" bei Cloudflare) gäbe sonst einem Freund die Seite eines anderen. Nur firmware.bin darf hinein.
NO_STORE = {"Cache-Control": "no-store"}

NOT_LAN = ("Unter diesem Namen antwortet der LAN-Port nicht: nur unter IP-Adressen, localhost, Namen ohne Punkt und "
           "Namen auf .local, .home.arpa und .internal (Schutz vor DNS-Rebinding). Weitere Namen, auch den, den der "
           "Router vergibt und der etwa als Server-Adresse im Einrichtungs-WLAN des Panels steht (oder als SERVER_URL "
           "in seiner secrets.h), in .env unter LAN_HOSTS eintragen, etwa LAN_HOSTS=nas.fritz.box, und den Server neu "
           "starten.\n")
VIA_CLOUDFLARE = ("Diese Anfrage kam über Cloudflare an den LAN-Port. Der Tunnel gehört an PUBLIC_PORT, nie an den "
                  "LAN-Port: Der hat keine Anmeldung (docs/remote-panels.md).\n")
FOREIGN_ORIGIN = "Schreiben nur von den Seiten dieses Servers aus dem LAN (Origin fremd).\n"

# Beispielflüge für /frame.png?sample=1: die Aufteilungen zur Wahl, auch wenn gerade nichts fliegt
SAMPLE_FLIGHTS = (
    Flight(icao24="sample1", callsign="DLH2YM", status=ARR, dist_km=6.2, bearing=250, alt_ft=3200, speed_kt=160,
           track=70, vrate_fpm=-800, origin="MUC", dest="BER", airline="Lufthansa", airline_icao="DLH",
           airline_iata="LH", actype="A320", land_eta_s=240, seq=1),
    Flight(icao24="sample2", callsign="EZY45KP", status=DEP, dist_km=11.5, bearing=80, alt_ft=6100, speed_kt=250,
           track=260, vrate_fpm=2200, origin="BER", dest="LGW", airline="easyJet", airline_icao="EZY",
           airline_iata="U2", actype="A20N"),
)

# Was die Vorschau unter "Technik" verlinkt, mit Namen
LINK_NAMES = {"/display.json": "Flugdaten (JSON)", "/health": "Zustand des Servers", "/issues": "Auffälligkeiten",
              "/stats": "Statistik (JSON)", "/idle": "Leerlauf-Karten (JSON)"}

HEAD = """<!doctype html>
<html lang="de">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<meta name="color-scheme" content="dark">
<meta name="theme-color" content="#0a1214">
<title>{title}</title>
<link rel="stylesheet" href="/static/preview.css">
{extra}</head>
<body>
"""


def _by_group(cards) -> list[tuple[str, list]]:
    """Leerlauf-Karten nach Gruppe, in der Reihenfolge ihres ersten Auftretens."""
    groups: dict[str, list] = {}
    for c in cards:
        groups.setdefault(c.group, []).append(c)
    return list(groups.items())


def page_top(title: str, current: str, settings: bool, extra: str = "") -> str:
    """Kopf jeder Seite: Titel, Stile und Skripte (extra), die Marke und, mit Einstellungsseite, die zwei Reiter.
    current: "/" oder "/settings"."""
    tabs = ""
    if settings:
        tabs = '<nav class="tabs" aria-label="Seiten">' + "".join(
            f'<a href="{path}"{" aria-current=\"page\"" if path == current else ""}>{name}</a>'
            for path, name in (("/", "Live"), ("/settings", "Einstellungen"))) + "</nav>"
    return (HEAD.format(title=html.escape(title), extra=extra)
            + f'<header class="topbar"><a class="brand" href="/"><span class="brand-mark" aria-hidden="true"></span>'
              f'flightpanel</a>{tabs}</header>\n')


PREVIEW_HTML = """<main class="page live">
<section class="stage" aria-label="Das Panel">
<div class="device"><img id="f" class="screen" src="{frame}" alt="Was das Panel gerade zeigt" width="768"
 height="192"></div>
<p class="state" id="s" data-mode="{mode}" aria-live="polite"{health}><span class="dot" aria-hidden="true"></span><span
 id="s-text">{state}</span>{back}</p>
</section>
<div class="columns">
<section class="sky" aria-labelledby="sky-title">
<div class="section-head"><h2 id="sky-title">In der Nähe</h2><p class="meta" id="sky-meta"></p></div>
<ol class="flights" id="flights"></ol>
<p class="empty" id="flights-empty">Lade Flugzeuge …</p>
<p class="next" id="next"></p>
</section>
<section class="look" aria-labelledby="look-title">
<h2 id="look-title">Ansicht ausprobieren</h2>
<p class="hint">Nur hier in der Vorschau, das Panel selbst bleibt, wie es ist. Fliegt gerade nichts, mit
Beispielflügen.{keep}</p>
<div class="segmented" role="group" aria-label="Aufteilung">{layouts}</div>
<h3>Karten im Leerlauf</h3>
<p class="hint">Was das Panel zeigt, wenn kein Flug zu sehen ist. Passt die Lage gerade nicht, mit Beispieldaten.</p>
<div class="chips">{live}</div>
{cards}
</section>
</div>
</main>
<footer class="foot"><details><summary>Technik</summary><ul class="tech">{links}</ul></details></footer>
</body>
</html>
"""


# ---------------------------------------------------------------------------------------- statische Dateien --
STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
STATIC_TYPES = {".css": "text/css", ".js": "text/javascript"}


def read_static(*names: str) -> dict[str, bytes]:
    """Skripte und Stile aus static/, gelesen beim Import: Ausgeliefert wird nur, was hier mit Namen steht, nie ein
    Verzeichnis."""
    out = {}
    for name in names:
        with open(os.path.join(STATIC_DIR, name), "rb") as f:
            out[name] = f.read()
    return out


PREVIEW_STATIC = read_static("preview.css", "preview.js")


def add_static(web_app: web.Application, files: dict[str, bytes], kind: str | None = None) -> None:
    """Jede Datei als eigene Route /static/<Name> (add), am Zugang von außen mit der Klasse kind."""
    for name, body in files.items():
        add(web_app, "GET", f"/static/{name}", _static(name, body), kind)


def _static(name: str, body: bytes) -> Handler:
    content_type = STATIC_TYPES[os.path.splitext(name)[1]]

    async def serve(req: web.Request) -> web.Response:
        return web.Response(body=body, content_type=content_type, charset="utf-8",
                            headers={"Cache-Control": "no-cache"})
    return serve


# ---------------------------------------------------------------------------------------------- Routen --
ROUTES = web.AppKey("routes", dict)     # jede Route -> ihre Identitätsklasse (Zugang von außen), am LAN-Port None


def add(web_app: web.Application, method: str, path: str, handler: Handler, kind: str | None = None) -> None:
    """Eine Route anmelden, GET samt HEAD, und mit ihrer Identitätsklasse kind in web_app[ROUTES] vermerken (am
    Zugang von außen email, token oder public, siehe access.Gate; am LAN-Port None). Jede Route geht hier durch: Eine
    Route an diesem Weg vorbei hätte keine Klasse, und der Zugang von außen wiese sie immer ab."""
    for m in (method, "HEAD") if method == "GET" else (method,):
        web_app[ROUTES][web_app.router.add_route(m, path, handler)] = kind


def add_panel_routes(web_app: web.Application, h: "Handlers") -> None:
    """Was ein Panel ausmacht: Bild, Firmware, Vorschau, Modell, Statistik, Leerlauf-Karten, mit Skripten und Stilen
    der Vorschau. Für jeden Port gleich; welches Panel, sagt der Resolver von h."""
    for path in ("/panel/frame", "/frame"):
        add(web_app, "GET", path, h.frame)
    add(web_app, "GET", "/panel/firmware.txt", h.firmware_txt)
    add(web_app, "GET", "/panel/firmware.bin", h.firmware_bin)
    add(web_app, "GET", "/", h.index)
    add(web_app, "GET", "/frame.png", h.frame_png)
    add(web_app, "GET", "/display.json", h.display)
    add(web_app, "GET", "/stats", h.stats)
    add(web_app, "GET", "/idle", h.idle)
    add_static(web_app, PREVIEW_STATIC)


def make_web_app(app: "App") -> web.Application:
    """Der LAN-Port (HTTP_PORT): das Panel am LAN-Port ohne Anmeldung, dazu /health, /ok und /issues für den ganzen
    Server, mit SETTINGS_PAGE=1 die Einstellungsseite. Geschützt wie oben beschrieben."""
    cfg = app.server_cfg
    web_app = web.Application(middlewares=[lan_guard(app)], handler_args=HANDLER_ARGS)
    web_app[ROUTES] = {}
    web_app.on_response_prepare.append(security_headers)
    links = LAN_LINKS + (("/settings",) if cfg.settings_page else ())
    h = Handlers(app, lambda req: app.lan,
                 lambda origin, req: same_origin(origin, req) and lan_origin(origin, cfg.lan_hosts), links)
    add_panel_routes(web_app, h)
    add(web_app, "GET", "/health", h.health)
    add(web_app, "GET", "/ok", h.ok)
    add(web_app, "GET", "/issues", h.issues)
    add(web_app, "POST", "/issues/clear", h.issues_clear)
    if cfg.settings_page:
        # nur mit dem Schalter geladen
        from .settings import SettingsPage
        SettingsPage(h).add_routes(web_app)
    return web_app


def make_public_app(app: "App", gate: "Gate") -> web.Application:
    """Der Zugang von außen (PUBLIC_PORT): nur hinter Cloudflare Access, nur mit den Schaltern in .env (main.App.start
    ruft das nur dann auf). Dieselben Handler wie am LAN-Port; das Panel kommt aus der geprüften Anmeldung (gate),
    nie aus der Adresse. Es gibt genau die Routen unten, jede mit ihrer Identitätsklasse (access.Gate): kein /health,
    /ok, /issues, kein /frame. Schreiben (Einstellungen) nur mit Origin genau https://<PUBLIC_HOST>."""
    from .access import EMAIL, PUBLIC, TOKEN
    cfg = app.server_cfg
    routes: dict = {}
    web_app = web.Application(middlewares=[gate.middleware(routes)], client_max_size=PUBLIC_MAX_BODY,
                              handler_args=HANDLER_ARGS)
    web_app[ROUTES] = routes
    web_app.on_response_prepare.append(security_headers)
    origin = f"https://{cfg.public_host}"
    links = PUBLIC_LINKS + (("/settings",) if cfg.settings_page else ())
    h = Handlers(app, _panel_of(app, gate), lambda o, req: o == origin, links, max_scale=PUBLIC_MAX_SCALE)
    for method, path, kind, handler in (
            ("GET", "/", EMAIL, h.index),                       # Vorschau, verlinkt nur das eigene Panel
            ("GET", "/frame.png", EMAIL, h.frame_png),
            ("GET", "/display.json", EMAIL, h.display),
            ("GET", "/stats", EMAIL, h.stats),
            ("GET", "/idle", EMAIL, h.idle),
            ("GET", "/panel/frame", TOKEN, h.frame),            # das Bild fürs ESP32, nur mit dessen Service-Token
            ("GET", "/panel/firmware.txt", PUBLIC, h.firmware_txt),     # signiert, ohne Geheimnisse: Rettungsweg
            ("GET", "/panel/firmware.bin", PUBLIC, h.firmware_bin)):
        add(web_app, method, path, handler, kind)
    add_static(web_app, PREVIEW_STATIC, EMAIL)
    if cfg.settings_page:
        from .settings import SettingsPage
        SettingsPage(h).add_routes(web_app, EMAIL)
    return web_app


def _panel_of(app: "App", gate: "Gate") -> Resolver:
    """Der Resolver am Zugang von außen: das Panel der geprüften Anmeldung (Gate.panel_id); None nur auf den offenen
    Firmware-Routen ohne Panel-Token (dann gilt stable). Hat panels add es eben erst angelegt, kennt App es vielleicht
    noch nicht (die Abrufschleife zieht spätestens nach einer Minute nach): einmal nachziehen, sonst 503."""
    def resolve(req: web.Request) -> "Panel | None":
        pid = gate.panel_id(req)
        if pid is None:
            return None
        if pid not in app.panels:
            try:
                app._sync_panels()
            except Exception as e:                  # etwa Datenbank gesperrt: dieses Mal nicht
                log.warning("Panels nicht nachgezogen: %s", e)
        p = app.panels.get(pid)
        if p is None:
            raise web.HTTPServiceUnavailable(text="Dieses Panel ist gerade nicht bereit, gleich noch einmal.\n")
        return p
    return resolve


# ------------------------------------------------------------------------------------------------ Schutz --
async def security_headers(req: web.Request, resp: web.StreamResponse) -> None:
    """Für jede Antwort, kurz bevor sie rausgeht (on_response_prepare): auch Fehler, 404 und statische Dateien."""
    resp.headers.update(SECURITY_HEADERS)


_HOST = re.compile(r"\[(?P<v6>[0-9a-f:.]+)\](?::[0-9]{1,5})?|(?P<name>[a-z0-9_.-]+)(?::[0-9]{1,5})?")
_NAME = re.compile(r"[a-z0-9_-]+(?:\.[a-z0-9_-]+)*")


def lan_host(host: str, extra: tuple[str, ...] = ()) -> bool:
    """Darf ein Browser den LAN-Port unter diesem Namen ansprechen (Host-Header, oder was im Origin hinter :// steht)?
    IP-Adressen (IPv6 in Klammern), localhost, Namen ohne Punkt (nas), Namen auf LAN_SUFFIXES (nas.local) und die
    Namen in extra (LAN_HOSTS, etwa nas.fritz.box), jeweils mit oder ohne Port. Solche Namen kann niemand öffentlich
    registrieren und auf den Server zeigen lassen, die in LAN_HOSTS hat der Betreiber selbst gewählt; IP-Adressen
    lassen sich nicht umbiegen. Nur ASCII: Ein Browser schickt Namen mit Umlauten als xn--…, und manches andere
    Zeichen würde klein geschrieben erst zu einem Buchstaben (K, das Kelvin-Zeichen)."""
    if not host.isascii():
        return False
    m = _HOST.fullmatch(host.strip().lower())
    if m is None:
        return False
    if m["v6"] is not None:
        return _ip(m["v6"]) == 6
    name = m["name"]
    if _ip(name) == 4:
        return True
    if not _NAME.fullmatch(name):
        return False
    return "." not in name or name.endswith(LAN_SUFFIXES) or name in extra


def _ip(text: str) -> int | None:
    """4 oder 6, wenn text eine IP-Adresse ist, sonst None."""
    try:
        return ipaddress.ip_address(text).version
    except ValueError:
        return None


def lan_origin(origin: str, extra: tuple[str, ...] = ()) -> bool:
    """Kommt ein Schreibzugriff von einer Seite im LAN? Origin wie http://192.168.1.5:8080: http oder https, dahinter
    nur ein Name, den lan_host annimmt (kein Pfad, kein Benutzer). "null" (etwa aus einer Datei) ist fremd."""
    scheme, sep, host = origin.partition("://")
    return bool(sep) and scheme in ("http", "https") and lan_host(host, extra)


def same_origin(origin: str, req: web.Request) -> bool:
    """Kommt ein Schreibzugriff von einer Seite dieses Servers, so wie der Browser ihn anfragt? Origin http oder https,
    dahinter genau der Host der Anfrage (Name und Port, groß oder klein egal). Den Host setzt der Browser aus der
    Adresse, die er anfragt; eine Seite anderswo schickt ihren eigenen Origin, auch eine unter einer IP-Adresse im
    Internet (http://203.0.113.5) oder auf einem anderen Gerät im LAN. Am LAN-Port zusammen mit lan_origin; am Zugang
    von außen gilt PUBLIC_HOST (vor dem Port steht cloudflared)."""
    scheme, sep, host = origin.partition("://")
    return (bool(sep) and scheme in ("http", "https")
            and [h.lower() for h in req.headers.getall("Host", [])] == [host.lower()])


def lan_guard(app: "App"):
    """Der Schutz des LAN-Ports vor jedem Handler, auch vor 404 (siehe oben). Absagen stehen im Log, höchstens
    einmal pro WARN_EVERY_S: Ein ESP32 oder Uptime-Monitor unter einem fremden Namen fiele sonst still aus. Der Header
    selbst nie: Was eine fremde Seite schickt, gehört nicht ins Log. Eine 421 auf einen Bildabruf mit X-Panel (so fragt
    die Firmware; eine fremde Webseite kommt nur per DNS-Rebinding an Preflight vorbei) zählt in App.refused (Zahl
    und Zeit, kein Text) und macht /ok rot, solange das Panel nichts holt (App.problems). Mehr als ein Fehlalarm
    für 10 min ist damit nicht zu holen; /ok darum nie als Liveness-Probe nutzen, die den Container neu startet."""
    cfg = app.server_cfg
    last = {"warned": -WARN_EVERY_S}

    def warn(msg: str, *args) -> None:
        if monotonic() - last["warned"] >= WARN_EVERY_S:
            last["warned"] = monotonic()
            log.warning(msg, *args)

    @web.middleware
    async def guard(req: web.Request, handler: Handler) -> web.StreamResponse:
        if cfg.public and any(name in req.headers for name in CF_HEADERS):
            warn("Anfrage über Cloudflare am LAN-Port abgelehnt (403): Der Tunnel gehört an PUBLIC_PORT")
            raise web.HTTPForbidden(text=VIA_CLOUDFLARE)
        if not all(lan_host(host, cfg.lan_hosts) for host in req.headers.getall("Host", [])):
            if req.method == "GET" and req.path in ("/panel/frame", "/frame") and "X-Panel" in req.headers:
                app.refused += 1
                app.refused_at = time.time()
            warn("Anfrage unter einem Namen außerhalb des LAN abgelehnt (421). Erreicht ein eigenes Gerät den Server "
                 "unter einem Namen mit Punkt (etwa ein Panel mit nas.fritz.box als Server-Adresse im "
                 "Einrichtungs-WLAN, oder ein Uptime-Monitor), den Namen in .env unter LAN_HOSTS eintragen")
            raise web.HTTPMisdirectedRequest(text=NOT_LAN)
        return await handler(req)
    return guard


# ------------------------------------------------------------------------------------- Panel-Diagnose --
DIAG_MAX = 200          # Zeichen im Header X-Panel; mehr ist kein Panel
# Feld: (Muster, kleinster, größter Wert). Nur ASCII-Ziffern, ein Minus nur bei rssi.
_DIAG = {
    "fw": (re.compile(r"[0-9]{10}"), 0, 9_999_999_999),         # Firmware-Version JJMMTTHHMM
    "up": (re.compile(r"[0-9]{1,11}"), 0, 10 ** 10),            # Sekunden seit dem Start
    "rssi": (re.compile(r"-?[0-9]{1,3}"), -127, 0),             # WLAN-Empfang, dBm
    "heap": (re.compile(r"[0-9]{1,7}"), 0, 9_999_999),          # kleinster freier Speicher seit dem Start, Byte
}


def parse_diag(header: str | None) -> dict[str, int]:
    """Was ein Panel beim Abruf im Header X-Panel meldet: "fw=2610131200 up=3600 rssi=-61 heap=45000" -> höchstens
    vier Zahlen in Grenzen. Jedes Feld genau nach Muster und Grenzen; was nicht passt (Müll, Überlauf, Escape-Folgen,
    unbekannte Felder), fällt weg, Feld für Feld; ein zu langer Header ganz. Von einem doppelten Feld gilt das erste
    gültige. Nichts davon wird als Text gespeichert oder gezeigt."""
    if not header or len(header) > DIAG_MAX:
        return {}
    out: dict[str, int] = {}
    for item in header.split():
        name, _, value = item.partition("=")
        if name not in _DIAG or name in out:
            continue
        pattern, lo, hi = _DIAG[name]
        if pattern.fullmatch(value) and lo <= int(value) <= hi:
            out[name] = int(value)
    return out


def _etag_matches(etag: str, header: str | None) -> bool:
    """If-None-Match: ein oder mehrere ETags, auch schwach (W/)."""
    return bool(header) and etag in (t.strip().removeprefix("W/") for t in header.split(","))


# ------------------------------------------------------------------------------------------- Firmware --
FW_VERSION = re.compile(r"[0-9]{10}")


def _firmware_path(data_dir: str, channel: str, name: str) -> str:
    return os.path.join(data_dir, "firmware", channel, name)


def firmware_manifest(data_dir: str, channel: str) -> web.StreamResponse:
    """firmware.txt eines Kanals (tools/fw_release.sh legt es ab), nie gecacht: Ein Panel sieht jede neue Version
    sofort. Ohne Datei 404. channel: stable oder canary (panel.fw_channel); ohne bekanntes Panel stable."""
    path = _firmware_path(data_dir, channel, "firmware.txt")
    if not os.path.isfile(path):
        raise web.HTTPNotFound()
    return web.FileResponse(path, headers={"Cache-Control": "no-store"})


def firmware_image(data_dir: str, channel: str, version: str) -> web.StreamResponse:
    """firmware.bin eines Kanals, nur für die Version, die gerade in seinem firmware.txt steht (erste Zeile, genau
    zehn Ziffern): Unter dieser Adresse ist der Inhalt dann fest, und Cloudflare darf ihn einen Tag lang selbst
    ausliefern, statt dass jedes Panel die 1,8 MB hier holt. Sonst 404."""
    path = _firmware_path(data_dir, channel, "firmware.bin")
    if not FW_VERSION.fullmatch(version) or _manifest_version(data_dir, channel) != version.encode():
        raise web.HTTPNotFound()
    if not os.path.isfile(path):
        raise web.HTTPNotFound()
    return web.FileResponse(path, headers={"Cache-Control": "public, max-age=86400"})


def _manifest_version(data_dir: str, channel: str) -> bytes | None:
    """Die erste Zeile von firmware.txt ohne den Zeilenumbruch (LF); None ohne Datei."""
    try:
        with open(_firmware_path(data_dir, channel, "firmware.txt"), "rb") as f:
            return f.readline(16).removesuffix(b"\n")
    except OSError:
        return None


# ------------------------------------------------------------------------------------------- Handler --
def _pretty(obj, headers: dict[str, str] | None = None) -> web.Response:
    return web.json_response(obj, dumps=lambda o: json.dumps(o, ensure_ascii=False, indent=1), headers=headers)


class Handlers:
    """Die Handler, für jeden Port dieselben. resolve(req) gibt das Panel der Anfrage (None: keines bekannt, nur am
    Zugang von außen bei den Firmware-Dateien), origin_ok(origin, req) sagt, ob eine Seite mit diesem Origin mit
    dieser Anfrage schreiben darf, links, was die Vorschau verlinkt, max_scale, wie groß /frame.png höchstens wird."""

    def __init__(self, app: "App", resolve: Resolver, origin_ok: Callable[[str, web.Request], bool],
                 links: tuple[str, ...] = (), max_scale: int = LAN_MAX_SCALE):
        self.app = app
        self.resolve = resolve
        self.origin_ok = origin_ok
        self.links = links
        self.max_scale = max_scale

    def panel(self, req: web.Request) -> "Panel":
        p = self.resolve(req)
        if p is None:
            raise web.HTTPForbidden()
        return p

    def channel(self, req: web.Request) -> str:
        """Der Firmware-Kanal der Anfrage: der ihres Panels, ohne bekanntes Panel stable (Rettungskanal)."""
        p = self.resolve(req)
        return p.channel if p is not None else "stable"

    def foreign(self, req: web.Request) -> bool:
        """Ein Origin, der nicht schreiben darf? Ohne Origin nicht: Ein Browser schickt bei POST immer einen."""
        origin = req.headers.get("Origin")
        return origin is not None and not self.origin_ok(origin, req)

    def _render(self, p: "Panel", layout: str | None = None, card: str | None = None) -> Image.Image:
        r = (Renderer(replace(p.cfg, layout=layout), self.app.logos, idle=p.idle, rotation=p.renderer.rotation)
             if layout else p.renderer)
        return r.render(p.model, time.time(), card=card or None)

    def _sample(self, p: "Panel", layout: str) -> Image.Image:
        """Zwei Beispielflüge in der Aufteilung layout, für die Wahl auf der Einstellungsseite: ein eigener Renderer,
        die Rotation des Panels bleibt, wie sie ist."""
        r = Renderer(replace(p.cfg, layout=layout, blink=False), self.app.logos, idle=p.idle)
        return r.render(DisplayModel(flights=list(SAMPLE_FLIGHTS)), time.time())

    @staticmethod
    def _layout(req: web.Request) -> str | None:
        """?layout=, wie LAYOUT in den Panel-Einstellungen; Unbekanntes zählt wie nicht angegeben."""
        try:
            return parse_layout(req.query["layout"])
        except (KeyError, ValueError):
            return None

    async def frame(self, req: web.Request) -> web.Response:
        p = self.panel(req)
        now = time.time()
        diag = req.headers.getall("X-Panel", [])
        p.fetched(now, parse_diag(diag[0]) if len(diag) == 1 else {})
        body = p.renderer.frame_bytes(self._render(p), p.renderer.brightness(now))
        etag = f'"{hashlib.sha256(body).hexdigest()[:16]}"'
        headers = {"ETag": etag, "Cache-Control": "no-store, no-transform"}
        if _etag_matches(etag, req.headers.get("If-None-Match")):
            return web.Response(status=304, headers=headers)
        return web.Response(body=body, content_type="application/octet-stream", headers=headers)

    async def firmware_txt(self, req: web.Request) -> web.StreamResponse:
        return firmware_manifest(self.app.server_cfg.data_dir, self.channel(req))

    async def firmware_bin(self, req: web.Request) -> web.StreamResponse:
        return firmware_image(self.app.server_cfg.data_dir, self.channel(req), req.query.get("v", ""))

    async def frame_png(self, req: web.Request) -> web.Response:
        p = self.panel(req)
        sample = req.query.get("sample")
        if sample == "1" or (sample == "auto" and not p.model.flights):    # auto: nur, wenn gerade nichts fliegt
            img = self._sample(p, self._layout(req) or p.cfg.layout)
        else:
            img = self._render(p, self._layout(req), req.query.get("card"))
        try:
            scale = max(1, min(self.max_scale, int(req.query.get("scale", "6"))))
        except ValueError:
            scale = 6
        gap = req.query.get("gap", "1") != "0"
        return web.Response(body=p.renderer.png(img, scale, gap), content_type="image/png",
                            headers={"Cache-Control": "no-store"})

    async def index(self, req: web.Request) -> web.Response:
        p = self.panel(req)
        tried = self._layout(req)                   # ?layout=: eine Probeansicht, auch die eigene Aufteilung
        layout = tried or p.cfg.layout
        card = req.query.get("card", "")
        if card not in p.idle.cards:
            card = ""
        esc = html.escape
        settings = "/settings" in self.links
        if card:
            mode, state = "demo", f"Beispiel: Karte „{ABOUT.get(card, (card,))[0]}“"
        elif tried:
            mode, state = "demo", f"Probeansicht: {LAYOUT_NAMES[layout]}"
        else:
            mode, state = "live", "Live"

        def current(on: bool) -> str:
            return ' aria-current="true"' if on else ""

        page = page_top("flightpanel", "/", settings, '<script src="/static/preview.js" defer></script>\n')
        page += PREVIEW_HTML.format(
            # Probeansicht einer anderen Aufteilung: fliegt gerade nichts, mit den Beispielflügen statt der Uhr
            frame=esc(f"/frame.png?layout={layout}&card={card}" + ("&sample=auto" if mode == "demo" and not card
                                                                     else "")),
            mode=mode, state=esc(state), back='<a class="back" href="/">zurück zu Live</a>' if mode == "demo" else "",
            health=' data-health="/health"' if "/health" in self.links else "",
            keep=" Dauerhaft unter Einstellungen, Anzeige." if settings else "",
            layouts="".join(f'<a class="seg" href="{esc(f"/?layout={x}")}"{current(x == layout)}>'
                            f'{esc(LAYOUT_NAMES[x])}</a>' for x in LAYOUTS),
            live=f'<a class="chip" href="{esc(f"/?layout={layout}")}"{current(not card)}>Live</a>',
            cards="".join(f'<p class="chip-group" id="cg-{i}">{esc(GROUPS.get(g, g))}</p>'
                          f'<div class="chips" role="group" aria-labelledby="cg-{i}">' + "".join(
                              f'<a class="chip" href="{esc(f"/?layout={layout}&card={c.id}")}"'
                              f'{current(c.id == card)}>{esc(ABOUT.get(c.id, (c.id,))[0])}</a>' for c in cards)
                          + "</div>" for i, (g, cards) in enumerate(_by_group(p.idle.cards.values()))),
            links="".join(f'<li><a href="{esc(path)}">{esc(LINK_NAMES.get(path, path))}</a></li>'
                          for path in self.links if path != "/settings"))
        return web.Response(text=page, content_type="text/html", headers=NO_STORE)

    async def display(self, req: web.Request) -> web.Response:
        p = self.panel(req)
        return web.Response(text=p.model.to_json(quiet=p.renderer.brightness(time.time()) == 0),
                            content_type="application/json", headers=NO_STORE)

    async def ok(self, req: web.Request) -> web.Response:
        problems = self.app.problems(time.time())
        return web.Response(text="\n".join(problems or ["ok"]) + "\n", status=503 if problems else 200,
                            headers={"Cache-Control": "no-store"})

    async def health(self, req: web.Request) -> web.Response:
        return web.json_response(self.app.health())

    async def issues(self, req: web.Request) -> web.Response:
        return _pretty(self.app.issues_report())

    async def issues_clear(self, req: web.Request) -> web.Response:
        if self.foreign(req):
            raise web.HTTPForbidden(text=FOREIGN_ORIGIN)
        self.app.issues.clear()
        self.app.issues.save()
        return web.json_response(self.app.issues_report())

    async def stats(self, req: web.Request) -> web.Response:
        # Landerichtung: für alle Panels gleich
        p = self.panel(req)
        return _pretty({**p.stats.report(), "course": p.airfield.report() if p.airfield else None}, NO_STORE)

    async def idle(self, req: web.Request) -> web.Response:
        p = self.panel(req)
        return _pretty(p.idle.status(p.model, time.time()), NO_STORE)
