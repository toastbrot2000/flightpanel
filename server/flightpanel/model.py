"""Die Daten, die durch den Server fließen:

  State         ein Flugzeug, wie eine Quelle es meldet (adsblol.py, opensky.py)
  Flight        ein Flug, wie das Panel ihn zeigt
  DisplayModel  was gerade zu zeigen ist; build.py baut es bei jedem Abruf neu

Status eines Flugs: ARR landet am Heimatflughafen, DEP startet dort, OVF alles andere."""

import json
import time
from dataclasses import asdict, dataclass, field

ARR, DEP, OVF = "ARR", "DEP", "OVF"


@dataclass
class State:
    icao24: str
    callsign: str
    lon: float
    lat: float
    baro_alt_m: float | None
    on_ground: bool
    velocity_ms: float | None
    track_deg: float | None
    vrate_ms: float | None
    geo_alt_m: float | None = None
    actype: str = ""        # ICAO-Typ, nur von adsb.lol
    category: str = ""      # ADS-B-Kategorie, nur von adsb.lol (A7 = Hubschrauber)
    squawk: str = ""        # Transpondercode, 7700/7600/7500 = Notfall
    registration: str = ""  # Kennzeichen, nur von adsb.lol

    @property
    def alt_m(self) -> float | None:
        """Höhe für Sichtlinie und Anflug: geometrisch, sonst barometrisch."""
        return self.geo_alt_m if self.geo_alt_m is not None else self.baro_alt_m


@dataclass
class Flight:
    icao24: str
    callsign: str
    status: str                 # ARR | DEP | OVF
    dist_km: float
    bearing: float
    alt_ft: int | None
    speed_kt: int | None
    track: int | None
    vrate_fpm: int | None
    origin: str = ""            # IATA
    dest: str = ""              # IATA
    airline: str = ""           # Anzeigename
    airline_icao: str = ""      # z. B. DLH
    airline_iata: str = ""      # z. B. LH, für Logo-Abruf und -Datei
    actype: str = ""            # ICAO-Typ, z. B. A320
    kind: str = "airline"       # airline | other (privat, Hubschrauber, Polizei, Militär)
    visible: bool = True        # im Sichtfeld, jetzt oder bis zum nächsten Abruf
    elev_deg: float | None = None  # Höhenwinkel über dem Horizont
    seq: int | None = None      # Platz in der Landefolge (1 = landet als nächstes)
    label: str = ""             # ohne Airline: was es ist (Luftrettung, Kleinflugzeug ...)
    land_eta_s: int | None = None  # Landende: geschätzte Sekunden bis zum Aufsetzen

    @property
    def other_airport(self) -> str:
        """Der andere Flughafen als der eigene (bei Überflug das Ziel)."""
        if self.status == ARR:
            return self.origin or "---"
        if self.status == DEP:
            return self.dest or "---"
        return self.dest or self.origin or "---"


@dataclass
class DisplayModel:
    flights: list[Flight] = field(default_factory=list)
    generated_at: float = field(default_factory=time.time)
    source: str = "none"                               # woher die Daten kamen: adsblol, opensky, mock ...
    stale: bool = False
    empty: bool = False
    nearby: list[dict] = field(default_factory=list)   # alle im Radius, zum Eichen des Sichtfelds
    landing_course: float | None = None                # erkannte Landerichtung (Kurs im Endanflug)
    departures: list[dict] = field(default_factory=list)  # wer gerade am Platz startet (Karte "departures")
    arrivals: list[dict] = field(default_factory=list)    # Landefolge mit Herkunft und Minuten ("arrivals")
    last_airport_move: float = 0.0                     # letzter Start oder letzte Landung (setzt main)
    course_votes: int = 0                              # so viele Flugzeuge bestätigen sie gerade
    upcoming: Flight | None = None                     # kommt als nächstes ins Sichtfeld
    upcoming_eta_s: float | None = None                # in so vielen Sekunden (ab generated_at)
    upcoming_az: float | None = None                   # Richtung, in der es auftaucht
    upcoming_via: str = ""                             # "approach" (Endanflug) | "track" (geradeaus)
    alert: dict | None = None                          # Notfall-Squawk in der Nähe

    def to_json(self) -> str:
        d = asdict(self)
        for f in d["flights"]:
            f["other_airport"] = f.get("dest") if f["status"] != ARR else f.get("origin")
        return json.dumps(d, ensure_ascii=False, indent=2)
