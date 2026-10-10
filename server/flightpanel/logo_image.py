"""Logo-Bilder fürs Panel aufbereiten: weißen Hintergrund entfernen, auf den
Inhalt zuschneiden, Schriftzüge erkennen, quadratisch auf LOGO_SIZE einpassen und
auf Schwarz legen. Für den automatischen Abruf (logos.py) und tools/make_logo.py."""

import io

from PIL import Image

MAX_SOURCE_PX = 512            # größere Bilder vor dem Flood-Fill verkleinern

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
