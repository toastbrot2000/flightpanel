"""Statistik: Tag, Sammelalbum, Flaute, seltene Flugzeuge, Landerichtung, Datei."""

from datetime import datetime

from conftest import NAMES, make_cfg, state
from flightpanel.model import DisplayModel, Flight
from flightpanel.stats import Stats

RARE = {"types": {"A388": "A380"}, "callsigns": {"GAF": "Luftwaffe"}}


def ts(*a):
    return datetime(*a).timestamp()


def mk(tmp_path, **kw):
    return Stats(str(tmp_path / "stats.json"), make_cfg(), NAMES, RARE)


def fl(icao="v1", visible=True):
    return Flight(icao24=icao, callsign="EZY1", status="ARR", dist_km=5, bearing=150, alt_ft=3000,
                  speed_kt=200, track=250, vrate_fpm=0, visible=visible)


def test_day_counts_and_rollover(tmp_path):
    st = mk(tmp_path)
    t = ts(2026, 10, 10, 12, 0)
    st.observe([state(icao="a", alt=11900, cs="KLM1"), state(icao="b", cs="EZY2"),
                state(icao="g", ground=True)], DisplayModel(flights=[fl()]), t)
    d = st.today()
    assert d["aircraft"] == 2 and d["max_alt_m"] == 11900 and d["max_alt_callsign"] == "KLM1"
    assert d["airlines"] == 2 and d["shown"] == 1           # KLM unbekannt, aber groß (A3)
    st.observe([], DisplayModel(), ts(2026, 10, 11, 7, 0))
    assert st.history["2026-10-10"]["aircraft"] == 2
    assert st.today()["aircraft"] == 0


def test_dex_and_fanfare(tmp_path):
    st = mk(tmp_path)
    t = ts(2026, 10, 10, 12, 0)
    st.since = t - 3600                                    # frisch gestartet: keine Fanfare
    assert st.observe([state(cs="EZY1")], DisplayModel(), t) == ["EZY"]
    assert st.newest(t + 10, 120) is None
    st.since = t - 7 * 3600
    assert st.observe([state(icao="x", cs="DLH9")], DisplayModel(), t + 60) == ["DLH"]
    e = st.newest(t + 100, 120)
    assert e["name"] == "Lufthansa" and e["known"]
    assert st.newest(t + 300, 120) is None
    seen, total, extra = st.dex_count()
    assert (seen, total, extra) == (2, 3, 0)               # Christoph (keine Airline) zählt nicht
    assert st.observe([state(icao="y", cs="CHX31", category="A7")], DisplayModel(), t + 120) == []


def test_quiet_timer_and_gap_reset(tmp_path):
    st = mk(tmp_path)
    t = ts(2026, 10, 10, 12, 0)
    st.observe([], DisplayModel(flights=[fl()]), t)
    st.observe([], DisplayModel(), t + 600)
    assert st.quiet_s(t + 900) == 900
    assert st.today()["quiet_record_s"] == 600
    assert st.quiet_s(t + 600 + 11 * 60) is None            # keine Abrufe mehr: keine Aussage
    st.observe([], DisplayModel(), t + 600 + 3600)          # Lücke (Quiet Hours): Uhr neu
    assert st.quiet_s(t + 600 + 3600 + 60) == 60


def test_rare_sightings(tmp_path):
    st = mk(tmp_path)
    t = ts(2026, 10, 10, 12, 0)
    st.observe([state(icao="r1", cs="UAE45", actype="A388", lat=52.0, lon=14.2, alt=11000),
                state(icao="r2", cs="GAF611", actype="A350")], DisplayModel(), t)
    now = st.rare_now(t + 30)
    by = {e["icao24"]: e for e in now}
    assert by["r1"]["label"] == "A380" and by["r1"]["airline"] == "Emirates" and by["r1"]["kind"] == "type"
    assert by["r1"]["dist_km"] > 60
    assert by["r2"]["label"] == "Luftwaffe" and by["r2"]["kind"] == "gov"
    assert "GAF" not in st.dex                          # Luftwaffe ist selten, keine Airline fürs Album
    assert st.rare_now(t + 600) == []


def test_course_needs_three_polls_to_flip(tmp_path):
    st = mk(tmp_path)
    t = ts(2026, 10, 10, 12, 0)
    st.observe([], DisplayModel(landing_course=249, course_votes=2), t)
    assert st.course == 249 and st.course_since == t
    for i in (1, 2):
        st.observe([], DisplayModel(landing_course=69, course_votes=1), t + i * 20)
        assert st.course == 249
    st.observe([], DisplayModel(landing_course=69, course_votes=1), t + 60)
    assert st.course == 69 and st.prev_course == 249 and st.course_since == t + 60
    st.observe([], DisplayModel(landing_course=249, course_votes=0), t + 80)   # ohne Stimmen: nichts
    assert st.course == 69


def test_save_load_roundtrip_and_broken_file(tmp_path):
    st = mk(tmp_path)
    t = ts(2026, 10, 10, 12, 0)
    st.observe([state(cs="EZY1"), state(icao="r", cs="UAE1", actype="A388")],
               DisplayModel(flights=[fl()], landing_course=249, course_votes=3), t)
    st.save()
    again = mk(tmp_path)
    assert again.today() == st.today() and again.dex.keys() == st.dex.keys()
    assert again.course == 249 and "r" in again.rare
    (tmp_path / "stats.json").write_text("{kaputt", encoding="utf-8")
    broken = mk(tmp_path)
    assert broken.dex == {} and broken.course is None
