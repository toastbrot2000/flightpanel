"""Routen: Teilstrecke bei mehreren Stationen (adsb.im), Richtung über Abrufe hinweg,
Plausibilität."""

import asyncio

from conftest import BER, NAMES, FakeAdsb, make_cfg, state
from flightpanel.adsbim import leg_route, parse
from flightpanel.airlines import route_plausible
from flightpanel.airport import Recent, returning
from flightpanel.build import build_model
from flightpanel.geo import destination

NCE = (43.6584, 7.2159)
PMI = (39.5517, 2.7388)
HAM = (53.6304, 9.9882)


def answer(*stops, plausible=True):
    return {"plausible": plausible, "_airports": [{"iata": i, "lat": la, "lon": lo} for i, (la, lo) in stops]}


ROUND_TRIP = parse(answer(("BER", BER), ("NCE", NCE), ("BER", BER)))      # EWG9SK


def leg(s, hint=None):
    r = leg_route(ROUND_TRIP, s, "BER", hint)
    return (r["origin"], r["dest"]) if r else None


def test_round_trip_takes_the_leg_the_plane_is_on():
    lat, lon = destination(BER[0], BER[1], 69, 20)                  # 20 km vor BER
    assert leg(state(lat=lat, lon=lon, alt=1200, track=249, vrate=-4)) == ("NCE", "BER")    # sinkt: landet
    assert leg(state(lat=lat, lon=lon, alt=1500, track=210, vrate=8)) == ("BER", "NCE")     # steigt: gestartet
    # Horizontalflug oder am Boden in Platznähe: nur mit Gedächtnis zu sagen, sonst keine Route
    level = state(lat=lat, lon=lon, alt=900, track=69, vrate=0)
    assert leg(level) is None
    assert leg(level, "arr") == ("NCE", "BER") and leg(level, "dep") == ("BER", "NCE")
    taxiing = state(lat=BER[0], lon=BER[1], alt=0, speed=8, ground=True)
    assert leg(taxiing) is None and leg(taxiing, "dep") == ("BER", "NCE")
    # unterwegs, hoch über Bayern: der Kurs entscheidet
    lat, lon = destination(BER[0], BER[1], 215, 400)
    assert leg(state(lat=lat, lon=lon, alt=11000, track=35, vrate=0)) == ("NCE", "BER")
    assert leg(state(lat=lat, lon=lon, alt=11000, track=215, vrate=0)) == ("BER", "NCE")
    assert leg(state(lat=lat, lon=lon, alt=11000, track=None)) is None


def test_stopover_simple_and_unknown_routes():
    via_ber = parse(answer(("PMI", PMI), ("BER", BER), ("HAM", HAM)))
    lat, lon = destination(BER[0], BER[1], 200, 30)                 # landet in BER, fliegt danach weiter
    r = leg_route(via_ber, state(lat=lat, lon=lon, alt=1000, track=20, vrate=-3), "BER")
    assert (r["origin"], r["dest"]) == ("PMI", "BER") and r["dest_pos"] == list(BER)
    simple = parse(answer(("PMI", PMI), ("BER", BER)))
    assert leg_route(simple, state(lat=lat, lon=lon, alt=0, ground=True), "BER")["origin"] == "PMI"
    assert parse(answer(("PMI", PMI), ("BER", BER), plausible=False)) == []
    assert parse(answer(("BER", BER))) == [] and leg_route([], state()) == {}


def test_route_from_x_to_x_is_not_plausible():
    assert not route_plausible({"origin": "BER", "dest": "BER", "origin_pos": list(BER), "dest_pos": list(BER)},
                               *BER)
    assert route_plausible({"origin": "PMI", "dest": "BER", "origin_pos": list(PMI), "dest_pos": list(BER)},
                           52.0, 13.0)


OUTBOUND = {"origin": "BER", "dest": "NCE", "origin_pos": list(BER), "dest_pos": list(NCE),
            "airline": "Eurowings", "airline_icao": "EWG", "airline_iata": "EW"}


def test_returning_plane_keeps_its_direction_through_level_flight_and_landing():
    # EWG9SK kommt zurück, adsbdb kennt nur den Hinflug BER-NCE
    cfg, adsb, recent = make_cfg(), FakeAdsb({"EWG9SK": OUTBOUND}), Recent()
    lat, lon = destination(BER[0], BER[1], 69, 15)

    def poll(**kw):
        s = state(icao="3c56ec", cs="EWG9SK", **kw)
        return asyncio.run(build_model(cfg, [s], adsb, NAMES, recent=recent))

    f = poll(lat=lat, lon=lon, alt=800, track=249, vrate=-4).flights[0]          # sinkt im Endanflug
    assert f.status == "ARR" and f.seq == 1 and (f.origin, f.dest) == ("", "")    # "?→BER" statt "BER→NCE"
    assert f.airline == "Eurowings"
    f = poll(lat=lat, lon=lon, alt=800, track=249, vrate=0).flights[0]           # kurz horizontal
    assert f.status == "ARR" and f.seq == 1
    landed = poll(lat=BER[0], lon=BER[1], alt=0, speed=30, ground=True)          # Ausrollen
    assert landed.departures == []
    # ohne Gedächtnis: wer steigt, fliegt ab, auch mit derselben Route
    assert not returning(cfg, state(cs="EWG9SK", lat=lat, lon=lon, alt=800, vrate=8), OUTBOUND)
    assert returning(cfg, state(cs="EWG9SK", lat=lat, lon=lon, alt=800, vrate=0), OUTBOUND, hint="arr")
