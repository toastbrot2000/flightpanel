"""Vorhersage, Piste, Notfall und der nächste Flieger im Display-Modell."""

import asyncio
import math
import random

from conftest import BER, NAMES, FakeAdsb, make_cfg, state
from flightpanel.airport import landing_course_votes, path_in_view, predict_entry, runway_name
from flightpanel.build import build_model, find_alert
from flightpanel.geo import destination
from flightpanel.model import ARR
from flightpanel.view import entry_eta, in_view, sighting, visibility


def old_visibility(cfg, s, horizon_s):
    """Die Version vor dem Umbau, zum Vergleich."""
    alt = s.geo_alt_m if s.geo_alt_m is not None else s.baro_alt_m
    if in_view(cfg, sighting(cfg, s.lat, s.lon, alt)):
        return 2
    if s.velocity_ms and s.track_deg is not None and alt is not None and horizon_s > 0:
        for i in (1, 2, 3):
            dt = horizon_s * i / 3
            lat, lon = destination(s.lat, s.lon, s.track_deg, s.velocity_ms * dt / 1000)
            if in_view(cfg, sighting(cfg, lat, lon, max(0.0, alt + (s.vrate_ms or 0.0) * dt))):
                return 1
    return 0


def test_visibility_unchanged():
    cfg = make_cfg(view_from=140, view_to=190, view_min_elev=0.5, view_max_km=40)
    r = random.Random(1)
    for _ in range(3000):
        s = state(lat=52.52 + r.uniform(-0.4, 0.4), lon=13.4 + r.uniform(-0.6, 0.6), alt=r.uniform(300, 6000),
                  speed=r.choice([None, r.uniform(60, 250)]), track=r.uniform(0, 360), vrate=r.uniform(-10, 10))
        h = r.choice([0, 20, 60, 150])
        assert visibility(cfg, s, h) == old_visibility(cfg, s, h)


def test_entry_eta_straight_line():
    # 20 km östlich, fliegt nach Westen über das Haus; Sichtfeld im Westen
    cfg = make_cfg(view_from=250, view_to=290, view_min_elev=5, view_max_km=40)
    lat, lon = destination(52.52, 13.40, 90, 20)
    e = entry_eta(cfg, state(lat=lat, lon=lon, alt=2000, speed=100, track=270), 600, 15)
    assert e is not None
    t, az = e
    assert 180 <= t <= 300            # erst übers Haus, dann im Westen hoch genug
    assert 250 <= az <= 290
    assert entry_eta(cfg, state(lat=lat, lon=lon, alt=2000, speed=100, track=90), 600, 15) is None


def test_runway_name():
    assert runway_name(249) == "25"
    assert runway_name(69) == "07"
    assert runway_name(4) == "36"


def test_path_in_view_depends_on_cone():
    every = make_cfg()
    assert path_in_view(every, 249, "approach") and path_in_view(every, 249, "departure")
    away = make_cfg(view_from=300, view_to=359)      # nach Nordwesten, weg vom Flughafen
    assert not path_in_view(away, 249, "approach") and not path_in_view(away, 249, "departure")
    # Endanflug auf 25 kommt aus Ostnordost: von Mitte aus im Südosten zu sehen
    se = make_cfg(view_from=110, view_to=150, view_min_elev=0.5)
    assert path_in_view(se, 249, "approach")


def test_predict_entry_via_final_approach():
    cfg = make_cfg(view_from=110, view_to=150, view_min_elev=0.5, view_max_km=40)
    far_km = max(path_in_view(cfg, 249, "approach"))[0]
    # Im Gegenanflug 30 km ostnordöstlich, Kurs 069 (weg vom Platz): geradeaus käme er nie
    # ins Sichtfeld, nach dem Eindrehen fliegt er den Endanflug durchs Sichtfeld
    lat, lon = destination(BER[0], BER[1], 72, 30)
    s = state(lat=lat, lon=lon, alt=1500, speed=90, track=69, vrate=0)
    assert not in_view(cfg, sighting(cfg, lat, lon, 1500))
    p = predict_entry(cfg, s, 249, in_seq=True)
    assert p is not None and p[2] == "approach"
    assert abs(p[0] - (30 + 1.6 - far_km) * 1000 / 90) < 40       # Reststrecke bis dort / Tempo
    assert 110 <= p[1] <= 150
    q = predict_entry(cfg, s, 249, in_seq=False)
    assert q is None or q[2] == "track"
    assert far_km > 5


def test_landing_course_votes():
    cfg = make_cfg()
    near = [state(icao=f"a{i}", lat=BER[0] + 0.02, lon=BER[1] + 0.05, alt=600, track=249) for i in range(3)]
    assert landing_course_votes(cfg, near, 69) == (249, 3)
    assert landing_course_votes(cfg, [], 69) == (69, 0)


def test_find_alert():
    cfg = make_cfg()
    states = [state(icao="g", squawk="7700", ground=True), state(icao="a", squawk="7600", cs="DLH4AB"),
              state(icao="b", squawk="1000")]
    a = find_alert(cfg, states, NAMES)
    assert a["squawk"] == "7600" and a["meaning"] == "FUNKAUSFALL" and a["airline"] == "Lufthansa"
    assert find_alert(cfg, states[2:], NAMES) is None


def test_flights_landing_or_starting_here_go_first():
    """Mehr im Sichtfeld als MAX_FLIGHTS: Wer hier landet oder startet, geht vor
    Überflügen, auch wenn die näher sind. Mit Platz bleiben die Überflüge dabei."""
    def at(bearing, km, **kw):
        lat, lon = destination(52.52, 13.40, bearing, km)
        return state(lat=lat, lon=lon, **kw)

    states = [at(0, 3, icao="o1", cs="UAE45", alt=5000), at(270, 4, icao="o2", cs="UAE46", alt=5000),
              at(160, 12, icao="dep", cs="EZY1234", alt=1500, vrate=10),
              at(200, 14, icao="arr", cs="DLH4AB", alt=1000, vrate=-4)]
    adsb = FakeAdsb({"EZY1234": {"origin": "BER", "dest": "LGW"}, "DLH4AB": {"origin": "MUC", "dest": "BER"}})
    m = asyncio.run(build_model(make_cfg(max_flights=2), states, adsb, NAMES, horizon_s=20))
    assert {f.callsign for f in m.flights} == {"EZY1234", "DLH4AB"}
    roomy = asyncio.run(build_model(make_cfg(max_flights=4), states, adsb, NAMES, horizon_s=20))
    assert len(roomy.flights) == 4


def test_private_planes_join_the_rotation_in_four_ranks():
    """Reicht MAX_FLIGHTS nicht: Airlines, die hier landen oder starten; dann Privatflieger,
    die das tun; dann Airline-Überflüge; dann Privat-Überflüge. Mit Platz sind alle dabei,
    Privatflieger auch neben Airlines."""
    def at(bearing, km, **kw):
        lat, lon = destination(52.52, 13.40, bearing, km)
        return state(lat=lat, lon=lon, **kw)

    states = [at(200, 14, icao="arr", cs="DLH4AB", alt=1000, vrate=-4),              # Airline, landet hier
              at(160, 12, icao="biz", cs="BFX1073", alt=1200, vrate=-4, category="A2", actype="C25B"),
              at(0, 4, icao="ovf", cs="UAE45", alt=5000),                              # Airline, Überflug
              at(90, 2, icao="heli", cs="CHX31", alt=300, category="A7", actype="EC35")]   # Privat, Überflug
    adsb = FakeAdsb({"DLH4AB": {"origin": "MUC", "dest": "BER"}, "BFX1073": {"origin": "ZRH", "dest": "BER"}})
    picked = [{f.callsign for f in asyncio.run(build_model(make_cfg(max_flights=n), states, adsb, NAMES,
                                                            horizon_s=20)).flights} for n in (1, 2, 3, 4)]
    assert picked == [{"DLH4AB"}, {"DLH4AB", "BFX1073"}, {"DLH4AB", "BFX1073", "UAE45"},
                      {"DLH4AB", "BFX1073", "UAE45", "CHX31"}]
    heli = next(f for f in asyncio.run(build_model(make_cfg(max_flights=4), states, adsb, NAMES)).flights
                if f.callsign == "CHX31")
    assert heli.kind == "other" and heli.rotorcraft


def test_build_model_idle_fields():
    cfg = make_cfg(view_from=250, view_to=290, view_min_elev=5, view_max_km=40)
    lat, lon = destination(52.52, 13.40, 90, 20)
    states = [
        state(icao="in", cs="EZY1234", lat=lat, lon=lon, alt=2000, speed=100, track=270),
        state(icao="em", cs="DLH4AB", lat=52.6, lon=13.2, alt=2500, track=0, squawk="7700"),
        state(icao="gr", cs="UAE45", lat=52.36, lon=13.5, alt=0, ground=True),
    ]
    m = asyncio.run(build_model(cfg, states, FakeAdsb(), NAMES, horizon_s=20))
    assert m.flights == [] and m.empty
    assert m.upcoming is not None and m.upcoming.callsign == "EZY1234"
    assert 150 < m.upcoming_eta_s < 320 and m.upcoming_via == "track"
    assert m.alert["callsign"] == "DLH4AB"
    near = {n["icao24"]: n for n in m.nearby}
    assert set(near) == {"in", "em"}
    assert near["in"]["eta_s"] == m.upcoming_eta_s and near["in"]["status"] in (ARR, "DEP", "OVF")
    assert m.course_votes == 0 and m.landing_course == 249
    assert math.isfinite(m.upcoming_az)


def test_rare_flights_are_marked():
    rare = {"types": {"A388": "A380"}, "callsigns": {"KRH": "UK Royal VIP"}}
    states = [state(icao="vip", cs="KRH01", actype="A321"), state(icao="big", cs="UAE45", actype="A388", lat=52.62),
              state(icao="ezy", cs="EZY1234", lat=52.58)]
    m = asyncio.run(build_model(make_cfg(max_flights=4), states, FakeAdsb(), NAMES, rare=rare))
    assert {f.icao24: f.rare for f in m.flights} == {"vip": True, "big": True, "ezy": False}


def test_the_path_cache_does_not_grow_without_bound():
    """Jede Änderung am Sichtfeld (panels set, bald die Einstellungsseite) gibt neue Schlüssel."""
    from flightpanel import airport
    airport._PATH_CACHE.clear()
    for i in range(airport.PATH_CACHE_MAX + 10):
        path_in_view(make_cfg(home_lat=52.5 + i / 10000), 249, "approach")
    assert 0 < len(airport._PATH_CACHE) <= airport.PATH_CACHE_MAX
