"""Routen von adsb.im, dem Routendienst hinter den tar1090-Karten (auch adsb.lol).

Aktueller als adsbdb, das Callsigns für wechselnde Strecken weiterverwendet:
EWG55E stand bei adsbdb als London-Düsseldorf, adsb.im kannte Palma-Berlin;
THY5MH Istanbul-Van statt Berlin-Istanbul. OpenSky taugt dafür nicht, dessen
Routendienst ist seit 2023 nicht mehr gepflegt.

Eine Abfrage für mehrere Flüge mit Position, adsb.im prüft die Plausibilität
selbst mit. Die Stationen bleiben pro Callsign 30 min im Cache (Strecken wechseln
von Tag zu Tag), also etwa ein Abruf pro neuem Flug.

Manche Callsigns fliegen mehrere Teilstrecken, auch hin und zurück: EWG9SK ist
BER-NCE-BER. Welche Teilstrecke gerade dran ist, entscheidet current_leg bei
jedem Abruf neu aus der Position, nicht einmal für 30 min: Beim ersten Abruf
rollt das Flugzeug vielleicht noch.

    POST https://adsb.im/api/0/routeset  {"planes": [{"callsign", "lat", "lng"}]}
    -> [{"callsign", "_airport_codes_iata": "BER-NCE-BER", "_airports": [{iata, lat, lon}, ...],
         "airline_code": "EWG", "plausible": true}]       unbekannt: "unknown"
"""

import logging
from itertools import pairwise
from typing import NamedTuple

import aiohttp

from .cache import Cache
from .geo import angle_diff, bearing_deg, haversine_km
from .issues import Issues
from .model import State

log = logging.getLogger("adsbim")

URL = "https://adsb.im/api/0/routeset"
TTL = 30 * 60
MAX_PER_REQUEST = 20
SAME_LEG_KM = 50       # Teilstrecken, deren Umweg sich so wenig unterscheidet, liegen gleich gut
NEAR_KM = 60           # so nah am Flughafen und tief: Steigen heißt Start dort, Sinken Landung
LOW_M = 3000


class Stop(NamedTuple):
    iata: str
    lat: float
    lon: float


def parse(r: dict) -> list[Stop]:
    """Antwort für einen Flug -> Stationen der Reihe nach, leer wenn unbekannt oder
    unplausibel."""
    if not r.get("plausible"):
        return []
    stops = []
    for a in r.get("_airports") or []:
        try:
            if a.get("iata"):
                stops.append(Stop(str(a["iata"]).upper(), float(a["lat"]), float(a["lon"])))
        except (AttributeError, KeyError, TypeError, ValueError):
            continue
    return stops if len(stops) >= 2 else []


def current_leg(stops: list[Stop], s: State, home: str = "", hint: str | None = None) -> tuple[Stop, Stop] | None:
    """Die Teilstrecke, auf der das Flugzeug gerade ist: die mit dem kleinsten Umweg
    über seine Position. Liegen mehrere gleich gut (hin und zurück auf derselben
    Strecke, oder kurz vor oder nach einer Zwischenlandung), entscheidet die Richtung:
    hint ("arr" landet am Heimatflughafen home, "dep" ist dort gestartet, aus
    airport.Recent); sonst tief in Flughafennähe Steigen (Start dort) oder Sinken
    (Landung dort); unterwegs der Kurs. Am Boden oder im Horizontalflug in
    Flughafennähe ohne hint: None, lieber keine Route als eine falsche."""
    legs = list(pairwise(stops))
    if not legs:
        return None

    def detour(leg: tuple[Stop, Stop]) -> float:
        o, d = leg
        return (haversine_km(o.lat, o.lon, s.lat, s.lon) + haversine_km(s.lat, s.lon, d.lat, d.lon)
                - haversine_km(o.lat, o.lon, d.lat, d.lon))

    def near(stop: Stop) -> bool:
        return haversine_km(s.lat, s.lon, stop.lat, stop.lon) <= NEAR_KM

    best = min(detour(leg) for leg in legs)
    same = [leg for leg in legs if detour(leg) <= best + SAME_LEG_KM]
    if len(same) == 1:
        return same[0]
    if hint and home:
        known = [(o, d) for o, d in same if (d if hint == "arr" else o).iata == home]
        if len(known) == 1:
            return known[0]
    if s.on_ground:
        return None
    if s.alt_m is not None and s.alt_m < LOW_M and any(near(stop) for leg in same for stop in leg):
        vr = s.vrate_ms or 0.0
        if vr > 1.0:
            return next(((o, d) for o, d in same if near(o)), None)
        if vr < -1.0:
            return next(((o, d) for o, d in same if near(d)), None)
        return None
    if s.track_deg is not None:
        return min(same, key=lambda leg: angle_diff(s.track_deg, bearing_deg(s.lat, s.lon, leg[1].lat, leg[1].lon)))
    return None


def leg_route(stops: list[Stop], s: State, home: str = "", hint: str | None = None) -> dict:
    """Die aktuelle Teilstrecke als Route wie bei adsbdb, {} wenn nicht zu entscheiden."""
    leg = current_leg(stops, s, home, hint)
    if leg is None or leg[0].iata == leg[1].iata:
        return {}
    o, d = leg
    return {"origin": o.iata, "dest": d.iata, "origin_pos": [o.lat, o.lon], "dest_pos": [d.lat, d.lon]}


class AdsbIm:
    def __init__(self, session: aiohttp.ClientSession, cache: Cache, issues: Issues | None = None):
        self.session = session
        self.cache = cache
        self.issues = issues
        self.last_error: str | None = None

    async def routes(self, states: list[State]) -> dict[str, list[Stop]]:
        """{callsign: Stationen, leer wenn unbekannt} für die Flugzeuge. Fehler: Flug
        fehlt im Ergebnis und wird beim nächsten Mal wieder gefragt."""
        out: dict[str, list[Stop]] = {}
        ask: dict[str, State] = {}
        for s in states:
            if not s.callsign:
                continue
            hit = self.cache.get(f"imstops:{s.callsign}")
            if hit is not None:
                out[s.callsign] = [Stop(*x) for x in hit]
            else:
                ask[s.callsign] = s
        todo = [{"callsign": cs, "lat": round(s.lat, 3), "lng": round(s.lon, 3)} for cs, s in ask.items()]
        for i in range(0, len(todo), MAX_PER_REQUEST):
            try:
                async with self.session.post(URL, json={"planes": todo[i:i + MAX_PER_REQUEST]},
                                             timeout=aiohttp.ClientTimeout(total=10)) as r:
                    if r.status >= 400:
                        raise RuntimeError(f"HTTP {r.status}")
                    resp = await r.json(content_type=None)
                self.last_error = None
            except Exception as e:
                self.last_error = str(e) or type(e).__name__
                log.warning("adsb.im: %s", self.last_error)
                if self.issues is not None:
                    self.issues.source_error("adsbim", self.last_error)
                continue
            for item in resp if isinstance(resp, list) else []:
                cs = str(item.get("callsign") or "").strip().upper()
                if cs in ask:
                    stops = parse(item)
                    self.cache.set(f"imstops:{cs}", [list(x) for x in stops], TTL)
                    out[cs] = stops
        return out
