"""adsb.lol: Community-Netz (ADS-B und MLAT), ohne Account und ohne Tageslimit.
Liefert neben der Position auch den Flugzeugtyp (t) und die ADS-B-Kategorie
(A7 = Hubschrauber), die OpenSky nicht hat.

Rate-Limit pro IP: Bei zu vielen Abrufen kommt HTTP 429 (vorher bis zu einige
Sekunden verzögert). Dann Pause, 60 s, bei Wiederholung doppelt, bis 10 min.

Hinter api.adsb.lol stehen mehrere Server (DNS mit kurzer TTL, wechselnde
Reihenfolge). Im Oktober 2026 brauchte einer davon 5 bis 17 s oder antwortete mit
429, die anderen 0,1 s; jeder zwanzigste Abruf lief in den Timeout. Darum nach
kurzem Warten ein zweiter Versuch: aiohttp reicht die Adressen aus seinem DNS-Cache
reihum weiter, der zweite Versuch geht also an einen anderen Server."""

import logging
import math
import time

import aiohttp

from .issues import error_text
from .model import State

log = logging.getLogger("adsblol")

POINT_URL = "https://api.adsb.lol/v2/point/{lat:.4f}/{lon:.4f}/{nm}"
MAX_POS_AGE_S = 30      # ältere Positionen verwerfen
BACKOFF_MIN, BACKOFF_MAX = 60, 600
ATTEMPT_TIMEOUTS_S = (5, 8)     # erster Versuch, zweiter an einen anderen Server
RETRY_ON = (TimeoutError, aiohttp.ClientConnectionError)   # 429 nie: das ist die Bremse


class Paused(RuntimeError):
    """In der Pause nach einem 429; kein neuer Fehler."""
FT, KT, FPM = 0.3048, 0.514444, 0.00508


def _num(v) -> float | None:
    return float(v) if isinstance(v, (int, float)) else None


def parse(ac: dict) -> State | None:
    """Ein Eintrag aus "ac" -> State, oder None ohne aktuelle Position."""
    lat, lon = _num(ac.get("lat")), _num(ac.get("lon"))
    if lat is None or lon is None or (_num(ac.get("seen_pos")) or 0) > MAX_POS_AGE_S:
        return None
    baro = ac.get("alt_baro")
    vrate = _num(ac.get("baro_rate"))
    if vrate is None:
        vrate = _num(ac.get("geom_rate"))
    track = _num(ac.get("track"))
    if track is None:
        track = _num(ac.get("true_heading"))
    gs, geom = _num(ac.get("gs")), _num(ac.get("alt_geom"))
    return State(
        icao24=str(ac.get("hex", "")).lower().lstrip("~"),
        callsign=str(ac.get("flight") or "").strip().upper(),
        lon=lon, lat=lat,
        baro_alt_m=_num(baro) * FT if _num(baro) is not None else None,
        on_ground=baro == "ground",
        velocity_ms=gs * KT if gs is not None else None,
        track_deg=track,
        vrate_ms=vrate * FPM if vrate is not None else None,
        geo_alt_m=geom * FT if geom is not None else None,
        actype=str(ac.get("t") or "").upper(),
        category=str(ac.get("category") or "").upper(),
        squawk=str(ac.get("squawk") or ""),
        registration=str(ac.get("r") or "").strip().upper(),
    )


class AdsbLol:
    def __init__(self, session: aiohttp.ClientSession):
        self.session = session
        self.last_ok: float = 0
        self.last_error: str | None = None
        self.backoff_until: float = 0
        self._backoff = 0

    async def fetch(self, lat: float, lon: float, radius_km: float) -> list[State]:
        """Flugzeuge im Umkreis. Hängt der erste Versuch, ein zweiter (siehe oben).
        Wirft bei Fehlern, damit der Aufrufer auf OpenSky ausweichen kann."""
        wait = self.backoff_until - time.time()
        if wait > 0:
            raise Paused(f"Pause nach Rate-Limit, noch {wait:.0f} s")
        nm = max(1, min(250, math.ceil(radius_km / 1.852)))
        url = POINT_URL.format(lat=lat, lon=lon, nm=nm)
        for attempt, limit in enumerate(ATTEMPT_TIMEOUTS_S, 1):
            try:
                j = await self._get(url, limit)
                break
            except Exception as e:
                self.last_error = error_text(e)
                if attempt == len(ATTEMPT_TIMEOUTS_S) or not isinstance(e, RETRY_ON):
                    raise
                log.info("adsb.lol: %s, zweiter Versuch", self.last_error)
        self.last_ok = time.time()
        self.last_error = None
        self._backoff = 0
        return [s for s in (parse(ac) for ac in j.get("ac") or []) if s is not None]

    async def _get(self, url: str, limit: float) -> dict:
        async with self.session.get(url, timeout=aiohttp.ClientTimeout(total=limit)) as r:
            if r.status == 429:
                self._backoff = min(BACKOFF_MAX, max(BACKOFF_MIN, self._backoff * 2))
                self.backoff_until = time.time() + self._backoff
                raise RuntimeError(f"HTTP 429, Pause {self._backoff} s")
            if r.status >= 400:
                raise RuntimeError(f"HTTP {r.status}")
            return await r.json(content_type=None)
