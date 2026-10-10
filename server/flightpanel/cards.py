"""Die Leerlauf-Karten. Jede: Bedingung (Hit oder None) + Zeichenfunktion
(img, ctx, data, t_in, f) + Beispieldaten für die Vorschau (/frame.png?card=<id>).

Gruppen und Gewichte: Nützliches (nächster Flieger, Radar, Piste, Wetter) kommt
öfter als Statistik, Statistik öfter als Witze. Privatflieger, Hubschrauber und
Militär im Sichtfeld sind eine Karte wie jede andere, ohne Vorrang. Dringend
(bricht durch): NEU!-Airline, Flieger in unter 3 min, seltenes Flugzeug in den
ersten 2 min, ISS gerade sichtbar. Der Notfall-Squawk ist die Karte "alert",
render.py zeigt sie vor allem anderen, auch vor Flügen.

Zeilenraster wie die Fluglayouts: drei Zeilen bei y = 0, 12, 23."""

import math
import random
from datetime import datetime

from PIL import Image

from .aircraft import second_line, shown_elsewhere
from . import sprites
from .astro import hhmm, sun_day
from .colors import (ARR_C, BAND, CLOCK, CYAN, DEP_C, GOLD, HORIZON, INFO, MUTED, OVF_C, PINK, RED,
                     dim)
from .config import in_hours
from .fonts import FONT_BIG, FONT_CLOCK, FONT_MID, FONT_SMALL, Font
from .geo import kompass
from .idle import Card, Ctx, Hit
from .metar import describe_wx
from .model import ARR, DEP, path_in_view, runway_name

ROW1, ROW2, ROW3 = 0, 12, 24       # die untere Zeile sitzt auf der Kante (siehe Font.draw)
STATUS_C = {ARR: ARR_C, DEP: DEP_C, "OVF": OVF_C}
WEEKDAYS = ["Mo", "Di", "Mi", "Do", "Fr", "Sa", "So"]
NEXT_MAX_S = 600
NEXT_URGENT_S = 180
NEW_URGENT_S = 120
RARE_URGENT_S = 120
QUIET_MIN_S = 600
RUNWAY_STABLE_S = 1800
RUNWAY_SEEN_S = 2 * 3600


# ---------------------------------------------------------------- Helfer --
def text(img, font: Font, x: int, y: int, s: str, c, f: float, max_w: int | None = None) -> int:
    if max_w is not None:
        s = font.fit(s, max_w)
    return font.draw(img, x, y, s, dim(c, f))


def center(img, font: Font, y: int, s: str, c, f: float, x0: int = 0, x1: int | None = None) -> None:
    x1 = img.width if x1 is None else x1
    s = font.fit(s, x1 - x0)
    font.draw(img, x0 + (x1 - x0 - font.text_width(s)) // 2, y, s, dim(c, f))


def line(img, x: int, y: int, s: str, c, f: float, max_w: int | None = None) -> int:
    """Eine Zeile in FONT_MID; passt sie nicht, in FONT_SMALL, notfalls gekürzt."""
    max_w = (img.width - x) if max_w is None else max_w
    if FONT_MID.text_width(s) <= max_w:
        return FONT_MID.draw(img, x, y, s, dim(c, f))
    return FONT_SMALL.draw(img, x, y + 1, FONT_SMALL.fit(s, max_w), dim(c, f))


def right(img, font: Font, y: int, s: str, c, f: float) -> None:
    font.draw(img, img.width - font.text_width(s), y, s, dim(c, f))


def km(n: float) -> str:
    return f"{int(round(n)):,}".replace(",", " ")


def minutes(s: float) -> str:
    m = max(1, round(s / 60))
    return f"{m // 60} h {m % 60:02d} min" if m >= 90 else f"{m} min"


def blink(now: float) -> bool:
    """An/aus im Takt der ESP32-Abrufe (alle 2 s)."""
    return int(now // 2) % 2 == 0


def frame(img, c, f: float) -> None:
    W, H = img.size
    c = dim(c, f)
    for x in range(W):
        img.putpixel((x, 0), c)
        img.putpixel((x, H - 1), c)
    for y in range(H):
        img.putpixel((0, y), c)
        img.putpixel((W - 1, y), c)


def _rng(ctx: Ctx, salt: str) -> random.Random:
    return random.Random(f"{int(ctx.now // max(1, ctx.cfg.idle_seconds))}:{salt}")


def in_cone(ctx: Ctx, az: float, el: float | None = None, margin: float = 0) -> bool:
    c = ctx.cfg
    if c.view_span >= 360:
        ok = True
    else:
        ok = (az - c.view_from + margin) % 360 <= c.view_span + 2 * margin
    return ok and (el is None or c.view_min_elev <= el <= c.view_max_elev)


def side_arrow(ctx: Ctx, az: float) -> str:
    """← oder →: auf welcher Seite des Fensters etwas auftaucht. Leer bei Rundumsicht."""
    if ctx.cfg.view_span >= 300:
        return ""
    d = (az - ctx.cfg.view_center + 180) % 360 - 180
    return "←" if d < 0 else "→"


# ------------------------------------------------------------------ Uhr --
def clock_cond(ctx: Ctx):
    return Hit()


def clock_draw(img, ctx, data, t_in, f):
    t = datetime.fromtimestamp(ctx.now)
    txt = t.strftime("%H:%M")
    FONT_CLOCK.draw(img, (img.width - FONT_CLOCK.text_width(txt)) // 2, 3, txt, dim(CLOCK, f))
    date = f"{WEEKDAYS[t.weekday()]} {t.strftime('%d.%m.')}"
    FONT_SMALL.draw(img, (img.width - FONT_SMALL.text_width(date)) // 2, 22, date, dim(MUTED, 0.6 * f))


# --------------------------------------------------------- Privatflieger --
def private_cond(ctx: Ctx):
    others = [fl for fl in ctx.model.flights if fl.kind != "airline"]
    return Hit(others) if others and ctx.draw_flight else None


def private_demo(ctx: Ctx):
    from .model import Flight
    return [Flight(icao24="demo", callsign="DEFGH", status="OVF", dist_km=6.2, bearing=160, alt_ft=2500,
                   speed_kt=95, track=250, vrate_fpm=0, actype="C172", kind="other",
                   airline="D-EFGH", label="Kleinflugzeug")]


def private_draw(img, ctx, data, t_in, f):
    slot = int(ctx.now // max(1, ctx.cfg.idle_seconds))
    fl = data[slot % len(data)]
    page = int(t_in // max(1, ctx.cfg.page_seconds)) % 3
    ctx.draw_flight(img, ctx.model, fl, page, f)


# ------------------------------------------------------ Nächster Flieger --
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
    from .model import Flight
    fl = Flight(icao24="demo", callsign="EZY45KZ", status=ARR, dist_km=24, bearing=250, alt_ft=4000,
                speed_kt=200, track=70, vrate_fpm=-700, origin="LGW", dest="BER", airline="easyJet",
                airline_icao="EZY", airline_iata="U2", actype="A320", visible=False)
    return {"flight": fl, "eta": 170, "az": ctx.cfg.view_from, "via": "approach"}


def next_draw(img, ctx, d, t_in, f):
    fl, eta, az = d["flight"], d["eta"], d["az"]
    col = STATUS_C.get(fl.status, OVF_C)
    glyph = "↓" if fl.status == ARR else "↑" if fl.status == DEP else "→"
    W = img.width
    head = f"{glyph} {fl.airline or fl.callsign}"
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
    if where and not (eta < NEXT_URGENT_S / 3 and not blink(ctx.now)):
        FONT_MID.draw(img, W - FONT_MID.text_width(where), ROW2, where, dim(GOLD, f))
    # Route, sonst was der Flug ist, sonst ?→BER; das Rufzeichen nur, wenn es oben nicht steht
    bottom = (second_line(fl.origin, fl.dest, fl.status, fl.label, ctx.cfg.local_airport_iata)
              or ("" if shown_elsewhere(fl.callsign, head) else fl.callsign))
    line(img, 0, ROW3, bottom, MUTED, f, W - (FONT_MID.text_width(fl.actype) + 6 if fl.actype else 0))
    if fl.actype:
        right(img, FONT_MID, ROW3, fl.actype, MUTED, 0.7 * f)


# --------------------------------------------------- Abflug und Landung BER --
def _runway_lab(ctx: Ctx) -> str:
    c = getattr(ctx.model, "landing_course", None)
    return f"Piste {runway_name(c)}" if c is not None else ""


def _two_rows(img, ctx, rows, f) -> None:
    """Bis zu zwei Flüge in Zeile 2 und 3: links Airline und Ort, rechts der Zustand.
    Links mehrere Fassungen, lang bis kurz ("Lufthansa aus MUC", "Lufthansa MUC", "Lufthansa"):
    die erste, die (notfalls in FONT_SMALL) passt, statt abzuschneiden."""
    W = img.width
    for (variants, state, hot), y in zip(rows[:2], (ROW2, ROW3)):
        sw = FONT_MID.text_width(state) + 4 if state else 0
        avail = W - sw
        name = next((v for v in variants if min(FONT_MID.text_width(v), FONT_SMALL.text_width(v)) <= avail),
                    variants[-1])
        line(img, 0, y, name, INFO, f, avail)
        if state:
            right(img, FONT_MID, y, state, GOLD if hot else MUTED, f)


def _title(img, ctx, text_: str, col, f) -> None:
    W = img.width
    x = FONT_MID.draw(img, 0, ROW1, text_, dim(col, f))
    lab = _runway_lab(ctx)
    if lab and x + 4 + FONT_SMALL.text_width(lab) <= W:
        right(img, FONT_SMALL, ROW1 + 1, lab, MUTED, 0.8 * f)


def departures_cond(ctx: Ctx):
    deps = getattr(ctx.model, "departures", None) or []
    return Hit(deps[:2]) if deps else None


def departures_demo(ctx: Ctx):
    return [{"airline": "easyJet", "dest": "LGW", "phase": "startet", "ago_s": None, "type": "A320"},
            {"airline": "Ryanair", "dest": "PMI", "phase": "gestartet", "ago_s": 130, "type": "B738"}]


def departures_draw(img, ctx, deps, t_in, f):
    """Wer gerade am Platz startet: rollt zur Bahn, startet, oder ist vor kurzem gestartet."""
    _title(img, ctx, f"↑ Abflug {ctx.cfg.local_airport_iata}", DEP_C, f)
    rows = []
    for d in deps:
        name = [f"{d['airline']} → {d['dest']}", f"{d['airline']} {d['dest']}", d["airline"]] if d.get("dest") else [d["airline"]]
        if d["phase"] == "gestartet":
            ago = (d.get("ago_s") or 0) + (ctx.now - ctx.model.generated_at)
            state = "gerade" if ago < 60 else f"vor {minutes(ago)}"
        else:
            state = d["phase"]
        rows.append((name, state, d["phase"] == "startet"))
    if len(rows) == 1 and deps[0].get("type"):
        rows.append(([deps[0]["type"]], "", False))   # zweite Zeile frei: der Typ
    _two_rows(img, ctx, rows, f)


def arrivals_cond(ctx: Ctx):
    arr = [a for a in getattr(ctx.model, "arrivals", None) or [] if (a.get("eta_s") or 9e9) <= 15 * 60]
    return Hit(arr[:2]) if arr else None


def arrivals_demo(ctx: Ctx):
    return [{"airline": "Ryanair", "origin": "DUB", "eta_s": 240, "type": "B738"},
            {"airline": "Lufthansa", "origin": "MUC", "eta_s": 520, "type": "A21N"}]


def arrivals_draw(img, ctx, arr, t_in, f):
    """Wer als nächstes landet, mit Herkunft und Minuten bis zum Aufsetzen."""
    _title(img, ctx, f"↓ Landung {ctx.cfg.local_airport_iata}", ARR_C, f)
    rows = []
    for a in arr:
        name = [f"{a['airline']} aus {a['origin']}", f"{a['airline']} {a['origin']}", a["airline"]] if a.get("origin") else [a["airline"]]
        eta = (a.get("eta_s") or 0) - (ctx.now - ctx.model.generated_at)
        rows.append((name, "landet" if eta < 60 else f"~{minutes(eta)}", eta < 60))
    if len(rows) == 1 and arr[0].get("type"):
        rows.append(([arr[0]["type"]], "", False))
    _two_rows(img, ctx, rows, f)


# ----------------------------------------------------------------- Radar --
def radar_cond(ctx: Ctx):
    near = ctx.model.nearby
    if not near:
        return None
    arr = sum(1 for n in near if n.get("seq") or n.get("status") == ARR)
    return Hit({"n": len(near), "arr": arr})


def radar_demo(ctx: Ctx):
    return {"n": 0, "arr": 0}


def radar_draw(img, ctx, d, t_in, f):
    W, H = img.size
    cfg = ctx.cfg
    near = ctx.model.nearby or _demo_nearby(ctx)
    n = d["n"] or len(near)
    arr = d["arr"] if d["n"] else sum(1 for x in near if x.get("status") == ARR)
    head = f"{n} im Umkreis" + (f" · {arr} landen" if arr else "")
    FONT_SMALL.draw(img, 0, 0, FONT_SMALL.fit(head, W), dim(INFO, f))
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
        col = STATUS_C.get(p.get("status"), OVF_C)
        lit = p.get("in_view") == "now"
        if p.get("icao24") == up and not blink(ctx.now):
            continue
        c = dim(col, f if lit or p.get("icao24") == up else f * 0.55)
        img.paste(c, (max(0, x - 1), y, min(W, x + 1), y + 2))


def _demo_nearby(ctx: Ctx) -> list[dict]:
    r = random.Random(7)
    return [{"az": r.randint(0, 359), "elev": r.uniform(1, 30), "status": r.choice([ARR, DEP, "OVF"]),
             "in_view": r.choice(["now", "no", "no"]), "icao24": str(i)} for i in range(8)]


# ----------------------------------------------------------------- Piste --
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
    app = bool(path_in_view(ctx.cfg, course, "approach"))
    dep = bool(path_in_view(ctx.cfg, course, "departure"))
    if app and dep:
        return "An- und Abflüge im Blick", GOLD
    if app:
        return "Landungen im Blick", ARR_C
    if dep:
        return "Starts im Blick", DEP_C
    return "so nicht im Blick", MUTED


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


# ---------------------------------------------------------------- Wetter --
def weather_cond(ctx: Ctx):
    """Nur wenn das Wetter erklärt, warum am Himmel nichts zu sehen ist."""
    m = ctx.metar.fresh(ctx.now) if ctx.metar else None
    if m is None:
        return None
    above = None
    if m.ceiling_m is not None:
        above = sum(1 for n in ctx.model.nearby if n.get("alt_ft") and n["alt_ft"] * 0.3048 > m.ceiling_m)
    if m.has("TS"):
        return Hit({"kind": "ts", "m": m, "above": above}, weight=3)
    if (m.vis_m is not None and m.vis_m < 2000) or m.has("FG"):
        return Hit({"kind": "fog", "m": m, "above": above}, weight=3)
    if m.ceiling_m is not None and m.ceiling_m < 1500:
        return Hit({"kind": "cloud", "m": m, "above": above}, weight=2.5)
    if m.has("SN"):
        return Hit({"kind": "snow", "m": m, "above": above}, weight=2)
    return None


def weather_demo(ctx: Ctx):
    from .metar import parse
    return {"kind": "cloud", "m": parse("EDDB 101220Z 25012KT 9999 BKN020 OVC035 09/07 Q1012"), "above": 3}


def weather_draw(img, ctx, d, t_in, f):
    m, kind, above = d["m"], d["kind"], d["above"]
    W = img.width
    if kind == "ts":
        FONT_BIG.draw(img, 0, ROW1, f"Gewitter am {ctx.cfg.local_airport_iata}", dim(GOLD, f))
        text(img, FONT_MID, 0, ROW2, "Vorfeld gesperrt,", INFO, f, W)
        text(img, FONT_MID, 0, ROW3, "Flieger warten", INFO, f, W)
        if blink(ctx.now):
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
        FONT_BIG.draw(img, 0, ROW1, f"Schnee am {ctx.cfg.local_airport_iata}", dim(CYAN, f))
        text(img, FONT_MID, 0, ROW2, "Enteisen dauert", INFO, f, W)
    else:
        FONT_BIG.draw(img, 0, ROW1, f"Wolken auf {km(round(m.ceiling_m, -2))} m", dim(MUTED, f))
        text(img, FONT_MID, 0, ROW2, "Flieger versteckt", INFO, f, W)
    if above:
        text(img, FONT_MID, 0, ROW3, f"{above} Flieger darüber", OVF_C, f, W)
    else:
        wx = describe_wx(m)
        if wx:
            text(img, FONT_MID, 0, ROW3, wx, MUTED, f, W)


# ----------------------------------------------------- Nachtflugverbot --
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


# ------------------------------------------------------------- Statistik --
def scoreboard_cond(ctx: Ctx):
    if ctx.stats is None:
        return None
    t = ctx.stats.today()
    return Hit(t) if t["aircraft"] >= 10 else None


def scoreboard_demo(ctx: Ctx):
    return {"aircraft": 143, "airlines": 27, "shown": 38, "max_alt_m": 11900, "max_alt_callsign": "UAE59"}


def scoreboard_draw(img, ctx, d, t_in, f):
    W = img.width
    x = FONT_BIG.draw(img, 0, ROW1, "Heute:", dim(GOLD, f))
    FONT_BIG.draw(img, x + 4, ROW1, f"{km(d['aircraft'])} Flieger", dim(GOLD, f))
    line2 = f"{d['airlines']} Airlines" + (f" · {d['shown']} hier" if d.get("shown") else "")
    text(img, FONT_MID, 0, ROW2, line2, INFO, f, W)
    if d.get("max_alt_m"):
        text(img, FONT_MID, 0, ROW3, f"höchster {km(d['max_alt_m'])} m", MUTED, f, W)


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
    W = img.width
    logo = None
    if ctx.logos is not None and d.get("known"):
        n = ctx.names.get(d.get("code", ""), {})
        logo = ctx.logos.get(n.get("iata", ""), d.get("code", ""))
    x0 = 0
    if logo is not None:
        if f < 1.0:
            logo = Image.eval(logo, lambda v: int(v * f))
        img.paste(logo, (0, (img.height - logo.height) // 2))
        x0 = logo.width + 2
    if blink(ctx.now):
        FONT_BIG.draw(img, x0, ROW1, "NEU!", dim(GOLD, f))
    FONT_SMALL.draw(img, W - FONT_SMALL.text_width(d.get("callsign", "")), ROW1 + 1, d.get("callsign", ""),
                    dim(MUTED, f * 0.7))
    text(img, FONT_BIG, x0, ROW2, d["name"], ARR_C, f, W - x0)
    if d.get("known"):
        text(img, FONT_SMALL, x0, ROW3 + 1, f"Nr. {d['seen']} von {d['total']}", INFO, f, W - x0)
    else:
        text(img, FONT_SMALL, x0, ROW3 + 1, "nicht im Album", PINK, f, W - x0)
    if logo is None:
        frame(img, GOLD if blink(ctx.now) else ARR_C, f * 0.6)


def rare_cond(ctx: Ctx):
    if ctx.stats is None:
        return None
    shown = {fl.icao24 for fl in ctx.model.flights}
    hits = [e for e in ctx.stats.rare_now(ctx.now) if e["icao24"] not in shown]
    if not hits:
        return None
    e = hits[0]
    urgent = e["kind"] == "type" and ctx.now - e["first"] < RARE_URGENT_S
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


# ------------------------------------------------------------ Himmel --
def iss_cond(ctx: Ctx):
    iss = ctx.astro
    if iss is None:
        return None
    p = iss.next_pass(ctx.now)
    if p is None:
        return None
    if p.start <= ctx.now <= p.end:
        st = iss.state(ctx.now)
        if st is None:
            return None
        return Hit({"pass": p, "now": True, "az": st[0], "el": st[1]}, urgent=True)
    if 0 < p.start - ctx.now <= 30 * 60:
        return Hit({"pass": p, "now": False}, weight=3 if p.start - ctx.now < 600 else 2)
    return None


def iss_demo(ctx: Ctx):
    from .astro import Pass
    t = ctx.now + 240
    return {"pass": Pass(start=t, end=t + 300, max_elev=52, max_time=t + 150, az_start=260, az_end=80,
                         view_start=t + 60), "now": False}


def iss_draw(img, ctx, d, t_in, f):
    p = d["pass"]
    W = img.width
    sprites.blit(img, sprites.ISS, W - 8, 2, sprites.ISS_PAL, f)
    if d["now"]:
        if blink(ctx.now):
            FONT_BIG.draw(img, 0, ROW1, "ISS jetzt!", dim(CYAN, f))
        text(img, FONT_MID, 0, ROW2, f"{kompass(d['az'])}, {d['el']:.0f}° hoch", INFO, f, W)
        if in_cone(ctx, d["az"], d["el"]):
            arrow = side_arrow(ctx, d["az"])
            text(img, FONT_MID, 0, ROW3, f"im Fenster {arrow}".strip(), GOLD, f, W)
        else:
            text(img, FONT_MID, 0, ROW3, f"bis {hhmm(p.end)}, nicht im Fenster", MUTED, f, W)
        return
    FONT_BIG.draw(img, 0, ROW1, f"ISS in {minutes(p.start - ctx.now)}", dim(CYAN, f))
    text(img, FONT_MID, 0, ROW2, f"{kompass(p.az_start)} → {kompass(p.az_end)} · max {p.max_elev:.0f}°", INFO, f, W)
    if p.view_start is not None:
        text(img, FONT_MID, 0, ROW3, f"im Fenster ab {hhmm(p.view_start)}", GOLD, f, W)
    else:
        text(img, FONT_MID, 0, ROW3, f"{hhmm(p.start)}-{hhmm(p.end)}, nicht im Fenster", MUTED, f, W)


def _sun_draw(img, ctx, f, k: float, rising: bool) -> None:
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
    _sun_draw(img, ctx, f, 1 - min(1.0, (d["t"] - ctx.now) / 7200), rising=False)


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
    _sun_draw(img, ctx, f, min(1.0, (d["t"] - ctx.now) / 5400), rising=True)


# --------------------------------------------------------------- Spaß --
def _fmt(s: str, ctx: Ctx) -> str:
    t = datetime.fromtimestamp(ctx.now)
    return s.format(year=t.year, next_year=t.year + 1, ber_age=t.year - 2020)


def _today_md(ctx: Ctx) -> str:
    return datetime.fromtimestamp(ctx.now).strftime("%m-%d")


def joke_cond(ctx: Ctx):
    jokes = ctx.fun.get("jokes", [])
    md = _today_md(ctx)
    dated = [j for j in jokes if md in j.get("dates", [])]
    plain = [j for j in jokes if not j.get("dates")]
    if dated:
        return Hit(_rng(ctx, "joke").choice(dated), weight=4)
    if plain:
        return Hit(_rng(ctx, "joke").choice(plain))
    return None


def joke_demo(ctx: Ctx):
    return {"lines": ["SXF heißt jetzt", "BER Terminal 5", "...und ist zu."]}


def joke_draw(img, ctx, d, t_in, f):
    lines = [_fmt(s, ctx) for s in d.get("lines", [])][:3]
    cols = [GOLD, INFO, MUTED]
    fonts = [FONT_MID, FONT_MID, FONT_MID]
    for i, s in enumerate(lines):
        center(img, fonts[i], (ROW1, ROW2, ROW3)[i], s, cols[i], f)


def delay_cond(ctx: Ctx):
    reasons = ctx.fun.get("delays", [])
    if not reasons:
        return None
    r = _rng(ctx, "delay")
    return Hit({"reason": reasons[r.randrange(len(reasons))]})


def delay_demo(ctx: Ctx):
    return {"reason": "Rolltreppen einige|Stufen zu kurz|2012"}


def delay_draw(img, ctx, d, t_in, f):
    """Echte Verspätungsgründe des Heimatflughafens (data/jokes.json, für BER: warum er
    neun Jahre zu spät eröffnete): zwei Zeilen, das Jahr rechts unten, wenn es neben
    die zweite passt."""
    W = img.width
    text(img, FONT_SMALL, 0, ROW1 + 1, f"Verspätungsgrund {ctx.cfg.local_airport_iata}", ARR_C, f, W)
    a, b, year = (_fmt(d["reason"], ctx).split("|") + ["", "", ""])[:3]
    line(img, 0, ROW2, a, INFO, f)
    yw = FONT_SMALL.text_width(year) + 6 if year else 0
    show_year = bool(year) and FONT_MID.text_width(b) + yw <= W
    line(img, 0, ROW3, b, INFO, f, W - yw if show_year else None)
    if show_year:
        right(img, FONT_SMALL, ROW3 + 1, year, MUTED, 0.8 * f)


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
    for i, y in enumerate(years[:shown]):
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


PLANE_TEXTS = ["Himmel frei", "Warteschleife …", "gleich kommt einer"]   # ohne eigene in jokes.json


def plane_cond(ctx: Ctx):
    return Hit({"text": _rng(ctx, "plane").choice(ctx.fun.get("plane") or PLANE_TEXTS)})


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


def _season(ctx: Ctx) -> datetime:
    return datetime.fromtimestamp(ctx.now)


def santa_cond(ctx: Ctx):
    t = _season(ctx)
    if (t.month, t.day) == (12, 24) and t.hour >= 15 or (t.month, t.day) == (12, 25) and t.hour < 6:
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
    if not blink(ctx.now):
        pal["n"] = (120, 20, 20)
    sprites.blit(img, sprites.SLEIGH, x, 2, pal, f)
    left = "gelandet!" if d["km"] <= 0 else f"{km(d['km'])} km"
    FONT_MID.draw(img, 0, ROW2, "Schlitten 1", dim(RED, f))
    right(img, FONT_MID, ROW2, left, GOLD, f)
    line(img, 0, ROW3, "Rentiere: 9 · Ziel: alle", INFO, f)


def fireworks_cond(ctx: Ctx):
    t = _season(ctx)
    if (t.month, t.day) == (12, 31) and t.hour >= 18 or (t.month, t.day) == (1, 1) and t.hour < 2:
        return Hit({}, weight=8)
    return None


def fireworks_draw(img, ctx, d, t_in, f):
    t = _season(ctx)
    sprites.fireworks(img, int(ctx.now // max(1, ctx.cfg.idle_seconds)), t_in, f, rockets=5)
    if t.month == 1:
        center(img, FONT_BIG, ROW2, f"Frohes {t.year}!", GOLD, f)
    elif (t.month, t.day) == (12, 31):
        left = datetime(t.year + 1, 1, 1).timestamp() - ctx.now
        when = f"{math.ceil(left / 3600)} h" if left > 2 * 3600 else minutes(left)
        center(img, FONT_MID, ROW2, f"noch {when} bis {t.year + 1}", GOLD, f)
    else:
        center(img, FONT_BIG, ROW2, "Frohes neues Jahr!", GOLD, f)


# ---------------------------------------------------------------- Notfall --
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


ALERT = Card("alert", alert_cond, alert_draw, demo=alert_demo, tier=0, group="notfall", live=True)

CARDS = [
    ALERT,
    Card("clock", clock_cond, clock_draw, tier=3, group="uhr"),
    Card("private", private_cond, private_draw, weight=2.0, tier=2, group="nützlich", live=True, demo=private_demo),
    Card("departures", departures_cond, departures_draw, weight=3.0, tier=2, group="nützlich", live=True,
         demo=departures_demo),
    Card("arrivals", arrivals_cond, arrivals_draw, weight=2.5, tier=2, group="nützlich", live=True,
         demo=arrivals_demo),
    Card("next", next_cond, next_draw, weight=3.0, tier=2, group="nützlich", live=True, demo=next_demo),
    Card("radar", radar_cond, radar_draw, weight=1.5, tier=3, group="nützlich", live=True, demo=radar_demo),
    Card("runway", runway_cond, runway_draw, weight=1.5, tier=3, group="nützlich", demo=runway_demo),
    Card("runway_change", runway_change_cond, runway_change_draw, tier=2, group="nützlich", demo=runway_change_demo),
    Card("weather", weather_cond, weather_draw, tier=3, group="nützlich", demo=weather_demo),
    Card("curfew", curfew_cond, curfew_draw, tier=3, group="nützlich", demo=curfew_demo),
    Card("scoreboard", scoreboard_cond, scoreboard_draw, weight=1.2, tier=3, group="nerdig", demo=scoreboard_demo),
    Card("pokedex", pokedex_cond, pokedex_draw, weight=1.0, tier=3, group="nerdig", demo=pokedex_demo),
    Card("new", new_cond, new_draw, tier=3, group="nerdig", demo=new_demo),
    Card("rare", rare_cond, rare_draw, tier=3, group="nerdig", demo=rare_demo),
    Card("quiet", quiet_cond, quiet_draw, weight=1.0, tier=3, group="nerdig", live=True, demo=quiet_demo),
    Card("iss", iss_cond, iss_draw, tier=3, group="himmel", demo=iss_demo),
    Card("sunset", sunset_cond, sunset_draw, tier=3, group="himmel", demo=sunset_demo),
    Card("sunrise", sunrise_cond, sunrise_draw, tier=3, group="himmel", demo=sunrise_demo),
    Card("joke", joke_cond, joke_draw, weight=0.6, tier=4, group="spaß", demo=joke_demo),
    Card("delay", delay_cond, delay_draw, weight=0.5, tier=4, group="spaß", demo=delay_demo),
    Card("txl", txl_cond, txl_draw, weight=0.25, tier=4, group="spaß", only_at="BER"),
    Card("ber_opening", ber_opening_cond, ber_opening_draw, weight=0.25, tier=4, group="spaß", only_at="BER"),
    Card("plane", plane_cond, plane_draw, weight=0.5, tier=4, group="spaß"),
    Card("pigeon", pigeon_cond, pigeon_draw, weight=0.3, tier=4, group="spaß"),
    Card("santa", santa_cond, santa_draw, tier=3, group="saison", demo=santa_demo),
    Card("fireworks", fireworks_cond, fireworks_draw, tier=3, group="saison", demo=lambda ctx: {}),
]
