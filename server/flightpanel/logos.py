"""Airline-Logos: aus data/logos/<CODE>.png laden, und wenn keins da ist,
einmalig von Logo-CDNs holen, aufbereiten (logo_image.py) und ablegen.

Quellen (LOGO_URLS) werden der Reihe nach probiert. Genommen werden nur
annähernd quadratische Bildmarken (Seitenverhältnis bis LOGO_MAX_ASPECT):
Schriftzüge werden bei 32 x 32 Pixeln unlesbar, dann lieber der Platzhalter
(logo_image.placeholder_logo, ein graues Flugzeug).

Negative Treffer werden in data/logos/.missing.json gemerkt (Retry nach 7 Tagen).

Eigene Logos liegen in data/logos/own/ (tools/make_logo.py legt sie dort ab): Sie
gehen den abgerufenen vor, und der Abruf fasst sie nie an, auch nicht mit
fetch_logos --force. Neue Dateien werden im Betrieb erkannt."""

import asyncio
import hashlib
import io
import logging
import os
import time

import aiohttp
from PIL import Image

from .logo_image import NotSquare, prepare_logo
from .storage import read_json, write_json

log = logging.getLogger("logos")

EXTS = ("png", "PNG", "gif", "bmp", "jpg", "jpeg")
OWN = "own"                    # Unterordner für eigene Logos
MISSING_TTL = 7 * 24 * 3600
ERROR_RETRY = 10 * 60          # nach Netzfehler erst nach 10 min wieder versuchen
MAX_DOWNLOAD = 2 * 1024 * 1024  # Bytes; Logos sind wenige KB

# Quellen der Reihe nach: (Vorlage, größtes Seitenverhältnis; None = LOGO_MAX_ASPECT).
# Kiwi: quadratische App-Icon-Kacheln, per IATA. FlightAware: Bildmarken, per ICAO.
# Google Flights zuletzt und bis 2:1: viele Bildmarken (Austrian-Pfeil, Eurowings-
# Streifen) sind etwas breiter als quadratisch, bei 32 px aber gut erkennbar.
DEFAULT_URLS = (
    ("https://images.kiwi.com/airlines/128x128/{iata}.png", None),
    ("https://images.kiwi.com/airlines/64/{iata}.png", None),
    ("https://www.flightaware.com/images/airline_logos/90p/{icao}.png", None),
    ("https://www.gstatic.com/flights/airline_logos/70px/{iata}.png", 2.0),
)


class LogoStore:
    def __init__(self, directory: str, size: int, fetch: bool = True,
                 urls: tuple[tuple[str, float | None], ...] = DEFAULT_URLS, max_aspect: float = 1.5,
                 session: aiohttp.ClientSession | None = None):
        """session: ohne gibt es keinen Abruf, nur die vorhandenen Dateien."""
        self.dir = directory
        self.own_dir = os.path.join(directory, OWN)
        self.size = size
        self.fetch_enabled = fetch
        self.urls = urls
        self.max_aspect = max_aspect
        self.session = session
        self._cache: dict[str, tuple[float, Image.Image | None]] = {}
        self._inflight: set[str] = set()
        self._tasks: set[asyncio.Task] = set()      # laufende Abrufe, festgehalten bis sie fertig sind
        self._errors: dict[str, float] = {}
        self._placeholders: dict[str, str | None] = {}
        self._missing_path = os.path.join(directory, ".missing.json")
        self._missing: dict[str, float] = self._load_missing()
        os.makedirs(directory, exist_ok=True)

    # ------------------------------------------------------------ Dateien --
    def path_for(self, code: str) -> str | None:
        """Datei zum Code: ein eigenes Logo vor dem abgerufenen."""
        if not code:
            return None
        for d in (self.own_dir, self.dir):
            for ext in EXTS:
                p = os.path.join(d, f"{code.upper()}.{ext}")
                if os.path.isfile(p):
                    return p
        return None

    def own(self, *codes: str) -> bool:
        """Gibt es für einen der Codes ein eigenes Logo? Das fasst der Abruf nie an."""
        return any(p and os.path.dirname(p) == self.own_dir for p in map(self.path_for, codes))

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
        codes: set[str] = set()
        for d in (self.dir, self.own_dir):
            try:
                codes |= {os.path.splitext(f)[0].upper() for f in os.listdir(d)
                          if f.lower().endswith(EXTS) and not f.startswith(".")}
            except FileNotFoundError:
                pass
        return sorted(codes)

    def missing(self) -> list[str]:
        """Codes (IATA, sonst ICAO), für die keine Quelle ein quadratisches Logo
        geliefert hat und bis heute keins da ist (Kandidaten für Handarbeit)."""
        now = time.time()
        return [k for k, t in self._missing.items() if now - t < MISSING_TTL and not self.path_for(k)]

    # ------------------------------------------------------------- Abruf --
    def _load_missing(self) -> dict[str, float]:
        raw = read_json(self._missing_path)
        return raw if isinstance(raw, dict) else {}

    def _save_missing(self) -> None:
        try:
            write_json(self._missing_path, self._missing)
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
        task = asyncio.create_task(self._fetch(iata, icao))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

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
            for tpl, aspect in self.urls:
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
                    img = await asyncio.to_thread(prepare_logo, data, self.size, True,
                                                  aspect or self.max_aspect)
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


def parse_urls(spec: str) -> tuple[tuple[str, float | None], ...]:
    """LOGO_URLS: kommagetrennt, je "Vorlage" oder "Vorlage|Seitenverhältnis"."""
    out = []
    for part in (p.strip() for p in spec.split(",")):
        if part:
            tpl, _, aspect = part.partition("|")
            out.append((tpl.strip(), float(aspect) if aspect.strip() else None))
    return tuple(out)


def make_logo_store(cfg, session: aiohttp.ClientSession | None = None) -> LogoStore:
    return LogoStore(os.path.join(cfg.data_dir, "logos"), cfg.logo_size, fetch=cfg.logo_fetch,
                     urls=parse_urls(cfg.logo_urls) or DEFAULT_URLS, max_aspect=cfg.logo_max_aspect, session=session)
