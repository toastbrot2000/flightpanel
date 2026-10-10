"""Schriften für die HUB75-Matrix. TrueType (DejaVu Sans) wird mit
Antialiasing gerendert und dann hart geschwellt (THRESHOLD), das ergibt
saubere 1-Bit-Glyphen wie bei einem Pixelfont, aber mit proportionalen
Breiten. Fallback ist Pillows eingebaute Schrift.

Die Pfeile ↑ ↓ → ← werden nicht aus der Schrift genommen, sondern als feste
Pixelmuster gezeichnet, damit sie bei 10 px nicht zu Matsch werden."""

import logging
import os

from PIL import Image, ImageDraw, ImageFont

log = logging.getLogger("fonts")

THRESHOLD = int(os.environ.get("FONT_THRESHOLD", "128"))   # kleiner = fettere Buchstaben

_CANDIDATES = {
    "regular": [
        os.environ.get("FONT_REGULAR", ""),
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/TTF/DejaVuSans.ttf",
        "C:/Windows/Fonts/verdana.ttf",
        "/System/Library/Fonts/Supplemental/Verdana.ttf",
    ],
    "bold": [
        os.environ.get("FONT_BOLD", ""),
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/TTF/DejaVuSans-Bold.ttf",
        "C:/Windows/Fonts/verdanab.ttf",
        "/System/Library/Fonts/Supplemental/Verdana Bold.ttf",
    ],
}

# Pixel-Pfeile, 7 Zeilen hoch
_ARROWS = {
    "↑": ["..#..", ".###.", "#.#.#", "..#..", "..#..", "..#..", "..#.."],
    "↓": ["..#..", "..#..", "..#..", "..#..", "#.#.#", ".###.", "..#.."],
    "→": [".......", "...#...", "....#..", "#######", "....#..", "...#...", "......."],
    "←": [".......", "...#...", "..#....", "#######", "..#....", "...#...", "......."],
}
_ARROW_GAP = 1


def _load(kind: str, size: int) -> ImageFont.FreeTypeFont:
    for p in _CANDIDATES[kind]:
        if p and os.path.isfile(p):
            return ImageFont.truetype(p, size)
    log.warning("Keine %s-Schrift gefunden, nutze Pillow-Default", kind)
    return ImageFont.load_default(size=size)


class Font:
    def __init__(self, kind: str, size: int, threshold: int | None = None):
        self.font = _load(kind, size)
        self.size = size
        self.threshold = THRESHOLD if threshold is None else threshold
        x0, y0, x1, y1 = self.font.getbbox("H")
        self.y_offset = -y0          # Großbuchstaben beginnen bei y
        self.cap_height = y1 - y0

    # -------------------------------------------------------------- Maße --
    def _segments(self, text: str) -> list[tuple[str, str]]:
        """Zerlegt in ("text", ...) und ("arrow", "↑") Stücke."""
        out: list[tuple[str, str]] = []
        buf = ""
        for ch in text:
            if ch in _ARROWS:
                if buf:
                    out.append(("text", buf))
                    buf = ""
                out.append(("arrow", ch))
            else:
                buf += ch
        if buf:
            out.append(("text", buf))
        return out

    def text_width(self, text: str) -> int:
        w = 0
        for kind, s in self._segments(text):
            if kind == "text":
                w += int(round(self.font.getlength(s)))
            else:
                w += len(_ARROWS[s][0]) + _ARROW_GAP
        return w

    def fit(self, text: str, max_width: int) -> str:
        """Kürzt text, bis er in max_width passt."""
        while text and self.text_width(text) > max_width:
            text = text[:-1].rstrip()
        return text

    # ----------------------------------------------------------- Zeichnen --
    def draw(self, img: Image.Image, x: int, y: int, text: str, color: tuple[int, int, int]) -> int:
        """Zeichnet text so, dass Großbuchstaben bei y beginnen. Gibt das x nach dem Text zurück."""
        if not text:
            return x
        px = img.load()
        for kind, s in self._segments(text):
            if kind == "text":
                mask = Image.new("L", img.size, 0)
                ImageDraw.Draw(mask).text((x, y + self.y_offset), s, font=self.font, fill=255)
                mask = mask.point(lambda v: 255 if v > self.threshold else 0)
                img.paste(Image.new("RGB", img.size, color), (0, 0), mask)
                x += int(round(self.font.getlength(s)))
            else:
                rows = _ARROWS[s]
                ay = y + (self.cap_height - len(rows)) // 2
                for dy, row in enumerate(rows):
                    for dx, c in enumerate(row):
                        if c == "#" and 0 <= x + dx < img.width and 0 <= ay + dy < img.height:
                            px[x + dx, ay + dy] = color
                x += len(rows[0]) + _ARROW_GAP
        return x


# Für 32 Pixel Höhe: drei Zeilen à 11 px
FONT_BIG = Font("bold", 12)
FONT_MID = Font("regular", 11)
FONT_SMALL = Font("regular", 10, threshold=90)   # dünne Striche bei 10 px sonst weg
FONT_CLOCK = Font("bold", 18)
