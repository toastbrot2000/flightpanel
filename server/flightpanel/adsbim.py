"""Routen von adsb.im, dem Routendienst hinter den tar1090-Karten (auch adsb.lol).

Aktueller als adsbdb, das Callsigns für wechselnde Strecken weiterverwendet:
EWG55E stand bei adsbdb als London-Düsseldorf, adsb.im kannte Palma-Berlin;
THY5MH Istanbul-Van statt Berlin-Istanbul. OpenSky taugt dafür nicht, dessen
Routendienst ist seit 2023 nicht mehr gepflegt.

Eine Abfrage für mehrere Flüge mit Position, adsb.im prüft die Plausibilität
selbst mit. Pro Callsign 30 min im Cache (Strecken wechseln von Tag zu Tag,
nicht im Flug), also etwa ein Abruf pro neuem Flug.

Manche Callsigns fliegen mehrere Teilstrecken, auch hin und zurück: EWG9SK ist
BER-NCE-BER. Gezeigt wird die Teilstrecke, auf der das Flugzeug gerade ist
(current_leg), nicht erste bis letzte Station (das wäre BER→BER).

    POST https://adsb.im/api/0/routeset  {"planes": [{"callsign", "lat", "lng"}]}
    -> [{"callsign", "_airport_codes_iata": "BER-NCE-BER", "_airports": [{iata, lat, lon}, ...],
         "airline_code": "EWG", "plausible": true}]       unbekannt: "unknown"
"""

import logging
from typing import NamedTuple

import aiohttp

from .adsbdb import Cache
from .geo import angle_diff, bearing_deg, haversine_km
from .model import State

log = logging.getLogger("adsbim")

URL = "https://adsb.im/api/0/routeset"
TTL = 30 * 60
MAX_PER_REQUEST = 20
SAME_LEG_KM = 50       # Teilstrecken, deren Umweg sich so wenig unterscheidet, liegen gleich gut
NEAR_KM = 60           # so nah am Flughafen und tief: Steigen heißt Start dort, sonst Landung
LOW_M = 3000


class Stop(NamedTuple):
    iata: str
    lat: float
    lon: float


def parse(r: dict, s: State) -> dict:
    """Antwort für einen Flug -> {"origin", "dest", "origin_pos", "dest_pos"} wie bei
    adsbdb, oder {} wenn unbekannt, unplausibel oder nicht zu entscheiden."""
    if not r.get("plausible"):
        return {}
    stops = []
    for a in r.get("_airports") or []:
        try:
            if a.get("iata"):
                stops.append(Stop(str(a["iata"]).upper(), float(a["lat"]), float(a["lon"])))
        except (AttributeError, KeyError, TypeError, ValueError):
            continue
    leg = current_leg(stops, s)
    if leg is None or leg[0].iata == leg[1].iata:
        return {}
    o, d = leg
    return {"origin": o.iata, "dest": d.iata, "origin_pos": [o.lat, o.lon], "dest_pos": [d.lat, d.lon]}


def current_leg(stops: list[Stop], s: State) -> tuple[Stop, Stop] | None:
    """Die Teilstrecke, auf der das Flugzeug gerade ist: die mit dem kleinsten Umweg
    über seine Position. Liegen mehrere gleich gut (hin und zurück auf derselben
    Strecke, oder kurz vor oder nach einer Zwischenlandung), entscheidet die
    Richtung: tief in Flughafennähe steigend = Start dort, sonst Landung dort;
    unterwegs der Kurs. None, wenn nichts davon hilft."""
    legs = list(zip(stops, stops[1:], strict=False))
    if not legs:
        return None

    def detour(leg: tuple[Stop, Stop]) -> float:
        o, d = leg
        return (haversine_km(o.lat, o.lon, s.lat, s.lon) + haversine_km(s.lat, s.lon, d.lat, d.lon)
                - haversine_km(o.lat, o.lon, d.lat, d.lon))

    best = min(detour(leg) for leg in legs)
    same = [leg for leg in legs if detour(leg) <= best + SAME_LEG_KM]
    if len(same) == 1:
        return same[0]
    climbing = (s.vrate_ms or 0.0) > 1.0
    if s.alt_m is not None and s.alt_m < LOW_M:
        for o, d in same:
            end = o if climbing else d
            if haversine_km(s.lat, s.lon, end.lat, end.lon) <= NEAR_KM:
                return o, d
    if s.track_deg is not None:
        return min(same, key=lambda leg: angle_diff(s.track_deg, bearing_deg(s.lat, s.lon, leg[1].lat, leg[1].lon)))
    return None


class AdsbIm:
    def __init__(self, session: aiohttp.ClientSession, cache: Cache):
        self.session = session
        self.cache = cache
        self.last_error: str | None = None
        self.issues = None      # Issues, setzt main

    async def routes(self, states: list[State]) -> dict[str, dict]:
        """{callsign: Route oder {}} für die Flugzeuge. Fehler: Flug fehlt im Ergebnis
        und wird beim nächsten Mal wieder gefragt."""
        out: dict[str, dict] = {}
        ask: dict[str, State] = {}
        for s in states:
            if not s.callsign:
                continue
            hit = self.cache.get(f"im:{s.callsign}")
            if hit is not None:
                out[s.callsign] = hit
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
                    self.issues.note("rate_limit" if "429" in self.last_error else "source_error", "adsbim",
                                     error=self.last_error)
                continue
            for item in resp if isinstance(resp, list) else []:
                cs = str(item.get("callsign") or "").strip().upper()
                if cs in ask:
                    route = parse(item, ask[cs])
                    self.cache.set(f"im:{cs}", route, TTL)
                    out[cs] = route
        return out
