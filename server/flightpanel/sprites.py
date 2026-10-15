"""Pixel-Sprites für die Leerlauf-Karten. Jedes Sprite ist eine Liste von
Zeilen; "." ist durchsichtig, jedes andere Zeichen eine Farbe aus der Palette.
blit() schneidet am Bildrand ab, Sprites dürfen also hinein- und hinausfliegen."""

import math
import random

from PIL import Image

from .colors import Color, dim

# Flugzeug von oben, Nase rechts
PLANE = [
    ".......#......",
    ".......##.....",
    "#......###....",
    "#############>",
    "#......###....",
    ".......##.....",
    ".......#......",
]
PLANE_PAL = {"#": (90, 120, 190), ">": (140, 170, 230)}

# Flugzeug von der Seite, schlafend (Nase rechts, steht auf dem Boden)
PLANE_SIDE = [
    "#............",
    "##...........",
    "##.........w.",
    "#############",
    ".############",
    "....#....#...",
]
PLANE_SIDE_PAL = {"#": (70, 90, 140), "w": (20, 30, 50)}

# Taube von der Seite, schaut nach links
PIGEON = [
    "..gg.......",
    ".gog.......",
    "bggg.......",
    "..gggg.....",
    "..ggwwgggg.",
    "...gggggggg",
    "....gggg...",
    ".....y.y...",
]
PIGEON_PECK = [
    "...........",
    "...........",
    "..gg.......",
    ".gog.......",
    "bggggg.....",
    "...gwwgggg.",
    "....ggggggg",
    ".....y.y...",
]
PIGEON_PAL = {"g": (110, 115, 130), "o": (230, 120, 0), "b": (200, 150, 60), "w": (60, 140, 120),
              "y": (200, 100, 40)}

# Weihnachtsmann im Schlitten mit Rentier, Richtung rechts
SLEIGH = [
    "..........................b.b",
    "..........................bb.",
    "....rr.............bb.bb..bbbn",
    "...rrrr............bbbbbbbbb.",
    "...rwwr...........bbbbbbbb...",
    "#.rrrrrrr...........b.b.b.b..",
    "#yyyyyyyyy..----..b.b.b.b...",
    ".#########.......................",
]
SLEIGH_PAL = {"r": (210, 30, 30), "w": (200, 180, 160), "y": (180, 140, 30), "#": (150, 110, 40),
              "b": (130, 80, 40), "n": (255, 40, 40), "-": (120, 100, 60)}

ISS = [
    "##.#.##",
    "##ooo##",
    "##.#.##",
]
ISS_PAL = {"#": (60, 110, 200), "o": (200, 200, 210)}
# Ein anderer Satellit (Tiangong): Flügel golden, Rumpf hell
SAT = [
    "#.ooo.#",
    "##o#o##",
    "#.ooo.#",
]
SAT_PAL = {"#": (220, 170, 50), "o": (210, 210, 220)}
# Eine Starlink-Kette: Lichter in einer Reihe
TRAIN = [
    "......#",
    ".....#.",
    "....#..",
    "...#...",
    "..#....",
    ".#.....",
    "#......",
]
TRAIN_PAL = {"#": (230, 230, 240)}

# Winzige Buchstaben (5 hoch) für die Himmelsrichtungen im Radar; N und W brauchen
# 5 Spalten, in 3 sahen beide aus wie M
TINY = {
    "N": ["#...#", "##..#", "#.#.#", "#..##", "#...#"],
    "O": ["###", "#.#", "#.#", "#.#", "###"],
    "S": ["###", "#..", "###", "..#", "###"],
    "W": ["#...#", "#...#", "#.#.#", "#.#.#", ".#.#."],
}


def blit(img: Image.Image, sprite: list[str], x: int, y: int, pal: dict[str, Color] | Color,
         f: float = 1.0, flip: bool = False) -> None:
    px = img.load()
    W, H = img.size
    for dy, row in enumerate(sprite):
        yy = y + dy
        if not 0 <= yy < H:
            continue
        if flip:
            row = row[::-1]
        for dx, ch in enumerate(row):
            if ch == ".":
                continue
            xx = x + dx
            if 0 <= xx < W:
                c = pal if isinstance(pal, tuple) else pal.get(ch, (120, 120, 120))
                px[xx, yy] = dim(c, f)


def width(sprite: list[str]) -> int:
    return max(len(r) for r in sprite)


def tiny_text(img: Image.Image, x: int, y: int, text: str, color: Color) -> int:
    for ch in text:
        g = TINY.get(ch)
        if g:
            blit(img, g, x, y, color)
            x += width(g) + 1
    return x


def put(img: Image.Image, x: int, y: int, c: Color) -> None:
    if 0 <= x < img.width and 0 <= y < img.height:
        img.putpixel((x, y), c)


def fireworks(img: Image.Image, seed: int, t: float, f: float = 1.0, rockets: int = 4,
              area: tuple[int, int, int, int] | None = None) -> None:
    """Raketen steigen auf und zerplatzen; alles hängt nur von t (Sekunden) ab."""
    x0, y0, x1, y1 = area or (0, 0, img.width, img.height)
    colors = [(255, 60, 60), (255, 200, 40), (60, 200, 255), (120, 255, 120), (230, 90, 255)]
    for i in range(rockets):
        r = random.Random(seed * 31 + i)
        period = r.uniform(4.0, 7.0)
        tt = (t + r.uniform(0, period)) % period
        cx = r.randint(x0 + 8, x1 - 8)
        top = r.randint(y0 + 4, y0 + (y1 - y0) // 2)
        col = r.choice(colors)
        rise = 1.2
        if tt < rise:
            yy = int(y1 - (y1 - top) * tt / rise)
            put(img, cx, yy, dim((200, 160, 90), f))
            put(img, cx, yy + 1, dim((90, 60, 30), f))
            continue
        k = (tt - rise) / (period - rise)
        if k > 0.75:
            continue
        rad = 2 + k * 14
        fade = f * max(0.15, 1 - k * 1.2)
        for j in range(12):
            a = 2 * math.pi * j / 12 + i
            put(img, int(cx + rad * math.cos(a)), int(top + rad * math.sin(a) * 0.8 + k * 3), dim(col, fade))
