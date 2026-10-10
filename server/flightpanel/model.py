"""Aus Rohdaten (OpenSky-States + adsbdb) das Display-Modell bauen:
filtern, anreichern, klassifizieren, sortieren."""

import json
import logging
import os
import random
import time
from dataclasses import asdict, dataclass, field

from .adsbdb import AdsbDb
from .config import Config
from .geo import bearing_deg, haversine_km
from .opensky import State

log = logging.getLogger("model")

ARR, DEP, OVF = "ARR", "DEP", "OVF"


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

    @property
    def other_airport(self) -> str:
        """Der Flughafen, der nicht BER ist (bei Überflug das Ziel)."""
        if self.status == ARR:
            return self.origin or "---"
        if self.status == DEP:
            return self.dest or "---"
        return self.dest or self.origin or "---"


@dataclass
class DisplayModel:
    flights: list[Flight] = field(default_factory=list)
    generated_at: float = field(default_factory=time.time)
    source: str = "none"
    stale: bool = False
    empty: bool = False

    def to_json(self) -> str:
        d = asdict(self)
        for f in d["flights"]:
            f["other_airport"] = f.get("dest") if f["status"] != ARR else f.get("origin")
        return json.dumps(d, ensure_ascii=False, indent=2)

    def signature(self) -> tuple:
        """Alles, was eine Neu-Renderung erfordert."""
        return (self.stale, self.empty, tuple(
            (f.callsign, f.status, f.alt_ft, f.speed_kt, f.origin, f.dest, f.airline, f.airline_iata, f.actype, round(f.dist_km))
            for f in self.flights))


def classify(s: State, origin: str, dest: str, local: str) -> str:
    if dest and dest == local:
        return ARR
    if origin and origin == local:
        return DEP
    alt = s.baro_alt_m or 99999
    vr = s.vrate_ms or 0.0
    if alt < 3000 and vr < -2:
        return ARR
    if alt < 3000 and vr > 2:
        return DEP
    return OVF


def load_airlines(data_dir: str) -> dict[str, dict]:
    """data/airlines.json: {"DLH": {"name": "Lufthansa", "iata": "LH"}, ...}
    (alte Form "DLH": "Lufthansa" geht auch). Kurze Anzeigenamen statt der
    langen adsbdb-Firmennamen, plus IATA-Code als Fallback für das Logo."""
    path = os.path.join(data_dir, "airlines.json")
    try:
        with open(path, encoding="utf-8") as f:
            raw = json.load(f)
    except FileNotFoundError:
        return {}
    except Exception as e:
        log.warning("airlines.json nicht lesbar: %s", e)
        return {}
    out = {}
    for k, v in raw.items():
        if k.startswith("_"):
            continue
        if isinstance(v, str):
            v = {"name": v, "iata": ""}
        out[k.upper()] = {"name": v.get("name", ""), "iata": (v.get("iata") or "").upper()}
    return out


def short_airline(name: str, icao: str, names: dict[str, dict]) -> str:
    if icao in names and names[icao]["name"]:
        return names[icao]["name"]
    if not name:
        return ""
    # Lange Firmennamen eindampfen: "Deutsche Lufthansa AG" -> "Lufthansa"
    stop = {"AG", "GMBH", "LTD", "LIMITED", "PLC", "SA", "S.A.", "INC", "LLC", "CO", "COMPANY",
            "AIRLINES", "AIRLINE", "AIRWAYS", "DEUTSCHE", "THE", "GROUP", "EUROPE", "UK"}
    words = [w for w in name.replace(",", " ").split() if w.upper().strip(".") not in stop]
    short = " ".join(words) if words else name
    return short[:12]


async def build_model(cfg: Config, states: list[State], adsb: AdsbDb, names: dict[str, dict]) -> DisplayModel:
    cands: list[tuple[float, State]] = []
    for s in states:
        if s.on_ground:
            continue
        if s.baro_alt_m is not None and s.baro_alt_m > cfg.max_alt_m:
            continue
        d = haversine_km(cfg.home_lat, cfg.home_lon, s.lat, s.lon)
        if d > cfg.radius_km:
            continue
        cands.append((d, s))
    cands.sort(key=lambda t: t[0])

    flights: list[Flight] = []
    for d, s in cands[: cfg.max_flights]:
        route = await adsb.route(s.callsign)
        ac = await adsb.aircraft(s.icao24)
        origin, dest = route.get("origin", ""), route.get("dest", "")
        airline_icao = route.get("airline_icao") or (s.callsign[:3] if s.callsign[:3].isalpha() else "")
        airline_iata = route.get("airline_iata") or names.get(airline_icao, {}).get("iata", "")
        flights.append(Flight(
            icao24=s.icao24,
            callsign=s.callsign or s.icao24.upper(),
            status=classify(s, origin, dest, cfg.local_airport_iata),
            dist_km=round(d, 1),
            bearing=round(bearing_deg(cfg.home_lat, cfg.home_lon, s.lat, s.lon)),
            alt_ft=int(s.baro_alt_m * 3.28084) if s.baro_alt_m is not None else None,
            speed_kt=int(s.velocity_ms * 1.94384) if s.velocity_ms is not None else None,
            track=int(s.track_deg) if s.track_deg is not None else None,
            vrate_fpm=int(s.vrate_ms * 196.85) if s.vrate_ms is not None else None,
            origin=origin, dest=dest,
            airline=short_airline(route.get("airline", "") or ac.get("operator", ""), airline_icao, names),
            airline_icao=airline_icao,
            airline_iata=airline_iata,
            actype=ac.get("type", ""),
        ))
    return DisplayModel(flights=flights, source="opensky", empty=not flights)


# ---------------------------------------------------------------- Mock -----
_MOCK_POOL = [
    ("EZY45KZ", "EZY", "U2", "easyJet", "LGW", "BER", "A320", ARR),
    ("DLH2041", "DLH", "LH", "Lufthansa", "BER", "LIS", "A321", DEP),
    ("RYR8DK", "RYR", "FR", "Ryanair", "DUB", "BER", "B738", ARR),
    ("EWG7PL", "EWG", "EW", "Eurowings", "BER", "PMI", "A319", DEP),
    ("KLM1823", "KLM", "KL", "KLM", "AMS", "BER", "E190", ARR),
    ("THY1725", "THY", "TK", "Turkish", "IST", "BER", "A333", ARR),
    ("UAE59", "UAE", "EK", "Emirates", "DXB", "WAW", "B77W", OVF),
    ("WZZ3RU", "WZZ", "W6", "Wizz Air", "BER", "BUD", "A21N", DEP),
]


def mock_model(cfg: Config, seed: int) -> DisplayModel:
    """Wechselnde Fantasieflüge, damit das Layout ohne API-Zugang entworfen
    werden kann. Der Seed wechselt alle 20 s."""
    rnd = random.Random(seed)
    n = rnd.choice([0, 1, 2, 2, 2]) if cfg.max_flights >= 2 else 1
    picks = rnd.sample(_MOCK_POOL, k=min(n, len(_MOCK_POOL)))
    flights = []
    for i, (cs, al_icao, al_iata, al, o, d, typ, st) in enumerate(picks):
        alt = rnd.randint(1800, 9500) if st != OVF else rnd.randint(30000, 39000)
        flights.append(Flight(
            icao24=f"3c{seed % 0xFFFF:04x}", callsign=cs, status=st,
            dist_km=round(4 + i * 9 + rnd.random() * 6, 1), bearing=rnd.randint(0, 359),
            alt_ft=alt, speed_kt=rnd.randint(160, 460), track=rnd.randint(0, 359),
            vrate_fpm=(-rnd.randint(500, 1500) if st == ARR else rnd.randint(500, 2500) if st == DEP else 0),
            origin=o, dest=d, airline=al, airline_icao=al_icao, airline_iata=al_iata, actype=typ,
        ))
    return DisplayModel(flights=flights, source="mock", empty=not flights)
