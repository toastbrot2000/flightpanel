"""Logo-Bilder fürs Panel aufbereiten: weißen Hintergrund entfernen, auf den
Inhalt zuschneiden, Schriftzüge erkennen, quadratisch auf LOGO_SIZE einpassen und
auf Schwarz legen. Für den automatischen Abruf (logos.py) und tools/make_logo.py.
Dazu der Platzhalter für Airlines ohne Logo."""

import functools
import io
import math
from collections import Counter

from PIL import Image, ImageChops, ImageDraw

from .colors import MUTED, Color, dim

MAX_SOURCE_PX = 512            # größere Bilder vor dem Flood-Fill verkleinern

# Platzhalter: Flugzeug von oben, Nase oben, in Einheiten 0..100 (rechte Hälfte,
# die linke ist gespiegelt). Grau und dunkler als Text, damit es nicht nach Airline aussieht.
_PLANE_HALF = [(50, 2), (53.5, 6), (55, 12), (55, 36), (95, 58), (95, 64), (55, 54), (54, 78), (70, 88),
               (70, 93), (52, 89), (50, 92)]
_PLANE = _PLANE_HALF + [(100 - x, y) for x, y in reversed(_PLANE_HALF[1:-1])]
PLACEHOLDER_COLOR = dim(MUTED, 0.65)

# Schutz vor Dekompressionsbomben aus dem Netz: Logos haben selten mehr als 1000 x 1000,
# Pillow bricht ab dem Doppelten ab.
Image.MAX_IMAGE_PIXELS = 4_000_000


class NotSquare(ValueError):
    """Das Bild ist ein Schriftzug, keine Bildmarke."""


def remove_background(img: Image.Image, threshold: int = 235) -> Image.Image:
    """Macht alle hellen Pixel transparent, die vom Rand aus mit hellen Pixeln
    verbunden sind (Hintergrund), lässt helle Flächen im Logo selbst stehen."""
    img = img.convert("RGBA")
    w, h = img.size
    px = img.load()
    border = [(x, y) for x in range(w) for y in (0, h - 1)] + [(x, y) for y in range(h) for x in (0, w - 1)]
    # Kachel (Rand größtenteils deckend, App-Icons von Kiwi): halbtransparente Pixel sind
    # die geglätteten runden Ecken und fliegen mit raus, sonst bleiben graue Ecken stehen.
    # Logo auf transparentem Grund: halbtransparente Pixel sind seine eigene Kante (etwa
    # weiße Schrift) und bleiben, sonst frisst die Füllung die Schrift auf.
    tile = sum(1 for x, y in border if px[x, y][3] >= 10) > len(border) // 2

    def bright(x, y):
        r, g, b, a = px[x, y]
        return a < 10 or (tile and a < 255) or (r >= threshold and g >= threshold and b >= threshold)

    seen = bytearray(w * h)
    stack = list(border)
    while stack:
        x, y = stack.pop()
        i = y * w + x
        if seen[i] or not bright(x, y):
            continue
        seen[i] = 1
        r, g, b, _ = px[x, y]
        px[x, y] = (r, g, b, 0)
        if x > 0:
            stack.append((x - 1, y))
        if x < w - 1:
            stack.append((x + 1, y))
        if y > 0:
            stack.append((x, y - 1))
        if y < h - 1:
            stack.append((x, y + 1))
    return img


def background_threshold(img: Image.Image, default: int = 235) -> int:
    """Schwelle für remove_background: knapp unter einem hellgrauen Grund, der nicht
    ganz weiß ist (234 bei Bildern von Webseiten), sonst default. Grund ist die Farbe,
    die mindestens 80 % des Randes ausmacht: Die Marke darf den Rand berühren."""
    rgb = img.convert("RGB")
    w, h = rgb.size
    border = [rgb.getpixel((x, y)) for x in range(w) for y in (0, h - 1)] + \
             [rgb.getpixel((x, y)) for y in range(h) for x in (0, w - 1)]
    bg = Counter(border).most_common(1)[0][0]
    share = sum(1 for c in border if max(abs(a - b) for a, b in zip(c, bg, strict=True)) <= 6) / len(border)
    lo = min(bg)
    return lo - 4 if share >= 0.8 and 200 <= lo < default and max(bg) - lo <= 8 else default


def white_mark(img: Image.Image, color: Color) -> Image.Image:
    """Weiße Bildmarke auf farbiger Kachel (App-Icon, etwa das e von easyJet): nur die
    Marke, in color, die Kachel durchsichtig. Deckkraft aus dem kleinsten Farbkanal:
    Weiß deckt ganz, die Kachelfarbe (vom Rand) gar nicht, geglättete Kanten halb."""
    img = img.convert("RGBA")
    r, g, b, a = img.split()
    low = ImageChops.darker(ImageChops.darker(r, g), b)
    w, h = img.size
    border = [(x, y) for x in range(w) for y in (0, h - 1)] + [(x, y) for y in range(h) for x in (0, w - 1)]
    tile = sorted(low.getpixel(p) for p in border if a.getpixel(p) >= 128) or [0]
    t = tile[len(tile) // 2]
    alpha = low.point(lambda v: max(0, min(255, round((v - t) * 255 / max(1, 255 - t)))))
    out = Image.new("RGBA", img.size, (*color, 0))
    out.putalpha(ImageChops.multiply(alpha, a))
    return out


def circle_crop(img: Image.Image, threshold: int = 235) -> Image.Image:
    """Rundes Logo (Turkish): außerhalb des Kreises um den farbigen Inhalt durchsichtig,
    innen bleibt alles, auch Weiß. Für weiße Teile, die über Lücken am Kreisrand mit dem
    Hintergrund verbunden sind; remove_background nähme sie mit."""
    img = img.convert("RGBA")
    r, g, b, a = img.split()
    low = ImageChops.darker(ImageChops.darker(r, g), b)
    colored = ImageChops.multiply(low.point(lambda v: 255 if v < threshold else 0),
                                  a.point(lambda v: 255 if v >= 10 else 0))
    box = colored.getbbox()
    if box is None:
        return img
    circle = Image.new("L", img.size)
    ImageDraw.Draw(circle).ellipse((box[0], box[1], box[2] - 1, box[3] - 1), fill=255)
    img.putalpha(ImageChops.multiply(a, circle))
    return img


def brighten(img: Image.Image) -> Image.Image:
    """Dunkles Logo (LOT-Navy) fürs Panel aufhellen: Farben strecken, bis bei 90 % der
    sichtbaren Pixel der hellste Kanal 255 erreicht; der Farbton bleibt. Ein dünner
    heller Saum von der Kantenglättung (Finnair auf Hellgrau) zählt so nicht mit; Logos
    mit nennenswert Weiß bleiben, wie sie sind. Auf LEDs ist Dunkelblau sonst fast schwarz."""
    img = img.convert("RGBA")
    r, g, b, a = img.split()
    seen = a.point(lambda v: 255 if v >= 128 else 0)
    hist = ImageChops.lighter(ImageChops.lighter(r, g), b).histogram(mask=seen)
    total, run, top = sum(hist), 0, 0
    for v, n in enumerate(hist):
        run += n
        if run >= 0.9 * total:
            top = v
            break
    if top in (0, 255):
        return img
    k = 255 / top
    return Image.merge("RGBA", [ch.point(lambda v: min(255, round(v * k))) for ch in (r, g, b)] + [a])


def drop_specks(img: Image.Image, min_px: int) -> Image.Image:
    """Entfernt winzige sichtbare Inseln, die nach dem Freistellen übrig bleiben,
    etwa geglättete Pixel an den runden Ecken eines Icons (sonst drei graue
    Pixel in jeder Ecke). Bei 32 x 32 wäre so eine Insel kleiner als eine LED."""
    w, h = img.size
    px = img.load()
    seen = bytearray(w * h)
    for y0 in range(h):
        for x0 in range(w):
            if seen[y0 * w + x0] or px[x0, y0][3] < 10:
                continue
            seen[y0 * w + x0] = 1
            comp, stack = [], [(x0, y0)]
            while stack:
                x, y = stack.pop()
                comp.append((x, y))
                for nx, ny in ((x - 1, y), (x + 1, y), (x, y - 1), (x, y + 1)):
                    if 0 <= nx < w and 0 <= ny < h and not seen[ny * w + nx] and px[nx, ny][3] >= 10:
                        seen[ny * w + nx] = 1
                        stack.append((nx, ny))
            if len(comp) < min_px:
                for x, y in comp:
                    r, g, b, _ = px[x, y]
                    px[x, y] = (r, g, b, 0)
    return img


def prepare_logo(data: bytes, size: int, strip_white: bool = True,
                 max_aspect: float | None = None) -> Image.Image:
    """Bilddatei -> size x size RGB auf Schwarz. max_aspect: Seitenverhältnis des
    Inhalts nach dem Zuschnitt, ab dem das Bild als Schriftzug gilt (NotSquare)."""
    img = Image.open(io.BytesIO(data)).convert("RGBA")
    if max(img.size) > MAX_SOURCE_PX:
        img.thumbnail((MAX_SOURCE_PX, MAX_SOURCE_PX), Image.LANCZOS)
    if strip_white:
        img = drop_specks(remove_background(img), max(4, img.width * img.height // 1000))
    bbox = img.getchannel("A").getbbox()
    if bbox:
        img = img.crop(bbox)
    w, h = img.size
    if max_aspect is not None and max(w, h) > max_aspect * max(1, min(w, h)):
        raise NotSquare(f"{w}x{h}")
    s = max(w, h, 1)
    sq = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    sq.paste(img, ((s - w) // 2, (s - h) // 2))
    small = sq.resize((size, size), Image.LANCZOS)
    bg = Image.new("RGBA", (size, size), (0, 0, 0, 255))
    bg.alpha_composite(small)
    return bg.convert("RGB")


def _icon(shapes: list[list[tuple[float, float]]], size: int) -> Image.Image:
    """Umrisse in Einheiten 0..100 (Nase oben) -> size x size RGB, grau, um 45 Grad
    gedreht (Nase nach rechts oben), groß gezeichnet und glatt verkleinert wie ein Logo."""
    big = size * 8
    c = s = math.sqrt(0.5)
    rot = [[((x - 50) * c - (y - 50) * s, (x - 50) * s + (y - 50) * c) for x, y in shape] for shape in shapes]
    xs, ys = [p[0] for shape in rot for p in shape], [p[1] for shape in rot for p in shape]
    k = big * 0.86 / max(max(xs) - min(xs), max(ys) - min(ys))
    mx, my = (max(xs) + min(xs)) / 2, (max(ys) + min(ys)) / 2
    mask = Image.new("L", (big, big))
    draw = ImageDraw.Draw(mask)
    for shape in rot:
        draw.polygon([((x - mx) * k + big / 2, (y - my) * k + big / 2) for x, y in shape], fill=255)
    img = Image.new("RGB", (size, size))
    img.paste(PLACEHOLDER_COLOR, (0, 0, size, size), mask.resize((size, size), Image.LANCZOS))
    return img


def _blade(cx: float, cy: float, deg: float, half: float, width: float) -> list[tuple[float, float]]:
    """Rotorblatt: Rechteck durch die Nabe."""
    a = math.radians(deg)
    dx, dy, nx, ny = math.cos(a) * half, math.sin(a) * half, -math.sin(a) * width / 2, math.cos(a) * width / 2
    return [(cx - dx + nx, cy - dy + ny), (cx + dx + nx, cy + dy + ny), (cx + dx - nx, cy + dy - ny),
            (cx - dx - nx, cy - dy - ny)]


# Kleinflugzeug von oben, Nase oben: gerade Flügel weit vorn, schlanker Rumpf, Leitwerk
_SMALL_PLANE = [
    [(47, 6), (53, 6), (55.5, 22), (55, 70), (52.5, 93), (47.5, 93), (45, 70), (44.5, 22)],
    [(3, 27), (97, 27), (97, 40), (3, 40)],
    [(29, 78), (71, 78), (71, 88), (29, 88)],
]
# Hubschrauber von oben, Nase oben: Kabine, Heckausleger mit Heckrotor, Rotor als X
_HELI = [
    [(50 + 13 * math.sin(math.radians(a)), 40 - 19 * math.cos(math.radians(a))) for a in range(0, 360, 15)],
    [(48, 56), (52, 56), (51.2, 92), (48.8, 92)],
    [(41, 88), (59, 88), (59, 93), (41, 93)],
    _blade(50, 40, 45, 44, 5),
    _blade(50, 40, -45, 44, 5),
]


@functools.cache
def placeholder_logo(size: int) -> Image.Image:
    """size x size RGB: graues Flugzeug, Nase nach rechts oben. Hält im Layout C die
    Logospalte besetzt, wenn eine Airline kein Logo hat."""
    return _icon([_PLANE], size)


@functools.cache
def small_plane_logo(size: int) -> Image.Image:
    """Symbol für Privatflieger ohne Logo, außer Hubschraubern: Kleinflugzeug von oben."""
    return _icon(_SMALL_PLANE, size)


@functools.cache
def helicopter_logo(size: int) -> Image.Image:
    """Symbol für Hubschrauber ohne Logo (Luftrettung, Polizei ...): von oben, Rotor als X."""
    return _icon(_HELI, size)
