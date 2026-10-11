"""Logos: eigene (data/logos/own/) vor abgerufenen, der Abruf lässt sie in Ruhe;
eine weiße Bildmarke von ihrer farbigen Kachel lösen (tools/make_logo.py --mark)."""

import asyncio

from PIL import Image

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


def test_white_mark_comes_off_its_tile():
    """easyJet: weißes e auf oranger Kachel -> oranges e, ohne Kachel."""
    from flightpanel.logo_image import white_mark

    tile = Image.new("RGB", (100, 100), (255, 102, 0))
    tile.paste((255, 255, 255), (30, 30, 70, 70))           # die Marke
    out = white_mark(tile, (255, 102, 0))
    assert out.getpixel((50, 50)) == (255, 102, 0, 255)
    assert out.getpixel((5, 5))[3] == 0                     # Kachel durchsichtig
