"""Logos: eigene (data/logos/own/) vor abgerufenen, der Abruf lässt sie in Ruhe;
eine weiße Bildmarke von ihrer farbigen Kachel lösen (tools/make_logo.py --mark)."""

import asyncio
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


def test_white_mark_comes_off_its_tile():
    """easyJet: weißes e auf oranger Kachel -> oranges e, ohne Kachel."""
    from flightpanel.logo_image import white_mark

    tile = Image.new("RGB", (100, 100), (255, 102, 0))
    tile.paste((255, 255, 255), (30, 30, 70, 70))           # die Marke
    out = white_mark(tile, (255, 102, 0))
    assert out.getpixel((50, 50)) == (255, 102, 0, 255)
    assert out.getpixel((5, 5))[3] == 0                     # Kachel durchsichtig
