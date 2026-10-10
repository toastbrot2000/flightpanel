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


def in_hours(rng: tuple[int, int], minute: int) -> bool:
    """Liegt die Minute des Tages im Fenster? Fenster über Mitternacht gehen auch."""
    a, b = rng
    return a <= minute < b if a <= b else (minute >= a or minute < b)


def _on(name: str, default: str) -> bool:
    return _env(name, default) in ("1", "true", "yes")


@dataclass
class Config:
    # Standort und Filter
    home_lat: float = float(_env("HOME_LAT", "52.52"))
    home_lon: float = float(_env("HOME_LON", "13.40"))
    radius_km: float = float(_env("RADIUS_KM", "40"))
    # Abrufradius für Statistik, seltene Flugzeuge und Notfall-Squawks; angezeigt wird
    # weiter nur, was in RADIUS_KM und unter MAX_ALT_M ist. Default: wie RADIUS_KM.
    scan_radius_km: float = float(_env("SCAN_RADIUS_KM", _env("RADIUS_KM", "40")))
    max_alt_m: float = float(_env("MAX_ALT_M", "6000"))
    local_airport_iata: str = _env("LOCAL_AIRPORT_IATA", "BER").upper()
    # Für die Landefolge: Flughafen und Anflugkurs einer Landerichtung (BER 25: 249°,
    # die Gegenrichtung ergibt sich). Default BER.
    airport_lat: float = float(_env("AIRPORT_LAT", "52.3667"))
    airport_lon: float = float(_env("AIRPORT_LON", "13.5033"))
    runway_course: float = float(_env("RUNWAY_COURSE", "249"))
    airport_alt_m: float = float(_env("AIRPORT_ALT_M", "48"))
    # Nachtflugverbot am Flughafen (BER: 0-5 Uhr keine Starts und Landungen)
    curfew_hours: tuple[int, int] = field(default_factory=lambda: _range(_env("CURFEW_HOURS", "00:00-05:00")))

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

    # Datenquellen. SOURCE=adsblol: Community-Netz ohne Account und Tageslimit,
    # bei Fehlern springt OpenSky ein. SOURCE=opensky: nur OpenSky.
    mock: bool = _on("MOCK", "0")
    source: str = _env("SOURCE", "adsblol").lower()
    # Routen: adsb.im zuerst (ADSBIM=0 schaltet ab), dann adsbdb; ohne Route der
    # Abflughafen aus der OpenSky-Flugspur (TRACK_ORIGIN=0 schaltet ab).
    route_adsbim: bool = _env("ADSBIM", "1") in ("1", "true", "yes")
    track_origin: bool = _env("TRACK_ORIGIN", "1") in ("1", "true", "yes")
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
    logo_size: int = int(_env("LOGO_SIZE", "32"))

    # Leerlauf: Wenn kein Flug zu zeigen ist, wechseln Karten (Uhr, nächster Flieger,
    # Radar, Statistik, Wetter, ISS, Witze ...) alle IDLE_SECONDS. IDLE_OFF: Karten-IDs,
    # kommagetrennt, die nie kommen sollen (Liste unter /idle).
    idle_seconds: int = int(_env("IDLE_SECONDS", "12"))
    idle_off: str = _env("IDLE_OFF", "")
    idle_clock_share: float = float(_env("IDLE_CLOCK_SHARE", "0.35"))
    metar: bool = _on("METAR", "1")                 # Wetter von aviationweather.gov
    metar_station: str = _env("METAR_STATION", "EDDB").upper()
    iss: bool = _on("ISS", "1")                     # ISS-Bahndaten von Celestrak

    # Logos: automatischer Abruf beim ersten Auftauchen einer Airline. LOGO_URLS:
    # kommagetrennte Vorlagen mit {iata} oder {icao}, optional "|2.0" für ein eigenes
    # Seitenverhältnis, werden der Reihe nach probiert. Leer = logos.DEFAULT_URLS.
    # Nur annähernd quadratische Logos (bis LOGO_MAX_ASPECT) werden genommen.
    logo_fetch: bool = _env("LOGO_FETCH", "1") in ("1", "true", "yes")
    logo_urls: str = _env("LOGO_URLS", _env("LOGO_URL", ""))
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
    def fetch_radius_km(self) -> float:
        return max(self.radius_km, self.scan_radius_km)

    @property
    def idle_disabled(self) -> set[str]:
        return {x.strip().lower() for x in self.idle_off.split(",") if x.strip()}

    @property
    def view_span(self) -> float:
        return (self.view_to - self.view_from) % 360 or 360

    @property
    def view_center(self) -> float:
        return (self.view_from + self.view_span / 2) % 360

    @property
    def bbox(self) -> tuple[float, float, float, float]:
        """(lamin, lomin, lamax, lomax) um Home, grob aus Radius abgeleitet."""
        import math
        r = self.fetch_radius_km
        dlat = r / 111.0
        dlon = r / (111.0 * max(0.2, math.cos(math.radians(self.home_lat))))
        return (self.home_lat - dlat, self.home_lon - dlon, self.home_lat + dlat, self.home_lon + dlon)


CFG = Config()
