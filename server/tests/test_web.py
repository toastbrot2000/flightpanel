"""Server im Mock-Modus: jeder Endpunkt antwortet, /frame hat das Format für den ESP32."""

import asyncio
import json
import time
from dataclasses import replace
from datetime import datetime
from types import SimpleNamespace

from aiohttp.test_utils import TestClient, TestServer

from conftest import FakeAdsb, make_cfg, state
from flightpanel.main import App
from flightpanel.mock import mock_model
from flightpanel.web import make_web_app


def test_every_endpoint_answers(tmp_path):
    async def run():
        cfg = make_cfg(mock=True, data_dir=str(tmp_path), metar=False, iss=False, logo_fetch=False)
        app = App(cfg)
        await app._poll(time.time())
        client = TestClient(TestServer(make_web_app(app)))
        await client.start_server()
        try:
            for path in ("/", "/?layout=C&card=radar", "/?layout=auto", "/frame.png", "/frame.png?layout=x",
                         "/frame.png?card=pigeon&scale=x", "/display.json", "/health", "/issues", "/stats", "/idle"):
                r = await client.get(path)
                assert r.status == 200, path
            frame = await (await client.get("/frame")).read()
            assert frame[0] == 1 and frame[2:4] == bytes([128, 32]) and len(frame) == 4 + 128 * 32 * 3
            health = json.loads(await (await client.get("/health")).text())
            assert health["mock"] and health["esp32"]["fetches"] == 1
            r = await client.post("/issues/clear")
            assert r.status == 200
        finally:
            await client.close()
            await app.session.close()

    asyncio.run(run())


def test_ok_says_what_is_wrong(tmp_path):
    """GET /ok für Docker-Healthcheck und Uptime-Monitore: 200, sonst 503 mit Gründen."""
    async def run():
        cfg = make_cfg(mock=True, data_dir=str(tmp_path), metar=False, iss=False, logo_fetch=False,
                       quiet_hours=(0, 0))                     # keine Ruhezeit
        app = App(cfg)
        client = TestClient(TestServer(make_web_app(app)))
        await client.start_server()
        try:
            now = time.time()
            app.last_model_ok = now
            assert (await client.get("/ok")).status == 200
            app.last_model_ok = now - 20 * 60                          # seit 20 min nichts Neues
            app.frame_count, app.last_frame_fetch = 5, now - 15 * 60    # ESP32 war da, ist weg
            app.issues.note("exception", "KeyError build.py:12")
            r = await client.get("/ok")
            text = await r.text()
            assert r.status == 503 and "Flugdaten" in text and "ESP32" in text and "KeyError" in text
            night = App(replace(cfg, quiet_hours=(0, 24 * 60)))         # Ruhezeit: dunkel ist normal
            night.last_model_ok, night.frame_count, night.last_frame_fetch = now - 3600, 5, now - 3600
            assert night.problems(now) == []
            await night.session.close()
        finally:
            await client.close()
            await app.session.close()

    asyncio.run(run())


def test_quiet_hours_drop_the_old_picture(tmp_path):
    async def run():
        cfg = make_cfg(data_dir=str(tmp_path), metar=False, iss=False, logo_fetch=False,
                       quiet_hours=(0, 24 * 60))            # rund um die Uhr Ruhezeit
        app = App(cfg)
        try:
            app.model = mock_model(cfg, 3)
            app.model.flights = app.model.flights or [app.model.upcoming]
            assert app._quiet_hours(time.time())
            assert app.model.flights == [] and app.model.empty and app.model.stale
            day = App(replace(cfg, quiet_hours=(0, 0)))       # keine Ruhezeit
            assert not day._quiet_hours(time.time())
            await day.session.close()
        finally:
            await app.session.close()

    asyncio.run(run())


def test_new_year_midnight_lights_up_the_panel(tmp_path, monkeypatch):
    """Das Feuerwerk um Mitternacht kommt auch in der Ruhezeit hell beim ESP32 an, danach ist es wieder dunkel."""
    async def run():
        cfg = make_cfg(data_dir=str(tmp_path), metar=False, iss=False, logo_fetch=False,
                       quiet_hours=(0, 6 * 60 + 30))
        app = App(cfg)
        client = TestClient(TestServer(make_web_app(app)))
        await client.start_server()
        try:
            for when, brightness in ((datetime(2027, 1, 1, 0, 0, 2), cfg.brightness_evening),
                                     (datetime(2027, 1, 1, 0, 6), 0)):
                monkeypatch.setattr("flightpanel.web.time", SimpleNamespace(time=when.timestamp))
                frame = await (await client.get("/frame")).read()
                assert frame[1] == brightness, when
        finally:
            await client.close()
            await app.session.close()

    asyncio.run(run())


def test_unknown_operators_in_the_album_get_their_operator_name(tmp_path):
    """Neue unbekannte Betreiber im Sammelalbum: Name wie auf dem Panel, notfalls aus
    dem Betreiber-Code, nach ganzen Wörtern gekürzt."""
    async def run():
        cfg = make_cfg(data_dir=str(tmp_path), metar=False, iss=False, logo_fetch=False)
        app = App(cfg)
        try:
            app.adsb = FakeAdsb(airlines={"XYZ": "Example Air Charter Ltd"})
            await app._observe([state(icao="x1", cs="XYZ123")], time.time())
            return app.stats.dex["XYZ"]["name"]
        finally:
            await app.session.close()

    assert asyncio.run(run()) == "Example Air"
