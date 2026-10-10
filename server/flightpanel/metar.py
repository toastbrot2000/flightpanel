"""Wetter am Flughafen aus dem METAR (kostenlos, ohne Account): Wind, Sicht,
Wolkenuntergrenze, Wettererscheinungen. Für die Leerlauf-Karten "Wolken auf
600 m, Flieger versteckt", "Nebel" und die Pistenkarte.

Quellen der Reihe nach: aviationweather.gov, dann der NOAA-Dateiserver. Fallen
beide aus, bleibt der letzte Stand bis MAX_AGE_S gültig, danach liefern die
Wetterkarten nichts mehr und die übrigen Karten kommen dran."""

import logging
import re
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime

import aiohttp

log = logging.getLogger("metar")

URLS = [
    "https://aviationweather.gov/api/data/metar?ids={st}&format=raw",
    "https://tgftp.nws.noaa.gov/data/observations/metar/stations/{st}.TXT",
]
MAX_AGE_S = 90 * 60
FT_M = 0.3048

WX_NAMES = {"FG": "Nebel", "BR": "Dunst", "TS": "Gewitter", "SN": "Schnee", "RA": "Regen",
            "DZ": "Niesel", "GR": "Hagel", "GS": "Graupel", "FZ": "gefrierend", "SH": "Schauer",
            "HZ": "Dunst", "FU": "Rauch", "SQ": "Böen"}
_WX = re.compile(r"^(\+|-|VC)?((MI|BC|PR|DR|BL|SH|TS|FZ)?"
                 r"(DZ|RA|SN|SG|PL|GR|GS|UP|BR|FG|FU|VA|DU|SA|HZ|PY|PO|SQ|FC|SS|DS)*)$")
_WIND = re.compile(r"^(\d{3}|VRB)(\d{2,3})(?:G(\d{2,3}))?(KT|MPS)$")
_VAR = re.compile(r"^(\d{3})V(\d{3})$")
_CLOUD = re.compile(r"^(FEW|SCT|BKN|OVC|VV)(\d{3}|///)")
_TIME = re.compile(r"^(\d{2})(\d{2})(\d{2})Z$")


@dataclass
class Metar:
    raw: str
    obs_time: float | None = None
    wind_dir: int | None = None         # None bei VRB oder Windstille
    wind_kt: int | None = None
    gust_kt: int | None = None
    wind_var: tuple[int, int] | None = None
    vis_m: int | None = None
    ceiling_m: int | None = None        # tiefste BKN/OVC/VV-Schicht
    clouds: list[tuple[str, int]] = field(default_factory=list)
    wx: list[str] = field(default_factory=list)
    cavok: bool = False

    def has(self, code: str) -> bool:
        return any(code in w for w in self.wx)


def _obs_time(day: int, hh: int, mm: int, now: float) -> float:
    """Tag/Uhrzeit (UTC) aus dem METAR -> Zeitstempel; im laufenden Monat, sonst im Vormonat."""
    ref = datetime.fromtimestamp(now, tz=UTC)
    y, mo = ref.year, ref.month
    for _ in range(2):
        try:
            t = datetime(y, mo, day, hh, mm, tzinfo=UTC).timestamp()
            if t <= now + 3600:
                return t
        except ValueError:
            pass
        y, mo = (y, mo - 1) if mo > 1 else (y - 1, 12)
    return now


def parse(text: str, now: float | None = None) -> Metar | None:
    """Roh-METAR (auch mit Datumszeile davor, wie beim NOAA-Server) -> Metar.
    Trend (TEMPO, BECMG, NOSIG) und Bemerkungen (RMK) werden ignoriert."""
    now = time.time() if now is None else now
    line = next((ln.strip() for ln in text.splitlines()
                 if re.match(r"^(METAR |SPECI )?[A-Z]{4} \d{6}Z", ln.strip())), "")
    if not line:
        return None
    toks = line.split()
    if toks[0] in ("METAR", "SPECI"):
        toks = toks[1:]
    m = Metar(raw=line)
    for tok in toks[1:]:
        if tok in ("TEMPO", "BECMG", "NOSIG", "RMK"):
            break
        if (t := _TIME.match(tok)) and m.obs_time is None:
            m.obs_time = _obs_time(int(t[1]), int(t[2]), int(t[3]), now)
        elif w := _WIND.match(tok):
            f = 1.94384 if w[4] == "MPS" else 1.0
            m.wind_dir = None if w[1] == "VRB" else int(w[1])
            m.wind_kt = round(int(w[2]) * f)
            m.gust_kt = round(int(w[3]) * f) if w[3] else None
            if m.wind_kt == 0:
                m.wind_dir = None
        elif v := _VAR.match(tok):
            m.wind_var = (int(v[1]), int(v[2]))
        elif tok == "CAVOK":
            m.cavok, m.vis_m = True, 10000
        elif re.fullmatch(r"\d{4}", tok) and m.vis_m is None:
            m.vis_m = 10000 if tok == "9999" else int(tok)
        elif c := _CLOUD.match(tok):
            if c[2] != "///":
                h = round(int(c[2]) * 100 * FT_M)
                m.clouds.append((c[1], h))
                if c[1] in ("BKN", "OVC", "VV") and (m.ceiling_m is None or h < m.ceiling_m):
                    m.ceiling_m = h
        elif tok not in ("AUTO", "COR", "NSC", "NCD", "SKC", "CLR") and len(tok) >= 2 \
                and (x := _WX.match(tok)) and x[2]:
            m.wx.append(tok)
    return m


def describe_wx(m: Metar) -> str:
    """Wichtigste Wettererscheinung als Wort, z. B. "Gewitter", sonst ""."""
    for code in ("TS", "FG", "GR", "SN", "FZ", "SH", "RA", "DZ", "BR"):
        if m.has(code):
            return WX_NAMES[code]
    return ""


class MetarSource:
    def __init__(self, session: aiohttp.ClientSession | None, station: str, issues=None):
        self.session = session
        self.station = station
        self.issues = issues
        self.current: Metar | None = None
        self.last_ok: float = 0
        self.last_error: str | None = None

    async def refresh(self) -> Metar | None:
        errors = []
        for url in URLS:
            try:
                async with self.session.get(url.format(st=self.station),
                                            timeout=aiohttp.ClientTimeout(total=10)) as r:
                    if r.status >= 400:
                        raise RuntimeError(f"HTTP {r.status}")
                    text = await r.text()
                m = parse(text)
                if m is None:
                    raise RuntimeError("kein METAR in der Antwort")
                self.current, self.last_ok, self.last_error = m, time.time(), None
                return m
            except Exception as e:
                errors.append(f"{url.split('/')[2]}: {e or type(e).__name__}")
        self.last_error = "; ".join(errors)
        log.warning("METAR %s: %s", self.station, self.last_error)
        if self.issues is not None:
            self.issues.source_error("metar", self.last_error[:300])
        return None

    def fresh(self, now: float) -> Metar | None:
        m = self.current
        if m is None:
            return None
        t = m.obs_time or self.last_ok
        return m if now - t < MAX_AGE_S else None

    def age_s(self, now: float) -> int | None:
        m = self.current
        return round(now - (m.obs_time or self.last_ok)) if m else None
