"""Flugdaten von adsb.lol: ein hängender Server kostet keinen Abruf, ein Rate-Limit
wird nicht überrannt."""

import asyncio
import json

import aiohttp
import pytest
from multidict import CIMultiDict, CIMultiDictProxy
from yarl import URL

from conftest import make_db
from flightpanel.adsblol import AdsbLol
from flightpanel.issues import Issues, error_text, without_urls
from flightpanel.opensky import OpenSky

AC = {"hex": "3c6444", "flight": "DLH4T   ", "lat": 52.4, "lon": 13.6, "alt_baro": 3000, "gs": 200, "track": 250}


class Response:
    def __init__(self, status, body=None):
        self.status, self.body = status, body

    async def json(self, content_type=None):
        return self.body

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class Session:
    """Antwortet der Reihe nach; eine Exception wird geworfen. Merkt sich die Timeouts."""

    def __init__(self, *answers):
        self.answers, self.timeouts = list(answers), []

    def get(self, url, params=None, headers=None, timeout=None):       # adsb.lol: nur url, OpenSky auch Box und Header
        self.timeouts.append(timeout.total)
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer


def test_a_hanging_adsblol_server_gets_a_second_try():
    """adsb.lol hat mehrere Server unter einem Namen; im Oktober 2026 brauchte einer
    davon 5 bis 17 s, die anderen 0,1 s. Nach kurzem Warten ein zweiter Versuch,
    den aiohttp an die nächste Adresse schickt, also an einen anderen Server."""
    session = Session(TimeoutError(), Response(200, {"ac": [AC]}))
    lol = AdsbLol(session)
    states = asyncio.run(lol.fetch(52.4, 13.6, 80))
    assert [s.callsign for s in states] == ["DLH4T"] and lol.last_error is None
    assert len(session.timeouts) == 2 and session.timeouts[0] < 10


def test_adsblol_gives_up_after_the_second_try():
    session = Session(TimeoutError(), TimeoutError())
    lol = AdsbLol(session)
    with pytest.raises(TimeoutError):
        asyncio.run(lol.fetch(52.4, 13.6, 80))
    assert lol.last_error == "TimeoutError" and len(session.timeouts) == 2


def test_adsblol_rate_limit_is_not_retried():
    session = Session(Response(429))
    lol = AdsbLol(session)
    with pytest.raises(RuntimeError, match="429"):
        asyncio.run(lol.fetch(52.4, 13.6, 80))
    assert len(session.timeouts) == 1 and lol.backoff_until > 0


def response_error(url: str) -> aiohttp.ClientResponseError:
    """Ein Fehler, wie raise_for_status() ihn wirft: Er nennt die ganze Anfrage samt Parametern."""
    info = aiohttp.RequestInfo(URL(url), "GET", CIMultiDictProxy(CIMultiDict()), URL(url))
    return aiohttp.ClientResponseError(info, (), status=400, message="Bad Request")


def test_error_texts_name_the_server_but_not_the_request():
    """OpenSky und adsb.lol tragen Mittelpunkt und Umkreis des Abrufs in der Adresse, also ungefähr das Fenster."""
    e = response_error("https://opensky-network.org/api/states/all?lamin=52.1234&lomin=13.0001&lamax=52.9&lomax=13.9")
    assert "52.1234" in str(e)                                   # so käme es sonst in Log, /health und /issues
    text = error_text(e)
    assert "52.1234" not in text and "13.0001" not in text
    assert "400" in text and "https://opensky-network.org/…" in text
    point = "Fehler bei https://api.adsb.lol/v2/point/52.5200/13.4000/22, nochmal"
    assert without_urls(point) == "Fehler bei https://api.adsb.lol/…, nochmal"
    assert error_text(TimeoutError()) == "TimeoutError"


def test_issues_never_store_a_request_url(tmp_path):
    issues = Issues(make_db(tmp_path))
    issues.source_error("opensky", "400, url='https://opensky-network.org/api/states/all?lamin=52.1234'")
    issues.note("source_error", "adsblol", error="https://api.adsb.lol/v2/point/52.5200/13.4000/22")
    text = json.dumps(issues.report())
    assert "52.1234" not in text and "52.5200" not in text and "opensky-network.org" in text


def test_issues_from_before_the_change_show_no_address_either(tmp_path):
    """Einträge von früher tragen die ganze Adresse, in der Datenbank wie in einer alten issues.json. note() kürzt nur,
    was neu dazukommt; report() und damit /issues kürzt auch das Alte."""
    window = "https://opensky-network.org/api/states/all?lamin=52.1234&lomin=13.0001&lamax=52.9&lomax=13.9"
    point = "https://api.adsb.lol/v2/point/52.5200/13.4000/22"
    rest = {"seen": 3, "days": 2, "first": 1.7e9, "last": 1.7e9, "callsigns": []}
    db = make_db(tmp_path)
    db.execute("INSERT INTO issues (key, data) VALUES (?, ?)", ("source_error:opensky", json.dumps(
        {"kind": "source_error", "key": "opensky", **rest, "example": {"error": f"400, url='{window}'"}})))
    issues = Issues(db)                                       # lädt die Zeile aus der Datenbank, wie sie steht
    issues.load_legacy({"items": {"source_error:adsblol": {
        "kind": "source_error", "key": "adsblol", **rest, "example": {"error": f"Timeout bei {point}"}}}})
    text = json.dumps(issues.report())
    assert "52.1234" not in text and "13.0001" not in text    # die Zeile aus der Datenbank
    assert "52.5200" not in text and "13.4000" not in text    # der Eintrag aus der alten issues.json
    assert "opensky-network.org" in text and "api.adsb.lol" in text


def test_a_connection_timeout_names_the_host_but_not_the_point_of_adsblol(caplog):
    """aiohttp schreibt die ganze Adresse in den ConnectionTimeoutError, bei adsb.lol samt Mittelpunkt und Umkreis.
    last_error geht in /health, beim ersten Versuch in die Zeile "zweiter Versuch" im Log."""
    url = "https://api.adsb.lol/v2/point/52.4000/13.6000/44"
    session = Session(*(aiohttp.ConnectionTimeoutError(f"Connection timeout to host {url}") for _ in range(2)))
    lol = AdsbLol(session)
    with caplog.at_level("INFO"), pytest.raises(aiohttp.ConnectionTimeoutError):
        asyncio.run(lol.fetch(52.4, 13.6, 80))
    assert len(session.timeouts) == 2 and "zweiter Versuch" in caplog.text
    assert "52.4000" not in lol.last_error and "13.6000" not in lol.last_error and "api.adsb.lol" in lol.last_error
    assert "52.4000" not in caplog.text and "13.6000" not in caplog.text


def test_a_connection_timeout_names_the_host_but_not_the_window_of_opensky():
    """Bei OpenSky stehen die Grenzen der Box in der Adresse, die aiohttp in den ConnectionTimeoutError schreibt.
    last_error geht in /health."""
    url = "https://opensky-network.org/api/states/all?lamin=52.1234&lomin=13.0001&lamax=52.9000&lomax=13.9000"
    sky = OpenSky(Session(aiohttp.ConnectionTimeoutError(f"Connection timeout to host {url}")), "", "")
    with pytest.raises(aiohttp.ConnectionTimeoutError):
        asyncio.run(sky.fetch((52.1234, 13.0001, 52.9, 13.9)))
    assert "52.1234" not in sky.last_error and "13.0001" not in sky.last_error
    assert "opensky-network.org" in sky.last_error
