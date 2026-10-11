#!/usr/bin/env python3
"""Logo-Datei (PNG/JPG/GIF/WebP, oder SVG wenn cairosvg installiert ist) von Hand
in ein eigenes Logo verwandeln: für Airlines, die der automatische Abruf nicht
kennt (siehe /health -> logos_missing), oder statt eines abgerufenen, das nicht
gefällt.

  python3 tools/make_logo.py lufthansa.svg LH
  python3 tools/make_logo.py easyjet.png U2 --mark "#ff6600"  # weißes e auf Orange -> oranges e
  python3 tools/make_logo.py easyjet.png U2 --bg "#ff6600"    # Markenfarbe statt Schwarz
  python3 tools/make_logo.py ryanair.png FR --crop 10         # 10 % Rand wegschneiden
  python3 tools/make_logo.py foo.png XY --keep-white          # weißen Hintergrund stehen lassen

Ohne Python auf dem Rechner im Container (die Bilddatei muss im Repo-Ordner liegen):

  docker compose run --rm -v "$PWD:/repo" -w /repo flightpanel python tools/make_logo.py bild.png LH

Der Code ist der Logo-Code aus data/airlines.json (meist IATA, 2 Zeichen) oder der
ICAO-Code (3); der Server probiert beide. Ergebnis: data/logos/own/<CODE>.png in
LOGO_SIZE (Default 32), dazu own/preview/<CODE>.png (8x vergrößert) zum Begutachten.
Eigene Logos gehen den abgerufenen vor, der Abruf fasst sie nie an; löschen, um zum
abgerufenen zurückzukehren. Dieselbe Aufbereitung wie beim automatischen Abruf:
weißer Hintergrund weg, auf den Inhalt zuschneiden, quadratisch einpassen."""

import argparse
import io
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "server"))
from PIL import Image, ImageColor

from flightpanel.logo_image import prepare_logo, remove_background, white_mark


def load(path: str, px: int = 512) -> Image.Image:
    if path.lower().endswith(".svg"):
        try:
            import cairosvg  # type: ignore
        except ImportError:
            sys.exit("SVG braucht 'pip install cairosvg' (oder vorher als PNG exportieren).")
        data = cairosvg.svg2png(url=path, output_width=px, output_height=px)
        return Image.open(io.BytesIO(data)).convert("RGBA")
    return Image.open(path).convert("RGBA")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("src")
    ap.add_argument("code", help="IATA (LH, U2) oder ICAO (DLH, EZY)")
    ap.add_argument("--size", type=int, default=int(os.environ.get("LOGO_SIZE", "32")))
    ap.add_argument("--bg", default="#000000", help="Hintergrundfarbe hinter transparenten Teilen")
    ap.add_argument("--crop", type=float, default=0, help="Prozent Rand abschneiden (0-40)")
    ap.add_argument("--keep-white", action="store_true", help="weißen Hintergrund nicht entfernen")
    ap.add_argument("--mark", help="weiße Bildmarke auf farbiger Kachel: nur die Marke, in dieser Farbe")
    ap.add_argument("--out", default="data/logos/own")
    a = ap.parse_args()

    img = load(a.src)
    if a.mark:
        img = white_mark(img, ImageColor.getrgb(a.mark)[:3])
    elif not a.keep_white:
        img = remove_background(img)
    if a.crop:
        w, h = img.size
        c = a.crop / 100
        img = img.crop((int(w * c), int(h * c), int(w * (1 - c)), int(h * (1 - c))))

    buf = io.BytesIO()
    img.save(buf, "PNG")
    out = prepare_logo(buf.getvalue(), a.size, strip_white=False)
    if a.bg != "#000000":
        # prepare_logo legt auf Schwarz; für eine Markenfarbe noch einmal mit Alpha zusammensetzen
        small = img
        bbox = small.getchannel("A").getbbox()
        if bbox:
            small = small.crop(bbox)
        w, h = small.size
        s = max(w, h, 1)
        sq = Image.new("RGBA", (s, s), (0, 0, 0, 0))
        sq.paste(small, ((s - w) // 2, (s - h) // 2))
        r, g, b = ImageColor.getrgb(a.bg)[:3]
        bg = Image.new("RGBA", (a.size, a.size), (r, g, b, 255))
        bg.alpha_composite(sq.resize((a.size, a.size), Image.LANCZOS))
        out = bg.convert("RGB")

    code = a.code.upper()
    dst = os.path.join(a.out, f"{code}.png")
    preview = os.path.join(a.out, "preview", f"{code}.png")
    os.makedirs(os.path.dirname(preview), exist_ok=True)
    out.save(dst)
    out.resize((a.size * 8, a.size * 8), Image.NEAREST).save(preview)
    print(f"geschrieben: {dst}, Vorschau: {preview}")


if __name__ == "__main__":
    main()
