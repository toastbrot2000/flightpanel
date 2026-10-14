"""Himmel ohne Flugzeuge: Sonne und ISS, im selben Richtungs- und Höhenwinkel-
System wie die Flugzeuge (0° = Nord, 90° = Ost, Höhenwinkel über dem Horizont),
also passend zum geeichten Sichtfeld.

Sonne: rein gerechnet (Näherung nach dem Astronomical Almanac, ~0,01°), kein Netz.
ISS: Bahnelemente (TLE) von Celestrak, ersatzweise einer zweiten Quelle, einmal
am Tag; zwischengespeichert in der Datenbank (state) und bis 14 Tage nach ihrer
Epoche genutzt. Daraus mit SGP4 die Überflüge der nächsten 24 h, die man sehen kann:
ISS mindestens 10° hoch, von der Sonne beschienen, beim Beobachter schon dunkel.
Ohne gültige Bahnelemente gibt es keine ISS-Karten, sonst ändert sich nichts."""

import asyncio
import json
import logging
import math
import time
from dataclasses import dataclass
from datetime import datetime

import aiohttp

log = logging.getLogger("astro")

R_EARTH = 6371.0
WGS_A, WGS_F = 6378.137, 1 / 298.257223563
SUN_HORIZON = -0.833          # Sonnenauf-/-untergang (Refraktion + Radius)
DARK = -6.0                   # Sonne darunter: Himmel dunkel genug für die ISS
PASS_MIN_ELEV = 10.0
PASS_STEP_S = 20
PASS_HOURS = 24
TLE_MAX_AGE_D = 14
TLE_REFRESH_S = 24 * 3600
TLE_URLS = [
    "https://celestrak.org/NORAD/elements/gp.php?CATNR=25544&FORMAT=TLE",
    "https://tle.ivanstanojevic.me/api/tle/25544",
]


# ------------------------------------------------------------------ Sonne --
def _jd(t: float) -> float:
    return t / 86400.0 + 2440587.5


def gmst_rad(t: float) -> float:
    d = _jd(t) - 2451545.0
    return math.radians((280.46061837 + 360.98564736629 * d) % 360)


def sun_radec(t: float) -> tuple[float, float]:
    """Rektaszension und Deklination der Sonne in Radiant."""
    d = _jd(t) - 2451545.0
    g = math.radians((357.529 + 0.98560028 * d) % 360)
    q = (280.459 + 0.98564736 * d) % 360
    lam = math.radians(q + 1.915 * math.sin(g) + 0.020 * math.sin(2 * g))
    eps = math.radians(23.439 - 0.00000036 * d)
    ra = math.atan2(math.cos(eps) * math.sin(lam), math.cos(lam))
    dec = math.asin(math.sin(eps) * math.sin(lam))
    return ra, dec


def sun_position(t: float, lat: float, lon: float) -> tuple[float, float]:
    """(Richtung, Höhenwinkel) der Sonne in Grad."""
    ra, dec = sun_radec(t)
    h = gmst_rad(t) + math.radians(lon) - ra
    phi = math.radians(lat)
    elev = math.asin(math.sin(phi) * math.sin(dec) + math.cos(phi) * math.cos(dec) * math.cos(h))
    az = math.atan2(-math.sin(h), math.tan(dec) * math.cos(phi) - math.sin(phi) * math.cos(h))
    return math.degrees(az) % 360, math.degrees(elev)


def sun_crossing(t0: float, t1: float, lat: float, lon: float, elev: float) -> float | None:
    """Erster Zeitpunkt in [t0, t1], an dem die Sonne die Höhe elev quert."""
    step = 600
    prev_t, prev_e = t0, sun_position(t0, lat, lon)[1] - elev
    t = t0
    while t < t1:
        t = min(t1, t + step)
        e = sun_position(t, lat, lon)[1] - elev
        if (prev_e < 0) != (e < 0):
            a, b, ea = prev_t, t, prev_e
            for _ in range(20):
                m = (a + b) / 2
                em = sun_position(m, lat, lon)[1] - elev
                if (ea < 0) == (em < 0):
                    a, ea = m, em
                else:
                    b = m
            return (a + b) / 2
        prev_t, prev_e = t, e
    return None


_SUN_CACHE: dict[tuple, dict] = {}


def sun_day(now: float, lat: float, lon: float) -> dict:
    """Sonnenaufgang, -untergang und Richtungen für den lokalen Tag von now."""
    day = datetime.fromtimestamp(now).date()
    key = (day, round(lat, 3), round(lon, 3))
    hit = _SUN_CACHE.get(key)
    if hit is not None:
        return hit
    t0 = datetime.combine(day, datetime.min.time()).timestamp()
    noon = t0 + 12 * 3600
    rise = sun_crossing(t0, noon + 3600, lat, lon, SUN_HORIZON)
    sset = sun_crossing(noon - 3600, t0 + 86400, lat, lon, SUN_HORIZON)
    out = {"sunrise": rise, "sunset": sset,
           "sunrise_az": sun_position(rise, lat, lon)[0] if rise else None,
           "sunset_az": sun_position(sset, lat, lon)[0] if sset else None,
           # tief stehende Sonne: abends unter 8°, morgens bis 8°
           "golden_evening": sun_crossing(noon, t0 + 86400, lat, lon, 8.0),
           "golden_morning": sun_crossing(t0, noon, lat, lon, 8.0)}
    if len(_SUN_CACHE) > 8:
        _SUN_CACHE.clear()
    _SUN_CACHE[key] = out
    return out


# -------------------------------------------------------------------- ISS --
def _ecef_observer(lat: float, lon: float, alt_m: float) -> tuple[float, float, float]:
    phi, lam = math.radians(lat), math.radians(lon)
    e2 = WGS_F * (2 - WGS_F)
    n = WGS_A / math.sqrt(1 - e2 * math.sin(phi) ** 2)
    h = alt_m / 1000
    return ((n + h) * math.cos(phi) * math.cos(lam), (n + h) * math.cos(phi) * math.sin(lam),
            (n * (1 - e2) + h) * math.sin(phi))


def look_angles(lat: float, lon: float, alt_m: float, ecef: tuple[float, float, float]) -> tuple[float, float, float]:
    """(Richtung, Höhenwinkel, Entfernung km) eines Punkts in ECEF vom Beobachter aus."""
    ox, oy, oz = _ecef_observer(lat, lon, alt_m)
    dx, dy, dz = ecef[0] - ox, ecef[1] - oy, ecef[2] - oz
    phi, lam = math.radians(lat), math.radians(lon)
    e = -math.sin(lam) * dx + math.cos(lam) * dy
    n = -math.sin(phi) * math.cos(lam) * dx - math.sin(phi) * math.sin(lam) * dy + math.cos(phi) * dz
    u = math.cos(phi) * math.cos(lam) * dx + math.cos(phi) * math.sin(lam) * dy + math.sin(phi) * dz
    return math.degrees(math.atan2(e, n)) % 360, math.degrees(math.atan2(u, math.hypot(e, n))), \
        math.sqrt(dx * dx + dy * dy + dz * dz)


def teme_to_ecef(r: tuple[float, float, float], t: float) -> tuple[float, float, float]:
    g = gmst_rad(t)
    c, s = math.cos(g), math.sin(g)
    return c * r[0] + s * r[1], -s * r[0] + c * r[1], r[2]


def ecef_to_geodetic(x: float, y: float, z: float) -> tuple[float, float, float]:
    """(Breite, Länge, Höhe km) auf WGS84, iterativ."""
    e2 = WGS_F * (2 - WGS_F)
    lon = math.atan2(y, x)
    p = math.hypot(x, y)
    lat = math.atan2(z, p * (1 - e2))
    h = 0.0
    for _ in range(5):
        n = WGS_A / math.sqrt(1 - e2 * math.sin(lat) ** 2)
        h = p / math.cos(lat) - n
        lat = math.atan2(z, p * (1 - e2 * n / (n + h)))
    return math.degrees(lat), math.degrees(lon), h


def sunlit(r_teme: tuple[float, float, float], t: float) -> bool:
    """Ist der Satellit von der Sonne beschienen (zylindrischer Erdschatten)?"""
    ra, dec = sun_radec(t)
    s = (math.cos(dec) * math.cos(ra), math.cos(dec) * math.sin(ra), math.sin(dec))
    proj = sum(a * b for a, b in zip(r_teme, s, strict=True))
    if proj > 0:
        return True
    perp = math.sqrt(max(0.0, sum(a * a for a in r_teme) - proj * proj))
    return perp > R_EARTH


def tle_epoch(line1: str) -> float:
    yy = int(line1[18:20])
    doy = float(line1[20:32])
    year = 2000 + yy if yy < 57 else 1900 + yy
    return (datetime(year, 1, 1) - datetime(1970, 1, 1)).total_seconds() + (doy - 1) * 86400


def parse_tle(text: str) -> tuple[str, str] | None:
    """Zwei TLE-Zeilen aus Text oder aus JSON {"line1", "line2"}."""
    text = text.strip()
    if text.startswith("{"):
        try:
            j = json.loads(text)
            l1, l2 = j.get("line1", ""), j.get("line2", "")
        except Exception:
            return None
    else:
        lines = [ln.strip() for ln in text.splitlines()]
        l1 = next((ln for ln in lines if ln.startswith("1 ")), "")
        l2 = next((ln for ln in lines if ln.startswith("2 ")), "")
    if len(l1) >= 69 and len(l2) >= 69:
        return l1, l2
    return None


@dataclass
class Pass:
    start: float            # sichtbarer Teil: Anfang
    end: float              # und Ende
    max_elev: float
    max_time: float
    az_start: float
    az_end: float
    view_start: float | None = None    # erster Moment im Sichtfeld, falls überhaupt


class Iss:
    def __init__(self, cfg, db=None, session: aiohttp.ClientSession | None = None, issues=None,
                 tle_text: str | None = None):
        """db: Bahndaten aus der Datenbank (state: tle, tle_fetched); tle_text: fertige
        Bahndaten, für Tests."""
        self.cfg = cfg
        self.db = db
        self.session = session
        self.issues = issues
        self.tle: tuple[str, str] | None = None
        self.sat = None
        self.tle_fetched: float = 0
        self.passes: list[Pass] = []
        self.passes_at: float = 0
        self.last_error: str | None = None
        if tle_text is not None:
            self.set_tle_text(tle_text, time.time())
        elif db is not None:
            self._load()

    # ----------------------------------------------------------- Bahndaten --
    def _load(self) -> None:
        text = self.db.state_get("tle")
        tle = parse_tle(text) if text else None
        if tle:
            self._set(tle)
            self.tle_fetched = self.db.state_get("tle_fetched", 0)

    def set_tle_text(self, text: str, fetched: float) -> bool:
        """Bahndaten übernehmen (Celestrak-Antwort oder alte tle.txt) und merken."""
        tle = parse_tle(text)
        if not tle:
            return False
        self._set(tle)
        self.tle_fetched = fetched
        if self.db is not None:
            self.db.state_set("tle", "ISS (ZARYA)\n" + "\n".join(tle) + "\n")
            self.db.state_set("tle_fetched", fetched)
        return True

    def _set(self, tle: tuple[str, str]) -> None:
        from sgp4.api import Satrec
        self.sat = Satrec.twoline2rv(*tle)
        self.tle = tle
        self.passes_at = 0

    def usable(self, now: float) -> bool:
        return self.sat is not None and now - tle_epoch(self.tle[0]) < TLE_MAX_AGE_D * 86400

    async def refresh_tle(self) -> bool:
        errors = []
        for url in TLE_URLS:
            try:
                async with self.session.get(url, timeout=aiohttp.ClientTimeout(total=15)) as r:
                    if r.status >= 400:
                        raise RuntimeError(f"HTTP {r.status}")
                    text = await r.text()
                if not self.set_tle_text(text, time.time()):
                    raise RuntimeError("keine Bahnelemente in der Antwort")
                self.last_error = None
                return True
            except Exception as e:
                errors.append(f"{url.split('/')[2]}: {e or type(e).__name__}")
        self.last_error = "; ".join(errors)
        log.warning("ISS-Bahnelemente: %s", self.last_error)
        if self.issues is not None:
            self.issues.source_error("tle", self.last_error[:300])
        return False

    def needs_tle(self, now: float) -> bool:
        return self.sat is None or now - self.tle_fetched > TLE_REFRESH_S

    # ------------------------------------------------------------- Rechnen --
    def state(self, t: float) -> tuple[float, float, float, bool] | None:
        """(Richtung, Höhenwinkel, Entfernung km, beschienen) vom Standort aus."""
        if self.sat is None:
            return None
        days = t / 86400.0
        jd, fr = 2440587.5 + math.floor(days), days - math.floor(days)
        err, r, _ = self.sat.sgp4(jd, fr)
        if err != 0:
            return None
        az, el, rng = look_angles(self.cfg.home_lat, self.cfg.home_lon, self.cfg.home_alt_m, teme_to_ecef(r, t))
        return az, el, rng, sunlit(r, t)

    def subpoint(self, t: float) -> tuple[float, float, float] | None:
        """(Breite, Länge, Höhe km) unter der ISS."""
        if self.sat is None:
            return None
        days = t / 86400.0
        err, r, _ = self.sat.sgp4(2440587.5 + math.floor(days), days - math.floor(days))
        return None if err else ecef_to_geodetic(*teme_to_ecef(r, t))

    def _visible(self, t: float, st) -> bool:
        return (st is not None and st[1] >= PASS_MIN_ELEV and st[3]
                and sun_position(t, self.cfg.home_lat, self.cfg.home_lon)[1] < DARK)

    def _in_view(self, az: float, el: float) -> bool:
        c = self.cfg
        return (az - c.view_from) % 360 <= c.view_span and c.view_min_elev <= el <= c.view_max_elev

    def compute_passes(self, start: float, hours: float = PASS_HOURS) -> list[Pass]:
        """Sichtbare Überflüge ab start, in PASS_STEP_S-Schritten."""
        out: list[Pass] = []
        cur: Pass | None = None
        t, end = start, start + hours * 3600
        while t <= end:
            st = self.state(t)
            if self._visible(t, st):
                az, el = st[0], st[1]
                if cur is None:
                    cur = Pass(start=t, end=t, max_elev=el, max_time=t, az_start=az, az_end=az)
                cur.end, cur.az_end = t, az
                if el > cur.max_elev:
                    cur.max_elev, cur.max_time = el, t
                if cur.view_start is None and self._in_view(az, el):
                    cur.view_start = t
            elif cur is not None:
                out.append(cur)
                cur = None
            t += PASS_STEP_S
        if cur is not None:
            out.append(cur)
        return out

    async def update_passes(self, now: float) -> None:
        if not self.usable(now):
            self.passes = []
            return
        if now - self.passes_at < 3600:
            return
        self.passes = await asyncio.to_thread(self.compute_passes, now - 600)
        self.passes_at = now
        log.info("ISS: %d sichtbare Überflüge in %d h", len(self.passes), PASS_HOURS)

    def next_pass(self, now: float) -> Pass | None:
        if not self.usable(now):
            return None
        return next((p for p in self.passes if p.end >= now), None)


def hhmm(t: float) -> str:
    return datetime.fromtimestamp(t).strftime("%H:%M")
