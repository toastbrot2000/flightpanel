"""Einstellungsseite (SETTINGS_PAGE=1): aus den Panel-Feldern in config.py gebaut, jeder Wert escaped, kein Skript und
kein Stil in der Seite. Speichern nur als JSON, mit einem Origin aus dem LAN, geprüft wie panels set; das Panel
übernimmt es sofort, ohne Neustart. Ohne den Schalter gibt es die Seite nicht."""

import asyncio
import json
import os
import re
from contextlib import asynccontextmanager

import pytest
from aiohttp.test_utils import TestClient, TestServer
from test_web import inline_free

from conftest import make_cfg
from flightpanel.cards import CARDS
from flightpanel.config import panel_fields
from flightpanel.main import App
from flightpanel.web import STATIC_DIR, make_web_app

ORIGIN = "http://192.168.1.5:8080"


@asynccontextmanager
async def page(tmp_path, settings_page=True, **kw):
    """Ein Server im Mock-Modus mit (oder ohne) Einstellungsseite und ein Client an seinem LAN-Port."""
    cfg = make_cfg(mock=True, data_dir=str(tmp_path), metar=False, iss=False, logo_fetch=False,
                   settings_page=settings_page, **kw)
    app = App(cfg, env={})
    client = TestClient(TestServer(make_web_app(app)))
    await client.start_server()
    try:
        yield app, client
    finally:
        await client.close()
        await app.session.close()


async def save(client, settings=None, origin=ORIGIN, content_type="application/json", body=None):
    """POST /settings, wie settings.js es schickt: {"settings": {NAME: Wert | None}} als JSON."""
    headers = {"Content-Type": content_type}
    if origin is not None:
        headers["Origin"] = origin
    data = body if body is not None else json.dumps({"settings": settings}).encode()
    return await client.post("/settings", data=data, headers=headers)


def stored(app: App) -> dict:
    return app.db.panel_get("home")["settings"]


def test_without_the_switch_there_is_no_page(tmp_path):
    async def run():
        async with page(tmp_path, settings_page=False) as (_app, client):
            for method, path in (("GET", "/settings"), ("POST", "/settings"), ("GET", "/static/settings.js"),
                                 ("GET", "/static/settings.css")):
                r = await client.request(method, path, headers={"Origin": ORIGIN, "Content-Type": "application/json"})
                assert r.status == 404, (method, path)
            assert "settings" not in await (await client.get("/")).text()

    asyncio.run(run())


def test_the_page_shows_every_panel_setting_and_every_card(tmp_path):
    async def run():
        async with page(tmp_path) as (_app, client):
            r = await client.get("/settings")
            text = await r.text()
            assert r.status == 200 and r.headers["Cache-Control"] == "no-store"
            inline_free(text)
            for f in panel_fields():
                key = f.metadata["env"]
                assert text.count(f'data-key="{key}"') == 1 and f'id="row-{key}"' in text, key
            for c in CARDS:
                assert f'id="f-CARD_{c.id.upper()}" data-key="CARD_{c.id.upper()}" checked>' in text, c.id
            assert 'placeholder="52.52"' in text and 'placeholder="00:00-06:30"' in text   # Defaults als Platzhalter
            assert '<option value="" selected>Standard (C)</option>' in text                # LAYOUT nicht gesetzt
            assert '<option value="" selected>Standard (an)</option>' in text               # BLINK nicht gesetzt
            assert 'id="paste"' in text and 'id="planes"' in text                           # Einfügen, Eichen
            assert 'href="/settings"' in await (await client.get("/")).text()               # die Vorschau verlinkt sie
            for name, kind in (("settings.js", "text/javascript"), ("settings.css", "text/css")):
                r = await client.get(f"/static/{name}")
                assert r.status == 200 and r.headers["Content-Type"] == f"{kind}; charset=utf-8", name

    asyncio.run(run())


def test_stored_values_are_shown_and_escaped(tmp_path):
    async def run():
        async with page(tmp_path) as (app, client):
            app.db.panel_set_settings("home", {"HOME_LAT": "<script>alert(1)</script>", "VIEW_FROM": '110"><b>x',
                                               "LAYOUT": "b", "BLINK": "0", "CARD_PIGEON": "0", "CARD_CLOCK": "1"})
            text = await (await client.get("/settings")).text()
            assert "<script>alert" not in text and 'value="&lt;script&gt;alert(1)&lt;/script&gt;"' in text
            assert 'value="110&quot;&gt;&lt;b&gt;x"' in text and '"><b>' not in text
            assert re.search(r'id="f-LAYOUT"(?:(?!</select>).)*<option value="B" selected>', text, re.S)
            assert re.search(r'id="f-BLINK"(?:(?!</select>).)*<option value="0" selected>', text, re.S)
            assert 'id="f-CARD_PIGEON" data-key="CARD_PIGEON">' in text                    # aus
            assert 'id="f-CARD_CLOCK" data-key="CARD_CLOCK" checked>' in text              # an

    asyncio.run(run())


def test_saving_applies_at_once_without_a_restart(tmp_path):
    async def run():
        async with page(tmp_path) as (app, client):
            home, stats = app.lan, app.lan.stats
            app.db.panel_set_settings("home", {"home_lat": "50.0", "BLINK": "1"})       # von Hand, klein geschrieben
            r = await save(client, {"HOME_LAT": " 52.45 ", "HOME_LON": "13.3", "VIEW_FROM": "110", "LAYOUT": "B",
                                    "CARD_PIGEON": "0", "CARD_CLOCK": None, "BLINK": None, "CALM": ""})
            assert r.status == 200 and await r.json() == {"ok": True}
            assert stored(app) == {"HOME_LAT": "52.45", "HOME_LON": "13.3", "VIEW_FROM": "110", "LAYOUT": "B",
                                   "CARD_PIGEON": "0"}
            cfg = app.lan.cfg
            assert (cfg.home_lat, cfg.home_lon, cfg.view_from, cfg.layout, cfg.cards_off) == (
                52.45, 13.3, 110, "B", {"pigeon"})
            assert app.lan is home and app.lan.stats is stats                         # dasselbe Panel, Album bleibt
            r = await save(client, {"VIEW_FROM": None, "CARD_PIGEON": None})
            assert r.status == 200 and stored(app) == {"HOME_LAT": "52.45", "HOME_LON": "13.3", "LAYOUT": "B"}
            assert app.lan.cfg.view_from == 0 and app.lan.cfg.cards_off == frozenset()

    asyncio.run(run())


@pytest.mark.parametrize("key", ["DATA_DIR", "OPENSKY_CLIENT_SECRET", "PUBLIC_PORT", "LAN_PANEL", "SETTINGS_PAGE",
                                 "LAN_HOSTS", "home_lat", " HOME_LAT", "CARD_", "CARD_NOPE", "card_pigeon",
                                 "CARD_pigeon"])
def test_what_is_no_panel_setting_is_refused_and_nothing_is_stored(tmp_path, key):
    async def run():
        async with page(tmp_path) as (app, client):
            app.db.panel_set_settings("home", {"HOME_LAT": "52.45"})
            r = await save(client, {"HOME_LON": "13.3", key: "1"})
            doc = await r.json()
            assert r.status == 400 and doc["error"] and doc["key"] is None
            assert stored(app) == {"HOME_LAT": "52.45"} and app.lan.cfg.home_lon == 13.40

    asyncio.run(run())


@pytest.mark.parametrize(("key", "raw", "part"), [
    ("HOME_LAT", "95", "erlaubt: -90 bis 90"),
    ("HOME_LAT", "52,52", "mit Punkt statt Komma: 52.52"),
    ("VIEW_MAX_KM", "weit", "keine Zahl"),
    ("PAGE_SECONDS", "0", "mindestens 1"),
    ("MAX_FLIGHTS", "2.5", "keine ganze Zahl"),
    ("QUIET_HOURS", "22 bis 6", "von-bis"),
    ("LAYOUT", "D", "möglich: A, B, C"),
    ("INFO_PAGES", "eta,wetter", "unbekannte Infoseite"),
    ("HOME_LAT", 52.5, "als Text"),
])
def test_a_bad_value_is_refused_next_to_its_field(tmp_path, key, raw, part):
    async def run():
        async with page(tmp_path) as (app, client):
            app.db.panel_set_settings("home", {"VIEW_FROM": "110"})
            r = await save(client, {"HOME_LON": "13.3", key: raw})
            doc = await r.json()
            assert r.status == 400 and doc["key"] == key and part in doc["error"], doc
            assert stored(app) == {"VIEW_FROM": "110"}

    asyncio.run(run())


def test_a_stored_value_that_no_longer_fits_is_named_too(tmp_path):
    """Steht von früher Unsinn in der Datenbank, zeigt das Speichern ihn an seinem Feld, statt still zu scheitern."""
    async def run():
        async with page(tmp_path) as (app, client):
            app.db.panel_set_settings("home", {"VIEW_TO": "999"})
            r = await save(client, {"HOME_LAT": "52.45"})
            assert r.status == 400 and (await r.json())["key"] == "VIEW_TO"
            assert (await save(client, {"HOME_LAT": "52.45", "VIEW_TO": None})).status == 200

    asyncio.run(run())


@pytest.mark.parametrize("body", [b"kein json", b"[]", b'{"settings": []}', b'{"settings": {}, "mehr": 1}',
                                  b'{"HOME_LAT": "1"}', b"\xff\xfe", b'"settings"', b'{"settings": null}',
                                  b"[" * 16000], ids=lambda b: repr(b[:30]))      # so tief: RecursionError
def test_anything_but_the_one_shape_is_refused(tmp_path, body):
    async def run():
        async with page(tmp_path) as (app, client):
            r = await save(client, body=body)
            assert r.status == 400 and (await r.json())["error"] and stored(app) == {}

    asyncio.run(run())


@pytest.mark.parametrize("origin", [None, "", "null", "https://evil.example.com", "http://nas.fritz.box:8080",
                                    "http://192.168.1.5.nip.io:8080"])
def test_saving_needs_an_origin_from_the_lan(tmp_path, origin):
    """Eine fremde Website im Browser im LAN darf keine Einstellungen schreiben; ohne Origin auch nicht (anders als
    /issues/clear: settings.js schickt immer einen)."""
    async def run():
        async with page(tmp_path) as (app, client):
            r = await save(client, {"HOME_LAT": "52.45"}, origin=origin)
            assert r.status == 403 and (await r.json())["error"] and stored(app) == {}

    asyncio.run(run())


def test_lan_origins_may_save(tmp_path):
    async def run():
        async with page(tmp_path, lan_hosts=("nas.fritz.box",)) as (app, client):
            for origin in ("http://192.168.1.5:8080", "http://nas:8080", "http://[::1]:8080",
                           "https://flightpanel.local", "http://nas.fritz.box:8080"):
                assert (await save(client, {"HOME_LAT": "52.45"}, origin=origin)).status == 200, origin
            assert stored(app) == {"HOME_LAT": "52.45"}

    asyncio.run(run())


@pytest.mark.parametrize("content_type", ["text/plain", "application/x-www-form-urlencoded", "multipart/form-data",
                                          "application/octet-stream", "application/json-patch+json"])
def test_saving_takes_json_only(tmp_path, content_type):
    """Ein Formular einer fremden Seite kommt als text/plain oder urlencoded, ohne Rückfrage des Browsers: nur JSON."""
    async def run():
        async with page(tmp_path) as (app, client):
            r = await save(client, {"HOME_LAT": "52.45"}, content_type=content_type)
            assert r.status == 415 and stored(app) == {}

    asyncio.run(run())


def test_too_much_is_refused_also_when_sent_in_pieces(tmp_path):
    async def run():
        async with page(tmp_path) as (app, client):
            r = await save(client, {"HOME_LAT": "1" * 17 * 1024})
            assert r.status == 413 and (await r.json())["error"] and stored(app) == {}

            async def pieces():                                         # ohne Content-Length (chunked)
                yield b'{"settings": {"INFO_PAGES": "'
                for _ in range(20):
                    yield b"eta," * 256
                yield b'eta"}}'

            r = await client.post("/settings", data=pieces(),
                                  headers={"Origin": ORIGIN, "Content-Type": "application/json"})
            assert r.status == 413 and stored(app) == {}
            assert (await save(client, {"HOME_LAT": " " * 16000 + "52.45"})).status == 200   # knapp unter 16 KB
            assert stored(app) == {"HOME_LAT": "52.45"}

    asyncio.run(run())


def test_the_scripts_never_write_html():
    """Callsigns und Typen kommen aus ADS-B, Fehlertexte enthalten, was jemand getippt hat: nur textContent."""
    for name in ("settings.js", "preview.js"):
        with open(os.path.join(STATIC_DIR, name), encoding="utf-8") as f:
            js = f.read()
        for sink in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "eval(", "new Function"):
            assert sink not in js, (name, sink)
