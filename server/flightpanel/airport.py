"""Was am Heimatflughafen passiert: Landerichtung aus dem Verkehr, Landefolge mit
Minuten bis zum Aufsetzen, wer gerade startet, und welche Teile von An- und Abflug
im Sichtfeld liegen.

Die Piste ist eine Gerade durch den Flughafen-Bezugspunkt (AIRPORT_LAT/LON) mit
dem Kurs RUNWAY_COURSE oder der Gegenrichtung; Parallelpisten wie am BER sind
dafür eine."""

import math
from collections.abc import Awaitable, Callable

from .airlines import NOT_RUNWAY_CATEGORIES, callsign_airline, route_plausible, short_airline
from .config import Config
from .geo import KT_PER_MS, angle_diff, bearing_deg, destination, haversine_km
from .model import State
from .view import entry_eta, in_view, sighting

TERMINAL_ALT_M = 3000  # darunter: im An- oder Abflug
APPROACH_AREA_KM = 45  # so weit um den Platz zählen Landende zur Landefolge
FINAL_KM = 18          # Länge des Endanflugs: Gegenanflüge drehen ungefähr hier ein
APPROACH_MS = 70.0     # ~135 kt im Endanflug, so langsam werden Landende zum Schluss
GLIDE_DEG = 3.0        # Gleitpfad im Endanflug
CLIMB = 0.10           # Steiggradient nach dem Start (m pro m)
DEPARTURE_KM = 12      # so weit fliegen Abflüge ungefähr geradeaus
NEXT_HORIZON_S = 600   # so weit voraus sucht der Leerlauf den nächsten Flieger
NEXT_STEP_S = 15
DEP_GROUND_KM = 4      # so weit vom Bezugspunkt reicht das Flughafengelände
DEP_AIR_KM = 30        # gerade gestartet: so weit weg, unter TERMINAL_ALT_M, steigend
DEP_RECENT_S = 300     # "in den letzten ~5 min gestartet"

RouteFor = Callable[[State], Awaitable[dict]]


def landing_course_votes(cfg: Config, states: list[State], prev: float) -> tuple[float, int]:
    """Landerichtung aus dem Verkehr: Was tief in Platznähe fliegt, landet oder
    startet gegen den Wind, also parallel zur Piste. Mehrheit entscheidet, ohne
    Verkehr bleibt die letzte. Dazu die Stimmen für die gewählte Richtung."""
    if cfg.runway_course is None:           # ohne Flughafen (AIRPORT=-) gibt es keine Landerichtung
        return prev, 0
    votes = {cfg.runway_course % 360: 0, (cfg.runway_course + 180) % 360: 0}
    for s in states:
        alt = s.alt_m
        if s.on_ground or s.track_deg is None or alt is None or alt > 1500:
            continue
        if haversine_km(cfg.airport_lat, cfg.airport_lon, s.lat, s.lon) > 20:
            continue
        for c in votes:
            if angle_diff(s.track_deg, c) < 30:
                votes[c] += 1
    best = max(votes, key=votes.get)
    return (best, votes[best]) if votes[best] else (prev, 0)


def runway_name(course: float) -> str:
    """249° -> "25", 69° -> "07"."""
    n = round((course % 360) / 10) % 36 or 36
    return f"{n:02d}"


_PATH_CACHE: dict[tuple, list[tuple[float, float, float]]] = {}
PATH_CACHE_MAX = 256    # jede Änderung am Sichtfeld gibt neue Schlüssel (panels set, Einstellungsseite)


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
    if len(_PATH_CACHE) >= PATH_CACHE_MAX:
        _PATH_CACHE.clear()
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
    if angle_diff(trk, course) < 30 and along > 0:
        return math.hypot(along, cross)
    if angle_diff(trk, course + 180) < 45 or along < 6:
        turn = max(along, FINAL_KM)
        return (turn - along) + cross + turn
    return cross + along


def landing_eta_s(cfg: Config, s: State, course: float) -> float:
    """Sekunden bis zum Aufsetzen: Reststrecke nach Flugphase (remaining_km) bei
    mittlerem Tempo zwischen jetzt und Endanflug, denn Landende werden langsamer."""
    v = max(s.velocity_ms or 0.0, APPROACH_MS * 0.8)
    v_mean = (v + APPROACH_MS) / 2 if v > APPROACH_MS else v
    return remaining_km(cfg, s, course) * 1000 / v_mean


def is_arrival(cfg: Config, s: State, route: dict) -> bool:
    """Landet am eigenen Flughafen: laut Route, sonst tief, nicht steigend, in Platznähe."""
    if route.get("dest") == cfg.local_airport_iata:
        return True
    if route.get("origin") == cfg.local_airport_iata:
        return False
    alt = s.alt_m
    return (alt is not None and alt < TERMINAL_ALT_M and (s.vrate_ms or 0.0) < 1.0
            and haversine_km(cfg.airport_lat, cfg.airport_lon, s.lat, s.lon) < APPROACH_AREA_KM)


def returning(cfg: Config, s: State, route: dict, hint: str | None = None) -> bool:
    """Die Route beginnt am Heimatflughafen, aber das Flugzeug landet hier: Es kommt
    zurück, die Route ist der Hinflug unter demselben Callsign (adsbdb kennt bei
    EWG9SK nur BER-NCE). Landet hier: war in den letzten Minuten in der Landefolge
    (hint "arr", auch nach dem Aufsetzen und im Horizontalflug), oder sinkt gerade
    tief in Platznähe. Umgekehrt gilt das nicht: Wer kurz vor der Landung steigt,
    startet vielleicht nur durch."""
    if route.get("origin") != cfg.local_airport_iata:
        return False
    if hint == "arr":
        return True
    alt = s.alt_m
    return (not s.on_ground and alt is not None and alt < TERMINAL_ALT_M and (s.vrate_ms or 0.0) < -1.0
            and haversine_km(cfg.airport_lat, cfg.airport_lon, s.lat, s.lon) < APPROACH_AREA_KM)


class Recent:
    """Wer in den letzten Minuten hier landete oder startete (icao24 und Callsign).

    Ein einzelner Abruf verrät die Richtung nicht immer: am Boden, im
    Horizontalflug. Bei Callsigns, die hin und zurück fliegen (EWG9SK BER-NCE-BER),
    entscheidet sie aber, welche Strecke stimmt. build_model fragt nur hint();
    nachgeführt wird (update) einmal je Abruf von airfield.Airfield.observe."""

    KEEP_S = 15 * 60        # so lange nach dem letzten Mal in Landefolge oder Abflügen

    def __init__(self) -> None:
        self._dirs: dict[tuple[str, str], tuple[str, float]] = {}

    def hint(self, s: State) -> str | None:
        """"arr": landet hier oder ist gerade gelandet, "dep": ist gerade gestartet, sonst None."""
        e = self._dirs.get((s.icao24, s.callsign))
        return e[0] if e else None

    def update(self, arrivals: list[dict], departures: list[dict], now: float) -> None:
        for a in arrivals:
            self._dirs[(a["icao24"], a["callsign"])] = ("arr", now)
        for d in departures:
            self._dirs[(d["icao24"], d["callsign"])] = ("dep", now)
        self._dirs = {k: v for k, v in self._dirs.items() if now - v[1] < self.KEEP_S}


async def landing_sequence(cfg: Config, states: list[State], names: dict[str, dict], course: float,
                           route_for: RouteFor) -> tuple[dict[str, int], dict[str, float]]:
    """(icao24 -> Platz in der Landefolge, icao24 -> Sekunden bis zum Aufsetzen).
    Zählt alles, was die Piste braucht, auch Businessjets (BFX1073), damit #2 der
    zweite Flieger am Himmel ist. Nicht: Kennzeichen-Callsigns (Privatflieger landen
    meist woanders), Rettung und Polizei, Hubschrauber."""
    eta: list[tuple[float, str]] = []
    for s in states:
        alt = s.alt_m
        code = callsign_airline(s.callsign)
        if s.on_ground or alt is None or alt > TERMINAL_ALT_M or not code or s.category in NOT_RUNWAY_CATEGORIES:
            continue
        if code in names and not names[code]["airline"]:
            continue
        if haversine_km(cfg.airport_lat, cfg.airport_lon, s.lat, s.lon) > APPROACH_AREA_KM:
            continue
        route = await route_for(s)
        if not route_plausible(route, s.lat, s.lon):
            route = {}
        if not is_arrival(cfg, s, route):
            continue
        eta.append((landing_eta_s(cfg, s, course), s.icao24))
    return {icao: i + 1 for i, (_, icao) in enumerate(sorted(eta))}, {icao: t for t, icao in eta}


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


def find_departures(cfg: Config, states: list[State], routes: dict[str, dict],
                    names: dict[str, dict]) -> list[dict]:
    """Wer gerade am eigenen Platz startet: rollt zur Bahn, beschleunigt auf der Bahn
    oder ist in den letzten Minuten gestartet.

    Am Boden zählt nur, wer laut Route am Platz abfliegt: Rollen nach der Landung
    sieht sonst genauso aus. In der Luft genügt es auch ohne Route, tief zu steigen
    und sich vom Platz zu entfernen."""
    out = []
    for s in states:
        code = callsign_airline(s.callsign)
        if not code or s.category in NOT_RUNWAY_CATEGORIES:
            continue
        route = routes.get(s.callsign, {})
        if not route_plausible(route, s.lat, s.lon):
            route = {}
        if route.get("dest") == cfg.local_airport_iata:
            continue
        from_here = route.get("origin") == cfg.local_airport_iata
        d = haversine_km(cfg.airport_lat, cfg.airport_lon, s.lat, s.lon)
        kt = (s.velocity_ms or 0.0) * KT_PER_MS
        if s.on_ground:
            if d > DEP_GROUND_KM or not from_here or kt < 5:
                continue
            phase, rank, ago = ("startet", 0, None) if kt >= 40 else ("rollt", 1, None)
        else:
            alt = s.alt_m
            vr = s.vrate_ms or 0.0
            if alt is None or alt > TERMINAL_ALT_M or d > DEP_AIR_KM or vr < 0:
                continue
            away = s.track_deg is not None and angle_diff(
                s.track_deg, bearing_deg(cfg.airport_lat, cfg.airport_lon, s.lat, s.lon)) < 60
            if not (from_here or (vr > 2 and away)):
                continue
            ago = d * 1000 / max(s.velocity_ms or 0.0, 80.0)
            if ago > DEP_RECENT_S:
                continue
            phase, rank = "gestartet", 2
        out.append({"callsign": s.callsign, "icao24": s.icao24, "phase": phase, "rank": rank,
                    "ago_s": round(ago) if ago is not None else None,
                    "airline": short_airline(route.get("airline", ""), route.get("airline_icao") or code, names)
                               or s.callsign,
                    "dest": route.get("dest", ""), "type": s.actype})
    out.sort(key=lambda x: (x["rank"], x["ago_s"] or 0))
    return out
