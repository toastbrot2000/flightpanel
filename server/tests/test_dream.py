"""Nachts aufräumen ("träumen"), damit der Server ein Jahr am Stück läuft: Einmaliges
in /issues vergessen, Abgelaufenes aus dem Cache, volles /issues macht Platz."""

import asyncio
import time
from datetime import datetime

from conftest import make_cfg
from flightpanel import cache as cache_mod
from flightpanel import issues as issues_mod
from flightpanel.cache import Cache
from flightpanel.db import Db
from flightpanel.issues import Issues

DAY = 24 * 3600


def clock(monkeypatch, module, t0=1_800_000_000.0):
    now = [t0]
    monkeypatch.setattr(module.time, "time", lambda: now[0])
    return now


def test_one_offs_go_after_a_week_recurring_after_a_month(tmp_path, monkeypatch):
    now = clock(monkeypatch, issues_mod)
    iss = Issues(str(tmp_path / "i.json"))
    iss.note("route_implausible", "EJU9047", "EJU9047")      # ein Flug an einem Abend
    iss.note("unknown_operator", "SFG", "SFG55D")
    iss.note("exception", "KeyError build.py:12")            # Fehler im Code: einmal, aber wichtig
    now[0] += DAY
    iss.note("unknown_operator", "SFG", "SFG12A")            # am nächsten Tag wieder: wiederkehrend
    now[0] += 8 * DAY
    assert iss.forget(now[0]) == 1                            # nur der einmalige Routenfehler
    assert set(iss.report()["counts"]) == {"unknown_operator", "exception"}
    now[0] += 30 * DAY
    assert iss.forget(now[0]) == 2                            # 30 Tage nicht mehr aufgetreten


def test_full_issue_list_makes_room_for_news(tmp_path, monkeypatch):
    monkeypatch.setattr(issues_mod, "MAX_KEYS", 3)
    now = clock(monkeypatch, issues_mod)
    iss = Issues(str(tmp_path / "i.json"))
    for i in range(3):
        iss.note("route_implausible", f"X{i}")
        now[0] += 60
    iss.note("exception", "ValueError main.py:1")
    keys = {it["key"] for items in iss.report()["issues"].values() for it in items}
    assert keys == {"X1", "X2", "ValueError main.py:1"}       # das am längsten Ungesehene ist raus


def test_cache_drops_expired_entries_when_saving(tmp_path, monkeypatch):
    db = Db(str(tmp_path / "c.db"))
    now = clock(monkeypatch, cache_mod)
    c = Cache(db)
    c.set("route:EZY1", {"origin": "LGW"}, ttl=60)
    c.set("ac:3c0001", {"type": "A320"}, ttl=3600)
    now[0] += 120
    c.save()
    assert len(c) == 1 and [r["key"] for r in db.query("SELECT key FROM cache")] == ["ac:3c0001"]
    assert Cache(db).get("ac:3c0001") == {"type": "A320"}      # neu geladen: derselbe Inhalt


def test_cache_takes_over_the_old_json(tmp_path, monkeypatch):
    now = clock(monkeypatch, cache_mod)
    db = Db(str(tmp_path / "c.db"))
    c = Cache(db)
    c.load_legacy({"route:A": [now[0] + 100, {"origin": "X"}], "route:B": [now[0] - 1, {}], "kaputt": "x"})
    c.save()
    assert Cache(db).get("route:A") == {"origin": "X"} and Cache(db).get("route:B") is None and len(c) == 1


def test_cache_overwrites_a_saved_entry(tmp_path, monkeypatch):
    db = Db(str(tmp_path / "c.db"))
    now = clock(monkeypatch, cache_mod)
    c = Cache(db)
    c.set("route:EZY1", {}, ttl=60)                     # nichts gefunden: nur kurz merken
    c.save()
    c.set("route:EZY1", {"origin": "LGW"}, ttl=3600)    # später doch gefunden
    c.save()
    now[0] += 120                                       # die erste Frist wäre jetzt um
    assert Cache(db).get("route:EZY1") == {"origin": "LGW"}


def test_cache_forgets_saved_entries_once_they_expire(tmp_path, monkeypatch):
    db = Db(str(tmp_path / "c.db"))
    now = clock(monkeypatch, cache_mod)
    c = Cache(db)
    c.set("route:EZY1", {"origin": "LGW"}, ttl=60)
    c.set("ac:3c0001", {"type": "A320"}, ttl=3600)
    c.save()
    now[0] += 120
    assert len(Cache(db)) == 1                          # beim Laden fällt Abgelaufenes weg
    assert c.prune() == 1 and [r["key"] for r in db.query("SELECT key FROM cache")] == ["ac:3c0001"]


def test_cache_saves_after_an_unsaved_entry_expired_on_lookup(tmp_path, monkeypatch):
    """get() wirft Abgelaufenes aus dem Speicher. War es noch ungespeichert, darf save() nicht darüber stolpern:
    Sonst scheiterte jedes weitere Speichern, bis jemand denselben Schlüssel wieder setzt."""
    db = Db(str(tmp_path / "c.db"))
    now = clock(monkeypatch, cache_mod)
    c = Cache(db)
    c.set("route:EZY1", {"origin": "LGW"}, ttl=60)
    c.set("ac:3c0001", {"type": "A320"}, ttl=3600)
    now[0] += 120
    assert c.get("route:EZY1") is None                  # abgelaufen, bevor es je gespeichert wurde
    c.save()
    assert [r["key"] for r in db.query("SELECT key FROM cache")] == ["ac:3c0001"]


def test_dream_once_a_night_at_the_start_of_the_quiet_hours():
    from flightpanel.main import dream_due

    def at(*a):
        return datetime(2026, 10, *a).timestamp()

    midnight = make_cfg(quiet_hours=(0, 6 * 60 + 30))
    assert dream_due(midnight, None, at(12, 0, 5))
    assert not dream_due(midnight, "2026-10-12", at(12, 15, 0))     # heute schon
    late = make_cfg(quiet_hours=(23 * 60, 6 * 60))
    assert not dream_due(late, "2026-10-11", at(12, 15, 0))
    assert dream_due(late, "2026-10-11", at(12, 23, 10))
    never = make_cfg(quiet_hours=(0, 0))                             # ohne Ruhezeit: ab 4 Uhr
    assert not dream_due(never, "2026-10-11", at(12, 3, 0))
    assert dream_due(never, "2026-10-11", at(12, 4, 10))


def test_dream_tidies_up_and_says_so_in_health(tmp_path):
    from flightpanel.main import App

    async def run():
        app = App(make_cfg(mock=True, data_dir=str(tmp_path), metar=False, iss=False, logo_fetch=False))
        try:
            now = time.time()
            app.issues.items["route_implausible:OLD1"] = {
                "kind": "route_implausible", "key": "OLD1", "seen": 9, "first": now - 10 * DAY,
                "last": now - 10 * DAY, "callsigns": ["OLD1"]}
            app.cache.data["route:GONE"] = (now - 1, {})
            app.dream(now)
            assert app.issues.items == {} and "route:GONE" not in app.cache.data
            assert app.health()["dream"]["issues_forgotten"] == 1
        finally:
            await app.session.close()

    asyncio.run(run())
