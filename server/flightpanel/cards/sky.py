"""Tag und Himmel: Uhr, Satelliten-Überflüge (ISS, Tiangong, Starlink-Ketten: SATS), Sonnenauf- und -untergang."""

from dataclasses import dataclass
from datetime import datetime
from functools import partial

from .. import sprites
from ..astro import ISS, TIANGONG, Pass, hhmm, sun_day
from ..colors import ARR_C, CLOCK, CYAN, GOLD, HORIZON, INFO, MUTED, dim
from ..draw import ROW1, ROW2, ROW3, line, minutes, text
from ..fonts import FONT_BIG, FONT_CLOCK, FONT_MID, FONT_SMALL
from ..geo import kompass
from ..idle import Ctx, Hit
from .common import in_cone, side_arrow

WEEKDAYS = ["Mo", "Di", "Mi", "Do", "Fr", "Sa", "So"]


# --------------------------------------------------------------------- Uhr --
def clock_cond(ctx: Ctx):
    return Hit()


def clock_draw(img, ctx, data, t_in, f):
    t = datetime.fromtimestamp(ctx.now)
    txt = t.strftime("%H:%M")
    FONT_CLOCK.draw(img, (img.width - FONT_CLOCK.text_width(txt)) // 2, 3, txt, dim(CLOCK, f))
    date = f"{WEEKDAYS[t.weekday()]} {t.strftime('%d.%m.')}"
    FONT_SMALL.draw(img, (img.width - FONT_SMALL.text_width(date)) // 2, 22, date, dim(MUTED, 0.6 * f))


# -------------------------------------------------------------- Satelliten --
@dataclass(frozen=True)
class SatCard:
    """Eine Satelliten-Karte: welcher Satellit (NORAD, astro.WANTED), wie er heißt und aussieht."""
    norad: int
    label: str
    sprite: list[str]
    pal: dict


SATS = {
    "iss": SatCard(ISS, "ISS", sprites.ISS, sprites.ISS_PAL),
    "tiangong": SatCard(TIANGONG, "Tiangong", sprites.SAT, sprites.SAT_PAL),
    "starlink": SatCard(0, "Starlink-Kette", sprites.TRAIN, sprites.TRAIN_PAL),     # 0: die Ketten des Katalogs
}


def _tracker(cid: str, ctx: Ctx):
    """Der Tracker dieser Karte (ctx.astro: je Karten-id einer, panel.py); None ohne oder bei abgeschalteter Karte."""
    return ctx.astro.get(cid) if isinstance(ctx.astro, dict) else None


def sat_cond(cid: str, ctx: Ctx):
    tr = _tracker(cid, ctx)
    if tr is None:
        return None
    p = tr.next_pass(ctx.now)
    if p is None:
        return None
    label = SATS[cid].label
    n = getattr(tr, "count", 0)             # Satelliten einer Kette
    if p.start <= ctx.now <= p.end:
        st = tr.state(ctx.now)
        if st is None:
            return None
        # Eine Kette ist nie dringend: ob sie noch eine ist, weiß nur der Himmel
        return Hit({"pass": p, "now": True, "az": st[0], "el": st[1], "label": label, "n": n},
                   urgent=cid != "starlink", weight=3 if cid == "starlink" else None)
    if 0 < p.start - ctx.now <= 30 * 60:
        return Hit({"pass": p, "now": False, "label": label, "n": n}, weight=3 if p.start - ctx.now < 600 else 2)
    return None


def sat_demo(cid: str, ctx: Ctx):
    t = ctx.now + 240
    return {"pass": Pass(start=t, end=t + 300, max_elev=52, max_time=t + 150, az_start=260, az_end=80,
                         view_start=t + 60), "now": False, "label": SATS[cid].label, "n": 23}


def sat_draw(cid: str, img, ctx, d, t_in, f):
    p = d["pass"]
    W = img.width
    sat = SATS[cid]
    sprites.blit(img, sat.sprite, W - 8, 2, sat.pal, f)
    room = W - 10                                           # links vom Sprite

    def head(s: str) -> None:                               # groß, wenn es passt (ISS), sonst mittel (Tiangong)
        text(img, FONT_BIG if FONT_BIG.text_width(s) <= room else FONT_MID, 0, ROW1, s, CYAN, f, room)

    if d["now"]:
        if ctx.blink_on:
            head(f"{d['label']} jetzt!")
        text(img, FONT_MID, 0, ROW2, f"{kompass(d['az'])}, {d['el']:.0f}° hoch", INFO, f, W)
        if in_cone(ctx, d["az"], d["el"]):
            arrow = side_arrow(ctx, d["az"])
            text(img, FONT_MID, 0, ROW3, f"im Fenster {arrow}".strip(), GOLD, f, W)
        else:
            text(img, FONT_MID, 0, ROW3, f"bis {hhmm(p.end)}, nicht im Fenster", MUTED, f, W)
        return
    head(f"{d['label']} in {minutes(p.start - ctx.now)}")
    if cid == "starlink":
        text(img, FONT_MID, 0, ROW2, f"{d['n']} Satelliten, {kompass(p.az_start)} → {kompass(p.az_end)}", INFO, f, W)
    else:
        text(img, FONT_MID, 0, ROW2, f"{kompass(p.az_start)} → {kompass(p.az_end)} · max {p.max_elev:.0f}°", INFO, f,
             W)
    if p.view_start is not None:
        text(img, FONT_MID, 0, ROW3, f"im Fenster ab {hhmm(p.view_start)}", GOLD, f, W)
    else:
        text(img, FONT_MID, 0, ROW3, f"{hhmm(p.start)}-{hhmm(p.end)}, nicht im Fenster", MUTED, f, W)


# Je Karte ihre drei Funktionen (cards/__init__.py); die Namen bleiben, Tests und Doku nennen sie
iss_cond, iss_draw, iss_demo = partial(sat_cond, "iss"), partial(sat_draw, "iss"), partial(sat_demo, "iss")
tiangong_cond, tiangong_draw, tiangong_demo = (partial(sat_cond, "tiangong"), partial(sat_draw, "tiangong"),
                                               partial(sat_demo, "tiangong"))
starlink_cond, starlink_draw, starlink_demo = (partial(sat_cond, "starlink"), partial(sat_draw, "starlink"),
                                               partial(sat_demo, "starlink"))


# ------------------------------------------------------------------- Sonne --
def _sun_draw(img, f: float, k: float) -> None:
    """Sonne rechts, k = 0 hoch ... 1 am Horizont."""
    W, H = img.size
    cx, r = W - 7, 4
    cy = int(16 + k * (H - 16))
    col = (255, int(200 - 120 * k), int(40 * (1 - k)))
    for y in range(cy - r, cy + r + 1):
        for x in range(cx - r, cx + r + 1):
            if (x - cx) ** 2 + (y - cy) ** 2 <= r * r + 1 and y < H - 1:
                sprites.put(img, x, y, dim(col, f))
    for x in range(W - 14, W):
        sprites.put(img, x, H - 1, dim(HORIZON, f * 1.5))


def sunset_cond(ctx: Ctx):
    sd = sun_day(ctx.now, ctx.cfg.home_lat, ctx.cfg.home_lon)
    s = sd["sunset"]
    if s is None or not 0 <= s - ctx.now <= 2 * 3600:
        return None
    return Hit({"t": s, "az": sd["sunset_az"], "golden": sd["golden_evening"]}, weight=2)


def sunset_demo(ctx: Ctx):
    sd = sun_day(ctx.now, ctx.cfg.home_lat, ctx.cfg.home_lon)
    return {"t": ctx.now + 3000, "az": sd["sunset_az"] or 260, "golden": ctx.now + 1200}


def sunset_draw(img, ctx, d, t_in, f):
    line(img, 0, ROW1, "Sonnenuntergang", INFO, f)
    FONT_BIG.draw(img, 0, ROW2, hhmm(d["t"]), dim(GOLD, f))
    g = d.get("golden")
    if g:
        lab = "Gegenlicht ab" if in_cone(ctx, d["az"], margin=15) else "tiefe Sonne ab"
        line(img, 0, ROW3, f"{lab} {hhmm(g)}", ARR_C, f, img.width - 14)
    _sun_draw(img, f, 1 - min(1.0, (d["t"] - ctx.now) / 7200))


def sunrise_cond(ctx: Ctx):
    sd = sun_day(ctx.now, ctx.cfg.home_lat, ctx.cfg.home_lon)
    s = sd["sunrise"]
    if s is None or not 0 <= s - ctx.now <= 90 * 60:
        return None
    return Hit({"t": s, "az": sd["sunrise_az"], "golden": sd["golden_morning"]}, weight=2)


def sunrise_demo(ctx: Ctx):
    return {"t": ctx.now + 1800, "az": 100, "golden": ctx.now + 4200}


def sunrise_draw(img, ctx, d, t_in, f):
    line(img, 0, ROW1, "Sonnenaufgang", INFO, f)
    FONT_BIG.draw(img, 0, ROW2, hhmm(d["t"]), dim(GOLD, f))
    g = d.get("golden")
    if g:
        lab = "Gegenlicht bis" if in_cone(ctx, d["az"], margin=15) else "tiefe Sonne bis"
        line(img, 0, ROW3, f"{lab} {hhmm(g)}", ARR_C, f, img.width - 14)
    _sun_draw(img, f, min(1.0, (d["t"] - ctx.now) / 5400))
