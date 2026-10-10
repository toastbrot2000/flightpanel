"""Server im Mock-Modus: jeder Endpunkt antwortet, /frame hat das Format für den ESP32."""

import asyncio
import json
import time
from dataclasses import replace

from aiohttp.test_utils import TestClient, TestServer

from conftest import make_cfg
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
            for path in ("/", "/?layout=C&card=radar", "/frame.png", "/frame.png?card=joke&scale=x",
                         "/display.json", "/health", "/issues", "/stats", "/idle"):
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
