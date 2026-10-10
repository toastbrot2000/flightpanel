"""Abflughafen aus der Flugspur, wenn keine Routendatenbank den Flug kennt.

EJU9047 (easyJet Orly-Berlin) stand bei adsbdb als Basel-Toulouse, adsb.im
kannte ihn nicht. Die OpenSky-Spur des laufenden Flugs (/tracks/all, time=0)
begann aber am Boden, 1 km neben der Piste von Orly: ORY.

Der erste Spurpunkt zählt nur als Start, wenn er tief liegt (MAX_START_ALT_M);
höher hat OpenSky den Flug erst unterwegs erfasst und der Start bleibt
unbekannt. Flughafen: der nächste mit Linienverkehr aus data/airports.json
(OurAirports, gemeinfrei) im Umkreis von MAX_AIRPORT_KM. Ziel kennt die Spur
nicht; bei Landungen am eigenen Platz ergänzt es das Modell.

Nur für gezeigte Flüge ohne Route abfragen, das Ergebnis bleibt pro Flug im
Cache: ein paar Abrufe am Tag."""

import json
import logging
import os

import aiohttp

from .adsbdb import Cache
from .geo import haversine_km

log = logging.getLogger("origins")

TRACK_URL = "https://opensky-network.org/api/tracks/all"
MAX_START_ALT_M = 1000
MAX_AIRPORT_KM = 8
TTL = 3 * 3600          # Flug dauert selten länger, und die Spur ändert ihren Anfang nicht
NEG_TTL = 15 * 60       # keine Spur: später vielleicht doch


def load_airports(data_dir: str) -> list[tuple[str, float, float]]:
    """[(IATA, Breite, Länge)] aus data/airports.json, leer wenn die Datei fehlt."""
    try:
        with open(os.path.join(data_dir, "airports.json"), encoding="utf-8") as f:
            return [(r[0], float(r[2]), float(r[3])) for r in json.load(f)["airports"]]
    except FileNotFoundError:
        return []
    except Exception as e:
        log.warning("airports.json nicht lesbar: %s", e)
        return []


def nearest_airport(airports: list[tuple[str, float, float]], lat: float, lon: float,
                    max_km: float = MAX_AIRPORT_KM) -> tuple[str, float, float] | None:
    best, best_d = None, max_km
    for ap in airports:
        if abs(ap[1] - lat) > 0.2:          # grob vorfiltern, ~22 km
            continue
        d = haversine_km(lat, lon, ap[1], ap[2])
        if d <= best_d:
            best, best_d = ap, d
    return best


def track_start(track: dict, callsign: str) -> tuple[float, float] | None:
    """Startpunkt aus der Spur: erster Punkt, wenn tief genug und die Spur zu
    diesem Callsign gehört (sonst ist es ein anderer Flug desselben Flugzeugs)."""
    path = track.get("path") or []
    if not path or (track.get("callsign") or "").strip().upper() != callsign:
        return None
    t, lat, lon, alt = path[0][:4]
    if lat is None or lon is None or alt is None or alt > MAX_START_ALT_M:
        return None
    return float(lat), float(lon)


class Origins:
    def __init__(self, session: aiohttp.ClientSession, cache: Cache, airports: list[tuple[str, float, float]]):
        self.session = session
        self.cache = cache
        self.airports = airports
        self.positions = {iata: (lat, lon) for iata, lat, lon in airports}
        self.last_error: str | None = None
        self.issues = None      # Issues, setzt main
        self.auth = None        # async () -> Header mit OpenSky-Token, setzt main, wenn Zugangsdaten da sind

    def position(self, iata: str) -> list[float] | None:
        p = self.positions.get(iata)
        return [p[0], p[1]] if p else None

    async def origin(self, icao24: str, callsign: str) -> dict:
        """{"origin": "ORY", "origin_pos": [lat, lon]} oder {}."""
        if not (self.airports and icao24 and callsign):
            return {}
        key = f"trk:{icao24}:{callsign}"
        hit = self.cache.get(key)
        if hit is not None:
            return hit
        try:
            headers = await self.auth() if self.auth else {}
            async with self.session.get(TRACK_URL, params={"icao24": icao24, "time": "0"}, headers=headers,
                                        timeout=aiohttp.ClientTimeout(total=15)) as r:
                if r.status == 404:
                    self.cache.set(key, {}, NEG_TTL)
                    return {}
                if r.status >= 400:
                    raise RuntimeError(f"HTTP {r.status}")
                track = await r.json(content_type=None)
            self.last_error = None
        except Exception as e:
            self.last_error = str(e) or type(e).__name__
            log.warning("OpenSky-Spur %s: %s", callsign, self.last_error)
            if self.issues is not None:
                self.issues.note("rate_limit" if "429" in self.last_error else "source_error", "opensky_track",
                                 error=self.last_error)
            return {}
        start = track_start(track, callsign)
        ap = nearest_airport(self.airports, *start) if start else None
        out = {"origin": ap[0], "origin_pos": [ap[1], ap[2]]} if ap else {}
        self.cache.set(key, out, TTL if out else NEG_TTL)
        if out:
            log.info("Abflughafen aus der Spur: %s %s", callsign, ap[0])
        return out
