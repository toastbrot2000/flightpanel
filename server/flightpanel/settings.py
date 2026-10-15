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
Handler zulassen (sonst 403): am LAN-Port eine Seite dieses Servers (web.same_origin, web.lan_origin), am Zugang von
außen genau https://<PUBLIC_HOST> (web.make_public_app). Welches Panel gemeint ist, sagt der Resolver der Handler:
am LAN-Port das Panel am LAN-Port, von außen das der Anmeldung.

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

from .cards import ABOUT, CARDS, GROUPS
from .config import (
    CARD_PREFIX,
    INFO_PAGES,
    LAYOUT_NAMES,
    LAYOUTS,
    _hours,
    _pages,
    card_off,
    default_text,
    is_owner_key,
    is_panel_key,
    panel_fields,
    parse_layout,
)
from .panels import check, check_card
from .web import Handlers, add, add_static, page_top, read_static

log = logging.getLogger("settings")

MAX_BODY = 16 * 1024
STATIC = read_static("settings.css", "settings.js")
# Was die Seite als Feld zeigt: daneben kann ein Fehler stehen
FIELDS = (frozenset(f.metadata["env"] for f in panel_fields())
          | frozenset(f"{CARD_PREFIX}{c.id.upper()}" for c in CARDS))
OPEN = ("Standort", "Sichtfeld")        # aufgeklappt: darum geht es am Fenster
INTRO = {
    "Standort": "Wo das Fenster ist und welche Flugzeuge in Frage kommen.",
    "Sichtfeld": ("Was man vom Fenster aus sieht. Am einfachsten unten eichen, mit einem Flugzeug, das gerade am "
                  "Rand des Fensters vorbeifliegt."),
    "Flughafen": "Welcher Flughafen zu diesem Panel gehört. Das stellt der Betreiber des Servers ein.",
    "Anzeige": "Wie das Panel Flüge zeigt und wie lange.",
    "Helligkeit": "Wie hell das Panel leuchtet, und wann es dunkel bleibt.",
}
CARDS_TITLE = "Karten"
CARDS_INTRO = ("Was das Panel zeigt, wenn kein Flug zu sehen ist. Eine ausgeschaltete Karte kommt nie. Wie sie "
               "aussehen, zeigt die Vorschau unter Live.")
INFO_NAMES = {"eta": "Landezeit", "alt": "Höhe", "dist": "Entfernung", "callsign": "Flugnummer"}

PAGE = """<main class="page form-page">
<header class="head">
<h1>Einstellungen</h1>
<p class="lead">Für dein Panel. Ein leeres Feld hat den Standardwert, der grau darin steht. Gespeichert wird alles
zusammen, das Panel zeigt es nach wenigen Sekunden.</p>
<noscript><p class="error">Speichern und Eichen brauchen JavaScript.</p></noscript>
</header>
<nav class="jump" aria-label="Abschnitte">{jump}</nav>
<form id="settings" class="settings" action="/settings" method="post" novalidate>
{groups}<div class="bar">
<p id="status" class="status" role="status">Alles gespeichert.</p>
<button type="submit" class="save">Speichern</button>
</div>
</form>
</main>
</body>
</html>
"""

PASTE = """<div class="field paste">
<label for="paste">Koordinaten einfügen</label>
<div class="paste-row">
<input type="text" id="paste" autocomplete="off" autocapitalize="off" spellcheck="false" placeholder="52.52, 13.40"
 aria-describedby="paste-note">
<button type="button" class="ghost" id="locate" hidden>Hier bin ich</button>
</div>
<p class="hint" id="paste-note">Aus einer Karten-App kopiert, Breite und Länge: füllt die beiden Felder darunter.</p>
</div>
<div class="pair">
"""

CALIBRATION = """<section class="calib" id="calib" aria-labelledby="calib-title">
<h3 id="calib-title">Am Fenster eichen</h3>
<p class="hint">Die Flugzeuge in der Nähe, die nächsten zuerst. Ist eines gerade am linken Rand des Fensters zu sehen,
seine Richtung als linken Rand übernehmen, am rechten Rand als rechten; eines knapp über Dach oder Baum als
Mindesthöhe. Danach speichern.</p>
<div class="compass-wrap"><canvas id="compass" class="compass" width="320" height="320" role="img"
 aria-label="Sichtfeld und Flugzeuge von oben, Norden oben"></canvas></div>
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
        if not origin or not self.h.origin_ok(origin, req):
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
        if is_owner_key(key):
            raise Refused(f"{key}: stellt nur der Betreiber ein, mit panels set", _shown(key))
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
SCRIPTS = '<link rel="stylesheet" href="/static/settings.css">\n<script src="/static/settings.js" defer></script>\n'


def render(settings: Mapping[str, object]) -> str:
    """Die Seite zu den gespeicherten Einstellungen eines Panels (rohe Werte, wie sie in der Datenbank stehen)."""
    groups: dict[str, list[str]] = {}
    for f in panel_fields():
        rows = groups.setdefault(f.metadata["panel"][0], [])
        key = f.metadata["env"]
        if key == "HOME_LAT":
            rows.append(PASTE)                          # öffnet die Zeile für Breite und Länge
        rows.append(_field(f, settings))
        if key == "HOME_LON":
            rows.append("</div>\n")
    out = [_group(name, "".join(rows) + (CALIBRATION if name == "Sichtfeld" else ""), name in OPEN,
                  INTRO.get(name, "")) for name, rows in groups.items()]
    out.append(_group(CARDS_TITLE, _cards(settings), False, CARDS_INTRO))
    jump = "".join(f'<a class="chip" href="#{_anchor(name)}">{html.escape(name)}</a>'
                   for name in [*groups, CARDS_TITLE])
    return (page_top("flightpanel · Einstellungen", "/settings", True, SCRIPTS)
            + PAGE.format(groups="".join(out), jump=jump))


def _anchor(name: str) -> str:
    return "g-" + re.sub(r"[^a-z]+", "-", name.lower().replace("ü", "ue").replace("ä", "ae").replace("ö", "oe"))


def _group(name: str, body: str, is_open: bool, intro: str = "") -> str:
    """Eine Gruppe zum Auf- und Zuklappen; auf dem Telefon ist die Seite sonst sehr lang."""
    intro = f'<p class="intro">{html.escape(intro)}</p>\n' if intro else ""
    return (f'<details class="group" id="{_anchor(name)}"{" open" if is_open else ""}>\n'
            f'<summary><h2>{html.escape(name)}</h2></summary>\n<div class="group-body">\n{intro}{body}</div>\n'
            f'</details>\n')


def _field(f: Field, settings: Mapping[str, object]) -> str:
    """Ein Feld mit Name, Satz dazu und dem passenden Bedienelement. Leer heißt Standard: Textfelder zeigen ihn als
    Platzhalter; Schalter, Regler, Uhrzeiten, Infoseiten und Aufteilung zeigen ihn als Wert und tragen ihn in
    data-default, und settings.js schickt null, solange er gilt. Kommazahlen mit Punkt, darum die normale Tastatur
    und nicht die für Zahlen (auf einem deutschen iPhone hat sie nur ein Komma)."""
    key = f.metadata["env"]
    ui = f.metadata["ui"]
    raw = _raw(settings.get(key))
    parse = f.metadata["parse"]
    esc = html.escape
    default = esc(default_text(f))
    hint = f'<p class="hint" id="hint-{key}">{esc(ui.hint)}</p>\n' if ui.hint else ""
    described = f"err-{key}" + (f" hint-{key}" if ui.hint else "")
    error = f'<p class="error" id="err-{key}" hidden></p>\n'
    hidden = f'<input type="hidden" id="f-{key}" data-key="{key}" data-default="{default}" value="{esc(raw)}">\n'
    head = f'<div class="field" id="row-{key}">\n'
    group_label = f'<p class="label" id="label-{key}">{esc(ui.label)}</p>\n{hint}'
    if f.metadata.get("owner"):                     # nur zu sehen: settings.js lässt gesperrte Felder weg
        return (f'{head}<label class="label" for="f-{key}">{esc(ui.label)}</label>\n{hint}'
                f'<div class="input"><input type="text" id="f-{key}" data-key="{key}" value="{esc(raw)}" '
                f'placeholder="{default or "wie der Server"}" disabled aria-describedby="{described} owner-{key}">'
                f'</div>\n'
                f'<p class="hint" id="owner-{key}">Stellt der Betreiber ein (panels set).</p>\n{error}</div>\n')
    if isinstance(f.default, bool):
        on = parse(raw) if raw else f.default
        return (f'<div class="field switch-field" id="row-{key}">\n<label class="switch">'
                f'<span class="switch-text"><span class="label" id="label-{key}">{esc(ui.label)}</span>'
                f'{_span_hint(ui.hint, key)}</span><input type="checkbox" role="switch" '
                f'data-default="{"1" if f.default else "0"}" id="f-{key}" data-key="{key}" '
                f'aria-labelledby="label-{key}" aria-describedby="{described}"{" checked" if on else ""}>'
                f'</label>\n{error}</div>\n')
    if parse is parse_layout:
        current = _layout(raw) or f.default
        options = "".join(
            f'<label class="layout-option"><input type="radio" name="layout-pick" value="{x}"'
            f'{" checked" if x == current else ""}><img src="/frame.png?layout={x}&amp;sample=1&amp;scale=3&amp;gap=0" '
            f'alt="" width="384" height="96" loading="lazy"><span>{esc(LAYOUT_NAMES[x])}</span></label>'
            for x in LAYOUTS)
        return (f'{head}{group_label}<div class="layouts" role="radiogroup" aria-labelledby="label-{key}" '
                f'data-for="{key}">{options}</div>\n{hidden}{error}</div>\n')
    if parse is _pages:
        chosen = [p for p in _parts(raw or default_text(f)) if p in INFO_PAGES]
        order = chosen + [p for p in INFO_PAGES if p not in chosen]
        boxes = "".join(f'<label class="check-chip"><input type="checkbox" value="{p}"'
                        f'{" checked" if p in chosen else ""}><span>{esc(INFO_NAMES.get(p, p))}</span></label>'
                        for p in order)
        return (f'{head}{group_label}<div class="check-chips" role="group" aria-labelledby="label-{key}" '
                f'data-for="{key}">{boxes}</div>\n{hidden}{error}</div>\n')
    if parse is _hours:
        start, end = [*_parts(raw or default_text(f), "-"), "", ""][:2]
        off = ui.can_off and bool(start) and _hhmm(start) == _hhmm(end)
        if off:                                         # zum Wiedereinschalten stehen die Standardzeiten da
            start, end = [*_parts(default_text(f), "-"), "", ""][:2]
        times = "".join(
            f'<label class="time"><span>{word}</span><input type="time" value="{esc(_hhmm(t))}" '
            f'aria-label="{esc(ui.label)} {word}"></label>' for word, t in (("von", start), ("bis", end)))
        if ui.can_off:                                  # Schalter statt zweimal derselben Uhrzeit
            group_label = (f'<label class="switch"><span class="switch-text"><span class="label" id="label-{key}">'
                           f'{esc(ui.label)}</span>{_span_hint(ui.hint, key)}</span><input type="checkbox" '
                           f'role="switch" class="times-on" data-for="{key}" aria-labelledby="label-{key}" '
                     f'aria-describedby="err-{key}{f" hint-{key}" if ui.hint else ""}"'
                           f'{"" if off else " checked"}></label>\n')
        return (f'{head}{group_label}<div class="times" role="group" aria-labelledby="label-{key}" '
                f'data-for="{key}"{" hidden" if off else ""}>{times}</div>\n{hidden}{error}</div>\n')
    if ui.slider:
        lo, hi, step = ui.slider
        return (f'{head}<div class="label-row"><label class="label" for="f-{key}">{esc(ui.label)}</label>'
                f'<output class="level" for="f-{key}" id="out-{key}"></output></div>\n{hint}'
                f'<input type="range" id="f-{key}" data-key="{key}" data-default="{default}" min="{lo:g}" '
                f'max="{hi:g}" step="{step:g}" value="{esc(raw or default_text(f))}" aria-describedby="{described}">\n'
                f'{error}</div>\n')
    digits = ' inputmode="numeric"' if type(f.default) is int else ""      # nur ganze Zahlen ab 0
    unit = f'<span class="unit" aria-hidden="true">{esc(ui.unit)}</span>' if ui.unit else ""
    return (f'{head}<label class="label" for="f-{key}">{esc(ui.label)}</label>\n{hint}'
            f'<div class="input{" has-unit" if ui.unit else ""}"><input type="text" id="f-{key}" name="{key}" '
            f'data-key="{key}" value="{esc(raw)}" placeholder="{esc(_placeholder(f))}" autocomplete="off" '
            f'autocapitalize="off" spellcheck="false"{digits} aria-describedby="{described}">{unit}</div>\n'
            f'{error}</div>\n')


def _placeholder(f: Field) -> str:
    """Der Standard im leeren Feld, Kommazahlen ohne ".0" (6000 statt 6000.0)."""
    return f"{f.default:g}" if type(f.default) is float else default_text(f)


def _span_hint(hint: str, key: str) -> str:
    return f'<span class="hint" id="hint-{key}">{html.escape(hint)}</span>' if hint else ""


def _parts(raw: str, sep: str = ",") -> list[str]:
    return [p.strip().lower() for p in raw.split(sep) if p.strip()]


def _hhmm(text: str) -> str:
    """"6:30" -> "06:30", wie ein Zeitfeld es braucht; Unlesbares bleibt leer."""
    m = re.fullmatch(r"(\d{1,2}):(\d{2})", text)
    return f"{int(m[1]):02d}:{m[2]}" if m and int(m[1]) < 24 and int(m[2]) < 60 else ""


def _layout(raw: str) -> str:
    """Das gespeicherte LAYOUT; Unsinn von früher zeigt den Standard (Speichern räumt ihn weg)."""
    try:
        return parse_layout(raw) if raw else ""
    except ValueError:
        return ""


def _cards(settings: Mapping[str, object]) -> str:
    """Ein Schalter je Karte, nach Gruppen: an ist der Standard (gespeichert wird dann nichts), aus speichert
    CARD_<ID>=0."""
    by_group: dict[str, list[str]] = {}
    for c in CARDS:
        key = f"{CARD_PREFIX}{c.id.upper()}"
        title, about = ABOUT.get(c.id, (c.id, ""))
        checked = "" if card_off(settings.get(key)) else " checked"
        by_group.setdefault(c.group, []).append(
            f'<div class="field switch-field" id="row-{key}"><label class="switch">'
            f'<span class="switch-text"><span class="label" id="label-{key}">{html.escape(title)}</span>'
            f'<span class="hint" id="hint-{key}">{html.escape(about)}</span></span>'
            f'<input type="checkbox" role="switch" aria-labelledby="label-{key}" aria-describedby="hint-{key}" '
            f'data-default="1" id="f-{key}" data-key="{key}"{checked}>'
            f'</label><p class="error" hidden></p></div>\n')
    return "".join(f'<h3 class="card-group">{html.escape(GROUPS.get(g, g))}</h3>\n<div class="cards">\n'
                   f'{"".join(rows)}</div>\n' for g, rows in by_group.items())


def _raw(v) -> str:
    """Ein gespeicherter Wert als Text (eine Zahl aus einer Handänderung wie ihr Text)."""
    return "" if v is None else str(v).strip()
