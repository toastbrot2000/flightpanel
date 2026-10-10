"""Airline-Logos: aus data/logos/<CODE>.png laden, und wenn keins da ist,
einmalig von einem Logo-CDN per IATA-Code holen, aufbereiten und ablegen.

Aufbereitung: weißen Hintergrund entfernen (Flood-Fill vom Rand), auf den
Inhalt zuschneiden, proportional in LOGO_SIZE einpassen, auf Schwarz legen.

Negative Treffer werden in data/logos/.missing.json gemerkt (Retry nach 7 Tagen).
Neue Dateien werden im Betrieb erkannt, man kann also jederzeit handgemachte
Logos ablegen; die haben Vorrang vor dem Abruf."""

import asyncio
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


def prepare_logo(data: bytes, size: int, strip_white: bool = True) -> Image.Image:
    img = Image.open(io.BytesIO(data)).convert("RGBA")
    if strip_white:
        img = remove_background(img)
    bbox = img.getchannel("A").getbbox()
    if bbox:
        img = img.crop(bbox)
    w, h = img.size
    s = max(w, h, 1)
    sq = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    sq.paste(img, ((s - w) // 2, (s - h) // 2))
    small = sq.resize((size, size), Image.LANCZOS)
    bg = Image.new("RGBA", (size, size), (0, 0, 0, 255))
    bg.alpha_composite(small)
    return bg.convert("RGB")


class LogoStore:
    def __init__(self, directory: str, size: int, fetch: bool = True,
                 url_template: str = "https://pics.avs.io/200/200/{code}.png"):
        self.dir = directory
        self.size = size
        self.fetch_enabled = fetch
        self.url_template = url_template
        self.session: aiohttp.ClientSession | None = None
        self._cache: dict[str, tuple[float, Image.Image | None]] = {}
        self._inflight: set[str] = set()
        self._errors: dict[str, float] = {}
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
        """IATA-Codes, für die der Abruf zuletzt nichts geliefert hat (Kandidaten für Handarbeit)."""
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

    def ensure(self, iata: str) -> None:
        """Startet im Hintergrund den Abruf, falls nötig. Blockiert nicht."""
        iata = (iata or "").upper()
        if not self.fetch_enabled or not self.session or len(iata) != 2:
            return
        if self.path_for(iata) or iata in self._inflight:
            return
        if time.time() - self._missing.get(iata, 0) < MISSING_TTL:
            return
        if time.time() - self._errors.get(iata, 0) < ERROR_RETRY:
            return
        self._inflight.add(iata)
        asyncio.create_task(self._fetch(iata))

    async def fetch_now(self, iata: str) -> bool:
        """Synchroner Abruf, für das Seed-Skript."""
        iata = iata.upper()
        if self.path_for(iata):
            return True
        return await self._fetch(iata)

    async def _fetch(self, iata: str) -> bool:
        url = self.url_template.format(code=iata)
        try:
            async with self.session.get(url, timeout=aiohttp.ClientTimeout(total=15)) as r:
                if r.status != 200 or not r.headers.get("Content-Type", "").startswith("image"):
                    log.info("Kein Logo für %s (HTTP %s)", iata, r.status)
                    self._missing[iata] = time.time()
                    self._save_missing()
                    return False
                data = await r.read()
            img = await asyncio.to_thread(prepare_logo, data, self.size)
            # Leeres oder fast leeres Ergebnis (z. B. Platzhalterbild) verwerfen
            if sum(1 for p in img.getdata() if max(p) > 40) < self.size * self.size * 0.05:
                log.info("Logo für %s ist leer, verworfen", iata)
                self._missing[iata] = time.time()
                self._save_missing()
                return False
            img.save(os.path.join(self.dir, f"{iata}.png"))
            log.info("Logo geholt: %s", iata)
            return True
        except Exception as e:
            log.warning("Logo-Abruf %s: %s", iata, e)
            self._errors[iata] = time.time()
            return False
        finally:
            self._inflight.discard(iata)
