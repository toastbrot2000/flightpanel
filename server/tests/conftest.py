import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest  # noqa: E402

from flightpanel.config import Config  # noqa: E402
from flightpanel.opensky import State  # noqa: E402

DATA = os.path.join(os.path.dirname(__file__), "..", "..", "data")
FIXTURES = os.path.join(os.path.dirname(__file__), "data")

# Berlin-Mitte und BER, wie die Defaults
HOME = (52.52, 13.40)
BER = (52.3667, 13.5033)


def make_cfg(**kw) -> Config:
    base = dict(home_lat=HOME[0], home_lon=HOME[1], home_alt_m=40, radius_km=40, scan_radius_km=40,
                airport_lat=BER[0], airport_lon=BER[1], runway_course=249, view_from=0, view_to=360,
                view_min_elev=0, view_max_elev=90, view_max_km=100, view_only=True, show_other=True,
                max_flights=3, data_dir=DATA, idle_seconds=12, idle_off="", idle_clock_share=0.35)
    base.update(kw)
    return Config(**base)


def state(icao="3c0001", cs="EZY1234", lat=52.6, lon=13.6, alt=3000.0, speed=110.0, track=250.0,
          vrate=0.0, ground=False, actype="A320", category="A3", squawk="") -> State:
    return State(icao24=icao, callsign=cs, lon=lon, lat=lat, baro_alt_m=alt, on_ground=ground,
                 velocity_ms=speed, track_deg=track, vrate_ms=vrate, geo_alt_m=alt, actype=actype,
                 category=category, squawk=squawk)


NAMES = {"EZY": {"name": "easyJet", "iata": "U2", "airline": True},
         "DLH": {"name": "Lufthansa", "iata": "LH", "airline": True},
         "UAE": {"name": "Emirates", "iata": "EK", "airline": True},
         "CHX": {"name": "Christoph", "iata": "", "airline": False}}


class FakeAdsb:
    """adsbdb ohne Netz: keine Routen, keine Flugzeugdaten."""

    def __init__(self, routes=None):
        self.routes = routes or {}

    async def route(self, cs):
        return self.routes.get(cs, {})

    async def aircraft(self, icao):
        return {}


@pytest.fixture
def cfg():
    return make_cfg()
