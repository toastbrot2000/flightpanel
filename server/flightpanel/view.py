"""Was man vom Fenster aus sieht: Richtung, Höhenwinkel und Entfernung eines
Flugzeugs vom Standort aus, ob es im Sichtfeld liegt (VIEW_* in .env), und wann
es hineinfliegt, wenn es so weiterfliegt wie gerade."""

import math
from dataclasses import dataclass

from .config import Config
from .geo import bearing_deg, destination, elevation_deg, haversine_km
from .model import State


@dataclass
class Sighting:
    """Wo ein Flugzeug vom Standort aus zu sehen ist."""
    az: float          # Richtung, 0 = Nord, 90 = Ost
    elev: float        # Höhenwinkel über dem Horizont
    dist_km: float     # Entfernung über Grund
    slant_km: float    # Sichtlinie


def sighting(cfg: Config, lat: float, lon: float, alt_m: float | None) -> Sighting:
    d = haversine_km(cfg.home_lat, cfg.home_lon, lat, lon)
    h = (alt_m if alt_m is not None else 0.0) - cfg.home_alt_m
    return Sighting(az=bearing_deg(cfg.home_lat, cfg.home_lon, lat, lon), elev=elevation_deg(d, h),
                    dist_km=d, slant_km=math.hypot(d, h / 1000))


def in_view(cfg: Config, v: Sighting) -> bool:
    return ((v.az - cfg.view_from) % 360 <= cfg.view_span
            and cfg.view_min_elev <= v.elev <= cfg.view_max_elev
            and v.slant_km <= cfg.view_max_km)


def entry_eta(cfg: Config, s: State, horizon_s: float, step_s: float) -> tuple[float, float] | None:
    """(Sekunden, Richtung): wann und wo das Flugzeug ins Sichtfeld kommt, wenn es
    mit Geschwindigkeit, Kurs und Steigrate weiterfliegt; (0, jetzt) wenn es schon
    drin ist, None wenn nicht innerhalb von horizon_s."""
    alt = s.alt_m
    v = sighting(cfg, s.lat, s.lon, alt)
    if in_view(cfg, v):
        return 0.0, v.az
    if not (s.velocity_ms and s.track_deg is not None and alt is not None and horizon_s > 0 and step_s > 0):
        return None
    n = max(1, math.ceil(horizon_s / step_s - 1e-9))
    for i in range(1, n + 1):
        dt = min(horizon_s, step_s * i)
        lat, lon = destination(s.lat, s.lon, s.track_deg, s.velocity_ms * dt / 1000)
        w = sighting(cfg, lat, lon, max(0.0, alt + (s.vrate_ms or 0.0) * dt))
        if in_view(cfg, w):
            return dt, w.az
    return None


def visibility(cfg: Config, s: State, horizon_s: float) -> int:
    """2 = jetzt im Sichtfeld, 1 = kommt bis zum nächsten Abruf hinein, 0 = nicht.
    Die Vorschau zählt mit: Anflüge queren das Sichtfeld in wenigen Minuten, sonst
    wären sie schon durch, bevor sie auf dem Panel stehen."""
    e = entry_eta(cfg, s, horizon_s, horizon_s / 3)
    if e is None:
        return 0
    return 2 if e[0] == 0 else 1
