"""Bei jedem Abruf aus den Rohdaten das Display-Modell bauen.

1. Routen: adsb.im zuerst (eine Abfrage für alle, aktueller), dann adsbdb (auch
   Airline-Namen und -Codes). Routen, die nicht zur Position passen, fliegen raus;
   ohne Route kommt der Abflughafen aus der Flugspur (origins.py).
2. Kandidaten: in der Luft, im Radius, unter MAX_ALT_M, mit Sichtbarkeit (view.py).
3. Reihenfolge: Sichtbares vor dem Rest; Airlines vor Privatfliegern, Hubschraubern,
   Polizei und Militär, die nur kommen, wenn keine Airline zu sehen ist; dann die
   kürzeste Sichtlinie. Die ersten MAX_FLIGHTS werden gezeigt, Landende in Landefolge.
4. Für den Leerlauf: Landerichtung, Landefolge, Abflüge, wer als nächstes ins
   Sichtfeld kommt, Notfall-Squawks und alle Flugzeuge im Radius (nearby)."""

import logging

from .aircraft import describe
from .airlines import (AIRLINER_CATEGORIES, callsign_airline, is_airline, note_operator, route_plausible,
                       short_airline)
from .airport import (APPROACH_AREA_KM, DEP_AIR_KM, DEP_GROUND_KM, TERMINAL_ALT_M, find_departures,
                      landing_course_votes, landing_sequence, predict_entry)
from .config import Config
from .geo import FPM_PER_MS, FT_PER_M, KT_PER_MS, haversine_km
from .issues import Issues
from .model import ARR, DEP, OVF, DisplayModel, Flight, State
from .view import Sighting, in_view, sighting, visibility

log = logging.getLogger("build")

SHORTLIST = 8          # so viele Kandidaten werden per Route als Airline oder nicht eingeordnet
EMERGENCY = {"7700": "NOTFALL", "7600": "FUNKAUSFALL", "7500": "ENTFÜHRUNG"}

Candidate = tuple[State, Sighting, int]          # Flugzeug, Sichtung, Sichtbarkeit (view.visibility)


async def build_model(cfg: Config, states: list[State], adsb, names: dict[str, dict],
                      horizon_s: float = 0, prev_course: float | None = None, issues: Issues | None = None,
                      adsbim=None, origins=None, source: str = "live") -> DisplayModel:
    """adsb: adsbdb.AdsbDb (Routen, Flugzeugtypen). names: data/airlines.json.
    horizon_s: Zeit bis zum nächsten Abruf, für die Sichtfeld-Vorschau.
    prev_course: Landerichtung vom letzten Abruf, falls gerade niemand landet.
    issues: sammelt Auffälligkeiten. adsbim, origins: weitere Routenquellen, optional."""
    b = _Builder(cfg, adsb, names, issues, origins)
    if adsbim is not None:
        # nur, wer eine Route brauchen kann, nicht der ganze Statistik-Abruf (SCAN_RADIUS_KM)
        b.im = await adsbim.routes([(s.callsign, s.lat, s.lon) for s in states
                                    if callsign_airline(s.callsign) and needs_route(cfg, s)])
    cands = candidates(cfg, states, horizon_s)
    ranked = await b.rank(cands)

    course, votes = landing_course_votes(cfg, states, cfg.runway_course if prev_course is None else prev_course)
    b.seq, b.etas = await landing_sequence(cfg, states, names, course, b.route_for)
    flights = [await b.flight(s, v, vis, route, airline) for s, v, vis, route, airline in ranked[:cfg.max_flights]]
    flights.sort(key=lambda f: (f.seq is None, f.seq or 0))    # Landende in Landefolge, #1 zuerst

    # Für den Leerlauf: wann kommt wer ins Sichtfeld, auch über den nächsten Abruf hinaus,
    # wer startet gerade, wer landet als nächstes
    preds = {s.icao24: predict_entry(cfg, s, course, s.icao24 in b.seq) for s, _, _ in cands}
    routes = {s.callsign: await b.route_for(s) for s in states
              if callsign_airline(s.callsign) and (s.icao24 in b.seq or needs_route(cfg, s)
                                                   and haversine_km(cfg.airport_lat, cfg.airport_lon, s.lat, s.lon)
                                                   <= DEP_AIR_KM)}
    in_view_ids = {s.icao24 for s, _, vis in cands if vis > 0}
    model = DisplayModel(flights=flights, source=source, empty=not flights,
                         nearby=_nearby(cfg, cands, b.seq, b.etas, preds),
                         departures=find_departures(cfg, states, routes, names),
                         arrivals=_arrivals(states, b.seq, b.etas, routes, in_view_ids, names),
                         landing_course=course, course_votes=votes, alert=find_alert(cfg, states, names))
    await b.upcoming(model, cands, preds, {f.icao24 for f in flights})
    return model


def needs_route(cfg: Config, s: State) -> bool:
    """Wer eine Route brauchen kann: in der Luft im Anzeigeradius oder in der
    Landefolge, am Boden nur, wer am Platz rollt (Karte "departures")."""
    d_ap = haversine_km(cfg.airport_lat, cfg.airport_lon, s.lat, s.lon)
    if s.on_ground:
        return d_ap <= DEP_GROUND_KM and (s.velocity_ms or 0.0) * KT_PER_MS >= 5
    return haversine_km(cfg.home_lat, cfg.home_lon, s.lat, s.lon) <= cfg.radius_km or d_ap <= APPROACH_AREA_KM


def candidates(cfg: Config, states: list[State], horizon_s: float) -> list[Candidate]:
    """In der Luft, im Radius, unter MAX_ALT_M; Sichtbares zuerst, dann die kürzeste Sichtlinie."""
    out = []
    for s in states:
        if s.on_ground:
            continue
        if s.baro_alt_m is not None and s.baro_alt_m > cfg.max_alt_m:
            continue
        v = sighting(cfg, s.lat, s.lon, s.alt_m)
        if v.dist_km > cfg.radius_km:
            continue
        out.append((s, v, visibility(cfg, s, horizon_s)))
    out.sort(key=lambda c: (c[2] == 0, c[1].slant_km))
    return out


def classify(s: State, origin: str, dest: str, local: str, guess: bool = True) -> str:
    """Ankunft, Abflug oder Überflug. Ohne Route raten aus Höhe und Steigrate,
    aber nur bei Airlines (guess): Ein landender Rettungshubschrauber fliegt
    zur Klinik, nicht zum Flughafen."""
    if dest and dest == local:
        return ARR
    if origin and origin == local:
        return DEP
    if not guess:
        return OVF
    alt = s.baro_alt_m or 99999
    vr = s.vrate_ms or 0.0
    if alt < TERMINAL_ALT_M and vr < -2:
        return ARR
    if alt < TERMINAL_ALT_M and vr > 2:
        return DEP
    return OVF


def find_alert(cfg: Config, states: list[State], names: dict[str, dict]) -> dict | None:
    """Das nächste Flugzeug in der Luft mit Notfall-Squawk (7700, 7600, 7500)."""
    hits = []
    for s in states:
        if s.on_ground or s.squawk not in EMERGENCY:
            continue
        v = sighting(cfg, s.lat, s.lon, s.alt_m)
        if v.dist_km > cfg.fetch_radius_km:
            continue
        code = callsign_airline(s.callsign)
        hits.append((v.dist_km, {
            "callsign": s.callsign or s.icao24.upper(), "icao24": s.icao24, "squawk": s.squawk,
            "meaning": EMERGENCY[s.squawk], "type": s.actype,
            "airline": names[code]["name"] if code in names else "",
            "dist_km": round(v.dist_km, 1), "az": round(v.az), "elev": round(v.elev, 1),
            "alt_ft": int(s.baro_alt_m * FT_PER_M) if s.baro_alt_m is not None else None,
            "in_view": in_view(cfg, v)}))
    return min(hits, key=lambda h: h[0])[1] if hits else None


def _nearby(cfg: Config, cands: list[Candidate], seq: dict[str, int], etas: dict[str, float],
            preds: dict[str, tuple | None]) -> list[dict]:
    """Alle Flugzeuge im Radius, für Radar, "Nächster" und zum Eichen des Sichtfelds."""
    return [{"callsign": s.callsign or s.icao24.upper(), "icao24": s.icao24, "type": s.actype, "az": round(v.az),
             "elev": round(v.elev, 1), "dist_km": round(v.dist_km, 1),
             "alt_ft": int(s.baro_alt_m * FT_PER_M) if s.baro_alt_m is not None else None,
             "in_view": ("no", "soon", "now")[vis], "seq": seq.get(s.icao24),
             "land_eta_s": round(etas[s.icao24]) if s.icao24 in etas else None,
             "eta_s": round(preds[s.icao24][0]) if preds[s.icao24] else None,
             "status": ARR if s.icao24 in seq else classify(s, "", "", cfg.local_airport_iata)}
            for s, v, vis in cands]


def _arrivals(states: list[State], seq: dict[str, int], etas: dict[str, float], routes: dict[str, dict],
              in_view_ids: set[str], names: dict[str, dict]) -> list[dict]:
    """Die Landefolge mit Herkunft und Sekunden bis zum Aufsetzen (Karte "arrivals")."""
    by_icao = {s.icao24: s for s in states}
    out = []
    for icao, pos in sorted(seq.items(), key=lambda kv: kv[1]):
        s = by_icao[icao]
        route = routes.get(s.callsign, {})
        if not route_plausible(route, s.lat, s.lon):
            route = {}
        code = callsign_airline(s.callsign)
        out.append({"callsign": s.callsign, "icao24": icao, "seq": pos, "eta_s": round(etas[icao]),
                    "airline": short_airline(route.get("airline", ""), route.get("airline_icao") or code, names)
                               or s.callsign,
                    "origin": route.get("origin", ""), "type": s.actype, "in_view": icao in in_view_ids})
    return out


class _Builder:
    """Was alle Schritte eines Abrufs brauchen: Quellen, Routen von adsb.im, Landefolge."""

    def __init__(self, cfg: Config, adsb, names: dict[str, dict], issues: Issues | None, origins):
        self.cfg = cfg
        self.adsb = adsb
        self.names = names
        self.issues = issues
        self.origins = origins
        self.im: dict[str, dict] = {}        # Routen von adsb.im, pro Callsign
        self.seq: dict[str, int] = {}        # icao24 -> Platz in der Landefolge
        self.etas: dict[str, float] = {}     # icao24 -> Sekunden bis zum Aufsetzen

    async def route_for(self, s: State) -> dict:
        """Route von adsbdb, Strecke von adsb.im, wenn die zur Position passt."""
        route = await self.adsb.route(s.callsign)
        alt = self.im.get(s.callsign)
        if alt and route_plausible(alt, s.lat, s.lon):
            return {**route, **alt}
        return route

    async def rank(self, cands: list[Candidate]) -> list[tuple[State, Sighting, int, dict, bool]]:
        """(State, Sichtung, Sichtbarkeit, Route, Airline) in Anzeigereihenfolge. Ob
        Airline oder nicht, braucht die Route, deshalb nur für die vordersten."""
        pool = [c for c in cands if c[2] > 0] if self.cfg.view_only else cands
        ranked = []
        for s, v, vis in pool[:SHORTLIST]:
            route = await self.route_for(s)
            airline = is_airline(s.callsign, route, self.names, s.category)
            if self.issues is not None:
                note_operator(self.issues, s, route, self.names, airline)
            if airline or self.cfg.show_other:
                ranked.append((s, v, vis, route, airline))
        # Sichtbar vor unsichtbar, Airline vor Sonstigen, jetzt vor gleich, dann die Sichtlinie
        ranked.sort(key=lambda r: (r[2] == 0, not r[4], -r[2], r[1].slant_km))
        # Sonstige nur, wenn keine Airline dran ist: Ist eine im Sichtfeld (oder, ohne
        # Sichtbares, überhaupt eine da), fliegen Privatjets und Hubschrauber raus.
        tier = [r for r in ranked if r[2] > 0] or ranked
        if any(r[4] for r in tier):
            tier = [r for r in tier if r[4]]
        return tier

    async def upcoming(self, model: DisplayModel, cands: list[Candidate], preds: dict, shown: set[str]) -> None:
        """Der nächste, der ins Sichtfeld kommt (Karte "next"): große Flugzeuge mit
        Airline-Callsign zuerst."""
        coming = [(s, v, preds[s.icao24]) for s, v, vis in cands
                  if vis == 0 and s.icao24 not in shown and preds[s.icao24] and preds[s.icao24][0] > 0]
        if not coming:
            return

        def rank(c):
            s, _, p = c
            big = bool(callsign_airline(s.callsign)) or s.category in AIRLINER_CATEGORIES
            return (not big, p[0])

        s, v, p = min(coming, key=rank)
        route = await self.route_for(s)
        airline = is_airline(s.callsign, route, self.names, s.category)
        if airline or self.cfg.show_other:
            model.upcoming = await self.flight(s, v, 0, route, airline, note=False)
            model.upcoming_eta_s, model.upcoming_az, model.upcoming_via = round(p[0]), round(p[1]), p[2]

    async def flight(self, s: State, v: Sighting, vis: int, route: dict, airline: bool,
                     note: bool = True) -> Flight:
        """Ein Flug fürs Panel. note: Auffälligkeiten (unpassende oder fehlende Route) melden."""
        cfg, names = self.cfg, self.names
        issues = self.issues if note else None
        ac = await self.adsb.aircraft(s.icao24)
        origin, dest = route.get("origin", ""), route.get("dest", "")
        if not route_plausible(route, s.lat, s.lon):
            log.info("Route %s %s-%s passt nicht zur Position, verworfen", s.callsign, origin, dest)
            if issues is not None:
                issues.note("route_implausible", s.callsign, s.callsign, route=f"{origin}-{dest}",
                            pos=[round(s.lat, 2), round(s.lon, 2)])
            origin = dest = ""
        # Herkunft aus der Flugspur, wenn keine Datenbank sie kennt (EJU9047: Spur beginnt
        # in Orly). Nur für Flüge mit Betreiber-Callsign; Landende enden hier.
        if not origin and self.origins is not None and callsign_airline(s.callsign):
            o = await self.origins.origin(s.icao24, s.callsign)
            if o:
                origin = o["origin"]
                if not dest and s.icao24 in self.seq:
                    dest = cfg.local_airport_iata
        if airline and not (origin or dest) and issues is not None:
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
        # Name und Bezeichnung: Airlines aus airlines.json oder adsbdb; sonst Betreiber
        # (Christoph 31, Bundespolizei) oder Kennzeichen, dazu was es ist. Nie der Halter
        # aus adsbdb: bei Privatflugzeugen eine Privatperson.
        if airline:
            name, label = short_airline(route.get("airline", "") or ac.get("operator", ""), airline_icao, names), ""
        else:
            name, label = describe(s.callsign, s.category, s.registration, names)
        f = Flight(
            icao24=s.icao24,
            callsign=s.callsign or s.icao24.upper(),
            status=classify(s, origin, dest, cfg.local_airport_iata, guess=airline),
            dist_km=round(v.dist_km, 1),
            bearing=round(v.az),
            alt_ft=int(s.baro_alt_m * FT_PER_M) if s.baro_alt_m is not None else None,
            speed_kt=int(s.velocity_ms * KT_PER_MS) if s.velocity_ms is not None else None,
            track=int(s.track_deg) if s.track_deg is not None else None,
            vrate_fpm=int(s.vrate_ms * FPM_PER_MS) if s.vrate_ms is not None else None,
            origin=origin, dest=dest,
            airline=name,
            airline_icao=airline_icao,
            airline_iata=airline_iata,
            actype=s.actype or ac.get("type", ""),
            kind="airline" if airline else "other",
            visible=vis > 0,
            elev_deg=round(v.elev, 1),
            seq=self.seq.get(s.icao24),
            label=label,
            land_eta_s=round(self.etas[s.icao24]) if s.icao24 in self.etas else None,
        )
        if f.seq is not None and f.status != ARR:
            f.status = ARR          # in der Landefolge, auch wenn die Route nichts sagt
        return f
