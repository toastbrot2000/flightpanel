"""Logos: eigene (data/logos/own/) vor abgerufenen, der Abruf lässt sie in Ruhe;
eine weiße Bildmarke von ihrer farbigen Kachel lösen (tools/make_logo.py --mark)."""

import asyncio
import io
import json
import time

from PIL import Image, ImageDraw

from conftest import make_cfg
from flightpanel import fetch_logos
from flightpanel.logos import LogoStore


def _logo(path, color):
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (32, 32), color).save(path)


def test_own_logo_wins_over_the_fetched_one(tmp_path):
    _logo(tmp_path / "U2.png", (255, 0, 0))                 # abgerufen
    _logo(tmp_path / "own" / "U2.png", (0, 255, 0))         # eigenes
    store = LogoStore(str(tmp_path), 32, fetch=False)
    assert store.get("U2").getpixel((5, 5)) == (0, 255, 0)
    assert store.available() == ["U2"]


def test_fetching_leaves_own_logos_alone(tmp_path, monkeypatch):
    own = tmp_path / "logos" / "own" / "U2.png"
    _logo(own, (0, 255, 0))
    urls = []

    async def download(self, url):
        urls.append(url)
        return None

    monkeypatch.setattr(LogoStore, "_download", download)
    asyncio.run(fetch_logos.run(make_cfg(data_dir=str(tmp_path)), [("U2", "EZY")], force=True))
    assert urls == []                                       # nicht einmal versucht
    assert own.exists()


def test_fetched_logo_is_prepared_and_stored(tmp_path, monkeypatch):
    """Abruf: die erste Quelle mit Bildmarke gewinnt, aufbereitet als <IATA>.png."""
    tile = Image.new("RGB", (64, 64), (255, 255, 255))
    tile.paste((200, 20, 20), (8, 8, 56, 56))
    buf = io.BytesIO()
    tile.save(buf, "PNG")

    async def download(self, url):
        return None if "ZZ" in url else buf.getvalue()         # ZZ: Platzhalter-Probe

    monkeypatch.setattr(LogoStore, "_download", download)
    store = LogoStore(str(tmp_path), 32, urls=(("https://logos.example/{iata}.png", None),))
    assert asyncio.run(store.fetch_now("XQ", "XQA"))
    assert store.get("XQ").getpixel((16, 16)) == (200, 20, 20)


def test_missing_lists_only_codes_still_without_a_logo(tmp_path):
    """LY war vermerkt und wurde später doch geholt, AY bekommt ein eigenes: beide
    gehören nicht mehr nach /health -> logos_missing."""
    now = time.time()
    (tmp_path / ".missing.json").write_text(json.dumps({"LY": now, "AY": now, "FX": now}))
    _logo(tmp_path / "LY.png", (0, 0, 255))
    _logo(tmp_path / "own" / "AY.png", (0, 0, 255))
    assert LogoStore(str(tmp_path), 32, fetch=False).missing() == ["FX"]


def test_circle_logo_keeps_its_white_inside():
    """Turkish: weißer Vogel im roten Kreis, über Lücken mit dem weißen Rand verbunden.
    Die Hintergrundsuche würde ihn mit wegnehmen; der Kreis-Schnitt behält ihn."""
    from flightpanel.logo_image import circle_crop

    img = Image.new("RGB", (100, 100), (255, 255, 255))
    ImageDraw.Draw(img).ellipse((10, 10, 89, 89), fill=(200, 10, 10))
    img.paste((255, 255, 255), (5, 45, 70, 55))                 # weißer Balken bis über den Rand
    out = circle_crop(img)
    assert out.getpixel((50, 50)) == (255, 255, 255, 255)      # Weiß im Kreis bleibt
    assert out.getpixel((30, 30))[:3] == (200, 10, 10)
    assert out.getpixel((2, 2))[3] == 0                         # außerhalb durchsichtig
    assert out.getpixel((7, 50))[3] == 0                        # auch der Balken außerhalb


def test_dark_logo_is_brightened_keeping_its_hue():
    """LOT-Navy wäre auf LEDs fast schwarz: hellster Kanal auf 255, Farbton bleibt.
    Logos, die schon Weiß enthalten, bleiben, wie sie sind."""
    from flightpanel.logo_image import brighten

    navy = Image.new("RGBA", (10, 10), (0, 0, 0, 0))
    navy.paste((37, 38, 104, 255), (2, 2, 8, 8))
    out = brighten(navy)
    assert out.getpixel((5, 5)) == (91, 93, 255, 255)
    assert out.getpixel((0, 0))[3] == 0
    white = Image.new("RGBA", (4, 4), (255, 255, 255, 255))
    assert brighten(white).getpixel((1, 1)) == (255, 255, 255, 255)
    # Finnair: ein dünner heller Saum von der Kantenglättung zum Hintergrund hält es nicht auf
    edged = Image.new("RGBA", (70, 70), (0, 0, 0, 0))
    edged.paste((200, 200, 210, 255), (4, 4, 66, 66))
    edged.paste((37, 38, 104, 255), (5, 5, 65, 65))
    assert brighten(edged).getpixel((35, 35)) == (91, 93, 255, 255)


def test_light_gray_background_counts_as_background():
    """Finnair: Bilder von Webseiten liegen oft auf Hellgrau (234) statt Weiß. Ist der
    Rand fast überall so hell und grau, ist das der Hintergrund; die Marke bleibt,
    auch wo sie den Rand berührt (der Fuß des F)."""
    from flightpanel.logo_image import background_threshold, remove_background

    img = Image.new("RGB", (64, 64), (234, 234, 234))
    ImageDraw.Draw(img).polygon([(0, 50), (0, 44), (30, 12), (54, 12), (34, 50)], fill=(11, 20, 95))
    out = remove_background(img, background_threshold(img))
    assert out.getpixel((0, 0))[3] == 0 and out.getpixel((63, 63))[3] == 0
    assert out.getpixel((32, 30)) == (11, 20, 95, 255)
    assert background_threshold(Image.new("RGB", (8, 8), (255, 255, 255))) == 235    # Weiß wie bisher
    photo = Image.new("RGB", (8, 8), (240, 240, 240))
    photo.paste((60, 60, 60), (0, 0, 8, 1))                  # Rand nicht einheitlich: wie bisher
    assert background_threshold(photo) == 235


def test_white_mark_comes_off_its_tile():
    """easyJet: weißes e auf oranger Kachel -> oranges e, ohne Kachel."""
    from flightpanel.logo_image import white_mark

    tile = Image.new("RGB", (100, 100), (255, 102, 0))
    tile.paste((255, 255, 255), (30, 30, 70, 70))           # die Marke
    out = white_mark(tile, (255, 102, 0))
    assert out.getpixel((50, 50)) == (255, 102, 0, 255)
    assert out.getpixel((5, 5))[3] == 0                     # Kachel durchsichtig
