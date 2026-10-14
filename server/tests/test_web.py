"""Server im Mock-Modus: jeder Endpunkt antwortet, /frame hat das Format für den ESP32. Dazu der Schutz des LAN-Ports
(Host, Cloudflare-Header, Origin, Sicherheits-Header) und /panel/* (ETag, Diagnose, Firmware-Dateien)."""

import asyncio
import hashlib
import json
import re
import time
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import datetime
from types import SimpleNamespace

import pytest
from aiohttp.test_utils import TestClient, TestServer

from conftest import FakeAdsb, make_cfg, state
from flightpanel.db import Db, db_path
from flightpanel.main import REST_S, App
from flightpanel.mock import mock_model
from flightpanel.model import DisplayModel, Flight
from flightpanel.panel import ESP32_GAP_S
from flightpanel.web import ROUTES, Handlers, lan_host, make_web_app, parse_diag


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
            app.lan.frame_count, app.lan.last_frame_fetch = 5, now - 15 * 60    # ESP32 war da, ist weg
            app.issues.note("exception", "KeyError build.py:12")
            r = await client.get("/ok")
            text = await r.text()
            assert r.status == 503 and "Flugdaten" in text and "ESP32" in text and "KeyError" in text
            night = App(replace(cfg, quiet_hours=(0, 24 * 60)))         # Ruhezeit: dunkel ist normal
            night.last_model_ok, night.lan.frame_count, night.lan.last_frame_fetch = now - 3600, 5, now - 3600
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
            p = app.lan
            p.model = mock_model(cfg, 3)
            p.model.flights = p.model.flights or [p.model.upcoming]
            assert not p.active(time.time())
            assert await app.poll_once(time.time()) == REST_S          # ruht: nichts abgerufen, Bild weg
            assert p.model.flights == [] and p.model.empty and p.model.stale
            day = App(replace(cfg, quiet_hours=(0, 0)))       # keine Ruhezeit
            assert day.lan.active(time.time())
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


def test_preview_with_another_layout_shows_the_panels_flight(tmp_path, monkeypatch):
    """Die Vorschau mit ?layout= zeigt denselben Flug wie das Panel, statt von vorn zu zählen."""
    async def run():
        cfg = make_cfg(data_dir=str(tmp_path), metar=False, iss=False, logo_fetch=False, quiet_hours=(0, 0),
                       page_seconds=4, flight_seconds=0, info_pages=("alt", "dist"))     # 8 s pro Flug
        app = App(cfg)
        a = Flight(icao24="a", callsign="EZY1", status="OVF", dist_km=5, bearing=150, alt_ft=2500, speed_kt=100,
                   track=250, vrate_fpm=0)
        app.lan.model = DisplayModel(source="test", flights=[a, replace(a, icao24="b", callsign="EZY2", bearing=160)])
        client = TestClient(TestServer(make_web_app(app)))
        await client.start_server()
        try:
            for when in (1000.0, 1009.0):                                  # dann ist der zweite dran
                monkeypatch.setattr("flightpanel.web.time", SimpleNamespace(time=lambda w=when: w))
                panel = await (await client.get("/frame.png")).read()
            return panel == await (await client.get("/frame.png?layout=C")).read()
        finally:
            await client.close()
            await app.session.close()

    assert asyncio.run(run())


def test_unknown_operators_in_the_album_get_their_operator_name(tmp_path):
    """Neue unbekannte Betreiber im Sammelalbum: Name wie auf dem Panel, notfalls aus
    dem Betreiber-Code, nach ganzen Wörtern gekürzt."""
    async def run():
        cfg = make_cfg(data_dir=str(tmp_path), metar=False, iss=False, logo_fetch=False)
        app = App(cfg)
        try:
            app.adsb = FakeAdsb(airlines={"XYZ": "Example Air Charter Ltd"})
            await app._observe(app.lan, [state(icao="x1", cs="XYZ123")], time.time())
            return app.lan.stats.dex["XYZ"]["name"]
        finally:
            await app.session.close()

    assert asyncio.run(run()) == "Example Air"


# ------------------------------------------------------------------ der LAN-Port: Schutz und /panel/* --

SECURITY = {"Content-Security-Policy": "default-src 'self'; frame-ancestors 'none'",
            "X-Content-Type-Options": "nosniff", "Referrer-Policy": "no-referrer"}
PUBLIC = dict(public_port=8081, access_team="beispiel", access_aud="a" * 64, public_host="flightpanel.example.com")
NOON = datetime(2026, 10, 14, 12, 0, 0).timestamp()           # tagsüber, außerhalb jeder Ruhezeit


@asynccontextmanager
async def lan(tmp_path, **kw):
    """Ein Server im Mock-Modus und ein Client an seinem LAN-Port (der Client schickt Host: 127.0.0.1:<Port>)."""
    cfg = make_cfg(mock=True, data_dir=str(tmp_path), metar=False, iss=False, logo_fetch=False, **kw)
    app = App(cfg, env={})
    client = TestClient(TestServer(make_web_app(app)))
    await client.start_server()
    try:
        yield app, client
    finally:
        await client.close()
        await app.session.close()


def inline_free(html: str) -> None:
    """Kein Skript und kein Stil im HTML selbst: Die CSP (default-src 'self') ließe sie nicht laufen. Skripte und
    Stile kommen als Dateien (/static/...)."""
    tags = re.findall(r"<[^>]*>", html)
    assert tags
    for tag in tags:
        assert not re.match(r"<style\b", tag) and not re.search(r"\sstyle\s*=", tag), tag
        assert not re.search(r"\son[a-z]+\s*=", tag) and "javascript:" not in tag, tag
        if re.match(r"<script\b", tag):
            assert re.search(r'\ssrc="/static/[a-z]+\.js"', tag), tag
    assert not re.search(r"<script\b[^>]*>[^<]", html)          # nichts zwischen <script ...> und </script>


@pytest.mark.parametrize(("host", "ok"), [
    ("192.168.1.5", True), ("192.168.1.5:8080", True), ("127.0.0.1:8080", True), ("10.0.0.7", True),
    ("[::1]", True), ("[::1]:8080", True), ("[fe80::1]:8080", True), ("[::ffff:192.168.1.5]:8080", True),
    ("localhost", True), ("LOCALHOST:8080", True), ("nas", True), ("Nas:8080", True),
    ("flightpanel.local", True), ("flightpanel.local:8080", True), ("nas.home.arpa:8080", True),
    ("nas.internal", True), ("Nas.Internal:8080", True), ("internal", True), ("nas.internal.", False),
    ("x.internal.example.com", False), ("nas.internals", False),
    ("nas.fritz.box", False), ("nas.fritz.box:8080", False), ("nas.lan", False), ("nas.home", False),
    ("evil.example.com", False),
    ("evil.example.com:8080", False), ("192.168.1.5.nip.io", False), ("local", True), ("home.arpa", False),
    ("x.local.", False), ("nas.local.example.com", False), ("::1", False), ("[::1", False),
    ("[evil.example.com]", False), ("[192.168.1.5]", False), ("[cafe]", False), ("[dead:beef]:8080", False),
    ("nas:http", False), ("nas:", False), ("nas:123456", False), ("", False),
    (" ", False), ("na s", False), ("nas/x", False), ("user@nas", False), ("münchen.local", False),
    (chr(0x212A), False), (f"nas{chr(0x212A)}.local", False)])      # Kelvin-Zeichen: klein geschrieben ein k
def test_the_lan_port_answers_to_lan_names_only(host, ok):
    """Gegen DNS-Rebinding: Eine fremde Website, deren Name plötzlich auf den Server zeigt, schickt ihren eigenen
    Namen als Host. Erlaubt sind Namen, die niemand öffentlich registrieren kann, und IP-Adressen. Die Namen der
    Router nicht ohne LAN_HOSTS: .box ist eine öffentliche Endung (wer fritz.box besitzt, könnte nas.fritz.box auf
    den Server zeigen lassen), .lan und .home sind nicht reserviert."""
    assert lan_host(host) is ok


def test_lan_hosts_adds_names(tmp_path):
    assert lan_host("nas.fritz.box:8080", ("nas.fritz.box",)) and lan_host("Nas.Fritz.Box", ("nas.fritz.box",))
    assert not lan_host("evil.fritz.box", ("nas.fritz.box",))


def test_a_foreign_host_gets_421_and_the_healthcheck_keeps_working(tmp_path, caplog):
    async def run():
        async with lan(tmp_path) as (app, client):
            with caplog.at_level("WARNING"):
                for path in ("/", "/panel/frame", "/ok", "/gibt-es-nicht"):
                    r = await client.get(path, headers={"Host": "evil.example.com"})
                    text = await r.text()
                    assert r.status == 421 and "LAN_HOSTS" in text, path
                    assert all(s in text for s in (".local", ".home.arpa", ".internal", "LAN_HOSTS=nas.fritz.box",
                                                   "FRAME_URL"))
            assert "421" in caplog.text and "LAN_HOSTS" in caplog.text           # einmal im Log ...
            assert caplog.text.count("421") == 1 and "evil" not in caplog.text  # ... ohne den Header selbst
            assert app.lan.frame_count == 0                                     # kam nicht bis zum Panel
            r = await client.post("/issues/clear", headers={"Host": "evil.example.com"})
            assert r.status == 421
            for host in ("127.0.0.1:8080", "[::1]:8080", "nas:8080", "flightpanel.local", "nas.home.arpa",
                         "nas.internal:8080"):
                assert (await client.get("/ok", headers={"Host": host})).status in (200, 503), host  # Docker: /ok
            assert (await client.get("/panel/frame", headers={"Host": "nas.fritz.box:8080"})).status == 421
        async with lan(tmp_path, lan_hosts=("nas.fritz.box",)) as (app, client):       # FRAME_URL des ESP32
            assert (await client.get("/panel/frame", headers={"Host": "nas.fritz.box:8080"})).status == 200
            assert (await client.get("/", headers={"Host": "evil.fritz.box:8080"})).status == 421

    asyncio.run(run())


def test_cloudflare_headers_on_the_lan_port_mean_the_tunnel_is_wired_wrong(tmp_path):
    """Mit eingerichtetem Zugang von außen kommt über Cloudflare nichts an den LAN-Port: Trägt eine Anfrage dessen
    Header, hängt der Tunnel am falschen Port, und der hat keine Anmeldung. Ohne Zugang von außen gibt es nichts zu
    verwechseln."""
    async def run():
        async with lan(tmp_path, **PUBLIC) as (app, client):
            for header in ("Cf-Ray", "Cf-Connecting-Ip", "Cf-Access-Jwt-Assertion", "cf-ray"):
                for path in ("/", "/panel/frame", "/ok", "/gibt-es-nicht"):
                    r = await client.get(path, headers={header: "8c1f2a3b4c5d6e7f-FRA"})
                    assert r.status == 403 and "PUBLIC_PORT" in await r.text(), (header, path)
            assert app.lan.frame_count == 0
            assert (await client.get("/")).status == 200
        async with lan(tmp_path) as (app, client):
            assert (await client.get("/", headers={"Cf-Ray": "8c1f2a3b4c5d6e7f-FRA"})).status == 200

    asyncio.run(run())


def test_every_response_carries_the_security_headers(tmp_path, monkeypatch):
    """Auch Fehler, 404, statische Dateien und die Absagen des Schutzes."""
    async def run():
        async with lan(tmp_path, **PUBLIC) as (app, client):
            def boom():
                raise RuntimeError("kaputt")

            monkeypatch.setattr(app, "health", boom)
            for method, path, headers, status in (
                    ("GET", "/", {}, 200), ("GET", "/static/preview.js", {}, 200),
                    ("GET", "/static/preview.css", {}, 200), ("GET", "/panel/frame", {}, 200),
                    ("GET", "/gibt-es-nicht", {}, 404), ("GET", "/static/", {}, 404),
                    ("GET", "/static/../web.py", {}, 404), ("GET", "/panel/firmware.txt", {}, 404),
                    ("POST", "/ok", {}, 405), ("GET", "/health", {}, 500),
                    ("GET", "/", {"Host": "evil.example.com"}, 421), ("GET", "/", {"Cf-Ray": "x"}, 403),
                    ("POST", "/issues/clear", {"Origin": "https://evil.example.com"}, 403)):
                r = await client.request(method, path, headers=headers)
                assert r.status == status, path
                for name, value in SECURITY.items():
                    assert r.headers.get(name) == value, (path, name)

    asyncio.run(run())


def test_the_preview_has_no_inline_script_or_style(tmp_path):
    async def run():
        async with lan(tmp_path) as (_app, client):
            html = await (await client.get("/?layout=B&card=radar")).text()
            inline_free(html)
            for link in ("/display.json", "/health", "/issues", "/stats", "/idle"):
                assert f'href="{link}"' in html, link
            assert "settings" not in html                                      # ohne SETTINGS_PAGE kein Link
            assert 'src="/frame.png?layout=B&amp;card=radar"' in html
            js = await client.get("/static/preview.js")
            css = await client.get("/static/preview.css")
            assert js.headers["Content-Type"] == "text/javascript; charset=utf-8" and "setInterval" in await js.text()
            assert css.headers["Content-Type"] == "text/css; charset=utf-8" and "body" in await css.text()

    asyncio.run(run())


def test_every_route_goes_through_the_one_helper(tmp_path):
    """Der Zugang von außen gibt später jeder Route dort ihre Identitätsklasse: Keine darf daran vorbei."""
    async def run():
        app = App(make_cfg(mock=True, data_dir=str(tmp_path), metar=False, iss=False, logo_fetch=False), env={})
        try:
            web_app = make_web_app(app)
            routes = list(web_app.router.routes())
            assert routes and set(routes) == set(web_app[ROUTES])
            assert {r.method for r in routes} == {"GET", "HEAD", "POST"}
        finally:
            await app.session.close()

    asyncio.run(run())


def test_the_frame_has_an_etag_and_the_same_frame_again_is_a_304(tmp_path, monkeypatch):
    monkeypatch.setattr("flightpanel.web.time", SimpleNamespace(time=lambda: NOON))

    async def run():
        async with lan(tmp_path, quiet_hours=(0, 0)) as (app, client):
            r = await client.get("/panel/frame")
            body, etag = await r.read(), r.headers["ETag"]
            assert r.status == 200 and len(body) == 4 + 128 * 32 * 3 and body[1] > 0
            assert etag == f'"{hashlib.sha256(body).hexdigest()[:16]}"'
            assert r.headers["Cache-Control"] == "no-store, no-transform"
            assert r.headers["Content-Type"] == "application/octet-stream"
            for sent in (etag, f"W/{etag}", f'"0123456789abcdef", {etag}'):
                r = await client.get("/panel/frame", headers={"If-None-Match": sent})
                assert r.status == 304 and await r.read() == b"" and r.headers["ETag"] == etag, sent
                assert r.headers["Cache-Control"] == "no-store, no-transform"
            r = await client.get("/frame", headers={"If-None-Match": '"0123456789abcdef"'})     # /frame: dasselbe
            assert r.status == 200 and await r.read() == body and r.headers["ETag"] == etag
            assert (await client.get("/frame", headers={"If-None-Match": etag})).status == 304
            assert app.lan.frame_count == 6                                     # ein 304 ist auch ein Abruf

    asyncio.run(run())


def wide(digits: str) -> str:
    """Dieselben Ziffern in voller Breite (U+FF10 …): int() nähme sie, das Muster nicht."""
    return "".join(chr(0xFF10 + int(d)) for d in digits)


@pytest.mark.parametrize(("header", "want"), [
    ("fw=2610131200 up=3600 rssi=-61 heap=45000", {"fw": 2610131200, "up": 3600, "rssi": -61, "heap": 45000}),
    ("heap=0 rssi=0 up=0 fw=0000000000", {"heap": 0, "rssi": 0, "up": 0, "fw": 0}),
    ("fw=9999999999 up=10000000000 rssi=-127 heap=9999999",
     {"fw": 9999999999, "up": 10 ** 10, "rssi": -127, "heap": 9999999}),                          # die Grenzen
    ("fw=261013120 up=-1 rssi=-128 heap=10000000", {}),                                            # knapp daneben
    ("fw=26101312000 up=10000000001 rssi=1 heap=99999999999999999999", {}),                        # Überlauf
    ("fw=2610131200 up=abc rssi=-61 heap=", {"fw": 2610131200, "rssi": -61}),                      # Feld für Feld
    ("fw=\x1b[31m2610131200 up=60 rssi=-6\x1b[0m1", {"up": 60}),                                    # Escape-Folgen
    (f"fw={wide('2610131200')} up={wide('60')} rssi=-61", {"rssi": -61}),                        # breite Ziffern
    ("up=1_000 heap=0x10 rssi=+5 fw=2.61e9", {}),
    ("fw=2610131200;up=60", {}), ("FW=2610131200 Up=60", {}), ("name=<script>alert(1)</script> up=60", {"up": 60}),
    ("fw=2610131200 fw=2610131201", {"fw": 2610131200}),
    ("fw=2610131200 " + "x" * 190, {}), ("", {}), (None, {})])
def test_x_panel_is_read_as_four_bounded_numbers(header, want):
    """Was ein Panel meldet, wird nur als vier Zahlen in Grenzen gelesen; alles andere fällt weg, Feld für Feld.
    Nichts davon wird je als Text gespeichert oder gezeigt."""
    got = parse_diag(header)
    assert got == want and all(type(v) is int for v in got.values())


def test_the_diagnostics_of_the_last_fetch_show_in_health(tmp_path):
    async def run():
        async with lan(tmp_path) as (_app, client):
            await client.get("/panel/frame", headers={"X-Panel": "fw=2610131200 up=60 rssi=-61 heap=45000 x=<b>"})
            health = await (await client.get("/health")).json()
            assert health["panels"]["home"]["esp32"]["diag"] == {"fw": 2610131200, "up": 60, "rssi": -61,
                                                                  "heap": 45000}
            await client.get("/frame")                                         # ohne Header: nichts gemeldet
            assert (await (await client.get("/health")).json())["panels"]["home"]["esp32"]["diag"] == {}

    asyncio.run(run())


def test_a_gap_in_the_fetches_is_noted_per_panel(tmp_path):
    """Am LAN-Port wie bisher unter "frames", für ein weiteres Panel mit seiner Kennung."""
    db = Db(db_path(str(tmp_path)))
    db.panel_add("home")
    db.panel_add("anna", settings={"HOME_LAT": "52.40", "HOME_LON": "14.05"})
    db.close()

    async def run():
        app = App(make_cfg(mock=True, data_dir=str(tmp_path), metar=False, iss=False, logo_fetch=False), env={})
        try:
            for p in app.panels.values():
                p.fetched(1000.0, {})
                p.fetched(1000.0 + ESP32_GAP_S - 1, {})                         # keine Lücke
                assert not any(k.startswith("esp32_gap") for k in app.issues.items)
            for p in app.panels.values():
                p.fetched(2000.0, {"rssi": -70})
            assert {k for k in app.issues.items if k.startswith("esp32_gap")} == {"esp32_gap:frames",
                                                                                  "esp32_gap:frames anna"}
            assert app.panels["anna"].diag == {"rssi": -70} and app.panels["anna"].frame_count == 3
        finally:
            await app.session.close()

    asyncio.run(run())


def firmware(tmp_path, channel: str, version: str, image: bytes) -> None:
    """Ein Release in DATA_DIR/firmware/<Kanal>/, wie tools/fw_release.sh es ablegt (Manifest mit vier Zeilen)."""
    folder = tmp_path / "firmware" / channel
    folder.mkdir(parents=True)
    (folder / "firmware.txt").write_bytes(f"{version}\n{len(image)}\n{'0' * 64}\nTUVVQ0g=\n".encode())
    (folder / "firmware.bin").write_bytes(image)


def set_channel(app: App, channel: str, updated: float) -> None:
    """Wie panels channel (eine Zeile in der Datenbank, updated neu); der Server zieht es nach (_sync_panels)."""
    app.db.execute("UPDATE panels SET fw_channel = ?, updated = ? WHERE id = ?", (channel, updated, "home"))
    app._sync_panels()


def test_firmware_files_are_served_from_the_channel_of_the_lan_panel(tmp_path):
    async def run():
        async with lan(tmp_path) as (app, client):
            assert app.lan.channel == "stable"
            assert (await client.get("/panel/firmware.txt")).status == 404                 # ohne Dateien
            assert (await client.get("/panel/firmware.bin?v=2610131200")).status == 404
            firmware(tmp_path, "stable", "2610131200", b"stabil")
            firmware(tmp_path, "canary", "2610141200", b"kanarienvogel")
            r = await client.get("/panel/firmware.txt")
            assert r.status == 200 and (await r.read()).startswith(b"2610131200\n")
            assert r.headers["Cache-Control"] == "no-store"
            r = await client.get("/panel/firmware.bin?v=2610131200")
            assert r.status == 200 and await r.read() == b"stabil"
            assert r.headers["Cache-Control"] == "public, max-age=86400"
            for v in ("2610141200", "261013120", "26101312000", "2610131200x", " 2610131200", "", "../x"):
                assert (await client.get("/panel/firmware.bin", params={"v": v})).status == 404, v
            assert (await client.get("/panel/firmware.bin")).status == 404
            set_channel(app, "canary", time.time() + 10)
            assert app.lan.channel == "canary"
            assert (await (await client.get("/panel/firmware.txt")).read()).startswith(b"2610141200\n")
            assert await (await client.get("/panel/firmware.bin?v=2610141200")).read() == b"kanarienvogel"
            assert (await client.get("/panel/firmware.bin?v=2610131200")).status == 404     # die von stable nicht
            (tmp_path / "firmware" / "canary" / "firmware.bin").unlink()
            assert (await client.get("/panel/firmware.bin?v=2610141200")).status == 404
            set_channel(app, "../stable", time.time() + 20)                     # von Hand Unsinn: gilt als stable
            assert app.lan.channel == "stable"

    asyncio.run(run())


def test_without_a_known_panel_the_firmware_comes_from_stable(tmp_path):
    """Für den Zugang von außen: Die Firmware-Dateien kommen dort ohne Identität an (Rettungskanal), dann stable."""
    async def run():
        app = App(make_cfg(mock=True, data_dir=str(tmp_path), metar=False, iss=False, logo_fetch=False), env={})
        try:
            set_channel(app, "canary", time.time() + 10)
            assert Handlers(app, lambda req: None, lambda origin: False).channel(None) == "stable"
            assert Handlers(app, lambda req: app.lan, lambda origin: False).channel(None) == "canary"
        finally:
            await app.session.close()

    asyncio.run(run())


def test_clearing_issues_needs_no_origin_or_one_from_the_lan(tmp_path):
    """curl schickt keinen Origin (README); ein Browser immer, und dann muss er aus dem LAN kommen: Eine fremde Website
    im Browser im LAN darf /issues nicht leeren."""
    async def run():
        async with lan(tmp_path) as (app, client):
            for origin in ("https://evil.example.com", "null", "http://nas.fritz.box:8080", "http://192.168.1.5.nip.io",
                           "file://", "http://192.168.1.5:8080/pfad", "http://user@192.168.1.5", "ftp://192.168.1.5"):
                app.issues.note("exception", "x")
                r = await client.post("/issues/clear", headers={"Origin": origin})
                assert r.status == 403 and "exception:x" in app.issues.items, origin
            for headers in ({}, {"Origin": "http://192.168.1.5:8080"}, {"Origin": "http://nas:8080"},
                            {"Origin": "http://[::1]:8080"}, {"Origin": "https://flightpanel.local"},
                            {"Origin": "http://nas.internal:8080"}):
                app.issues.note("exception", "x")
                r = await client.post("/issues/clear", headers=headers)
                assert r.status == 200 and "exception:x" not in app.issues.items, headers

    asyncio.run(run())
