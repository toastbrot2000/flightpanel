"""adsb.lol: Community-Netz (ADS-B und MLAT), ohne Account und ohne Tageslimit.
Liefert neben der Position auch den Flugzeugtyp (t) und die ADS-B-Kategorie
(A7 = Hubschrauber), die OpenSky nicht hat."""

import logging
import math
import time

import aiohttp

from .opensky import State

log = logging.getLogger("adsblol")

POINT_URL = "https://api.adsb.lol/v2/point/{lat:.4f}/{lon:.4f}/{nm}"
MAX_POS_AGE_S = 30      # ältere Positionen verwerfen
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
    )


class AdsbLol:
    def __init__(self, session: aiohttp.ClientSession):
        self.session = session
        self.last_ok: float = 0
        self.last_error: str | None = None

    async def fetch(self, lat: float, lon: float, radius_km: float) -> list[State]:
        """Flugzeuge im Umkreis. Wirft bei Fehlern, damit der Aufrufer auf
        OpenSky ausweichen kann."""
        nm = max(1, min(250, math.ceil(radius_km / 1.852)))
        try:
            async with self.session.get(POINT_URL.format(lat=lat, lon=lon, nm=nm),
                                        timeout=aiohttp.ClientTimeout(total=10)) as r:
                if r.status >= 400:
                    raise RuntimeError(f"HTTP {r.status}")
                j = await r.json(content_type=None)
        except Exception as e:
            self.last_error = str(e) or type(e).__name__
            raise
        self.last_ok = time.time()
        self.last_error = None
        return [s for s in (parse(ac) for ac in j.get("ac") or []) if s is not None]
