"""Wer fragt? Prüft das Token, das Cloudflare Access jeder Anfrage an den Zugang von außen mitgibt (Header
Cf-Access-Jwt-Assertion), selbst und nur mit der Standardbibliothek: RS256 nach RFC 7515/7518, die Signatur nach
RFC 8017 (RSASSA-PKCS1-v1_5) durch Nachbauen und Vergleichen des ganzen Blocks in konstanter Zeit, nie durch
Auslesen der DigestInfo. Den Inhalt liest es erst, wenn die Signatur stimmt.

Geladen wird dieses Modul nur mit eingerichtetem Zugang von außen (Config.public). cloudflared prüft das Token
vorher auch (access.required), und der Port hängt nur in einem eigenen Docker-Netz mit cloudflared: drei
Schichten, und die App verlässt sich auf keine der anderen.

Was ein Token erfüllen muss:
  - genau ein Header, höchstens MAX_TOKEN Zeichen, drei Teile aus [A-Za-z0-9_-], die sich genau so zurück
    kodieren (kein Füllzeichen, keine überzähligen Bits, kein Leerraum)
  - Kopf: alg RS256, kein crit; jku, x5u und jwk werden nie benutzt; kid ein bekannter Schlüssel (sonst
    UnknownKey: KeySet holt die Liste neu)
  - Schlüssel: RSA, n mit MIN_BITS bis MAX_BITS Bit, e genau 65537; Signatur genau so lang wie n und kleiner
  - exp Pflicht, nbf nur wenn da, je LEEWAY_S Spielraum; iss genau https://<team>.cloudflareaccess.com;
    type app; aud eine Liste, die das AUD-Tag der Anwendung enthält
  - Identität: entweder email (klein geschrieben) oder common_name mit sub "" (Service-Token: die Client-ID),
    nie beides. Ob sie etwas darf, entscheidet die Datenbank (identities), nie das Token."""

import base64
import hashlib
import hmac
import json
import logging
import math
import re
from typing import NamedTuple

import aiohttp

from .issues import error_text

log = logging.getLogger("access")

MAX_TOKEN = 8192        # Zeichen: Ein Token mit custom-Claims hat gut 2 KB; aiohttp lässt Header bis 8190 Byte zu
LEEWAY_S = 60
MIN_BITS = 2048
MAX_BITS = 8192
_SEGMENT = re.compile(r"[A-Za-z0-9_-]+")
# DigestInfo von SHA-256 (RFC 8017, 9.2, Anmerkung 1); dahinter folgen die 32 Bytes des Hashwerts
SHA256_DIGESTINFO = bytes.fromhex("3031300d060960864801650304020105000420")


class Invalid(Exception):
    """Token oder Schlüssel gelten nicht. Die Nachricht ist fürs Log des Servers, nie für den, der fragt."""


class UnknownKey(Invalid):
    """Das Token nennt einen Schlüssel (kid), den die Liste nicht kennt: vielleicht hat Cloudflare gewechselt."""


class Identity(NamedTuple):
    kind: str               # "email" (Browser) oder "token" (Service-Token eines Panels)
    value: str              # E-Mail-Adresse, klein geschrieben, oder die Client-ID
    iat: float | None       # wann Access das Token ausgestellt hat, für die Uhrzeit-Abweichung in /health


class RsaKey(NamedTuple):
    n: int
    e: int


def b64url(segment: str) -> bytes:
    """Base64url ohne Füllzeichen, streng: nur das Alphabet, und das Ergebnis kodiert sich genau so zurück. So hat
    jedes Token genau eine Schreibweise."""
    if not isinstance(segment, str) or not _SEGMENT.fullmatch(segment) or len(segment) % 4 == 1:
        raise Invalid("kein Base64url")
    raw = base64.urlsafe_b64decode(segment + "=" * (-len(segment) % 4))
    if base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii") != segment:
        raise Invalid("Base64url nicht kanonisch")
    return raw


def rsa_key(jwk) -> tuple[str, RsaKey]:
    """(kid, Schlüssel) aus einem Eintrag der Schlüsselliste von Access. Nur RSA für RS256-Signaturen, n mit
    MIN_BITS bis MAX_BITS Bit, e genau 65537; alles andere ist Invalid."""
    if not isinstance(jwk, dict) or jwk.get("kty") != "RSA":
        raise Invalid("kein RSA-Schlüssel")
    if jwk.get("alg", "RS256") != "RS256" or jwk.get("use", "sig") != "sig":
        raise Invalid("Schlüssel nicht für RS256-Signaturen")
    kid = jwk.get("kid")
    if not isinstance(kid, str) or not kid:
        raise Invalid("Schlüssel ohne kid")
    n, e = (int.from_bytes(b64url(jwk.get(k)), "big") for k in ("n", "e"))
    if not MIN_BITS <= n.bit_length() <= MAX_BITS or e != 65537:
        raise Invalid("Schlüssel zu kurz, zu lang oder e nicht 65537")
    return kid, RsaKey(n, e)


def rs256_valid(key: RsaKey, signing_input: bytes, signature: bytes) -> bool:
    """RSASSA-PKCS1-v1_5 mit SHA-256 (RFC 8017, 8.2.2): den erwarteten Block bauen und mit dem aus der Signatur
    vergleichen, ganz und in konstanter Zeit."""
    k = (key.n.bit_length() + 7) // 8
    if len(signature) != k:
        return False
    s = int.from_bytes(signature, "big")
    if s >= key.n:
        return False
    em = pow(s, key.e, key.n).to_bytes(k, "big")
    t = SHA256_DIGESTINFO + hashlib.sha256(signing_input).digest()
    return hmac.compare_digest(em, b"\x00\x01" + b"\xff" * (k - len(t) - 3) + b"\x00" + t)


def verify(token: str, keys: dict[str, RsaKey], team: str, aud: str, now: float) -> Identity:
    """Das Token prüfen (siehe oben) und die Identität darin zurückgeben; sonst Invalid, bei unbekanntem
    Schlüssel UnknownKey."""
    if not isinstance(token, str) or len(token) > MAX_TOKEN:
        raise Invalid("Token fehlt oder ist zu lang")
    parts = token.split(".")
    if len(parts) != 3:
        raise Invalid("Token hat nicht drei Teile")
    head = _object(b64url(parts[0]))
    if head.get("alg") != "RS256" or "crit" in head:
        raise Invalid("alg nicht RS256 oder crit gesetzt")
    kid = head.get("kid")
    key = keys.get(kid) if isinstance(kid, str) else None
    if key is None:
        raise UnknownKey(f"Schlüssel {kid!r} unbekannt")
    if not rs256_valid(key, f"{parts[0]}.{parts[1]}".encode("ascii"), b64url(parts[2])):
        raise Invalid("Signatur falsch")
    claims = _object(b64url(parts[1]))                    # erst jetzt: Die Signatur stimmt
    exp, nbf = claims.get("exp"), claims.get("nbf", now)
    if not (_number(exp) and _number(nbf)):
        raise Invalid("exp fehlt, oder exp oder nbf sind keine Zahl")
    if now > exp + LEEWAY_S or now < nbf - LEEWAY_S:
        raise Invalid("abgelaufen oder noch nicht gültig")
    if claims.get("iss") != f"https://{team}.cloudflareaccess.com" or claims.get("type") != "app":
        raise Invalid("iss oder type falsch")
    audience = claims.get("aud")
    if not (isinstance(audience, list) and aud in audience):
        raise Invalid("aud passt nicht")
    email, name = claims.get("email"), claims.get("common_name")
    iat = claims["iat"] if _number(claims.get("iat")) else None
    if isinstance(email, str) and email and name is None:
        return Identity("email", email.lower(), iat)
    if isinstance(name, str) and name and claims.get("sub") == "" and email is None:
        return Identity("token", name.lower(), iat)
    raise Invalid("weder E-Mail noch Service-Token, oder beides")


def certs_url(team: str) -> str:
    return f"https://{team}.cloudflareaccess.com/cdn-cgi/access/certs"


class KeySet:
    """Die Signaturschlüssel von Access. Cloudflare wechselt sie alle sechs Wochen, die alten gelten noch sieben
    Tage: Ein unbekannter kid holt die Liste neu, höchstens einmal pro Minute; dazu einmal am Tag (main.App). Die
    letzte gute Liste liegt in der Datenbank (state "access_keys") und gilt nach einem Neustart sofort, auch ohne
    Netz. Jeder Schlüssel wird einzeln geprüft (rsa_key): Ein unbrauchbarer fällt weg, die anderen gelten. Keine
    Umleitungen, eine Zeitgrenze und eine Größengrenze: Die Antwort kommt aus dem Netz."""

    REFETCH_S = 60
    TIMEOUT_S = 10
    MAX_BYTES = 64 * 1024

    def __init__(self, db, session: aiohttp.ClientSession, url: str):
        self.db, self.session, self.url = db, session, url
        self.keys: dict[str, RsaKey] = {}
        self.fetched = 0.0
        self.last_try = 0.0
        self.last_error: str | None = None
        saved = db.state_get("access_keys")
        if isinstance(saved, dict):
            self.keys, _ = _usable(saved.get("keys"))
            self.fetched = saved["fetched"] if _number(saved.get("fetched")) else 0.0

    async def refresh(self, now: float, force: bool = False) -> bool:
        """Die Liste neu holen; True, wenn eine neue gültige da ist. Ohne force höchstens einmal pro REFETCH_S."""
        if not force and now - self.last_try < self.REFETCH_S:
            return False
        self.last_try = now
        try:
            async with self.session.get(self.url, allow_redirects=False,
                                        timeout=aiohttp.ClientTimeout(total=self.TIMEOUT_S)) as r:
                if r.status != 200:
                    raise Invalid(f"HTTP {r.status}")
                # Bis zum Ende lesen: Übers Netz kommt die Antwort mitunter in Stücken, ein einzelnes read() gäbe nur
                # das erste (die Liste bräche ab, und die Größengrenze griffe nicht).
                raw = bytearray()
                async for chunk in r.content.iter_chunked(16 * 1024):
                    raw += chunk
                    if len(raw) > self.MAX_BYTES:
                        raise Invalid("Antwort zu groß")
            keys, jwks = _usable(_object(bytes(raw)).get("keys"))
            if not keys:
                raise Invalid("kein brauchbarer Schlüssel")
        except Exception as e:                            # Netz, Antwort, Inhalt: die alte Liste bleibt
            self.last_error = error_text(e)
            log.warning("Access-Schlüssel nicht erneuert: %s", self.last_error)
            return False
        self.keys, self.fetched, self.last_error = keys, now, None
        self.db.state_set("access_keys", {"keys": jwks, "fetched": now})
        log.info("Access-Schlüssel erneuert: %s", ", ".join(sorted(self.keys)))
        return True

    def age_h(self, now: float) -> float | None:
        return round((now - self.fetched) / 3600, 1) if self.fetched else None


def _usable(jwks) -> tuple[dict[str, RsaKey], list]:
    """Die brauchbaren Schlüssel einer Liste (rsa_key): nach kid, dazu ihre Einträge zum Speichern. Die anderen
    stehen im Log."""
    keys, good = {}, []
    for jwk in jwks if isinstance(jwks, list) else []:
        try:
            kid, key = rsa_key(jwk)
        except Invalid as e:
            log.warning("Access-Schlüssel übergangen: %s", e)
            continue
        keys[kid] = key
        good.append(jwk)
    return keys, good


def _object(raw: bytes) -> dict:
    try:
        v = json.loads(raw.decode("utf-8"))
    except ValueError as e:                               # UnicodeDecodeError und JSONDecodeError sind ValueError
        raise Invalid("kein JSON") from e
    if not isinstance(v, dict):
        raise Invalid("kein JSON-Objekt")
    return v


def _number(v) -> bool:
    return isinstance(v, int | float) and not isinstance(v, bool) and math.isfinite(v)
