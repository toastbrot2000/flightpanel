"""Statistik: Tag, Sammelalbum, Flaute, seltene Flugzeuge, Landerichtung, Datenbank."""

import time
from datetime import datetime, timedelta

import pytest

from conftest import DATA, NAMES, make_cfg, make_db, state
from flightpanel.data import load_airlines, load_rare, rare_match
from flightpanel.model import DisplayModel, Flight
from flightpanel.stats import HISTORY_DAYS, Stats

RARE = {"types": {"A388": "A380"}, "callsigns": {"GAF": "Luftwaffe"}}


def ts(*a):
    return datetime(*a).timestamp()


def mk(tmp_path, db=None):
    return Stats(db or make_db(tmp_path), "home", make_cfg(), NAMES, RARE)


def on(st, date):
    """Der leere Starttag soll `date` sein statt heute: Die Tests spielen in der Vergangenheit, sonst käme
    beim ersten observe() ein Tag mit dem heutigen Datum in die History."""
    st.day["date"] = date
    return st


def fl(icao="v1", visible=True):
    return Flight(icao24=icao, callsign="EZY1", status="ARR", dist_km=5, bearing=150, alt_ft=3000,
                  speed_kt=200, track=250, vrate_fpm=0, visible=visible)


def test_day_counts_and_rollover(tmp_path):
    db = make_db(tmp_path)
    st = mk(tmp_path, db)
    t = ts(2026, 10, 10, 12, 0)
    st.observe([state(icao="a", alt=11900, cs="KLM1"), state(icao="b", cs="EZY2"),
                state(icao="g", ground=True)], DisplayModel(flights=[fl()]), t)
    d = st.today()
    assert d["aircraft"] == 2 and d["max_alt_m"] == 11900 and d["max_alt_callsign"] == "KLM1"
    assert d["airlines"] == 2 and d["shown"] == 1           # KLM unbekannt, aber groß (A3)
    st.observe([], DisplayModel(), ts(2026, 10, 11, 7, 0))
    assert st.history["2026-10-10"]["aircraft"] == 2
    assert st.today()["aircraft"] == 0
    st.save()
    again = mk(tmp_path, db)                                 # Neustart: gestern in der History, heute leer
    assert again.history["2026-10-10"]["aircraft"] == 2 and again.day["date"] == "2026-10-11"


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


def test_extras_join_their_brand_once_listed(tmp_path):
    """Ein Exot, der inzwischen in airlines.json steht, zählt beim nächsten Start zur
    Marke statt doppelt (CXI: adsbdb sagte "Shanxi Airlines", es ist Corendon)."""
    corendon = {"name": "Corendon", "iata": "XC", "airline": True}
    t = ts(2026, 10, 11, 23, 0)
    db = make_db(tmp_path)
    st = Stats(db, "home", make_cfg(), {"CAI": corendon}, RARE)
    st.observe([state(icao="a", cs="CAI98GP"), state(icao="b", cs="CXI97DR"), state(icao="c", cs="ABC123")],
               DisplayModel(), t)
    assert st.dex_count()[2] == 2                                   # CXI und ABC als Exoten
    st.save()
    again = Stats(db, "home", make_cfg(), {"CAI": corendon, "CXI": corendon}, RARE)
    assert set(again.dex) == {"Corendon", "ABC"} and again.dex_count()[2] == 1
    again.save()
    assert {r["brand"] for r in db.query("SELECT brand FROM album")} == {"Corendon", "ABC"}     # CXI-Zeile weg
    assert set(Stats(db, "home", make_cfg(), {"CAI": corendon, "CXI": corendon}, RARE).dex) == {"Corendon", "ABC"}
    solo = make_db(tmp_path / "solo")                               # Marke noch nicht im Album
    st = Stats(solo, "home", make_cfg(), {}, RARE)
    st.observe([state(icao="b", cs="CXI97DR")], DisplayModel(), t)
    st.save()
    later = Stats(solo, "home", make_cfg(), {"CXI": corendon}, RARE)
    assert later.dex["Corendon"]["known"] and later.dex["Corendon"]["first"] == t


def test_save_load_roundtrip_and_broken_file(tmp_path):
    db = make_db(tmp_path)
    st = mk(tmp_path, db)
    t = ts(2026, 10, 10, 12, 0)
    st.observe([state(cs="EZY1"), state(icao="r", cs="UAE1", actype="A388")],
               DisplayModel(flights=[fl()], landing_course=249, course_votes=3), t)
    st.save()
    again = mk(tmp_path, db)
    assert again.today() == st.today() and again.dex.keys() == st.dex.keys() and again.day["shown"] == {"v1"}
    assert again.course == 249 and "r" in again.rare and again.since == st.since
    db.close()
    (tmp_path / "flightpanel.db").write_text("{kaputt", encoding="utf-8")
    broken = mk(tmp_path)                                            # kaputte Datei: beiseite, leer
    assert broken.dex == {} and broken.course is None


def test_high_visits_come_from_airlines_json():
    """Hoher Besuch: Militär und Regierungsflug aus airlines.json, dazu die Sonderfälle
    aus rare.json; Polizei und Rettung nicht."""
    rare = load_rare(DATA, load_airlines(DATA))
    cs = rare["callsigns"]
    assert cs["GAF"] == "Luftwaffe" and cs["KRH"] == "UK Royal VIP" and cs["QAF"] == "Katar"
    assert cs["AF1"] == "Air Force One" and cs["SAM"] == "US-Regierung"
    assert "BPO" not in cs and "CHX" not in cs and "DLH" not in cs
    assert rare_match(rare, "QAF1", "A319") == ("Katar", "gov")
    assert rare_match(rare, "UAE45", "A388") == ("A380", "type")
    assert rare_match(rare, "DLH4AB", "A320") == ("", "")


def test_small_operators_are_exotics_not_album(tmp_path):
    """album false (CPH AirTaxi): Name ja, aber nicht in "Nr. x von y"; nur mit großem
    Flugzeug ein Exot. Ein alter Album-Eintrag wird beim Laden zum Exoten."""
    names = {**NAMES, "CAT": {"name": "CPH AirTaxi", "iata": "", "airline": True, "album": False}}
    db = make_db(tmp_path)
    st = Stats(db, "home", make_cfg(), names, RARE)
    t = ts(2026, 10, 10, 12, 0)
    st.since = t - 7 * 3600
    assert st.observe([state(icao="s", cs="CAT12", category="A1")], DisplayModel(), t) == []    # klein: gar nicht
    assert st.observe([state(icao="b", cs="CAT13", category="A3")], DisplayModel(), t + 60) == ["CAT"]
    e = st.newest(t + 70, 120)
    assert e["name"] == "CPH AirTaxi" and not e["known"]
    assert st.dex_count() == (0, 3, 1)
    st.dex["CPH AirTaxi"]["known"] = True                  # Stand von vorher
    st.dex["Lufthansa"] = {"first": t, "code": "DLH", "name": "Lufthansa", "known": True}
    st.save()
    again = Stats(db, "home", make_cfg(), names, RARE)
    assert again.dex_count() == (1, 3, 1)


def test_old_stats_json_is_taken_over(tmp_path):
    """Der laufende Tag der alten Datei ist heute (der Server lief bis eben), die History älter."""
    t = time.time()
    today = time.strftime("%Y-%m-%d", time.localtime(t))
    raw = {"since": t - 86400,
           "dex": {"easyJet": {"first": t - 3600, "code": "EZY", "name": "easyJet", "known": True,
                               "callsign": "EZY1", "fanfare": False, "last": t}},
           "day": {"date": today, "aircraft": ["a", "b"], "airlines": ["easyJet"], "shown": ["a"],
                   "max_alt_m": 9000, "max_alt_callsign": "EZY1", "quiet_record_s": 300},
           "history": {"2026-10-09": {"aircraft": 5, "airlines": 2, "shown": 1, "max_alt_m": 1, "quiet_record_s": 0}},
           "last_obs": t, "last_in_view": t - 60,
           "rare": {"r1": {"first": t, "last": t, "label": "A380", "kind": "type"}},
           "course": {"now": 249, "prev": 69, "since": t - 7200, "seen": t}}
    db = make_db(tmp_path)
    st = mk(tmp_path, db)
    st.load_legacy(raw)
    st.save()
    again = mk(tmp_path, db)
    assert again.since == t - 86400 and "easyJet" in again.dex and again.course == 249 and again.prev_course == 69
    assert again.history["2026-10-09"]["aircraft"] == 5 and "r1" in again.rare and again.last_in_view == t - 60
    assert again.day["date"] == today and again.day["aircraft"] == {"a", "b"} and again.today()["shown"] == 1
    old = mk(tmp_path / "old", make_db(tmp_path / "old"))             # Server lag Tage still: alles History
    old.load_legacy({**raw, "day": {**raw["day"], "date": "2026-10-10"}})
    old.save()
    later = mk(tmp_path / "old", make_db(tmp_path / "old"))
    assert later.history["2026-10-10"]["aircraft"] == 2 and later.day["date"] == today
    assert later.today()["aircraft"] == 0


def test_a_failed_save_is_written_next_time(tmp_path):
    """App.save() schreibt in einer Transaktion; scheitert sie, ist beim nächsten Lauf nichts verloren."""
    db = make_db(tmp_path)
    st = mk(tmp_path, db)
    st.observe([state(icao="a", cs="EZY1")], DisplayModel(), ts(2026, 10, 10, 12, 0))
    st.observe([], DisplayModel(), ts(2026, 10, 11, 7, 0))            # der 10. ist abgeschlossen
    with pytest.raises(RuntimeError):
        with db.transaction():
            st.save()
            raise RuntimeError("Platte voll")
    assert mk(tmp_path, db).history == {}                               # nichts geschrieben
    st.save()
    assert mk(tmp_path, db).history["2026-10-10"]["aircraft"] == 1     # beim nächsten Mal alles da


# ------------------------------------------------- zusätzlich: Randfälle der Datenbank --
def test_album_and_day_survive_the_database_unchanged(tmp_path):
    """Das Album ist das Wertvollste: Jeder Eintrag kommt so zurück, wie er geschrieben wurde, auch wenn
    ein zweites Speichern die Zeilen nur aktualisiert."""
    db = make_db(tmp_path)
    st = on(mk(tmp_path, db), "2026-10-10")
    st.since = ts(2026, 10, 1, 0, 0)                                  # lange gelaufen: Neues bekommt Fanfare
    seen = [state(cs="EZY1"), state(icao="x", cs="ABC9"), state(icao="r", cs="UAE1", actype="A388")]
    st.observe(seen, DisplayModel(flights=[fl()], landing_course=249, course_votes=3), ts(2026, 10, 10, 12, 0))
    st.save()
    st.set_name("ABC", "Abc Air")
    st.observe(seen[:1], DisplayModel(landing_course=69, course_votes=1), ts(2026, 10, 10, 12, 1))
    st.save()
    again = mk(tmp_path, db)
    assert st.rare and again.dex == st.dex
    assert again.dex["easyJet"]["fanfare"] is True and again.dex["ABC"]["name"] == "Abc Air"
    assert again.dex["easyJet"]["first"] == ts(2026, 10, 10, 12, 0)
    assert again.dex["easyJet"]["last"] == ts(2026, 10, 10, 12, 1)
    assert again.day == st.day and again.history == st.history and again.rare == st.rare
    assert (again.since, again.last_obs, again.last_in_view) == (st.since, st.last_obs, st.last_in_view)
    assert again.course_seen == ts(2026, 10, 10, 12, 1) != again.course_since
    assert (again.course, again.prev_course, again.course_since) == (st.course, st.prev_course, st.course_since)


def test_rare_sightings_expire_in_the_database_too(tmp_path):
    db = make_db(tmp_path)
    st = on(mk(tmp_path, db), "2026-10-10")
    t = ts(2026, 10, 10, 12, 0)
    st.observe([state(icao="r1", cs="UAE45", actype="A388")], DisplayModel(), t)
    st.save()
    assert mk(tmp_path, db).rare.keys() == {"r1"}
    st.observe([state(icao="r2", cs="UAE46", actype="A388")], DisplayModel(), t + 25 * 3600)   # r1 ist vergessen
    st.save()
    assert mk(tmp_path, db).rare.keys() == {"r2"} and db.one("SELECT COUNT(*) AS n FROM rare")["n"] == 1


def test_a_restart_in_the_middle_of_the_day_continues_the_day(tmp_path):
    db = make_db(tmp_path)
    st = on(mk(tmp_path, db), "2026-10-10")
    st.observe([state(icao="a", alt=9000, cs="EZY1")], DisplayModel(flights=[fl("v1")]), ts(2026, 10, 10, 9, 0))
    st.save()
    again = mk(tmp_path, db)                                          # Neustart um 12 Uhr, derselbe Tag
    again.observe([state(icao="a", alt=11000, cs="DLH2"), state(icao="b", cs="UAE3")],
                  DisplayModel(flights=[fl("v2")]), ts(2026, 10, 10, 12, 0))
    again.save()
    third = mk(tmp_path, db)
    assert third.day["date"] == "2026-10-10" and third.history == {}
    assert third.day["aircraft"] == {"a", "b"} and third.day["shown"] == {"v1", "v2"}
    assert third.day["airlines"] == {"easyJet", "Lufthansa", "Emirates"}
    assert third.today()["max_alt_m"] == 11000 and third.today()["max_alt_callsign"] == "DLH2"
    assert db.one("SELECT COUNT(*) AS n FROM days")["n"] == 1         # eine Zeile für den Tag, nicht eine je Speichern


def test_a_finished_day_with_a_later_date_does_not_hide_the_running_day(tmp_path):
    """Ging die Uhr zurück (Start ohne Zeit, bis die Zeitabfrage durch ist), steht ein abgeschlossener Tag
    hinter dem laufenden. Der laufende bleibt trotzdem der laufende."""
    db = make_db(tmp_path)
    st = on(mk(tmp_path, db), "2026-10-12")
    st.observe([state(icao="a")], DisplayModel(), ts(2026, 10, 12, 12, 0))
    st.observe([state(icao="b")], DisplayModel(), ts(2026, 10, 11, 12, 0))      # Uhr zurück: der 12. ist abgeschlossen
    st.save()
    again = mk(tmp_path, db)
    assert again.day["date"] == "2026-10-11" and again.day["aircraft"] == {"b"}
    assert again.history["2026-10-12"]["aircraft"] == 1


@pytest.mark.parametrize("save_every", [1, 7, 1000])
def test_history_is_cut_after_sixty_days_in_the_database_too(tmp_path, save_every):
    """Egal, wie oft gespeichert wird (nach einer Pause kommen mehrere Tage auf einmal dran):
    Es bleiben HISTORY_DAYS Tage und der laufende, die Zeilen der älteren sind weg."""
    db = make_db(tmp_path)
    st = on(mk(tmp_path, db), "2026-07-01")
    first = datetime(2026, 7, 1, 12, 0)
    days = HISTORY_DAYS + 3
    for i in range(days):
        st.observe([state(icao=f"a{i}")], DisplayModel(), (first + timedelta(days=i)).timestamp())
        if i % save_every == 0:
            st.save()
    st.save()
    rows = db.query("SELECT date, ids FROM days ORDER BY date")
    assert len(rows) == HISTORY_DAYS + 1 and rows[-1]["date"] == st.day["date"] and rows[-1]["ids"]
    assert all(r["ids"] is None for r in rows[:-1])
    again = mk(tmp_path, db)
    assert again.history == st.history and len(again.history) == HISTORY_DAYS
    assert min(again.history) == (first + timedelta(days=days - 1 - HISTORY_DAYS)).strftime("%Y-%m-%d")


def test_a_failed_save_still_deletes_next_time(tmp_path):
    """Auch die Löschungen (Exot zur Marke gezogen, Tag aus der History gefallen) bleiben bei
    einem ROLLBACK vorgemerkt."""
    corendon = {"name": "Corendon", "iata": "XC", "airline": True}
    db = make_db(tmp_path)
    first = datetime(2026, 7, 1, 12, 0)
    old = on(Stats(db, "home", make_cfg(), {"CAI": corendon}, RARE), "2026-07-01")
    for i in range(HISTORY_DAYS + 1):                                 # 60 Tage History, der 61. läuft
        old.observe([state(icao="b", cs="CXI97DR")], DisplayModel(), (first + timedelta(days=i)).timestamp())
    old.save()
    finished = "SELECT date FROM days WHERE ids IS NULL ORDER BY date"
    brands = "SELECT brand FROM album"
    assert len(db.query(finished)) == HISTORY_DAYS and {r["brand"] for r in db.query(brands)} == {"CXI"}
    st = Stats(db, "home", make_cfg(), {"CAI": corendon, "CXI": corendon}, RARE)      # CXI gehört jetzt zu Corendon
    st.observe([], DisplayModel(), (first + timedelta(days=HISTORY_DAYS + 1)).timestamp())    # ältester Tag fällt raus
    with pytest.raises(RuntimeError):
        with db.transaction():
            st.save()
            raise RuntimeError("Platte voll")
    assert {r["brand"] for r in db.query(brands)} == {"CXI"}
    assert db.query(finished)[0]["date"] == first.strftime("%Y-%m-%d") and len(db.query(finished)) == HISTORY_DAYS
    st.save()
    assert {r["brand"] for r in db.query(brands)} == {"Corendon"}
    rows = db.query(finished)
    assert len(rows) == HISTORY_DAYS and rows[0]["date"] == (first + timedelta(days=1)).strftime("%Y-%m-%d")
    assert mk(tmp_path, db).history.keys() == st.history.keys()


def test_old_stats_json_with_gaps_is_taken_over(tmp_path):
    """Einträge ohne manche Felder (ältere Fassungen) dürfen weder die Übernahme noch jedes spätere
    Speichern brechen."""
    t = ts(2026, 10, 10, 12, 0)
    db = make_db(tmp_path)
    st = mk(tmp_path, db)
    st.load_legacy({"dex": {"easyJet": {"first": t, "code": "EZY", "name": "easyJet", "known": True}},
                    "history": {"2026-10-08": {"aircraft": 4}, "2026-10-09": {"airlines": 2, "max_alt_m": 7000}}})
    st.save()
    again = mk(tmp_path, db)
    assert again.history["2026-10-08"] == {"aircraft": 4, "airlines": 0, "shown": 0, "max_alt_m": 0,
                                           "quiet_record_s": 0}
    assert again.history["2026-10-09"] == {"aircraft": 0, "airlines": 2, "shown": 0, "max_alt_m": 7000,
                                           "quiet_record_s": 0}
    e = again.dex["easyJet"]
    assert e["first"] == e["last"] == t and e["callsign"] == "" and e["fanfare"] is False
    assert not st.dirty                                                # und gespeichert ist gespeichert


def test_old_stats_json_that_is_not_complete_keeps_what_it_can(tmp_path):
    """Ein kaputter Teil (hier history) nimmt den Rest nicht mit; gespeichert wird trotzdem."""
    db = make_db(tmp_path)
    st = mk(tmp_path, db)
    t = ts(2026, 10, 10, 12, 0)
    st.load_legacy({"since": t - 3600, "history": "kaputt",
                    "dex": {"easyJet": {"first": t, "code": "EZY", "name": "easyJet", "known": True}}})
    st.save()
    assert "easyJet" in mk(tmp_path, db).dex
