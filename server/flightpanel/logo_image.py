"""Logo-Bilder fürs Panel aufbereiten: weißen Hintergrund entfernen, auf den
Inhalt zuschneiden, Schriftzüge erkennen, quadratisch auf LOGO_SIZE einpassen und
auf Schwarz legen. Für den automatischen Abruf (logos.py) und tools/make_logo.py.
Dazu der Platzhalter für Airlines ohne Logo."""

import functools
import io
import math

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
    """Dunkles Logo (LOT-Navy) fürs Panel aufhellen: Farben strecken, bis der hellste
    Kanal der sichtbaren Pixel 255 erreicht; der Farbton bleibt. Auf LEDs ist Dunkelblau
    sonst fast schwarz."""
    img = img.convert("RGBA")
    r, g, b, a = img.split()
    seen = a.point(lambda v: 255 if v >= 128 else 0)
    top = max(ImageChops.multiply(ch, seen).getextrema()[1] for ch in (r, g, b))
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


@functools.cache
def placeholder_logo(size: int) -> Image.Image:
    """size x size RGB: graues Flugzeug, Nase nach rechts oben. Hält im Layout C die
    Logospalte besetzt, wenn eine Airline kein Logo hat."""
    big = size * 8                              # groß zeichnen, dann glatt verkleinern wie ein Logo
    c = s = math.sqrt(0.5)                      # 45 Grad gedreht
    pts = [((x - 50) * c - (y - 50) * s, (x - 50) * s + (y - 50) * c) for x, y in _PLANE]
    xs, ys = [p[0] for p in pts], [p[1] for p in pts]
    k = big * 0.86 / max(max(xs) - min(xs), max(ys) - min(ys))
    mx, my = (max(xs) + min(xs)) / 2, (max(ys) + min(ys)) / 2
    mask = Image.new("L", (big, big))
    ImageDraw.Draw(mask).polygon([((x - mx) * k + big / 2, (y - my) * k + big / 2) for x, y in pts], fill=255)
    img = Image.new("RGB", (size, size))
    img.paste(PLACEHOLDER_COLOR, (0, 0, size, size), mask.resize((size, size), Image.LANCZOS))
    return img
