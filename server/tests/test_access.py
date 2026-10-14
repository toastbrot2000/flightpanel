"""Das Token von Cloudflare Access: RS256 nur mit der Standardbibliothek, gegen fremde Testvektoren (RFC 7515 A.2,
Wycheproof) und gegen alles, was ein Angreifer probieren kann."""

import asyncio
import base64
import hashlib
import json
import os

import aiohttp
import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer

from conftest import FIXTURES, make_db
from flightpanel import access
from flightpanel.access import (
    Identity,
    Invalid,
    KeySet,
    RsaKey,
    UnknownKey,
    b64url,
    certs_url,
    rs256_valid,
    rsa_key,
    verify,
)

TEAM, AUD, NOW = "beispiel", "a" * 64, 1791654544.0


def load(name: str):
    with open(os.path.join(FIXTURES, name), encoding="utf-8") as f:
        return json.load(f)


KEY = load("access_test_key.json")
N, E, D = (int.from_bytes(b64url(KEY[k]), "big") for k in ("n", "e", "d"))
KEYS = {"testkey": RsaKey(N, E)}


def enc(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def sign(claims: dict, header: dict | None = None, d: int = D, n: int = N) -> str:
    """Ein Token wie von Access, mit dem Testschlüssel signiert (RSASSA-PKCS1-v1_5, SHA-256)."""
    head = enc(json.dumps(header or {"alg": "RS256", "kid": "testkey", "typ": "JWT"}).encode())
    body = enc(json.dumps(claims).encode())
    k = (n.bit_length() + 7) // 8
    t = access.SHA256_DIGESTINFO + hashlib.sha256(f"{head}.{body}".encode()).digest()
    em = b"\x00\x01" + b"\xff" * (k - len(t) - 3) + b"\x00" + t
    sig = pow(int.from_bytes(em, "big"), d, n).to_bytes(k, "big")
    return f"{head}.{body}.{enc(sig)}"


def user(**kw) -> dict:
    return {"aud": [AUD], "email": "Anna@Example.com", "exp": NOW + 600, "iat": NOW - 5, "nbf": NOW - 5,
            "iss": f"https://{TEAM}.cloudflareaccess.com", "type": "app", "sub": "7335d417-61da", **kw}


def service(**kw) -> dict:
    return {"aud": [AUD], "common_name": "0123456789abcdef0123456789abcdef.access", "exp": NOW + 600,
            "iat": NOW - 5, "iss": f"https://{TEAM}.cloudflareaccess.com", "type": "app", "sub": "", **kw}


def test_a_browser_and_a_panel_token_are_recognized():
    assert verify(sign(user()), KEYS, TEAM, AUD, NOW) == Identity("email", "anna@example.com", NOW - 5)
    assert verify(sign(service()), KEYS, TEAM, AUD, NOW) == Identity(
        "token", "0123456789abcdef0123456789abcdef.access", NOW - 5)


def test_rfc_7515_a2_signature_checks_out():
    v = load("rfc7515_a2.json")
    head, body, sig = v["jws"].split(".")
    key = RsaKey(int.from_bytes(b64url(v["n"]), "big"), int.from_bytes(b64url(v["e"]), "big"))
    assert rs256_valid(key, f"{head}.{body}".encode(), b64url(sig))
    bad = bytearray(b64url(sig))
    bad[-1] ^= 1
    assert not rs256_valid(key, f"{head}.{body}".encode(), bytes(bad))


def test_wycheproof_pkcs1_vectors():
    """Jeder Vektor: gültig genau dann, wenn Wycheproof "valid" sagt. "acceptable" (veraltete Kodierungen) lehnen
    wir ab: Verglichen wird mit der einen kanonischen Kodierung."""
    data = load("wycheproof_rsa_signature_2048_sha256.json")
    count = 0
    for group in data["testGroups"]:
        key = group.get("publicKey") or {}
        n = int(key.get("modulus") or group["n"], 16)
        e = int(key.get("publicExponent") or group["e"], 16)
        for t in group["tests"]:
            ok = rs256_valid(RsaKey(n, e), bytes.fromhex(t["msg"]), bytes.fromhex(t["sig"]))
            assert ok == (t["result"] == "valid"), t["tcId"]
            count += 1
    assert count > 100


@pytest.mark.parametrize("change", [
    {"aud": AUD}, {"aud": ["b" * 64]}, {"iss": "https://andere.cloudflareaccess.com"}, {"type": "org"},
    {"exp": NOW - 61}, {"nbf": NOW + 61}, {"exp": None}, {"exp": True}, {"exp": "morgen"},
    {"common_name": "x.access"}, {"email": ""}, {"email": 5}], ids=str)
def test_wrong_claims_are_refused(change):
    claims = {k: v for k, v in user(**change).items() if v is not None}
    with pytest.raises(Invalid):
        verify(sign(claims), KEYS, TEAM, AUD, NOW)


def test_a_service_token_needs_an_empty_sub_and_no_email():
    with pytest.raises(Invalid):
        verify(sign(service(sub="x")), KEYS, TEAM, AUD, NOW)
    with pytest.raises(Invalid):
        verify(sign(service(email="a@example.com")), KEYS, TEAM, AUD, NOW)


def test_the_clock_has_sixty_seconds_of_leeway():
    assert verify(sign(user(exp=NOW - 59)), KEYS, TEAM, AUD, NOW).kind == "email"
    assert verify(sign(user(nbf=NOW + 59)), KEYS, TEAM, AUD, NOW).kind == "email"


@pytest.mark.parametrize("header", [{"alg": "none", "kid": "testkey"}, {"alg": "PS256", "kid": "testkey"},
                                    {"alg": "HS256", "kid": "testkey"},
                                    {"alg": "RS256", "kid": "testkey", "crit": ["exp"]}, {"alg": "RS256"}], ids=str)
def test_wrong_headers_are_refused(header):
    with pytest.raises(Invalid):
        verify(sign(user(), header=header), KEYS, TEAM, AUD, NOW)


def test_an_unknown_kid_says_so():
    with pytest.raises(UnknownKey):
        verify(sign(user(), header={"alg": "RS256", "kid": "neu"}), KEYS, TEAM, AUD, NOW)


def test_tampering_and_odd_encodings_are_refused():
    token = sign(user())
    head, body, sig = token.split(".")
    other = enc(json.dumps(user(email="mallory@example.com")).encode())
    for bad in (f"{head}.{other}.{sig}",                              # anderer Inhalt, alte Signatur
                f"{head}.{body}.{sig}AA",                             # Müll hinten
                f"{head}.{body}.{sig[:-1]}",                          # gekürzt
                f"{head}.{body}.{sig}=",                              # Füllzeichen
                f"{head}.{body}",                                     # zwei Teile
                f"{head}.{body}.{sig}.{sig}",                         # vier Teile
                f"{head} .{body}.{sig}",                              # Leerraum
                "x" * (access.MAX_TOKEN + 1)):
        with pytest.raises(Invalid):
            verify(bad, KEYS, TEAM, AUD, NOW)
    last = sig[-1]
    twin = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"
    sibling = twin[twin.index(last) ^ 1]                               # gleiche Bytes, andere letzte Bits
    with pytest.raises(Invalid):
        verify(f"{head}.{body}.{sig[:-1]}{sibling}", KEYS, TEAM, AUD, NOW)


def test_keys_are_checked_one_by_one():
    good = {"kty": "RSA", "kid": "k", "alg": "RS256", "use": "sig", "n": KEY["n"], "e": "AQAB"}
    assert rsa_key(good) == ("k", RsaKey(N, E))
    for bad in ({**good, "e": enc(b"\x03")},                              # e = 3
                {**good, "kty": "EC"}, {**good, "alg": "PS256"}, {**good, "use": "enc"},
                {**good, "n": enc((N >> 1100).to_bytes(120, "big"))},   # zu kurz
                {**good, "kid": ""}, {**good, "n": KEY["n"] + "="}, "kein Objekt"):
        with pytest.raises(Invalid):
            rsa_key(bad)


def test_a_signature_as_long_as_n_but_not_smaller_is_refused():
    k = (N.bit_length() + 7) // 8
    assert not rs256_valid(RsaKey(N, E), b"x", N.to_bytes(k, "big"))
    assert not rs256_valid(RsaKey(N, E), b"x", b"\x00" + N.to_bytes(k, "big"))     # ein Byte zu lang


JWK = {"kty": "RSA", "kid": "testkey", "alg": "RS256", "use": "sig", "n": KEY["n"], "e": "AQAB"}
GOOD = json.dumps({"keys": [JWK]}).encode()            # eine gültige Liste


def serve(handler):
    """Ein kleiner HTTP-Server statt Cloudflare; gibt den Server zurück (noch nicht gestartet). Unter /umweg liegt eine
    gültige Liste: Folgte KeySet einer Umleitung, fiele es auf."""
    async def detour(req):
        return web.Response(body=GOOD, content_type="application/json")

    app = web.Application()
    app.router.add_get("/cdn-cgi/access/certs", handler)
    app.router.add_get("/umweg", detour)
    return TestServer(app)


async def pieces(req, *parts: bytes) -> web.StreamResponse:
    """Eine Antwort in mehreren Stücken, wie sie übers Netz kommen kann: KeySet muss bis zum Ende lesen."""
    resp = web.StreamResponse(headers={"Content-Type": "application/json"})
    await resp.prepare(req)
    for part in parts:
        await resp.write(part)
        await asyncio.sleep(0.05)
    await resp.write_eof()
    return resp


def test_the_key_set_is_fetched_checked_one_by_one_and_kept(tmp_path):
    db = make_db(tmp_path)

    async def certs(req):
        body = json.dumps({"keys": [JWK, {**JWK, "kid": "schwach", "e": "Aw"}],
                           "public_cert": {}, "public_certs": []}).encode()
        return await pieces(req, body[:100], body[100:])                       # in zwei Stücken, wie übers Netz

    async def run():
        server = serve(certs)
        await server.start_server()
        async with aiohttp.ClientSession() as session:
            ks = KeySet(db, session, str(server.make_url("/cdn-cgi/access/certs")))
            assert await ks.refresh(NOW) and set(ks.keys) == {"testkey"}           # e = 3 fällt weg, der Rest gilt
            assert not await ks.refresh(NOW + 30)                                  # höchstens einmal pro Minute
            again = KeySet(db, session, "http://127.0.0.1:9/nie")                  # Neustart ohne Netz
            assert set(again.keys) == {"testkey"} and again.fetched == NOW
            assert not await again.refresh(NOW + 120, force=True)                  # Netz weg: alte Liste bleibt
            assert set(again.keys) == {"testkey"} and again.last_error
        await server.close()

    asyncio.run(run())


@pytest.mark.parametrize("answer", ["redirect", "huge", "html", "nokeys", "status"])
def test_a_misbehaving_key_endpoint_changes_nothing(tmp_path, answer):
    """Wo es geht, steckt in der Antwort eine gültige Liste (hinter der Umleitung, vor dem Zuviel, in der 503): Die
    alte bleibt nur, wenn KeySet Umleitung, Größe und Status wirklich prüft."""
    db = make_db(tmp_path)
    db.state_set("access_keys", {"keys": [JWK], "fetched": NOW - 3600})

    async def certs(req):
        if answer == "redirect":
            raise web.HTTPFound("/umweg")
        if answer == "huge":                                                    # gültig, dahinter über 64 KB Leerraum
            return await pieces(req, GOOD, b" " * (70 * 1024))
        if answer == "html":
            return web.Response(text="<html>Anmelden</html>", content_type="text/html")
        if answer == "nokeys":
            return web.json_response({"keys": [{"kty": "EC"}]})
        return web.Response(body=GOOD, status=503, content_type="application/json")

    async def run():
        server = serve(certs)
        await server.start_server()
        async with aiohttp.ClientSession() as session:
            ks = KeySet(db, session, str(server.make_url("/cdn-cgi/access/certs")))
            assert not await ks.refresh(NOW, force=True)
            assert set(ks.keys) == {"testkey"} and ks.fetched == NOW - 3600 and ks.last_error
        await server.close()

    asyncio.run(run())


def test_certs_url():
    assert certs_url("beispiel") == "https://beispiel.cloudflareaccess.com/cdn-cgi/access/certs"
