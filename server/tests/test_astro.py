"""Sonne gegen veröffentlichte Zeiten, ISS gegen eine unabhängige Position."""

import asyncio
import math
import os
import time
from datetime import UTC, datetime

from conftest import FIXTURES, make_cfg
from flightpanel.astro import TLE_URLS, Iss, Orbit, parse_tle, sun_crossing, sun_day, sun_position, sun_radec, tle_epoch
from flightpanel.db import Db
from flightpanel.issues import Issues

BERLIN = (52.52, 13.405)


def utc(*a):
    return datetime(*a, tzinfo=UTC).timestamp()


def near(t, *expected, tol_min=3):
    return abs(t - utc(*expected)) <= tol_min * 60


def test_sunrise_sunset_berlin():
    # Berlin, 21.06.2024: 04:43 / 21:33 MESZ; 21.12.2024: 08:15 / 15:54 MEZ
    assert near(sun_crossing(utc(2024, 6, 21), utc(2024, 6, 21, 12), *BERLIN, -0.833), 2024, 6, 21, 2, 43)
    assert near(sun_crossing(utc(2024, 6, 21, 12), utc(2024, 6, 22), *BERLIN, -0.833), 2024, 6, 21, 19, 33)
    assert near(sun_crossing(utc(2024, 12, 21), utc(2024, 12, 21, 12), *BERLIN, -0.833), 2024, 12, 21, 7, 15)
    assert near(sun_crossing(utc(2024, 12, 21, 12), utc(2024, 12, 22), *BERLIN, -0.833), 2024, 12, 21, 14, 54)


def test_sun_direction():
    az, el = sun_position(utc(2024, 6, 21, 11, 10), *BERLIN)   # um Mittag im Süden, ~61° hoch
    assert 170 < az < 190 and 60 < el < 62
    # Deklination zum Zeitpunkt der Vergleichsmessung (wheretheiss.at: solar_lat -6.845)
    assert abs(math.degrees(sun_radec(1791654544)[1]) + 6.845) < 0.05


def _tle_text():
    with open(os.path.join(FIXTURES, "iss.tle"), encoding="utf-8") as f:
        return f.read()


def _iss():
    return Iss(make_cfg(home_lat=BERLIN[0], home_lon=BERLIN[1]), Orbit(tle_text=_tle_text()))


def test_iss_position_matches_independent_source():
    iss = _iss()
    lat, lon, h = iss.subpoint(1791654544)          # wheretheiss.at: 50.977, -94.647, 429.97 km
    assert abs(lat - 50.977) < 0.1 and abs(lon + 94.647) < 0.1 and abs(h - 429.97) < 2


def test_iss_passes_and_validity():
    iss = _iss()
    epoch = tle_epoch(iss.orbit.tle[0])
    assert iss.usable(epoch + 86400) and not iss.usable(epoch + 15 * 86400)
    # Diese Woche liegen alle Überflüge im Hellen: sichtbar ist keiner
    assert iss.compute_passes(epoch, hours=24) == []
    # Nur nach Höhe (wie bei Nacht): mehrere Überflüge mit stimmigen Feldern
    iss._visible = lambda t, st: st is not None and st[1] >= 10
    passes = iss.compute_passes(epoch, hours=24)
    assert 3 <= len(passes) <= 8
    for p in passes:
        assert p.start <= p.max_time <= p.end and p.end - p.start < 900 and p.max_elev >= 10


def test_parse_tle_formats():
    l1 = "1 25544U 98067A   26282.95044904  .00006711  00000+0  13074-3 0  9997"
    l2 = "2 25544  51.6315  92.1575 0006672 244.0714 115.9587 15.48795780589546"
    assert parse_tle(f"ISS (ZARYA)\n{l1}\n{l2}\n") == (l1, l2)
    assert parse_tle('{"name": "ISS", "line1": "%s", "line2": "%s"}' % (l1, l2)) == (l1, l2)
    assert parse_tle("Too many requests") is None


def test_tle_lives_in_the_database(tmp_path):
    db = Db(str(tmp_path / "f.db"))
    text = _tle_text()
    orbit = Orbit(db)
    assert orbit.sat is None and orbit.needs_tle(0)
    assert orbit.set_tle_text(text, fetched=1791654544)
    again = Orbit(db)
    assert again.tle == orbit.tle and again.tle_fetched == 1791654544 and not again.needs_tle(1791654544 + 3600)
    assert not again.set_tle_text("Too many requests", fetched=0)


class _Answer:
    """Antwort eines Bahndaten-Dienstes, nur was refresh_tle davon braucht."""

    def __init__(self, status, text):
        self.status, self._text = status, text

    async def text(self):
        return self._text

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class _Session:
    """Die Dienste antworten der Reihe nach, so wie TLE_URLS sie nennt."""

    def __init__(self, *answers):
        self.answers, self.urls = list(answers), []

    def get(self, url, timeout=None):
        self.urls.append(url)
        return self.answers.pop(0)


def test_refresh_tle_stores_the_answer_in_the_database(tmp_path):
    """Die erste Quelle meldet einen HTTP-Fehler, die zweite liefert Bahndaten: sie liegen danach in der Datenbank."""
    db = Db(str(tmp_path / "f.db"))
    session = _Session(_Answer(503, "Service Unavailable"), _Answer(200, _tle_text()))
    orbit = Orbit(db, session=session)
    before = time.time()
    assert asyncio.run(orbit.refresh_tle())
    assert session.urls == TLE_URLS and orbit.last_error is None
    assert before <= orbit.tle_fetched <= time.time() and not orbit.needs_tle(time.time())
    again = Orbit(db)                                                   # nach einem Neustart
    assert again.tle == orbit.tle and again.tle_fetched == orbit.tle_fetched


def test_refresh_tle_failures_are_reported_until_the_next_success(tmp_path):
    """HTTP 429 und eine Antwort ohne Bahnelemente: kein Erfolg, der Fehler steht in last_error und in den
    Auffälligkeiten, in der Datenbank ändert sich nichts. Der nächste Erfolg löscht den Fehler wieder."""
    db = Db(str(tmp_path / "f.db"))
    issues = Issues(db)
    session = _Session(_Answer(429, "Too many requests"), _Answer(200, "Too many requests"))
    orbit = Orbit(db, session=session, issues=issues)
    assert not asyncio.run(orbit.refresh_tle())
    error = "celestrak.org: HTTP 429; tle.ivanstanojevic.me: keine Bahnelemente in der Antwort"
    assert orbit.last_error == error and issues.items["rate_limit:tle"]["example"] == {"error": error}
    assert orbit.sat is None and db.state_get("tle") is None and db.state_get("tle_fetched") is None
    session.answers = [_Answer(200, _tle_text())]
    assert asyncio.run(orbit.refresh_tle()) and orbit.last_error is None and db.state_get("tle") is not None


def test_passes_are_computed_in_a_thread_without_touching_the_database(tmp_path):
    """update_passes ruft compute_passes in einem eigenen Thread auf, die Datenbank gehört aber dem Thread der
    Event-Loop: Griffe compute_passes darauf zu, scheiterte SQLite hier."""
    iss = Iss(make_cfg(home_lat=BERLIN[0], home_lon=BERLIN[1]),
              Orbit(Db(str(tmp_path / "f.db")), tle_text=_tle_text()))
    now = tle_epoch(iss.orbit.tle[0]) + 86400
    asyncio.run(iss.update_passes(now))
    assert iss.passes_at == now


def test_two_windows_share_one_orbit_and_recompute_after_new_data():
    """Bahndaten einmal für alle Panels; Stand und Überflüge rechnet jedes Fenster selbst, und neu, sobald neue
    Bahndaten da sind (auch innerhalb der Stunde)."""
    orbit = Orbit(tle_text=_tle_text())
    berlin = Iss(make_cfg(home_lat=BERLIN[0], home_lon=BERLIN[1]), orbit)
    madrid = Iss(make_cfg(home_lat=40.42, home_lon=-3.70), orbit)
    t = 1791654544
    assert berlin.state(t)[:2] != madrid.state(t)[:2]                 # dieselbe ISS, ein anderer Himmel
    now = tle_epoch(orbit.tle[0]) + 86400
    asyncio.run(berlin.update_passes(now))
    assert berlin.passes_at == now
    asyncio.run(berlin.update_passes(now + 60))                       # innerhalb der Stunde: nichts neu
    assert berlin.passes_at == now
    assert orbit.set_tle_text(_tle_text(), now + 120)                 # neue Bahndaten
    asyncio.run(berlin.update_passes(now + 180))
    assert berlin.passes_at == now + 180


def test_the_sun_cache_has_room_for_many_windows():
    from flightpanel import astro
    astro._SUN_CACHE.clear()
    for i in range(20):
        sun_day(1791654544, 52.0 + i / 100, 13.4)
    assert len(astro._SUN_CACHE) == 20
