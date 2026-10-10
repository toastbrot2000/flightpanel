"""Bedingungen der Karten (wann sie etwas zu sagen haben) und das Rendern."""

from datetime import datetime, timezone

import pytest
from PIL import Image

from conftest import DATA, NAMES, make_cfg, state
from flightpanel.cards import album, fun, runway, sky, traffic
from flightpanel.astro import Pass, sun_day
from flightpanel.cards import CARDS
from flightpanel.data import load_fun
from flightpanel.idle import Ctx, IdleScreen
from flightpanel.logos import make_logo_store
from flightpanel.metar import MetarSource, parse
from flightpanel.model import DisplayModel, Flight
from flightpanel.render import Renderer
from flightpanel.stats import Stats


def ts(*a):
    return datetime(*a).timestamp()


def fl(kind="airline", icao="f1", visible=True):
    return Flight(icao24=icao, callsign="DEFGH" if kind == "other" else "EZY1", status="OVF", dist_km=5,
                  bearing=150, alt_ft=2500, speed_kt=100, track=250, vrate_fpm=0, kind=kind, visible=visible)


def ctx(now=None, model=None, cfg=None, **kw):
    return Ctx(cfg=cfg or make_cfg(), model=model or DisplayModel(source="test"),
               now=now if now is not None else ts(2026, 10, 10, 15, 0), names=NAMES, **kw)


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
    stamp = datetime.fromtimestamp(t, timezone.utc).strftime("%d%H%MZ")
    assert runway.weather_cond(ctx(t, near, metar=metar_src(f"EDDB {stamp} 21007KT CAVOK 13/05 Q1007", t))) is None
    h = runway.weather_cond(ctx(t, near, metar=metar_src(f"EDDB {stamp} 21007KT 9999 BKN012 13/05 Q1007", t)))
    assert h.data["kind"] == "cloud" and h.data["above"] == 1
    h = runway.weather_cond(ctx(t, near, metar=metar_src(f"EDDB {stamp} 00000KT 0400 FG VV002 03/03 Q1021", t)))
    assert h.data["kind"] == "fog"
    h = runway.weather_cond(ctx(t, near, metar=metar_src(f"EDDB {stamp} 27012KT 9999 TSRA BKN030CB 21/15 Q1008", t)))
    assert h.data["kind"] == "ts"
    old = datetime.fromtimestamp(t - 3 * 3600, timezone.utc).strftime("%d%H%MZ")
    stale_fog = metar_src(f"EDDB {old} 00000KT 0400 FG VV002 03/03 Q1021", t)
    assert runway.weather_cond(ctx(t, near, metar=stale_fog)) is None
    assert runway.weather_cond(ctx(t, near, metar=None)) is None


def _stats(tmp_path, course=249, since_min=60, seen_min=5, prev=None, now=None):
    st = Stats(str(tmp_path / "s.json"), make_cfg(), NAMES, {"types": {}, "callsigns": {}})
    st.course, st.prev_course = course, prev
    st.course_since = now - since_min * 60
    st.course_seen = now - seen_min * 60
    return st


def test_runway_card_only_when_stable(tmp_path):
    t = ts(2026, 10, 10, 15, 0)
    stamp = datetime.fromtimestamp(t, timezone.utc).strftime("%d%H%MZ")
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
    assert fun.fireworks_cond(ctx(ts(2026, 12, 31, 23, 30))) is not None
    assert fun.fireworks_cond(ctx(ts(2027, 1, 1, 1, 0))) is not None
    assert fun.fireworks_cond(ctx(ts(2027, 1, 1, 3, 0))) is None


def test_sunset_and_sunrise_windows():
    c = make_cfg()
    sd = sun_day(ts(2026, 10, 10, 12, 0), c.home_lat, c.home_lon)
    assert sky.sunset_cond(ctx(sd["sunset"] - 3600, cfg=c)) is not None
    assert sky.sunset_cond(ctx(sd["sunset"] - 3 * 3600, cfg=c)) is None
    assert sky.sunrise_cond(ctx(sd["sunrise"] - 1800, cfg=c)) is not None
    assert sky.sunrise_cond(ctx(sd["sunrise"] + 600, cfg=c)) is None


def test_jokes_prefer_todays_date():
    jokes = load_fun(DATA, "BER")
    h = fun.joke_cond(ctx(ts(2026, 10, 31, 15, 0), fun=jokes))
    assert h.weight == 4 and "BER wird" in h.data["lines"][0]
    assert not fun.joke_cond(ctx(ts(2026, 10, 10, 15, 0), fun=jokes)).data.get("dates")
    assert fun.joke_cond(ctx(fun={})) is None and fun.delay_cond(ctx(fun={})) is None
    assert load_fun(DATA, "MUC") == {}                      # Berliner Witze nur am BER


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
    return Renderer(cfg, logos, idle=IdleScreen(cfg, CARDS, logos=logos, names=NAMES, fun=load_fun(DATA, "BER")))


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
    alert = {"callsign": "DLH4AB", "squawk": "7700", "meaning": "NOTFALL", "type": "A20N", "dist_km": 9.0,
             "az": 120, "alt_ft": 3000, "in_view": False}
    r.render(DisplayModel(source="test", flights=[fl()], alert=alert), now)
    assert r.idle.last_pick == "alert"                      # Notfall vor allem
