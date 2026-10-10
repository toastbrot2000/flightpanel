"""Geometrie auf der Erdkugel und Einheiten der Luftfahrt."""

import math

EARTH_KM = 6371.0

# Luftfahrt rechnet in Fuß und Knoten, die Quellen teils in Metern
M_PER_FT = 0.3048          # Fuß -> Meter
FT_PER_M = 3.28084         # Meter -> Fuß
KT_PER_MS = 1.94384        # m/s -> Knoten
MS_PER_KT = 0.514444       # Knoten -> m/s
FPM_PER_MS = 196.85        # m/s -> Fuß pro Minute
MS_PER_FPM = 0.00508       # Fuß pro Minute -> m/s


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = p2 - p1
    dl = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_KM * math.asin(math.sqrt(a))


def bearing_deg(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Richtung von Punkt 1 zu Punkt 2, 0 = Nord, 90 = Ost."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dl = math.radians(lon2 - lon1)
    x = math.sin(dl) * math.cos(p2)
    y = math.cos(p1) * math.sin(p2) - math.sin(p1) * math.cos(p2) * math.cos(dl)
    return (math.degrees(math.atan2(x, y)) + 360) % 360


def angle_diff(a: float, b: float) -> float:
    """Kleinster Winkel zwischen zwei Richtungen, 0 bis 180."""
    return abs((a - b + 180) % 360 - 180)


def kompass(deg: float) -> str:
    """Himmelsrichtung auf Deutsch, O statt E."""
    return ["N", "NO", "O", "SO", "S", "SW", "W", "NW"][int((deg + 22.5) // 45) % 8]


def destination(lat: float, lon: float, bearing: float, dist_km: float) -> tuple[float, float]:
    """Punkt in dist_km Entfernung in Richtung bearing (Koppelnavigation)."""
    p1, l1, b = math.radians(lat), math.radians(lon), math.radians(bearing)
    d = dist_km / EARTH_KM
    p2 = math.asin(math.sin(p1) * math.cos(d) + math.cos(p1) * math.sin(d) * math.cos(b))
    l2 = l1 + math.atan2(math.sin(b) * math.sin(d) * math.cos(p1), math.cos(d) - math.sin(p1) * math.sin(p2))
    return math.degrees(p2), math.degrees(l2)


def elevation_deg(dist_km: float, height_m: float) -> float:
    """Höhenwinkel über dem Horizont für ein Objekt height_m über dem Beobachter,
    mit Erdkrümmung und Standardrefraktion (k = 0,13)."""
    d_m = max(dist_km, 0.001) * 1000
    drop_m = d_m ** 2 / (2 * EARTH_KM * 1000 / (1 - 0.13))
    return math.degrees(math.atan2(height_m - drop_m, d_m))
