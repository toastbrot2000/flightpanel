"""Leerlauf: Wenn kein Flug zu zeigen ist, wechseln Karten.

Jede Karte (cards.py) hat eine Bedingung und eine Zeichenfunktion. Die Bedingung
schaut in den Kontext (Display-Modell, Statistik, Wetter, Sonne/ISS, Uhrzeit) und
gibt ein Hit mit den Daten zurück, oder None, wenn die Karte gerade nichts zu sagen
hat. Fällt eine Quelle aus (kein METAR, keine Bahndaten), liefern ihre Karten None
und die anderen kommen dran; die Uhr geht immer.

Auswahl pro Zeitscheibe (IDLE_SECONDS):
  1. Dringendes bricht durch: Notfall-Squawk (schon in render), NEU!-Airline,
     Flieger in unter 3 min, ISS gerade über dem Haus. Mehrere wechseln sich ab.
  2. Sonst mit IDLE_CLOCK_SHARE die Uhr, ansonsten gewichteter Zufall unter den
     übrigen Karten, ohne die der letzten Scheiben.

Gerendert wird bei jedem Abruf des ESP32 (alle 2 s); Animationen hängen nur von
der Zeit seit Beginn der Scheibe ab, nicht von einem Bildzähler."""

import json
import logging
import random
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Callable

from PIL import Image

from .config import Config

log = logging.getLogger("idle")

RECENT = 3                  # so viele zuletzt gezeigte Karten kommen nicht gleich wieder


@dataclass
class Hit:
    data: Any = None
    urgent: bool = False
    weight: float | None = None     # None: Gewicht der Karte


@dataclass
class Ctx:
    cfg: Config
    model: Any
    now: float
    stats: Any = None
    metar: Any = None
    astro: Any = None
    logos: Any = None
    names: dict = field(default_factory=dict)
    fun: dict = field(default_factory=dict)       # data/jokes.json
    draw_flight: Any = None                       # Renderer: Flug wie im Normalbetrieb zeichnen

    @property
    def live(self) -> bool:
        """Flugdaten frisch genug für Karten, die sich auf sie stützen."""
        return not self.model.stale and self.model.source not in ("startup", "none")


@dataclass
class Card:
    id: str
    cond: Callable[[Ctx], Hit | None]
    draw: Callable[[Image.Image, Ctx, Any, float, float], None]
    weight: float = 1.0
    group: str = ""
    live: bool = False                                  # braucht frische Flugdaten
    demo: Callable[[Ctx], Any] | None = None            # Beispieldaten für ?card=<id>


class IdleScreen:
    def __init__(self, cfg: Config, cards: list[Card], **providers):
        self.cfg = cfg
        self.cards = {c.id: c for c in cards}
        self.providers = providers
        self._slot: int | None = None
        self._cur: str | None = None
        self._recent: deque[str] = deque(maxlen=RECENT)
        self._errors: dict[str, float] = {}
        self.last_pick: str | None = None

    def context(self, model, now: float) -> Ctx:
        return Ctx(cfg=self.cfg, model=model, now=now, **self.providers)

    def _fail(self, cid: str, what: str, e: Exception) -> None:
        """Fehler einer Karte loggen, höchstens einmal pro Stunde je Karte."""
        now = time.time()
        if now - self._errors.get(cid, 0) > 3600:
            self._errors[cid] = now
            log.exception("Karte %s: %s fehlgeschlagen: %s", cid, what, e)

    def candidates(self, ctx: Ctx) -> dict[str, Hit]:
        out: dict[str, Hit] = {}
        off = self.cfg.idle_disabled
        for cid, c in self.cards.items():
            if cid in off or (c.live and not ctx.live):
                continue
            try:
                h = c.cond(ctx)
            except Exception as e:
                self._fail(cid, "Bedingung", e)
                continue
            if h is not None:
                out[cid] = h
        return out

    def slot(self, now: float) -> int:
        return int(now // max(1, self.cfg.idle_seconds))

    def pick(self, ctx: Ctx) -> tuple[str, Hit]:
        slot = self.slot(ctx.now)
        cands = self.candidates(ctx)
        urgent = sorted(cid for cid, h in cands.items() if h.urgent)
        if urgent:
            cid = urgent[slot % len(urgent)]
            return cid, cands[cid]
        if slot == self._slot and self._cur in cands:
            return self._cur, cands[self._cur]
        if slot != self._slot and self._cur:
            self._recent.append(self._cur)
        self._slot = slot
        rng = random.Random(slot)
        pool = {cid: h for cid, h in cands.items() if cid != "clock"}
        last = self._recent[-1] if self._recent else None
        if not pool or ("clock" in cands and last != "clock" and rng.random() < self.cfg.idle_clock_share):
            cid = "clock" if "clock" in cands else next(iter(cands), "clock")
        else:
            fresh = {k: h for k, h in pool.items() if k not in self._recent}
            if not fresh:
                fresh = {k: h for k, h in pool.items() if k not in list(self._recent)[-1:]} or pool
            ids = sorted(fresh)
            weights = [fresh[k].weight if fresh[k].weight is not None else self.cards[k].weight for k in ids]
            cid = rng.choices(ids, weights=weights)[0]
        self._cur = cid
        return cid, cands.get(cid, Hit())

    def draw(self, img: Image.Image, model, now: float, f: float = 1.0, force: str | None = None,
             preview: bool = False) -> str:
        """Zeichnet die dran befindliche Karte (oder force) und gibt ihre ID zurück. preview:
        Vorschau mit ?card=, zählt nicht als "gerade gezeigt"."""
        ctx = self.context(model, now)
        if force and force in self.cards:
            c = self.cards[force]
            try:
                h = c.cond(ctx)
            except Exception as e:
                self._fail(force, "Bedingung", e)
                h = None
            if h is None and c.demo is not None:
                h = Hit(c.demo(ctx))
            cid, hit = force, h or Hit()
        else:
            cid, hit = self.pick(ctx)
        t_in = now - self.slot(now) * max(1, self.cfg.idle_seconds)
        try:
            self.cards[cid].draw(img, ctx, hit.data, t_in, f)
        except Exception as e:
            self._fail(cid, "Zeichnen", e)
            img.paste((0, 0, 0), (0, 0, img.width, img.height))
            if cid != "clock" and "clock" in self.cards:
                self.cards["clock"].draw(img, ctx, None, t_in, f)
                cid = "clock"
        if not preview:
            self.last_pick = cid
        return cid

    def status(self, model, now: float) -> dict:
        """Für GET /idle: welche Karten gerade etwas zu sagen haben."""
        ctx = self.context(model, now)
        cands = self.candidates(ctx)
        off = self.cfg.idle_disabled

        def js(v):
            try:
                return json.loads(json.dumps(v, default=lambda o: getattr(o, "__dict__", str(o))))
            except Exception:
                return str(v)

        return {
            "slot_seconds": self.cfg.idle_seconds,
            "showing": self.last_pick,
            "recent": list(self._recent),
            "cards": [{"id": cid, "group": c.group, "weight": c.weight,
                       "state": "off" if cid in off else "active" if cid in cands else "idle",
                       "urgent": bool(cands.get(cid) and cands[cid].urgent),
                       "data": js(cands[cid].data) if cid in cands else None}
                      for cid, c in self.cards.items()],
        }
