"""Sonne gegen veröffentlichte Zeiten, ISS gegen eine unabhängige Position."""

import math
import os
from datetime import UTC, datetime

from conftest import FIXTURES, make_cfg
from flightpanel.astro import Iss, parse_tle, sun_crossing, sun_position, sun_radec, tle_epoch

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


def _iss():
    return Iss(make_cfg(home_lat=BERLIN[0], home_lon=BERLIN[1]), os.path.join(FIXTURES, "iss.tle"))


def test_iss_position_matches_independent_source():
    iss = _iss()
    lat, lon, h = iss.subpoint(1791654544)          # wheretheiss.at: 50.977, -94.647, 429.97 km
    assert abs(lat - 50.977) < 0.1 and abs(lon + 94.647) < 0.1 and abs(h - 429.97) < 2


def test_iss_passes_and_validity():
    iss = _iss()
    epoch = tle_epoch(iss.tle[0])
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
