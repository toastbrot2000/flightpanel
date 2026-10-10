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
        self._rows: dict[str, tuple[int, int]] = {}
        self._cols: dict[str, tuple[int, int]] = {}

    def _ink_rows(self, ch: str) -> tuple[int, int]:
        """Erste und letzte Pixelzeile eines Zeichens relativ zur Oberkante der
        Großbuchstaben, gerastert wie in draw() (mit Schwelle). Gecacht."""
        hit = self._rows.get(ch)
        if hit is None:
            n = self.size * 3
            mask = Image.new("L", (n, n), 0)
            ImageDraw.Draw(mask).text((self.size, self.size + self.y_offset), ch, font=self.font, fill=255)
            px = mask.load()
            ys = [y for y in range(n) if any(px[x, y] > self.threshold for x in range(n))]
            hit = (ys[0] - self.size, ys[-1] - self.size) if ys else (0, -1)
            self._rows[ch] = hit
        return hit

    def descent(self, text: str) -> int:
        """Wie viele Pixelzeilen der Text unter die Großbuchstaben reicht (g, p, y)."""
        d = 0
        for kind, s in self._segments(text):
            if kind == "text":
                for ch in set(s):
                    d = max(d, self._ink_rows(ch)[1] - (self.cap_height - 1))
        return d

    def _ink_cols(self, ch: str) -> tuple[int, int]:
        """(leere Spalten links, leere Spalten rechts) eines Zeichens, gerastert wie in
        draw() (mit Schwelle): Ein schwach gezeichneter Rand bleibt auf dem Panel dunkel."""
        hit = self._cols.get(ch)
        if hit is None:
            adv = int(round(self.font.getlength(ch)))
            n = self.size * 3
            mask = Image.new("L", (n, n), 0)
            ImageDraw.Draw(mask).text((self.size, self.size + self.y_offset), ch, font=self.font, fill=255)
            px = mask.load()
            xs = [x for x in range(n) if any(px[x, y] > self.threshold for y in range(n))]
            hit = (max(0, xs[0] - self.size), max(0, adv - (xs[-1] - self.size + 1))) if xs else (0, 0)
            self._cols[ch] = hit
        return hit

    def _bearings(self, s: str) -> tuple[int, int]:
        """(leerer Rand links, leerer Rand rechts) eines Textstücks: erstes und letztes Zeichen."""
        s = s.strip() or s
        return (self._ink_cols(s[0])[0], self._ink_cols(s[-1])[1]) if s else (0, 0)

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
        """Breite bis zum letzten Pixel: Rechtsbündiges endet so auf der letzten Spalte."""
        segs = self._segments(text)
        w = 0
        for kind, s in segs:
            if kind == "text":
                w += int(round(self.font.getlength(s)))
            else:
                w += len(_ARROWS[s][0]) + _ARROW_GAP
        if segs:
            kind, s = segs[-1]
            w -= self._bearings(s)[1] if kind == "text" else _ARROW_GAP
        return w

    def fit(self, text: str, max_width: int) -> str:
        """Kürzt text, bis er in max_width passt."""
        while text and self.text_width(text) > max_width:
            text = text[:-1].rstrip()
        return text

    # ----------------------------------------------------------- Zeichnen --
    def draw(self, img: Image.Image, x: int, y: int, text: str, color: tuple[int, int, int]) -> int:
        """Zeichnet text so, dass Großbuchstaben bei y beginnen. Gibt das x nach dem Text zurück.

        Bündig an den Rändern, damit keine leeren Pixel am Panelrand stehen bleiben:
        Bei x = 0 beginnt die Tinte auf der ersten Spalte. Reicht der Text über die
        letzte Zeile, rückt er hoch, bis Großbuchstaben oder Unterlängen auf der
        letzten Zeile sitzen: Ohne g, p, y steht die untere Zeile bündig auf der
        Kante, mit wird nichts abgeschnitten."""
        if not text:
            return x
        segs = self._segments(text)
        if x == 0 and segs[0][0] == "text":
            x -= self._bearings(segs[0][1])[0]
        over = y + self.cap_height + self.descent(text) - img.height
        if over > 0 and 0 < y < img.height:
            y = max(0, y - over)
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
