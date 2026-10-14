"""Einstellungsseite eines Panels (SETTINGS_PAGE=1): Standort, Sichtfeld, Anzeige, Helligkeit und Ruhezeit und die
Leerlauf-Karten im Browser setzen, fürs Telefon gemacht, und das Sichtfeld am Fenster eichen. Geladen wird das Modul
nur mit dem Schalter (web.make_web_app, web.make_public_app).

  GET  /settings   das Formular, gebaut aus den Panel-Feldern in config.py (Gruppe, Erklärung, Default): Es gibt keine
                   zweite Liste der Einstellungen. Was gespeichert ist, steht im Feld, der Default als Platzhalter;
                   jeder Wert geht durch html.escape.
  POST /settings   {"settings": {NAME: "Wert" | null}} als JSON; null oder leer setzt zurück. Geprüft genau wie
                   panels set (panels.check, panels.check_card), dann in die Datenbank, und main.App baut das Panel
                   sofort neu (_sync_panels). Ein Fehler gibt 400 mit {"error": Satz, "key": NAME oder null}; die
                   Datenbank bleibt, wie sie war.

Schreiben nur als application/json (sonst 415), mit höchstens MAX_BODY Byte (sonst 413) und einem Origin, den die
Handler zulassen (sonst 403): am LAN-Port eine Seite aus dem LAN (web.lan_origin), am Zugang von außen genau
https://<PUBLIC_HOST> (web.make_public_app). Welches Panel gemeint ist, sagt der Resolver der Handler: am LAN-Port
das Panel am LAN-Port, von außen das der Anmeldung.

Skript und Stil liegen in static/ (settings.js, settings.css), nicht in der Seite: Die Content-Security-Policy
(web.SECURITY_HEADERS) lässt nur Dateien vom Server selbst laufen. Zum Eichen holt die Seite alle 5 s display.json
und baut die Liste der Flugzeuge nur mit textContent: Callsigns und Typen kommen aus ADS-B, also von Fremden."""

import html
import json
import logging
import re
from collections.abc import Mapping
from dataclasses import Field

from aiohttp import web

from .cards import CARDS
from .config import CARD_PREFIX, LAYOUTS, card_off, default_text, is_panel_key, panel_fields, parse_layout
from .panels import check, check_card
from .web import Handlers, add, add_static, read_static

log = logging.getLogger("settings")

MAX_BODY = 16 * 1024
STATIC = read_static("settings.css", "settings.js")
# Was die Seite als Feld zeigt: daneben kann ein Fehler stehen
FIELDS = (frozenset(f.metadata["env"] for f in panel_fields())
          | frozenset(f"{CARD_PREFIX}{c.id.upper()}" for c in CARDS))
OPEN = ("Standort", "Sichtfeld")        # aufgeklappt: darum geht es am Fenster
INTRO = {
    "Standort": "Wo das Fenster ist.",
    "Sichtfeld": ("Was man vom Fenster aus sieht. Richtungen im Uhrzeigersinn, 0 = Nord, 90 = Ost: Der Anfang ist der "
                  "linke Rand, wenn man hinausschaut, das Ende der rechte. Am einfachsten unten am Fenster eichen."),
}
CARDS_TITLE = "Leerlauf-Karten"
CARDS_INTRO = "Was das Panel zeigt, wenn kein Flug im Sichtfeld ist. Eine ausgeschaltete Karte kommt nie."

PAGE = """<!doctype html>
<html lang="de">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="color-scheme" content="dark">
<title>flightpanel · Einstellungen</title>
<link rel="stylesheet" href="/static/settings.css">
<script src="/static/settings.js" defer></script>
</head>
<body>
<main class="page">
<header class="top">
<a class="back" href="/">← Vorschau</a>
<h1>Einstellungen</h1>
<p class="lead">Ein leeres Feld hat den Standardwert, der grau darin steht. Gespeichert wird alles zusammen; das Panel
zeigt es ab dem nächsten Bild.</p>
<noscript><p class="error">Speichern und Eichen brauchen JavaScript.</p></noscript>
</header>
<form id="settings" class="settings" action="/settings" method="post" novalidate>
{groups}<div class="bar">
<p id="status" class="status" role="status"></p>
<button type="submit" class="save">Speichern</button>
</div>
</form>
</main>
</body>
</html>
"""

PASTE = """<div class="field paste">
<label for="paste">Koordinaten einfügen</label>
<input type="text" id="paste" autocomplete="off" autocapitalize="off" spellcheck="false" placeholder="52.52, 13.40"
 aria-describedby="paste-note">
<p class="hint" id="paste-note">Aus einer Karten-App kopiert, Breite und Länge: füllt die beiden Felder darunter.</p>
</div>
"""

CALIBRATION = """<section class="calib" id="calib" aria-labelledby="calib-title">
<h3 id="calib-title">Am Fenster eichen</h3>
<p class="hint">Die Flugzeuge in der Nähe, die nächsten zuerst. Ist eines gerade am linken Rand des Fensters zu sehen,
seine Richtung als Anfang übernehmen, am rechten Rand als Ende; eines knapp über Dach oder Baum als Mindesthöhe.
Danach speichern.</p>
<ol class="planes" id="planes"></ol>
<p class="hint" id="planes-note">Lade Flugzeuge …</p>
<p class="calib-note" id="calib-note" role="status"></p>
</section>
"""


class Refused(ValueError):
    """Was nicht gespeichert wird: der Satz für die Seite und, wenn sie es als Feld zeigt, der Name dazu."""

    def __init__(self, message: str, key: str | None = None):
        super().__init__(message)
        self.key = key


class SettingsPage:
    """Die Seite für das Panel, das die Handler h auflösen, mit ihrer Origin-Regel."""

    def __init__(self, h: Handlers):
        self.h = h

    def add_routes(self, web_app: web.Application, kind: str | None = None) -> None:
        """Seite, Speichern, Skript und Stil; am Zugang von außen mit der Identitätsklasse kind (email)."""
        add(web_app, "GET", "/settings", self.get, kind)
        add(web_app, "POST", "/settings", self.post, kind)
        add_static(web_app, STATIC, kind)

    async def get(self, req: web.Request) -> web.Response:
        p = self.h.panel(req)
        row = self.h.app.db.panel_get(p.id)
        return web.Response(text=render(row["settings"] if row else {}), content_type="text/html",
                            headers={"Cache-Control": "no-store"})

    async def post(self, req: web.Request) -> web.Response:
        origin = req.headers.get("Origin")
        if not origin or not self.h.origin_ok(origin):
            return _refuse(403, "Gespeichert wird nur von dieser Seite aus: Der Browser schickte einen fremden oder "
                                "keinen Origin.")
        if req.content_type != "application/json":
            return _refuse(415, "Erwartet JSON (Content-Type: application/json).")
        body = await _read(req)
        if body is None:
            return _refuse(413, f"Zu viele Daten, höchstens {MAX_BODY // 1024} KB.")
        app, p = self.h.app, self.h.panel(req)
        try:
            changes = _changes(body)
            row = app.db.panel_get(p.id)
            if row is None:
                raise Refused("Dieses Panel steht nicht mehr in der Datenbank.")
            new = merge(row["settings"], changes)
            try:
                check(app.server_cfg, p.id, new)
            except ValueError as e:
                raise Refused(str(e), _named(str(e))) from None
        except Refused as e:
            return _refuse(400, str(e), e.key)
        app.db.panel_set_settings(p.id, new)
        changed = sorted(k for k in new.keys() | row["settings"].keys() if new.get(k) != row["settings"].get(k))
        log.info("Panel %s: auf der Einstellungsseite gespeichert: %s", p.id, ", ".join(changed) or "nichts geändert")
        try:
            app._sync_panels()              # sofort übernehmen, nicht erst beim nächsten Abruf
        except Exception as e:              # etwa Datenbank gesperrt: gespeichert ist es, die Abrufschleife zieht nach
            log.warning("Panel %s: gespeichert, aber noch nicht übernommen: %s", p.id, e)
        return web.json_response({"ok": True})


def merge(stored: Mapping[str, str], changes: Mapping[str, str | None]) -> dict[str, str]:
    """Wie panels set: Ein Name ersetzt, was unter ihm gespeichert ist, in jeder Schreibweise (von Hand klein
    geschrieben); null oder leer setzt ihn zurück. Was nicht in changes steht, bleibt, wie es ist."""
    new = {k: v for k, v in stored.items() if k.strip().upper() not in changes}
    new.update({k: v.strip() for k, v in changes.items() if v is not None and v.strip()})
    return new


def _changes(body: bytes) -> dict[str, str | None]:
    """{"settings": {NAME: "Wert" | null}} -> {NAME: Wert oder None}. Nur Panel-Einstellungen, groß geschrieben wie
    in panels keys, und nur Karten, die es gibt; sonst Refused, und nichts wird gespeichert."""
    try:
        doc = json.loads(body)
    except (ValueError, RecursionError):        # kein JSON, kein UTF-8, oder tausendfach verschachtelt
        raise Refused("Kein lesbares JSON.") from None
    if not isinstance(doc, dict) or doc.keys() != {"settings"} or not isinstance(doc["settings"], dict):
        raise Refused('Erwartet {"settings": {NAME: "Wert" oder null}}.')
    out: dict[str, str | None] = {}
    for key, value in doc["settings"].items():
        if key != key.strip().upper() or not is_panel_key(key):
            raise Refused(f"{key}: keine Panel-Einstellung")
        try:
            check_card(key)
        except ValueError as e:
            raise Refused(str(e)) from None
        if value is not None and not isinstance(value, str):
            raise Refused(f"{key}: Der Wert gehört als Text in Anführungszeichen, oder null", _shown(key))
        out[key] = value
    return out


_NAMED = re.compile(r"([A-Z][A-Z0-9_]*)[=/]")     # "HOME_LAT='95': …", "HOME_LAT/HOME_LON liegen …"


def _named(message: str) -> str | None:
    """Das Feld, das ein Fehler der Parser oder von panels.check nennt (er beginnt mit dem Namen), wenn die Seite es
    zeigt."""
    m = _NAMED.match(message)
    return _shown(m[1]) if m else None


def _shown(key: str) -> str | None:
    return key if key in FIELDS else None


async def _read(req: web.Request) -> bytes | None:
    """Der Inhalt der Anfrage, höchstens MAX_BODY Byte; None, wenn es mehr sind, auch ohne Content-Length."""
    if (req.content_length or 0) > MAX_BODY:
        return None
    body = bytearray()
    async for chunk in req.content.iter_chunked(4096):
        body += chunk
        if len(body) > MAX_BODY:
            return None
    return bytes(body)


def _refuse(status: int, message: str, key: str | None = None) -> web.Response:
    return web.json_response({"error": message, "key": key}, status=status)


# ------------------------------------------------------------------------------------------------- Seite --
def render(settings: Mapping[str, object]) -> str:
    """Die Seite zu den gespeicherten Einstellungen eines Panels (rohe Werte, wie sie in der Datenbank stehen)."""
    groups: dict[str, list[str]] = {}
    for f in panel_fields():
        groups.setdefault(f.metadata["panel"][0], []).append(_field(f, settings))
    out = [_group(name, (PASTE if name == "Standort" else "") + "".join(rows)
                  + (CALIBRATION if name == "Sichtfeld" else ""), name in OPEN, INTRO.get(name, ""))
           for name, rows in groups.items()]
    out.append(_group(CARDS_TITLE, _cards(settings), False, CARDS_INTRO))
    return PAGE.format(groups="".join(out))


def _group(name: str, body: str, is_open: bool, intro: str = "") -> str:
    """Eine Gruppe zum Auf- und Zuklappen; auf dem Telefon ist die Seite sonst sehr lang."""
    intro = f'<p class="intro">{html.escape(intro)}</p>\n' if intro else ""
    return (f'<details class="group"{" open" if is_open else ""}>\n<summary><h2>{html.escape(name)}</h2></summary>\n'
            f'{intro}{body}</details>\n')


def _field(f: Field, settings: Mapping[str, object]) -> str:
    """Ein Feld mit Erklärung und Name; Schalter und LAYOUT als Auswahl, deren erster Eintrag (leer) der Standard
    ist, alles andere als Text: Kommazahlen mit Punkt, darum die normale Tastatur und nicht die für Zahlen (auf einem
    deutschen iPhone hat sie nur ein Komma)."""
    key = f.metadata["env"]
    raw = _raw(settings.get(key))
    if isinstance(f.default, bool):
        current = ("1" if f.metadata["parse"](raw) else "0") if raw else ""
        control = _select(key, (("", f"Standard ({'an' if f.default else 'aus'})"), ("1", "an (1)"), ("0", "aus (0)")),
                          current)
    elif f.metadata["parse"] is parse_layout:
        control = _select(key, (("", f"Standard ({default_text(f)})"), *((x, x) for x in LAYOUTS)), _layout(raw))
    else:
        digits = ' inputmode="numeric"' if type(f.default) is int else ""      # nur ganze Zahlen ab 0
        control = (f'<input type="text" id="f-{key}" name="{key}" data-key="{key}" value="{html.escape(raw)}" '
                   f'placeholder="{html.escape(default_text(f))}" autocomplete="off" autocapitalize="off" '
                   f'spellcheck="false"{digits} aria-describedby="err-{key}">')
    return (f'<div class="field" id="row-{key}">\n<label for="f-{key}">{html.escape(f.metadata["panel"][1])} '
            f'<span class="key">{key}</span></label>\n{control}\n<p class="error" id="err-{key}" hidden></p>\n</div>\n')


def _select(key: str, options, current: str) -> str:
    opts = "".join(f'<option value="{html.escape(v)}"{" selected" if v == current else ""}>{html.escape(t)}</option>'
                   for v, t in options)
    return (f'<span class="select"><select id="f-{key}" name="{key}" data-key="{key}" '
            f'aria-describedby="err-{key}">{opts}</select></span>')


def _layout(raw: str) -> str:
    """Das gespeicherte LAYOUT als Eintrag der Auswahl; Unsinn von früher zeigt den Standard (Speichern räumt ihn
    weg)."""
    try:
        return parse_layout(raw) if raw else ""
    except ValueError:
        return ""


def _cards(settings: Mapping[str, object]) -> str:
    """Ein Schalter je Karte: an ist der Standard (gespeichert wird dann nichts), aus speichert CARD_<ID>=0."""
    rows = []
    for c in CARDS:
        key = f"{CARD_PREFIX}{c.id.upper()}"
        checked = "" if card_off(settings.get(key)) else " checked"
        rows.append(f'<div class="switch-row" id="row-{key}"><label class="switch">'
                    f'<input type="checkbox" id="f-{key}" data-key="{key}"{checked}>'
                    f'<span class="card-id">{html.escape(c.id)}</span>'
                    f'<span class="card-group">{html.escape(c.group)}</span></label>'
                    f'<p class="error" hidden></p></div>\n')
    return f'<div class="cards">\n{"".join(rows)}</div>\n'


def _raw(v) -> str:
    """Ein gespeicherter Wert als Text (eine Zahl aus einer Handänderung wie ihr Text)."""
    return "" if v is None else str(v).strip()
