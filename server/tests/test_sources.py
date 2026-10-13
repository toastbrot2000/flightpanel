"""Flugdaten von adsb.lol: ein hängender Server kostet keinen Abruf, ein Rate-Limit
wird nicht überrannt."""

import asyncio

import pytest

from flightpanel.adsblol import AdsbLol

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

    def get(self, url, timeout=None):
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
