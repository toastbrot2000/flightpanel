"""Leerlauf: Wenn keine Airline zu zeigen ist, wechseln Karten.

Jede Karte (cards/) hat eine Bedingung und eine Zeichenfunktion. Die Bedingung
schaut in den Kontext (Display-Modell, Statistik, Wetter, Sonne/ISS, Uhrzeit) und
gibt ein Hit mit den Daten zurück, oder None, wenn die Karte gerade nichts zu sagen
hat. Fällt eine Quelle aus (kein METAR, keine Bahndaten), liefern ihre Karten None
und die anderen kommen dran.

Auswahl pro Zeitscheibe (IDLE_SECONDS):
  1. Dringendes bricht durch: NEU!-Airline, Flieger in unter 3 min, ISS gerade
     über dem Haus. Mehrere wechseln sich ab. (Den Notfall-Squawk zeigt render.py
     vor allem anderen.)
  2. Sonst mit IDLE_CLOCK_SHARE die Uhr, ansonsten gewichteter Zufall unter den
     Karten der höchsten Stufe, die etwas zu zeigen hat (docs/screens.md):
     2 Verkehr am Platz, 3 wenig los, 4 Spielereien. Stufe 4 nur, wenn gar nichts
     los ist (Ctx.quiet), dann gemischt mit Stufe 3. Ohne die der letzten Scheiben.

Jede Karte lässt sich abschalten (CARD_<ID>=0). Hat keine Karte etwas zu zeigen,
auch nicht die Uhr, bleibt das Panel dunkel.

Gerendert wird bei jedem Abruf des ESP32 (alle 2 s); Animationen hängen nur von
der Zeit seit Beginn der Scheibe ab, nicht von einem Bildzähler."""

import json
import logging
import random
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from PIL import Image

from .config import Config

log = logging.getLogger("idle")

RECENT = 3                  # so viele zuletzt gezeigte Karten kommen nicht gleich wieder
QUIET_SINCE_S = 15 * 60     # gar nichts los: so lange kein Start und keine Landung am Platz
QUIET_AHEAD_S = 10 * 60     # ... und so lange landet auch keiner


@dataclass
class Hit:
    data: Any = None
    urgent: bool = False
    weight: float | None = None     # None: Gewicht der Karte


@dataclass
class Ctx:
    """Alles, was eine Karte sehen darf."""
    cfg: Config
    model: Any                                    # model.DisplayModel
    now: float
    stats: Any = None                             # stats.Stats
    metar: Any = None                             # metar.MetarSource
    astro: Any = None                             # astro.Iss
    logos: Any = None                             # logos.LogoStore
    names: dict = field(default_factory=dict)     # data/airlines.json
    fun: dict = field(default_factory=dict)       # data/jokes.json
    draw_flight: Any = None                       # Renderer: Flug wie im Normalbetrieb zeichnen

    @property
    def quiet(self) -> bool:
        """Gar nichts los am Platz: seit 15 min kein Start und keine Landung, und in
        den nächsten 10 min landet keiner. Erst dann kommen Spielereien (Stufe 4)."""
        m = self.model
        if m.departures:
            return False
        if any((a.get("eta_s") or 9e9) <= QUIET_AHEAD_S for a in m.arrivals):
            return False
        return self.now - m.last_airport_move > QUIET_SINCE_S

    @property
    def live(self) -> bool:
        """Flugdaten frisch genug für Karten, die sich auf sie stützen."""
        return not self.model.stale and self.model.source not in ("startup", "none")


@dataclass
class Card:
    id: str
    cond: Callable[[Ctx], Hit | None]
    draw: Callable[[Image.Image, Ctx, Any, float, float], None]   # (img, ctx, data, t_in, f)
    weight: float = 1.0
    group: str = ""
    tier: int = 3                         # 0 Notfall, 2 Verkehr am Platz, 3 wenig los, 4 Spielereien
    live: bool = False                    # braucht frische Flugdaten
    only_at: str = ""                     # nur an diesem Heimatflughafen (IATA), z. B. Tegel-Karte
    demo: Callable[[Ctx], Any] | None = None   # Beispieldaten für /frame.png?card=<id>


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
        for cid in sorted(cfg.cards_off - self.cards.keys()):
            log.warning("CARD_%s: keine solche Karte (IDs unter /idle)", cid.upper())

    def context(self, model, now: float) -> Ctx:
        return Ctx(cfg=self.cfg, model=model, now=now, **self.providers)

    def enabled(self, cid: str) -> bool:
        c = self.cards.get(cid)
        return (c is not None and cid not in self.cfg.cards_off
                and (not c.only_at or c.only_at == self.cfg.local_airport_iata))

    def _fail(self, cid: str, what: str, e: Exception) -> None:
        """Fehler einer Karte loggen, höchstens einmal pro Stunde je Karte."""
        now = time.time()
        if now - self._errors.get(cid, 0) > 3600:
            self._errors[cid] = now
            log.exception("Karte %s: %s fehlgeschlagen: %s", cid, what, e)

    def candidates(self, ctx: Ctx) -> dict[str, Hit]:
        """Die eingeschalteten Karten, die gerade etwas zu sagen haben."""
        out: dict[str, Hit] = {}
        for cid, c in self.cards.items():
            if not self.enabled(cid) or (c.live and not ctx.live):
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

    def _top_tier(self, ctx: Ctx, pool: dict[str, Hit]) -> dict[str, Hit]:
        """Nur die höchste Stufe mit etwas zu zeigen; Spielereien nur, wenn gar nichts los ist."""
        quiet = ctx.quiet
        pool = {cid: h for cid, h in pool.items() if self.cards[cid].tier < 4 or quiet}
        if not pool:
            return pool
        top = min(self.cards[cid].tier for cid in pool)
        ok = {top, 4} if top == 3 and quiet else {top}
        return {cid: h for cid, h in pool.items() if self.cards[cid].tier in ok}

    def pick(self, ctx: Ctx) -> tuple[str, Hit] | None:
        """Die Karte für diese Zeitscheibe; None, wenn keine etwas zu zeigen hat."""
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
        if pool:
            pool = self._top_tier(ctx, pool)
        last = self._recent[-1] if self._recent else None
        if not pool or ("clock" in cands and last != "clock" and rng.random() < self.cfg.idle_clock_share):
            cid = "clock" if "clock" in cands else None
        else:
            fresh = {k: h for k, h in pool.items() if k not in self._recent}
            if not fresh:
                fresh = {k: h for k, h in pool.items() if k not in list(self._recent)[-1:]} or pool
            ids = sorted(fresh)
            weights = [fresh[k].weight if fresh[k].weight is not None else self.cards[k].weight for k in ids]
            cid = rng.choices(ids, weights=weights)[0]
        self._cur = cid
        return (cid, cands[cid]) if cid else None

    def draw(self, img: Image.Image, model, now: float, f: float = 1.0, force: str | None = None,
             preview: bool = False) -> str | None:
        """Zeichnet die Karte, die dran ist (oder force), und gibt ihre ID zurück; None,
        wenn keine dran ist (Panel bleibt dunkel). preview: Vorschau mit ?card=, zählt
        nicht als gezeigt und zeigt auch abgeschaltete Karten."""
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
            picked = self.pick(ctx)
            cid, hit = picked if picked else (None, None)
        if cid is not None:
            t_in = now - self.slot(now) * max(1, self.cfg.idle_seconds)
            try:
                self.cards[cid].draw(img, ctx, hit.data, t_in, f)
            except Exception as e:
                self._fail(cid, "Zeichnen", e)
                img.paste((0, 0, 0), (0, 0, img.width, img.height))
                failed, cid = cid, None
                if failed != "clock" and self.enabled("clock"):
                    self.cards["clock"].draw(img, ctx, None, t_in, f)
                    cid = "clock"
        if not preview:
            self.last_pick = cid
        return cid

    def status(self, model, now: float) -> dict:
        """Für GET /idle: welche Karten gerade etwas zu sagen haben."""
        ctx = self.context(model, now)
        cands = self.candidates(ctx)

        def js(v):
            try:
                return json.loads(json.dumps(v, default=lambda o: getattr(o, "__dict__", str(o))))
            except Exception:
                return str(v)

        return {
            "slot_seconds": self.cfg.idle_seconds,
            "showing": self.last_pick,
            "recent": list(self._recent),
            "cards": [{"id": cid, "tier": c.tier, "group": c.group, "weight": c.weight,
                       "state": "off" if not self.enabled(cid) else "active" if cid in cands else "idle",
                       "urgent": bool(cands.get(cid) and cands[cid].urgent),
                       "data": js(cands[cid].data) if cid in cands else None}
                      for cid, c in self.cards.items()],
        }
