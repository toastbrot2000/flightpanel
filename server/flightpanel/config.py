"""Konfiguration aus Umgebungsvariablen. Alles hat einen Default, damit der
Server auch ohne .env im Mock-Modus startet."""

import os
from dataclasses import dataclass, field


def _env(name: str, default: str) -> str:
    v = os.environ.get(name)
    return v if v not in (None, "") else default


def _hhmm(s: str) -> int:
    h, m = s.strip().split(":")
    return int(h) * 60 + int(m)


def _range(s: str) -> tuple[int, int]:
    a, b = s.split("-")
    return _hhmm(a), _hhmm(b)


@dataclass
class Config:
    # Standort und Filter
    home_lat: float = float(_env("HOME_LAT", "52.52"))
    home_lon: float = float(_env("HOME_LON", "13.40"))
    radius_km: float = float(_env("RADIUS_KM", "40"))
    max_alt_m: float = float(_env("MAX_ALT_M", "6000"))
    local_airport_iata: str = _env("LOCAL_AIRPORT_IATA", "BER").upper()

    # Sichtfeld: was man vom Fenster aus sieht. Richtung im Uhrzeigersinn von
    # VIEW_FROM bis VIEW_TO (0 = Nord, 90 = Ost), Höhenwinkel über dem Horizont,
    # Sichtweite. Default: rundum, also wie ohne Sichtfeld.
    home_alt_m: float = float(_env("HOME_ALT_M", "40"))
    view_from: float = float(_env("VIEW_FROM", "0"))
    view_to: float = float(_env("VIEW_TO", "360"))
    view_min_elev: float = float(_env("VIEW_MIN_ELEV", "0"))
    view_max_elev: float = float(_env("VIEW_MAX_ELEV", "90"))
    view_max_km: float = float(_env("VIEW_MAX_KM", "100"))
    view_only: bool = _env("VIEW_ONLY", "0") in ("1", "true", "yes")   # sonst Sichtbares nur zuerst
    show_other: bool = _env("SHOW_OTHER", "1") in ("1", "true", "yes")  # Private, Hubschrauber, Militär

    # Datenquellen
    mock: bool = _env("MOCK", "0") in ("1", "true", "yes")
    opensky_client_id: str = _env("OPENSKY_CLIENT_ID", "")
    opensky_client_secret: str = _env("OPENSKY_CLIENT_SECRET", "")
    poll_interval: int = int(_env("POLL_INTERVAL", "30"))
    adsbdb_base: str = _env("ADSBDB_BASE", "https://api.adsbdb.com/v0")

    # Anzeige
    width: int = int(_env("WIDTH", "128"))
    height: int = int(_env("HEIGHT", "32"))
    layout: str = _env("LAYOUT", "auto").upper()  # auto | A | B | C
    page_seconds: int = int(_env("PAGE_SECONDS", "4"))
    max_flights: int = int(_env("MAX_FLIGHTS", "2"))
    logo_size: int = int(_env("LOGO_SIZE", "30"))

    # Logos: automatischer Abruf beim ersten Auftauchen einer Airline. LOGO_URLS:
    # kommagetrennte Vorlagen mit {iata} oder {icao}, werden der Reihe nach probiert.
    # Leer = eingebaute Liste (logos.DEFAULT_URLS). Nur annähernd quadratische Logos
    # (Seitenverhältnis bis LOGO_MAX_ASPECT) werden genommen.
    logo_fetch: bool = _env("LOGO_FETCH", "1") in ("1", "true", "yes")
    logo_urls: tuple[str, ...] = tuple(u.strip() for u in _env("LOGO_URLS", _env("LOGO_URL", "")).split(",") if u.strip())
    logo_max_aspect: float = float(_env("LOGO_MAX_ASPECT", "1.5"))

    # Helligkeit
    brightness_day: int = int(_env("BRIGHTNESS_DAY", "90"))
    brightness_evening: int = int(_env("BRIGHTNESS_EVENING", "35"))
    day_hours: tuple[int, int] = field(default_factory=lambda: _range(_env("DAY_HOURS", "08:00-19:00")))
    quiet_hours: tuple[int, int] = field(default_factory=lambda: _range(_env("QUIET_HOURS", "23:00-06:30")))

    # Betrieb
    data_dir: str = _env("DATA_DIR", "./data")
    http_port: int = int(_env("HTTP_PORT", "8080"))
    stale_after: int = int(_env("STALE_AFTER", "120"))     # Sekunden bis "stale"
    empty_after: int = int(_env("EMPTY_AFTER", "600"))     # Sekunden bis leerer Zustand
    log_level: str = _env("LOG_LEVEL", "INFO")

    @property
    def bbox(self) -> tuple[float, float, float, float]:
        """(lamin, lomin, lamax, lomax) um Home, grob aus Radius abgeleitet."""
        import math
        dlat = self.radius_km / 111.0
        dlon = self.radius_km / (111.0 * max(0.2, math.cos(math.radians(self.home_lat))))
        return (self.home_lat - dlat, self.home_lon - dlon, self.home_lat + dlat, self.home_lon + dlon)


CFG = Config()
