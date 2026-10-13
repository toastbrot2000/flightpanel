"""Routen: Teilstrecke bei mehreren Stationen (adsb.im), Richtung über Abrufe hinweg,
Plausibilität."""

import asyncio

from conftest import BER, NAMES, FakeAdsb, make_cfg, state
from flightpanel.adsbim import AdsbIm, leg_route, parse
from flightpanel.airlines import route_plausible
from flightpanel.airport import Recent, returning
from flightpanel.build import build_model
from flightpanel.cache import Cache
from flightpanel.db import Db
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


def test_adsbim_skips_empty_entries(tmp_path):
    """adsb.im liefert manchmal null in der Liste: überspringen, statt den ganzen Abruf
    abzubrechen (AttributeError adsbim.py:151, zweimal in /issues)."""
    class Response:
        status = 200

        async def json(self, content_type=None):
            return [None, {"callsign": "EWG9SK", **answer(("BER", BER), ("NCE", NCE))}]

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

    class Session:
        def post(self, url, json=None, timeout=None):
            return Response()

    im = AdsbIm(Session(), Cache(Db(str(tmp_path / "c.db"))))
    out = asyncio.run(im.routes([state(cs="EWG9SK"), state(icao="x", cs="DLH1")]))
    assert [stop.iata for stop in out["EWG9SK"]] == ["BER", "NCE"]


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


def test_calibration_aircraft_is_no_arrival_and_unknown_operators_are_noted(tmp_path):
    # FCK2FT, King Air der Flugvermessung, tief und horizontal westlich von BER
    from flightpanel.issues import Issues
    s = state(icao="3cce71", cs="FCK2FT", lat=52.3239, lon=12.8735, alt=690, track=43, vrate=0.3,
              category="A1", actype="B350")
    cfg = make_cfg()
    unknown = Issues(Db(str(tmp_path / "i.db")))
    m = asyncio.run(build_model(cfg, [s], FakeAdsb(), NAMES, issues=unknown))
    assert m.arrivals and m.arrivals[0]["callsign"] == "FCK2FT"            # unbekannt: geraten
    assert "unknown_operator" in unknown.report()["counts"]                # und gemeldet
    names = {**NAMES, "FCK": {"name": "Flugvermessung", "iata": "", "airline": False,
                              "label": "prüft Landehilfen", "number": False}}
    m = asyncio.run(build_model(cfg, [s], FakeAdsb(), names))
    assert m.arrivals == [] and all(n["seq"] is None for n in m.nearby)


def test_operator_names_match():
    from flightpanel.issues import names_match
    assert names_match("Norwegian", "Norwegian Air Sweden AOC")
    assert names_match("SAS", "Scandinavian Airlines System")
    assert names_match("airBaltic", "Air Baltic")                 # Leerzeichen zählen nicht
    assert not names_match("Sundair", "City Airline")


def test_checked_adsbdb_names_are_not_reported_again(tmp_path):
    """Sundair heißt bei adsbdb noch City Airline. Einmal geprüft und in airlines.json
    vermerkt ("adsbdb"), meldet /issues das nicht bei jedem Flug wieder."""
    from flightpanel.airlines import note_operator
    from flightpanel.issues import Issues
    s = state(cs="SDR17RA")
    route = {"airline": "City Airline", "airline_icao": "SDR"}
    names = {"SDR": {"name": "Sundair", "iata": "SR", "airline": True, "label": "", "number": False}}
    issues = Issues(Db(str(tmp_path / "i.db")))
    note_operator(issues, s, route, names, True)
    assert "name_mismatch" in issues.report()["counts"]                   # ungeprüft: gemeldet
    issues.clear()
    names["SDR"]["adsbdb"] = "City Airline"
    note_operator(issues, s, route, names, True)
    assert issues.report()["counts"] == {}


def test_unknown_operator_gets_its_name_from_adsbdb():
    """Kennt adsbdb die Flugnummer nicht, aber den Betreiber (AWH: Aerowest GmbH), heißt
    der Flug nach dem Betreiber, auf dem Panel und in der Landefolge. Er bleibt ein
    Privatflieger, und das Rufzeichen wandert in die wechselnde Zeile."""
    lat, lon = destination(BER[0], BER[1], 69, 12)            # im Endanflug auf die 25
    s = state(icao="3cc24f", cs="AWH91J", lat=lat, lon=lon, alt=650, track=249, vrate=-3.5, speed=75,
              category="A2", actype="C560")
    adsb = FakeAdsb(airlines={"AWH": "Aerowest GmbH"})
    m = asyncio.run(build_model(make_cfg(), [s], adsb, NAMES))
    fl = m.flights[0]
    assert (fl.airline, fl.kind, fl.status) == ("Aerowest", "other", "ARR")
    assert [a["airline"] for a in m.arrivals] == ["Aerowest"]
    plain = asyncio.run(build_model(make_cfg(), [s], FakeAdsb(), NAMES))
    assert plain.flights[0].airline == "AWH91J"                 # ohne Betreibernamen wie bisher


def test_long_names_are_shortened_by_whole_words():
    """Nie mitten im Wort kürzen ("Copenhagen A"): Wörter von hinten weg, bis es passt."""
    from flightpanel.airlines import short_airline

    assert short_airline("Aerowest GmbH", "AWH", NAMES) == "Aerowest"
    assert short_airline("Copenhagen Airtaxi", "CAT", NAMES) == "Copenhagen"
    assert short_airline("Norwegian Air Sweden AOC", "NSZ", NAMES) == "Norwegian"
    assert short_airline("Redstar Aviation", "RHH", NAMES) == "Redstar"
    assert short_airline("Deutsche Lufthansa AG", "XYZ", NAMES) == "Lufthansa"
    assert short_airline("Scandinavian Airlines System", "XYZ", NAMES) == "Scandinavian"


def test_adsbdb_operator_lookup(tmp_path):
    """adsbdb /airline/<ICAO> antwortet mit einer Liste; unbekannt und Unsinn ergeben "".
    Beides kommt in den Cache, damit nicht jeder Abruf neu fragt."""
    from flightpanel.adsbdb import AdsbDb

    asked = []

    class Response:
        def __init__(self, status, body):
            self.status, self.body = status, body

        def raise_for_status(self):
            pass

        async def json(self):
            return self.body

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

    class Session:
        def get(self, url, timeout=None):
            asked.append(url)
            if url.endswith("/airline/AWH"):
                return Response(200, {"response": [{"name": "Aerowest GmbH", "icao": "AWH", "iata": None}]})
            return Response(404, {"response": "unknown airline"})

    db = AdsbDb(Session(), "https://api.adsbdb.com/v0", Cache(Db(str(tmp_path / "c.db"))))

    async def run():
        return [await db.airline("AWH"), await db.airline("AWH"), await db.airline("ZZZ"), await db.airline("ZZZ"),
                await db.airline("../x")]

    assert asyncio.run(run()) == ["Aerowest GmbH", "Aerowest GmbH", "", "", ""]
    assert len(asked) == 2                                       # je Code einmal, Unsinn gar nicht
