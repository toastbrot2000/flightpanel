"""Der Zugang von außen (PUBLIC_PORT): Jede Route hat eine Identitätsklasse (email, token, public). Ohne geprüftes
Token von Cloudflare Access antwortet nichts außer den zwei Firmware-Dateien; jede Anmeldung sieht nur ihr eigenes
Panel, und nur, wenn sie freigeschaltet ist. Die Tokens signiert der Testschlüssel aus test_access."""

import asyncio
import gzip
import itertools
import json
import os
import re
import subprocess
import sys
import time
from contextlib import asynccontextmanager
from dataclasses import replace
from typing import ClassVar

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from multidict import CIMultiDict
from test_access import AUD, JWK, TEAM, enc, sign
from test_web import SECURITY, firmware, inline_free

from conftest import make_cfg
from flightpanel import selfcheck
from flightpanel.access import EMAIL, PUBLIC, TOKEN, Gate, KeySet
from flightpanel.db import Db, db_path
from flightpanel.main import App
from flightpanel.web import ROUTES, make_public_app

HOST = "flightpanel.example.com"
ORIGIN = f"https://{HOST}"
ANNA, BEN = "anna@example.com", "ben@example.com"
ANNA_PANEL = "0123456789abcdef0123456789abcdef.access"         # Client-IDs der Service-Tokens
BEN_PANEL = "fedcba9876543210fedcba9876543210.access"
HEADER = "Cf-Access-Jwt-Assertion"

# Die Routen am Zugang von außen und ihre Klasse, so und nicht anders (mit SETTINGS_PAGE=1). Eine neue Route ohne
# Eintrag hier lässt den Test scheitern: Wer eine dazunimmt, entscheidet ihre Klasse ausdrücklich.
TABLE = {("GET", "/"): EMAIL, ("GET", "/frame.png"): EMAIL, ("GET", "/display.json"): EMAIL,
         ("GET", "/stats"): EMAIL, ("GET", "/idle"): EMAIL, ("GET", "/static/preview.css"): EMAIL,
         ("GET", "/static/preview.js"): EMAIL, ("GET", "/settings"): EMAIL, ("POST", "/settings"): EMAIL,
         ("GET", "/static/settings.css"): EMAIL, ("GET", "/static/settings.js"): EMAIL,
         ("GET", "/panel/frame"): TOKEN,
         ("GET", "/panel/firmware.txt"): PUBLIC, ("GET", "/panel/firmware.bin"): PUBLIC}


def claims(kind: str, value: str, **kw) -> dict:
    """Was Access in ein Token schreibt, für einen Browser (email) oder ein Panel (token), gültig ab jetzt."""
    now = time.time()
    base = {"aud": [AUD], "exp": now + 600, "iat": now - 5, "iss": f"https://{TEAM}.cloudflareaccess.com",
            "type": "app"}
    who = ({"email": value, "sub": "7335d417-61da", "nbf": now - 5} if kind == EMAIL
           else {"common_name": value, "sub": ""})
    return {k: v for k, v in {**base, **who, **kw}.items() if v is not None}


def login(kind: str, value: str, header: dict | None = None, **kw) -> dict[str, str]:
    """Der Header, den cloudflared nach der Anmeldung mitschickt."""
    return {HEADER: sign(claims(kind, value, **kw), header=header)}


def browser(address: str = ANNA, **kw) -> dict[str, str]:
    return login(EMAIL, address, **kw)


def panel(client_id: str = ANNA_PANEL, **kw) -> dict[str, str]:
    return login(TOKEN, client_id, **kw)


def rows(tmp_path, keys: bool = True) -> None:
    """home (LAN-Port), anna und ben, je mit E-Mail-Adresse und Service-Token; mit keys die Schlüssel von Access
    gespeichert, wie nach dem ersten Abruf (KeySet)."""
    db = Db(db_path(str(tmp_path)))
    db.panel_add("home")
    db.panel_add("anna", settings={"LAYOUT": "B", "VIEW_FROM": "111"})
    db.panel_add("ben", settings={"LAYOUT": "A", "VIEW_FROM": "222", "CARD_PIGEON": "0"})
    for kind, value, pid in ((EMAIL, ANNA, "anna"), (TOKEN, ANNA_PANEL, "anna"), (EMAIL, BEN, "ben"),
                             (TOKEN, BEN_PANEL, "ben")):
        db.identity_add(kind, value, pid)
    if keys:
        db.state_set("access_keys", {"keys": [JWK], "fetched": time.time()})
    db.close()


@asynccontextmanager
async def public(tmp_path, settings_page=True, certs="http://127.0.0.1:9/nie", clock=None, before_start=None):
    """Ein Server im Mock-Modus mit Zugang von außen und ein Client an dessen Port (ohne cloudflared davor: Der
    Client schickt das Token selbst). Die Uhr der Ratenbegrenzung springt ohne clock bei jedem Blick 10 s weiter:
    Nur die Tests der Begrenzung selbst sollen an sie stoßen. before_start(web_app): ändern, bevor der Router
    eingefroren ist."""
    rows(tmp_path)
    cfg = make_cfg(mock=True, data_dir=str(tmp_path), metar=False, iss=False, logo_fetch=False, quiet_hours=(0, 0),
                   settings_page=settings_page, public_port=8081, access_team=TEAM, access_aud=AUD, public_host=HOST)
    app = App(cfg, env={})
    keys = KeySet(app.db, app.session, certs)
    gate = Gate(keys, TEAM, AUD, app.db, clock=clock or itertools.count(0.0, 10.0).__next__)
    web_app = make_public_app(app, gate)
    if before_start:
        before_start(web_app)
    client = TestClient(TestServer(web_app))
    await client.start_server()
    try:
        yield app, gate, client
    finally:
        await client.close()
        await app.session.close()


def counting_key_server(jwks: list[dict]):
    """Statt Cloudflare: liefert die Schlüsselliste und zählt die Abrufe."""
    hits = []

    async def certs(req):
        hits.append(time.time())
        return web.json_response({"keys": jwks})

    app = web.Application()
    app.router.add_get("/cdn-cgi/access/certs", certs)
    return TestServer(app), hits


# ------------------------------------------------------------------------------------- jede Route --
def test_every_route_has_its_class_and_refuses_without_a_token_or_with_the_wrong_one(tmp_path):
    async def run():
        async with public(tmp_path) as (_app, _gate, client):
            kinds = client.server.app[ROUTES]
            routes = list(client.server.app.router.routes())
            assert routes and set(routes) == set(kinds)                        # jede Route ging durch add()
            heads = {("HEAD", path): kind for (method, path), kind in TABLE.items() if method == "GET"}
            assert {(r.method, r.resource.canonical): kinds[r] for r in routes} == {**TABLE, **heads}
            assert {r.resource.canonical for r in routes if kinds[r] == PUBLIC} == {"/panel/firmware.txt",
                                                                                   "/panel/firmware.bin"}
            wrong = {EMAIL: panel(), TOKEN: browser()}
            right = {EMAIL: browser(), TOKEN: panel()}
            for r in routes:
                if kinds[r] == PUBLIC:
                    continue
                path = r.resource.canonical
                resp = await client.request(r.method, path)
                assert resp.status == 401, (r.method, path)
                resp = await client.request(r.method, path, headers=wrong[kinds[r]])
                assert resp.status == 403, (r.method, path)
                headers = {**right[kinds[r]], "Origin": ORIGIN, "Content-Type": "application/json"}
                data = b'{"settings": {}}' if r.method == "POST" else None
                resp = await client.request(r.method, path, headers=headers, data=data)
                assert resp.status == 200, (r.method, path)

    asyncio.run(run())


def test_without_the_switch_there_is_no_settings_page_outside_either(tmp_path):
    async def run():
        async with public(tmp_path, settings_page=False) as (_app, _gate, client):
            paths = {r.resource.canonical for r in client.server.app.router.routes()}
            assert not {p for p in paths if "settings" in p}
            assert (await client.get("/settings")).status == 401                       # ohne Anmeldung wie jede
            assert (await client.get("/settings", headers=browser())).status == 404
            assert 'href="/settings"' not in await (await client.get("/", headers=browser())).text()

    asyncio.run(run())


def test_a_route_without_a_class_is_never_served(tmp_path):
    """Eine Route, die an add() vorbei dazukam oder ohne Klasse vermerkt ist: 403, mit jeder Anmeldung."""
    async def leak(req):
        return web.Response(text="geheim")

    def forget(web_app):
        web_app.router.add_route("GET", "/vergessen", leak)                    # an add() vorbei
        web_app[ROUTES][web_app.router.add_route("GET", "/ohne", leak)] = None  # ohne Klasse

    async def run():
        async with public(tmp_path, before_start=forget) as (_app, gate, client):
            for headers in ({}, browser(), panel()):
                for path in ("/vergessen", "/ohne"):
                    r = await client.get(path, headers=headers)
                    assert r.status == 403 and "geheim" not in await r.text(), (path, headers)
            assert gate.report(time.time())["auth_failures"]["undeclared"] == 6

    asyncio.run(run())


# ------------------------------------------------------------------------------ wer was sieht --
def test_a_browser_sees_the_preview_of_its_own_panel(tmp_path):
    async def run():
        async with public(tmp_path) as (_app, _gate, client):
            r = await client.get("/", headers=browser(ANNA.upper()))                # Groß/klein egal
            html = await r.text()
            assert r.status == 200
            inline_free(html)
            assert 'src="/frame.png?layout=B&amp;card="' in html                   # anna: LAYOUT=B
            for link in ("/display.json", "/stats", "/idle", "/settings"):
                assert f'href="{link}"' in html, link
            assert "/health" not in html and "/issues" not in html and "data-health" not in html
            assert not re.search(r'(?:href|src)="[^"]*(?:anna|ben|home)', html)     # keine Panel-Kennung in Adressen
            for path in ("/static/preview.js", "/static/preview.css", "/display.json", "/stats", "/idle",
                         "/frame.png", "/settings"):
                r = await client.get(path, headers=browser())
                assert r.status == 200, path
                for name, value in SECURITY.items():
                    assert r.headers.get(name) == value, (path, name)

    asyncio.run(run())


def test_a_panel_gets_its_frame_with_etag(tmp_path):
    async def run():
        async with public(tmp_path) as (app, _gate, client):
            anna, ben = app.panels["anna"], app.panels["ben"]
            r = await client.get("/panel/frame", headers={**panel(), "X-Panel": "fw=2610131200 rssi=-61"})
            body, etag = await r.read(), r.headers["ETag"]
            assert r.status == 200 and len(body) == 4 + 128 * 32 * 3
            assert r.headers["Cache-Control"] == "no-store, no-transform"
            r = await client.get("/panel/frame", headers={**panel(), "If-None-Match": etag})
            assert r.status == 304 and await r.read() == b""
            assert (anna.frame_count, ben.frame_count, app.lan.frame_count) == (2, 0, 0)
            assert anna.diag == {} and r.headers["ETag"] == etag                 # der zweite Abruf ohne X-Panel

    asyncio.run(run())


def test_one_panel_never_reaches_another(tmp_path):
    """Nichts in der Anfrage wählt das Panel, nur die Anmeldung: weder Pfad noch Parameter noch ein Header."""
    async def run():
        async with public(tmp_path) as (app, _gate, client):
            anna, ben = app.panels["anna"], app.panels["ben"]
            ben.stats.day["aircraft"].add("3c0001")
            ben.model = replace(ben.model, source="nur-ben")
            for path in ("/settings", "/settings?panel=ben", "/settings?id=ben"):
                text = await (await client.get(path, headers={**browser(), "X-Panel-Id": "ben"})).text()
                assert 'value="111"' in text and 'value="222"' not in text, path
            stats = await (await client.get("/stats?panel=ben", headers=browser())).json()
            assert stats["today"]["aircraft"] == 0
            assert (await (await client.get("/stats", headers=browser(BEN))).json())["today"]["aircraft"] == 1
            assert "nur-ben" not in await (await client.get("/display.json?panel=ben", headers=browser())).text()
            assert "nur-ben" in await (await client.get("/display.json", headers=browser(BEN))).text()
            idle = await (await client.get("/idle", headers=browser())).json()
            assert next(c["state"] for c in idle["cards"] if c["id"] == "pigeon") != "off"   # ben hat sie aus
            for path in ("/panel/frame", "/panel/frame?panel=ben", "/panel/frame?id=ben"):
                assert (await client.get(path, headers=panel())).status == 200
            assert ben.frame_count == 0 and anna.frame_count == 3
            r = await client.post("/settings", data=json.dumps({"settings": {"VIEW_FROM": "10"}}),
                                  headers={**browser(BEN), "Origin": ORIGIN, "Content-Type": "application/json"})
            assert r.status == 200
            assert app.db.panel_get("anna")["settings"]["VIEW_FROM"] == "111"
            assert app.db.panel_get("ben")["settings"]["VIEW_FROM"] == "10" and ben.cfg.view_from == 10

    asyncio.run(run())


def test_unknown_identities_are_refused_even_with_a_valid_token(tmp_path):
    """Access hat sie durchgelassen, die Datenbank kennt sie nicht (oder nicht mehr): 403, nichts vom Panel."""
    async def run():
        async with public(tmp_path) as (app, gate, client):
            for path, headers in (("/", browser("carla@example.com")),
                                  ("/panel/frame", panel("00000000000000000000000000000000.access"))):
                r = await client.get(path, headers=headers)
                assert r.status == 403 and "nicht freigeschaltet" in await r.text(), path
            app.db.identity_remove(ANNA)                                     # panels unlink: gilt sofort
            assert (await client.get("/", headers=browser())).status == 403
            assert gate.report(time.time())["auth_failures"]["not_allowed"] == 3
            assert sum(p.frame_count for p in app.panels.values()) == 0

    asyncio.run(run())


def test_a_panel_token_on_a_browser_page_and_a_browser_on_the_frame_are_refused(tmp_path):
    async def run():
        async with public(tmp_path) as (app, gate, client):
            for path in ("/", "/settings", "/display.json", "/stats", "/idle", "/frame.png", "/static/preview.js"):
                assert (await client.get(path, headers=panel())).status == 403, path
            r = await client.get("/panel/frame", headers=browser())
            assert r.status == 403 and app.panels["anna"].frame_count == 0
            assert gate.report(time.time())["auth_failures"]["wrong_class"] == 8

    asyncio.run(run())


@pytest.mark.parametrize("case", ["missing", "twice", "empty", "aud", "aud-string", "iss", "type", "expired",
                                  "not-yet", "alg-none", "forged", "both", "garbage"])
def test_a_bad_or_missing_token_is_401(tmp_path, case):
    async def run():
        async with public(tmp_path) as (app, gate, client):
            good = browser()[HEADER]
            head, _, sig = good.split(".")
            other = enc(json.dumps(claims(EMAIL, BEN)).encode())
            headers = {
                "missing": {}, "twice": CIMultiDict([(HEADER, good), (HEADER, good)]), "empty": {HEADER: ""},
                "aud": browser(aud=["b" * 64]), "aud-string": browser(aud=AUD),
                "iss": browser(iss="https://andere.cloudflareaccess.com"), "type": browser(type="org"),
                "expired": browser(exp=time.time() - 121), "not-yet": browser(nbf=time.time() + 121),
                "alg-none": browser(header={"alg": "none", "kid": "testkey"}),
                "forged": {HEADER: f"{head}.{other}.{sig}"},                       # anderer Inhalt, alte Signatur
                "both": browser(common_name=ANNA_PANEL), "garbage": {HEADER: "x" * 8000},     # aiohttp: bis 8190
            }[case]
            for path in ("/", "/panel/frame", "/settings"):
                r = await client.get(path, headers=headers)
                assert r.status == 401, path
                for name, value in SECURITY.items():
                    assert r.headers.get(name) == value, (path, name)
            failures = gate.report(time.time())["auth_failures"]
            assert sum(failures.values()) == 3 and app.panels["anna"].frame_count == 0
            reason = {"missing": "missing", "twice": "several"}.get(case, "invalid")
            assert failures == {reason: 3}

    asyncio.run(run())


def test_nothing_of_the_token_reaches_the_log(tmp_path, caplog):
    async def run():
        async with public(tmp_path) as (_app, _gate, client):
            with caplog.at_level("DEBUG"):
                for headers in (browser(aud=["b" * 64]), browser(header={"alg": "RS256", "kid": "geheim-kid"}),
                                browser("carla@example.com"), panel(), {HEADER: "a.b.c"}):
                    await client.get("/", headers=headers)
            for secret in ("geheim-kid", "carla", "example.com", "a.b.c", ANNA_PANEL, "eyJ"):
                assert secret not in caplog.text, secret
            assert "abgelehnt" in caplog.text

    asyncio.run(run())


def test_an_unknown_kid_fetches_the_keys_once(tmp_path):
    """Cloudflare hat den Schlüssel gewechselt: Ein unbekannter kid holt die Liste neu, und das Token gilt. Noch ein
    unbekannter kid in derselben Minute holt nichts (KeySet), und das Token gilt nicht."""
    server, hits = counting_key_server([JWK, {**JWK, "kid": "neu"}])

    async def run():
        await server.start_server()
        try:
            async with public(tmp_path, certs=str(server.make_url("/cdn-cgi/access/certs"))) as (_, gate, client):
                r = await client.get("/", headers=browser(header={"alg": "RS256", "kid": "neu"}))
                assert r.status == 200 and len(hits) == 1
                assert (await client.get("/", headers=browser(header={"alg": "RS256", "kid": "neu"}))).status == 200
                r = await client.get("/", headers=browser(header={"alg": "RS256", "kid": "anders"}))
                assert r.status == 401 and len(hits) == 1
                assert gate.report(time.time())["auth_failures"] == {"unknown_key": 1}
                assert gate.report(time.time())["keys"] == 2
        finally:
            await server.close()

    asyncio.run(run())


def test_each_identity_has_its_own_rate_limit(tmp_path):
    """2 Anfragen pro Sekunde im Mittel, 10 auf einmal; dann 429, nur für diese Anmeldung."""
    now = [1000.0]

    async def run():
        async with public(tmp_path, clock=lambda: now[0]) as (app, gate, client):
            for _ in range(10):
                assert (await client.get("/panel/frame", headers=panel())).status == 200
            r = await client.get("/panel/frame", headers=panel())
            assert r.status == 429 and r.headers["Retry-After"] == "1"
            for name, value in SECURITY.items():
                assert r.headers.get(name) == value, name
            assert (await client.get("/", headers=browser())).status == 200        # andere Anmeldung: eigener Eimer
            assert (await client.get("/panel/frame", headers=panel(BEN_PANEL))).status == 200
            now[0] += 0.5                                                            # eine halbe Sekunde: eine mehr
            assert (await client.get("/panel/frame", headers=panel())).status == 200
            assert (await client.get("/panel/frame", headers=panel())).status == 429
            assert app.panels["anna"].frame_count == 11
            assert gate.report(time.time())["rate_limited"] == 2

    asyncio.run(run())


def test_the_firmware_files_share_one_rate_limit(tmp_path):
    now = [1000.0]

    async def run():
        async with public(tmp_path, clock=lambda: now[0]) as (_app, gate, client):
            for _ in range(20):
                assert (await client.get("/panel/firmware.txt")).status == 404          # keine Dateien: 404
            assert (await client.get("/panel/firmware.bin?v=2610131200")).status == 429
            assert (await client.get("/panel/firmware.txt", headers=panel())).status == 429   # Token egal
            now[0] += 1
            for _ in range(5):
                assert (await client.get("/panel/firmware.txt")).status == 404
            assert (await client.get("/panel/firmware.txt")).status == 429
            assert gate.report(time.time())["rate_limited"] == 3

    asyncio.run(run())


def test_the_server_pages_do_not_exist_outside(tmp_path):
    """/health, /ok, /issues und /frame gibt es nur am LAN-Port. Ohne Anmeldung sagt der Zugang von außen nicht
    einmal, was es gibt (401 wie überall), mit Anmeldung 404."""
    async def run():
        async with public(tmp_path) as (app, _gate, client):
            app.issues.note("exception", "x")
            for method, path in (("GET", "/health"), ("GET", "/ok"), ("GET", "/issues"), ("POST", "/issues/clear"),
                                 ("GET", "/frame"), ("GET", "/gibt-es-nicht"), ("GET", "/static/../web.py"),
                                 ("PUT", "/settings"), ("DELETE", "/panel/frame")):
                assert (await client.request(method, path)).status == 401, (method, path)
                for headers in (browser(), panel()):
                    r = await client.request(method, path, headers=headers)
                    assert r.status in (404, 405), (method, path)
            assert "exception:x" in app.issues.items

    asyncio.run(run())


def test_the_preview_image_is_at_most_twelve_times_as_large(tmp_path):
    def width(png: bytes) -> int:
        return int.from_bytes(png[16:20], "big")

    async def run():
        async with public(tmp_path) as (_app, _gate, client):
            sizes = [width(await (await client.get(f"/frame.png?scale={s}", headers=browser())).read())
                     for s in (6, 12, 40, 100000)]
            assert sizes == [128 * 6, 128 * 12, 128 * 12, 128 * 12]

    asyncio.run(run())


# --------------------------------------------------------------------------------- Einstellungen --
def test_settings_are_saved_from_the_public_page_only(tmp_path):
    async def run():
        async with public(tmp_path) as (app, _gate, client):
            body = json.dumps({"settings": {"VIEW_FROM": "120"}})
            for origin in (None, "null", "http://flightpanel.example.com", "https://flightpanel.example.com:443",
                           "https://evil.example.com", "http://192.168.1.5:8080", "http://nas.fritz.box:8080",
                           "https://flightpanel.example.com.evil.example.com"):
                headers = {**browser(), "Content-Type": "application/json"}
                if origin is not None:
                    headers["Origin"] = origin
                r = await client.post("/settings", data=body, headers=headers)
                assert r.status == 403, origin
            assert app.db.panel_get("anna")["settings"]["VIEW_FROM"] == "111"
            r = await client.post("/settings", data=body,
                                  headers={**browser(), "Origin": ORIGIN, "Content-Type": "application/json"})
            assert r.status == 200 and await r.json() == {"ok": True}
            assert app.db.panel_get("anna")["settings"]["VIEW_FROM"] == "120"
            assert app.panels["anna"].cfg.view_from == 120                              # gilt sofort

    asyncio.run(run())


def test_packed_bodies_are_not_unpacked(tmp_path):
    """aiohttp entpackt gzip sonst schon beim Empfang, vor jeder Prüfung (web.HANDLER_ARGS): Ein gepackter Körper
    bleibt gepackt und ist damit kein JSON."""
    async def run():
        async with public(tmp_path) as (app, _gate, client):
            body = gzip.compress(json.dumps({"settings": {"VIEW_FROM": "120"}}).encode())
            r = await client.post("/settings", data=body, headers={
                **browser(), "Origin": ORIGIN, "Content-Type": "application/json", "Content-Encoding": "gzip"})
            assert r.status == 400
            assert app.db.panel_get("anna")["settings"]["VIEW_FROM"] == "111"

    asyncio.run(run())


def test_settings_of_a_friend_obey_the_limits_for_further_panels(tmp_path):
    async def run():
        async with public(tmp_path) as (app, _gate, client):
            munich = {"HOME_LAT": "48.35", "HOME_LON": "11.79"}
            r = await client.post("/settings", data=json.dumps({"settings": munich}),
                                  headers={**browser(), "Origin": ORIGIN, "Content-Type": "application/json"})
            assert r.status == 400 and "PANEL_MAX_KM" in (await r.json())["error"]
            for key in ("DATA_DIR", "PUBLIC_PORT", "ACCESS_AUD", "LAN_HOSTS"):
                r = await client.post("/settings", data=json.dumps({"settings": {key: "x"}}),
                                      headers={**browser(), "Origin": ORIGIN, "Content-Type": "application/json"})
                assert r.status == 400, key
            assert app.db.panel_get("anna")["settings"] == {"LAYOUT": "B", "VIEW_FROM": "111"}

    asyncio.run(run())


# ------------------------------------------------------------------------------------- Firmware --
def test_firmware_comes_from_stable_and_from_the_channel_of_a_known_panel(tmp_path):
    server, hits = counting_key_server([{**JWK, "kid": "neu"}])

    async def run():
        await server.start_server()
        try:
            async with public(tmp_path, certs=str(server.make_url("/cdn-cgi/access/certs"))) as (app, _, client):
                firmware(tmp_path, "stable", "2610131200", b"stabil")
                firmware(tmp_path, "canary", "2610141200", b"kanarienvogel")
                app.db.panel_set_channel("anna", "canary")
                app._sync_panels()
                for headers in ({}, panel(BEN_PANEL), browser(), panel(aud=["b" * 64]), {HEADER: "Unsinn"},
                                panel(header={"alg": "RS256", "kid": "neu"})):
                    r = await client.get("/panel/firmware.txt", headers=headers)
                    assert r.status == 200 and (await r.read()).startswith(b"2610131200\n"), headers
                    assert r.headers["Cache-Control"] == "no-store"
                assert not hits                                  # ein unbekannter kid holt hier keine Schlüssel
                r = await client.get("/panel/firmware.txt", headers=panel())
                assert (await r.read()).startswith(b"2610141200\n")
                r = await client.get("/panel/firmware.bin?v=2610141200", headers=panel())
                assert r.status == 200 and await r.read() == b"kanarienvogel"
                assert r.headers["Cache-Control"] == "public, max-age=86400"
                r = await client.get("/panel/firmware.bin?v=2610131200")
                assert r.status == 200 and await r.read() == b"stabil"
                for name, value in SECURITY.items():
                    assert r.headers.get(name) == value, name
        finally:
            await server.close()

    asyncio.run(run())


# ----------------------------------------------------------------------------------- Diagnose --
def test_health_counts_refusals_and_the_clock_skew(tmp_path):
    async def run():
        async with public(tmp_path) as (_app, gate, client):
            await client.get("/")
            await client.get("/", headers=browser(exp=time.time() - 3600))
            await client.get("/panel/frame", headers=panel(iat=time.time() - 3))
            report = gate.report(time.time())
            assert report["auth_failures"] == {"invalid": 1, "missing": 1}
            assert report["keys"] == 1 and report["keys_age_h"] == 0.0 and report["keys_last_error"] is None
            assert report["rate_limited"] == 0 and 2 <= report["clock_skew_s"] <= 5

    asyncio.run(run())


def test_a_panel_added_while_running_is_served_at_once(tmp_path):
    """panels add und token neben dem laufenden Server: Der erste Abruf kommt vor dem nächsten Durchgang der
    Abrufschleife und findet sein Panel trotzdem."""
    async def run():
        async with public(tmp_path) as (app, _gate, client):
            cli = Db(db_path(str(tmp_path)), put_aside=False)
            try:
                cli.panel_add("carla")
                cli.identity_add(TOKEN, "c" * 32 + ".access", "carla")
            finally:
                cli.close()
            assert "carla" not in app.panels
            assert (await client.get("/panel/frame", headers=panel("c" * 32 + ".access"))).status == 200
            assert app.panels["carla"].frame_count == 1

    asyncio.run(run())


# ------------------------------------------------------------------------------------ Start und Stopp --
class FakeSite:
    """Statt eines Ports: schreibt nur mit, wo main.App lauschen würde (kein Test belegt 8080)."""
    opened: ClassVar[list] = []

    def __init__(self, runner, host, port):
        self.opened.append((runner, host, port))

    async def start(self):
        pass


DEFAULTS_OFF = """
import asyncio, sys
from aiohttp import web

opened = []


class Site:
    def __init__(self, runner, host, port):
        opened.append(port)

    async def start(self):
        pass


web.TCPSite = Site                                  # kein Port wird belegt
from flightpanel.config import Config
from flightpanel.main import App
from flightpanel.web import make_web_app


async def run():
    cfg = Config.from_env()
    app = App(cfg)
    make_web_app(app)
    runner = await app.start()
    health = app.health()
    await app.stop(runner)
    return cfg, health


cfg, health = asyncio.run(run())
assert not cfg.public and opened == [cfg.http_port], opened
assert health["access"] is None
loaded = sorted(m for m in ("flightpanel.access", "flightpanel.selfcheck") if m in sys.modules)
assert not loaded, loaded
print("ok")
"""


def test_without_the_switches_nothing_of_it_is_loaded_or_listening(tmp_path):
    """Wer das Panel nur für sich betreibt: Ohne die Schalter lädt der Server access.py und selfcheck.py nicht und
    öffnet keinen zweiten Port. In einem eigenen Prozess, weil dieser hier access.py längst geladen hat (Tests)."""
    server = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
    env = {"MOCK": "1", "DATA_DIR": str(tmp_path), "LOGO_FETCH": "0", "PYTHONPATH": server}
    r = subprocess.run([sys.executable, "-c", DEFAULTS_OFF], env=env, capture_output=True, text=True, timeout=120)
    assert r.returncode == 0 and r.stdout.strip() == "ok", r.stdout + r.stderr


def test_with_the_switches_a_second_site_starts_and_stop_closes_both(tmp_path, monkeypatch):
    """Mit den Schaltern: der LAN-Port und PUBLIC_PORT, davor der Türsteher. Liegen noch keine Schlüssel in der
    Datenbank, holt der Start sie; /health zeigt den Zugang von außen; stop() räumt beide Anwendungen ab."""
    rows(tmp_path, keys=False)
    server, hits = counting_key_server([JWK])
    monkeypatch.setattr(FakeSite, "opened", [])
    monkeypatch.setattr("aiohttp.web.TCPSite", FakeSite)

    async def run():
        await server.start_server()
        monkeypatch.setattr("flightpanel.access.certs_url", lambda team: str(server.make_url("/cdn-cgi/access/certs")))
        cfg = make_cfg(mock=True, data_dir=str(tmp_path), metar=False, iss=False, logo_fetch=False,
                       public_port=8081, access_team=TEAM, access_aud=AUD, public_host=HOST)
        app = App(cfg, env={})
        assert app.health()["access"] is None                          # vor dem Start gibt es ihn noch nicht
        runner = await app.start()
        try:
            (lan, _, lan_port), (out, _, out_port) = FakeSite.opened
            assert (lan, lan_port, out_port) == (runner, 8080, 8081) and out is not runner
            assert set(out.app[ROUTES].values()) == {EMAIL, TOKEN, PUBLIC}
            assert set(runner.app[ROUTES].values()) == {None}                 # am LAN-Port: keine Klassen
            for _ in range(200):                                              # die Schlüssel kommen beim Start
                if app.keys.keys:
                    break
                await asyncio.sleep(0.02)
            assert set(app.keys.keys) == {"testkey"} and len(hits) == 1
            access = app.health()["access"]
            assert access["keys"] == 1 and access["keys_age_h"] == 0.0 and access["auth_failures"] == {}
            # der tägliche TLS-Selbsttest: der eigene Hostname, mit der Vertrauensliste der Firmware
            assert app.tls.url == f"https://{HOST}/panel/firmware.txt" and app.tls.roots == selfcheck.ROOTS
            assert app.tls.stale is None and app.tls.last_ok is None             # der erste erst nach einer Minute
        finally:
            await app.stop(runner)
            await server.close()
        assert runner.server is None and out.server is None

    asyncio.run(run())
