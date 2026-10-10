"""Farben fürs Panel. Satt, kein Weiß als Textfarbe (Strom, Blendung)."""

ARR_C = (255, 120, 0)       # Ankunft, amber
DEP_C = (0, 210, 70)        # Abflug, grün
OVF_C = (70, 120, 230)      # Überflug, blau
INFO = (120, 150, 230)
MUTED = (150, 150, 150)
CLOCK = (140, 70, 0)
RED = (230, 30, 30)
GOLD = (230, 170, 20)
CYAN = (40, 190, 210)
PINK = (220, 60, 160)
BAND = (14, 14, 22)         # Sichtfeld im Radar
HORIZON = (40, 40, 40)

Color = tuple[int, int, int]


def dim(c: Color, f: float) -> Color:
    return tuple(max(0, min(255, int(v * f))) for v in c)
