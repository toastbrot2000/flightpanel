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
from .issues import Issues, names_match
from .opensky import State

log = logging.getLogger("model")

ARR, DEP, OVF = "ARR", "DEP", "OVF"
SHORTLIST = 8          # so viele Kandidaten werden per adsbdb als Airline oder nicht eingeordnet
FINAL_KM = 18          # Länge des Endanflugs: Gegenanflüge drehen ungefähr hier ein
NEXT_HORIZON_S = 600   # so weit voraus sucht der Leerlauf den nächsten Flieger
NEXT_STEP_S = 15
GLIDE_DEG = 3.0        # Gleitpfad im Endanflug
CLIMB = 0.10           # Steiggradient nach dem Start (m pro m)
DEPARTURE_KM = 12      # so weit fliegen Abflüge ungefähr geradeaus
EMERGENCY = {"7700": "NOTFALL", "7600": "FUNKAUSFALL", "7500": "ENTFÜHRUNG"}


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


def entry_eta(cfg: Config, s: State, horizon_s: float, step_s: float) -> tuple[float, float] | None:
    """(Sekunden, Richtung), wann und wo das Flugzeug ins Sichtfeld kommt, wenn es
    mit Geschwindigkeit, Kurs und Steigrate weiterfliegt; (0, jetzt) wenn es schon
    drin ist, None wenn nicht innerhalb von horizon_s."""
    alt = s.geo_alt_m if s.geo_alt_m is not None else s.baro_alt_m
    v = sighting(cfg, s.lat, s.lon, alt)
    if in_view(cfg, v):
        return 0.0, v.az
    if not (s.velocity_ms and s.track_deg is not None and alt is not None and horizon_s > 0 and step_s > 0):
        return None
    n = max(1, math.ceil(horizon_s / step_s - 1e-9))
    for i in range(1, n + 1):
        dt = min(horizon_s, step_s * i)
        lat, lon = destination(s.lat, s.lon, s.track_deg, s.velocity_ms * dt / 1000)
        w = sighting(cfg, lat, lon, max(0.0, alt + (s.vrate_ms or 0.0) * dt))
        if in_view(cfg, w):
            return dt, w.az
    return None


def visibility(cfg: Config, s: State, horizon_s: float) -> int:
    """2 = jetzt im Sichtfeld, 1 = kommt bis zum nächsten Abruf hinein, 0 = nicht.
    Die Vorschau rechnet Geschwindigkeit, Kurs und Steigrate weiter: Anflüge
    queren das Sichtfeld in wenigen Minuten, so lang wie ein anonymer Abruf."""
    e = entry_eta(cfg, s, horizon_s, horizon_s / 3)
    if e is None:
        return 0
    return 2 if e[0] == 0 else 1


def _angle(a: float, b: float) -> float:
    return abs((a - b + 180) % 360 - 180)


def _alt(s: State) -> float | None:
    return s.geo_alt_m if s.geo_alt_m is not None else s.baro_alt_m


def landing_course(cfg: Config, states: list[State], prev: float) -> float:
    return landing_course_votes(cfg, states, prev)[0]


def landing_course_votes(cfg: Config, states: list[State], prev: float) -> tuple[float, int]:
    """Landerichtung aus dem Verkehr: Was tief in Platznähe fliegt, landet oder
    startet gegen den Wind, also parallel zur Piste. Mehrheit entscheidet, ohne
    Verkehr bleibt die letzte. Dazu die Stimmen für die gewählte Richtung."""
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
    return (best, votes[best]) if votes[best] else (prev, 0)


def runway_name(course: float) -> str:
    """249° -> "25", 69° -> "07"."""
    n = round((course % 360) / 10) % 36 or 36
    return f"{n:02d}"


_PATH_CACHE: dict[tuple, list[tuple[float, float, float]]] = {}


def path_in_view(cfg: Config, course: float, kind: str) -> list[tuple[float, float, float]]:
    """Punkte der verlängerten Pistenachse, die im Sichtfeld liegen, als
    (km ab Flughafen, Richtung, Höhenwinkel). kind "approach": Endanflug auf dem
    3°-Gleitpfad gegen den Kurs, "departure": geradeaus steigend mit dem Kurs."""
    key = (kind, round(course), cfg.view_from, cfg.view_to, cfg.view_min_elev, cfg.view_max_elev,
           cfg.view_max_km, cfg.home_lat, cfg.home_lon, cfg.home_alt_m, cfg.airport_lat, cfg.airport_lon)
    hit = _PATH_CACHE.get(key)
    if hit is not None:
        return hit
    out = []
    far = FINAL_KM + 4 if kind == "approach" else DEPARTURE_KM
    brg = course + 180 if kind == "approach" else course
    grad = math.tan(math.radians(GLIDE_DEG)) if kind == "approach" else CLIMB
    d = 0.5
    while d <= far:
        lat, lon = destination(cfg.airport_lat, cfg.airport_lon, brg, d)
        v = sighting(cfg, lat, lon, cfg.airport_alt_m + d * 1000 * grad)
        if in_view(cfg, v):
            out.append((d, v.az, v.elev))
        d += 0.5
    _PATH_CACHE[key] = out
    return out


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


def predict_entry(cfg: Config, s: State, course: float, in_seq: bool) -> tuple[float, float, str] | None:
    """(Sekunden, Richtung, Art) bis das Flugzeug ins Sichtfeld kommt, über die
    nächsten NEXT_HORIZON_S. Landende fliegen nicht geradeaus, sondern drehen in
    den Endanflug: Liegt der im Sichtfeld, zählt die Reststrecke bis dorthin."""
    e = entry_eta(cfg, s, NEXT_HORIZON_S, NEXT_STEP_S)
    best = (e[0], e[1], "track") if e is not None else None
    if best is not None and best[0] == 0:
        return best
    if in_seq:
        path = path_in_view(cfg, course, "approach")
        if path:
            far_km, az, _ = max(path)
            rem = remaining_km(cfg, s, course)
            if rem > far_km:
                t = (rem - far_km) * 1000 / max(s.velocity_ms or 0.0, 60.0)
                if t <= NEXT_HORIZON_S and (best is None or t < best[0]):
                    best = (t, az, "approach")
    return best


def find_alert(cfg: Config, states: list[State], names: dict[str, dict]) -> dict | None:
    """Das nächste Flugzeug in der Luft mit Notfall-Squawk (7700, 7600, 7500)."""
    hits = []
    for s in states:
        if s.on_ground or s.squawk not in EMERGENCY:
            continue
        v = sighting(cfg, s.lat, s.lon, _alt(s))
        if v.dist_km > cfg.fetch_radius_km:
            continue
        code = callsign_airline(s.callsign)
        hits.append((v.dist_km, {
            "callsign": s.callsign or s.icao24.upper(), "icao24": s.icao24, "squawk": s.squawk,
            "meaning": EMERGENCY[s.squawk], "type": s.actype,
            "airline": names[code]["name"] if code in names else "",
            "dist_km": round(v.dist_km, 1), "az": round(v.az), "elev": round(v.elev, 1),
            "alt_ft": int(s.baro_alt_m * 3.28084) if s.baro_alt_m is not None else None,
            "in_view": in_view(cfg, v)}))
    return min(hits, key=lambda h: h[0])[1] if hits else None


def note_operator(issues: Issues, s: State, route: dict, names: dict[str, dict], airline: bool) -> None:
    """Unbekannte Betreiber und Namen, die nicht zu adsbdb passen: Kandidaten fürs
    Nachpflegen von airlines.json."""
    code = callsign_airline(s.callsign)
    if not code:
        return
    if code not in names:
        issues.note("unknown_operator", code, s.callsign, adsbdb=route.get("airline", ""),
                    category=s.category, type=s.actype, counted_as="airline" if airline else "other")
    elif (route.get("airline") and route.get("airline_icao") == code
          and not names_match(names[code]["name"], route["airline"])):
        issues.note("name_mismatch", code, s.callsign, ours=names[code]["name"], adsbdb=route["airline"])


async def build_model(cfg: Config, states: list[State], adsb: AdsbDb, names: dict[str, dict],
                      horizon_s: float = 0, prev_course: float | None = None,
                      issues: Issues | None = None) -> DisplayModel:
    """horizon_s: Zeit bis zum nächsten Abruf, für die Sichtfeld-Vorschau.
    prev_course: Landerichtung vom letzten Abruf, falls gerade niemand landet.
    issues: sammelt Auffälligkeiten (unbekannte Betreiber, verworfene Routen)."""
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
        if issues is not None:
            note_operator(issues, s, route, names, airline)
        if airline or cfg.show_other:
            ranked.append((s, v, vis, route, airline))
    # Sichtbar vor unsichtbar, Airline vor Sonstigen, jetzt vor gleich, dann die Sichtlinie
    ranked.sort(key=lambda r: (r[2] == 0, not r[4], -r[2], r[1].slant_km))
    # Sonstige nur, wenn keine Airline dran ist: Ist eine im Sichtfeld (oder, ohne
    # Sichtbares, überhaupt eine da), fliegen Privatjets und Hubschrauber raus.
    tier = [r for r in ranked if r[2] > 0] or ranked
    if any(r[4] for r in tier):
        tier = [r for r in tier if r[4]]

    course, votes = landing_course_votes(cfg, states, cfg.runway_course if prev_course is None else prev_course)
    seq = await landing_sequence(cfg, states, adsb, names, course)

    flights: list[Flight] = []
    for s, v, vis, route, airline in tier[: cfg.max_flights]:
        flights.append(await _make_flight(cfg, s, v, vis, route, airline, adsb, names, seq, issues))
    # Landende in Landefolge zeigen, damit #1 zuerst kommt
    flights.sort(key=lambda f: (f.seq is None, f.seq or 0))

    # Für den Leerlauf: wann kommt wer ins Sichtfeld, auch über den nächsten Abruf hinaus
    preds = {s.icao24: predict_entry(cfg, s, course, s.icao24 in seq) for s, v, vis in cands}
    nearby = [{"callsign": s.callsign or s.icao24.upper(), "icao24": s.icao24, "type": s.actype, "az": round(v.az),
               "elev": round(v.elev, 1), "dist_km": round(v.dist_km, 1),
               "alt_ft": int(s.baro_alt_m * 3.28084) if s.baro_alt_m is not None else None,
               "in_view": ("no", "soon", "now")[vis], "seq": seq.get(s.icao24),
               "eta_s": round(preds[s.icao24][0]) if preds[s.icao24] else None,
               "status": ARR if s.icao24 in seq else classify(s, "", "", cfg.local_airport_iata)}
              for s, v, vis in cands]
    model = DisplayModel(flights=flights, source="opensky", empty=not flights, nearby=nearby,
                         landing_course=course, course_votes=votes, alert=find_alert(cfg, states, names))

    # Der nächste, der ins Sichtfeld kommt: große Flugzeuge mit Airline-Callsign zuerst
    shown = {f.icao24 for f in flights}
    coming = [(s, v, preds[s.icao24]) for s, v, vis in cands
              if vis == 0 and s.icao24 not in shown and preds[s.icao24] and preds[s.icao24][0] > 0]
    if coming:
        def rank(c):
            s, _, p = c
            big = bool(callsign_airline(s.callsign)) or s.category in AIRLINER_CATEGORIES
            return (not big, p[0])
        s, v, p = min(coming, key=rank)
        route = await adsb.route(s.callsign)
        airline = is_airline(s.callsign, route, names, s.category)
        if airline or cfg.show_other:
            model.upcoming = await _make_flight(cfg, s, v, 0, route, airline, adsb, names, seq, None)
            model.upcoming_eta_s, model.upcoming_az, model.upcoming_via = round(p[0]), round(p[1]), p[2]
    return model


async def _make_flight(cfg: Config, s: State, v: Sighting, vis: int, route: dict, airline: bool,
                       adsb: AdsbDb, names: dict[str, dict], seq: dict[str, int],
                       issues: Issues | None) -> Flight:
    ac = await adsb.aircraft(s.icao24)
    origin, dest = route.get("origin", ""), route.get("dest", "")
    if not route_plausible(route, s.lat, s.lon):
        log.info("Route %s %s-%s passt nicht zur Position, verworfen", s.callsign, origin, dest)
        if issues is not None:
            issues.note("route_implausible", s.callsign, s.callsign, route=f"{origin}-{dest}",
                        pos=[round(s.lat, 2), round(s.lon, 2)])
        origin = dest = ""
    elif airline and not (origin or dest) and issues is not None:
        issues.note("route_unknown", callsign_airline(s.callsign) or s.callsign, s.callsign)
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
    f = Flight(
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
    )
    if f.seq is not None and f.status != ARR:
        f.status = ARR          # in der Landefolge, auch wenn die Route nichts sagt
    return f


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
    if flights and rnd.random() < 0.25:
        # ab und zu nur ein Privatflieger: kommt als Leerlauf-Karte, ohne Vorrang
        p = flights[0]
        p.callsign, p.kind, p.status, p.actype, p.alt_ft = "DEFGH", "other", OVF, "C172", 2500
        p.airline = p.airline_icao = p.airline_iata = p.origin = p.dest = ""
        flights = [p]
    for n, f in enumerate(sorted((f for f in flights if f.status == ARR), key=lambda f: f.dist_km)):
        f.seq = n + 1
    nearby = [{"callsign": f.callsign, "icao24": f.icao24, "az": f.bearing, "elev": 8.0, "dist_km": f.dist_km,
               "alt_ft": f.alt_ft, "in_view": "now", "seq": f.seq, "eta_s": 0, "status": f.status}
              for f in flights]
    # Weitere Flugzeuge im Radius, außerhalb des Sichtfelds: für Radar und "Nächster"
    for i in range(rnd.randint(2, 7)):
        cs, *_ , st = rnd.choice(_MOCK_POOL)
        nearby.append({"callsign": f"{cs[:3]}{rnd.randint(10, 9999)}", "icao24": f"3d{i:04x}",
                       "az": rnd.randint(0, 359), "elev": round(rnd.uniform(0.5, 25), 1),
                       "dist_km": round(rnd.uniform(5, 40), 1), "alt_ft": rnd.randint(2000, 20000),
                       "in_view": "no", "seq": None, "eta_s": rnd.choice([None, rnd.randint(40, 580)]),
                       "status": st})
    model = DisplayModel(flights=flights, source="mock", empty=not flights, nearby=nearby,
                         landing_course=cfg.runway_course, course_votes=2)
    if not flights:
        cs, al_icao, al_iata, al, o, d, typ, st = rnd.choice(_MOCK_POOL)
        model.upcoming = Flight(icao24="3cffff", callsign=cs, status=st, dist_km=22.0, bearing=250,
                                alt_ft=4200, speed_kt=210, track=70, vrate_fpm=-800, origin=o, dest=d,
                                airline=al, airline_icao=al_icao, airline_iata=al_iata, actype=typ,
                                visible=False)
        model.upcoming_eta_s = rnd.randint(60, 480)
        model.upcoming_az = (cfg.view_from + rnd.choice([-5, 5])) % 360
        model.upcoming_via = "approach" if st == ARR else "track"
    return model
