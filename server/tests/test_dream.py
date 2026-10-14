"""Nachts aufräumen ("träumen"), damit der Server ein Jahr am Stück läuft: Einmaliges
in /issues vergessen, Abgelaufenes aus dem Cache, volles /issues macht Platz."""

import asyncio
import os
import time
from datetime import datetime

import pytest

from conftest import make_cfg, state
from flightpanel import cache as cache_mod
from flightpanel import issues as issues_mod
from flightpanel.cache import Cache
from flightpanel.db import Db
from flightpanel.issues import Issues
from flightpanel.model import DisplayModel

DAY = 24 * 3600


def clock(monkeypatch, module, t0=1_800_000_000.0):
    now = [t0]
    monkeypatch.setattr(module.time, "time", lambda: now[0])
    return now


def test_one_offs_go_after_a_week_recurring_after_a_month(tmp_path, monkeypatch):
    now = clock(monkeypatch, issues_mod)
    iss = Issues(Db(str(tmp_path / "i.db")))
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
    iss.save()
    assert Issues(iss.db).items == {}                          # auch aus der Datenbank verschwunden


def test_full_issue_list_makes_room_for_news(tmp_path, monkeypatch):
    monkeypatch.setattr(issues_mod, "MAX_KEYS", 3)
    now = clock(monkeypatch, issues_mod)
    iss = Issues(Db(str(tmp_path / "i.db")))
    for i in range(3):
        iss.note("route_implausible", f"X{i}")
        now[0] += 60
    iss.note("exception", "ValueError main.py:1")
    keys = {it["key"] for items in iss.report()["issues"].values() for it in items}
    assert keys == {"X1", "X2", "ValueError main.py:1"}       # das am längsten Ungesehene ist raus


def test_issues_survive_a_restart_and_clear_starts_over(tmp_path, monkeypatch):
    now = clock(monkeypatch, issues_mod)
    db = Db(str(tmp_path / "i.db"))
    iss = Issues(db)
    iss.note("unknown_operator", "SFG", "SFG55D", adsbdb="Sun Fly")
    now[0] += 60
    iss.note("unknown_operator", "SFG", "SFG12A")
    iss.save()
    again = Issues(db)
    it = again.items["unknown_operator:SFG"]
    assert it["seen"] == 2 and it["callsigns"] == ["SFG55D", "SFG12A"] and again.since == iss.since
    again.clear()
    again.save()
    assert Issues(db).items == {} and Issues(db).since == now[0]


def test_issues_take_over_the_old_json(tmp_path, monkeypatch):
    now = clock(monkeypatch, issues_mod)
    db = Db(str(tmp_path / "i.db"))
    iss = Issues(db)
    iss.load_legacy({"since": now[0] - 5 * DAY, "items": {"exception:x": {
        "kind": "exception", "key": "x", "seen": 3, "days": 2, "first": now[0] - DAY, "last": now[0],
        "callsigns": []}}})
    iss.save()
    again = Issues(db)
    assert again.since == now[0] - 5 * DAY and again.items["exception:x"]["seen"] == 3


def test_issues_keep_their_changes_when_the_save_fails(tmp_path, monkeypatch):
    """App.save() schreibt alle Speicher in einer Transaktion; scheitert sie, bleibt alles
    für den nächsten Lauf vorgemerkt (Db.after_commit)."""
    clock(monkeypatch, issues_mod)
    db = Db(str(tmp_path / "i.db"))
    iss = Issues(db)
    iss.note("exception", "x")
    with pytest.raises(RuntimeError):
        with db.transaction():
            iss.save()
            raise RuntimeError("Platte voll")
    assert Issues(db).items == {}                             # nichts geschrieben
    iss.save()
    assert "exception:x" in Issues(db).items                  # beim nächsten Mal


def test_forgotten_and_evicted_issues_leave_the_database(tmp_path, monkeypatch):
    """Was schon in der Tabelle steht, muss beim Vergessen und beim Verdrängen auch dort verschwinden,
    sonst käme es mit dem nächsten Start zurück."""
    monkeypatch.setattr(issues_mod, "MAX_KEYS", 3)
    now = clock(monkeypatch, issues_mod)
    db = Db(str(tmp_path / "i.db"))
    iss = Issues(db)
    for i in range(3):
        iss.note("route_implausible", f"X{i}")
        now[0] += 60
    iss.save()
    iss.note("exception", "ValueError main.py:1")             # verdrängt X0, das am längsten Ungesehene
    iss.save()
    assert set(Issues(db).items) == {"route_implausible:X1", "route_implausible:X2", "exception:ValueError main.py:1"}
    now[0] += 10 * DAY
    assert iss.forget(now[0]) == 2                            # X1 und X2: einmalig, über eine Woche her
    iss.note("route_implausible", "X1")                       # X1 taucht vor dem nächsten Speichern wieder auf
    iss.save()
    assert set(Issues(db).items) == {"exception:ValueError main.py:1", "route_implausible:X1"}


def test_issues_noted_after_a_clear_are_kept_and_updated_by_later_saves(tmp_path, monkeypatch):
    clock(monkeypatch, issues_mod)
    db = Db(str(tmp_path / "i.db"))
    iss = Issues(db)
    iss.note("exception", "alt")
    iss.save()
    iss.clear()
    iss.save()                                                # "alt" ist weg
    iss.note("exception", "a")
    iss.save()
    iss.note("exception", "a")
    iss.note("exception", "b")
    iss.save()                                                # löscht "a" nicht wieder und schreibt es neu
    again = Issues(db)
    assert set(again.items) == {"exception:a", "exception:b"} and again.items["exception:a"]["seen"] == 2


def test_issues_forget_what_they_wrote_once_the_transaction_committed(tmp_path, monkeypatch):
    clock(monkeypatch, issues_mod)
    db = Db(str(tmp_path / "i.db"))
    iss = Issues(db)
    iss.note("exception", "alt")
    iss.save()
    iss.clear()                                               # Tabelle leeren und den Beginn neu setzen
    iss.note("exception", "x")
    with db.transaction():
        iss.save()
    changes = db.conn.total_changes
    iss.save()
    assert db.conn.total_changes == changes                   # nichts mehr vorgemerkt, nichts geschrieben


def test_issues_keep_what_changed_after_saving_but_before_the_commit(tmp_path, monkeypatch):
    monkeypatch.setattr(issues_mod, "MAX_KEYS", 2)
    now = clock(monkeypatch, issues_mod)
    db = Db(str(tmp_path / "i.db"))
    iss = Issues(db)
    for key in ("a", "b"):
        iss.note("exception", key)
        now[0] += 60
    iss.save()
    iss.note("exception", "c")                                # verdrängt a
    with db.transaction():
        iss.save()
        iss.note("exception", "d")                            # kommt erst nach dem Schreiben dazu, verdrängt b
    iss.save()
    assert set(Issues(db).items) == {"exception:c", "exception:d"}


def test_issues_ignore_an_old_json_that_is_no_object(tmp_path, monkeypatch):
    clock(monkeypatch, issues_mod)
    iss = Issues(Db(str(tmp_path / "i.db")))
    since = iss.since
    iss.load_legacy(["kaputt"])
    assert iss.items == {} and iss.since == since


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


def test_cache_stays_unsaved_when_the_surrounding_transaction_fails(tmp_path, monkeypatch):
    """App.save() schreibt alle Speicher in einer Transaktion. Scheitert sie, steht davon nichts in der Datenbank:
    Der Zwischenspeicher muss alles für das nächste Speichern vorgemerkt lassen."""
    db = Db(str(tmp_path / "c.db"))
    clock(monkeypatch, cache_mod)
    c = Cache(db)
    c.set("route:EZY1", {"origin": "LGW"}, ttl=3600)
    with pytest.raises(RuntimeError):
        with db.transaction():
            c.save()
            raise RuntimeError("ein anderer Speicher scheitert")
    assert db.query("SELECT key FROM cache") == []
    c.save()                                            # später ohne Transaktion
    assert [r["key"] for r in db.query("SELECT key FROM cache")] == ["route:EZY1"]


def test_cache_keeps_what_was_set_after_saving_but_before_the_commit(tmp_path, monkeypatch):
    db = Db(str(tmp_path / "c.db"))
    clock(monkeypatch, cache_mod)
    c = Cache(db)
    c.set("route:A", {"origin": "LGW"}, ttl=3600)
    with db.transaction():
        c.save()
        c.set("route:B", {"origin": "BER"}, ttl=3600)   # kommt erst nach dem Schreiben dazu
    c.save()
    assert [r["key"] for r in db.query("SELECT key FROM cache ORDER BY key")] == ["route:A", "route:B"]


def test_cache_forgets_what_it_wrote_once_the_transaction_committed(tmp_path, monkeypatch):
    db = Db(str(tmp_path / "c.db"))
    clock(monkeypatch, cache_mod)
    c = Cache(db)
    c.set("route:EZY1", {"origin": "LGW"}, ttl=3600)
    with db.transaction():
        c.save()
    changes = db.conn.total_changes
    c.save()
    assert db.conn.total_changes == changes             # nichts mehr vorgemerkt, also nichts noch einmal geschrieben


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


def test_dream_leaves_a_copy_of_the_database_with_the_album(tmp_path):
    """Die Datenbank liegt auf dem Laptop in einem synchronisierten Ordner und sonst ohne Sicherung: Jede Nacht eine
    stimmige Kopie flightpanel.db.bak, die die der Vornacht ersetzt. /health sagt, von wann sie ist."""
    from flightpanel.main import App

    async def run():
        app = App(make_cfg(mock=True, data_dir=str(tmp_path), metar=False, iss=False, logo_fetch=False), env={})
        try:
            now = time.time()
            app.stats.observe([state(cs="EZY1")], DisplayModel(), now)
            app.save()
            bak = tmp_path / "flightpanel.db.bak"
            bak.write_text("die Kopie der Vornacht")
            assert app.health()["database"]["copy"] is not None
            app.dream(now)
            copy = Db(str(bak), put_aside=False)
            assert [tuple(r) for r in copy.query("SELECT panel_id, brand, first FROM album")] == [
                ("home", "EZY", now)]                   # ohne airlines.json in tmp_path: ein Exot unter seinem Code
            copy.close()
            assert app.last_dream["copy"] == "flightpanel.db.bak"
            assert app.health()["database"]["copy"] == time.strftime(
                "%Y-%m-%d %H:%M", time.localtime(os.path.getmtime(bak)))
        finally:
            await app.session.close()

    asyncio.run(run())


def test_a_failing_copy_is_logged_and_noted_and_the_night_goes_on(tmp_path, monkeypatch, caplog):
    from flightpanel.main import App

    def full(self, dest):
        raise OSError("Platte voll")

    async def run():
        app = App(make_cfg(mock=True, data_dir=str(tmp_path), metar=False, iss=False, logo_fetch=False), env={})
        try:
            now = time.time()
            assert app.health()["database"]["copy"] is None               # noch keine Nacht
            app.cache.data["route:GONE"] = (now - 1, {})
            monkeypatch.setattr(Db, "backup", full)
            with caplog.at_level("WARNING"):
                app.dream(now)
            assert "route:GONE" not in app.cache.data and app.last_dream["cache_expired"] == 1
            assert "Platte voll" in caplog.text and "flightpanel.db.bak" in caplog.text
            assert app.issues.items["backup:flightpanel.db.bak"]["example"] == {"error": "Platte voll"}
            assert app.last_dream["copy"] == "Fehler: Platte voll"
        finally:
            await app.session.close()

    asyncio.run(run())
