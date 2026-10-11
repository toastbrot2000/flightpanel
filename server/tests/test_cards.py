"""Bedingungen der Karten (wann sie etwas zu sagen haben) und das Rendern."""

from dataclasses import replace
from datetime import UTC, datetime

import pytest
from PIL import Image

from conftest import NAMES, make_cfg, state
from flightpanel.astro import Pass, sun_day
from flightpanel.cards import CARDS, album, fun, runway, sky, traffic
from flightpanel.draw import ROW2, ROW3
from flightpanel.idle import Ctx, IdleScreen
from flightpanel.logos import LogoStore, make_logo_store
from flightpanel.metar import MetarSource, parse
from flightpanel.model import DisplayModel, Flight
from flightpanel.render import LOGO_COL, Renderer
from flightpanel.stats import Stats


def ts(*a):
    return datetime(*a).timestamp()


def fl(kind="airline", icao="f1", visible=True):
    return Flight(icao24=icao, callsign="DEFGH" if kind == "other" else "EZY1", status="OVF", dist_km=5,
                  bearing=150, alt_ft=2500, speed_kt=100, track=250, vrate_fpm=0, kind=kind, visible=visible)


def ctx(now=None, model=None, cfg=None, **kw):
    return Ctx(cfg=cfg or make_cfg(), model=model or DisplayModel(source="test"),
               now=now if now is not None else ts(2026, 10, 10, 15, 0), names=NAMES, **kw)


SQUAWK = {"callsign": "DLH4AB", "squawk": "7700", "meaning": "NOTFALL", "type": "A20N", "dist_km": 9.0,
          "az": 120, "alt_ft": 3000, "in_view": False}


def metar_src(raw, now):
    src = MetarSource(None, "EDDB")
    src.current = parse(raw, now)
    return src


# ------------------------------------------------------------ Bedingungen --
def test_next_countdown_and_urgency():
    t = ts(2026, 10, 10, 15, 0)
    m = DisplayModel(source="test", generated_at=t, upcoming=fl(), upcoming_eta_s=400, upcoming_az=150)
    assert not traffic.next_cond(ctx(t, m)).urgent
    assert traffic.next_cond(ctx(t + 300, m)).urgent              # 100 s übrig
    assert traffic.next_cond(ctx(t + 395, m)) is None             # schon da
    m.upcoming_eta_s = 700
    assert traffic.next_cond(ctx(t, m)) is None                   # zu weit weg


def test_weather_only_when_it_explains_an_empty_sky():
    t = ts(2026, 10, 10, 15, 0)
    near = DisplayModel(source="test", nearby=[{"alt_ft": 5000}, {"alt_ft": 1000}])
    stamp = datetime.fromtimestamp(t, UTC).strftime("%d%H%MZ")
    assert runway.weather_cond(ctx(t, near, metar=metar_src(f"EDDB {stamp} 21007KT CAVOK 13/05 Q1007", t))) is None
    h = runway.weather_cond(ctx(t, near, metar=metar_src(f"EDDB {stamp} 21007KT 9999 BKN012 13/05 Q1007", t)))
    assert h.data["kind"] == "cloud" and h.data["above"] == 1
    h = runway.weather_cond(ctx(t, near, metar=metar_src(f"EDDB {stamp} 00000KT 0400 FG VV002 03/03 Q1021", t)))
    assert h.data["kind"] == "fog"
    h = runway.weather_cond(ctx(t, near, metar=metar_src(f"EDDB {stamp} 27012KT 9999 TSRA BKN030CB 21/15 Q1008", t)))
    assert h.data["kind"] == "ts"
    old = datetime.fromtimestamp(t - 3 * 3600, UTC).strftime("%d%H%MZ")
    stale_fog = metar_src(f"EDDB {old} 00000KT 0400 FG VV002 03/03 Q1021", t)
    assert runway.weather_cond(ctx(t, near, metar=stale_fog)) is None
    assert runway.weather_cond(ctx(t, near, metar=None)) is None


def test_weather_does_not_claim_hidden_planes_that_are_not_there():
    """Wolken und Nebel verstecken nur, was fliegt: Fliegt keiner darüber (oder sind
    die Flugdaten alt), sagt die Karte nichts. Gewitter erklärt den leeren Himmel auch so."""
    t = ts(2026, 10, 10, 15, 0)
    stamp = datetime.fromtimestamp(t, UTC).strftime("%d%H%MZ")
    clouds = metar_src(f"EDDB {stamp} 25007KT 9999 OVC049 09/08 Q1011", t)
    fog = metar_src(f"EDDB {stamp} 00000KT 0400 FG VV002 03/03 Q1021", t)
    storm = metar_src(f"EDDB {stamp} 27012KT 9999 TSRA BKN030CB 21/15 Q1008", t)
    nobody = DisplayModel(source="test")
    below = DisplayModel(source="test", nearby=[{"alt_ft": 3000}])          # unter den Wolken, zu sehen
    stale = DisplayModel(source="test", stale=True, nearby=[{"alt_ft": 38000}])
    assert runway.weather_cond(ctx(t, nobody, metar=clouds)) is None
    assert runway.weather_cond(ctx(t, below, metar=clouds)) is None
    assert runway.weather_cond(ctx(t, stale, metar=clouds)) is None
    assert runway.weather_cond(ctx(t, nobody, metar=fog)) is None
    assert runway.weather_cond(ctx(t, below, metar=fog)).data["kind"] == "fog"
    assert runway.weather_cond(ctx(t, nobody, metar=storm)).data["kind"] == "ts"


def test_weather_headline_fits_for_every_ceiling():
    """"Wolken auf 1500 m" passt in großer Schrift aufs Panel, für jede Wolkenhöhe, die
    die Karte zeigt: Auf doppelt so breiter Fläche gezeichnet, bleibt rechts alles frei."""
    t = ts(2026, 10, 10, 15, 0)
    stamp = datetime.fromtimestamp(t, UTC).strftime("%d%H%MZ")
    for hundreds_ft in range(1, 50):                        # BKN001 bis BKN049, knapp unter 1500 m
        m = parse(f"EDDB {stamp} 21007KT 9999 BKN{hundreds_ft:03d} 13/05 Q1007", t)
        wide = Image.new("RGB", (256, 32))
        runway.weather_draw(wide, ctx(t), {"kind": "cloud", "m": m, "above": 12}, 0, 1.0)
        assert wide.crop((128, 0, 256, 32)).getbbox() is None, f"Wolken auf {m.ceiling_m} m"


def _stats(tmp_path, course=249, since_min=60, seen_min=5, prev=None, now=None):
    st = Stats(str(tmp_path / "s.json"), make_cfg(), NAMES, {"types": {}, "callsigns": {}})
    st.course, st.prev_course = course, prev
    st.course_since = now - since_min * 60
    st.course_seen = now - seen_min * 60
    return st


def test_runway_card_only_when_stable(tmp_path):
    t = ts(2026, 10, 10, 15, 0)
    stamp = datetime.fromtimestamp(t, UTC).strftime("%d%H%MZ")
    h = runway.runway_cond(ctx(t, stats=_stats(tmp_path, now=t)))
    assert h.data == {"course": 249, "wind": "Westwind"}
    assert runway.runway_cond(ctx(t, stats=_stats(tmp_path, since_min=10, now=t))) is None      # frisch gewechselt
    assert runway.runway_cond(ctx(t, stats=_stats(tmp_path, seen_min=300, now=t))) is None      # lange kein Verkehr
    head = metar_src(f"EDDB {stamp} 25012KT CAVOK 13/05 Q1007", t)
    assert runway.runway_cond(ctx(t, stats=_stats(tmp_path, now=t), metar=head)).data["wind"] == "Wind W 12 kt"
    tail = metar_src(f"EDDB {stamp} 07012KT CAVOK 13/05 Q1007", t)
    assert runway.runway_cond(ctx(t, stats=_stats(tmp_path, now=t), metar=tail)) is None        # Rückenwind
    assert runway.runway_change_cond(ctx(t, stats=_stats(tmp_path, since_min=10, prev=69, now=t))) is not None
    assert runway.runway_change_cond(ctx(t, stats=_stats(tmp_path, since_min=90, prev=69, now=t))) is None


def test_view_line_texts():
    c = ctx(cfg=make_cfg())
    assert runway._view_line(c, 249)[0] == "An- und Abflüge im Blick"
    assert runway._view_line(ctx(cfg=make_cfg(view_from=300, view_to=359)), 249)[0] == "so nicht im Blick"


def test_date_and_time_windows():
    assert runway.curfew_cond(ctx(ts(2026, 10, 10, 2, 0))) is not None
    assert runway.curfew_cond(ctx(ts(2026, 10, 10, 12, 0))) is None
    assert fun.santa_cond(ctx(ts(2026, 12, 24, 18, 0))).data["km"] > 0
    assert fun.santa_cond(ctx(ts(2026, 12, 25, 5, 0))) is not None
    assert fun.santa_cond(ctx(ts(2026, 12, 24, 10, 0))) is None
    assert fun.fireworks_cond(ctx(ts(2026, 12, 31, 23, 30))) is None       # an Silvester noch nicht
    assert fun.fireworks_cond(ctx(ts(2027, 1, 1, 0, 0, 30))) is not None
    assert fun.fireworks_cond(ctx(ts(2027, 1, 1, 15, 0))) is not None       # Neujahr den Tag über
    assert fun.fireworks_cond(ctx(ts(2027, 1, 2, 0, 30))) is None


def test_sunset_and_sunrise_windows():
    c = make_cfg()
    sd = sun_day(ts(2026, 10, 10, 12, 0), c.home_lat, c.home_lon)
    assert sky.sunset_cond(ctx(sd["sunset"] - 3600, cfg=c)) is not None
    assert sky.sunset_cond(ctx(sd["sunset"] - 3 * 3600, cfg=c)) is None
    assert sky.sunrise_cond(ctx(sd["sunrise"] - 1800, cfg=c)) is not None
    assert sky.sunrise_cond(ctx(sd["sunrise"] + 600, cfg=c)) is None


def test_scoreboard_says_how_many_came_through_the_window():
    """Unter der Tageszahl "davon 38 im Fenster"; Airlines und der höchste wechseln sich
    unten im Takt der Infoseiten ab. Auch mit großen Zahlen wird nichts abgeschnitten."""
    c = ctx()
    pages = []
    for t_in in (0, c.cfg.page_seconds):
        img = Image.new("RGB", (128, 32))
        album.scoreboard_draw(img, c, album.scoreboard_demo(c), t_in, 1.0)
        pages.append(img)
    split = ROW3 - 2                                            # Umlautpunkte von Zeile 3 ragen über ROW3
    top, bottom = [[p.crop(box).tobytes() for p in pages] for box in ((0, 0, 128, split), (0, split, 128, 32))]
    assert top[0] == top[1] and bottom[0] != bottom[1]
    many = {"aircraft": 1234, "airlines": 99, "shown": 999, "max_alt_m": 13716}
    img = Image.new("RGB", (128, 32))
    album.scoreboard_draw(img, c, many, 0, 1.0)
    assert img.crop((0, 0, 128, ROW2)).getbbox()[2] < 128      # "Heute: 1 234 Flieger" kleiner statt abgeschnitten
    for t_in in (0, c.cfg.page_seconds):
        wide = Image.new("RGB", (256, 32))
        album.scoreboard_draw(wide, c, many, t_in, 1.0)
        assert wide.crop((128, ROW2, 256, 32)).getbbox() is None, t_in     # Zeile 2 und 3 in normaler Schrift


def test_new_airline_text_stays_off_the_frame(monkeypatch):
    """Ohne Logo hat "NEU!" einen Rahmen. Der Text bleibt innerhalb wie beim Notfall,
    sonst schneidet der Rahmen ihn an ("JTD504", "Jet Time", "nicht im Album")."""
    monkeypatch.setattr(album, "frame", lambda *a: None)         # nur der Text
    d = {"name": "Jet Time", "code": "JTD", "callsign": "JTD504", "known": False, "seen": 44, "total": 119}
    border = [(x, y) for x in range(128) for y in (0, 31)] + [(x, y) for x in (0, 127) for y in range(32)]
    for now in (ts(2026, 10, 11, 23, 58, 0), ts(2026, 10, 11, 23, 58, 2)):      # Blinktakt an und aus
        img = Image.new("RGB", (128, 32))
        album.new_draw(img, ctx(now), d, 0, 1.0)
        assert img.getbbox() is not None
        assert [p for p in border if img.getpixel(p) != (0, 0, 0)] == []


def test_private_planes_are_a_card_not_a_takeover():
    m = DisplayModel(source="test", flights=[fl("other")])
    assert traffic.private_cond(ctx(model=m, draw_flight=lambda *a: None)).data[0].callsign == "DEFGH"
    airline_only = DisplayModel(source="test", flights=[fl()])
    assert traffic.private_cond(ctx(model=airline_only, draw_flight=lambda *a: None)) is None


def test_quiet_rare_new(tmp_path):
    t = ts(2026, 10, 10, 15, 0)
    st = Stats(str(tmp_path / "s.json"), make_cfg(), NAMES, {"types": {"A388": "A380"}, "callsigns": {}})
    st.since = t - 8 * 3600
    st.observe([], DisplayModel(flights=[fl()]), t - 900)
    st.observe([state(icao="big", cs="UAE45", actype="A388", lat=52.0, lon=14.2, alt=11000),
                state(icao="new", cs="DLH9")], DisplayModel(), t)
    assert album.quiet_cond(ctx(t + 10, stats=st)).data["quiet"] >= 900
    h = album.rare_cond(ctx(t + 10, stats=st))
    assert h.urgent and h.data["label"] == "A380"
    assert album.rare_cond(ctx(t + 200, stats=st)).urgent is False
    shown = DisplayModel(source="test", flights=[fl(icao="big")])
    assert album.rare_cond(ctx(t + 10, model=shown, stats=st)) is None
    n = album.new_cond(ctx(t + 10, stats=st))
    assert n.urgent and {n.data["name"]} <= {"Lufthansa", "Emirates"}
    assert album.new_cond(ctx(t + 300, stats=st)) is None


def test_iss_card_countdown_and_now():
    t = ts(2026, 10, 10, 20, 0)

    class FakeIss:
        def __init__(self, p):
            self.p = p

        def next_pass(self, now):
            return self.p

        def state(self, now):
            return (250.0, 35.0, 700.0, True)

    p = Pass(start=t + 600, end=t + 900, max_elev=50, max_time=t + 750, az_start=260, az_end=80)
    assert not sky.iss_cond(ctx(t, astro=FakeIss(p))).urgent
    assert sky.iss_cond(ctx(t + 700, astro=FakeIss(p))).urgent
    assert sky.iss_cond(ctx(t - 3600, astro=FakeIss(p))) is None
    assert sky.iss_cond(ctx(t, astro=FakeIss(None))) is None
    assert sky.iss_cond(ctx(t, astro=None)) is None


# --------------------------------------------------------------- Rendern --
def _renderer(cfg):
    logos = make_logo_store(cfg)
    logos.fetch_enabled = False
    return Renderer(cfg, logos, idle=IdleScreen(cfg, CARDS, logos=logos, names=NAMES))


@pytest.mark.parametrize("cid", [c.id for c in CARDS])
def test_every_card_renders_with_demo_data(cid):
    cfg = make_cfg(view_from=140, view_to=190, view_min_elev=0.5, view_max_km=40)
    r = _renderer(cfg)
    img = Image.new("RGB", (128, 32))
    drawn = r.idle.draw(img, DisplayModel(source="test"), ts(2026, 10, 10, 15, 0, 7), force=cid, preview=True)
    assert drawn == cid, "Karte ist beim Zeichnen abgestürzt und auf die Uhr gefallen"
    assert img.getbbox() is not None
    assert r.idle.last_pick is None                         # Vorschau zählt nicht als gezeigt
    assert r.render(DisplayModel(source="test"), ts(2026, 10, 10, 15, 0), card=cid).size == (128, 32)


def test_render_priorities():
    cfg = make_cfg()
    r = _renderer(cfg)
    now = ts(2026, 10, 10, 15, 0)
    r.render(DisplayModel(source="test", flights=[fl("other")]), now)
    assert r.idle.last_pick is not None                     # Privatflieger: Leerlauf mit Karten
    r.idle.last_pick = None
    r.render(DisplayModel(source="test", flights=[fl()]), now)
    assert r.idle.last_pick is None                         # Airline: normales Layout
    r.render(DisplayModel(source="test", flights=[fl()], alert=SQUAWK), now)
    assert r.idle.last_pick == "alert"                      # Notfall vor allem


def test_new_year_fireworks_take_over_at_midnight():
    """Um 0 Uhr an Neujahr ein paar Minuten Feuerwerk vor allem außer einem Notfall:
    auch vor Flügen, und mit alten Daten (Ruhezeit) in voller Helligkeit."""
    r = _renderer(make_cfg())
    midnight, later = ts(2027, 1, 1, 0, 0, 2), ts(2027, 1, 1, 0, 6, 0)
    flight = DisplayModel(source="test", flights=[fl()])
    r.render(flight, midnight)
    assert r.idle.last_pick == "fireworks"
    r.idle.last_pick = None
    r.render(flight, later)
    assert r.idle.last_pick is None                         # danach wieder der Flug
    r.render(DisplayModel(source="test", flights=[fl()], alert=SQUAWK), midnight)
    assert r.idle.last_pick == "alert"
    stale = DisplayModel(source="test", stale=True, empty=True)
    assert r.render(stale, midnight).tobytes() == r.render(flight, midnight).tobytes()
    off = _renderer(make_cfg(cards_off=frozenset({"fireworks"})))
    off.render(flight, midnight)
    assert off.idle.last_pick is None


def test_new_year_fireworks_light_up_the_quiet_hours():
    cfg = make_cfg(quiet_hours=(0, 6 * 60 + 30))
    r = _renderer(cfg)
    assert r.brightness(ts(2027, 1, 1, 0, 0, 2)) == cfg.brightness_evening
    assert r.brightness(ts(2027, 1, 1, 0, 6, 0)) == 0          # danach Ruhezeit
    assert r.brightness(ts(2027, 1, 2, 0, 0, 2)) == 0
    assert r.brightness(ts(2027, 1, 1, 12, 0)) == cfg.brightness_day
    assert _renderer(replace(cfg, cards_off=frozenset({"fireworks"}))).brightness(ts(2027, 1, 1, 0, 0, 2)) == 0


def _lit(img, box):
    """Welche Pixel im Ausschnitt leuchten."""
    crop = img.crop(box)
    return {(x, y) for x in range(crop.width) for y in range(crop.height) if crop.getpixel((x, y)) != (0, 0, 0)}


def test_flight_without_logo_keeps_the_layout(tmp_path):
    """Im Wechsel mit einem Flug mit Logo: Text und Himmelspunkte bleiben, wo sie sind;
    in der Logospalte steht ein Platzhalter."""
    Image.new("RGB", (32, 32), (200, 30, 30)).save(tmp_path / "LH.png")
    cfg = make_cfg()
    logos = LogoStore(str(tmp_path), cfg.logo_size, fetch=False)
    r = Renderer(cfg, logos, idle=IdleScreen(cfg, CARDS, logos=logos, names=NAMES))
    lh = Flight(icao24="f1", callsign="DLH1", status="ARR", dist_km=12, bearing=150, alt_ft=4200, speed_kt=210,
                track=249, vrate_fpm=-800, origin="FRA", dest="BER", airline="Lufthansa", airline_icao="DLH",
                airline_iata="LH", actype="A320", seq=1, land_eta_s=420)
    nologo = replace(lh, icao24="f2", seq=2, airline_icao="KLC", airline_iata="WA")   # kein WA.png

    def draw(flights, shown):
        img = Image.new("RGB", (cfg.width, cfg.height))
        r.draw_flight(img, DisplayModel(source="test", flights=flights), shown, 0, 1.0)
        return img

    text_area, logo_area = (LOGO_COL, 0, cfg.width, cfg.height), (0, 0, LOGO_COL, cfg.height)
    dots = (0, cfg.height - 3, LOGO_COL, cfg.height)
    a, b = draw([lh, nologo], lh), draw([lh, nologo], nologo)               # im Wechsel, mit Himmelspunkten
    assert a.crop(text_area).tobytes() == b.crop(text_area).tobytes()
    assert _lit(a, dots) == _lit(b, dots)                                   # Punkte an derselben Stelle
    a, b = draw([lh], lh), draw([nologo], nologo)                           # allein
    assert a.crop(text_area).tobytes() == b.crop(text_area).tobytes()
    assert _lit(b, logo_area), "Platzhalter in der Logospalte"
    assert a.crop(logo_area).tobytes() != b.crop(logo_area).tobytes()


def test_private_planes_without_logo_use_the_whole_width():
    """Privatflieger, Hubschrauber, Polizei: kein Airline-Platzhalter, sondern Layout A."""
    other = fl("other")
    m = DisplayModel(source="test", flights=[other])
    imgs = []
    for layout in ("C", "A"):
        img = Image.new("RGB", (128, 32))
        _renderer(make_cfg(layout=layout)).draw_flight(img, m, other, 0, 1.0)
        imgs.append(img.tobytes())
    assert imgs[0] == imgs[1]


# ------------------------------------------------------------ ruhiger Modus --
def test_calm_info_pages_and_rotation():
    from flightpanel.render import info_pages

    landing = Flight(icao24="a", callsign="EZY1", status="ARR", dist_km=12, bearing=150, alt_ft=4200, speed_kt=210,
                     track=249, vrate_fpm=-800, land_eta_s=420, visible=True)
    over = Flight(icao24="b", callsign="EZY2", status="OVF", dist_km=30, bearing=170, alt_ft=34000, speed_kt=450,
                  track=90, vrate_fpm=0, visible=True)
    assert info_pages(landing, which=("eta", "alt")) == ["landet in ~7 min", "4200ft↓ 210kt"]
    assert info_pages(over, which=("eta", "alt")) == ["FL340 450kt"]
    assert info_pages(over, which=("eta",)) == [""]                 # nichts zu sagen: leere Zeile
    calm = make_cfg(page_seconds=10, flight_seconds=20, info_pages=("eta", "alt"), blink=False)
    r = _renderer(calm)
    m = DisplayModel(source="test", flights=[landing, over])
    t0 = ts(2026, 10, 10, 15, 0) // 40 * 40                        # Beginn eines Durchlaufs
    picks = [(fl.callsign, page) for fl, page in (r._pick(m, t0 + s) for s in range(0, 40, 5))]
    assert picks == [("EZY1", 0), ("EZY1", 0), ("EZY1", 1), ("EZY1", 1),      # 20 s pro Flug, 10 s pro Seite
                     ("EZY2", 0), ("EZY2", 0), ("EZY2", 1), ("EZY2", 1)]
    only_over = DisplayModel(source="test", flights=[over])
    assert {r._pick(only_over, t0 + s)[1] for s in range(0, 40, 5)} == {0}   # eine Seite, steht still


def test_calm_mode_does_not_blink_except_emergencies():
    t = ts(2026, 10, 10, 15, 0)
    m = DisplayModel(source="test", generated_at=t, upcoming=fl(), upcoming_eta_s=40, upcoming_az=150)

    def phases(draw, data, cfg, model=m):
        """Bild im An- und im Aus-Takt (2 s später)."""
        out = []
        for now in (t, t + 2):
            img = Image.new("RGB", (128, 32))
            draw(img, ctx(now, model, cfg=cfg), data, 0, 1.0)
            out.append(img.tobytes())
        return out

    normal, calm = make_cfg(), make_cfg(blink=False)
    nxt = traffic.next_cond(ctx(t, m, cfg=normal)).data       # unter 1 min: Richtung blinkt
    a, b = phases(traffic.next_draw, nxt, normal)
    assert a != b
    a, b = phases(traffic.next_draw, nxt, calm)
    assert a == b
    a, b = phases(traffic.alert_draw, traffic.alert_demo(None), calm)
    assert a != b                                               # ein Notfall blinkt immer


def test_radar_counts_only_planes_landing_here():
    m = DisplayModel(source="test", nearby=[{"az": 100, "elev": 3, "status": "ARR", "seq": None},
                                            {"az": 120, "elev": 2, "status": "ARR", "seq": 1},
                                            {"az": 140, "elev": 9, "status": "OVF", "seq": None}])
    assert traffic.radar_cond(ctx(model=m)).data == {"n": 3, "arr": 1}
