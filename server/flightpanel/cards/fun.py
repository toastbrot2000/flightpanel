"""Spielereien für wenn gar nichts los ist (Flugzeug und Taube) und Saison:
Weihnachten und Neujahr."""

from datetime import datetime

from .. import sprites
from ..colors import ARR_C, GOLD, INFO, MUTED, RED, dim
from ..draw import ROW1, ROW2, ROW3, center, km, line, right, text
from ..fonts import FONT_BIG, FONT_MID, FONT_SMALL
from ..idle import Ctx, Hit
from .common import slot_rng

PLANE_TEXTS = ["Himmel frei", "Warteschleife …"]   # unter dem Flugzeug; nichts, was nach einer Vorhersage klingt


# -------------------------------------------------------------- Animationen --
def plane_cond(ctx: Ctx):
    return Hit({"text": slot_rng(ctx, "plane").choice(PLANE_TEXTS)})


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


NEW_YEAR_S = 5 * 60         # so lange gehört das Panel um Mitternacht dem Feuerwerk


def new_year(now: float) -> bool:
    """Die ersten Minuten des Jahres: Dann zeigt render.py das Feuerwerk vor allem
    außer einem Notfall, auch vor Flügen und in der Ruhezeit."""
    t = datetime.fromtimestamp(now)
    return (t.month, t.day, t.hour) == (1, 1, 0) and t.minute * 60 + t.second < NEW_YEAR_S


def fireworks_cond(ctx: Ctx):
    """An Neujahr den ganzen Tag in der Rotation, an Silvester noch nicht."""
    t = datetime.fromtimestamp(ctx.now)
    return Hit({}) if (t.month, t.day) == (1, 1) else None


def fireworks_draw(img, ctx, d, t_in, f):
    t = datetime.fromtimestamp(ctx.now)
    sprites.fireworks(img, int(ctx.now // max(1, ctx.cfg.idle_seconds)), t_in, f, rockets=5)
    if t.month == 1:
        center(img, FONT_BIG, ROW2, f"Frohes {t.year}!", GOLD, f)
    else:
        center(img, FONT_BIG, ROW2, "Frohes neues Jahr!", GOLD, f)
