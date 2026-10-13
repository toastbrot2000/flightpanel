"""Datenbank: Schema anlegen und fortschreiben, kaputte Datei beiseitelegen, Panels, Zustand."""

import re
from pathlib import Path

import pytest

import flightpanel
from flightpanel.db import MIGRATIONS, Db


def test_fresh_file_gets_the_schema_once(tmp_path):
    path = str(tmp_path / "f.db")
    db = Db(path)
    assert db.one("PRAGMA user_version")[0] == len(MIGRATIONS)
    tables = {r["name"] for r in db.query("SELECT name FROM sqlite_master WHERE type = 'table'")}
    assert {"panels", "identities", "album", "days", "rare", "cache", "issues", "logo_missing", "state"} <= tables
    assert db.one("PRAGMA journal_mode")[0] == "wal"
    db.close()
    again = Db(path)                                    # zweites Öffnen: nichts doppelt
    assert again.one("PRAGMA user_version")[0] == len(MIGRATIONS)
    again.close()


def test_state_roundtrip(tmp_path):
    db = Db(str(tmp_path / "f.db"))
    assert db.state_get("fehlt") is None and db.state_get("fehlt", 7) == 7
    db.state_set("course", {"now": 249.0, "prev": None, "name": "Gänsefüßchen"})
    db.state_set("course", {"now": 69.0})
    assert db.state_get("course") == {"now": 69.0}


def test_broken_file_is_put_aside(tmp_path, caplog):
    path = tmp_path / "f.db"
    path.write_text("das ist keine Datenbank, nur Text " * 100)
    with caplog.at_level("ERROR"):
        db = Db(str(path))
    assert "beiseite" in caplog.text and (tmp_path / "f.db.broken").exists()
    db.state_set("k", 1)
    assert db.state_get("k") == 1


def test_transaction_is_all_or_nothing(tmp_path):
    db = Db(str(tmp_path / "f.db"))
    with pytest.raises(RuntimeError):
        with db.transaction():
            db.state_set("a", 1)
            raise RuntimeError("mittendrin")
    assert db.state_get("a") is None
    with db.transaction():
        db.state_set("a", 2)
    assert db.state_get("a") == 2


def test_panel_rows(tmp_path):
    db = Db(str(tmp_path / "f.db"))
    db.panel_add("home", settings={"HOME_LAT": "52.5"})
    db.panel_add("anna", name="Anna")
    with pytest.raises(ValueError, match="gibt es schon"):
        db.panel_add("home")
    assert [p["id"] for p in db.panel_list()] == ["home", "anna"]
    assert db.panel_get("home")["settings"] == {"HOME_LAT": "52.5"} and db.panel_get("nope") is None
    db.panel_set_settings("anna", {"VIEW_FROM": "110"})
    anna = db.panel_get("anna")
    assert anna["settings"] == {"VIEW_FROM": "110"} and anna["updated"] >= anna["created"]
    db.panel_remove("anna")
    assert db.panel_get("anna") is None
    with pytest.raises(ValueError, match="gibt es nicht"):
        db.panel_set_settings("anna", {})


def test_backup_copies_everything(tmp_path):
    db = Db(str(tmp_path / "f.db"))
    db.state_set("k", "v")
    db.backup(str(tmp_path / "copy.db"))
    assert Db(str(tmp_path / "copy.db")).state_get("k") == "v"


def test_a_second_connection_sees_commits(tmp_path):
    """panels.py schreibt, während der Server läuft: beide sehen dieselbe Datei."""
    path = str(tmp_path / "f.db")
    server, cli = Db(path), Db(path)
    cli.panel_add("home", settings={"HOME_LAT": "1"})
    assert server.panel_get("home")["settings"] == {"HOME_LAT": "1"}
    with server.transaction():
        server.state_set("x", 1)
    assert cli.state_get("x") == 1


def test_only_db_module_talks_sqlite():
    pkg = Path(flightpanel.__file__).parent
    offenders = sorted(p.relative_to(pkg).as_posix() for p in pkg.rglob("*.py")
                       if p.name != "db.py"
                       and re.search(r"^\s*(import|from)\s+sqlite3\b", p.read_text(encoding="utf-8"), re.M))
    assert offenders == []
