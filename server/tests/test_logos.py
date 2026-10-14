"""Logos: eigene (data/logos/own/) vor abgerufenen, der Abruf lässt sie in Ruhe;
eine weiße Bildmarke von ihrer farbigen Kachel lösen (tools/make_logo.py --mark)."""

import asyncio
import io
import json
import os
import sys
import time

import pytest
from PIL import Image, ImageDraw

from conftest import make_cfg
from flightpanel import fetch_logos
from flightpanel.db import Db
from flightpanel.logos import LogoStore


def _logo(path, color):
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (32, 32), color).save(path)


def _tile_png() -> bytes:
    """Eine Bildmarke auf weißer Kachel, wie ein Logo-CDN sie liefert: rotes Quadrat auf Weiß."""
    tile = Image.new("RGB", (64, 64), (255, 255, 255))
    tile.paste((200, 20, 20), (8, 8, 56, 56))
    buf = io.BytesIO()
    tile.save(buf, "PNG")
    return buf.getvalue()


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
    png = _tile_png()

    async def download(self, url):
        return None if "ZZ" in url else png                    # ZZ: Platzhalter-Probe

    monkeypatch.setattr(LogoStore, "_download", download)
    store = LogoStore(str(tmp_path), 32, urls=(("https://logos.example/{iata}.png", None),))
    assert asyncio.run(store.fetch_now("XQ", "XQA"))
    assert store.get("XQ").getpixel((16, 16)) == (200, 20, 20)


@pytest.mark.skipif(not hasattr(os, "geteuid"), reason="Dateirechte gibt es nur unter Linux und macOS")
def test_fetch_logos_creates_nothing_readable_by_everyone(tmp_path, monkeypatch):
    """Wie der Server (main._main) und panels setzt auch das Seed-Skript umask 027: Die Logos und die Datenbank, falls
    sie fehlt, sind für andere Benutzer des Rechners unlesbar, selbst wenn die umask am Terminal offener ist (022).
    conftest.keep_the_umask stellt sie nach dem Test zurück."""
    png = _tile_png()

    async def download(self, url):
        return None if "ZZ" in url else png                    # ZZ: Platzhalter-Probe

    monkeypatch.setattr(LogoStore, "_download", download)
    monkeypatch.setattr(sys, "argv", ["fetch_logos", "XQ"])
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    os.umask(0o022)
    with pytest.raises(SystemExit) as done:
        fetch_logos.main()
    assert done.value.code == 0
    made = [p for p in tmp_path.rglob("*") if p.is_file()]
    assert sorted(p.name for p in made) == ["XQ.png", "flightpanel.db"]         # sonst prüfte der Test nichts
    assert [p.name for p in made if p.stat().st_mode & 0o007] == []


def test_missing_lists_only_codes_still_without_a_logo(tmp_path):
    """LY war vermerkt und wurde später doch geholt, AY bekommt ein eigenes: beide
    gehören nicht mehr nach /health -> logos_missing."""
    now = time.time()
    db = Db(str(tmp_path / "f.db"))
    store = LogoStore(str(tmp_path), 32, fetch=False, db=db)
    store.load_legacy({"LY": now, "AY": now, "FX": now})                 # alte .missing.json
    _logo(tmp_path / "LY.png", (0, 0, 255))
    _logo(tmp_path / "own" / "AY.png", (0, 0, 255))
    assert store.missing() == ["FX"]
    assert LogoStore(str(tmp_path), 32, fetch=False, db=db).missing() == ["FX"]     # aus der Datenbank


def test_no_logo_anywhere_is_remembered_in_the_database(tmp_path, monkeypatch):
    async def download(self, url):
        return None

    monkeypatch.setattr(LogoStore, "_download", download)
    db = Db(str(tmp_path / "f.db"))
    store = LogoStore(str(tmp_path), 32, urls=(("https://logos.example/{iata}.png", None),), db=db)
    assert not asyncio.run(store.fetch_now("XQ", "XQA"))
    assert db.one("SELECT code FROM logo_missing")["code"] == "XQ"


def test_fetch_logos_leaves_a_broken_database_alone(tmp_path):
    """fetch_logos läuft neben dem Server: eine kaputte Datenbank bleibt liegen, wie sie ist
    (beiseitelegen darf nur der Server, der sie offen hat)."""
    junk = "kaputt, keine Datenbank " * 100
    (tmp_path / "flightpanel.db").write_text(junk)
    with pytest.raises(Exception, match="not a database"):
        asyncio.run(fetch_logos.run(make_cfg(data_dir=str(tmp_path)), [], force=True))
    assert (tmp_path / "flightpanel.db").read_text() == junk and not list(tmp_path.glob("*.broken*"))


def test_load_legacy_takes_over_only_timestamps(tmp_path):
    """Die alte .missing.json kann von Hand verändert sein: übernommen wird nur, was eine Zeit ist."""
    now = time.time()
    db = Db(str(tmp_path / "f.db"))
    store = LogoStore(str(tmp_path), 32, fetch=False, db=db)
    store.load_legacy({"FX": now, "LY": "gestern", "AY": None, "SK": [now]})
    assert store.missing() == ["FX"]
    assert [r["code"] for r in db.query("SELECT code FROM logo_missing")] == ["FX"]


def test_load_legacy_skips_what_is_not_a_finite_time_with_one_warning(tmp_path, caplog):
    """Von Hand verändert, steht in der alten .missing.json mitunter nan, inf, true oder eine Zahl, die kein float ist:
    nan scheiterte in der Datenbank (NOT NULL), inf hielte einen Code für immer für fehlend, true zählte als 1, und
    float() liefe über. Übernommen wird nur eine endliche Zahl; was wegfällt, nennt eine einzige Warnung mit Anzahl."""
    now = time.time()
    db = Db(str(tmp_path / "f.db"))
    store = LogoStore(str(tmp_path), 32, fetch=False, db=db)
    odd = json.loads('{"NA": NaN, "PI": Infinity, "MI": -Infinity, "BI": 1e999, "T": true, "F": false, '
                     '"S": "gestern", "N": null, "L": [1]}')
    with caplog.at_level("WARNING", logger="logos"):
        store.load_legacy({"FX": now, "LY": now - 3600, **odd, "HUGE": 10**400})
    assert sorted(store.missing()) == ["FX", "LY"]
    assert sorted(r["code"] for r in db.query("SELECT code FROM logo_missing")) == ["FX", "LY"]
    (warning,) = [r.getMessage() for r in caplog.records if r.levelname == "WARNING"]
    assert ".missing.json" in warning and "10 Einträge" in warning
    caplog.clear()
    with caplog.at_level("WARNING", logger="logos"):
        store.load_legacy({"AY": now})                          # eine heile Datei bleibt ohne Warnung
    assert caplog.records == []


def test_without_a_database_failures_are_only_kept_in_memory(tmp_path, monkeypatch):
    """Ohne db (tools/screenshots.py): Fehlschläge stehen im Speicher, auf die Platte kommt nichts."""
    async def download(self, url):
        return None

    monkeypatch.setattr(LogoStore, "_download", download)
    store = LogoStore(str(tmp_path), 32, urls=(("https://logos.example/{iata}.png", None),))
    assert not asyncio.run(store.fetch_now("XQ", "XQA"))
    assert store.missing() == ["XQ"]
    assert list(tmp_path.iterdir()) == []


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
