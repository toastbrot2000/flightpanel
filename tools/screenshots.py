#!/usr/bin/env python3
"""Beispielbilder für die Doku: rendert ausgewählte Screens mit festen Beispieldaten
nach docs/images/, in LED-Optik wie die Vorschau im Browser. Ohne Netz und ohne
eigene Daten. Airline-Logos sind Marken der Airlines und gehören nicht in die Doku:
Das Beispiel mit Logo zeigt die erfundene "Demo Air" mit einem hier gezeichneten
Logo.

    docker compose run --rm -v "$PWD:/repo" -w /repo flightpanel python tools/screenshots.py

Im Container, weil die Bilder mit den DejaVu-Schriften des Containers entstehen sollen.

Nach Änderungen am Aussehen neu erzeugen und mit einchecken."""

import io
import os
import sys
import tempfile
from datetime import datetime

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "server"))
from PIL import Image, ImageDraw

from flightpanel.cards import CARDS
from flightpanel.config import Config
from flightpanel.data import load_airlines
from flightpanel.idle import IdleScreen
from flightpanel.logos import LogoStore
from flightpanel.model import ARR, DEP, OVF, DisplayModel, Flight
from flightpanel.render import Renderer

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
OUT = os.path.join(ROOT, "docs", "images")
NOW = datetime(2026, 10, 10, 17, 42, 6).timestamp()
SCALE = 4

CFG = Config(data_dir=os.path.join(ROOT, "data"), view_from=110, view_to=190, view_min_elev=0.5,
             view_max_km=60, logo_fetch=False)


def flight(**kw) -> Flight:
    base = dict(icao24="000001", callsign="DMO42", status=ARR, dist_km=11.6, bearing=150, alt_ft=3400,
                speed_kt=186, track=249, vrate_fpm=-750, origin="PMI", dest="BER", airline="Demo Air",
                airline_icao="DMO", airline_iata="", actype="A320", seq=1, land_eta_s=230, elev_deg=5.1)
    base.update(kw)
    return Flight(**base)


ARRIVING = flight()
SECOND = flight(icao24="000002", callsign="EZY45KZ", airline="easyJet", airline_icao="EZY", airline_iata="",
                origin="LGW", seq=2, land_eta_s=420, bearing=128, dist_km=24.0)
DEPARTING = flight(icao24="000003", callsign="EWG7PL", status=DEP, airline="Eurowings", airline_icao="EWG",
                   airline_iata="", origin="BER", dest="PMI", actype="A319", alt_ft=3200, speed_kt=220,
                   vrate_fpm=1800, track=69, seq=None, land_eta_s=None, bearing=176)
RESCUE = flight(icao24="000004", callsign="CHX31", kind="other", status=OVF, airline="Christoph 31",
                airline_icao="CHX", label="Luftrettung", origin="", dest="", actype="EC35", alt_ft=1100,
                speed_kt=120, vrate_fpm=0, track=200, seq=None, land_eta_s=None, dist_km=4.2)


def demo_logo(directory: str) -> None:
    """Logo der erfundenen Demo Air: weißes Flugzeug auf blauem Kreis."""
    img = Image.new("RGBA", (128, 128), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.ellipse((4, 4, 124, 124), fill=(20, 110, 200, 255))
    plane = [(64, 14), (71, 30), (71, 52), (112, 74), (112, 84), (71, 72), (70, 98), (84, 110), (84, 116),
             (64, 110), (44, 116), (44, 110), (58, 98), (57, 72), (16, 84), (16, 74), (57, 52), (57, 30)]
    d.polygon(plane, fill=(255, 255, 255, 255))
    img.rotate(-35, resample=Image.BICUBIC).save(os.path.join(directory, "DMO.png"))


LOGOS = tempfile.mkdtemp()
demo_logo(LOGOS)


def renderer(cfg: Config) -> Renderer:
    logos = LogoStore(LOGOS, cfg.logo_size, fetch=False)
    idle = IdleScreen(cfg, CARDS, logos=logos, names=load_airlines(cfg.data_dir))
    return Renderer(cfg, logos, idle=idle)


def flight_image(r: Renderer, flights: list[Flight], shown: Flight, page: int) -> Image.Image:
    img = Image.new("RGB", (CFG.width, CFG.height))
    r.draw_flight(img, DisplayModel(source="docs", flights=flights, generated_at=NOW), shown, page, 1.0)
    return img


def card_image(r: Renderer, cid: str, now: float = NOW) -> Image.Image:
    return r.render(DisplayModel(source="docs", generated_at=now, landing_course=249), now, card=cid)


def save(r: Renderer, name: str, img: Image.Image) -> Image.Image:
    with open(os.path.join(OUT, f"{name}.png"), "wb") as f:
        f.write(r.png(img, SCALE, gap=True))
    return img


def overview(images: list[Image.Image], cols: int = 2) -> Image.Image:
    """Mehrere Screens als Raster, für den Kopf der README."""
    w, h, gap = CFG.width * SCALE, CFG.height * SCALE, 6 * SCALE
    rows = (len(images) + cols - 1) // cols
    sheet = Image.new("RGB", (cols * w + (cols + 1) * gap, rows * h + (rows + 1) * gap), (24, 24, 24))
    r = renderer(CFG)
    for i, img in enumerate(images):
        png = Image.open(io.BytesIO(r.png(img, SCALE, gap=True)))
        sheet.paste(png, (gap + (i % cols) * (w + gap), gap + (i // cols) * (h + gap)))
    return sheet


def main() -> None:
    os.makedirs(OUT, exist_ok=True)
    r = renderer(CFG)
    rotation = [ARRIVING, SECOND, DEPARTING]        # Eurowings ohne Logo: Platzhalter
    shots = {
        "flight-logo": flight_image(r, rotation, ARRIVING, 0),
        "flight-alt": flight_image(r, rotation, ARRIVING, 1),
        "flight-nologo": flight_image(r, rotation, DEPARTING, 0),
        "private": flight_image(r, [RESCUE], RESCUE, 0),
    }
    for cid in ("departures", "arrivals", "next", "radar", "runway", "weather", "iss", "scoreboard",
                "pokedex", "clock"):
        shots[cid] = card_image(r, cid)
    shots["santa"] = card_image(r, "santa", datetime(2026, 12, 24, 18, 0, 5).timestamp())
    shots["fireworks"] = card_image(r, "fireworks", datetime(2027, 1, 1, 0, 0, 4).timestamp())
    for name, img in shots.items():
        save(r, name, img)
    sheet = overview([shots[k] for k in ("flight-logo", "departures", "next", "radar", "flight-nologo", "iss")])
    sheet.save(os.path.join(OUT, "overview.png"))
    print(f"{len(shots) + 1} Bilder -> {os.path.normpath(OUT)}")


if __name__ == "__main__":
    main()
