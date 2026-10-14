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
    assert db.aside is None                             # nichts war kaputt
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


@pytest.fixture
def stamp(monkeypatch):
    """Die Zeit im Namen einer beiseitegelegten Datei, fest: Sonst hinge der Test an der Minute."""
    monkeypatch.setattr("flightpanel.db._stamp", lambda: "20261014-0312")
    return "20261014-0312"


def test_broken_file_is_put_aside_under_a_name_with_the_time(tmp_path, caplog, stamp):
    """Die kaputte Datei liegt danach unverändert unter einem Namen mit Zeit daneben; der Start ist leer, aber
    arbeitsfähig. db.aside sagt, wohin sie kam und warum."""
    path = tmp_path / "f.db"
    text = "das ist keine Datenbank, nur Text " * 100
    path.write_text(text)
    with caplog.at_level("ERROR"):
        db = Db(str(path))
    aside = tmp_path / f"f.db.broken-{stamp}"
    assert aside.read_text() == text
    assert db.aside.file == aside.name and "not a database" in db.aside.error
    assert "beiseite" in caplog.text and aside.name in caplog.text
    db.state_set("k", 1)
    assert db.state_get("k") == 1


def test_a_second_incident_does_not_overwrite_the_first_copy(tmp_path, stamp):
    """Zwei Schäden mit demselben Namen (zwei Starts in einer Minute): Die erste Kopie bleibt, die zweite bekommt
    eine Nummer. Sonst wäre mit dem zweiten Schaden auch der Rest des ersten Albums weg."""
    path = tmp_path / "f.db"
    path.write_text("erster Schaden " * 100)
    Db(str(path)).close()
    path.write_text("zweiter Schaden " * 100)
    db = Db(str(path))
    assert (tmp_path / f"f.db.broken-{stamp}").read_text().startswith("erster Schaden")
    assert (tmp_path / f"f.db.broken-{stamp}-2").read_text().startswith("zweiter Schaden")
    assert db.aside.file == f"f.db.broken-{stamp}-2"


def test_the_files_next_to_a_broken_database_go_aside_with_it(tmp_path, stamp):
    """-wal und -shm gehören zur Datei und ziehen mit um. Das Beiseitelegen allein, ohne SQLite dazwischen: Beim
    Öffnen räumt SQLite ein ungültiges -wal selbst weg, bevor der Fehler bei Db ankommt."""
    path = tmp_path / "f.db"
    for suffix in ("", "-wal", "-shm"):
        (tmp_path / f"f.db{suffix}").write_text(f"Inhalt{suffix}")
    db = Db.__new__(Db)                                 # ohne __init__: nur path wird gebraucht
    db.path = str(path)
    aside = db._put_aside(Exception("kaputt"))
    assert aside.file == f"f.db.broken-{stamp}" and aside.error == "kaputt"
    for suffix in ("", "-wal", "-shm"):
        assert (tmp_path / f"f.db.broken-{stamp}{suffix}").read_text() == f"Inhalt{suffix}"
        assert not (tmp_path / f"f.db{suffix}").exists()


def test_the_log_names_the_nightly_copy_as_the_way_back(tmp_path, caplog, stamp):
    """Die nächtliche Kopie (App.dream) ist der Weg zurück: Das Log sagt, welche Datei und was damit zu tun ist.
    Die Kopie selbst fasst das Beiseitelegen nicht an."""
    path = tmp_path / "f.db"
    path.write_text("kaputt " * 100)
    (tmp_path / "f.db.bak").write_bytes(b"die Kopie der Vornacht")
    with caplog.at_level("ERROR"):
        db = Db(str(path))
    assert "f.db.bak" in caplog.text and "nächtliche Kopie" in caplog.text
    assert "f.db.bak" in db.aside.way_back and "als f.db kopieren" in db.aside.way_back
    assert (tmp_path / "f.db.bak").read_bytes() == b"die Kopie der Vornacht"


def test_the_log_says_when_there_is_no_nightly_copy_yet(tmp_path, caplog, stamp):
    path = tmp_path / "f.db"
    path.write_text("kaputt " * 100)
    with caplog.at_level("ERROR"):
        db = Db(str(path))
    assert "gibt es nicht" in db.aside.way_back and "f.db.bak" in db.aside.way_back
    assert "gibt es nicht" in caplog.text


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
    assert not list(tmp_path.glob("f.db.broken*"))
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


def test_after_commit_runs_once_the_transaction_is_committed(tmp_path):
    path = str(tmp_path / "f.db")
    db, other = Db(path), Db(path)
    seen = []
    with db.transaction():
        db.state_set("a", 1)
        db.after_commit(lambda: seen.append(other.state_get("a")))
        assert seen == []                               # erst nach dem COMMIT
    assert seen == [1]                                  # und dann ist es für andere Verbindungen sichtbar
    with db.transaction():
        db.state_set("a", 2)
    assert seen == [1]                                  # einmal angemeldet, einmal ausgeführt


def test_after_commit_is_dropped_on_rollback(tmp_path):
    db = Db(str(tmp_path / "f.db"))
    ran = []
    with pytest.raises(RuntimeError):
        with db.transaction():
            db.after_commit(lambda: ran.append("zurückgerollt"))
            raise RuntimeError("mittendrin")
    db.after_commit(lambda: ran.append("sofort"))       # danach gilt außerhalb wieder: sofort
    with db.transaction():
        db.state_set("a", 1)                            # und eine spätere Transaktion führt nichts Altes aus
    assert ran == ["sofort"]


def test_after_commit_is_dropped_when_the_commit_itself_fails(tmp_path):
    db = Db(str(tmp_path / "f.db"))
    ran = []
    # aufgeschobener Fremdschlüssel: nicht schon eine Anweisung scheitert, erst das COMMIT
    with pytest.raises(Exception, match="FOREIGN KEY"):
        with db.transaction():
            db.after_commit(lambda: ran.append("COMMIT gescheitert"))
            db.execute("PRAGMA defer_foreign_keys = ON")
            db.execute("INSERT INTO album (panel_id, brand, first, last) VALUES ('gibt-es-nicht', 'X', 1, 1)")
    db.after_commit(lambda: ran.append("sofort"))
    with db.transaction():
        db.state_set("a", 1)
    assert ran == ["sofort"]


def test_after_commit_outside_a_transaction_runs_at_once(tmp_path):
    db = Db(str(tmp_path / "f.db"))
    ran = []
    db.after_commit(lambda: ran.append(1))              # dort ist jede Anweisung schon für sich festgeschrieben
    assert ran == [1]


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


@pytest.mark.parametrize("name", ["f.db", "./f.db", "f.db-wal", "f.db-shm"])
def test_backup_refuses_the_database_itself_and_its_side_files(tmp_path, name):
    """Eine Kopie auf die Datenbank selbst oder auf ihr -wal/-shm zerstörte sie, statt sie zu sichern."""
    db = Db(str(tmp_path / "f.db"))
    db.state_set("k", "v")
    with pytest.raises(ValueError, match="Datenbank selbst"):
        db.backup(str(tmp_path / name))
    assert db.state_get("k") == "v" and db.one("PRAGMA integrity_check")[0] == "ok"


def test_backup_replaces_an_older_copy_only_once_the_new_one_is_complete(tmp_path):
    """Die nächtliche Kopie überschreibt die der Vornacht: Eine kaputte alte hält sie nicht auf, und eine
    abgebrochene neue lässt die alte stehen. Reste bleiben keine liegen."""
    db = Db(str(tmp_path / "f.db"))
    target = tmp_path / "copy.db"
    target.write_text("die Kopie von gestern, inzwischen kaputt " * 100)
    db.state_set("k", "heute")
    db.backup(str(target))
    db.close()
    with pytest.raises(Exception, match="closed"):
        db.backup(str(target))                          # scheitert mittendrin
    assert sorted(p.name for p in tmp_path.iterdir()) == ["copy.db", "f.db"]
    assert Db(str(target)).state_get("k") == "heute"


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
