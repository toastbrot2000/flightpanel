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


def test_tools_leave_a_broken_file_alone(tmp_path):
    """panels.py und fetch_logos.py laufen neben dem Server, der die Datei noch offen hat: Sie legen nichts beiseite,
    sondern brechen mit dem Fehler ab. Sonst schriebe der Server in die umbenannte Datei, das Werkzeug in eine neue."""
    path = tmp_path / "f.db"
    text = "das ist keine Datenbank, nur Text " * 100
    path.write_text(text)
    with pytest.raises(Exception, match="not a database"):
        Db(str(path), put_aside=False)
    assert path.read_text() == text
    assert sorted(p.name for p in tmp_path.iterdir()) == ["f.db"]          # kein .broken, keine Reste daneben


def test_a_failing_migration_leaves_the_file_alone(tmp_path, monkeypatch):
    """Scheitert eine Migration, bleibt die Datei liegen und der Start schlägt laut fehl, statt leer zu starten."""
    path = str(tmp_path / "f.db")
    db = Db(path)
    db.panel_add("home", name="Heim")
    db.close()
    broken = "CREATE TABLE halb (a);\nUPDATE panels SET name = NULL;"      # die zweite Anweisung scheitert an "home"
    monkeypatch.setattr("flightpanel.db.MIGRATIONS", [*MIGRATIONS, broken])
    with pytest.raises(Exception, match="NOT NULL"):
        Db(path)
    assert not (tmp_path / "f.db.broken").exists()
    monkeypatch.undo()
    db = Db(path)
    assert db.panel_get("home")["name"] == "Heim" and db.one("PRAGMA user_version")[0] == len(MIGRATIONS)
    assert db.one("SELECT 1 FROM sqlite_master WHERE name = 'halb'") is None      # alles oder nichts


def test_a_slower_opener_does_not_migrate_twice(tmp_path, monkeypatch):
    """Server und panels.py öffnen eine neue Datei gleichzeitig: Der langsamere hat den Stand gelesen, bevor der
    schnellere fertig war, und darf dessen Migration trotzdem nicht ein zweites Mal ausführen."""
    path = str(tmp_path / "f.db")
    script = "CREATE TABLE IF NOT EXISTS runs (n);\nINSERT INTO runs VALUES (1);"     # ein zweiter Lauf ginge durch
    monkeypatch.setattr("flightpanel.db.MIGRATIONS", [*MIGRATIONS, script])
    Db(path).close()                                    # der schnellere Öffner migriert bis zum Ende
    stale, real = iter([1]), flightpanel.db._version
    # der langsamere sieht beim ersten Blick noch den alten Stand 1, danach den wahren
    monkeypatch.setattr("flightpanel.db._version", lambda conn: next(stale, None) or real(conn))
    db = Db(path)
    assert [r["n"] for r in db.query("SELECT n FROM runs")] == [1]
    assert db.one("PRAGMA user_version")[0] == len(MIGRATIONS) + 1


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


def test_a_failed_commit_leaves_the_connection_usable(tmp_path):
    """COMMIT kann scheitern (hier: aufgeschobener Fremdschlüssel); die nächste Transaktion muss trotzdem gehen."""
    db = Db(str(tmp_path / "f.db"))
    with pytest.raises(Exception, match="FOREIGN KEY"):
        with db.transaction():
            db.execute("PRAGMA defer_foreign_keys = ON")
            db.execute("INSERT INTO album (panel_id, brand, first, last) VALUES ('gibt-es-nicht', 'X', 1, 1)")
    with db.transaction():
        db.state_set("a", 1)
    assert db.state_get("a") == 1 and db.one("SELECT 1 FROM album") is None


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
