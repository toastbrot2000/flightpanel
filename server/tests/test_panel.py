"""Was alle Panels gemeinsam brauchen: ein Abruf, der jedes abdeckt, und die Zeit, in der eines abruft. Mit einem
Panel genau wie bisher."""

from dataclasses import replace

from conftest import make_cfg
from flightpanel.geo import haversine_km
from flightpanel.panel import MAX_FETCH_KM, active_seconds, fetch_circle, union_bbox


def test_one_panel_fetches_exactly_as_before():
    cfg = make_cfg(radius_km=30, scan_radius_km=45)
    assert fetch_circle([cfg]) == (cfg.home_lat, cfg.home_lon, cfg.fetch_radius_km)
    assert union_bbox([cfg]) == cfg.bbox
    for quiet in ((0, 390), (1380, 390), (0, 0), (300, 300)):
        c = replace(cfg, quiet_hours=quiet)
        assert active_seconds([c]) == (24 * 60 - (quiet[1] - quiet[0]) % (24 * 60)) * 60, quiet


def test_the_fetch_covers_every_panel_and_stays_within_what_adsb_lol_serves():
    a = make_cfg()
    b = make_cfg(home_lat=52.40, home_lon=14.05, radius_km=10, scan_radius_km=10)
    lat, lon, r = fetch_circle([a, b])
    for c in (a, b):
        assert haversine_km(lat, lon, c.home_lat, c.home_lon) + c.fetch_radius_km <= r + 1e-9
    lamin, lomin, lamax, lomax = union_bbox([a, b])
    for c in (a, b):
        box = c.bbox
        assert lamin <= box[0] and lomin <= box[1] and lamax >= box[2] and lomax >= box[3]
    far = make_cfg(home_lat=60.17, home_lon=24.94)                     # Helsinki
    assert fetch_circle([a, far])[2] == MAX_FETCH_KM


def test_the_fetch_runs_while_any_panel_is_awake():
    early = make_cfg(quiet_hours=(22 * 60, 6 * 60))                    # 22:00-06:00 dunkel
    late = make_cfg(quiet_hours=(0, 8 * 60))                           # 00:00-08:00 dunkel
    owl = make_cfg(quiet_hours=(0, 0))                                 # nie
    assert active_seconds([early]) == 16 * 3600
    assert active_seconds([early, late]) == 18 * 3600                  # alle dunkel nur 00:00-06:00
    assert active_seconds([early, owl]) == 24 * 3600


def test_regions_share_a_fetch_only_when_it_stays_small():
    """Regionen: Panels mit demselben Flughafen holen gemeinsam; andere kommen dazu, solange der gemeinsame Abruf
    höchstens REGION_MERGE_KM reicht. Berlin bleibt ein Abruf, Biberach (500 km weiter) ein eigener."""
    from flightpanel.config import Config
    from flightpanel.panel import REGION_MERGE_KM, regions
    server = Config.from_env({"DATA_DIR": "../data"})
    home = Config.for_panel(server, {})
    anna = Config.for_panel(server, {"HOME_LAT": "52.40", "HOME_LON": "14.05", "RADIUS_KM": "10"})
    eltern = Config.for_panel(server, {"AIRPORT": "FMM", "RUNWAY": "240", "HOME_LAT": "48.09", "HOME_LON": "9.83"})
    see = Config.for_panel(server, {"AIRPORT": "FDH", "RUNWAY": "240", "HOME_LAT": "47.70", "HOME_LON": "9.60"})
    wald = Config.for_panel(server, {"AIRPORT": "-", "HOME_LAT": "48.00", "HOME_LON": "9.90"})
    assert regions([home]) == [[home]]
    assert regions([home, anna]) == [[home, anna]]
    assert regions([home, anna, eltern]) == [[home, anna], [eltern]]
    south = regions([eltern, see, wald])                                   # FMM, FDH und ohne: nah beieinander
    assert len(south) == 1 and fetch_circle(south[0])[2] <= REGION_MERGE_KM
    assert len(regions([home, eltern, see, wald, anna])) == 2
