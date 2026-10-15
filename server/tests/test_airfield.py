"""Flughafen über Abrufe hinweg, für alle Panels gleich: Landerichtung (ein Wechsel gilt erst nach drei Abrufen),
wer zuletzt landete oder startete, letzte Bewegung am Platz. Die Landerichtung übersteht Neustarts."""

from datetime import datetime

import pytest

from conftest import make_db, state
from flightpanel.airfield import Airfield
from flightpanel.model import DisplayModel


def ts(*a):
    return datetime(*a).timestamp()


def test_course_needs_three_polls_to_flip(tmp_path):
    af = Airfield(make_db(tmp_path))
    t = ts(2026, 10, 10, 12, 0)
    af.observe(DisplayModel(landing_course=249, course_votes=2), t)
    assert af.course == 249 and af.course_since == t
    for i in (1, 2):
        af.observe(DisplayModel(landing_course=69, course_votes=1), t + i * 20)
        assert af.course == 249
    af.observe(DisplayModel(landing_course=69, course_votes=1), t + 60)
    assert af.course == 69 and af.prev_course == 249 and af.course_since == t + 60
    af.observe(DisplayModel(landing_course=249, course_votes=0), t + 80)   # ohne Stimmen: nichts
    assert af.course == 69


def test_the_last_course_is_the_next_start_value_and_landings_count_as_moves(tmp_path):
    af = Airfield(make_db(tmp_path))
    assert af.landing_course is None and af.last_move == 0
    t = ts(2026, 10, 10, 12, 0)
    landing = {"icao24": "3c56ec", "callsign": "EWG9SK", "eta_s": 60}
    af.observe(DisplayModel(landing_course=69, course_votes=0, arrivals=[landing], generated_at=t), t)
    assert af.landing_course == 69                       # Startwert des nächsten Abrufs, auch ohne Stimmen
    assert af.last_move == t                             # setzt gleich auf: Bewegung am Platz
    assert af.recent.hint(state(icao="3c56ec", cs="EWG9SK")) == "arr"
    af.observe(DisplayModel(landing_course=69, arrivals=[{**landing, "eta_s": 600}], generated_at=t + 20), t + 20)
    assert af.last_move == t                             # noch zehn Minuten: keine Bewegung


def test_the_course_survives_a_restart_and_a_hand_edited_one_is_ignored(tmp_path, caplog):
    db = make_db(tmp_path)
    af = Airfield(db)
    t = ts(2026, 10, 10, 12, 0)
    af.observe(DisplayModel(landing_course=249, course_votes=3), t)
    af.observe(DisplayModel(landing_course=69, course_votes=1), t + 60)
    af.save()
    again = Airfield(db)
    assert (again.course, again.prev_course, again.course_since, again.course_seen) == (249, None, t, t + 60)
    assert again.landing_course == 249                   # nach einem Neustart: die bestätigte als Startwert
    db.state_set("course", {"now": "249"})               # von Hand verändert
    with caplog.at_level("WARNING"):
        broken = Airfield(db)
    assert broken.course is None and "Landerichtung" in caplog.text


def test_a_failed_save_is_written_next_time(tmp_path):
    db = make_db(tmp_path)
    af = Airfield(db)
    af.observe(DisplayModel(landing_course=249, course_votes=3), ts(2026, 10, 10, 12, 0))
    with pytest.raises(RuntimeError):
        with db.transaction():
            af.save()
            raise RuntimeError("Platte voll")
    assert Airfield(db).course is None
    af.save()
    assert Airfield(db).course == 249


def test_the_course_of_an_old_stats_json_is_taken_over(tmp_path, caplog):
    t = ts(2026, 10, 10, 12, 0)
    db = make_db(tmp_path)
    af = Airfield(db)
    af.load_legacy({"dex": {}, "course": {"now": 249, "prev": 69, "since": t - 7200, "seen": t}})
    assert af.landing_course == 249
    af.save()
    again = Airfield(db)
    assert (again.course, again.prev_course, again.course_since, again.course_seen) == (249, 69, t - 7200, t)
    odd = Airfield(make_db(tmp_path / "odd"))
    with caplog.at_level("WARNING"):
        odd.load_legacy({"course": {"now": "249", "prev": 69}})
    assert odd.course is None and "Landerichtung" in caplog.text
    odd.load_legacy({"dex": {}})                          # ohne Landerichtung: nichts zu tun
    assert odd.course is None and not odd.dirty


def test_report_shows_the_course_like_stats_did(tmp_path):
    af = Airfield(make_db(tmp_path))
    assert af.report() == {"now": None, "prev": None, "since": None}
    af.observe(DisplayModel(landing_course=249, course_votes=2), ts(2026, 10, 10, 12, 0))
    assert af.report() == {"now": 249, "prev": None, "since": "2026-10-10 12:00"}


def test_each_airport_has_its_own_course_and_the_old_key_is_taken_over_once(tmp_path):
    """Regionen: je Flughafen ein Airfield mit Schlüssel course:<IATA>; der alte Schlüssel "course" (ein Flughafen
    für alle) wird einmalig für den Flughafen des Servers übernommen und beim Speichern unter dem neuen abgelegt."""
    db = make_db(tmp_path)
    t = ts(2026, 10, 10, 12, 0)
    db.state_set("course", {"now": 249, "prev": None, "since": t, "seen": t})
    ber = Airfield(db, "BER", legacy=True)
    assert ber.course == 249 and ber.key == "course:BER" and ber.dirty            # übernommen, noch nicht geschrieben
    fmm = Airfield(db, "FMM", legacy=False)
    assert fmm.course is None and fmm.key == "course:FMM"
    fmm.observe(DisplayModel(landing_course=240, course_votes=2), t)
    with db.transaction():
        ber.save()
        fmm.save()
    assert db.state_get("course:BER")["now"] == 249 and db.state_get("course:FMM")["now"] == 240
    assert Airfield(db, "BER", legacy=True).course == 249 and Airfield(db, "FMM").course == 240
    db.state_set("course", {"now": 69, "prev": None, "since": t, "seen": t})       # alt geändert: zählt nicht mehr
    assert Airfield(db, "BER", legacy=True).course == 249
    assert Airfield(db).key == "course"                                            # ohne Flughafen wie bisher
