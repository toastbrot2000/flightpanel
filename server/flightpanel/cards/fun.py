"""Spielereien für wenn gar nichts los ist, und Saison: Witze und echte
Verspätungsgründe (data/jokes.json), Tegel und BER-Eröffnung (nur am BER),
Flugzeug und Taube, Weihnachten und Silvester."""

import math
from datetime import datetime

from .. import sprites
from ..colors import ARR_C, DEP_C, GOLD, INFO, MUTED, RED, dim
from ..draw import ROW1, ROW2, ROW3, center, km, line, minutes, right, text
from ..fonts import FONT_BIG, FONT_MID, FONT_SMALL
from ..idle import Ctx, Hit
from .common import slot_rng

PLANE_TEXTS = ["Himmel frei", "Warteschleife …", "gleich kommt einer"]   # ohne eigene in jokes.json


def _fmt(s: str, ctx: Ctx) -> str:
    """Platzhalter in Texten aus jokes.json: {year}, {next_year}, {ber_age}."""
    t = datetime.fromtimestamp(ctx.now)
    return s.format(year=t.year, next_year=t.year + 1, ber_age=t.year - 2020)


# ------------------------------------------------------------------- Witze --
def joke_cond(ctx: Ctx):
    """Witze mit "dates" (MM-TT) nur an ihren Tagen, dann aber öfter."""
    jokes = ctx.fun.get("jokes", [])
    md = datetime.fromtimestamp(ctx.now).strftime("%m-%d")
    dated = [j for j in jokes if md in j.get("dates", [])]
    plain = [j for j in jokes if not j.get("dates")]
    if dated:
        return Hit(slot_rng(ctx, "joke").choice(dated), weight=4)
    if plain:
        return Hit(slot_rng(ctx, "joke").choice(plain))
    return None


def joke_demo(ctx: Ctx):
    return {"lines": ["SXF heißt jetzt", "BER Terminal 5", "...und ist zu."]}


def joke_draw(img, ctx, d, t_in, f):
    lines = [_fmt(s, ctx) for s in d.get("lines", [])][:3]
    for s, y, col in zip(lines, (ROW1, ROW2, ROW3), (GOLD, INFO, MUTED), strict=False):
        center(img, FONT_MID, y, s, col, f)


def delay_cond(ctx: Ctx):
    reasons = ctx.fun.get("delays", [])
    if not reasons:
        return None
    r = slot_rng(ctx, "delay")
    return Hit({"reason": reasons[r.randrange(len(reasons))]})


def delay_demo(ctx: Ctx):
    return {"reason": "Rolltreppen einige|Stufen zu kurz|2012"}


def delay_draw(img, ctx, d, t_in, f):
    """Echte Verspätungsgründe des Heimatflughafens (für BER: warum er neun Jahre zu
    spät eröffnete): zwei Zeilen, das Jahr rechts unten, wenn es neben die zweite passt."""
    W = img.width
    text(img, FONT_SMALL, 0, ROW1 + 1, f"Verspätungsgrund {ctx.cfg.local_airport_iata}", ARR_C, f, W)
    a, b, year = [*_fmt(d["reason"], ctx).split("|"), "", "", ""][:3]
    line(img, 0, ROW2, a, INFO, f)
    yw = FONT_SMALL.text_width(year) + 6 if year else 0
    show_year = bool(year) and FONT_MID.text_width(b) + yw <= W
    line(img, 0, ROW3, b, INFO, f, W - yw if show_year else None)
    if show_year:
        right(img, FONT_SMALL, ROW3 + 1, year, MUTED, 0.8 * f)


# ------------------------------------------------------------ Berlin, BER --
def txl_cond(ctx: Ctx):
    return Hit({})


def txl_draw(img, ctx, d, t_in, f):
    sprites.blit(img, sprites.TOMB, 2, 12, sprites.TOMB_PAL, f)
    x0 = 16
    FONT_BIG.draw(img, x0, ROW1, "TXL", dim(MUTED, f))
    FONT_MID.draw(img, x0 + FONT_BIG.text_width("TXL") + 6, ROW1, "Status:", dim(MUTED, f * 0.8))
    text(img, FONT_MID, x0, ROW2, "geschlossen", ARR_C, f, img.width - x0)
    text(img, FONT_MID, x0, ROW3, "seit 08.11.2020", INFO, f, img.width - x0)


def ber_opening_cond(ctx: Ctx):
    return Hit({})


def ber_opening_draw(img, ctx, d, t_in, f):
    W = img.width
    FONT_MID.draw(img, 0, ROW1, "BER Eröffnung:", dim(INFO, f))
    years = ["2011", "2012", "2013", "…"]
    # Die Jahre erscheinen nacheinander und werden durchgestrichen, dann 2020 mit Haken
    step = max(1.0, ctx.cfg.idle_seconds / 6)
    shown = int(t_in // step) + 1
    x = 0
    for y in years[:shown]:
        w = FONT_MID.text_width(y)
        FONT_MID.draw(img, x, ROW2, y, dim(MUTED, f))
        if y != "…":
            img.paste(dim(RED, f), (x, ROW2 + 4, x + w, ROW2 + 5))
        x += w + 5
    if shown > len(years):
        # Untere Zeile aus den echten Breiten: "2020", Haken, Text. Passt "nur 9 Jahre
        # später" nicht daneben, die kürzere Fassung statt abgeschnitten ("... spä").
        y = img.height - FONT_BIG.cap_height
        x = FONT_BIG.draw(img, 0, y, "2020", dim(DEP_C, f)) + 2
        sprites.blit(img, sprites.CHECK, x, y + FONT_BIG.cap_height - len(sprites.CHECK) - 1, DEP_C, f)
        x += sprites.width(sprites.CHECK) + 4
        msg = next((m for m in ("nur 9 Jahre später", "9 Jahre später") if x + FONT_SMALL.text_width(m) <= W),
                   "9 Jahre später")
        text(img, FONT_SMALL, x, img.height - FONT_SMALL.cap_height, msg, MUTED, f, W - x)


# -------------------------------------------------------------- Animationen --
def plane_cond(ctx: Ctx):
    return Hit({"text": slot_rng(ctx, "plane").choice(ctx.fun.get("plane") or PLANE_TEXTS)})


def plane_draw(img, ctx, d, t_in, f):
    W = img.width
    pw = sprites.width(sprites.PLANE)
    dur = max(1.0, ctx.cfg.idle_seconds)
    x = int(-pw + (W + pw) * (t_in / dur))
    y = 4
    for i in range(1, 40, 3):            # Kondensstreifen
        cx = x - i
        if 0 <= cx < W:
            sprites.put(img, cx, y + 3, dim((60, 60, 70), f * max(0.2, 1 - i / 40)))
    sprites.blit(img, sprites.PLANE, x, y, sprites.PLANE_PAL, f)
    center(img, FONT_MID, ROW3, d["text"], MUTED, f)


def pigeon_cond(ctx: Ctx):
    return Hit({})


def pigeon_draw(img, ctx, d, t_in, f):
    W = img.width
    text(img, FONT_SMALL, 0, ROW1 + 1, "Einziger Verkehr:", MUTED, f, 96)
    FONT_BIG.draw(img, 0, ROW2, "1 Taube", dim(INFO, f))
    text(img, FONT_MID, 0, ROW3, "Abflug Balkon", ARR_C, f, 96)
    peck = int(ctx.now // 2) % 3 == 1
    sp = sprites.PIGEON_PECK if peck else sprites.PIGEON
    sprites.blit(img, sp, W - 16, 21, sprites.PIGEON_PAL, f)
    for x in range(W - 30, W):
        sprites.put(img, x, 30, dim((80, 60, 40), f))      # Balkongeländer
    for x in range(W - 30, W, 4):
        for y in range(28, 31):
            sprites.put(img, x, y, dim((80, 60, 40), f))


# ------------------------------------------------------------------- Saison --
def santa_cond(ctx: Ctx):
    t = datetime.fromtimestamp(ctx.now)
    if ((t.month, t.day) == (12, 24) and t.hour >= 15) or ((t.month, t.day) == (12, 25) and t.hour < 6):
        start = t.replace(month=12, day=24, hour=15, minute=0, second=0, microsecond=0).timestamp()
        if t.day == 25:
            start -= 86400
        left = max(0.0, 12000 * (1 - (ctx.now - start) / (9 * 3600)))
        return Hit({"km": left}, weight=6)
    return None


def santa_demo(ctx: Ctx):
    return {"km": 12000}


def santa_draw(img, ctx, d, t_in, f):
    W = img.width
    sw = sprites.width(sprites.SLEIGH)
    x = int(-sw + (W + sw) * (t_in / max(1.0, ctx.cfg.idle_seconds)))
    pal = dict(sprites.SLEIGH_PAL)
    if not ctx.blink_on:
        pal["n"] = (120, 20, 20)
    sprites.blit(img, sprites.SLEIGH, x, 2, pal, f)
    left = "gelandet!" if d["km"] <= 0 else f"{km(d['km'])} km"
    FONT_MID.draw(img, 0, ROW2, "Schlitten 1", dim(RED, f))
    right(img, FONT_MID, ROW2, left, GOLD, f)
    line(img, 0, ROW3, "Rentiere: 9 · Ziel: alle", INFO, f)


def fireworks_cond(ctx: Ctx):
    t = datetime.fromtimestamp(ctx.now)
    if ((t.month, t.day) == (12, 31) and t.hour >= 18) or ((t.month, t.day) == (1, 1) and t.hour < 2):
        return Hit({}, weight=8)
    return None


def fireworks_draw(img, ctx, d, t_in, f):
    t = datetime.fromtimestamp(ctx.now)
    sprites.fireworks(img, int(ctx.now // max(1, ctx.cfg.idle_seconds)), t_in, f, rockets=5)
    if t.month == 1:
        center(img, FONT_BIG, ROW2, f"Frohes {t.year}!", GOLD, f)
    elif (t.month, t.day) == (12, 31):
        left = datetime(t.year + 1, 1, 1).timestamp() - ctx.now
        when = f"{math.ceil(left / 3600)} h" if left > 2 * 3600 else minutes(left)
        center(img, FONT_MID, ROW2, f"noch {when} bis {t.year + 1}", GOLD, f)
    else:
        center(img, FONT_BIG, ROW2, "Frohes neues Jahr!", GOLD, f)
