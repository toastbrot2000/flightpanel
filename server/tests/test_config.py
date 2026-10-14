"""Einstellungen: Server-Felder aus der Umgebung, Panel-Felder wie aus der Datenbank (for_panel):
Typen, Defaults, Karten-Schalter, Heimatflughafen."""

from datetime import datetime

import pytest

from conftest import DATA
from flightpanel.config import Config, default_text, in_hours, is_panel_key, panel_fields, panel_settings_from_env


def env(**kw):
    return {"DATA_DIR": DATA, **{k: str(v) for k, v in kw.items()}}


def cfg(**kw):
    """Server-Felder aus der Umgebung, Panel-Felder wie aus der Datenbank, beides geprüft wie .env."""
    panel = {k: str(v) for k, v in kw.items() if is_panel_key(k)}
    server = {k: str(v) for k, v in kw.items() if not is_panel_key(k)}
    return Config.for_panel(Config.from_env(env(**server)), panel)


def test_types_and_empty_values():
    c = cfg(HOME_LAT="52.5", POLL_INTERVAL="20", VIEW_ONLY="1", SHOW_OTHER="0", LAYOUT="c", SOURCE="OpenSky",
            QUIET_HOURS="22:30-06:00", MAX_ALT_M="")
    assert c.home_lat == 52.5 and c.poll_interval == 20 and c.view_only and not c.show_other
    assert c.layout == "C" and c.source == "opensky" and c.quiet_hours == (22 * 60 + 30, 6 * 60)
    assert c.max_alt_m == Config.max_alt_m                  # leer zählt wie nicht gesetzt


def test_scan_radius_defaults_to_radius():
    assert cfg(RADIUS_KM=25).scan_radius_km == 25
    assert cfg(RADIUS_KM=25, SCAN_RADIUS_KM=80).fetch_radius_km == 80


def test_bad_value_names_the_variable():
    with pytest.raises(ValueError, match="POLL_INTERVAL"):
        Config.from_env(env(POLL_INTERVAL="oft"))
    with pytest.raises(ValueError, match="LAYOUT"):
        cfg(LAYOUT="D")


def test_layout_auto_is_c():
    """Früher hieß C ohne Logo Layout A; jetzt bekommt es einen Platzhalter. auto aus
    älteren Fassungen ist dasselbe wie C."""
    assert cfg(LAYOUT="auto").layout == "C"
    assert cfg().layout == "C"


def test_cards_switched_off_one_by_one():
    c = cfg(CARD_PLANE="0", CARD_PIGEON="false", CARD_CLOCK="1", CARD_RADAR="")
    assert c.cards_off == {"plane", "pigeon"}
    assert Config.for_panel(c, {}).cards_off == {"plane", "pigeon"}          # ohne CARD-Zeile: wie vorher


def test_home_airport_from_airport_list():
    ber = Config.from_env(env())
    assert (ber.local_airport_iata, ber.metar_station) == ("BER", "EDDB")
    muc = Config.from_env(env(LOCAL_AIRPORT_IATA="muc"))
    assert muc.local_airport_iata == "MUC" and muc.metar_station == "EDDM"
    assert abs(muc.airport_lat - 48.35) < 0.05 and abs(muc.airport_lon - 11.79) < 0.05
    own = Config.from_env(env(LOCAL_AIRPORT_IATA="MUC", AIRPORT_LAT="48.0", AIRPORT_LON="11.0", METAR_STATION="X"))
    assert (own.airport_lat, own.airport_lon, own.metar_station) == (48.0, 11.0, "X")
    with pytest.raises(ValueError, match="AIRPORT_LAT"):
        Config.from_env(env(LOCAL_AIRPORT_IATA="QQQ"))


def test_brightness_schedule():
    c = Config(day_hours=(8 * 60, 19 * 60), quiet_hours=(23 * 60, 6 * 60 + 30), brightness_day=90,
               brightness_evening=35)
    at = lambda h, m=0: c.brightness(datetime(2026, 10, 10, h, m).timestamp())   # noqa: E731
    assert (at(12), at(20), at(23, 30), at(6, 29), at(6, 30)) == (90, 35, 0, 0, 35)
    assert c.active_seconds_per_day() == (24 * 60 - 7 * 60 - 30) * 60
    assert in_hours((23 * 60, 60), 0) and not in_hours((23 * 60, 60), 120)


def test_calm_mode_sets_defaults_explicit_values_win():
    calm = cfg(CALM="1")
    assert (calm.page_seconds, calm.flight_seconds, calm.idle_seconds, calm.blink) == (10, 20, 30, False)
    assert calm.info_pages == ("eta", "alt")
    own = cfg(CALM="1", PAGE_SECONDS="6", BLINK="1", INFO_PAGES="alt, eta")
    assert (own.page_seconds, own.blink, own.info_pages, own.idle_seconds) == (6, True, ("alt", "eta"), 30)
    normal = cfg()
    assert (normal.page_seconds, normal.flight_seconds, normal.blink) == (4, 0, True)
    assert normal.info_pages == ("eta", "alt", "dist", "callsign")
    with pytest.raises(ValueError, match="INFO_PAGES"):
        cfg(INFO_PAGES="eta,wetter")


def test_airport_height_and_station_checks(caplog):
    muc = Config.from_env(env(LOCAL_AIRPORT_IATA="MUC"))
    assert muc.airport_alt_m == 453                         # Höhe aus airports.json
    with caplog.at_level("WARNING"):
        Config.from_env(env(LOCAL_AIRPORT_IATA="HAM", AIRPORT_LAT="52.3667", AIRPORT_LON="13.5033",
                            METAR_STATION="EDDB"))           # alte .env nach dem Umzug
    assert "km von HAM" in caplog.text and "METAR_STATION=EDDB" in caplog.text
    elsewhere = Config.from_env(env(LOCAL_AIRPORT_IATA="QQQ", AIRPORT_LAT="50", AIRPORT_LON="10"))
    assert not elsewhere.metar                              # ohne Station kein Wetter statt Berliner Wetter


def test_values_out_of_range_and_old_names(caplog):
    for name, value in (("PAGE_SECONDS", "0"), ("IDLE_SECONDS", "-5"), ("IDLE_CLOCK_SHARE", "2"),
                        ("BRIGHTNESS_DAY", "300")):
        with pytest.raises(ValueError, match=name):
            cfg(**{name: value})
    with caplog.at_level("WARNING"):
        Config.from_env(env(IDLE_OFF="pigeon"))
        cfg(CALM="1", PAGE_SECONDS="4")
    assert "IDLE_OFF gibt es nicht mehr" in caplog.text and "PAGE_SECONDS=4" in caplog.text


def test_env_panel_keys_are_not_server_settings():
    """Standort und Co. stehen nicht mehr in .env: from_env lässt sie auf dem Default,
    panel_settings_from_env sammelt sie für die einmalige Übernahme (Karten nur, wenn aus)."""
    e = env(HOME_LAT="51.0", VIEW_FROM="110", CARD_PIGEON="0", CARD_CLOCK="1", CARD_RADAR="", POLL_INTERVAL="30")
    c = Config.from_env(e)
    assert c.home_lat == Config.home_lat and c.view_from == 0 and c.cards_off == frozenset() and c.poll_interval == 30
    assert panel_settings_from_env(e) == {"HOME_LAT": "51.0", "VIEW_FROM": "110", "CARD_PIGEON": "0"}
    assert c.lan_panel == "home" and Config.from_env(env(LAN_PANEL="wohnzimmer")).lan_panel == "wohnzimmer"


def test_for_panel_rejects_what_is_not_a_panel_setting():
    with pytest.raises(ValueError, match="keine Panel-Einstellung: DATA_DIR, POLL_INTERVAL"):
        Config.for_panel(Config.from_env(env()), {"POLL_INTERVAL": "5", "DATA_DIR": "/x", "HOME_LAT": "1"})
    with pytest.raises(ValueError, match="HOME_LAT='hier'"):
        Config.for_panel(Config.from_env(env()), {"HOME_LAT": "hier"})


def test_panel_fields_carry_group_text_and_a_default_to_type_in():
    by_env = {f.metadata["env"]: f for f in panel_fields()}
    assert {"HOME_LAT", "VIEW_FROM", "LAYOUT", "QUIET_HOURS", "IDLE_CLOCK_SHARE"} <= set(by_env)
    assert "POLL_INTERVAL" not in by_env and "WIDTH" not in by_env
    assert all(len(f.metadata["panel"]) == 2 and f.metadata["panel"][1] for f in panel_fields())
    assert default_text(by_env["QUIET_HOURS"]) == "00:00-06:30" and default_text(by_env["BLINK"]) == "1"
    assert default_text(by_env["INFO_PAGES"]) == "eta,alt,dist,callsign" and default_text(by_env["HOME_LAT"]) == "52.52"
    assert is_panel_key("CARD_PIGEON") and not is_panel_key("CARD_") and not is_panel_key("TZ")


def test_default_text_of_every_panel_field_parses_back_to_the_default():
    """Was als Default zum Eintippen steht, ergibt beim Setzen wieder den Default."""
    server = Config.from_env(env())
    assert Config.for_panel(server, {f.metadata["env"]: default_text(f) for f in panel_fields()}) == server
