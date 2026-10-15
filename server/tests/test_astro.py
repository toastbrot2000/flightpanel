"""Sonne gegen veröffentlichte Zeiten, ISS gegen eine unabhängige Position."""

import asyncio
import math
import os
import time
from datetime import UTC, datetime

from conftest import FIXTURES, make_cfg
from flightpanel.astro import (
    TLE_URLS,
    Catalog,
    Iss,
    Orbit,
    parse_tle,
    sun_crossing,
    sun_day,
    sun_position,
    sun_radec,
    tle_epoch,
)
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
    cat = Catalog(db)
    assert not cat.orbits and cat.needs_tle(0)
    assert cat.set_tle_text(text, fetched=1791654544)
    again = Catalog(db)
    assert again.get(25544).tle == cat.get(25544).tle and again.fetched == 1791654544
    assert not again.needs_tle(1791654544 + 3600)
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
    cat = Catalog(db, session=session)
    before = time.time()
    assert asyncio.run(cat.refresh())
    assert session.urls == TLE_URLS and "HTTP 503" in cat.last_error    # Teilerfolg: die Ersatzquelle brachte die ISS
    assert before <= cat.fetched <= time.time() and not cat.needs_tle(time.time())
    again = Catalog(db)                                                 # nach einem Neustart
    assert again.get(25544).tle == cat.get(25544).tle and again.fetched == cat.fetched


def test_refresh_tle_failures_are_reported_until_the_next_success(tmp_path):
    """HTTP 429 und eine Antwort ohne Bahnelemente: kein Erfolg, der Fehler steht in last_error und in den
    Auffälligkeiten, in der Datenbank ändert sich nichts. Der nächste Erfolg löscht den Fehler wieder."""
    db = Db(str(tmp_path / "f.db"))
    issues = Issues(db)
    session = _Session(_Answer(429, "Too many requests"), _Answer(200, "Too many requests"))
    cat = Catalog(db, session=session, issues=issues)
    assert not asyncio.run(cat.refresh())
    error = "celestrak.org: HTTP 429; tle.ivanstanojevic.me: keine Bahnelemente in der Antwort"
    assert cat.last_error == error and issues.items["rate_limit:tle"]["example"] == {"error": error}
    assert not cat.orbits and db.state_get("tle_sats") is None
    session.answers = [_Answer(200, _stations_text())]
    assert asyncio.run(cat.refresh()) and cat.last_error is None and db.state_get("tle_sats") is not None


def test_passes_are_computed_in_a_thread_without_touching_the_database(tmp_path):
    """update_passes ruft compute_passes in einem eigenen Thread auf, die Datenbank gehört aber dem Thread der
    Event-Loop: Griffe compute_passes darauf zu, scheiterte SQLite hier."""
    iss = Iss(make_cfg(home_lat=BERLIN[0], home_lon=BERLIN[1]), Orbit(tle_text=_tle_text()))
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


# ------------------------------------------------------------------- Katalog: mehrere Satelliten --
def _stations_text():
    with open(os.path.join(FIXTURES, "stations.tle"), encoding="utf-8") as f:
        return f.read()


def test_parse_tles_reads_a_celestrak_group_and_plain_pairs():
    from flightpanel.astro import norad_of, parse_tles
    sats = parse_tles(_stations_text())
    assert [s[0] for s in sats] == ["ISS (ZARYA)", "CSS (TIANHE)"]
    assert [norad_of(s[2]) for s in sats] == [25544, 48274]
    l1, l2 = parse_tle(_tle_text())
    assert parse_tles(_tle_text()) == [("ISS (ZARYA)", l1, l2)]
    assert parse_tles(f"{l1}\n{l2}\n") == [("", l1, l2)]                            # zwei Zeilen ohne Namen
    assert parse_tles("Too many requests") == []


def test_the_catalog_keeps_iss_and_tiangong_and_seeds_from_the_old_key(tmp_path):
    """Die Bahndaten aller Satelliten liegen unter tle_sats; der alte Schlüssel tle (nur die ISS) sät den Katalog
    einmalig, sodass die ISS über das Update hinweg läuft, auch ohne Netz."""
    from flightpanel.astro import Catalog
    db = Db(str(tmp_path / "f.db"))
    db.state_set("tle", _tle_text())
    db.state_set("tle_fetched", 1791654544)
    cat = Catalog(db)
    assert set(cat.orbits) == {25544} and cat.fetched == 1791654544 and cat.get(25544).usable(1791654544)
    cat.session = _Session(_Answer(200, _stations_text()))
    assert asyncio.run(cat.refresh()) and cat.last_error is None
    assert set(cat.orbits) == {25544, 48274} and cat.get(48274).name == "CSS (TIANHE)"
    assert set(db.state_get("tle_sats")["sats"]) == {"25544", "48274"}
    again = Catalog(db)
    assert set(again.orbits) == {25544, 48274} and not again.needs_tle(time.time())


def test_a_failing_source_is_reported_and_the_others_still_count(tmp_path):
    from flightpanel.astro import Catalog
    db = Db(str(tmp_path / "f.db"))
    issues = Issues(db)
    cat = Catalog(db, session=_Session(_Answer(429, "Too many requests"), _Answer(200, _tle_text())), issues=issues)
    assert asyncio.run(cat.refresh())                                     # stations weg, die ISS-Ersatzquelle da
    assert set(cat.orbits) == {25544} and "celestrak.org: HTTP 429" in cat.last_error
    assert "rate_limit:tle" in issues.items
    cat.session = _Session(_Answer(503, "x"), _Answer(200, "Too many requests"))
    assert not asyncio.run(cat.refresh()) and "HTTP 503" in cat.last_error and set(cat.orbits) == {25544}


def test_tiangong_orbit_is_plausible():
    """Ohne Fremdquelle: Breite gipfelt bei der Inklination (41,47°), Höhe 370-400 km, Umlauf 92-93 min."""
    from flightpanel.astro import Orbit, Tracker, parse_tles
    name, l1, l2 = next(s for s in parse_tles(_stations_text()) if "TIANHE" in s[0])
    tr = Tracker(make_cfg(home_lat=48.09, home_lon=9.83), Orbit(name, (l1, l2)))
    epoch = tle_epoch(l1)
    pts = [tr.subpoint(epoch + i * 10) for i in range(0, 200 * 6)]
    assert 41.3 < max(p[0] for p in pts) < 41.7 and 370 < min(p[2] for p in pts) and max(p[2] for p in pts) < 400
    peaks = [i for i in range(1, len(pts) - 1) if pts[i][0] > pts[i - 1][0] and pts[i][0] >= pts[i + 1][0]
             and pts[i][0] > 41]
    assert len(peaks) >= 2 and 91 * 60 <= (peaks[1] - peaks[0]) * 10 <= 94 * 60


# --------------------------------------------------------------------------- Starlink-Ketten --
def _batch_text():
    with open(os.path.join(FIXTURES, "starlink-batch.tle"), encoding="utf-8") as f:
        return f.read()


def _checksum(line: str) -> str:
    total = sum(int(c) if c.isdigit() else 1 if c == "-" else 0 for c in line[:68])
    return line[:68] + str(total % 10)


def _string(name_l1_l2, n: int, step_deg: float, year="26", launch="160"):
    """Eine synthetische Kette: n Kopien eines Satelliten, in der mittleren Anomalie je step_deg versetzt, als
    neuester Start des Jahres (Bezeichnung YYNNN), NORAD-Nummern fortlaufend."""
    _name, l1, l2 = name_l1_l2
    out = []
    for i in range(n):
        ma = (float(l2[43:51]) + i * step_deg) % 360
        norad = f"{70100 + i:05d}"
        a = _checksum(l1[:2] + norad + l1[7:9] + f"{year}{launch}{chr(65 + i % 26)}  " + l1[17:])
        b = _checksum(l2[:2] + norad + l2[7:43] + f"{ma:8.4f}" + l2[51:])
        out.append((f"STARLINK-9{i:04d}", a, b))
    return out


def test_find_trains_takes_a_tight_fresh_string_and_leaves_a_dispersed_batch():
    from flightpanel.astro import TRAIN_MAX_SPAN_DEG, find_trains, parse_tles
    batch = parse_tles(_batch_text())                      # ein echter Start, nach Tagen schon weit verteilt
    assert len(batch) >= 20 and all(n.startswith("STARLINK") for n, _, _ in batch)
    t = tle_epoch(batch[0][1])
    assert find_trains(batch, t, year=2026) == []
    tight = _string(batch[0], 22, 1.5)                     # 22 Satelliten über 31°: eine Kette
    trains = find_trains(batch + tight, t, year=2026)
    assert len(trains) == 1 and trains[0].n == 22 and trains[0].key == "26160" and trains[0].span < TRAIN_MAX_SPAN_DEG
    assert trains[0].name in ("STARLINK-90010", "STARLINK-90011")      # der in der Mitte der Kette
    assert find_trains(_string(batch[0], 22, 4.0), t, year=2026) == []      # 84°: zu weit auseinander
    assert find_trains(_string(batch[0], 8, 1.5), t, year=2026) == []       # zu wenige
    assert find_trains(_string(batch[0], 22, 1.5, year="24"), t, year=2026) == []   # alter Start


def test_the_catalog_keeps_trains_and_a_tracker_follows_the_next_one(tmp_path):
    from flightpanel.astro import Catalog, TrainTracker, parse_tles
    db = Db(str(tmp_path / "f.db"))
    batch = parse_tles(_batch_text())
    tight = _string(batch[0], 22, 1.5)
    text = "\n".join(f"{n}\n{a}\n{b}" for n, a, b in batch + tight) + "\n"
    cat = Catalog(db, session=_Session(_Answer(200, _stations_text()), _Answer(200, text)),
                  trains_url="https://celestrak.org/NORAD/elements/gp.php?GROUP=starlink&FORMAT=TLE")
    cat.year = 2026
    assert asyncio.run(cat.refresh()) and cat.last_error is None and len(cat.trains) == 1
    assert len(Catalog(db).trains) == 1 and Catalog(db).trains[0].n == 22        # gespeichert, wieder geladen
    tr = TrainTracker(make_cfg(home_lat=48.09, home_lon=9.83), cat)
    assert tr.count == 22 and tr.usable(tle_epoch(tight[0][1]) + 3600)
    now = tle_epoch(tight[0][1]) + 3600
    for t in tr.trackers:
        t._visible = lambda ts, st: st is not None and st[1] >= 10                # Tag und Nacht egal: nur Höhe
    asyncio.run(tr.update_passes(now))
    assert tr.next_pass(now) is not None and tr.state(now) is not None
    cat.trains, cat.trains_version = [], cat.trains_version + 1                    # Kette verteilt: weg
    asyncio.run(tr.update_passes(now + 3600))
    assert tr.next_pass(now + 3600) is None and tr.count == 0
