"""Himmel ohne Flugzeuge: Sonne und ISS, im selben Richtungs- und Höhenwinkel-
System wie die Flugzeuge (0° = Nord, 90° = Ost, Höhenwinkel über dem Horizont),
also passend zum geeichten Sichtfeld.

Sonne: rein gerechnet (Näherung nach dem Astronomical Almanac, ~0,01°), kein Netz.
Satelliten (ISS, Tiangong: WANTED): Bahnelemente (TLE) von Celestrak, für die ISS
ersatzweise einer zweiten Quelle, einmal am Tag für alle Panels (Catalog, je
Satellit ein Orbit); zwischengespeichert in der Datenbank (state) und bis 14 Tage
nach ihrer Epoche genutzt. Daraus je Fenster und Satellit (Tracker) mit SGP4 die
Überflüge der nächsten 24 h, die man sehen kann: mindestens 10° hoch, von der
Sonne beschienen, beim Beobachter schon dunkel. Ohne gültige Bahnelemente gibt es
keine Satelliten-Karten, sonst ändert sich nichts."""

import asyncio
import json
import logging
import math
import time
from dataclasses import asdict, dataclass
from datetime import UTC, datetime

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
ISS, TIANGONG = 25544, 48274          # NORAD-Nummern
WANTED = {ISS: "ISS", TIANGONG: "Tiangong"}     # was der Katalog aus den Quellen behält (cards/sky.py SATS)
STATIONS_URL = "https://celestrak.org/NORAD/elements/gp.php?GROUP=stations&FORMAT=TLE"   # ISS, Tiangong, ...
ISS_FALLBACK_URL = "https://tle.ivanstanojevic.me/api/tle/25544"
TLE_URLS = [STATIONS_URL, ISS_FALLBACK_URL]
STARLINK_URL = "https://celestrak.org/NORAD/elements/gp.php?GROUP=starlink&FORMAT=TLE"   # Ketten (find_trains)
TRAIN_MIN_N = 10              # so viele Satelliten mindestens in einer Kette
TRAIN_MAX_SPAN_DEG = 60.0     # längs der Bahn höchstens so weit auseinander, sonst keine Kette mehr
TRAIN_NEWEST = 12             # so viele der jüngsten Starts des Jahres werden angesehen


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
SUN_CACHE_MAX = 64      # Platz für viele Fenster (Panels) über ein paar Tage


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
    if len(_SUN_CACHE) >= SUN_CACHE_MAX:
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


def norad_of(line2: str) -> int:
    """Die NORAD-Nummer aus Zeile 2 (Spalten 3-7)."""
    return int(line2[2:7])


def parse_tles(text: str) -> list[tuple[str, str, str]]:
    """Alle Satelliten aus Text (3-zeilig mit Namenszeile wie Celestrak, oder nur die zwei Zeilen) oder aus JSON
    {"name", "line1", "line2"}: (Name, Zeile 1, Zeile 2) je Satellit, der Name leer, wenn keiner dasteht."""
    text = text.strip()
    if text.startswith("{"):
        try:
            j = json.loads(text)
            l1, l2 = j.get("line1", ""), j.get("line2", "")
        except Exception:
            return []
        return [(str(j.get("name", "")), l1, l2)] if len(l1) >= 69 and len(l2) >= 69 else []
    out: list[tuple[str, str, str]] = []
    name, l1 = "", ""
    for raw in text.splitlines():
        ln = raw.strip()
        if ln.startswith("1 ") and len(ln) >= 69:
            l1 = ln
        elif ln.startswith("2 ") and len(ln) >= 69 and l1:
            try:
                norad_of(ln)
            except ValueError:
                continue
            out.append((name, l1, ln))
            name, l1 = "", ""
        elif ln:
            name, l1 = (ln[2:].strip() if ln.startswith("0 ") else ln), ""
    return out


def parse_tle(text: str) -> tuple[str, str] | None:
    """Die zwei TLE-Zeilen des ersten Satelliten im Text (parse_tles)."""
    sats = parse_tles(text)
    return (sats[0][1], sats[0][2]) if sats else None


@dataclass
class Pass:
    start: float            # sichtbarer Teil: Anfang
    end: float              # und Ende
    max_elev: float
    max_time: float
    az_start: float
    az_end: float
    view_start: float | None = None    # erster Moment im Sichtfeld, falls überhaupt


class Orbit:
    """Die Bahndaten eines Satelliten (TLE) und sein Ort daraus (SGP4): einer je Satellit, für alle Panels gemeinsam
    (Catalog). version zählt neue Bahndaten: Ein Tracker rechnet seine Überflüge dann neu. Ohne Bahndaten (ein
    Platzhalter, bis die Quelle sie liefert) ist er nicht usable."""

    def __init__(self, name: str = "", tle: tuple[str, str] | None = None, tle_text: str | None = None):
        self.name = name
        self.tle: tuple[str, str] | None = None
        self.sat = None
        self.version = 0
        if tle is not None:
            self._set(tle)
        elif tle_text is not None:
            self.set_tle_text(tle_text)

    @property
    def norad(self) -> int:
        return norad_of(self.tle[1]) if self.tle else 0

    def set_tle_text(self, text: str, fetched: float | None = None) -> bool:
        """Bahndaten aus Text übernehmen (die ersten zwei TLE-Zeilen darin)."""
        tle = parse_tle(text)
        if not tle:
            return False
        self._set(tle)
        return True

    def _set(self, tle: tuple[str, str]) -> None:
        from sgp4.api import Satrec
        self.sat = Satrec.twoline2rv(*tle)
        self.tle = tle
        self.version += 1

    def usable(self, now: float) -> bool:
        return self.sat is not None and now - tle_epoch(self.tle[0]) < TLE_MAX_AGE_D * 86400

    def position(self, t: float) -> tuple[float, float, float] | None:
        """Ort des Satelliten zur Zeit t (TEME, km); None ohne Bahndaten oder wenn SGP4 scheitert."""
        if self.sat is None:
            return None
        days = t / 86400.0
        err, r, _ = self.sat.sgp4(2440587.5 + math.floor(days), days - math.floor(days))
        return None if err else r


class Catalog:
    """Die Bahndaten aller Satelliten, die Karten zeigen (WANTED), einmal für alle Panels: von Celestrak (Gruppe
    stations: ISS und Tiangong), für die ISS ersatzweise eine zweite Quelle, einmal am Tag; in der Datenbank (state
    tle_sats). Der alte Schlüssel tle (nur die ISS) sät den Katalog einmalig, sodass die ISS über das Update hinweg
    läuft, auch ohne Netz. Wo ein Satellit von einem Fenster aus steht und wann man ihn dort sieht, rechnet Tracker."""

    def __init__(self, db=None, session: aiohttp.ClientSession | None = None, issues=None,
                 sources: list[str] | None = None, trains_url: str | None = None):
        """trains_url: die Starlink-Liste für frische Ketten (STARLINK_URL); ohne gibt es keine."""
        self.db = db
        self.session = session
        self.issues = issues
        self.sources = list(sources if sources is not None else TLE_URLS)
        self.trains_url = trains_url
        self.year: int | None = None    # Startjahr für find_trains (Tests), sonst das aktuelle
        self.orbits: dict[int, Orbit] = {}
        self.trains: list[Train] = []
        self.trains_version = 0         # zählt neue Ketten: TrainTracker rechnet dann neu
        self.fetched: float = 0
        self.version = 0                # zählt neue Bahndaten irgendeines Satelliten
        self.last_error: str | None = None
        if db is not None:
            self._load()

    def _load(self) -> None:
        doc = self.db.state_get("tle_sats")
        if isinstance(doc, dict):
            for s in doc.get("sats", {}).values():
                try:
                    self._take(s["name"], s["l1"], s["l2"])
                except (KeyError, TypeError, ValueError):
                    continue
            self.fetched = doc.get("fetched", 0) or 0
            for t in doc.get("trains", []):
                try:
                    self.trains.append(Train(**t))
                except TypeError:
                    continue
            return
        text = self.db.state_get("tle")                 # Altbestand: nur die ISS
        if text:
            self.set_tle_text(text, self.db.state_get("tle_fetched", 0) or 0)

    def _take(self, name: str, l1: str, l2: str) -> int:
        """Bahndaten eines Satelliten übernehmen: in sein Orbit-Objekt, damit Tracker, die es halten, neu rechnen."""
        norad = norad_of(l2)
        o = self.orbits.get(norad)
        if o is None:
            self.orbits[norad] = Orbit(name or WANTED.get(norad, ""), (l1, l2))
        else:
            o._set((l1, l2))
            if name:
                o.name = name
        self.version += 1
        return norad

    def orbit_for(self, norad: int) -> Orbit:
        """Das Orbit-Objekt eines Satelliten, notfalls ein Platzhalter ohne Bahndaten (bis die Quelle sie liefert)."""
        o = self.orbits.get(norad)
        if o is None:
            o = self.orbits[norad] = Orbit(WANTED.get(norad, ""))
        return o

    def get(self, norad: int) -> Orbit | None:
        o = self.orbits.get(norad)
        return o if o is not None and o.tle else None

    def set_tle_text(self, text: str, fetched: float) -> bool:
        """Bahndaten aus Text übernehmen (Antwort einer Quelle oder eine alte tle.txt), beliebig viele Satelliten
        darin, nur die gewollten (WANTED); merken."""
        taken = [self._take(n, l1, l2) for n, l1, l2 in parse_tles(text) if norad_of(l2) in WANTED]
        if not taken:
            return False
        self.fetched = fetched
        self._save()
        return True

    def _save(self) -> None:
        if self.db is None:
            return
        sats = {str(n): {"name": o.name, "l1": o.tle[0], "l2": o.tle[1]} for n, o in self.orbits.items() if o.tle}
        self.db.state_set("tle_sats", {"fetched": self.fetched, "sats": sats,
                                       "trains": [asdict(t) for t in self.trains]})

    def usable(self, now: float) -> bool:
        return any(o.usable(now) for o in self.orbits.values())

    def needs_tle(self, now: float) -> bool:
        return not any(o.tle for o in self.orbits.values()) or now - self.fetched > TLE_REFRESH_S

    async def refresh(self) -> bool:
        """Alle Quellen der Reihe nach; die Ersatzquelle für die ISS nur, wenn die Gruppe sie nicht brachte. Ein
        Teilerfolg zählt: Was eine Quelle liefert, gilt, der Ausfall der anderen steht in last_error und /issues."""
        errors: list[str] = []
        fresh: set[int] = set()
        for url in self.sources:
            if url == ISS_FALLBACK_URL and ISS in fresh:
                continue
            try:
                async with self.session.get(url, timeout=aiohttp.ClientTimeout(total=15)) as r:
                    if r.status >= 400:
                        raise RuntimeError(f"HTTP {r.status}")
                    text = await r.text()
                sats = [(n, l1, l2) for n, l1, l2 in parse_tles(text) if norad_of(l2) in WANTED]
                if not sats:
                    raise RuntimeError("keine Bahnelemente in der Antwort")
                for n, l1, l2 in sats:
                    fresh.add(self._take(n, l1, l2))
            except Exception as e:
                errors.append(f"{url.split('/')[2]}: {e or type(e).__name__}")
        if self.trains_url:
            try:
                async with self.session.get(self.trains_url, timeout=aiohttp.ClientTimeout(total=30)) as r:
                    if r.status >= 400:
                        raise RuntimeError(f"HTTP {r.status}")
                    text = await r.text()
                trains = find_trains(parse_tles(text), time.time(), self.year)
                if [asdict(t) for t in trains] != [asdict(t) for t in self.trains]:
                    self.trains, self.trains_version = trains, self.trains_version + 1
                    log.info("Starlink-Ketten: %s", ", ".join(f"{t.key} ({t.n})" for t in trains) or "keine")
                fresh.add(0)
            except Exception as e:
                errors.append(f"{self.trains_url.split('/')[2]} (starlink): {e or type(e).__name__}")
        if fresh:
            self.fetched = time.time()
            self._save()
        self.last_error = "; ".join(errors) or None
        if errors:
            log.warning("Bahnelemente: %s", self.last_error)
            if self.issues is not None:
                self.issues.source_error("tle", self.last_error[:300])
        return bool(fresh)

    async def refresh_tle(self) -> bool:
        return await self.refresh()


class Tracker:
    """Ein Satellit von einem Fenster aus (cfg: Standort und Sichtfeld eines Panels), aus den gemeinsamen Bahndaten
    (orbit): wo er gerade steht und die Überflüge der nächsten 24 h, die man von dort sieht."""

    def __init__(self, cfg, orbit: Orbit):
        self.cfg = cfg
        self.orbit = orbit
        self.passes: list[Pass] = []
        self.passes_at: float = 0       # 0: beim nächsten update_passes neu rechnen (auch nach neuen Einstellungen)
        self._version = -1              # aus welchem Stand der Bahndaten passes stammt

    def usable(self, now: float) -> bool:
        return self.orbit.usable(now)

    def state(self, t: float) -> tuple[float, float, float, bool] | None:
        """(Richtung, Höhenwinkel, Entfernung km, beschienen) vom Standort aus."""
        r = self.orbit.position(t)
        if r is None:
            return None
        az, el, rng = look_angles(self.cfg.home_lat, self.cfg.home_lon, self.cfg.home_alt_m, teme_to_ecef(r, t))
        return az, el, rng, sunlit(r, t)

    def subpoint(self, t: float) -> tuple[float, float, float] | None:
        """(Breite, Länge, Höhe km) unter dem Satelliten."""
        r = self.orbit.position(t)
        return None if r is None else ecef_to_geodetic(*teme_to_ecef(r, t))

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
        if now - self.passes_at < 3600 and self._version == self.orbit.version:
            return
        version = self.orbit.version
        self.passes = await asyncio.to_thread(self.compute_passes, now - 600)
        self.passes_at, self._version = now, version
        log.info("%s: %d sichtbare Überflüge in %d h", self.orbit.name or "Satellit", len(self.passes), PASS_HOURS)

    def next_pass(self, now: float) -> Pass | None:
        if not self.usable(now):
            return None
        return next((p for p in self.passes if p.end >= now), None)


Iss = Tracker          # der alte Name, für Tests und Altlasten


# ------------------------------------------------------------------------------- Starlink-Ketten --
@dataclass
class Train:
    """Eine frische Starlink-Kette: der Start (key, YYNNN), wie viele Satelliten, wie weit sie längs der Bahn
    auseinanderliegen (Grad), und der Vertreter in der Mitte mit seinen Bahndaten."""
    key: str
    n: int
    span: float
    name: str
    l1: str
    l2: str

    def orbit(self) -> Orbit:
        return Orbit(self.name, (self.l1, self.l2))


def _unit(r) -> tuple[float, float, float]:
    d = math.sqrt(r[0] * r[0] + r[1] * r[1] + r[2] * r[2]) or 1.0
    return r[0] / d, r[1] / d, r[2] / d


def _angle(a, b) -> float:
    return math.degrees(math.acos(max(-1.0, min(1.0, a[0] * b[0] + a[1] * b[1] + a[2] * b[2]))))


def find_trains(sats: list[tuple[str, str, str]], now: float, year: int | None = None) -> list[Train]:
    """Frische Starlink-Ketten in einer Celestrak-Liste: unter den jüngsten Starts des Jahres (internationale
    Bezeichnung YYNNN) die Gruppen ab TRAIN_MIN_N Satelliten, deren Orte zur Zeit now (SGP4) längs der Bahn
    höchstens TRAIN_MAX_SPAN_DEG auseinanderliegen: Erst dann sieht man sie als Lichterreihe. Nach Tagen verteilen
    sie sich über die ganze Bahn, dann ist es keine Kette mehr. Mittlere Anomalien aus den TLE taugen dafür
    nicht: Jede hat ihre eigene Epoche. year: das Startjahr (Tests), sonst das von now."""
    if year is None:
        year = datetime.fromtimestamp(now, UTC).year
    yy = f"{year % 100:02d}"
    groups: dict[str, list[tuple[str, str, str]]] = {}
    for name, l1, l2 in sats:
        if name.startswith("STARLINK") and l1[9:11] == yy:
            groups.setdefault(l1[9:14], []).append((name, l1, l2))
    out: list[Train] = []
    for key in sorted(groups, reverse=True)[:TRAIN_NEWEST]:
        g = groups[key]
        if len(g) < TRAIN_MIN_N:
            continue
        pts = []
        for name, l1, l2 in g:
            r = Orbit(name, (l1, l2)).position(now)
            if r is not None:
                pts.append((name, l1, l2, _unit(r)))
        if len(pts) < TRAIN_MIN_N:
            continue
        span, a, b = max((_angle(p[3], q[3]), p, q) for p in pts for q in pts)
        if span > TRAIN_MAX_SPAN_DEG:
            continue
        mid = min(pts, key=lambda p: abs(_angle(p[3], a[3]) - _angle(p[3], b[3])))     # in der Mitte der Kette
        out.append(Train(key, len(pts), round(span, 1), mid[0], mid[1], mid[2]))
    return out


class TrainTracker:
    """Die Starlink-Ketten (Catalog.trains) von einem Fenster aus, mit der Schnittstelle eines Trackers (cards/sky.py):
    der nächste Überflug irgendeiner Kette, ihr Stand, wie viele Satelliten sie hat (count). Rechnet neu, sobald
    der Katalog andere Ketten hat (trains_version)."""

    def __init__(self, cfg, catalog: "Catalog"):
        self.cfg = cfg
        self.catalog = catalog
        self.trackers: list[Tracker] = []
        self.trains: list[Train] = []
        self.passes_at: float = 0
        self._version = -1
        self._current: Train | None = None
        self._sync()

    def _sync(self) -> None:
        if self._version != self.catalog.trains_version:
            self.trains = list(self.catalog.trains)
            self.trackers = [Tracker(self.cfg, t.orbit()) for t in self.trains]
            self._version = self.catalog.trains_version
            self._current = None

    @property
    def orbit(self) -> Orbit | None:
        return self.trackers[0].orbit if self.trackers else None

    @property
    def passes(self) -> list[Pass]:
        return sorted((p for t in self.trackers for p in t.passes), key=lambda p: p.start)

    @property
    def count(self) -> int:
        """Satelliten der Kette, die als Nächstes kommt (nach next_pass), sonst der ersten."""
        if self._current is not None:
            return self._current.n
        return self.trains[0].n if self.trains else 0

    def usable(self, now: float) -> bool:
        return any(t.usable(now) for t in self.trackers)

    async def update_passes(self, now: float) -> None:
        self._sync()
        for t in self.trackers:
            t.cfg = self.cfg
            if self.passes_at == 0:
                t.passes_at = 0
            await t.update_passes(now)
        self.passes_at = now

    def _next(self, now: float):
        best = None
        for t, train in zip(self.trackers, self.trains, strict=True):
            p = t.next_pass(now)
            if p is not None and (best is None or p.start < best[0].start):
                best = (p, t, train)
        self._current = best[2] if best else None
        return best

    def next_pass(self, now: float) -> Pass | None:
        b = self._next(now)
        return b[0] if b else None

    def state(self, now: float):
        b = self._next(now)
        return b[1].state(now) if b else None


def hhmm(t: float) -> str:
    return datetime.fromtimestamp(t).strftime("%H:%M")
