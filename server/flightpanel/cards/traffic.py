"""Verkehr: Notfall-Squawk, Privatflieger im Sichtfeld, der nächste Flieger,
Abflüge und Landungen am Platz, Radar."""

import math
import random

from .. import sprites
from ..aircraft import second_line, shown_elsewhere
from ..airport import runway_name
from ..colors import ARR_C, BAND, DEP_C, GOLD, HORIZON, INFO, MUTED, OVF_C, RED, dim
from ..draw import ROW1, ROW2, ROW3, STATUS_COLORS, blink, frame, km, line, minutes, right, status_glyph, text
from ..fonts import FONT_BIG, FONT_MID, FONT_SMALL
from ..geo import kompass
from ..idle import Ctx, Hit
from ..model import ARR, DEP, OVF, Flight
from .common import side_arrow

NEXT_MAX_S = 600        # so weit voraus zeigt "next" den nächsten Flieger
NEXT_URGENT_S = 180     # darunter dringend; im letzten Drittel blinkt die Richtung
ARRIVALS_MAX_S = 15 * 60


# ----------------------------------------------------------------- Notfall --
def alert_cond(ctx: Ctx):
    return Hit(ctx.model.alert, urgent=True) if ctx.model.alert else None


def alert_demo(ctx: Ctx):
    return {"callsign": "DLH4AB", "squawk": "7700", "meaning": "NOTFALL", "type": "A20N", "airline": "Lufthansa",
            "dist_km": 12.4, "az": 135, "elev": 6.0, "alt_ft": 2400, "in_view": False}


def alert_draw(img, ctx, d, t_in, f):
    W = img.width
    on = blink(ctx.now)
    frame(img, RED if on else (90, 0, 0), f)
    head = f"{d['squawk']} {d['meaning']}"
    FONT_BIG.draw(img, 3, ROW1 + 1, head, dim(RED if on else (160, 20, 20), f))
    who = " ".join(x for x in (d.get("callsign"), d.get("type")) if x)
    text(img, FONT_MID, 3, ROW2, who, ARR_C, f, W - 6)
    alt = f" · {km(d['alt_ft'])} ft" if d.get("alt_ft") else ""
    where = f"{d['dist_km']:.0f} km {kompass(d['az'])}{alt}"
    if d.get("in_view"):
        where = f"im Blick {side_arrow(ctx, d['az'])} {where}".replace("  ", " ")
    text(img, FONT_MID, 3, ROW3 - 1, where, INFO, f, W - 6)


# ----------------------------------------------------------- Privatflieger --
def private_cond(ctx: Ctx):
    """Privatflieger, Hubschrauber, Polizei, Militär im Sichtfeld: ohne Airline daneben
    eine Karte wie jede andere, gezeichnet wie ein Flug."""
    others = [fl for fl in ctx.model.flights if fl.kind != "airline"]
    return Hit(others) if others and ctx.draw_flight else None


def private_demo(ctx: Ctx):
    return [Flight(icao24="demo", callsign="DEFGH", status=OVF, dist_km=6.2, bearing=160, alt_ft=2500,
                   speed_kt=95, track=250, vrate_fpm=0, actype="C172", kind="other",
                   airline="D-EFGH", label="Kleinflugzeug")]


def private_draw(img, ctx, data, t_in, f):
    slot = int(ctx.now // max(1, ctx.cfg.idle_seconds))
    fl = data[slot % len(data)]
    page = int(t_in // max(1, ctx.cfg.page_seconds)) % 3
    ctx.draw_flight(img, ctx.model, fl, page, f)


# -------------------------------------------------------- Nächster Flieger --
def next_cond(ctx: Ctx):
    m = ctx.model
    if m.upcoming is None or m.upcoming_eta_s is None:
        return None
    eta = m.upcoming_eta_s - (ctx.now - m.generated_at)
    if not 15 <= eta <= NEXT_MAX_S:
        return None
    return Hit({"flight": m.upcoming, "eta": eta, "az": m.upcoming_az, "via": m.upcoming_via},
               urgent=eta < NEXT_URGENT_S)


def next_demo(ctx: Ctx):
    fl = Flight(icao24="demo", callsign="EZY45KZ", status=ARR, dist_km=24, bearing=250, alt_ft=4000,
                speed_kt=200, track=70, vrate_fpm=-700, origin="LGW", dest="BER", airline="easyJet",
                airline_icao="EZY", airline_iata="U2", actype="A320", visible=False)
    return {"flight": fl, "eta": 170, "az": ctx.cfg.view_from, "via": "approach"}


def next_draw(img, ctx, d, t_in, f):
    fl, eta, az = d["flight"], d["eta"], d["az"]
    col = STATUS_COLORS.get(fl.status, OVF_C)
    W = img.width
    head = f"{status_glyph(fl.status)} {fl.airline or fl.callsign}"
    # Der Name geht vor: "Nächster" nur, wenn beides passt ("in ~3 min" sagt es auch)
    lab = "Nächster"
    lw = FONT_SMALL.text_width(lab) + 4
    show_lab = FONT_BIG.text_width(head) + lw <= W
    FONT_BIG.draw(img, 0, ROW1, FONT_BIG.fit(head, W - (lw if show_lab else 0)), dim(col, f))
    if show_lab:
        FONT_SMALL.draw(img, W - FONT_SMALL.text_width(lab), ROW1 + 1, lab, dim(MUTED, 0.7 * f))
    # Die Zeit bis zum Sichtfeld, nicht bis zur Landung: deshalb "Sichtbar in"
    when = "Gleich sichtbar" if eta < 45 else f"Sichtbar in ~{minutes(eta)}"
    where = ""
    if az is not None:
        arrow = side_arrow(ctx, az)
        where = f"{arrow} {kompass(az)}".strip() if arrow else f"aus {kompass(az)}"
    line(img, 0, ROW2, when, INFO, f, W - (FONT_MID.text_width(where) + 4 if where else 0))
    if where and not (eta < NEXT_URGENT_S / 3 and not ctx.blink_on):
        FONT_MID.draw(img, W - FONT_MID.text_width(where), ROW2, where, dim(GOLD, f))
    # Route, sonst was der Flug ist, sonst ?→BER; das Rufzeichen nur, wenn es oben nicht steht
    bottom = (second_line(fl.origin, fl.dest, fl.status, fl.label, ctx.cfg.local_airport_iata)
              or ("" if shown_elsewhere(fl.callsign, head) else fl.callsign))
    line(img, 0, ROW3, bottom, MUTED, f, W - (FONT_MID.text_width(fl.actype) + 6 if fl.actype else 0))
    if fl.actype:
        right(img, FONT_MID, ROW3, fl.actype, MUTED, 0.7 * f)


# ------------------------------------------------- Abflug und Landung am Platz --
def _title(img, ctx: Ctx, title: str, col, f: float) -> None:
    """Überschrift links, die Piste der Landerichtung rechts, wenn Platz ist."""
    W = img.width
    x = FONT_MID.draw(img, 0, ROW1, title, dim(col, f))
    c = ctx.model.landing_course
    lab = f"Piste {runway_name(c)}" if c is not None else ""
    if lab and x + 4 + FONT_SMALL.text_width(lab) <= W:
        right(img, FONT_SMALL, ROW1 + 1, lab, MUTED, 0.8 * f)


def _two_rows(img, rows, f: float) -> None:
    """Bis zu zwei Flüge in Zeile 2 und 3: links Airline und Ort, rechts der Zustand.
    Links mehrere Fassungen, lang bis kurz ("Lufthansa aus MUC", "Lufthansa MUC", "Lufthansa"):
    die erste, die (notfalls in FONT_SMALL) passt, statt abzuschneiden."""
    W = img.width
    for (variants, state, hot), y in zip(rows[:2], (ROW2, ROW3), strict=False):
        sw = FONT_MID.text_width(state) + 4 if state else 0
        avail = W - sw
        name = next((v for v in variants if min(FONT_MID.text_width(v), FONT_SMALL.text_width(v)) <= avail),
                    variants[-1])
        line(img, 0, y, name, INFO, f, avail)
        if state:
            right(img, FONT_MID, y, state, GOLD if hot else MUTED, f)


def departures_cond(ctx: Ctx):
    deps = ctx.model.departures
    return Hit(deps[:2]) if deps else None


def departures_demo(ctx: Ctx):
    return [{"airline": "easyJet", "dest": "LGW", "phase": "startet", "ago_s": None, "type": "A320"},
            {"airline": "Ryanair", "dest": "PMI", "phase": "gestartet", "ago_s": 130, "type": "B738"}]


def departures_draw(img, ctx, deps, t_in, f):
    """Wer gerade am Platz startet: rollt zur Bahn, startet, oder ist vor kurzem gestartet."""
    _title(img, ctx, f"↑ Abflug {ctx.cfg.local_airport_iata}", DEP_C, f)
    rows = []
    for d in deps:
        name = ([f"{d['airline']} → {d['dest']}", f"{d['airline']} {d['dest']}", d["airline"]] if d.get("dest")
                else [d["airline"]])
        if d["phase"] == "gestartet":
            ago = (d.get("ago_s") or 0) + (ctx.now - ctx.model.generated_at)
            state = "gerade" if ago < 60 else f"vor {minutes(ago)}"
        else:
            state = d["phase"]
        rows.append((name, state, d["phase"] == "startet"))
    if len(rows) == 1 and deps[0].get("type"):
        rows.append(([deps[0]["type"]], "", False))   # zweite Zeile frei: der Typ
    _two_rows(img, rows, f)


def arrivals_cond(ctx: Ctx):
    arr = [a for a in ctx.model.arrivals if (a.get("eta_s") or 9e9) <= ARRIVALS_MAX_S]
    return Hit(arr[:2]) if arr else None


def arrivals_demo(ctx: Ctx):
    return [{"airline": "Ryanair", "origin": "DUB", "eta_s": 240, "type": "B738"},
            {"airline": "Lufthansa", "origin": "MUC", "eta_s": 520, "type": "A21N"}]


def arrivals_draw(img, ctx, arr, t_in, f):
    """Wer als nächstes landet, mit Herkunft und Minuten bis zum Aufsetzen."""
    _title(img, ctx, f"↓ Landung {ctx.cfg.local_airport_iata}", ARR_C, f)
    rows = []
    for a in arr:
        name = ([f"{a['airline']} aus {a['origin']}", f"{a['airline']} {a['origin']}", a["airline"]] if a.get("origin")
                else [a["airline"]])
        eta = (a.get("eta_s") or 0) - (ctx.now - ctx.model.generated_at)
        rows.append((name, "landet" if eta < 60 else f"~{minutes(eta)}", eta < 60))
    if len(rows) == 1 and arr[0].get("type"):
        rows.append(([arr[0]["type"]], "", False))
    _two_rows(img, rows, f)


# ------------------------------------------------------------------- Radar --
def radar_cond(ctx: Ctx):
    near = ctx.model.nearby
    if not near:
        return None
    arr = sum(1 for n in near if n.get("seq") or n.get("status") == ARR)
    return Hit({"n": len(near), "arr": arr})


def radar_demo(ctx: Ctx):
    return {"n": 0, "arr": 0}


def radar_draw(img, ctx, d, t_in, f):
    """Rundumblick: Sichtfeld als Band in der Mitte, Flugzeuge als Punkte nach Richtung
    und Höhenwinkel in ihrer Statusfarbe; die im Sichtfeld hell, der nächste blinkt."""
    W, H = img.size
    cfg = ctx.cfg
    near = ctx.model.nearby or _demo_nearby()
    n = d["n"] or len(near)
    arr = d["arr"] if d["n"] else sum(1 for x in near if x.get("status") == ARR)
    head = f"{n} im Umkreis" + (f" · {arr} landen" if arr else "")
    FONT_SMALL.draw(img, 0, ROW1, FONT_SMALL.fit(head, W), dim(INFO, f))
    top, base = 11, H - 2
    center_az = cfg.view_center if cfg.view_span < 360 else 180

    def xof(az: float) -> int:
        return int(W / 2 + ((az - center_az + 180) % 360 - 180) * W / 360)

    if cfg.view_span < 360:
        a, b = xof(cfg.view_from), xof(cfg.view_to)
        xs = range(a, b + 1) if a <= b else list(range(a, W)) + list(range(0, b + 1))
        for x in xs:
            for y in range(top, base + 1):
                img.putpixel((x % W, y), dim(BAND, f))
    for x in range(W):
        img.putpixel((x, H - 1), dim(HORIZON, f))
    for az, ch in ((0, "N"), (90, "O"), (180, "S"), (270, "W")):
        x = xof(az)
        img.putpixel((min(W - 1, max(0, x)), H - 1), dim(MUTED, f))
        sprites.tiny_text(img, min(W - 3, max(0, x - 1)), base - 5, ch, dim(HORIZON, f * 1.6))

    up = ctx.model.upcoming.icao24 if ctx.model.upcoming else None
    for p in sorted(near, key=lambda p: p.get("in_view") == "now"):
        el = max(0.0, min(45.0, p.get("elev") or 0.0))
        y = int(base - 1 - math.sqrt(el / 45) * (base - top - 2))
        x = xof(p["az"])
        col = STATUS_COLORS.get(p.get("status"), OVF_C)
        lit = p.get("in_view") == "now"
        if p.get("icao24") == up and not ctx.blink_on:
            continue
        c = dim(col, f if lit or p.get("icao24") == up else f * 0.55)
        img.paste(c, (max(0, x - 1), y, min(W, x + 1), y + 2))


def _demo_nearby() -> list[dict]:
    r = random.Random(7)
    return [{"az": r.randint(0, 359), "elev": r.uniform(1, 30), "status": r.choice([ARR, DEP, OVF]),
             "in_view": r.choice(["now", "no", "no"]), "icao24": str(i)} for i in range(8)]
