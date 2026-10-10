"""Zeichenhelfer für Fluglayouts (render.py) und Leerlauf-Karten (cards/).

Raster: drei Zeilen bei y = 0, 12, 24; die untere sitzt auf der Kante (Font.draw
rückt Unterlängen hoch). Farben kommen gedimmt an: f ist der Helligkeitsfaktor,
1.0 normal, weniger bei alten Daten. Text wird nie so abgeschnitten, dass sich der
Sinn ändert: erst kleinere Schrift, dann kürzere Fassung (Aufrufer), zuletzt kürzen."""

from PIL import Image

from .colors import ARR_C, DEP_C, OVF_C, Color, dim
from .fonts import FONT_MID, FONT_SMALL, Font
from .model import ARR, DEP, OVF

ROW1, ROW2, ROW3 = 0, 12, 24
STATUS_COLORS = {ARR: ARR_C, DEP: DEP_C, OVF: OVF_C}


def status_glyph(status: str) -> str:
    return "↓" if status == ARR else "↑" if status == DEP else "→"


def text(img: Image.Image, font: Font, x: int, y: int, s: str, c: Color, f: float, max_w: int | None = None) -> int:
    """Text in einer Schrift, auf max_w gekürzt. Gibt das x nach dem Text zurück."""
    if max_w is not None:
        s = font.fit(s, max_w)
    return font.draw(img, x, y, s, dim(c, f))


def line(img: Image.Image, x: int, y: int, s: str, c: Color, f: float, max_w: int | None = None) -> int:
    """Eine Textzeile in FONT_MID; passt sie nicht, in FONT_SMALL (bündig mit der
    Unterkante der Großbuchstaben), erst dann gekürzt."""
    max_w = (img.width - x) if max_w is None else max_w
    if FONT_MID.text_width(s) <= max_w:
        return FONT_MID.draw(img, x, y, s, dim(c, f))
    return FONT_SMALL.draw(img, x, y + FONT_MID.cap_height - FONT_SMALL.cap_height, FONT_SMALL.fit(s, max_w), dim(c, f))


def center(img: Image.Image, font: Font, y: int, s: str, c: Color, f: float, x0: int = 0,
           x1: int | None = None) -> None:
    x1 = img.width if x1 is None else x1
    s = font.fit(s, x1 - x0)
    font.draw(img, x0 + (x1 - x0 - font.text_width(s)) // 2, y, s, dim(c, f))


def right(img: Image.Image, font: Font, y: int, s: str, c: Color, f: float) -> None:
    """Rechtsbündig: der Text endet auf der letzten Spalte."""
    font.draw(img, img.width - font.text_width(s), y, s, dim(c, f))


def frame(img: Image.Image, c: Color, f: float) -> None:
    """Rahmen um das ganze Bild."""
    W, H = img.size
    c = dim(c, f)
    for x in range(W):
        img.putpixel((x, 0), c)
        img.putpixel((x, H - 1), c)
    for y in range(H):
        img.putpixel((0, y), c)
        img.putpixel((W - 1, y), c)


def blink(now: float) -> bool:
    """An/aus im Takt der ESP32-Abrufe (alle 2 s)."""
    return int(now // 2) % 2 == 0


def km(n: float) -> str:
    """12345 -> "12 345"."""
    return f"{int(round(n)):,}".replace(",", " ")


def minutes(s: float) -> str:
    """Sekunden -> "7 min", ab 90 min "1 h 45 min"."""
    m = max(1, round(s / 60))
    return f"{m // 60} h {m % 60:02d} min" if m >= 90 else f"{m} min"
