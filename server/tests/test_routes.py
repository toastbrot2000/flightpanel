"""Routen: Teilstrecke bei mehreren Stationen (adsb.im), Plausibilität."""

from conftest import BER, state
from flightpanel.adsbim import parse
from flightpanel.airlines import route_plausible
from flightpanel.geo import destination

NCE = (43.6584, 7.2159)
PMI = (39.5517, 2.7388)
HAM = (53.6304, 9.9882)


def answer(*stops):
    return {"plausible": True, "_airports": [{"iata": i, "lat": la, "lon": lo} for i, (la, lo) in stops]}


ROUND_TRIP = answer(("BER", BER), ("NCE", NCE), ("BER", BER))


def test_round_trip_takes_the_leg_the_plane_is_on():
    # EWG9SK, BER-NCE-BER: 20 km vor BER im Sinkflug -> kommt aus Nizza
    lat, lon = destination(BER[0], BER[1], 69, 20)
    landing = state(cs="EWG9SK", lat=lat, lon=lon, alt=1200, track=249, vrate=-4)
    assert (parse(ROUND_TRIP, landing)["origin"], parse(ROUND_TRIP, landing)["dest"]) == ("NCE", "BER")
    # Gegenanflug, Kurs weg vom Platz, Höhe gehalten: trotzdem eine Landung
    downwind = state(cs="EWG9SK", lat=lat, lon=lon, alt=900, track=69, vrate=0)
    assert parse(ROUND_TRIP, downwind)["dest"] == "BER"
    # steigt nach dem Start -> fliegt nach Nizza
    climbing = state(cs="EWG9SK", lat=lat, lon=lon, alt=1500, track=210, vrate=8)
    assert parse(ROUND_TRIP, climbing)["dest"] == "NCE"
    # unterwegs, hoch über Bayern: der Kurs entscheidet
    lat, lon = destination(BER[0], BER[1], 215, 400)
    assert parse(ROUND_TRIP, state(lat=lat, lon=lon, alt=11000, track=35, vrate=0))["dest"] == "BER"
    assert parse(ROUND_TRIP, state(lat=lat, lon=lon, alt=11000, track=215, vrate=0))["dest"] == "NCE"
    # weder Höhe noch Kurs: lieber keine Route als eine falsche
    assert parse(ROUND_TRIP, state(lat=lat, lon=lon, alt=11000, track=None)) == {}


def test_stopover_and_simple_routes():
    via_ber = answer(("PMI", PMI), ("BER", BER), ("HAM", HAM))
    lat, lon = destination(BER[0], BER[1], 200, 30)        # landet in BER, fliegt danach weiter
    r = parse(via_ber, state(lat=lat, lon=lon, alt=1000, track=20, vrate=-3))
    assert (r["origin"], r["dest"]) == ("PMI", "BER") and r["dest_pos"] == list(BER)
    simple = answer(("PMI", PMI), ("BER", BER))
    assert parse(simple, state(lat=lat, lon=lon))["origin"] == "PMI"
    assert parse({**simple, "plausible": False}, state()) == {}
    assert parse(answer(("BER", BER)), state()) == {}


def test_route_from_x_to_x_is_not_plausible():
    assert not route_plausible({"origin": "BER", "dest": "BER", "origin_pos": list(BER), "dest_pos": list(BER)},
                               *BER)
    assert route_plausible({"origin": "PMI", "dest": "BER", "origin_pos": list(PMI), "dest_pos": list(BER)},
                           52.0, 13.0)


def test_outbound_route_of_a_returning_plane_is_dropped():
    # EWG9SK: adsbdb kennt nur BER-NCE, das Flugzeug sinkt 15 km vor BER
    import asyncio

    from conftest import NAMES, FakeAdsb, make_cfg
    from flightpanel.airport import returning
    from flightpanel.build import build_model

    cfg = make_cfg()
    lat, lon = destination(BER[0], BER[1], 69, 15)
    outbound = {"origin": "BER", "dest": "NCE", "origin_pos": list(BER), "dest_pos": list(NCE),
                "airline": "Eurowings", "airline_icao": "EWG", "airline_iata": "EW"}
    landing = state(icao="3c56ec", cs="EWG9SK", lat=lat, lon=lon, alt=800, track=249, vrate=-4)
    assert returning(cfg, landing, outbound)
    assert not returning(cfg, state(cs="EWG9SK", lat=lat, lon=lon, alt=800, vrate=8), outbound)   # steigt: Abflug
    m = asyncio.run(build_model(cfg, [landing], FakeAdsb({"EWG9SK": outbound}), NAMES))
    f = m.flights[0]
    assert f.status == "ARR" and f.seq == 1 and (f.origin, f.dest) == ("", "")    # "?→BER" statt "BER→NCE"
    assert f.airline == "Eurowings"
