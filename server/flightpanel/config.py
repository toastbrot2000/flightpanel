"""Einstellungen. Config.from_env() liest sie beim Start aus Umgebungsvariablen
(.env, jede einzelne erklärt in .env.example); Tests bauen Config(...) direkt.

Alles Ortsabhängige steht hier und nirgends sonst: Standort und Sichtfeld
(HOME_*, VIEW_*) und der Heimatflughafen (LOCAL_AIRPORT_IATA, RUNWAY_COURSE ...).
Position, Höhe und METAR-Station des Flughafens kommen aus data/airports.json, wenn
sie nicht gesetzt sind. Die Defaults sind Berlin und BER. Unsinnige Werte (etwa
PAGE_SECONDS=0) brechen den Start mit dem Namen der Variablen ab; Verdächtiges
steht als Warnung im Log.

Leerlauf-Karten lassen sich einzeln abschalten: CARD_<ID>=0, z. B. CARD_JOKE=0
(alle IDs unter /idle und in docs/screens.md).

CALM=1, der ruhige Modus, setzt nur Defaults (CALM_PRESET): Was ausdrücklich in
der Umgebung steht, gilt trotzdem."""

import logging
import math
import os
from collections.abc import Mapping
from dataclasses import dataclass, field, fields
from datetime import datetime

from .data import load_airports
from .geo import haversine_km

log = logging.getLogger("config")

# Gibt es nicht mehr: Variable -> was stattdessen
REMOVED = {"IDLE_OFF": "CARD_<ID>=0 je Karte, z. B. CARD_JOKE=0", "LOGO_URL": "LOGO_URLS"}


def _bool(s: str) -> bool:
    return s.strip().lower() in ("1", "true", "yes", "on")


def _hhmm(s: str) -> int:
    h, m = s.strip().split(":")
    return int(h) * 60 + int(m)


def _hours(s: str) -> tuple[int, int]:
    """"23:00-06:30" -> (1380, 390): Minuten des Tages, von bis."""
    a, b = s.split("-")
    return _hhmm(a), _hhmm(b)


INFO_PAGES = ("eta", "alt", "dist", "callsign")    # mögliche Infoseiten, siehe render.info_pages


def _pages(s: str) -> tuple[str, ...]:
    """"eta,alt" -> ("eta", "alt"); nur bekannte Seiten."""
    pages = tuple(p.strip().lower() for p in s.split(",") if p.strip())
    unknown = [p for p in pages if p not in INFO_PAGES]
    if unknown or not pages:
        raise ValueError(f"unbekannte Infoseite {', '.join(unknown)}; möglich: {', '.join(INFO_PAGES)}")
    return pages


def _int(lo: int, hi: int | None = None):
    """Ganzzahl in Grenzen, z. B. Sekunden pro Infoseite mindestens 1."""
    def parse(s: str) -> int:
        v = int(s)
        if v < lo or (hi is not None and v > hi):
            raise ValueError(f"erlaubt: {lo} bis {hi}" if hi is not None else f"mindestens {lo}")
        return v
    return parse


def _share(s: str) -> float:
    v = float(s)
    if not 0 <= v <= 1:
        raise ValueError("erlaubt: 0 bis 1")
    return v


def in_hours(rng: tuple[int, int], minute: int) -> bool:
    """Liegt die Minute des Tages im Fenster? Fenster über Mitternacht gehen auch."""
    a, b = rng
    return a <= minute < b if a <= b else (minute >= a or minute < b)


def _env(name: str, default, parse=None):
    """Ein Feld, das aus der Umgebungsvariablen name kommt. parse wandelt den Text;
    ohne parse der Typ des Defaults."""
    if parse is None:
        parse = _bool if isinstance(default, bool) else type(default)
    return field(default=default, metadata={"env": name, "parse": parse})


@dataclass(frozen=True)
class Config:
    # --- Standort und Abruf
    home_lat: float = _env("HOME_LAT", 52.52)
    home_lon: float = _env("HOME_LON", 13.40)
    home_alt_m: float = _env("HOME_ALT_M", 40.0)            # Augenhöhe am Fenster über dem Meer
    radius_km: float = _env("RADIUS_KM", 40.0)              # angezeigt wird, was so nah ist
    max_alt_m: float = _env("MAX_ALT_M", 6000.0)            # ... und so tief
    # Abrufradius für Statistik, seltene Flugzeuge und Notfall-Squawks; ohne Angabe
    # wie RADIUS_KM (siehe from_env)
    scan_radius_km: float = _env("SCAN_RADIUS_KM", 40.0)

    # --- Sichtfeld: Richtung im Uhrzeigersinn von VIEW_FROM bis VIEW_TO (0 = Nord,
    # 90 = Ost), Höhenwinkel über dem Horizont, Sichtweite. Default: rundum.
    view_from: float = _env("VIEW_FROM", 0.0)
    view_to: float = _env("VIEW_TO", 360.0)
    view_min_elev: float = _env("VIEW_MIN_ELEV", 0.0)
    view_max_elev: float = _env("VIEW_MAX_ELEV", 90.0)
    view_max_km: float = _env("VIEW_MAX_KM", 100.0)
    view_only: bool = _env("VIEW_ONLY", False)              # nur Sichtbares zeigen, sonst nur zuerst
    show_other: bool = _env("SHOW_OTHER", True)             # Privatflieger, Hubschrauber, Militär

    # --- Heimatflughafen
    local_airport_iata: str = _env("LOCAL_AIRPORT_IATA", "BER", str.upper)
    airport_lat: float = _env("AIRPORT_LAT", 52.3617)       # ohne Angabe aus airports.json
    airport_lon: float = _env("AIRPORT_LON", 13.5023)
    airport_alt_m: float = _env("AIRPORT_ALT_M", 48.0)
    runway_course: float = _env("RUNWAY_COURSE", 249.0)     # Endanflug einer Landerichtung, BER 25
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
    layout: str = _env("LAYOUT", "AUTO", str.upper)         # AUTO | A | B | C, siehe render.py
    max_flights: int = _env("MAX_FLIGHTS", 3, _int(1))
    calm: bool = _env("CALM", False)                        # ruhiger Modus, setzt CALM_PRESET
    page_seconds: int = _env("PAGE_SECONDS", 4, _int(1))    # so lange steht eine Infoseite
    flight_seconds: int = _env("FLIGHT_SECONDS", 0, _int(0))  # so lange mindestens ein Flug; 0: jede Seite einmal
    info_pages: tuple[str, ...] = _env("INFO_PAGES", INFO_PAGES, _pages)
    blink: bool = _env("BLINK", True)                       # sonst blinkt nur ein Notfall

    # --- Leerlauf-Karten, wenn keine Airline zu sehen ist
    idle_seconds: int = _env("IDLE_SECONDS", 12, _int(1))
    idle_clock_share: float = _env("IDLE_CLOCK_SHARE", 0.35, _share)
    cards_off: frozenset[str] = frozenset()                 # CARD_<ID>=0, siehe from_env
    iss: bool = _env("ISS", True)                           # Bahndaten von Celestrak

    # --- Logos: Abruf beim ersten Auftauchen einer Airline. LOGO_URLS: kommagetrennte
    # Vorlagen mit {iata} oder {icao}, optional "|2.0" als eigenes Seitenverhältnis;
    # leer = logos.DEFAULT_URLS.
    logo_fetch: bool = _env("LOGO_FETCH", True)
    logo_urls: str = _env("LOGO_URLS", "")
    logo_size: int = _env("LOGO_SIZE", 32, _int(8))
    logo_max_aspect: float = _env("LOGO_MAX_ASPECT", 1.5)

    # --- Helligkeit und Ruhezeiten
    brightness_day: int = _env("BRIGHTNESS_DAY", 90, _int(0, 255))
    brightness_evening: int = _env("BRIGHTNESS_EVENING", 35, _int(0, 255))
    day_hours: tuple[int, int] = _env("DAY_HOURS", (8 * 60, 19 * 60), _hours)
    quiet_hours: tuple[int, int] = _env("QUIET_HOURS", (23 * 60, 6 * 60 + 30), _hours)

    # --- Betrieb
    data_dir: str = _env("DATA_DIR", "./data")
    http_port: int = _env("HTTP_PORT", 8080, _int(1, 65535))
    stale_after: int = _env("STALE_AFTER", 120, _int(0))    # Sekunden ohne Daten bis gedimmt
    empty_after: int = _env("EMPTY_AFTER", 600, _int(0))    # ... bis leer
    log_level: str = _env("LOG_LEVEL", "INFO")

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "Config":
        """Aus Umgebungsvariablen; leere zählen wie nicht gesetzte."""
        env = os.environ if env is None else env
        values = {}
        for f in fields(cls):
            name = f.metadata.get("env")
            raw = (env.get(name) or "").strip() if name else ""
            if raw:
                try:
                    values[f.name] = f.metadata["parse"](raw)
                except ValueError as e:
                    raise ValueError(f"{name}={raw!r}: {e}") from None
        values.setdefault("scan_radius_km", values.get("radius_km", cls.scan_radius_km))
        if values.get("calm"):
            kept = [k for k in CALM_PRESET if k in values]
            if kept:
                log.warning("CALM=1, aber ausdrücklich gesetzt und damit gültig: %s",
                            ", ".join(f"{_env_name(k)}={values[k]}" for k in kept))
            for k, v in CALM_PRESET.items():
                values.setdefault(k, v)
        for name, instead in REMOVED.items():
            if (env.get(name) or "").strip():
                log.warning("%s gibt es nicht mehr und wird ignoriert; stattdessen %s", name, instead)
        values["cards_off"] = frozenset(k[len("CARD_"):].lower() for k, v in env.items()
                                        if k.startswith("CARD_") and v.strip() and not _bool(v))
        _airport_from_list(values, values.get("data_dir", cls.data_dir))
        return cls(**values)

    # ------------------------------------------------------------- abgeleitet --
    @property
    def fetch_radius_km(self) -> float:
        return max(self.radius_km, self.scan_radius_km)

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

    def active_seconds_per_day(self) -> int:
        """Sekunden pro Tag außerhalb der Quiet Hours, in denen abgerufen wird."""
        a, b = self.quiet_hours
        return (24 * 60 - (b - a) % (24 * 60)) * 60


# Ruhiger Modus: seltener wechseln, nicht blinken, in der Infozeile nur Landezeit und
# Höhe/Tempo. Nur Defaults; ausdrücklich gesetzte Werte gehen vor.
CALM_PRESET = {"page_seconds": 10, "flight_seconds": 20, "idle_seconds": 30, "blink": False,
               "info_pages": ("eta", "alt")}


def _env_name(field_name: str) -> str:
    return next(f.metadata["env"] for f in fields(Config) if f.name == field_name)


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
