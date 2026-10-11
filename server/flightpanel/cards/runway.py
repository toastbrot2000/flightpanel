"""Lage am Platz: Landerichtung und Pistenwechsel, Wetter, das den Himmel leer
macht, Nachtflugverbot."""

import math
from datetime import datetime

from .. import sprites
from ..airport import path_in_view, runway_name
from ..colors import ARR_C, CYAN, DEP_C, GOLD, INFO, MUTED, OVF_C, dim
from ..config import in_hours
from ..draw import ROW1, ROW2, ROW3, headline, line, right, text
from ..fonts import FONT_BIG, FONT_MID, FONT_SMALL
from ..geo import M_PER_FT, kompass
from ..idle import Ctx, Hit
from ..metar import describe_wx
from ..metar import parse as parse_metar

RUNWAY_STABLE_S = 1800      # so lange muss die Landerichtung gelten, bevor die Karte sie zeigt
RUNWAY_SEEN_S = 2 * 3600    # ... und so kurz ist der letzte Verkehr her, der sie bestätigt


def _wind(ctx: Ctx, course: float) -> tuple[str, bool]:
    """(Windtext, Landerichtung wackelt). Ohne METAR aus der Landerichtung abgeleitet."""
    m = ctx.metar.fresh(ctx.now) if ctx.metar else None
    side = "Westwind" if 180 <= course % 360 < 360 else "Ostwind"
    if m is None or m.wind_kt is None:
        return side, False
    if m.wind_kt <= 3:
        return "kaum Wind", False
    if m.wind_dir is None:
        return f"Wind wechselnd {m.wind_kt} kt", True
    tail = -m.wind_kt * math.cos(math.radians(m.wind_dir - course))
    gust = f" G{m.gust_kt}" if m.gust_kt else ""
    return f"Wind {kompass(m.wind_dir)} {m.wind_kt}{gust} kt", tail > 5


def _view_line(ctx: Ctx, course: float) -> tuple[str, tuple]:
    """Was die Landerichtung fürs Fenster heißt, mit Farbe."""
    app = bool(path_in_view(ctx.cfg, course, "approach"))
    dep = bool(path_in_view(ctx.cfg, course, "departure"))
    if app and dep:
        return "An- und Abflüge im Blick", GOLD
    if app:
        return "Landungen im Blick", ARR_C
    if dep:
        return "Starts im Blick", DEP_C
    return "so nicht im Blick", MUTED


# ---------------------------------------------------------- Landerichtung --
def runway_cond(ctx: Ctx):
    st = ctx.stats
    if st is None or st.course is None or not st.course_seen:
        return None
    if ctx.now - st.course_since < RUNWAY_STABLE_S or ctx.now - st.course_seen > RUNWAY_SEEN_S:
        return None
    wind, shaky = _wind(ctx, st.course)
    if shaky:
        return None          # Rückenwind oder wechselnd: Pistenwechsel möglich, lieber nichts sagen
    return Hit({"course": st.course, "wind": wind})


def runway_demo(ctx: Ctx):
    return {"course": ctx.cfg.runway_course, "wind": "Wind W 12 kt"}


def runway_draw(img, ctx, d, t_in, f):
    course = d["course"]
    text(img, FONT_MID, 0, ROW1, f"{ctx.cfg.local_airport_iata} landet auf {runway_name(course)}", GOLD, f, img.width)
    text(img, FONT_MID, 0, ROW2, d["wind"], INFO, f, img.width)
    msg, col = _view_line(ctx, course)
    line(img, 0, ROW3, msg, col, f)


def runway_change_cond(ctx: Ctx):
    st = ctx.stats
    if st is None or st.course is None or st.prev_course is None:
        return None
    age = ctx.now - st.course_since
    if not 0 <= age < 3600:
        return None
    return Hit({"course": st.course, "age": age}, weight=4)


def runway_change_demo(ctx: Ctx):
    return {"course": (ctx.cfg.runway_course + 180) % 360, "age": 600}


def runway_change_draw(img, ctx, d, t_in, f):
    FONT_BIG.draw(img, 0, ROW1, "Pistenwechsel!", dim(GOLD, f))
    line(img, 0, ROW2, f"jetzt Landung auf {runway_name(d['course'])}", INFO, f)
    msg, col = _view_line(ctx, d["course"])
    line(img, 0, ROW3, msg, col, f)


# ------------------------------------------------------------------ Wetter --
def weather_cond(ctx: Ctx):
    """Nur wenn das Wetter erklärt, warum am Himmel nichts zu sehen ist. Nebel und
    Wolken verstecken nur, was fliegt: Nebel nur, wenn einer in der Luft ist, Wolken
    nur, wenn einer darüber fliegt, nach frischen Flugdaten."""
    m = ctx.metar.fresh(ctx.now) if ctx.metar else None
    if m is None:
        return None
    aloft = [n["alt_ft"] * M_PER_FT for n in ctx.model.nearby if n.get("alt_ft")] if ctx.live else []
    above = sum(1 for alt_m in aloft if alt_m > m.ceiling_m) if m.ceiling_m is not None else None
    if m.has("TS"):
        return Hit({"kind": "ts", "m": m, "above": above}, weight=3)
    if aloft and ((m.vis_m is not None and m.vis_m < 2000) or m.has("FG")):
        return Hit({"kind": "fog", "m": m, "above": above}, weight=3)
    if above and m.ceiling_m < 1500:
        return Hit({"kind": "cloud", "m": m, "above": above}, weight=2.5)
    if m.has("SN"):
        return Hit({"kind": "snow", "m": m, "above": above}, weight=2)
    return None


def weather_demo(ctx: Ctx):
    return {"kind": "cloud", "m": parse_metar("EDDB 101220Z 25012KT 9999 BKN020 OVC035 09/07 Q1012"), "above": 3}


def weather_draw(img, ctx, d, t_in, f):
    m, kind, above = d["m"], d["kind"], d["above"]
    W = img.width
    iata = ctx.cfg.local_airport_iata
    if kind == "ts":
        FONT_BIG.draw(img, 0, ROW1, f"Gewitter am {iata}", dim(GOLD, f))
        text(img, FONT_MID, 0, ROW2, "Vorfeld gesperrt,", INFO, f, W)
        text(img, FONT_MID, 0, ROW3, "Flieger warten", INFO, f, W)
        if ctx.blink_on:
            for x, y in ((120, 2), (119, 3), (121, 3), (118, 4), (120, 5), (119, 6)):
                sprites.put(img, x, y, dim(GOLD, f))
        return
    if kind == "fog":
        vis = m.vis_m if m.vis_m is not None else 0
        FONT_BIG.draw(img, 0, ROW1, "Nebel", dim(MUTED, f))
        right(img, FONT_MID, ROW1, f"Sicht {vis} m" if vis < 1000 else f"Sicht {vis / 1000:.1f} km".replace(".", ","),
              INFO, f)
        text(img, FONT_MID, 0, ROW2, "Flieger nur zu hören", INFO, f, W)
    elif kind == "snow":
        FONT_BIG.draw(img, 0, ROW1, f"Schnee am {iata}", dim(CYAN, f))
        text(img, FONT_MID, 0, ROW2, "Enteisen dauert", INFO, f, W)
    else:
        # "1500 m" ohne Tausendertrennung: passt mit genau 128 px in großer Schrift
        headline(img, 0, ROW1, f"Wolken auf {round(m.ceiling_m, -2):.0f} m", MUTED, f, W)
        text(img, FONT_MID, 0, ROW2, "Flieger versteckt", INFO, f, W)
    if above:
        text(img, FONT_MID, 0, ROW3, f"{above} Flieger darüber", OVF_C, f, W)
    else:
        wx = describe_wx(m)
        if wx:
            text(img, FONT_MID, 0, ROW3, wx, MUTED, f, W)


# --------------------------------------------------------- Nachtflugverbot --
def curfew_cond(ctx: Ctx):
    t = datetime.fromtimestamp(ctx.now)
    if not in_hours(ctx.cfg.curfew_hours, t.hour * 60 + t.minute):
        return None
    return Hit({"until": ctx.cfg.curfew_hours[1]}, weight=3)


def curfew_demo(ctx: Ctx):
    return {"until": ctx.cfg.curfew_hours[1]}


def curfew_draw(img, ctx, d, t_in, f):
    until = f"{d['until'] // 60:02d}:{d['until'] % 60:02d}"
    text(img, FONT_MID, 0, ROW1, "Nachtflugverbot", ARR_C, f * 0.8)
    line(img, 0, ROW2, f"Ruhe bis {until}", INFO, f * 0.8, 92)
    sprites.blit(img, sprites.PLANE_SIDE, 2, 25, sprites.PLANE_SIDE_PAL, f)
    # Zzz steigt auf
    for i, ch in enumerate("zZZ"):
        k = (t_in / 3 + i / 3) % 1
        y = int(24 - k * 18)
        x = 100 + i * 7 + int(math.sin(k * 6) * 2)
        FONT_SMALL.draw(img, x, y, ch, dim(INFO, f * (1 - k * 0.7)))
