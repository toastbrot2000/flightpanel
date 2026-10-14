"""Einstellungen. Server-Einstellungen (Flughafen, Quellen, Logos, Betrieb) liest
Config.from_env() beim Start aus Umgebungsvariablen (.env, jede in .env.example erklärt).
Panel-Einstellungen (Standort, Sichtfeld, Anzeige, Helligkeit, Ruhezeit, Karten: die Felder
mit panel=...) liegen je Panel in der Datenbank und kommen über Config.for_panel(); gesetzt
werden sie mit panels.py oder der Einstellungsseite, mit denselben Parsern und Fehlertexten.
Tests bauen Config(...) direkt.

Alles Ortsabhängige steht hier und nirgends sonst: Standort und Sichtfeld
(HOME_*, VIEW_*) und der Heimatflughafen (LOCAL_AIRPORT_IATA, RUNWAY_COURSE ...).
Position, Höhe und METAR-Station des Flughafens kommen aus data/airports.json, wenn
sie nicht gesetzt sind. Die Defaults sind Berlin und BER. Unsinnige Werte (etwa
PAGE_SECONDS=0) brechen den Start mit dem Namen der Variablen ab; Verdächtiges
steht als Warnung im Log.

Leerlauf-Karten lassen sich einzeln abschalten: CARD_<ID>=0, z. B. CARD_PIGEON=0
(alle IDs unter /idle und in docs/screens.md).

CALM=1, der ruhige Modus, setzt nur Defaults (CALM_PRESET): Was ausdrücklich in
der Umgebung steht, gilt trotzdem."""

import logging
import math
import os
import re
from collections.abc import Iterable, Mapping
from dataclasses import Field, dataclass, field, fields, replace
from datetime import datetime

from .data import load_airports
from .geo import haversine_km

log = logging.getLogger("config")

# Gibt es nicht mehr: Variable -> was stattdessen
REMOVED = {"IDLE_OFF": "CARD_<ID>=0 je Karte, z. B. CARD_PIGEON=0", "LOGO_URL": "LOGO_URLS"}


def _bool(s: str) -> bool:
    return s.strip().lower() in ("1", "true", "yes", "on")


def _hhmm(s: str) -> int:
    h, m = s.strip().split(":")
    return int(h) * 60 + int(m)


def _hours(s: str) -> tuple[int, int]:
    """"23:00-06:30" -> (1380, 390): Minuten des Tages, von bis."""
    try:
        a, b = s.split("-")
        return _hhmm(a), _hhmm(b)
    except ValueError:
        raise ValueError("erwartet von-bis, etwa 08:00-19:00") from None


LAYOUTS = ("A", "B", "C")                           # Fluglayouts, siehe render.py


def parse_layout(s: str) -> str:
    """"c" -> "C". auto aus älteren .env-Dateien ist C: Das hieß "C mit Logo, sonst
    A", seit dem Platzhalter für fehlende Logos ist es dasselbe."""
    v = s.strip().upper()
    v = "C" if v == "AUTO" else v
    if v not in LAYOUTS:
        raise ValueError(f"möglich: {', '.join(LAYOUTS)}")
    return v


INFO_PAGES = ("eta", "alt", "dist", "callsign")    # mögliche Infoseiten, siehe render.info_pages


def _pages(s: str) -> tuple[str, ...]:
    """"eta,alt" -> ("eta", "alt"); nur bekannte Seiten."""
    pages = tuple(p.strip().lower() for p in s.split(",") if p.strip())
    unknown = [p for p in pages if p not in INFO_PAGES]
    if unknown or not pages:
        raise ValueError(f"unbekannte Infoseite {', '.join(unknown)}; möglich: {', '.join(INFO_PAGES)}")
    return pages


def _int(lo: int, hi: int | None = None):
    """Ganzzahl in Grenzen, z. B. Sekunden pro Infoseite 1 bis DAY_S."""
    def parse(s: str) -> int:
        try:
            v = int(s)
        except ValueError:
            raise ValueError("keine ganze Zahl") from None
        if v < lo or (hi is not None and v > hi):
            raise ValueError(f"erlaubt: {lo} bis {hi}" if hi is not None else f"mindestens {lo}")
        return v
    return parse


# Sekunden je Infoseite, Flug und Leerlauf-Karte: höchstens ein Tag. Ohne Grenze kam eine Zahl mit tausenden Ziffern
# durch, und jedes Bild des Panels scheiterte an Uhrzeit // Sekunden (float und int: OverflowError).
DAY_S = 24 * 3600


def _number(s: str) -> float:
    """Text als Kommazahl. Ein Komma statt des Punkts ist der häufigste Tippfehler (deutsche Tastatur, Telefon): Dann
    nennt der Fehler die Schreibweise mit Punkt, statt ihn still umzudeuten."""
    try:
        return float(s)
    except ValueError:
        pass
    dotted = s.strip().replace(",", ".")
    try:
        float(dotted)
    except ValueError:
        raise ValueError("keine Zahl") from None
    raise ValueError(f"keine Zahl; mit Punkt statt Komma: {dotted}")


def _share(s: str) -> float:
    v = _number(s)
    if not 0 <= v <= 1:
        raise ValueError("erlaubt: 0 bis 1")
    return v


def _float(lo: float, hi: float, above: bool = False):
    """Kommazahl in Grenzen, endlich: nan oder inf zerlegten sonst Abruf und Bild. above: mehr als lo (ein
    Radius von 0 km zeigt nichts). Die Grenzen sind großzügig, sie fangen nur, was nirgends Sinn hat."""
    allowed = f"erlaubt: {'über ' if above else ''}{lo:g} bis {hi:g}"

    def parse(s: str) -> float:
        v = _number(s)
        if not math.isfinite(v) or v > hi or (v <= lo if above else v < lo):
            raise ValueError(allowed)
        return v
    return parse


_lat = _float(-90, 90)                  # Breitengrad
_lon = _float(-180, 180)                # Längengrad
_height = _float(-500, 10000)           # Meter über dem Meer
_km = _float(0, 1000, above=True)       # Radien und Sichtweite
_deg = _float(0, 360)                   # Richtung, 0 = Nord
_elev = _float(-90, 90)                 # Höhenwinkel über dem Horizont


def _matching(pattern: str, hint: str):
    """Text in Kleinbuchstaben, der ganz auf pattern passt, sonst ValueError mit hint (wie die anderen Parser)."""
    rx = re.compile(pattern)

    def parse(s: str) -> str:
        v = s.strip().lower()
        if not rx.fullmatch(v):
            raise ValueError(hint)
        return v
    return parse


_LABEL = r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?"
_team = _matching(_LABEL, "der Team-Name aus Zero Trust, etwa beispiel (aus beispiel.cloudflareaccess.com)")
_aud = _matching(r"[0-9a-f]{64}", "das Application Audience (AUD) Tag der Access-Anwendung, 64 Hexziffern")
_host = _matching(rf"(?:{_LABEL}\.)+[a-z]{{2,63}}", "ein Hostname mit Punkt, etwa flightpanel.example.com, ohne https://")
_name = _matching(rf"{_LABEL}(?:\.{_LABEL})*", "ein Hostname wie nas oder nas.fritz.box")


def _names(s: str) -> tuple[str, ...]:
    """"nas.fritz.box, Laptop" -> ("nas.fritz.box", "laptop")."""
    return tuple(_name(x) for x in s.split(",") if x.strip())


PANEL_ID = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")


def panel_id(text: str) -> str:
    """Kennung eines Panels (LAN_PANEL, panels add): Kleinbuchstaben, Ziffern, - und _, höchstens 32 Zeichen."""
    if not PANEL_ID.fullmatch(text):                # fullmatch: $ ließe einen Zeilenumbruch am Ende durch
        raise ValueError("Kleinbuchstaben, Ziffern, - und _, höchstens 32 Zeichen")
    return text


# Firmware-Kanäle: je Panel einer (Spalte fw_channel, Default stable), die Dateien in DATA_DIR/firmware/<Kanal>/
FW_CHANNELS = ("stable", "canary")


def in_hours(rng: tuple[int, int], minute: int) -> bool:
    """Liegt die Minute des Tages im Fenster? Fenster über Mitternacht gehen auch."""
    a, b = rng
    return a <= minute < b if a <= b else (minute >= a or minute < b)


def _env(name: str, default, parse=None, panel: tuple[str, str] | None = None):
    """Ein Feld aus der Umgebungsvariablen name (Server-Einstellung, .env) oder, mit
    panel=(Gruppe, Erklärung), eine Panel-Einstellung (Datenbank, panels.py; beim ersten
    Start einmalig aus einer älteren .env übernommen). parse wandelt den Text; ohne parse
    der Typ des Defaults."""
    if parse is None:
        parse = _bool if isinstance(default, bool) else type(default)
    return field(default=default, metadata={"env": name, "parse": parse, "panel": panel})


CARD_PREFIX = "CARD_"


def _text(v) -> str:
    """Ein Rohwert als Text. In der Datenbank stehen Strings; eine Zahl aus einer Handänderung gilt wie ihr Text,
    statt den Start an einem AttributeError scheitern zu lassen."""
    return "" if v is None else str(v).strip()


def card_off(raw) -> bool:
    """Schaltet der Wert einer Zeile CARD_<ID> die Karte ab? Ja bei 0, false, aus und allem anderen, was kein Ja ist;
    leer oder nicht gesetzt heißt an. Für for_panel, die alte .env und die Einstellungsseite."""
    text = _text(raw)
    return bool(text) and not _bool(text)


def _parse(fs: Iterable[Field], src: Mapping[str, str], problems: list[tuple[str, str]] | None = None) -> dict:
    """Werte der Felder fs aus den rohen Strings in src (.env oder Datenbank), für from_env
    und for_panel gemeinsam: leere zählen wie nicht gesetzte, ein Fehler nennt die Variable
    (NAME='roh': Grund des Parsers). Ergebnis: {Feldname: Wert}. Mit problems bricht ein
    Fehler nicht ab: Der Wert bleibt weg, (NAME, Fehler) kommt in die Liste."""
    values = {}
    for f in fs:
        name = f.metadata["env"]
        raw = _text(src.get(name))
        if raw:
            try:
                values[f.name] = f.metadata["parse"](raw)
            except ValueError as e:
                if problems is None:
                    raise ValueError(f"{name}={raw!r}: {e}") from None
                problems.append((name, f"{name}={raw!r}: {e}"))
    return values


@dataclass(frozen=True)
class Config:
    # --- Standort und Abruf
    home_lat: float = _env("HOME_LAT", 52.52, _lat, panel=("Standort", "Breitengrad des Fensters, Dezimalgrad"))
    home_lon: float = _env("HOME_LON", 13.40, _lon, panel=("Standort", "Längengrad des Fensters, Dezimalgrad"))
    home_alt_m: float = _env("HOME_ALT_M", 40.0, _height,
                             panel=("Standort", "Augenhöhe am Fenster über dem Meer, Meter"))
    radius_km: float = _env("RADIUS_KM", 40.0, _km,
                            panel=("Standort", "angezeigt wird, was höchstens so weit weg ist, km"))
    max_alt_m: float = _env("MAX_ALT_M", 6000.0, _float(-500, 20000),
                            panel=("Standort", "... und höchstens so hoch, Meter"))
    # Abrufradius für Statistik, seltene Flugzeuge und Notfall-Squawks; ohne Angabe
    # wie RADIUS_KM (siehe for_panel)
    scan_radius_km: float = _env("SCAN_RADIUS_KM", 40.0, _km,
                                 panel=("Standort",
                                        "Abrufradius für Statistik, Seltenes und Notfälle, km; leer = RADIUS_KM"))

    # --- Sichtfeld: Richtung im Uhrzeigersinn von VIEW_FROM bis VIEW_TO (0 = Nord,
    # 90 = Ost), Höhenwinkel über dem Horizont, Sichtweite. Default: rundum.
    view_from: float = _env("VIEW_FROM", 0.0, _deg,
                            panel=("Sichtfeld", "wo es beginnt, Grad im Uhrzeigersinn (0 Nord, 90 Ost)"))
    view_to: float = _env("VIEW_TO", 360.0, _deg, panel=("Sichtfeld", "wo es endet; 0 bis 360 = rundum"))
    view_min_elev: float = _env("VIEW_MIN_ELEV", 0.0, _elev,
                                panel=("Sichtfeld", "Mindesthöhe über dem Horizont, Grad (Häuser, Bäume)"))
    view_max_elev: float = _env("VIEW_MAX_ELEV", 90.0, _elev, panel=("Sichtfeld", "Obergrenze, Grad (Balkon darüber)"))
    view_max_km: float = _env("VIEW_MAX_KM", 100.0, _km, panel=("Sichtfeld", "Sichtweite, km"))
    view_only: bool = _env("VIEW_ONLY", False, panel=("Sichtfeld", "1: nur zeigen, was im Sichtfeld ist"))
    show_other: bool = _env("SHOW_OTHER", True,
                            panel=("Sichtfeld",
                                   "1: Privatflieger, Hubschrauber, Polizei, Militär in der Rotation, "
                                   "mit Symbol statt Logo"))

    # --- Heimatflughafen
    local_airport_iata: str = _env("LOCAL_AIRPORT_IATA", "BER", str.upper)
    airport_lat: float = _env("AIRPORT_LAT", 52.3617, _lat)     # ohne Angabe aus airports.json
    airport_lon: float = _env("AIRPORT_LON", 13.5023, _lon)
    airport_alt_m: float = _env("AIRPORT_ALT_M", 48.0)
    runway_course: float = _env("RUNWAY_COURSE", 249.0, _deg)   # Endanflug einer Landerichtung, BER 25
    curfew_hours: tuple[int, int] = _env("CURFEW_HOURS", (0, 5 * 60), _hours)   # Nachtflugverbot
    metar: bool = _env("METAR", True)                       # Wetter von aviationweather.gov
    metar_station: str = _env("METAR_STATION", "EDDB", str.upper)   # ohne Angabe aus airports.json

    # --- Datenquellen. adsblol: Community-Netz ohne Account; fällt es aus, springt
    # OpenSky ein. opensky: nur OpenSky.
    mock: bool = _env("MOCK", False)
    source: str = _env("SOURCE", "adsblol", str.lower)
    poll_interval: int = _env("POLL_INTERVAL", 20, _int(5))
    opensky_client_id: str = _env("OPENSKY_CLIENT_ID", "")
    opensky_client_secret: str = _env("OPENSKY_CLIENT_SECRET", "")
    # Routen: adsb.im zuerst, dann adsbdb; ohne Route der Abflughafen aus der Flugspur
    route_adsbim: bool = _env("ADSBIM", True)
    track_origin: bool = _env("TRACK_ORIGIN", True)
    adsbdb_base: str = _env("ADSBDB_BASE", "https://api.adsbdb.com/v0")

    # --- Anzeige
    width: int = _env("WIDTH", 128)
    height: int = _env("HEIGHT", 32)
    layout: str = _env("LAYOUT", "C", parse_layout,
                       panel=("Anzeige", "C Logo und drei Zeilen, A ohne Logo, B zwei Flüge"))
    max_flights: int = _env("MAX_FLIGHTS", 3, _int(1, 10), panel=("Anzeige", "so viele Flüge wechseln sich ab"))
    calm: bool = _env("CALM", False,
                      panel=("Anzeige", "1: ruhiger Modus (nichts blinkt, seltener Wechsel, nur Landezeit und Höhe)"))
    page_seconds: int = _env("PAGE_SECONDS", 4, _int(1, DAY_S), panel=("Anzeige", "Sekunden je Infoseite"))
    flight_seconds: int = _env("FLIGHT_SECONDS", 0, _int(0, DAY_S),
                               panel=("Anzeige", "Sekunden je Flug mindestens; 0 = jede Infoseite einmal"))
    info_pages: tuple[str, ...] = _env("INFO_PAGES", INFO_PAGES, _pages,
                                       panel=("Anzeige", "Infoseiten der Reihe nach: eta, alt, dist, callsign"))
    blink: bool = _env("BLINK", True, panel=("Anzeige", "0: nichts blinkt außer einem Notfall"))

    # --- Leerlauf-Karten, wenn keine Airline zu sehen ist
    idle_seconds: int = _env("IDLE_SECONDS", 12, _int(1, DAY_S), panel=("Anzeige", "Sekunden je Leerlauf-Karte"))
    idle_clock_share: float = _env("IDLE_CLOCK_SHARE", 0.35, _share,
                                   panel=("Anzeige", "Anteil der Uhr an den Leerlauf-Karten, 0 bis 1"))
    cards_off: frozenset[str] = frozenset()                 # CARD_<ID>=0, siehe for_panel
    iss: bool = _env("ISS", True)                           # Bahndaten von Celestrak

    # --- Logos: Abruf beim ersten Auftauchen einer Airline. LOGO_URLS: kommagetrennte
    # Vorlagen mit {iata} oder {icao}, optional "|2.0" als eigenes Seitenverhältnis;
    # leer = logos.DEFAULT_URLS.
    logo_fetch: bool = _env("LOGO_FETCH", True)
    logo_urls: str = _env("LOGO_URLS", "")
    logo_size: int = _env("LOGO_SIZE", 32, _int(8))
    logo_max_aspect: float = _env("LOGO_MAX_ASPECT", 1.5)

    # --- Helligkeit und Ruhezeiten
    brightness_day: int = _env("BRIGHTNESS_DAY", 90, _int(0, 255), panel=("Helligkeit", "tagsüber, 0 bis 255"))
    brightness_evening: int = _env("BRIGHTNESS_EVENING", 35, _int(0, 255), panel=("Helligkeit", "abends, 0 bis 255"))
    day_hours: tuple[int, int] = _env("DAY_HOURS", (8 * 60, 19 * 60), _hours,
                                      panel=("Helligkeit", "Tag von-bis, z. B. 08:00-19:00"))
    quiet_hours: tuple[int, int] = _env("QUIET_HOURS", (0, 6 * 60 + 30), _hours,
                                        panel=("Helligkeit", "Ruhezeit: Panel dunkel, kein Abruf, z. B. 00:00-06:30"))

    # --- Betrieb
    data_dir: str = _env("DATA_DIR", "./data")
    http_port: int = _env("HTTP_PORT", 8080, _int(1, 65535))
    lan_panel: str = _env("LAN_PANEL", "home", panel_id)    # das Panel am LAN-Port (:8080)
    # Weitere Panels (panels add, nicht das am LAN-Port): Ein Abruf deckt alle ab. Damit er klein bleibt, steht ihr
    # Fenster höchstens PANEL_MAX_KM vom Flughafen, und sie rufen höchstens PANEL_MAX_RADIUS_KM weit ab.
    panel_max_km: float = _env("PANEL_MAX_KM", 60.0, _km)
    panel_max_radius_km: float = _env("PANEL_MAX_RADIUS_KM", 80.0, _km)
    stale_after: int = _env("STALE_AFTER", 120, _int(0))    # Sekunden ohne Daten bis gedimmt
    empty_after: int = _env("EMPTY_AFTER", 600, _int(0))    # ... bis leer
    log_level: str = _env("LOG_LEVEL", "INFO")

    # --- Zugang von außen (optional, docs/remote-panels.md): ein zweiter Port nur für Cloudflare Access. Alle vier oder
    # keiner; ohne sie lädt der Server nichts davon (access.py).
    public_port: int = _env("PUBLIC_PORT", 0, _int(0, 65535))
    access_team: str = _env("ACCESS_TEAM", "", _team)
    access_aud: str = _env("ACCESS_AUD", "", _aud)
    public_host: str = _env("PUBLIC_HOST", "", _host)
    settings_page: bool = _env("SETTINGS_PAGE", False)          # Einstellungsseite /settings
    lan_hosts: tuple[str, ...] = _env("LAN_HOSTS", (), _names)  # weitere Namen, unter denen :8080 erreichbar ist

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "Config":
        """Server-Einstellungen aus Umgebungsvariablen; leere zählen wie nicht gesetzte.
        Panel-Felder bleiben auf ihren Defaults (dafür: for_panel)."""
        env = os.environ if env is None else env
        values = _parse([f for f in fields(cls) if f.metadata.get("env") and not f.metadata.get("panel")], env)
        for name, instead in REMOVED.items():
            if (env.get(name) or "").strip():
                log.warning("%s gibt es nicht mehr und wird ignoriert; stattdessen %s", name, instead)
        _airport_from_list(values, values.get("data_dir", cls.data_dir))
        _check_public(values)
        return cls(**values)

    @classmethod
    def for_panel(cls, server: "Config", settings: Mapping[str, str],
                  problems: list[tuple[str, str]] | None = None, remote: bool = False) -> "Config":
        """server plus die Einstellungen eines Panels: rohe Strings wie in .env, aus der
        Datenbank oder von panels.py, geprüft mit denselben Parsern und Fehlertexten.
        Nicht Gesetztes bleibt wie in server (im Betrieb: die Defaults). Karten gelten,
        sobald eine CARD-Zeile dabei ist: CARD_<ID>=0 schaltet ab.

        Ohne problems bricht ein unbekannter Name oder ein ungültiger Wert ab (panels.py:
        nichts Falsches speichern). Mit einer Liste problems bleibt beides weg und steht
        dort als (NAME, Fehler), der Rest gilt: So startet der Server auch, wenn eine spätere
        Fassung eine Einstellung nicht mehr kennt oder strenger prüft (App).

        remote: ein weiteres Panel (nicht das am LAN-Port). Dann gelten RADIUS_KM und SCAN_RADIUS_KM höchstens bis
        PANEL_MAX_RADIUS_KM: Ein größerer Wert ist ein Fehler wie jeder ungültige (ohne problems bricht er ab, mit
        problems bleibt er weg), und auch was ohne Angabe gälte, bleibt darunter. Ob das Fenster nah genug am
        Flughafen liegt, sagt too_far()."""
        panel = panel_fields()
        unknown = sorted(k for k in settings if not is_panel_key(k))
        if unknown and problems is None:
            raise ValueError(f"keine Panel-Einstellung: {', '.join(unknown)}; möglich: "
                             f"{', '.join(f.metadata['env'] for f in panel)}, CARD_<ID>")
        if problems is not None:
            problems += [(k, f"{k}: keine Panel-Einstellung") for k in unknown]
        values = _parse(panel, settings, problems)
        if remote:
            _cap_radii(server, settings, values, problems)
        if "radius_km" in values and "scan_radius_km" not in values:
            values["scan_radius_km"] = values["radius_km"]
        if remote:
            for key in ("radius_km", "scan_radius_km"):
                values[key] = min(values.get(key, getattr(server, key)), server.panel_max_radius_km)
        if values.get("calm"):
            kept = [k for k in CALM_PRESET if k in values]
            if kept:
                log.warning("CALM=1, aber ausdrücklich gesetzt und damit gültig: %s",
                            ", ".join(f"{_env_name(k)}={values[k]}" for k in kept))
            for k, v in CALM_PRESET.items():
                values.setdefault(k, v)
        cards = [k for k in settings if k.startswith(CARD_PREFIX)]
        if cards:
            values["cards_off"] = frozenset(k[len(CARD_PREFIX):].lower() for k in cards if card_off(settings[k]))
        return replace(server, **values)

    # ------------------------------------------------------------- abgeleitet --
    @property
    def fetch_radius_km(self) -> float:
        return max(self.radius_km, self.scan_radius_km)

    @property
    def public(self) -> bool:
        """Ist der Zugang von außen eingerichtet (PUBLIC_PORT und die drei anderen, _check_public)?"""
        return bool(self.public_port)

    @property
    def view_span(self) -> float:
        return (self.view_to - self.view_from) % 360 or 360

    @property
    def view_center(self) -> float:
        return (self.view_from + self.view_span / 2) % 360

    @property
    def bbox(self) -> tuple[float, float, float, float]:
        """(lamin, lomin, lamax, lomax) um Home, grob aus dem Abrufradius."""
        r = self.fetch_radius_km
        dlat = r / 111.0
        dlon = r / (111.0 * max(0.2, math.cos(math.radians(self.home_lat))))
        return (self.home_lat - dlat, self.home_lon - dlon, self.home_lat + dlat, self.home_lon + dlon)

    def brightness(self, now: float) -> int:
        """Helligkeit nach Uhrzeit; in den Quiet Hours 0 (Panel schwarz, kein Abruf)."""
        t = datetime.fromtimestamp(now)
        minute = t.hour * 60 + t.minute
        if in_hours(self.quiet_hours, minute):
            return 0
        if in_hours(self.day_hours, minute):
            return self.brightness_day
        return self.brightness_evening


# Ruhiger Modus: seltener wechseln, nicht blinken, in der Infozeile nur Landezeit und
# Höhe/Tempo. Nur Defaults; ausdrücklich gesetzte Werte gehen vor.
CALM_PRESET = {"page_seconds": 10, "flight_seconds": 20, "idle_seconds": 30, "blink": False,
               "info_pages": ("eta", "alt")}


def _env_name(field_name: str) -> str:
    return next(f.metadata["env"] for f in fields(Config) if f.name == field_name)


PUBLIC_KEYS = ("public_port", "access_team", "access_aud", "public_host")


def _check_public(values: dict) -> None:
    """Der Zugang von außen braucht alle vier Angaben. Ein Teil davon ist ein Tippfehler oder eine halbe
    Einrichtung: lieber nicht starten als still ohne oder halb offen laufen."""
    have = [k for k in PUBLIC_KEYS if values.get(k)]
    if not have:
        return
    missing = [_env_name(k) for k in PUBLIC_KEYS if k not in have]
    if missing:
        raise ValueError(f"{', '.join(_env_name(k) for k in have)} gesetzt, es fehlt {', '.join(missing)}: für den "
                         "Zugang von außen alle vier setzen (docs/remote-panels.md), sonst keinen")
    if values["public_port"] == values.get("http_port", Config.http_port):
        raise ValueError("PUBLIC_PORT muss ein anderer Port sein als HTTP_PORT")


def _cap_radii(server: Config, settings: Mapping[str, str], values: dict,
               problems: list[tuple[str, str]] | None) -> None:
    """Weitere Panels: ausdrücklich gesetzte Radien über PANEL_MAX_RADIUS_KM sind ungültig, wie in _parse."""
    cap = server.panel_max_radius_km
    for key in ("radius_km", "scan_radius_km"):
        if values.get(key, 0) > cap:
            name = _env_name(key)
            msg = f"{name}={_text(settings[name])!r}: für weitere Panels höchstens {cap:g} km (PANEL_MAX_RADIUS_KM)"
            if problems is None:
                raise ValueError(msg)
            problems.append((name, msg))
            del values[key]


def too_far(server: Config, panel: Config) -> str | None:
    """Liegt das Fenster eines weiteren Panels (nicht das am LAN-Port) zu weit vom Heimatflughafen? Ein Abruf deckt
    alle Panels ab, und ein Fenster weit weg machte ihn für alle größer. None, wenn es passt; sonst der Satz für
    panels.py, Log und /issues."""
    d = haversine_km(server.airport_lat, server.airport_lon, panel.home_lat, panel.home_lon)
    if d <= server.panel_max_km:
        return None
    return (f"HOME_LAT/HOME_LON liegen {d:.0f} km von {server.local_airport_iata}, für weitere Panels höchstens "
            f"{server.panel_max_km:g} km (PANEL_MAX_KM)")


def panel_fields() -> list[Field]:
    """Die Panel-Einstellungen (Felder mit panel=...), in der Reihenfolge der Klasse."""
    return [f for f in fields(Config) if f.metadata.get("panel")]


def is_panel_key(key: str) -> bool:
    return any(f.metadata["env"] == key for f in panel_fields()) or (
        key.startswith(CARD_PREFIX) and len(key) > len(CARD_PREFIX))


def default_text(f: Field) -> str:
    """Default eines Feldes, so wie man ihn setzen würde ("08:00-19:00", "eta,alt", "1")."""
    v = f.default
    if isinstance(v, bool):
        return "1" if v else "0"
    if f.metadata["parse"] is _hours:
        return f"{v[0] // 60:02d}:{v[0] % 60:02d}-{v[1] // 60:02d}:{v[1] % 60:02d}"
    if isinstance(v, tuple):
        return ",".join(v)
    return str(v)


def panel_settings_from_env(env: Mapping[str, str]) -> dict[str, str]:
    """Panel-Einstellungen aus einer (älteren) .env: beim ersten Start einmalig ins Panel
    übernommen, danach ignoriert. Karten nur, wenn abgeschaltet; an ist der Default."""
    out = {f.metadata["env"]: env[f.metadata["env"]].strip() for f in panel_fields()
           if (env.get(f.metadata["env"]) or "").strip()}
    out.update({k: v.strip() for k, v in env.items()
                if k.startswith(CARD_PREFIX) and len(k) > len(CARD_PREFIX) and card_off(v)})
    return out


def _airport_from_list(values: dict, data_dir: str) -> None:
    """Position, Höhe und METAR-Station des Heimatflughafens aus data/airports.json,
    soweit nicht gesetzt. Fehlt der Flughafen dort, müssen sie in .env stehen; ohne
    METAR-Station gibt es dann kein Wetter. Passt Gesetztes nicht zur Liste (alte
    .env nach einem Flughafenwechsel), steht das im Log."""
    iata = values.get("local_airport_iata", Config.local_airport_iata)
    ap = next((a for a in load_airports(data_dir) if a.iata == iata), None)
    if ap is None:
        if iata == Config.local_airport_iata:
            return                  # BER ohne airports.json: die Defaults sind BER
        missing = [_env_name(k) for k in ("airport_lat", "airport_lon") if k not in values]
        if missing:
            raise ValueError(f"LOCAL_AIRPORT_IATA={iata} steht nicht in {data_dir}/airports.json: "
                             f"{' und '.join(missing)} setzen, dazu AIRPORT_ALT_M und METAR_STATION")
        if "airport_alt_m" not in values:
            log.warning("AIRPORT_ALT_M fehlt für %s, nehme %.0f m", iata, Config.airport_alt_m)
        if "metar_station" not in values and values.get("metar", True):
            log.warning("METAR_STATION fehlt für %s: kein Wetter", iata)
            values["metar"] = False
        return
    if "airport_lat" in values and "airport_lon" in values:
        d = haversine_km(values["airport_lat"], values["airport_lon"], ap.lat, ap.lon)
        if d > 10:
            log.warning("AIRPORT_LAT/AIRPORT_LON liegen %.0f km von %s laut airports.json", d, iata)
    values.setdefault("airport_lat", ap.lat)
    values.setdefault("airport_lon", ap.lon)
    if ap.elev_m is not None:
        values.setdefault("airport_alt_m", float(ap.elev_m))
    if "metar_station" in values:
        if ap.icao and values["metar_station"] != ap.icao:
            log.warning("METAR_STATION=%s, airports.json kennt für %s %s", values["metar_station"], iata, ap.icao)
    elif ap.icao:
        values["metar_station"] = ap.icao
    elif values.get("metar", True):
        log.warning("%s hat keinen ICAO-Code in airports.json: METAR_STATION setzen, sonst kein Wetter", iata)
        values["metar"] = False
