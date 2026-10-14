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
    nie beides. Ob sie etwas darf, entscheidet die Datenbank (identities), nie das Token.

Gate ist der Türsteher davor, eine Middleware für den Zugang von außen (web.make_public_app). Er lässt nur durch, was
die Route ausdrücklich erlaubt; jede Route meldet dafür eine Identitätsklasse an (web.add):
  email   Browser eines Freundes (E-Mail-Code): Vorschau, Modell, Statistik, Einstellungen seines Panels
  token   Service-Token eines Panels: /panel/frame
  public  ohne Anmeldung: nur /panel/firmware.txt und .bin (signiert, ohne Geheimnisse; Rettungsweg der Panels)
Eine Route ohne Klasse bekommt immer 403. Sonst, der Reihe nach:
  - public: ein gemeinsamer Eimer für alle (PUBLIC_RATE, PUBLIC_BURST), sonst 429. Ein gültiges Service-Token
    eines freigeschalteten Panels wählt dessen Firmware-Kanal, sonst gilt stable.
  - genau ein Token-Header (keiner oder mehrere: 401), geprüft mit verify(); ein unbekannter kid holt die Schlüssel
    neu (KeySet: höchstens einmal pro Minute) und prüft noch einmal. Jede Ausnahme dabei: 401.
  - die Art der Identität gleich der Klasse der Route (sonst 403), die Identität in der Datenbank einem Panel
    zugeordnet (identities, sonst 403 "nicht freigeschaltet"), ihr eigener Eimer (RATE, BURST) nicht leer (sonst
    429). Dann bekommt der Handler das Panel (panel_id), und nur dieses.
  - Kennt der Router den Pfad nicht (404) oder nicht die Methode (405): 401 vor der Anmeldung, wie überall, damit
    ein Fremder nicht erfährt, was es gibt; danach der Fehler des Routers.
Ins Log kommen nur feste Sätze (REASONS), nie etwas aus dem Token, auch nicht der kid; /health zählt die Absagen je
Grund, die gebremsten Anfragen und die Uhrzeit-Abweichung (report)."""

import base64
import hashlib
import hmac
import json
import logging
import math
import re
import time
from collections.abc import Callable, Mapping
from time import monotonic
from typing import NamedTuple, NoReturn

import aiohttp
from aiohttp import web

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


# ------------------------------------------------------------------------------------------- Türsteher --
# Identitätsklassen der Routen; EMAIL und TOKEN sind zugleich die Arten von Identity.kind
EMAIL, TOKEN, PUBLIC = "email", "token", "public"
CLASSES = (EMAIL, TOKEN, PUBLIC)
HEADER = "Cf-Access-Jwt-Assertion"
WARN_EVERY_S = 60       # jeder Grund einer Absage höchstens so oft im Log
PANEL = web.RequestKey("panel", str)                    # das Panel der geprüften Anmeldung (Gate.panel_id)

# Warum eine Anfrage abgewiesen wurde: Schlüssel für /health, fester Satz fürs Log
REASONS = {
    "missing": "ohne Token",
    "several": "mit mehreren Tokens",
    "invalid": "mit ungültigem Token (Form, Signatur, Aussteller, Anwendung oder Ablauf)",
    "unknown_key": "mit einem Schlüssel, den Access auch nach dem Neuladen nicht kennt",
    "wrong_class": "mit einer Anmeldung, die nicht zur Seite passt (Panel auf einer Browserseite oder umgekehrt)",
    "not_allowed": "mit einer Anmeldung, die keinem Panel zugeordnet ist (panels email, panels token)",
    "undeclared": "auf einer Route ohne Identitätsklasse",
}
UNAUTHORIZED = "Anmeldung fehlt oder ist ungültig.\n"
WRONG_CLASS = "Diese Seite gibt es für diese Anmeldung nicht.\n"
NOT_ALLOWED = "Diese Anmeldung ist nicht freigeschaltet.\n"
UNDECLARED = "Gesperrt.\n"
TOO_MANY = "Zu viele Anfragen, bitte langsamer.\n"


class Bucket:
    """Token-Bucket: im Mittel rate Anfragen pro Sekunde, burst auf einmal."""

    def __init__(self, rate: float, burst: int, now: float):
        self.rate, self.burst = rate, burst
        self.tokens, self.at = float(burst), now

    def take(self, now: float) -> bool:
        self.tokens = min(float(self.burst), self.tokens + max(0.0, now - self.at) * self.rate)
        self.at = now
        if self.tokens < 1:
            return False
        self.tokens -= 1
        return True


class Gate:
    """Der Türsteher des Zugangs von außen (siehe oben). keys: die Schlüssel von Access, team und aud aus .env
    (ACCESS_TEAM, ACCESS_AUD), db: wer welches Panel sehen darf. clock: Uhr der Eimer (monoton; Tests geben eine
    eigene). Wie spät es ist, sagt für die Tokens time.time()."""

    RATE, BURST = 2.0, 10                   # je Anmeldung: ein Browser, der hämmert, bremst nicht den ganzen Server
    PUBLIC_RATE, PUBLIC_BURST = 5.0, 20     # die zwei Firmware-Dateien, alle zusammen (Cloudflare cacht das Abbild)

    def __init__(self, keys: KeySet, team: str, aud: str, db, clock: Callable[[], float] = monotonic):
        self.keys, self.team, self.aud, self.db, self.clock = keys, team, aud, db, clock
        self.buckets: dict[tuple[str, str], Bucket] = {}        # nur für freigeschaltete Anmeldungen
        self.public_bucket = Bucket(self.PUBLIC_RATE, self.PUBLIC_BURST, clock())
        self.failures: dict[str, int] = {}                     # Absagen je Grund (REASONS)
        self.limited = 0                                       # Anfragen mit 429
        self.skew_s: float | None = None                       # zuletzt: jetzt minus iat eines Service-Tokens
        self._warned: dict[str, float] = {}

    def middleware(self, routes: Mapping[web.AbstractRoute, str | None]):
        """Die Middleware für eine Anwendung, deren Routen routes mit ihrer Klasse vermerkt (web.add)."""
        @web.middleware
        async def gate(req: web.Request, handler) -> web.StreamResponse:
            refused = getattr(req.match_info, "http_exception", None)     # 404 oder 405 des Routers
            kind = None if refused is not None else routes.get(req.match_info.route)
            if refused is None and kind not in CLASSES:
                self._refuse("undeclared")
                raise web.HTTPForbidden(text=UNDECLARED)
            if kind == PUBLIC:
                if not self.public_bucket.take(self.clock()):
                    self._too_many()
                pid = self._channel_panel(req)
                if pid is not None:
                    req[PANEL] = pid
                return await handler(req)
            who = await self._identity(req)
            if refused is None and who.kind != kind:
                self._refuse("wrong_class")
                raise web.HTTPForbidden(text=WRONG_CLASS)
            pid = self.db.identity_panel(who.kind, who.value)
            if pid is None:
                self._refuse("not_allowed")
                raise web.HTTPForbidden(text=NOT_ALLOWED)
            bucket = self.buckets.get((who.kind, who.value))
            if bucket is None:
                bucket = self.buckets[(who.kind, who.value)] = Bucket(self.RATE, self.BURST, self.clock())
            if not bucket.take(self.clock()):
                self._too_many()
            if refused is not None:
                raise refused
            req[PANEL] = pid
            return await handler(req)
        return gate

    def panel_id(self, req: web.Request) -> str | None:
        """Das Panel, das die Anmeldung dieser Anfrage sehen darf; None auf den offenen Routen ohne Panel-Token."""
        return req.get(PANEL)

    async def _identity(self, req: web.Request) -> Identity:
        """Die geprüfte Identität der Anfrage, sonst 401. Jede Ausnahme beim Prüfen ist 401, ihr Text bleibt hier."""
        tokens = req.headers.getall(HEADER, [])
        if len(tokens) != 1:
            self._refuse("several" if tokens else "missing")
            raise web.HTTPUnauthorized(text=UNAUTHORIZED)
        now = time.time()
        try:
            try:
                who = verify(tokens[0], self.keys.keys, self.team, self.aud, now)
            except UnknownKey:
                await self.keys.refresh(now)                   # höchstens einmal pro Minute (KeySet)
                who = verify(tokens[0], self.keys.keys, self.team, self.aud, now)
        except UnknownKey:
            reason = "unknown_key"
        except Exception:
            reason = "invalid"
        else:
            if who.kind == TOKEN and who.iat is not None:
                # Nur Service-Tokens: Access stellt sie je Anfrage aus. Das Token eines Browsers stammt von der
                # Anmeldung, sein iat kann Stunden alt sein.
                self.skew_s = round(now - who.iat, 1)
            return who
        self._refuse(reason)
        raise web.HTTPUnauthorized(text=UNAUTHORIZED)

    def _channel_panel(self, req: web.Request) -> str | None:
        """Auf den offenen Routen: das Panel eines gültigen Service-Tokens, wenn genau eines mitkommt und das Panel
        freigeschaltet ist; es wählt den Firmware-Kanal. Ohne Neuladen der Schlüssel (diese Routen erreicht jeder,
        und ein Fremder soll keine Abrufe bei Cloudflare auslösen) und ohne Zählen: Was nicht passt, ist hier einfach
        keine Anmeldung."""
        tokens = req.headers.getall(HEADER, [])
        if len(tokens) != 1:
            return None
        try:
            who = verify(tokens[0], self.keys.keys, self.team, self.aud, time.time())
        except Exception:
            return None
        return self.db.identity_panel(who.kind, who.value) if who.kind == TOKEN else None

    def _refuse(self, reason: str) -> None:
        self.failures[reason] = self.failures.get(reason, 0) + 1
        self._warn(reason, "Zugang von außen: Anfrage %s abgelehnt", REASONS[reason])

    def _too_many(self) -> NoReturn:
        self.limited += 1
        self._warn("limited", "Zugang von außen: Anfragen gebremst (429)")
        raise web.HTTPTooManyRequests(text=TOO_MANY, headers={"Retry-After": "1"})

    def _warn(self, key: str, msg: str, *args) -> None:
        """Höchstens einmal pro WARN_EVERY_S je Grund ins Log: Ein Panel mit falschem Token fragt alle 2 s."""
        now = self.clock()
        if now - self._warned.get(key, float("-inf")) >= WARN_EVERY_S:
            self._warned[key] = now
            log.warning(msg, *args)

    def report(self, now: float) -> dict:
        """Für /health am LAN-Port."""
        return {"keys": len(self.keys.keys), "keys_age_h": self.keys.age_h(now),
                "keys_last_error": self.keys.last_error, "auth_failures": dict(sorted(self.failures.items())),
                "rate_limited": self.limited, "clock_skew_s": self.skew_s}
