"""METAR-Parser mit echten und typischen EDDB-Meldungen."""

from datetime import UTC, datetime

from flightpanel.metar import MAX_AGE_S, Metar, MetarSource, describe_wx, parse

NOW = datetime(2026, 10, 10, 18, 0, tzinfo=UTC).timestamp()


def test_cavok_live_sample():
    m = parse("METAR EDDB 101720Z AUTO 21007KT CAVOK 13/05 Q1007 NOSIG", NOW)
    assert (m.wind_dir, m.wind_kt, m.gust_kt) == (210, 7, None)
    assert m.cavok and m.vis_m == 10000 and m.ceiling_m is None and m.wx == []
    assert m.obs_time == datetime(2026, 10, 10, 17, 20, tzinfo=UTC).timestamp()


def test_rain_low_clouds_trend_ignored():
    m = parse("EDDB 101150Z 25015G28KT 220V280 4000 -RA BR SCT008 BKN012 OVC020 08/07 Q0998 TEMPO BKN005", NOW)
    assert (m.wind_dir, m.wind_kt, m.gust_kt, m.wind_var) == (250, 15, 28, (220, 280))
    assert m.vis_m == 4000 and m.wx == ["-RA", "BR"]
    assert m.ceiling_m == 366                      # BKN012, nicht BKN005 aus TEMPO
    assert describe_wx(m) == "Regen"


def test_fog_calm_vertical_visibility():
    m = parse("EDDB 100620Z 00000KT 0300 R25L/0600N FG VV001 03/03 Q1021", NOW)
    assert m.wind_kt == 0 and m.wind_dir is None
    assert m.vis_m == 300 and m.has("FG") and m.ceiling_m == 30
    assert describe_wx(m) == "Nebel"


def test_thunderstorm_mps_and_noaa_format():
    m = parse("2026/10/10 16:50\nEDDB 101650Z 27012MPS 9999 TSRA FEW040CB 21/15 Q1008\n", NOW)
    assert m.wind_kt == 23 and m.has("TS") and describe_wx(m) == "Gewitter"
    assert m.ceiling_m is None and m.clouds == [("FEW", 1219)]


def test_previous_month_and_garbage():
    m = parse("EDDB 302350Z VRB02KT 9999 NSC 05/04 Q1015", datetime(2026, 10, 1, 0, 30, tzinfo=UTC).timestamp())
    assert datetime.fromtimestamp(m.obs_time, tz=UTC).month == 9 and m.wind_dir is None
    assert parse("<html>Service unavailable</html>") is None
    assert parse("") is None


def test_freshness():
    src = MetarSource(None, "EDDB")
    src.current = Metar(raw="x", obs_time=NOW - MAX_AGE_S + 60)
    assert src.fresh(NOW) is src.current
    assert src.fresh(NOW + 120) is None
