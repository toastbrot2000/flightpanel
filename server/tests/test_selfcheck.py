"""Der tägliche TLS-Selbsttest (selfcheck.py, nur mit Zugang von außen): ein kleiner TLS-Server mit einer Wegwerf-CA
aus tests/data statt Cloudflare. Kennt die Vertrauensliste die CA nicht, meldet /ok "Root-Liste veraltet" und
/issues tls_roots; kennt sie sie, ist alles gut. Ein Netzfehler sagt nichts über die Liste."""

import asyncio
import os
import ssl
import time
from contextlib import asynccontextmanager

from aiohttp import web
from aiohttp.test_utils import TestServer

from conftest import FIXTURES, make_cfg
from flightpanel.main import App
from flightpanel.selfcheck import TlsCheck, tls12_context

CA, OTHER_CA = (os.path.join(FIXTURES, name) for name in ("tls_test_ca.pem", "tls_test_other_ca.pem"))
CERT, KEY = (os.path.join(FIXTURES, name) for name in ("tls_test_server.pem", "tls_test_server.key"))
PUBLIC = dict(public_port=8081, access_team="beispiel", access_aud="a" * 64, public_host="flightpanel.example.com")


@asynccontextmanager
async def tls_server(minimum: ssl.TLSVersion = ssl.TLSVersion.TLSv1_2):
    """Statt Cloudflare: https://127.0.0.1:<Port>/panel/firmware.txt mit dem Test-Zertifikat. Er antwortet 404 (keine
    Firmware-Dateien da): Jeder HTTP-Status heißt, TLS passt. Gibt (URL, Liste der Abrufe) zurück."""
    hits = []

    async def manifest(req):
        hits.append(req.path)
        return web.Response(status=404)

    app = web.Application()
    app.router.add_get("/panel/firmware.txt", manifest)
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.minimum_version = minimum
    ctx.load_cert_chain(CERT, KEY)
    server = TestServer(app)
    await server.start_server(ssl=ctx)
    try:
        yield str(server.make_url("/panel/firmware.txt")), hits
    finally:
        await server.close()


@asynccontextmanager
async def checked(tmp_path, url: str, roots: str):
    """Ein Server mit Zugang von außen (ohne Start), dessen Selbsttest url mit der Vertrauensliste roots prüft."""
    app = App(make_cfg(mock=True, data_dir=str(tmp_path), metar=False, iss=False, logo_fetch=False, **PUBLIC), env={})
    app.tls = TlsCheck(app.session, app.issues, url, roots)
    try:
        yield app
    finally:
        await app.session.close()


def stale_in_ok(app: App) -> bool:
    return any(p.startswith("Root-Liste veraltet") for p in app.problems(time.time()))


def test_the_check_trusts_only_the_list_checks_the_name_and_speaks_tls_12_only():
    """Wie ein Panel: keine Wurzeln des Systems, nur die aus der Liste; der Name muss passen; TLS 1.2, wie der ESP32."""
    ctx = tls12_context(CA)
    assert ctx.verify_mode == ssl.CERT_REQUIRED and ctx.check_hostname
    assert ctx.minimum_version == ctx.maximum_version == ssl.TLSVersion.TLSv1_2
    assert [dict(rdn[0] for rdn in c["subject"]) for c in ctx.get_ca_certs()] == [
        {"commonName": "flightpanel Test-CA (nur fuer Tests)"}]


def test_a_certificate_outside_the_list_turns_ok_red_until_the_list_fits_again(tmp_path):
    async def run():
        async with tls_server() as (url, hits), checked(tmp_path, url, OTHER_CA) as app:
            assert not stale_in_ok(app)
            await app.tls.run(time.time())
            assert app.tls.stale and hits == []                    # die Prüfung scheitert vor jeder Anfrage
            assert stale_in_ok(app)
            item = app.issues.items["tls_roots:roots.pem"]
            assert "fw_release.sh" in item["example"]["fix"] and "docs/freunde.md" in item["example"]["fix"]
            assert "certificate" in item["example"]["error"]
            assert not any(k.startswith(("source_error", "rate_limit")) for k in app.issues.items)
            app.tls.roots = CA                                     # eine Firmware mit passender Liste
            await app.tls.run(time.time())
            assert app.tls.stale is None and hits == ["/panel/firmware.txt"] and not stale_in_ok(app)

    asyncio.run(run())


def test_with_the_list_that_fits_all_is_well(tmp_path):
    async def run():
        async with tls_server() as (url, hits), checked(tmp_path, url, CA) as app:
            now = time.time()
            await app.tls.run(now)
            assert app.tls.stale is None and app.tls.last_ok == now and hits == ["/panel/firmware.txt"]
            assert app.issues.items == {} and not stale_in_ok(app)

    asyncio.run(run())


def test_a_network_error_says_nothing_about_the_list(tmp_path):
    """Kein Server (Verbindung abgelehnt) oder einer, der nur TLS 1.3 spricht (das kann ein Panel nicht): ein Fehler
    der Quelle in /issues, /ok bleibt grün."""
    async def run():
        async with tls_server() as (closed, _):
            pass                                                   # danach lauscht dort niemand mehr
        async with tls_server(minimum=ssl.TLSVersion.TLSv1_3) as (only13, hits):
            # TLS 1.3: je nach Zeitpunkt der Alarm "protocol version" oder nur ein Abbruch der Verbindung
            for url, why in ((closed, "Connect call failed"), (only13, "")):
                async with checked(tmp_path, url, CA) as app:
                    app.issues.clear()
                    await app.tls.run(time.time())
                    assert app.tls.stale is None and not stale_in_ok(app), url
                    assert list(app.issues.items) == ["source_error:tls_check"], url
                    assert why in app.issues.items["source_error:tls_check"]["example"]["error"], url
            assert hits == []

    asyncio.run(run())


def test_without_roots_pem_the_check_says_so_once_and_rests(tmp_path):
    async def run():
        async with tls_server() as (url, hits), checked(tmp_path, url, str(tmp_path / "certs" / "roots.pem")) as app:
            for _ in range(3):
                await app.tls.run(time.time())
            assert hits == [] and app.tls.stale is None and not stale_in_ok(app)
            assert list(app.issues.items) == ["tls_roots:roots.pem fehlt"]
            assert app.issues.items["tls_roots:roots.pem fehlt"]["seen"] == 1

    asyncio.run(run())
