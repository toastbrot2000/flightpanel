"""Airline-Logos: aus data/logos/<CODE>.png laden, und wenn keins da ist,
einmalig von Logo-CDNs holen, aufbereiten und ablegen.

Quellen (LOGO_URLS) werden der Reihe nach probiert. Genommen werden nur
annähernd quadratische Bildmarken (Seitenverhältnis bis LOGO_MAX_ASPECT):
Schriftzüge werden bei 30 x 30 Pixeln unlesbar, dann lieber Layout A ohne Logo.

Aufbereitung: weißen Hintergrund entfernen (Flood-Fill vom Rand), auf den
Inhalt zuschneiden, proportional in LOGO_SIZE einpassen, auf Schwarz legen.

Negative Treffer werden in data/logos/.missing.json gemerkt (Retry nach 7 Tagen).
Neue Dateien werden im Betrieb erkannt, man kann also jederzeit handgemachte
Logos ablegen; die haben Vorrang vor dem Abruf."""

import asyncio
import hashlib
import io
import json
import logging
import os
import time

import aiohttp
from PIL import Image

log = logging.getLogger("logos")

EXTS = ("png", "PNG", "gif", "bmp", "jpg", "jpeg")
MISSING_TTL = 7 * 24 * 3600
ERROR_RETRY = 10 * 60          # nach Netzfehler erst nach 10 min wieder versuchen
MAX_DOWNLOAD = 2 * 1024 * 1024 # Bytes; Logos sind wenige KB
MAX_SOURCE_PX = 512            # größere Bilder vor dem Flood-Fill verkleinern

# Schutz vor Dekompressionsbomben aus dem Netz: Logos haben selten mehr als 1000 x 1000,
# Pillow bricht ab dem Doppelten ab.
Image.MAX_IMAGE_PIXELS = 4_000_000

# Kiwi: quadratische App-Icon-Kacheln, per IATA. FlightAware: Bildmarken, per ICAO.
DEFAULT_URLS = (
    "https://images.kiwi.com/airlines/128x128/{iata}.png",
    "https://images.kiwi.com/airlines/64/{iata}.png",
    "https://www.flightaware.com/images/airline_logos/90p/{icao}.png",
)


class NotSquare(ValueError):
    pass


def remove_background(img: Image.Image, threshold: int = 235) -> Image.Image:
    """Macht alle hellen Pixel transparent, die vom Rand aus mit hellen Pixeln
    verbunden sind (Hintergrund), lässt helle Flächen im Logo selbst stehen."""
    img = img.convert("RGBA")
    w, h = img.size
    px = img.load()

    def bright(x, y):
        r, g, b, a = px[x, y]
        return a < 10 or (r >= threshold and g >= threshold and b >= threshold)

    seen = bytearray(w * h)
    stack = [(x, y) for x in range(w) for y in (0, h - 1)] + [(x, y) for y in range(h) for x in (0, w - 1)]
    while stack:
        x, y = stack.pop()
        i = y * w + x
        if seen[i] or not bright(x, y):
            continue
        seen[i] = 1
        r, g, b, a = px[x, y]
        px[x, y] = (r, g, b, 0)
        if x > 0: stack.append((x - 1, y))
        if x < w - 1: stack.append((x + 1, y))
        if y > 0: stack.append((x, y - 1))
        if y < h - 1: stack.append((x, y + 1))
    return img


def prepare_logo(data: bytes, size: int, strip_white: bool = True,
                 max_aspect: float | None = None) -> Image.Image:
    """max_aspect: Seitenverhältnis des Inhalts nach dem Zuschnitt, ab dem das
    Bild als Schriftzug gilt und mit NotSquare abgelehnt wird."""
    img = Image.open(io.BytesIO(data)).convert("RGBA")
    if max(img.size) > MAX_SOURCE_PX:
        img.thumbnail((MAX_SOURCE_PX, MAX_SOURCE_PX), Image.LANCZOS)
    if strip_white:
        img = remove_background(img)
    bbox = img.getchannel("A").getbbox()
    if bbox:
        img = img.crop(bbox)
    w, h = img.size
    if max_aspect is not None and max(w, h) > max_aspect * max(1, min(w, h)):
        raise NotSquare(f"{w}x{h}")
    s = max(w, h, 1)
    sq = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    sq.paste(img, ((s - w) // 2, (s - h) // 2))
    small = sq.resize((size, size), Image.LANCZOS)
    bg = Image.new("RGBA", (size, size), (0, 0, 0, 255))
    bg.alpha_composite(small)
    return bg.convert("RGB")


class LogoStore:
    def __init__(self, directory: str, size: int, fetch: bool = True,
                 urls: tuple[str, ...] = DEFAULT_URLS, max_aspect: float = 1.5):
        self.dir = directory
        self.size = size
        self.fetch_enabled = fetch
        self.urls = urls
        self.max_aspect = max_aspect
        self.session: aiohttp.ClientSession | None = None
        self._cache: dict[str, tuple[float, Image.Image | None]] = {}
        self._inflight: set[str] = set()
        self._errors: dict[str, float] = {}
        self._placeholders: dict[str, str | None] = {}
        self._missing_path = os.path.join(directory, ".missing.json")
        self._missing: dict[str, float] = self._load_missing()
        os.makedirs(directory, exist_ok=True)

    # ------------------------------------------------------------ Dateien --
    def path_for(self, code: str) -> str | None:
        if not code:
            return None
        for ext in EXTS:
            p = os.path.join(self.dir, f"{code.upper()}.{ext}")
            if os.path.isfile(p):
                return p
        return None

    def get(self, *codes: str) -> Image.Image | None:
        """Erstes vorhandene Logo für die angegebenen Codes (z. B. IATA, ICAO)."""
        for code in codes:
            p = self.path_for(code)
            if p is None:
                continue
            try:
                mtime = os.path.getmtime(p)
            except OSError:
                continue
            hit = self._cache.get(p)
            if hit and hit[0] == mtime:
                return hit[1]
            try:
                img = Image.open(p).convert("RGBA")
                if img.size != (self.size, self.size):
                    buf = io.BytesIO()
                    img.save(buf, "PNG")
                    img = prepare_logo(buf.getvalue(), self.size, strip_white=False)
                else:
                    bg = Image.new("RGBA", img.size, (0, 0, 0, 255))
                    bg.alpha_composite(img)
                    img = bg.convert("RGB")
                self._cache[p] = (mtime, img)
                return img
            except Exception as e:
                log.warning("Logo %s nicht lesbar: %s", p, e)
                self._cache[p] = (mtime, None)
        return None

    def available(self) -> list[str]:
        try:
            return sorted(os.path.splitext(f)[0].upper() for f in os.listdir(self.dir)
                          if f.lower().endswith(EXTS) and not f.startswith("."))
        except FileNotFoundError:
            return []

    def missing(self) -> list[str]:
        """Codes (IATA, sonst ICAO), für die keine Quelle ein quadratisches Logo
        geliefert hat (Kandidaten für Handarbeit)."""
        now = time.time()
        return [k for k, t in self._missing.items() if now - t < MISSING_TTL]

    # ------------------------------------------------------------- Abruf --
    def _load_missing(self) -> dict[str, float]:
        try:
            with open(self._missing_path, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}

    def _save_missing(self) -> None:
        try:
            with open(self._missing_path, "w", encoding="utf-8") as f:
                json.dump(self._missing, f)
        except OSError as e:
            log.warning("missing.json: %s", e)

    @staticmethod
    def key(iata: str, icao: str = "") -> str:
        """Dateiname und Schlüssel: IATA, wenn bekannt, sonst ICAO."""
        iata, icao = (iata or "").upper(), (icao or "").upper()
        if len(iata) == 2 and iata.isalnum():
            return iata
        if len(icao) == 3 and icao.isalpha():
            return icao
        return ""

    def ensure(self, iata: str, icao: str = "") -> None:
        """Startet im Hintergrund den Abruf, falls nötig. Blockiert nicht."""
        k = self.key(iata, icao)
        if not self.fetch_enabled or not self.session or not k:
            return
        if self.path_for(iata) or self.path_for(icao) or k in self._inflight:
            return
        if time.time() - self._missing.get(k, 0) < MISSING_TTL:
            return
        if time.time() - self._errors.get(k, 0) < ERROR_RETRY:
            return
        self._inflight.add(k)
        asyncio.create_task(self._fetch(iata, icao))

    async def fetch_now(self, iata: str, icao: str = "") -> bool:
        """Direkter Abruf, für das Seed-Skript."""
        if self.path_for(iata) or self.path_for(icao):
            return True
        if not self.key(iata, icao):
            return False
        return await self._fetch(iata, icao)

    async def _download(self, url: str) -> bytes | None:
        """Bild laden, höchstens MAX_DOWNLOAD Bytes. None heißt: gibt es dort nicht."""
        async with self.session.get(url, timeout=aiohttp.ClientTimeout(total=15)) as r:
            if r.status in (403, 404, 410):
                return None
            r.raise_for_status()
            if not r.headers.get("Content-Type", "").startswith("image"):
                return None
            if int(r.headers.get("Content-Length") or 0) > MAX_DOWNLOAD:
                return None
            data = bytearray()
            async for chunk in r.content.iter_chunked(64 * 1024):
                data += chunk
                if len(data) > MAX_DOWNLOAD:
                    return None
            return bytes(data)

    async def _placeholder(self, tpl: str) -> str | None:
        """Hash der Antwort für einen Code, den es nicht gibt. Manche Quellen liefern
        dann statt 404 ein Ersatzbild (graues Flugzeug), das kein Logo ist."""
        if tpl not in self._placeholders:
            try:
                data = await self._download(tpl.format(iata="ZZ", icao="ZZZ", code="ZZ"))
            except Exception:
                return None   # beim nächsten Mal wieder probieren
            self._placeholders[tpl] = hashlib.md5(data).hexdigest() if data else None
        return self._placeholders[tpl]

    async def _fetch(self, iata: str, icao: str) -> bool:
        """Quellen der Reihe nach probieren, das erste quadratische Logo nehmen."""
        iata, icao = (iata or "").upper(), (icao or "").upper()
        k = self.key(iata, icao)
        net_error = False
        try:
            for tpl in self.urls:
                if ("{iata}" in tpl or "{code}" in tpl) and len(iata) != 2:
                    continue
                if "{icao}" in tpl and len(icao) != 3:
                    continue
                url = tpl.format(iata=iata, icao=icao, code=iata)
                try:
                    data = await self._download(url)
                except Exception as e:
                    log.warning("Logo-Abruf %s: %s", url, e)
                    net_error = True
                    continue
                if data is None or hashlib.md5(data).hexdigest() == await self._placeholder(tpl):
                    continue
                try:
                    img = await asyncio.to_thread(prepare_logo, data, self.size, True, self.max_aspect)
                except NotSquare as e:
                    log.info("Logo %s ist ein Schriftzug (%s), übersprungen", url, e)
                    continue
                except Exception as e:
                    log.info("Logo %s nicht lesbar: %s", url, e)
                    continue
                # Leeres oder fast leeres Ergebnis (z. B. Platzhalterbild) verwerfen
                if sum(1 for p in img.getdata() if max(p) > 40) < self.size * self.size * 0.05:
                    log.info("Logo %s ist leer, verworfen", url)
                    continue
                img.save(os.path.join(self.dir, f"{k}.png"))
                log.info("Logo geholt: %s (%s)", k, url)
                return True
            if net_error:
                self._errors[k] = time.time()   # später noch einmal
            else:
                log.info("Kein quadratisches Logo für %s", k)
                self._missing[k] = time.time()
                self._save_missing()
            return False
        finally:
            self._inflight.discard(k)


def make_logo_store(cfg) -> LogoStore:
    return LogoStore(os.path.join(cfg.data_dir, "logos"), cfg.logo_size, fetch=cfg.logo_fetch,
                     urls=cfg.logo_urls or DEFAULT_URLS, max_aspect=cfg.logo_max_aspect)
