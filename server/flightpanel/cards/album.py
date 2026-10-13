"""Statistik und Sammelalbum (stats.py): Tageszahlen, gesammelte Airlines, neue
und seltene Flugzeuge, Flaute."""

from datetime import datetime

from PIL import Image

from ..colors import ARR_C, GOLD, HORIZON, INFO, MUTED, NAME, PINK, dim
from ..draw import ROW1, ROW2, ROW3, frame, headline, km, line, minutes, right, text
from ..fonts import FONT_BIG, FONT_MID, FONT_SMALL
from ..geo import kompass
from ..idle import Ctx, Hit
from .common import in_cone

NEW_URGENT_S = 120      # neue Airline: so lange dringend
RARE_URGENT_S = 120     # seltener Typ oder hoher Besuch: so lange dringend
QUIET_MIN_S = 600       # Flaute ab so vielen Sekunden ohne Flieger im Blick


def scoreboard_cond(ctx: Ctx):
    if ctx.stats is None:
        return None
    t = ctx.stats.today()
    return Hit(t) if t["aircraft"] >= 10 else None


def scoreboard_demo(ctx: Ctx):
    return {"aircraft": 143, "airlines": 27, "shown": 38, "max_alt_m": 11900, "max_alt_callsign": "UAE59"}


def scoreboard_draw(img, ctx, d, t_in, f):
    """Flieger im Umkreis, davon wie viele durchs Sichtfeld kamen ("im Fenster");
    unten wechseln sich Airlines und der höchste im Takt der Infoseiten ab."""
    headline(img, 0, ROW1, f"Heute: {km(d['aircraft'])} Flieger", GOLD, f, img.width)
    line(img, 0, ROW2, f"davon {d['shown']} im Fenster" if d.get("shown") else "keiner im Fenster", INFO, f)
    facts = [f"{d['airlines']} Airlines"] + ([f"höchster {km(d['max_alt_m'])} m"] if d.get("max_alt_m") else [])
    line(img, 0, ROW3, facts[int(t_in // max(1, ctx.cfg.page_seconds)) % len(facts)], MUTED, f)


def pokedex_cond(ctx: Ctx):
    if ctx.stats is None:
        return None
    seen, total, extra = ctx.stats.dex_count()
    if seen == 0:
        return None
    latest = max(ctx.stats.dex.values(), key=lambda e: e["first"], default=None)
    return Hit({"seen": seen, "total": total, "extra": extra, "latest": latest})


def pokedex_demo(ctx: Ctx):
    return {"seen": 84, "total": 130, "extra": 3, "latest": {"name": "Air Astana", "first": ctx.now - 86400 * 2}}


def pokedex_draw(img, ctx, d, t_in, f):
    W = img.width
    FONT_MID.draw(img, 0, ROW1, "Sammelalbum", dim(GOLD, f))
    x = FONT_BIG.draw(img, 0, ROW2, f"{d['seen']}/{d['total']}", dim(INFO, f))
    FONT_MID.draw(img, x + 4, ROW2, "Airlines", dim(INFO, f))
    if d.get("extra"):
        right(img, FONT_SMALL, ROW2 + 1, f"+{d['extra']}", PINK, f)
    # Fortschrittsbalken rechts oben neben der Überschrift
    bx0, bx1 = FONT_MID.text_width("Sammelalbum") + 6, W - 1
    done = int((bx1 - bx0) * min(1.0, d["seen"] / max(1, d["total"])))
    img.paste(dim(HORIZON, f), (bx0, 3, bx1, 6))
    img.paste(dim(GOLD, f * 0.8), (bx0, 3, bx0 + done, 6))
    lat = d.get("latest")
    if lat:
        when = datetime.fromtimestamp(lat["first"]).strftime("%d.%m.")
        line(img, 0, ROW3, f"neu: {lat['name']} · {when}", MUTED, f)


def new_cond(ctx: Ctx):
    if ctx.stats is None:
        return None
    e = ctx.stats.newest(ctx.now, NEW_URGENT_S)
    if e is None:
        return None
    seen, total, _ = ctx.stats.dex_count()
    return Hit({**e, "seen": seen, "total": total}, urgent=True)


def new_demo(ctx: Ctx):
    return {"name": "Air Astana", "code": "KZR", "callsign": "KZR901", "known": True, "seen": 85, "total": 130}


def new_draw(img, ctx, d, t_in, f):
    """Mit Logo steht der Text daneben. Ohne Logo blinkt ein Rahmen, und der Text bleibt
    innerhalb wie beim Notfall: 3 px vom Rand links und rechts, oben und unten frei."""
    W = img.width
    logo = None
    if ctx.logos is not None and d.get("known"):
        n = ctx.names.get(d.get("code", ""), {})
        logo = ctx.logos.get(n.get("iata", ""), d.get("code", ""))
    if logo is not None:
        if f < 1.0:
            logo = Image.eval(logo, lambda v: int(v * f))
        img.paste(logo, (0, (img.height - logo.height) // 2))
        x0, x1, top, bottom = logo.width + 2, W, ROW1, ROW3 + 1
    else:
        frame(img, GOLD if ctx.blink_on else ARR_C, f * 0.6)
        x0, x1, top, bottom = 3, W - 3, ROW1 + 1, ROW3
    if ctx.blink_on:
        FONT_BIG.draw(img, x0, top, "NEU!", dim(GOLD, f))
    cs = d.get("callsign", "")
    FONT_SMALL.draw(img, x1 - FONT_SMALL.text_width(cs), top + 1, cs, dim(MUTED, f * 0.7))
    text(img, FONT_BIG, x0, ROW2, d["name"], NAME, f, x1 - x0)
    if d.get("known"):
        text(img, FONT_SMALL, x0, bottom, f"Nr. {d['seen']} von {d['total']}", INFO, f, x1 - x0)
    else:
        text(img, FONT_SMALL, x0, bottom, "nicht im Album", PINK, f, x1 - x0)


def rare_cond(ctx: Ctx):
    if ctx.stats is None:
        return None
    shown = {fl.icao24 for fl in ctx.model.flights}
    hits = [e for e in ctx.stats.rare_now(ctx.now) if e["icao24"] not in shown]
    if not hits:
        return None
    e = hits[0]
    urgent = ctx.now - e["first"] < RARE_URGENT_S
    return Hit(e, urgent=urgent, weight=3)


def rare_demo(ctx: Ctx):
    return {"label": "A380", "kind": "type", "airline": "Emirates", "callsign": "UAE45", "type": "A388",
            "dist_km": 70, "az": 135, "elev": 2.1, "alt_m": 11000}


def rare_draw(img, ctx, d, t_in, f):
    W = img.width
    near = in_cone(ctx, d["az"], d.get("elev"))
    head = "Jetzt im Blick, selten:" if near else ("Hoher Besuch:" if d["kind"] == "gov" else "Weit weg, aber selten:")
    text(img, FONT_SMALL, 0, ROW1 + 1, head, MUTED, f, W)
    name = f"{d['label']} {d.get('airline', '')}".strip() if d["kind"] == "type" else d["label"]
    text(img, FONT_BIG, 0, ROW2, name, GOLD, f, W)
    alt = f" · {km(d['alt_m'])} m" if d.get("alt_m") else ""
    where = f"{km(d['dist_km'])} km {kompass(d['az'])}{alt}"
    if d["kind"] == "gov":
        where = f"{d['callsign']} · {where}"
    text(img, FONT_MID, 0, ROW3, where, INFO, f, W)


def quiet_cond(ctx: Ctx):
    if ctx.stats is None:
        return None
    q = ctx.stats.quiet_s(ctx.now)
    if q is None or q < QUIET_MIN_S:
        return None
    return Hit({"quiet": q, "record": ctx.stats.today()["quiet_record_s"]})


def quiet_demo(ctx: Ctx):
    return {"quiet": 23 * 60, "record": 58 * 60}


def quiet_draw(img, ctx, d, t_in, f):
    W = img.width
    FONT_BIG.draw(img, 0, ROW1, f"Seit {minutes(d['quiet'])}", dim(INFO, f))
    text(img, FONT_MID, 0, ROW2, "kein Flugzeug im Blick", INFO, f * 0.8, W)
    if d["quiet"] >= d["record"] - 30:
        text(img, FONT_MID, 0, ROW3, "neuer Tagesrekord!", GOLD, f, W)
    else:
        text(img, FONT_MID, 0, ROW3, f"Rekord heute: {minutes(d['record'])}", MUTED, f, W)
