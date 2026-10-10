"""Aus Rohdaten (OpenSky-States + adsbdb) das Display-Modell bauen:
filtern, anreichern, klassifizieren, sortieren.

Reihenfolge: Was im Sichtfeld ist (jetzt oder bis zum nächsten Abruf), vor dem
Rest; Airlines vor Privatflügen, Hubschraubern und Militär; dann die kürzeste
Sichtlinie."""

import json
import logging
import math
import os
import random
import time
from dataclasses import asdict, dataclass, field

from .adsbdb import AdsbDb
from .config import Config
from .geo import bearing_deg, destination, elevation_deg, haversine_km
from .opensky import State

log = logging.getLogger("model")

ARR, DEP, OVF = "ARR", "DEP", "OVF"
SHORTLIST = 8          # so viele Kandidaten werden per adsbdb als Airline oder nicht eingeordnet
FINAL_KM = 18          # Länge des Endanflugs: Gegenanflüge drehen ungefähr hier ein


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
    nearby: list[dict] = field(default_factory=list)   # alle im Radius, zum Eichen des Sichtfelds
    landing_course: float | None = None                # erkannte Landerichtung (Kurs im Endanflug)

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


def classify(s: State, origin: str, dest: str, local: str, guess: bool = True) -> str:
    """Ankunft, Abflug oder Überflug. Ohne Route raten aus Höhe und Steigrate,
    aber nur bei Airlines (guess): Ein landender Rettungshubschrauber fliegt
    zur Klinik, nicht nach BER."""
    if dest and dest == local:
        return ARR
    if origin and origin == local:
        return DEP
    if not guess:
        return OVF
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
        out[k.upper()] = {"name": v.get("name", ""), "iata": (v.get("iata") or "").upper(),
                          "airline": bool(v.get("airline", True))}
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


def route_plausible(route: dict, lat: float, lon: float) -> bool:
    """adsbdb kennt Routen pro Callsign, und Airlines verwenden Callsigns für
    andere Strecken weiter. Liegt das Flugzeug weit neben der Strecke
    (Umweg über seine Position > 20 % + 100 km), ist die Route veraltet.
    Ohne Koordinaten (alte Cache-Einträge) gilt sie als plausibel."""
    o, d = route.get("origin_pos"), route.get("dest_pos")
    if not (o and d):
        return True
    direct = haversine_km(o[0], o[1], d[0], d[1])
    via = haversine_km(o[0], o[1], lat, lon) + haversine_km(lat, lon, d[0], d[1])
    return via <= direct * 1.2 + 100


def callsign_airline(callsign: str) -> str:
    """ICAO-Airlinecode aus dem Callsign: drei Buchstaben, dann eine Ziffer
    (EZY45KZ, DLH2041). Kennzeichen wie DEABC oder N123AB sind keine Airline."""
    cs = callsign or ""
    if len(cs) >= 4 and cs[:3].isalpha() and cs[3].isdigit():
        return cs[:3]
    return ""


# ADS-B-Kategorien, die nie eine Airline sind: leicht (A1), Hubschrauber (A7),
# Segler, Ballon, Fallschirm, Ultraleicht, Drohne, Raumfahrt (B1-B7)
NON_AIRLINE_CATEGORIES = {"A1", "A7", "B1", "B2", "B3", "B4", "B6", "B7"}
# Groß und schwer (A320/737 aufwärts): Businessjets sind A1/A2
AIRLINER_CATEGORIES = {"A3", "A4", "A5", "A6"}


def is_airline(callsign: str, route: dict, names: dict[str, dict], category: str = "") -> bool:
    """Linien-, Charter- und Frachtflüge. Private Flüge haben kein Airline-Callsign
    (DEABC), Rettung, Polizei und Militär stehen in airlines.json mit
    "airline": false (CHX31 = Christoph 31), Hubschrauber und Leichtflugzeuge
    erkennt adsb.lol an der Kategorie. Unbekannte Betreiber zählen, wenn adsbdb
    eine Route mit Airline kennt oder das Flugzeug groß ist (SVI8222, ein
    ägyptischer Charter, A3); BFX1073, ein Businessjet ohne Route, zählt nicht."""
    code = callsign_airline(callsign)
    if not code or category in NON_AIRLINE_CATEGORIES:
        return False
    if code in names:
        return names[code]["airline"]
    return bool(route.get("airline_icao")) or category in AIRLINER_CATEGORIES


@dataclass
class Sighting:
    """Wo ein Flugzeug vom Standort aus zu sehen ist."""
    az: float          # Richtung, 0 = Nord, 90 = Ost
    elev: float        # Höhenwinkel über dem Horizont
    dist_km: float     # Entfernung über Grund
    slant_km: float    # Sichtlinie


def sighting(cfg: Config, lat: float, lon: float, alt_m: float | None) -> Sighting:
    d = haversine_km(cfg.home_lat, cfg.home_lon, lat, lon)
    h = (alt_m if alt_m is not None else 0.0) - cfg.home_alt_m
    return Sighting(az=bearing_deg(cfg.home_lat, cfg.home_lon, lat, lon), elev=elevation_deg(d, h),
                    dist_km=d, slant_km=math.hypot(d, h / 1000))


def in_view(cfg: Config, v: Sighting) -> bool:
    span = (cfg.view_to - cfg.view_from) % 360 or 360
    return ((v.az - cfg.view_from) % 360 <= span
            and cfg.view_min_elev <= v.elev <= cfg.view_max_elev
            and v.slant_km <= cfg.view_max_km)


def visibility(cfg: Config, s: State, horizon_s: float) -> int:
    """2 = jetzt im Sichtfeld, 1 = kommt bis zum nächsten Abruf hinein, 0 = nicht.
    Die Vorschau rechnet Geschwindigkeit, Kurs und Steigrate weiter: Anflüge
    queren das Sichtfeld in wenigen Minuten, so lang wie ein anonymer Abruf."""
    alt = s.geo_alt_m if s.geo_alt_m is not None else s.baro_alt_m
    if in_view(cfg, sighting(cfg, s.lat, s.lon, alt)):
        return 2
    if s.velocity_ms and s.track_deg is not None and alt is not None and horizon_s > 0:
        for i in (1, 2, 3):
            dt = horizon_s * i / 3
            lat, lon = destination(s.lat, s.lon, s.track_deg, s.velocity_ms * dt / 1000)
            if in_view(cfg, sighting(cfg, lat, lon, max(0.0, alt + (s.vrate_ms or 0.0) * dt))):
                return 1
    return 0


def _angle(a: float, b: float) -> float:
    return abs((a - b + 180) % 360 - 180)


def _alt(s: State) -> float | None:
    return s.geo_alt_m if s.geo_alt_m is not None else s.baro_alt_m


def landing_course(cfg: Config, states: list[State], prev: float) -> float:
    """Landerichtung aus dem Verkehr: Was tief in Platznähe fliegt, landet oder
    startet gegen den Wind, also parallel zur Piste. Mehrheit entscheidet, ohne
    Verkehr bleibt die letzte."""
    votes = {cfg.runway_course % 360: 0, (cfg.runway_course + 180) % 360: 0}
    for s in states:
        alt = _alt(s)
        if s.on_ground or s.track_deg is None or alt is None or alt > 1500:
            continue
        if haversine_km(cfg.airport_lat, cfg.airport_lon, s.lat, s.lon) > 20:
            continue
        for c in votes:
            if _angle(s.track_deg, c) < 30:
                votes[c] += 1
    best = max(votes, key=votes.get)
    return best if votes[best] else prev


def remaining_km(cfg: Config, s: State, course: float) -> float:
    """Restliche Flugstrecke bis zum Aufsetzen, nach Flugphase geschätzt:
    - Endanflug (Kurs zur Piste, auf der Anflugseite): Entfernung zur Piste.
    - Gegenanflug (Kurs entgegen) oder schon an der Piste vorbei: weiter bis
      querab vom Beginn des Endanflugs, eindrehen, dann den ganzen Endanflug.
    - Sonst (Queranflug, Einkurven): quer zur Achse, dann den Rest der Achse."""
    x = (s.lon - cfg.airport_lon) * 111.32 * math.cos(math.radians(cfg.airport_lat))
    y = (s.lat - cfg.airport_lat) * 110.57
    ux, uy = math.sin(math.radians(course)), math.cos(math.radians(course))
    along = -(x * ux + y * uy)          # vor der Piste auf der Anflugseite: positiv
    cross = abs(x * uy - y * ux)        # seitlich der verlängerten Pistenachse
    trk = s.track_deg if s.track_deg is not None else course
    if _angle(trk, course) < 30 and along > 0:
        return math.hypot(along, cross)
    if _angle(trk, course + 180) < 45 or along < 6:
        turn = max(along, FINAL_KM)
        return (turn - along) + cross + turn
    return cross + along


def is_arrival(cfg: Config, s: State, route: dict) -> bool:
    """Landet am eigenen Flughafen: laut Route, sonst tief, nicht steigend, in Platznähe."""
    if route.get("dest") == cfg.local_airport_iata:
        return True
    if route.get("origin") == cfg.local_airport_iata:
        return False
    alt = _alt(s)
    return (alt is not None and alt < 3000 and (s.vrate_ms or 0.0) < 1.0
            and haversine_km(cfg.airport_lat, cfg.airport_lon, s.lat, s.lon) < 45)


# Landen nicht auf der Piste: Hubschrauber, Segler, Ballon, Fallschirm, UL, Drohne, Raumfahrt
NOT_RUNWAY_CATEGORIES = {"A7", "B1", "B2", "B3", "B4", "B6", "B7"}


async def landing_sequence(cfg: Config, states: list[State], adsb: AdsbDb, names: dict[str, dict],
                           course: float) -> dict[str, int]:
    """icao24 -> Platz in der Landefolge, nach geschätzter Zeit bis zum Aufsetzen.
    Zählt alles, was die Piste braucht, auch Businessjets (BFX1073), damit #2 der
    zweite Flieger am Himmel ist. Nicht: Kennzeichen-Callsigns (Privatflieger landen
    meist woanders), Rettung und Polizei, Hubschrauber."""
    eta: list[tuple[float, str]] = []
    for s in states:
        alt = _alt(s)
        code = callsign_airline(s.callsign)
        if s.on_ground or alt is None or alt > 3000 or not code or s.category in NOT_RUNWAY_CATEGORIES:
            continue
        if code in names and not names[code]["airline"]:
            continue
        if haversine_km(cfg.airport_lat, cfg.airport_lon, s.lat, s.lon) > 45:
            continue
        route = await adsb.route(s.callsign)
        if not route_plausible(route, s.lat, s.lon):
            route = {}
        if not is_arrival(cfg, s, route):
            continue
        eta.append((remaining_km(cfg, s, course) * 1000 / max(s.velocity_ms or 0.0, 60.0), s.icao24))
    return {icao: i + 1 for i, (_, icao) in enumerate(sorted(eta))}


async def build_model(cfg: Config, states: list[State], adsb: AdsbDb, names: dict[str, dict],
                      horizon_s: float = 0, prev_course: float | None = None) -> DisplayModel:
    """horizon_s: Zeit bis zum nächsten Abruf, für die Sichtfeld-Vorschau.
    prev_course: Landerichtung vom letzten Abruf, falls gerade niemand landet."""
    cands: list[tuple[State, Sighting, int]] = []
    for s in states:
        if s.on_ground:
            continue
        if s.baro_alt_m is not None and s.baro_alt_m > cfg.max_alt_m:
            continue
        v = sighting(cfg, s.lat, s.lon, s.geo_alt_m if s.geo_alt_m is not None else s.baro_alt_m)
        if v.dist_km > cfg.radius_km:
            continue
        cands.append((s, v, visibility(cfg, s, horizon_s)))
    cands.sort(key=lambda c: (c[2] == 0, c[1].slant_km))
    pool = [c for c in cands if c[2] > 0] if cfg.view_only else cands

    # Airline oder nicht braucht die Route (adsbdb, gecacht), deshalb nur für die vordersten.
    ranked = []
    for s, v, vis in pool[:SHORTLIST]:
        route = await adsb.route(s.callsign)
        airline = is_airline(s.callsign, route, names, s.category)
        if airline or cfg.show_other:
            ranked.append((s, v, vis, route, airline))
    # Sichtbar vor unsichtbar, Airline vor Sonstigen, jetzt vor gleich, dann die Sichtlinie
    ranked.sort(key=lambda r: (r[2] == 0, not r[4], -r[2], r[1].slant_km))
    # Sonstige nur, wenn keine Airline dran ist: Ist eine im Sichtfeld (oder, ohne
    # Sichtbares, überhaupt eine da), fliegen Privatjets und Hubschrauber raus.
    tier = [r for r in ranked if r[2] > 0] or ranked
    if any(r[4] for r in tier):
        tier = [r for r in tier if r[4]]

    course = landing_course(cfg, states, cfg.runway_course if prev_course is None else prev_course)
    seq = await landing_sequence(cfg, states, adsb, names, course)

    flights: list[Flight] = []
    for s, v, vis, route, airline in tier[: cfg.max_flights]:
        ac = await adsb.aircraft(s.icao24)
        origin, dest = route.get("origin", ""), route.get("dest", "")
        if not route_plausible(route, s.lat, s.lon):
            log.info("Route %s %s-%s passt nicht zur Position, verworfen", s.callsign, origin, dest)
            origin = dest = ""
        # Betreiber auch ohne Ziffer im Callsign (BPOBER -> Bundespolizei), nur für den Namen
        airline_icao = (route.get("airline_icao") or callsign_airline(s.callsign)
                        or (s.callsign[:3] if s.callsign[:3] in names else ""))
        # Logo-Code: airlines.json zuerst (Marke statt Tochter-AOC, z. B. Malta Air -> FR;
        # leer = Logo per ICAO, wenn der IATA-Code doppelt vergeben ist), sonst adsbdb.
        if not airline:
            airline_iata = ""
        elif airline_icao in names:
            airline_iata = names[airline_icao]["iata"]
        else:
            airline_iata = route.get("airline_iata", "")
        flights.append(Flight(
            icao24=s.icao24,
            callsign=s.callsign or s.icao24.upper(),
            status=classify(s, origin, dest, cfg.local_airport_iata, guess=airline),
            dist_km=round(v.dist_km, 1),
            bearing=round(v.az),
            alt_ft=int(s.baro_alt_m * 3.28084) if s.baro_alt_m is not None else None,
            speed_kt=int(s.velocity_ms * 1.94384) if s.velocity_ms is not None else None,
            track=int(s.track_deg) if s.track_deg is not None else None,
            vrate_fpm=int(s.vrate_ms * 196.85) if s.vrate_ms is not None else None,
            origin=origin, dest=dest,
            airline=short_airline(route.get("airline", "") or ac.get("operator", ""), airline_icao, names),
            airline_icao=airline_icao,
            airline_iata=airline_iata,
            actype=s.actype or ac.get("type", ""),
            kind="airline" if airline else "other",
            visible=vis > 0,
            elev_deg=round(v.elev, 1),
            seq=seq.get(s.icao24),
        ))
    for f in flights:
        if f.seq is not None and f.status != ARR:
            f.status = ARR          # in der Landefolge, auch wenn die Route nichts sagt
    # Landende in Landefolge zeigen, damit #1 zuerst kommt
    flights.sort(key=lambda f: (f.seq is None, f.seq or 0))
    nearby = [{"callsign": s.callsign or s.icao24.upper(), "icao24": s.icao24, "type": s.actype, "az": round(v.az),
               "elev": round(v.elev, 1), "dist_km": round(v.dist_km, 1),
               "alt_ft": int(s.baro_alt_m * 3.28084) if s.baro_alt_m is not None else None,
               "in_view": ("no", "soon", "now")[vis], "seq": seq.get(s.icao24)} for s, v, vis in cands]
    return DisplayModel(flights=flights, source="opensky", empty=not flights, nearby=nearby,
                        landing_course=course)


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
            icao24=f"3c{seed % 0xFFF:03x}{i}", callsign=cs, status=st,
            dist_km=round(4 + i * 9 + rnd.random() * 6, 1), bearing=rnd.randint(0, 359),
            alt_ft=alt, speed_kt=rnd.randint(160, 460), track=rnd.randint(0, 359),
            vrate_fpm=(-rnd.randint(500, 1500) if st == ARR else rnd.randint(500, 2500) if st == DEP else 0),
            origin=o, dest=d, airline=al, airline_icao=al_icao, airline_iata=al_iata, actype=typ,
        ))
    for n, f in enumerate(sorted((f for f in flights if f.status == ARR), key=lambda f: f.dist_km)):
        f.seq = n + 1
    nearby = [{"callsign": f.callsign, "icao24": f.icao24, "az": f.bearing, "in_view": "now", "seq": f.seq}
              for f in flights]
    return DisplayModel(flights=flights, source="mock", empty=not flights, nearby=nearby)
