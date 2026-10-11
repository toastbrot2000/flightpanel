"""Auswahl der Leerlauf-Karten: Dringendes zuerst, sonst gewichteter Zufall."""

from collections import Counter

from PIL import Image

from conftest import make_cfg
from flightpanel.idle import Card, Hit, IdleScreen
from flightpanel.model import DisplayModel

ACTIVE = Hit()


def card(cid, hit=ACTIVE, weight=1.0, live=False, draw=None):
    return Card(cid, lambda ctx, h=hit: h, draw or (lambda *a: None), weight=weight, live=live)


def model(**kw):
    return DisplayModel(source="test", **kw)


def test_urgent_breaks_through():
    idle = IdleScreen(make_cfg(), [card("clock"), card("a"), card("b"), card("now", Hit(urgent=True))])
    for slot in range(20):
        assert idle.pick(idle.context(model(), slot * 12 + 1))[0] == "now"


def test_several_urgent_take_turns():
    idle = IdleScreen(make_cfg(), [card("x", Hit(urgent=True)), card("y", Hit(urgent=True)), card("clock")])
    picks = {idle.pick(idle.context(model(), s * 12))[0] for s in range(4)}
    assert picks == {"x", "y"}


def test_same_slot_same_card_and_no_direct_repeat():
    idle = IdleScreen(make_cfg(), [card("clock")] + [card(c) for c in "abcd"])
    prev = None
    for slot in range(200):
        first = idle.pick(idle.context(model(), slot * 12))[0]
        for dt in (2, 5, 11):
            assert idle.pick(idle.context(model(), slot * 12 + dt))[0] == first
        assert first != prev
        prev = first


def test_clock_share_and_weights():
    idle = IdleScreen(make_cfg(), [card("clock"), card("heavy", weight=5), card("light", weight=0.5),
                                   card("mid", weight=1)])
    n = Counter(idle.pick(idle.context(model(), s * 12))[0] for s in range(3000))
    assert 0.2 < n["clock"] / 3000 < 0.4
    assert n["heavy"] > n["mid"] > n["light"] > 0


def test_cards_off_and_inactive_cards():
    idle = IdleScreen(make_cfg(cards_off=frozenset({"a", "b"})), [card("clock"), card("a"), card("b"), card("c", None)])
    assert set(idle.candidates(idle.context(model(), 0))) == {"clock"}


def test_everything_off_leaves_the_panel_dark():
    drawn = []
    cards = [card("clock", draw=lambda *a: drawn.append("clock")),
             card("pigeon", draw=lambda *a: drawn.append("pigeon"))]
    cards[1].tier = 4
    idle = IdleScreen(make_cfg(cards_off=frozenset({"clock"})), cards)
    # Spielereien nur, wenn gar nichts los ist; sonst auch ohne Uhr nichts
    busy = model(departures=[{"airline": "easyJet", "phase": "rollt"}])
    assert idle.pick(idle.context(busy, 0)) is None
    img = Image.new("RGB", (128, 32))
    assert idle.draw(img, busy, 0) is None and img.getbbox() is None and drawn == []
    assert idle.pick(idle.context(model(), 3600))[0] == "pigeon"   # gar nichts los: Spielerei
    off = IdleScreen(make_cfg(cards_off=frozenset({"clock", "pigeon"})), cards)
    assert off.draw(img, model(), 3600) is None and off.last_pick is None


def test_live_cards_skipped_when_stale():
    idle = IdleScreen(make_cfg(), [card("clock"), card("radar", live=True)])
    assert "radar" in idle.candidates(idle.context(model(), 0))
    assert "radar" not in idle.candidates(idle.context(model(stale=True), 0))
    assert "radar" not in idle.candidates(idle.context(DisplayModel(source="startup"), 0))


def test_broken_card_falls_back_to_clock():
    drawn = []

    def boom(ctx):
        raise ValueError("kaputt")

    def bad_draw(*a):
        raise ValueError("kaputt")

    cards = [Card("clock", lambda c: Hit(), lambda *a: drawn.append("clock")),
             Card("cond_err", boom, lambda *a: None),
             Card("draw_err", lambda c: Hit(), bad_draw)]
    idle = IdleScreen(make_cfg(idle_clock_share=0), cards)
    assert "cond_err" not in idle.candidates(idle.context(model(), 0))
    img = Image.new("RGB", (128, 32))
    assert idle.draw(img, model(), 0, force="draw_err") == "clock"
    assert drawn == ["clock"]


def test_status_lists_every_card():
    idle = IdleScreen(make_cfg(cards_off=frozenset({"b"})), [card("clock"), card("a", Hit({"x": 1})), card("b")])
    st = idle.status(model(), 0)
    states = {c["id"]: c["state"] for c in st["cards"]}
    assert states == {"clock": "active", "a": "active", "b": "off"}
    assert next(c for c in st["cards"] if c["id"] == "a")["data"] == {"x": 1}
