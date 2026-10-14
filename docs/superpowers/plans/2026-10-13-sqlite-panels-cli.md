# SQLite-Speicher, Panel-Einstellungen und `panels`-CLI — Implementation Plan (Schritt 1)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the JSON state files with one SQLite database, split `Config` into server settings (`.env`) and panel settings (database, set by a new `panels` CLI), import existing JSON state once, and harden the container — while the single LAN panel behaves exactly as today.

**Architecture:** `db.py` is the only module that speaks `sqlite3`; the existing stores (`Cache`, `Issues`, `Stats`, `LogoStore`, `astro.Iss`) keep their in-memory working sets and public APIs but load from and save to the database (`App.save()` writes everything dirty in one transaction once a minute and on stop). `Config` fields get a `panel=(Gruppe, Erklärung)` marker; `Config.from_env` reads only server fields, `Config.for_panel(server, settings)` applies a panel's raw-string settings with the same parsers. `App` builds the LAN panel's config from the database row `home` (created on first start, seeded once from a legacy `.env`). The CLI `python -m flightpanel.panels` edits panel rows with the same validation.

**Tech Stack:** Python 3.12, stdlib `sqlite3` (WAL), aiohttp (unchanged), pytest + ruff inside the Docker image `flightpanel-ci-dev`.

**Spec:** `docs/superpowers/specs/2026-10-13-freunde-multi-tenant-design.md` — sections "Principles", "Server §1 SQLite store", "§2 Shared layer" (only the `Config` parts), "§6 CLI", "Infrastructure → Container hardening", "Order → step 1". Later steps (multi-panel polling, public listener, firmware) are **not** in this plan.

## Global Constraints

- No new Python dependencies. Only `server/flightpanel/db.py` may `import sqlite3` (test enforces it). Every SQL statement is parameterized (ruff `S608` enabled).
- Code comments, docstrings, commit messages and docs are **German**; identifiers English. Line length 120. Tests must pass with `filterwarnings = error::DeprecationWarning:flightpanel.*`.
- Behavior of the single LAN panel stays identical: rendering code is untouched; `tools/screenshots.py` output (docs/images) must not change.
- Nothing location-specific or personal goes into the repo: no coordinates, no `.env`, no database files (`.gitignore` gets `data/flightpanel.db*`, `data/*.imported`, `.claude/worktrees/`).
- Everything remote stays out of this step; the self-hoster flow is `.env` → `docker compose up` → `panels set home …`.
- Work happens in the git worktree `.claude/worktrees/freunde-sqlite` of the repository (branch `worktree-freunde-sqlite`). Never `cd` to the main checkout. Commit after every task with the trailer `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`. Do not push; merging into `main` is the final step after review.
- **Running tests** (Git Bash, from the worktree root; the image `flightpanel-ci-dev` already has pytest and ruff; `MSYS_NO_PATHCONV=1` stops Git Bash from mangling the mount path):

  ```bash
  MSYS_NO_PATHCONV=1 docker run --rm -v "$(pwd -W):/repo" -w /repo flightpanel-ci-dev sh -c "ruff check . && python -m pytest -p no:cacheprovider -q server/tests/test_db.py"
  ```

  Replace the pytest target per step; the whole suite is `python -m pytest -p no:cacheprovider -q`. Baseline before this plan: 119 passed, ruff clean.

## Review Focus

1. **A corrupt or foreign `flightpanel.db`** (sync tools, disk full, someone copied a text file there): the server must still start, empty, with the file set aside — pinned in Task 1 (`test_broken_file_is_put_aside`).
2. **An old `.env` still carrying `HOME_LAT`, `VIEW_*`, `CARD_*` after the first start**: those values must be imported exactly once, then ignored with a warning naming the CLI command, never silently applied or fatal — pinned in Task 8 (`test_env_panel_keys_are_taken_over_on_the_first_start_then_ignored`).
3. **Sloppy CLI input** (`home_lat=52.5` lowercase, `KEY=` empty, `KEY` without `=`, a server key like `DATA_DIR`): lowercase is accepted, empty resets to default, the other two fail with a message and change nothing — pinned in Task 9.
4. **Album brands that move from "unknown extra" to a listed brand** (`airlines.json` grows): the stale row must leave the database, or it would reappear after a restart — pinned in Task 4 (`test_extras_join_their_brand_once_listed`, now reopening from the database).
5. **The CLI writing while the server runs**: a second connection must see committed rows and must not deadlock the first — pinned in Task 1 (`test_a_second_connection_sees_commits`).

## File Structure

| File | Responsibility |
|---|---|
| `server/flightpanel/db.py` (new) | open/migrate the database, transactions, `state` key-value, panel rows, backup |
| `server/flightpanel/cache.py` | API unchanged; rows in `cache` |
| `server/flightpanel/issues.py` | API unchanged; rows in `issues`, `since` in `state` |
| `server/flightpanel/stats.py` | `Stats(db, panel_id, cfg, names, rare)`; rows in `album`, `days`, `rare`, panel columns; course in `state` |
| `server/flightpanel/logos.py` | `LogoStore(..., db=None)`; missing codes in `logo_missing` |
| `server/flightpanel/astro.py` | `Iss(cfg, db=None, session=None, issues=None, tle_text=None)`; TLE in `state` |
| `server/flightpanel/config.py` | `panel=` field metadata, `panel_fields()`, `is_panel_key()`, `default_text()`, `panel_settings_from_env()`, `Config.for_panel()`, `LAN_PANEL` |
| `server/flightpanel/main.py` | `App(cfg, env=None)`: database, panel row `home`, legacy import, `save()` once a minute |
| `server/flightpanel/panels.py` (new) | CLI: list, keys, show, set, unset, add, remove, backup |
| `server/flightpanel/fetch_logos.py` | opens the database so failed lookups are remembered |
| `server/tests/test_db.py`, `test_panels.py`, `test_app.py` (new); `test_stats.py`, `test_dream.py`, `test_logos.py`, `test_astro.py`, `test_config.py`, `test_cards.py`, `conftest.py` (adapted) | |
| `server/Dockerfile`, `compose.yml`, `.github/workflows/*.yml`, `.gitignore`, `pyproject.toml`, `.env.example`, `README.md`, `docs/screens.md` | hardening, CI as root, docs for the new flow |

---

### Task 1: `db.py` — database core

**Files:**
- Create: `server/flightpanel/db.py`
- Test: `server/tests/test_db.py`

**Interfaces:**
- Produces: `class Db(path)` with `execute(sql, params=())`, `executemany(sql, rows)`, `query(sql, params=()) -> list[sqlite3.Row]`, `one(sql, params=()) -> Row | None`, `transaction()` (context manager, `BEGIN IMMEDIATE` … `COMMIT`/`ROLLBACK`), `state_get(key, default=None)`, `state_set(key, value)` (JSON values), `backup(dest_path)`, `close()`, and panel rows: `panel_add(pid, name="", settings=None)`, `panel_get(pid) -> dict | None`, `panel_list() -> list[dict]`, `panel_set_settings(pid, settings: dict[str, str])`, `panel_remove(pid)`. Panel dicts have keys `id, name, settings (dict), fw_channel, created, updated, stats_since, last_obs, last_in_view`. `MIGRATIONS: list[str]`.

- [ ] **Step 1: Write the failing tests**

Create `server/tests/test_db.py`:

```python
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
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `... sh -c "python -m pytest -p no:cacheprovider -q server/tests/test_db.py"`
Expected: ImportError `No module named 'flightpanel.db'`.

- [ ] **Step 3: Write `server/flightpanel/db.py`**

```python
"""SQLite-Datenbank für alles, was sich im Betrieb ändert: DATA_DIR/flightpanel.db.

Darin: Panels mit ihren Einstellungen (config.py: Panel-Felder), je Panel Statistik und
Sammelalbum (stats.py), der Zwischenspeicher der Routen- und Flugzeugdienste (cache.py),
Auffälligkeiten (issues.py), fehlende Logos (logos.py), Bahndaten der ISS (astro.py) und
Kleinkram in "state". Mitgelieferte Daten (airlines.json ...) bleiben JSON, Logos Dateien.

Nur dieses Modul importiert sqlite3. Die Speicher halten ihren Zustand im Arbeitsspeicher
und schreiben Geändertes in App.save() (main.py) in einer Transaktion; jedes SQL ist
parametrisiert. Schema-Änderungen kommen als weiteres Skript in MIGRATIONS, PRAGMA
user_version merkt sich den Stand der Datei.

WAL, damit panels.py schreiben kann, während der Server läuft; geht das nicht
(Netzlaufwerk), tut es das normale Journal auch. Eine kaputte Datei wird als .broken
beiseitegelegt, der Server startet leer statt gar nicht."""

import json
import logging
import os
import sqlite3
import time
from contextlib import contextmanager

log = logging.getLogger("db")

MIGRATIONS = [
    """
    CREATE TABLE panels (
        id TEXT PRIMARY KEY,
        name TEXT NOT NULL DEFAULT '',
        settings TEXT NOT NULL DEFAULT '{}',   -- Panel-Einstellungen, rohe Strings wie in .env
        fw_channel TEXT NOT NULL DEFAULT 'stable',
        created REAL NOT NULL,
        updated REAL NOT NULL,
        stats_since REAL,                      -- Beginn von Statistik und Album
        last_obs REAL NOT NULL DEFAULT 0,
        last_in_view REAL NOT NULL DEFAULT 0,
        seen_at REAL,                          -- letzter Abruf des ESP32 (ab Panel-Freigabe)
        fw_version INTEGER,
        uptime_s INTEGER,
        rssi INTEGER,
        min_heap INTEGER
    );
    CREATE TABLE identities (
        kind TEXT NOT NULL,                    -- email | token
        value TEXT NOT NULL,
        panel_id TEXT NOT NULL REFERENCES panels(id) ON DELETE CASCADE,
        PRIMARY KEY (kind, value)
    );
    CREATE TABLE album (
        panel_id TEXT NOT NULL REFERENCES panels(id) ON DELETE CASCADE,
        brand TEXT NOT NULL,
        code TEXT NOT NULL DEFAULT '',
        name TEXT NOT NULL DEFAULT '',
        known INTEGER NOT NULL DEFAULT 0,
        callsign TEXT NOT NULL DEFAULT '',
        first REAL NOT NULL,
        last REAL NOT NULL,
        fanfare INTEGER NOT NULL DEFAULT 0,
        PRIMARY KEY (panel_id, brand)
    );
    CREATE TABLE days (
        panel_id TEXT NOT NULL REFERENCES panels(id) ON DELETE CASCADE,
        date TEXT NOT NULL,
        aircraft INTEGER NOT NULL DEFAULT 0,
        airlines INTEGER NOT NULL DEFAULT 0,
        shown INTEGER NOT NULL DEFAULT 0,
        max_alt_m INTEGER NOT NULL DEFAULT 0,
        max_alt_callsign TEXT NOT NULL DEFAULT '',
        quiet_record_s INTEGER NOT NULL DEFAULT 0,
        ids TEXT,                              -- nur der laufende Tag: JSON mit den ICAO-Mengen
        PRIMARY KEY (panel_id, date)
    );
    CREATE TABLE rare (
        panel_id TEXT NOT NULL REFERENCES panels(id) ON DELETE CASCADE,
        icao24 TEXT NOT NULL,
        data TEXT NOT NULL,                    -- JSON, wie in /stats
        PRIMARY KEY (panel_id, icao24)
    );
    CREATE TABLE cache (key TEXT PRIMARY KEY, expires REAL NOT NULL, value TEXT NOT NULL);
    CREATE TABLE issues (key TEXT PRIMARY KEY, panel_id TEXT, data TEXT NOT NULL);
    CREATE TABLE logo_missing (code TEXT PRIMARY KEY, failed REAL NOT NULL);
    CREATE TABLE state (key TEXT PRIMARY KEY, value TEXT NOT NULL);
    """,
]

PANEL_SELECT = ("SELECT id, name, settings, fw_channel, created, updated, stats_since, last_obs, last_in_view "
                "FROM panels")


class Db:
    def __init__(self, path: str):
        self.path = path
        try:
            self.conn = self._connect()
        except sqlite3.DatabaseError as e:
            broken = self.path + ".broken"
            log.error("%s ist keine lesbare Datenbank (%s): liegt jetzt als %s beiseite, starte leer",
                      os.path.basename(self.path), e, os.path.basename(broken))
            for suffix in ("", "-wal", "-shm", "-journal"):
                try:
                    os.replace(self.path + suffix, broken + suffix)
                except FileNotFoundError:
                    pass
            self.conn = self._connect()

    def _connect(self) -> sqlite3.Connection:
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        conn = sqlite3.connect(self.path, isolation_level=None)      # Transaktionen ausdrücklich, s. u.
        try:
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA busy_timeout = 5000")
            mode = conn.execute("PRAGMA journal_mode = WAL").fetchone()[0]
            if mode != "wal":
                log.warning("WAL geht hier nicht (Dateisystem?), nehme %s", mode)
            conn.execute("PRAGMA synchronous = NORMAL")
            conn.execute("PRAGMA foreign_keys = ON")
            self._migrate(conn)
        except BaseException:
            conn.close()
            raise
        return conn

    @staticmethod
    def _migrate(conn: sqlite3.Connection) -> None:
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        for n, script in enumerate(MIGRATIONS[version:], start=version + 1):
            conn.executescript("BEGIN;\n" + script + "\nCOMMIT;")
            conn.execute(f"PRAGMA user_version = {n}")
            log.info("Datenbank auf Stand %d", n)

    # ------------------------------------------------------------ Zugriff --
    def execute(self, sql: str, params: tuple = ()) -> sqlite3.Cursor:
        return self.conn.execute(sql, params)

    def executemany(self, sql: str, rows) -> None:
        self.conn.executemany(sql, rows)

    def query(self, sql: str, params: tuple = ()) -> list[sqlite3.Row]:
        return self.conn.execute(sql, params).fetchall()

    def one(self, sql: str, params: tuple = ()) -> sqlite3.Row | None:
        return self.conn.execute(sql, params).fetchone()

    @contextmanager
    def transaction(self):
        """Mehrere Schreibzugriffe als eine Einheit: ganz oder gar nicht."""
        self.conn.execute("BEGIN IMMEDIATE")
        try:
            yield
        except BaseException:
            self.conn.execute("ROLLBACK")
            raise
        self.conn.execute("COMMIT")

    def state_get(self, key: str, default=None):
        row = self.one("SELECT value FROM state WHERE key = ?", (key,))
        return json.loads(row["value"]) if row else default

    def state_set(self, key: str, value) -> None:
        self.execute("INSERT INTO state (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                     (key, json.dumps(value, ensure_ascii=False)))

    def backup(self, dest: str) -> None:
        """Kopie der Datenbank, auch im laufenden Betrieb."""
        out = sqlite3.connect(dest)
        try:
            self.conn.backup(out)
        finally:
            out.close()

    def close(self) -> None:
        self.conn.close()

    # ------------------------------------------------------------- Panels --
    # Nur die Zeilen; die laufenden Panels (Modell, Renderer ...) baut main.py daraus.
    def panel_add(self, pid: str, name: str = "", settings: dict[str, str] | None = None) -> None:
        now = time.time()
        try:
            self.execute("INSERT INTO panels (id, name, settings, created, updated) VALUES (?, ?, ?, ?, ?)",
                         (pid, name, json.dumps(settings or {}, ensure_ascii=False), now, now))
        except sqlite3.IntegrityError:
            raise ValueError(f"Panel {pid} gibt es schon") from None

    def panel_get(self, pid: str) -> dict | None:
        row = self.one(PANEL_SELECT + " WHERE id = ?", (pid,))
        return _panel(row) if row else None

    def panel_list(self) -> list[dict]:
        return [_panel(r) for r in self.query(PANEL_SELECT + " ORDER BY created")]

    def panel_set_settings(self, pid: str, settings: dict[str, str]) -> None:
        cur = self.execute("UPDATE panels SET settings = ?, updated = ? WHERE id = ?",
                           (json.dumps(settings, ensure_ascii=False), time.time(), pid))
        if cur.rowcount == 0:
            raise ValueError(f"Panel {pid} gibt es nicht")

    def panel_remove(self, pid: str) -> None:
        if self.execute("DELETE FROM panels WHERE id = ?", (pid,)).rowcount == 0:
            raise ValueError(f"Panel {pid} gibt es nicht")


def _panel(row: sqlite3.Row) -> dict:
    d = dict(row)
    d["settings"] = json.loads(d["settings"] or "{}")
    return d
```

- [ ] **Step 4: Run the tests to verify they pass; run ruff**

Run: `... sh -c "ruff check . && python -m pytest -p no:cacheprovider -q server/tests/test_db.py"`
Expected: 8 passed, ruff clean.

- [ ] **Step 5: Add `.claude/worktrees/` to `.gitignore` and commit**

Append to `.gitignore` under `# Werkzeuge`:

```
.claude/worktrees/
```

```bash
git add .gitignore server/flightpanel/db.py server/tests/test_db.py docs/superpowers
git commit -m "SQLite-Datenbank: db.py mit Schema, Transaktionen, Zustand und Panel-Zeilen

Erster Schritt zum Server für mehrere Panels (docs/superpowers/specs/2026-10-13-freunde-multi-tenant-design.md).

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 2: `Cache` on the database

**Files:**
- Modify: `server/flightpanel/cache.py` (whole file)
- Test: `server/tests/test_dream.py` (the cache test), new test in the same file

**Interfaces:**
- Consumes: `Db.query/execute/executemany` from Task 1.
- Produces: `Cache(db)`; unchanged `get(key)`, `set(key, value, ttl)`, `prune() -> int`, `save()`, `__len__`, attribute `data`; new `load_legacy(raw: dict)` (old `cache.json` shape `{key: [expires, value]}`).

- [ ] **Step 1: Rewrite the cache tests in `server/tests/test_dream.py`**

Replace the imports and `test_cache_drops_expired_entries_when_saving`:

```python
import asyncio
import time
from datetime import datetime

from conftest import make_cfg
from flightpanel import cache as cache_mod
from flightpanel import issues as issues_mod
from flightpanel.cache import Cache
from flightpanel.db import Db
from flightpanel.issues import Issues
```

```python
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
```

(`json` is no longer imported in that file; remove the import.)

- [ ] **Step 2: Run the two tests to verify they fail**

Run: `... sh -c "python -m pytest -p no:cacheprovider -q server/tests/test_dream.py -k cache"`
Expected: TypeError (`Cache` expects a path) / AttributeError `load_legacy`.

- [ ] **Step 3: Rewrite `server/flightpanel/cache.py`**

```python
"""Zwischenspeicher für Antworten der Routen- und Flugzeugdienste: Schlüssel ->
Wert mit Ablaufzeit. Lebt im Speicher; Geändertes schreibt main.App.save() in die
Datenbank (Tabelle cache). Abgelaufenes fällt beim Laden und bei jedem Speichern weg,
sonst wüchse er im Dauerbetrieb mit jedem Flugzeug, das je vorbeikam.

Schlüssel: "route:<callsign>", "ac:<icao24>" (adsbdb), "imstops:<callsign>" (adsb.im),
"trk:<icao24>:<callsign>" (Flugspur)."""

import json
import logging
import time

from .db import Db

log = logging.getLogger("cache")


class Cache:
    def __init__(self, db: Db):
        self.db = db
        self.data: dict[str, tuple[float, object]] = {}
        self._dirty: set[str] = set()
        self.load()

    def load(self) -> None:
        self.data = {r["key"]: (r["expires"], json.loads(r["value"]))
                     for r in self.db.query("SELECT key, expires, value FROM cache WHERE expires > ?", (time.time(),))}
        if self.data:
            log.info("Cache geladen: %d Einträge", len(self.data))

    def load_legacy(self, raw: dict) -> None:
        """Alte cache.json übernehmen (main.absorb_legacy, einmalig); Unbrauchbares bleibt weg."""
        now = time.time()
        for k, item in raw.items():
            try:
                exp, v = item
                if float(exp) > now:
                    self.data[k] = (exp, v)
                    self._dirty.add(k)
            except (TypeError, ValueError):
                log.warning("cache.json: Eintrag %s unbrauchbar, ignoriert", k)

    def prune(self) -> int:
        """Abgelaufenes entfernen, im Speicher und in der Datenbank; gibt zurück, wie viele Einträge."""
        now = time.time()
        old = [k for k, (exp, _) in self.data.items() if exp < now]
        for k in old:
            del self.data[k]
            self._dirty.discard(k)
        self.db.execute("DELETE FROM cache WHERE expires < ?", (now,))
        return len(old)

    def save(self) -> None:
        self.prune()
        if self._dirty:
            self.db.executemany(
                "INSERT INTO cache (key, expires, value) VALUES (?, ?, ?) "
                "ON CONFLICT(key) DO UPDATE SET expires = excluded.expires, value = excluded.value",
                [(k, self.data[k][0], json.dumps(self.data[k][1], ensure_ascii=False)) for k in self._dirty])
            self._dirty.clear()

    def get(self, key: str):
        """Wert oder None, wenn unbekannt oder abgelaufen."""
        item = self.data.get(key)
        if item is None:
            return None
        exp, v = item
        if exp < time.time():
            del self.data[key]
            return None
        return v

    def set(self, key: str, value, ttl: int) -> None:
        self.data[key] = (time.time() + ttl, value)
        self._dirty.add(key)

    def __len__(self) -> int:
        return len(self.data)
```

- [ ] **Step 4: Run; expect the two cache tests to pass and `test_dream_tidies_up_and_says_so_in_health` and `test_web` to fail (they construct `App`, which still passes a path)**

Run: `... sh -c "python -m pytest -p no:cacheprovider -q server/tests/test_dream.py -k cache"`
Expected: 2 passed. Whole-suite failures in `App` are expected until Task 8; do not fix them here.

- [ ] **Step 5: Commit**

```bash
git add server/flightpanel/cache.py server/tests/test_dream.py
git commit -m "Cache liegt in der Datenbank statt in cache.json

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 3: `Issues` on the database

**Files:**
- Modify: `server/flightpanel/issues.py` (class `Issues`; module docstring paragraph about `data/issues.json`)
- Test: `server/tests/test_dream.py`

**Interfaces:**
- Consumes: `Db` (Task 1).
- Produces: `Issues(db)`; unchanged `note()`, `source_error()`, `save()`, `clear()`, `forget(now) -> int`, `report(**extra)`, attributes `items`, `since`; new `load_legacy(raw: dict)` (old `issues.json` shape `{"since": float, "items": {...}}`).

- [ ] **Step 1: Adapt and extend the tests in `server/tests/test_dream.py`**

In `test_one_offs_go_after_a_week_recurring_after_a_month` and `test_full_issue_list_makes_room_for_news` replace `Issues(str(tmp_path / "i.json"))` with `Issues(Db(str(tmp_path / "i.db")))`. Extend the first test's end:

```python
    now[0] += 30 * DAY
    assert iss.forget(now[0]) == 2                            # 30 Tage nicht mehr aufgetreten
    iss.save()
    assert Issues(iss.db).items == {}                          # auch aus der Datenbank verschwunden
```

Add:

```python
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
        "kind": "exception", "key": "x", "seen": 3, "days": 2, "first": now[0] - DAY, "last": now[0], "callsigns": []}}})
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
```

(`pytest` is already imported in `test_dream.py`; add the import if it is missing.)

- [ ] **Step 2: Run to verify they fail**

Run: `... sh -c "python -m pytest -p no:cacheprovider -q server/tests/test_dream.py -k issues or one_offs or full_issue"`
Expected: failures (`Issues` still wants a path).

- [ ] **Step 3: Change `Issues`**

Replace the import `from .storage import read_json, write_json` with `import json` (keep `logging, re, time`) and `from .db import Db`. Replace the docstring paragraph "Pro Eintrag: … POST /issues/clear fängt von vorn an." with:

```
Pro Eintrag: wie oft gesehen (je Abruf), an wie vielen Tagen, erstes und letztes
Mal, bis zu zehn Callsigns, ein Beispiel. Liegt in der Datenbank (Tabelle issues,
Beginn in state; main.App.save() schreibt Geändertes) und übersteht Neustarts;
POST /issues/clear fängt von vorn an.
```

Replace the class body up to `forget` (keep `forget`'s docstring, `report` unchanged):

```python
class Issues:
    def __init__(self, db: Db):
        self.db = db
        self.items: dict[str, dict] = {r["key"]: json.loads(r["data"])
                                       for r in db.query("SELECT key, data FROM issues")}
        self.since = db.state_get("issues_since")
        if self.since is None:
            self.since = time.time()
            db.state_set("issues_since", self.since)
        self._dirty: set[str] = set()       # geänderte Einträge
        self._gone: set[str] = set()        # gelöschte Einträge
        self._reset = False                 # clear(): alle Zeilen weg
        self._since_dirty = False

    def load_legacy(self, raw: dict) -> None:
        """Alte issues.json übernehmen (main.absorb_legacy, einmalig)."""
        if not isinstance(raw, dict):
            return
        items = raw.get("items", {})
        self.items.update(items)
        self._dirty.update(items)
        self.since = raw.get("since", self.since)
        self._since_dirty = True

    def note(self, kind: str, key: str, callsign: str = "", **example) -> None:
        k = f"{kind}:{key}"
        now = time.time()
        it = self.items.get(k)
        if it is None:
            if len(self.items) >= MAX_KEYS:
                oldest = min(self.items, key=lambda i: self.items[i]["last"])
                del self.items[oldest]
                self._gone.add(oldest)
                self._dirty.discard(oldest)
            it = self.items[k] = {"kind": kind, "key": key, "seen": 0, "days": 1, "first": now, "callsigns": []}
            log.info("Auffällig: %s %s %s", k, callsign, example or "")
        elif _day(it["last"]) != _day(now):
            it["days"] = it.get("days", 1) + 1
        it["seen"] += 1
        it["last"] = now
        if callsign and callsign not in it["callsigns"]:
            it["callsigns"] = (it["callsigns"] + [callsign])[-MAX_CALLSIGNS:]
        if example:
            it["example"] = example
        self._dirty.add(k)

    def source_error(self, source: str, error: str, **example) -> None:
        """Fehler einer Quelle: HTTP 429 als rate_limit, alles andere als source_error."""
        self.note("rate_limit" if "429" in error else "source_error", source, error=error, **example)

    def save(self) -> None:
        """Geändertes schreiben. Als gespeichert gilt es erst nach dem COMMIT (Db.after_commit);
        scheitert die Transaktion, schreibt der nächste Lauf es noch einmal."""
        reset, since, gone, dirty = self._reset, self._since_dirty, set(self._gone), set(self._dirty)
        if reset:
            self.db.execute("DELETE FROM issues")
        if since:
            self.db.state_set("issues_since", self.since)
        for k in gone:
            self.db.execute("DELETE FROM issues WHERE key = ?", (k,))
        rows = [(k, json.dumps(self.items[k], ensure_ascii=False)) for k in dirty if k in self.items]
        if rows:
            self.db.executemany("INSERT INTO issues (key, data) VALUES (?, ?) "
                                "ON CONFLICT(key) DO UPDATE SET data = excluded.data", rows)
        self.db.after_commit(lambda: self._saved(reset, since, gone, dirty))

    def _saved(self, reset: bool, since: bool, gone: set[str], dirty: set[str]) -> None:
        """Nach dem COMMIT: das Geschriebene vergessen. Nur Speicher, darf nicht scheitern."""
        if reset:
            self._reset = False
        if since:
            self._since_dirty = False
        self._gone -= gone
        self._dirty -= dirty

    def clear(self) -> None:
        self.items = {}
        self.since = time.time()
        self._reset = self._since_dirty = True
        self._dirty.clear()
        self._gone.clear()
```

In `forget`, after `del self.items[k]` add `self._gone.add(k)` and `self._dirty.discard(k)`; replace `self.dirty = self.dirty or bool(old)` with nothing (the `_gone` set is the dirt).

- [ ] **Step 4: Run the issues tests; expect pass. Ruff clean (no unused imports).**

- [ ] **Step 5: Commit**

```bash
git add server/flightpanel/issues.py server/tests/test_dream.py
git commit -m "Auffälligkeiten liegen in der Datenbank statt in issues.json

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 4: `Stats` on the database, per panel

**Files:**
- Modify: `server/flightpanel/stats.py` (loading, saving, `_roll`, `_merge_listed`; constructor signature)
- Test: `server/tests/conftest.py` (new helper `make_db`), `server/tests/test_stats.py`, `server/tests/test_cards.py` (two `Stats(...)` calls)

**Interfaces:**
- Consumes: `Db` incl. `panel_add`, `state_get/set` (Task 1).
- Produces: `Stats(db, panel_id, cfg, names, rare=None)`; unchanged `observe`, `set_name`, `save`, `today`, `dex_count`, `dex_total`, `newest`, `quiet_s`, `rare_now`, `report`, `brand`, attributes `since, dex, day, history, last_obs, last_in_view, rare, course, prev_course, course_since, course_seen, dirty`; new `load_legacy(raw: dict)` (old `stats.json` shape).
- Rows: `album` (one per brand), `days` (one per date; the latest row is the running day and carries `ids` JSON `{"aircraft": [...], "airlines": [...], "shown": [...]}`, older rows are history with `ids` NULL), `rare` (JSON per icao24), columns `stats_since/last_obs/last_in_view` on `panels`, key `course` in `state`.

- [ ] **Step 1: Add `make_db` to `server/tests/conftest.py`**

```python
from flightpanel.db import Db
```

```python
def make_db(tmp_path, *panels: str) -> Db:
    """Datenbank in tmp_path mit den Panel-Zeilen (Default: home), wie sie der Server anlegt."""
    db = Db(str(tmp_path / "flightpanel.db"))
    for pid in panels or ("home",):
        if db.panel_get(pid) is None:
            db.panel_add(pid)
    return db
```

- [ ] **Step 2: Adapt `server/tests/test_stats.py`**

Imports: add `import time` and change the conftest line to `from conftest import NAMES, make_cfg, make_db, state`. Replace `mk` and the three tests that touch files, and add the legacy test:

```python
def mk(tmp_path, db=None):
    return Stats(db or make_db(tmp_path), "home", make_cfg(), NAMES, RARE)
```

```python
def test_day_counts_and_rollover(tmp_path):
    db = make_db(tmp_path)
    st = mk(tmp_path, db)
    t = ts(2026, 10, 10, 12, 0)
    st.observe([state(icao="a", alt=11900, cs="KLM1"), state(icao="b", cs="EZY2"),
                state(icao="g", ground=True)], DisplayModel(flights=[fl()]), t)
    d = st.today()
    assert d["aircraft"] == 2 and d["max_alt_m"] == 11900 and d["max_alt_callsign"] == "KLM1"
    assert d["airlines"] == 2 and d["shown"] == 1           # KLM unbekannt, aber groß (A3)
    st.observe([], DisplayModel(), ts(2026, 10, 11, 7, 0))
    assert st.history["2026-10-10"]["aircraft"] == 2
    assert st.today()["aircraft"] == 0
    st.save()
    again = mk(tmp_path, db)                                 # Neustart: gestern in der History, heute leer
    assert again.history["2026-10-10"]["aircraft"] == 2 and again.day["date"] == "2026-10-11"
```

```python
def test_extras_join_their_brand_once_listed(tmp_path):
    """Ein Exot, der inzwischen in airlines.json steht, zählt beim nächsten Start zur
    Marke statt doppelt (CXI: adsbdb sagte "Shanxi Airlines", es ist Corendon)."""
    corendon = {"name": "Corendon", "iata": "XC", "airline": True}
    t = ts(2026, 10, 11, 23, 0)
    db = make_db(tmp_path)
    st = Stats(db, "home", make_cfg(), {"CAI": corendon}, RARE)
    st.observe([state(icao="a", cs="CAI98GP"), state(icao="b", cs="CXI97DR"), state(icao="c", cs="ABC123")],
               DisplayModel(), t)
    assert st.dex_count()[2] == 2                                   # CXI und ABC als Exoten
    st.save()
    again = Stats(db, "home", make_cfg(), {"CAI": corendon, "CXI": corendon}, RARE)
    assert set(again.dex) == {"Corendon", "ABC"} and again.dex_count()[2] == 1
    again.save()
    assert {r["brand"] for r in db.query("SELECT brand FROM album")} == {"Corendon", "ABC"}     # CXI-Zeile weg
    assert set(Stats(db, "home", make_cfg(), {"CAI": corendon, "CXI": corendon}, RARE).dex) == {"Corendon", "ABC"}
    solo = make_db(tmp_path / "solo")                               # Marke noch nicht im Album
    st = Stats(solo, "home", make_cfg(), {}, RARE)
    st.observe([state(icao="b", cs="CXI97DR")], DisplayModel(), t)
    st.save()
    later = Stats(solo, "home", make_cfg(), {"CXI": corendon}, RARE)
    assert later.dex["Corendon"]["known"] and later.dex["Corendon"]["first"] == t


def test_save_load_roundtrip_and_broken_file(tmp_path):
    db = make_db(tmp_path)
    st = mk(tmp_path, db)
    t = ts(2026, 10, 10, 12, 0)
    st.observe([state(cs="EZY1"), state(icao="r", cs="UAE1", actype="A388")],
               DisplayModel(flights=[fl()], landing_course=249, course_votes=3), t)
    st.save()
    again = mk(tmp_path, db)
    assert again.today() == st.today() and again.dex.keys() == st.dex.keys() and again.day["shown"] == {"v1"}
    assert again.course == 249 and "r" in again.rare and again.since == st.since
    db.close()
    (tmp_path / "flightpanel.db").write_text("{kaputt", encoding="utf-8")
    broken = mk(tmp_path)                                            # kaputte Datei: beiseite, leer
    assert broken.dex == {} and broken.course is None


def test_old_stats_json_is_taken_over(tmp_path):
    """Der laufende Tag der alten Datei ist heute (der Server lief bis eben), die History älter."""
    t = time.time()
    today = time.strftime("%Y-%m-%d", time.localtime(t))
    raw = {"since": t - 86400,
           "dex": {"easyJet": {"first": t - 3600, "code": "EZY", "name": "easyJet", "known": True,
                               "callsign": "EZY1", "fanfare": False, "last": t}},
           "day": {"date": today, "aircraft": ["a", "b"], "airlines": ["easyJet"], "shown": ["a"],
                   "max_alt_m": 9000, "max_alt_callsign": "EZY1", "quiet_record_s": 300},
           "history": {"2026-10-09": {"aircraft": 5, "airlines": 2, "shown": 1, "max_alt_m": 1, "quiet_record_s": 0}},
           "last_obs": t, "last_in_view": t - 60,
           "rare": {"r1": {"first": t, "last": t, "label": "A380", "kind": "type"}},
           "course": {"now": 249, "prev": 69, "since": t - 7200, "seen": t}}
    db = make_db(tmp_path)
    st = mk(tmp_path, db)
    st.load_legacy(raw)
    st.save()
    again = mk(tmp_path, db)
    assert again.since == t - 86400 and "easyJet" in again.dex and again.course == 249 and again.prev_course == 69
    assert again.history["2026-10-09"]["aircraft"] == 5 and "r1" in again.rare and again.last_in_view == t - 60
    assert again.day["date"] == today and again.day["aircraft"] == {"a", "b"} and again.today()["shown"] == 1
    old = mk(tmp_path / "old", make_db(tmp_path / "old"))             # Server lag Tage still: alles History
    old.load_legacy({**raw, "day": {**raw["day"], "date": "2026-10-10"}})
    old.save()
    later = mk(tmp_path / "old", make_db(tmp_path / "old"))
    assert later.history["2026-10-10"]["aircraft"] == 2 and later.day["date"] == today and later.today()["aircraft"] == 0
```

Note: `make_db(tmp_path / "solo")` needs the directory; `Db` creates it (`os.makedirs`).

In `server/tests/test_cards.py` add `make_db` to the `conftest` import and change the two constructions:

```python
    st = Stats(make_db(tmp_path), "home", make_cfg(), NAMES, {"types": {}, "callsigns": {}})
```

```python
    st = Stats(make_db(tmp_path), "home", make_cfg(), NAMES, {"types": {"A388": "A380"}, "callsigns": {}})
```

(`_stats` is called several times per test with the same `tmp_path`; `make_db` reopens the same file and keeps the existing `home` row, which is fine because those tests never save.)

- [ ] **Step 3: Run to verify failures**

Run: `... sh -c "python -m pytest -p no:cacheprovider -q server/tests/test_stats.py"`
Expected: TypeErrors (constructor signature).

- [ ] **Step 4: Change `server/flightpanel/stats.py`**

Imports: `import json`, `import logging`, `import time`; replace `from .storage import read_json, write_json` with `from .db import Db`. Docstring last paragraph:

```
Liegt je Panel in der Datenbank (db.py: Tabellen album, days, rare, Spalten in panels;
die Landerichtung in state), GET /stats zeigt alles. main.App.save() schreibt, was
sich geändert hat."""
```

Add after `_new_day`:

```python
def _counts(d: dict) -> dict:
    """Ein abgeschlossener Tag, wie er in history steht."""
    return {"aircraft": len(d["aircraft"]), "airlines": len(d["airlines"]), "shown": len(d["shown"]),
            "max_alt_m": d["max_alt_m"], "quiet_record_s": d["quiet_record_s"]}
```

Replace `__init__`, `_load`, `_merge_listed`, `to_dict`, `save` with:

```python
class Stats:
    def __init__(self, db: Db, panel_id: str, cfg: Config, names: dict[str, dict], rare: dict | None = None):
        self.db = db
        self.panel_id = panel_id
        self.cfg = cfg
        self.names = names
        self.rare_cfg = rare or {"types": {}, "callsigns": {}}
        self.since = time.time()
        self.dex: dict[str, dict] = {}
        self.day = _new_day(_date(self.since))
        self.history: dict[str, dict] = {}
        self.last_obs = 0.0
        self.last_in_view = 0.0
        self.rare: dict[str, dict] = {}
        self.course: float | None = None
        self.prev_course: float | None = None
        self.course_since = 0.0
        self.course_seen = 0.0
        self._pending: tuple[float, int] | None = None
        self._finished: dict[str, dict] = {}     # abgeschlossene Tage, noch nicht geschrieben
        self._gone_days: set[str] = set()        # aus der History gefallen
        self._gone_brands: set[str] = set()      # Exoten, die zur Marke gezogen sind
        self.dirty = False
        self._load()

    # --------------------------------------------------------- Laden/Speichern --
    def _load(self) -> None:
        pid = self.panel_id
        row = self.db.one("SELECT stats_since, last_obs, last_in_view FROM panels WHERE id = ?", (pid,))
        if row is not None and row["stats_since"]:
            self.since, self.last_obs, self.last_in_view = row["stats_since"], row["last_obs"], row["last_in_view"]
        self.dex = {r["brand"]: {"first": r["first"], "last": r["last"], "code": r["code"], "name": r["name"],
                                 "known": bool(r["known"]), "callsign": r["callsign"], "fanfare": bool(r["fanfare"])}
                    for r in self.db.query("SELECT * FROM album WHERE panel_id = ?", (pid,))}
        self._merge_listed()
        days = self.db.query("SELECT * FROM days WHERE panel_id = ? ORDER BY date", (pid,))
        # Der laufende Tag ist die jüngste Zeile, wenn sie ihre ICAO-Mengen (ids) noch hat;
        # eine abgeschlossene (ids NULL) ist History, der laufende Tag beginnt dann heute leer.
        if days and days[-1]["ids"]:
            cur, done = days[-1], days[:-1]
            ids = json.loads(cur["ids"])
            self.day = {**_new_day(cur["date"]), "max_alt_m": cur["max_alt_m"],
                        "max_alt_callsign": cur["max_alt_callsign"], "quiet_record_s": cur["quiet_record_s"],
                        **{k: set(ids.get(k, [])) for k in ("aircraft", "airlines", "shown")}}
        else:
            done = days
        self.history = {r["date"]: {"aircraft": r["aircraft"], "airlines": r["airlines"], "shown": r["shown"],
                                    "max_alt_m": r["max_alt_m"], "quiet_record_s": r["quiet_record_s"]}
                        for r in done}
        self.rare = {r["icao24"]: json.loads(r["data"])
                     for r in self.db.query("SELECT icao24, data FROM rare WHERE panel_id = ?", (pid,))}
        c = self.db.state_get("course") or {}
        self.course, self.prev_course = c.get("now"), c.get("prev")
        self.course_since, self.course_seen = c.get("since", 0.0), c.get("seen", 0.0)

    def load_legacy(self, raw: dict) -> None:
        """Alte stats.json übernehmen (main.absorb_legacy, einmalig)."""
        try:
            self.since = raw.get("since", self.since)
            for key, e in raw.get("dex", {}).items():
                self.dex.setdefault(key, e)
            self._merge_listed()
            d = raw.get("day") or {}
            if d.get("date"):
                day = {**_new_day(d["date"]), **d, **{k: set(d.get(k, [])) for k in ("aircraft", "airlines", "shown")}}
                if day["date"] >= self.day["date"]:
                    self.day = day
                else:
                    self.history[day["date"]] = _counts(day)
            self.history.update(raw.get("history", {}))
            self._finished.update(self.history)
            self.last_obs = raw.get("last_obs", self.last_obs)
            self.last_in_view = raw.get("last_in_view", self.last_in_view)
            self.rare.update(raw.get("rare", {}))
            c = raw.get("course") or {}
            self.course, self.prev_course = c.get("now", self.course), c.get("prev", self.prev_course)
            self.course_since, self.course_seen = c.get("since", self.course_since), c.get("seen", self.course_seen)
        except Exception as e:
            log.warning("stats.json unvollständig (%s), übernehme nur einen Teil", e)
        self.dirty = True

    def _merge_listed(self) -> None:
        """Exoten, die inzwischen in airlines.json stehen, zählen zur Marke (oder fallen
        raus, wenn sie keine Airline sind); sonst stünden sie doppelt im Album. Das erste
        Sichtungsdatum bleibt, außer die Marke hat schon einen Eintrag."""
        for code in [k for k, e in self.dex.items() if not e.get("known") and k in self.names]:
            e = self.dex.pop(code)
            self._gone_brands.add(code)
            b = self.brand(code)
            if b is not None:
                self.dex.setdefault(b[0], {**e, "name": b[1], "known": True})
            self.dirty = True

    def save(self) -> None:
        if not self.dirty:
            return
        pid = self.panel_id
        self.db.execute("UPDATE panels SET stats_since = ?, last_obs = ?, last_in_view = ? WHERE id = ?",
                        (self.since, self.last_obs, self.last_in_view, pid))
        for brand in self._gone_brands:
            self.db.execute("DELETE FROM album WHERE panel_id = ? AND brand = ?", (pid, brand))
        self._gone_brands.clear()
        self.db.executemany(
            "INSERT INTO album (panel_id, brand, code, name, known, callsign, first, last, fanfare) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(panel_id, brand) DO UPDATE SET code = excluded.code, "
            "name = excluded.name, known = excluded.known, callsign = excluded.callsign, first = excluded.first, "
            "last = excluded.last, fanfare = excluded.fanfare",
            [(pid, b, e.get("code", ""), e.get("name", ""), int(bool(e.get("known"))), e.get("callsign", ""),
              e["first"], e.get("last", e["first"]), int(bool(e.get("fanfare")))) for b, e in self.dex.items()])
        for date in self._gone_days:
            self.db.execute("DELETE FROM days WHERE panel_id = ? AND date = ?", (pid, date))
        self._gone_days.clear()
        day_sql = ("INSERT INTO days (panel_id, date, aircraft, airlines, shown, max_alt_m, max_alt_callsign, "
                   "quiet_record_s, ids) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(panel_id, date) DO UPDATE SET "
                   "aircraft = excluded.aircraft, airlines = excluded.airlines, shown = excluded.shown, "
                   "max_alt_m = excluded.max_alt_m, max_alt_callsign = excluded.max_alt_callsign, "
                   "quiet_record_s = excluded.quiet_record_s, ids = excluded.ids")
        self.db.executemany(day_sql, [(pid, date, h["aircraft"], h["airlines"], h.get("shown", 0), h["max_alt_m"],
                                       "", h["quiet_record_s"], None) for date, h in self._finished.items()])
        self._finished.clear()
        d = self.day
        ids = json.dumps({k: sorted(d[k]) for k in ("aircraft", "airlines", "shown")})
        self.db.execute(day_sql, (pid, d["date"], len(d["aircraft"]), len(d["airlines"]), len(d["shown"]),
                                  d["max_alt_m"], d["max_alt_callsign"], d["quiet_record_s"], ids))
        self.db.execute("DELETE FROM rare WHERE panel_id = ?", (pid,))
        self.db.executemany("INSERT INTO rare (panel_id, icao24, data) VALUES (?, ?, ?)",
                            [(pid, k, json.dumps(v, ensure_ascii=False)) for k, v in self.rare.items()])
        self.db.state_set("course", {"now": self.course, "prev": self.prev_course, "since": self.course_since,
                                     "seen": self.course_seen})
        self.dirty = False
```

**Bookkeeping only after the COMMIT** (controller ruling after Task 2; it overrides the clearing lines in the code above). `App.save()` (Task 8) runs all stores in one `db.transaction()`. `save()` therefore snapshots what it writes (`finished = dict(self._finished)`, `gone_days = set(self._gone_days)`, `gone_brands = set(self._gone_brands)`) and writes from the snapshots. It then forgets exactly those via `self.db.after_commit(...)`, including `self.dirty = False`, the same way `Cache.save()` and `Issues.save()` do. A ROLLBACK leaves everything for the next save. The callback touches memory only and must not raise. Nothing changes the store between its `save()` and the COMMIT, because `App.save()` is synchronous; say so in a short German comment. Test (in `test_stats.py`, add `import pytest` if missing):

```python
def test_a_failed_save_is_written_next_time(tmp_path):
    """App.save() schreibt in einer Transaktion; scheitert sie, ist beim nächsten Lauf nichts verloren."""
    db = make_db(tmp_path)
    st = mk(tmp_path, db)
    st.observe([state(icao="a", cs="EZY1")], DisplayModel(), ts(2026, 10, 10, 12, 0))
    st.observe([], DisplayModel(), ts(2026, 10, 11, 7, 0))            # der 10. ist abgeschlossen
    with pytest.raises(RuntimeError):
        with db.transaction():
            st.save()
            raise RuntimeError("Platte voll")
    assert mk(tmp_path, db).history == {}                               # nichts geschrieben
    st.save()
    assert mk(tmp_path, db).history["2026-10-10"]["aircraft"] == 1     # beim nächsten Mal alles da
```

Replace `_roll`:

```python
    def _roll(self, date: str) -> None:
        d = self.day
        self.history[d["date"]] = self._finished[d["date"]] = _counts(d)
        for k in sorted(self.history)[:-HISTORY_DAYS]:
            del self.history[k]
            self._finished.pop(k, None)
            self._gone_days.add(k)
        self.day = _new_day(date)
```

Everything else (`brand`, `observe`, `set_name`, `_rare`, `_course`, the queries, `report`) stays as it is.

- [ ] **Step 5: Run `test_stats.py` and `test_cards.py`; expect pass. Ruff clean.**

- [ ] **Step 6: Commit**

```bash
git add server/flightpanel/stats.py server/tests/conftest.py server/tests/test_stats.py server/tests/test_cards.py
git commit -m "Statistik und Sammelalbum liegen je Panel in der Datenbank

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 5: `LogoStore` remembers missing logos in the database

**Files:**
- Modify: `server/flightpanel/logos.py` (constructor, `_load_missing`, `_save_missing` → `_note_missing`, `_fetch`, `make_logo_store`), `server/flightpanel/fetch_logos.py` (`run`)
- Test: `server/tests/test_logos.py`

**Interfaces:**
- Consumes: `Db` (Task 1).
- Produces: `LogoStore(directory, size, fetch=True, urls=DEFAULT_URLS, max_aspect=1.5, session=None, db=None)`; `make_logo_store(cfg, session=None, db=None)`; new `load_legacy(raw: dict)` (old `.missing.json` shape `{code: epoch}`). Without `db` the missing list lives in memory only (tests, `tools/screenshots.py`).

- [ ] **Step 1: Adapt the tests in `server/tests/test_logos.py`**

Add `import pytest` and `from flightpanel.db import Db`; drop `import json`. Replace `test_missing_lists_only_codes_still_without_a_logo` and add two tests:

```python
def test_missing_lists_only_codes_still_without_a_logo(tmp_path):
    """LY war vermerkt und wurde später doch geholt, AY bekommt ein eigenes: beide
    gehören nicht mehr nach /health -> logos_missing."""
    now = time.time()
    db = Db(str(tmp_path / "f.db"))
    store = LogoStore(str(tmp_path), 32, fetch=False, db=db)
    store.load_legacy({"LY": now, "AY": now, "FX": now})                 # alte .missing.json
    _logo(tmp_path / "LY.png", (0, 0, 255))
    _logo(tmp_path / "own" / "AY.png", (0, 0, 255))
    assert store.missing() == ["FX"]
    assert LogoStore(str(tmp_path), 32, fetch=False, db=db).missing() == ["FX"]     # aus der Datenbank


def test_no_logo_anywhere_is_remembered_in_the_database(tmp_path, monkeypatch):
    async def download(self, url):
        return None

    monkeypatch.setattr(LogoStore, "_download", download)
    db = Db(str(tmp_path / "f.db"))
    store = LogoStore(str(tmp_path), 32, urls=(("https://logos.example/{iata}.png", None),), db=db)
    assert not asyncio.run(store.fetch_now("XQ", "XQA"))
    assert db.one("SELECT code FROM logo_missing")["code"] == "XQ"


def test_fetch_logos_leaves_a_broken_database_alone(tmp_path):
    """fetch_logos läuft neben dem Server: eine kaputte Datenbank bleibt liegen, wie sie ist
    (beiseitelegen darf nur der Server, der sie offen hat)."""
    junk = "kaputt, keine Datenbank " * 100
    (tmp_path / "flightpanel.db").write_text(junk)
    with pytest.raises(Exception, match="not a database"):
        asyncio.run(fetch_logos.run(make_cfg(data_dir=str(tmp_path)), [], force=True))
    assert (tmp_path / "flightpanel.db").read_text() == junk and not list(tmp_path.glob("*.broken*"))
```

- [ ] **Step 2: Run to verify failure** (`db` is not a known argument).

- [ ] **Step 3: Change `server/flightpanel/logos.py`**

Docstring line 9: `Negative Treffer werden in der Datenbank gemerkt (Tabelle logo_missing; Retry nach 7 Tagen).` Replace `from .storage import read_json, write_json` with `from .db import Db`. Constructor:

```python
    def __init__(self, directory: str, size: int, fetch: bool = True,
                 urls: tuple[tuple[str, float | None], ...] = DEFAULT_URLS, max_aspect: float = 1.5,
                 session: aiohttp.ClientSession | None = None, db: Db | None = None):
        """session: ohne gibt es keinen Abruf, nur die vorhandenen Dateien. db: ohne werden
        Fehlschläge nur im Speicher gemerkt."""
        self.dir = directory
        self.own_dir = os.path.join(directory, OWN)
        self.size = size
        self.fetch_enabled = fetch
        self.urls = urls
        self.max_aspect = max_aspect
        self.session = session
        self.db = db
        self._cache: dict[str, tuple[float, Image.Image | None]] = {}
        self._inflight: set[str] = set()
        self._tasks: set[asyncio.Task] = set()      # laufende Abrufe, festgehalten bis sie fertig sind
        self._errors: dict[str, float] = {}
        self._placeholders: dict[str, str | None] = {}
        self._missing: dict[str, float] = self._load_missing()
        os.makedirs(directory, exist_ok=True)
```

(`self._missing_path` is gone.) Replace `_load_missing` and `_save_missing`:

```python
    def _load_missing(self) -> dict[str, float]:
        if self.db is None:
            return {}
        return {r["code"]: r["failed"] for r in self.db.query("SELECT code, failed FROM logo_missing")}

    def load_legacy(self, raw: dict) -> None:
        """Alte logos/.missing.json übernehmen (main.absorb_legacy, einmalig)."""
        for code, t in raw.items():
            if isinstance(t, (int, float)):
                self._note_missing(code, float(t))

    def _note_missing(self, code: str, t: float) -> None:
        self._missing[code] = t
        if self.db is not None:
            self.db.execute("INSERT INTO logo_missing (code, failed) VALUES (?, ?) "
                            "ON CONFLICT(code) DO UPDATE SET failed = excluded.failed", (code, t))
```

In `_fetch`, replace the two lines `self._missing[k] = time.time()` / `self._save_missing()` with `self._note_missing(k, time.time())`. `make_logo_store`:

```python
def make_logo_store(cfg, session: aiohttp.ClientSession | None = None, db: Db | None = None) -> LogoStore:
    return LogoStore(os.path.join(cfg.data_dir, "logos"), cfg.logo_size, fetch=cfg.logo_fetch,
                     urls=parse_urls(cfg.logo_urls) or DEFAULT_URLS, max_aspect=cfg.logo_max_aspect,
                     session=session, db=db)
```

In `server/flightpanel/fetch_logos.py` import `from .db import Db` and change the start of `run`:

```python
    async with aiohttp.ClientSession(headers={"User-Agent": USER_AGENT}) as session:
        db = Db(os.path.join(cfg.data_dir, "flightpanel.db"), put_aside=False)   # neben dem Server: nichts beiseite
        store = make_logo_store(cfg, session, db)
```

and close it at the end of the `async with` block: after the `for` loop add `db.close()` (still inside the block).

- [ ] **Step 4: Run `test_logos.py`; expect pass. Ruff clean.**

- [ ] **Step 5: Commit**

```bash
git add server/flightpanel/logos.py server/flightpanel/fetch_logos.py server/tests/test_logos.py
git commit -m "Fehlende Logos stehen in der Datenbank statt in .missing.json

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 6: ISS orbit data in the database

**Files:**
- Modify: `server/flightpanel/astro.py` (class `Iss`: constructor, `_load`, `refresh_tle`, new `set_tle_text`)
- Test: `server/tests/test_astro.py`

**Interfaces:**
- Consumes: `Db.state_get/state_set`.
- Produces: `Iss(cfg, db=None, session=None, issues=None, tle_text=None)`; `set_tle_text(text, fetched) -> bool`; everything else unchanged. State keys `tle` (text) and `tle_fetched` (epoch).

- [ ] **Step 1: Adapt `server/tests/test_astro.py`**

```python
def _iss():
    with open(os.path.join(FIXTURES, "iss.tle"), encoding="utf-8") as f:
        return Iss(make_cfg(home_lat=BERLIN[0], home_lon=BERLIN[1]), tle_text=f.read())
```

Add:

```python
def test_tle_lives_in_the_database(tmp_path):
    from flightpanel.db import Db

    db = Db(str(tmp_path / "f.db"))
    with open(os.path.join(FIXTURES, "iss.tle"), encoding="utf-8") as f:
        text = f.read()
    iss = Iss(make_cfg(), db=db)
    assert iss.sat is None and iss.needs_tle(0)
    assert iss.set_tle_text(text, fetched=1791654544)
    again = Iss(make_cfg(), db=db)
    assert again.tle == iss.tle and again.tle_fetched == 1791654544 and not again.needs_tle(1791654544 + 3600)
    assert not again.set_tle_text("Too many requests", fetched=0)
```

- [ ] **Step 2: Run to verify failure.**

- [ ] **Step 3: Change `Iss` in `server/flightpanel/astro.py`**

```python
class Iss:
    def __init__(self, cfg, db=None, session: aiohttp.ClientSession | None = None, issues=None,
                 tle_text: str | None = None):
        """db: Bahndaten aus der Datenbank (state: tle, tle_fetched); tle_text: fertige
        Bahndaten, für Tests."""
        self.cfg = cfg
        self.db = db
        self.session = session
        self.issues = issues
        self.tle: tuple[str, str] | None = None
        self.sat = None
        self.tle_fetched: float = 0
        self.passes: list[Pass] = []
        self.passes_at: float = 0
        self.last_error: str | None = None
        if tle_text is not None:
            self.set_tle_text(tle_text, time.time())
        elif db is not None:
            self._load()

    # ----------------------------------------------------------- Bahndaten --
    def _load(self) -> None:
        text = self.db.state_get("tle")
        tle = parse_tle(text) if text else None
        if tle:
            self._set(tle)
            self.tle_fetched = self.db.state_get("tle_fetched", 0)

    def set_tle_text(self, text: str, fetched: float) -> bool:
        """Bahndaten übernehmen (Celestrak-Antwort oder alte tle.txt) und merken."""
        tle = parse_tle(text)
        if not tle:
            return False
        self._set(tle)
        self.tle_fetched = fetched
        if self.db is not None:
            self.db.state_set("tle", "ISS (ZARYA)\n" + "\n".join(tle) + "\n")
            self.db.state_set("tle_fetched", fetched)
        return True
```

In `refresh_tle`, replace the block

```python
                tle = parse_tle(await r.text())
                if not tle:
                    raise RuntimeError("keine Bahnelemente in der Antwort")
                self._set(tle)
                self.tle_fetched = time.time()
                self.last_error = None
                write_atomic(self.path, "ISS (ZARYA)\n" + "\n".join(tle) + "\n")
                return True
```

with

```python
                    text = await r.text()
                if not self.set_tle_text(text, time.time()):
                    raise RuntimeError("keine Bahnelemente in der Antwort")
                self.last_error = None
                return True
```

(mind the indentation: `text = await r.text()` stays inside the `async with`). Remove the now-unused imports `os` and `write_atomic` if ruff reports them.

- [ ] **Step 4: Run `test_astro.py`; expect pass. Ruff clean.**

- [ ] **Step 5: Commit**

```bash
git add server/flightpanel/astro.py server/tests/test_astro.py
git commit -m "ISS-Bahndaten liegen in der Datenbank statt in tle.txt

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 7: `Config` — server fields from `.env`, panel fields from the database

**Files:**
- Modify: `server/flightpanel/config.py`
- Test: `server/tests/test_config.py` (rewrite)

**Interfaces:**
- Produces: `_env(name, default, parse=None, panel: tuple[str, str] | None = None)`; module functions `panel_fields() -> list[Field]`, `is_panel_key(key) -> bool`, `default_text(field) -> str`, `panel_settings_from_env(env) -> dict[str, str]`; `Config.from_env(env=None)` (server fields only), `Config.for_panel(server, settings: Mapping[str, str]) -> Config`; new field `lan_panel` (`LAN_PANEL`, default `"home"`); constant `CARD_PREFIX = "CARD_"`.

- [ ] **Step 1: Rewrite `server/tests/test_config.py`**

Keep `env()`, `test_home_airport_from_airport_list`, `test_brightness_schedule`, `test_airport_height_and_station_checks` unchanged. Add the helper and replace the rest:

```python
from flightpanel.config import (Config, default_text, in_hours, is_panel_key, panel_fields,
                                panel_settings_from_env)


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
```

- [ ] **Step 2: Run to verify failures** (`for_panel`, `panel_fields` … do not exist).

- [ ] **Step 3: Change `server/flightpanel/config.py`**

Module docstring: replace the first paragraph with

```
"""Einstellungen. Server-Einstellungen (Flughafen, Quellen, Logos, Betrieb) liest
Config.from_env() beim Start aus Umgebungsvariablen (.env, jede in .env.example erklärt).
Panel-Einstellungen (Standort, Sichtfeld, Anzeige, Helligkeit, Ruhezeit, Karten: die Felder
mit panel=...) liegen je Panel in der Datenbank und kommen über Config.for_panel(); gesetzt
werden sie mit panels.py oder der Einstellungsseite, mit denselben Parsern und Fehlertexten.
Tests bauen Config(...) direkt.
```

and keep the rest of the docstring. Imports: add `replace` to the `dataclasses` import. Replace `_env`:

```python
def _env(name: str, default, parse=None, panel: tuple[str, str] | None = None):
    """Ein Feld aus der Umgebungsvariablen name (Server-Einstellung, .env) oder, mit
    panel=(Gruppe, Erklärung), eine Panel-Einstellung (Datenbank, panels.py; beim ersten
    Start einmalig aus einer älteren .env übernommen). parse wandelt den Text; ohne parse
    der Typ des Defaults."""
    if parse is None:
        parse = _bool if isinstance(default, bool) else type(default)
    return field(default=default, metadata={"env": name, "parse": parse, "panel": panel})


CARD_PREFIX = "CARD_"
```

Mark the panel fields (same defaults and parsers as today; only the `panel=` argument is new; the comment lines next to them may go):

```python
    home_lat: float = _env("HOME_LAT", 52.52, panel=("Standort", "Breitengrad des Fensters, Dezimalgrad"))
    home_lon: float = _env("HOME_LON", 13.40, panel=("Standort", "Längengrad des Fensters, Dezimalgrad"))
    home_alt_m: float = _env("HOME_ALT_M", 40.0, panel=("Standort", "Augenhöhe am Fenster über dem Meer, Meter"))
    radius_km: float = _env("RADIUS_KM", 40.0, panel=("Standort", "angezeigt wird, was höchstens so weit weg ist, km"))
    max_alt_m: float = _env("MAX_ALT_M", 6000.0, panel=("Standort", "... und höchstens so hoch, Meter"))
    scan_radius_km: float = _env("SCAN_RADIUS_KM", 40.0,
                                 panel=("Standort", "Abrufradius für Statistik, Seltenes und Notfälle, km; leer = RADIUS_KM"))
    view_from: float = _env("VIEW_FROM", 0.0, panel=("Sichtfeld", "wo es beginnt, Grad im Uhrzeigersinn (0 Nord, 90 Ost)"))
    view_to: float = _env("VIEW_TO", 360.0, panel=("Sichtfeld", "wo es endet; 0 bis 360 = rundum"))
    view_min_elev: float = _env("VIEW_MIN_ELEV", 0.0, panel=("Sichtfeld", "Mindesthöhe über dem Horizont, Grad (Häuser, Bäume)"))
    view_max_elev: float = _env("VIEW_MAX_ELEV", 90.0, panel=("Sichtfeld", "Obergrenze, Grad (Balkon darüber)"))
    view_max_km: float = _env("VIEW_MAX_KM", 100.0, panel=("Sichtfeld", "Sichtweite, km"))
    view_only: bool = _env("VIEW_ONLY", False, panel=("Sichtfeld", "1: nur zeigen, was im Sichtfeld ist"))
    show_other: bool = _env("SHOW_OTHER", True,
                            panel=("Sichtfeld", "1: Privatflieger, Hubschrauber, Polizei, Militär, wenn keine Airline dran ist"))
    layout: str = _env("LAYOUT", "C", parse_layout, panel=("Anzeige", "C Logo und drei Zeilen, A ohne Logo, B zwei Flüge"))
    max_flights: int = _env("MAX_FLIGHTS", 3, _int(1), panel=("Anzeige", "so viele Flüge wechseln sich ab"))
    calm: bool = _env("CALM", False, panel=("Anzeige", "1: ruhiger Modus (nichts blinkt, seltener Wechsel, nur Landezeit und Höhe)"))
    page_seconds: int = _env("PAGE_SECONDS", 4, _int(1), panel=("Anzeige", "Sekunden je Infoseite"))
    flight_seconds: int = _env("FLIGHT_SECONDS", 0, _int(0), panel=("Anzeige", "Sekunden je Flug mindestens; 0 = jede Infoseite einmal"))
    info_pages: tuple[str, ...] = _env("INFO_PAGES", INFO_PAGES, _pages, panel=("Anzeige", "Infoseiten der Reihe nach: eta, alt, dist, callsign"))
    blink: bool = _env("BLINK", True, panel=("Anzeige", "0: nichts blinkt außer einem Notfall"))
    idle_seconds: int = _env("IDLE_SECONDS", 12, _int(1), panel=("Anzeige", "Sekunden je Leerlauf-Karte"))
    idle_clock_share: float = _env("IDLE_CLOCK_SHARE", 0.35, _share, panel=("Anzeige", "Anteil der Uhr an den Leerlauf-Karten, 0 bis 1"))
    brightness_day: int = _env("BRIGHTNESS_DAY", 90, _int(0, 255), panel=("Helligkeit", "tagsüber, 0 bis 255"))
    brightness_evening: int = _env("BRIGHTNESS_EVENING", 35, _int(0, 255), panel=("Helligkeit", "abends, 0 bis 255"))
    day_hours: tuple[int, int] = _env("DAY_HOURS", (8 * 60, 19 * 60), _hours, panel=("Helligkeit", "Tag von-bis, z. B. 08:00-19:00"))
    quiet_hours: tuple[int, int] = _env("QUIET_HOURS", (0, 6 * 60 + 30), _hours,
                                        panel=("Helligkeit", "Ruhezeit: Panel dunkel, kein Abruf, z. B. 00:00-06:30"))
```

(wrap lines over 120 characters). The two comments that say `siehe from_env` (next to `scan_radius_km` and `cards_off`) now point at `for_panel`. Add under `# --- Betrieb`: `lan_panel: str = _env("LAN_PANEL", "home")   # das Panel am LAN-Port (:8080)`. Replace `from_env` and add `for_panel`:

```python
    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "Config":
        """Server-Einstellungen aus Umgebungsvariablen; leere zählen wie nicht gesetzte.
        Panel-Felder bleiben auf ihren Defaults (dafür: for_panel)."""
        env = os.environ if env is None else env
        values = {}
        for f in fields(cls):
            name = f.metadata.get("env")
            if not name or f.metadata.get("panel"):
                continue
            raw = (env.get(name) or "").strip()
            if raw:
                try:
                    values[f.name] = f.metadata["parse"](raw)
                except ValueError as e:
                    raise ValueError(f"{name}={raw!r}: {e}") from None
        for name, instead in REMOVED.items():
            if (env.get(name) or "").strip():
                log.warning("%s gibt es nicht mehr und wird ignoriert; stattdessen %s", name, instead)
        _airport_from_list(values, values.get("data_dir", cls.data_dir))
        return cls(**values)

    @classmethod
    def for_panel(cls, server: "Config", settings: Mapping[str, str]) -> "Config":
        """server plus die Einstellungen eines Panels: rohe Strings wie in .env, aus der
        Datenbank oder von panels.py, geprüft mit denselben Parsern und Fehlertexten.
        Nicht Gesetztes bleibt wie in server (im Betrieb: die Defaults). Karten gelten,
        sobald eine CARD-Zeile dabei ist: CARD_<ID>=0 schaltet ab."""
        known = {f.metadata["env"]: f for f in panel_fields()}
        unknown = sorted(k for k in settings if not is_panel_key(k))
        if unknown:
            raise ValueError(f"keine Panel-Einstellung: {', '.join(unknown)}; möglich: "
                             f"{', '.join(known)}, CARD_<ID>")
        values = {}
        for name, f in known.items():
            raw = (settings.get(name) or "").strip()
            if raw:
                try:
                    values[f.name] = f.metadata["parse"](raw)
                except ValueError as e:
                    raise ValueError(f"{name}={raw!r}: {e}") from None
        if "radius_km" in values and "scan_radius_km" not in values:
            values["scan_radius_km"] = values["radius_km"]
        if values.get("calm"):
            kept = [k for k in CALM_PRESET if k in values]
            if kept:
                log.warning("CALM=1, aber ausdrücklich gesetzt und damit gültig: %s",
                            ", ".join(f"{_env_name(k)}={values[k]}" for k in kept))
            for k, v in CALM_PRESET.items():
                values.setdefault(k, v)
        cards = [k for k in settings if k.startswith(CARD_PREFIX)]
        if cards:
            values["cards_off"] = frozenset(k[len(CARD_PREFIX):].lower() for k in cards
                                            if settings[k].strip() and not _bool(settings[k]))
        return replace(server, **values)
```

Add the module functions after `_env_name`:

```python
def panel_fields() -> list:
    """Die Panel-Einstellungen (Felder mit panel=...), in der Reihenfolge der Klasse."""
    return [f for f in fields(Config) if f.metadata.get("panel")]


def is_panel_key(key: str) -> bool:
    return any(f.metadata["env"] == key for f in panel_fields()) or (
        key.startswith(CARD_PREFIX) and len(key) > len(CARD_PREFIX))


def default_text(f) -> str:
    """Default eines Feldes, so wie man ihn setzen würde ("08:00-19:00", "eta,alt", "1")."""
    v = f.default
    if isinstance(v, bool):
        return "1" if v else "0"
    if f.metadata["parse"] is _hours:
        return f"{v[0] // 60:02d}:{v[0] % 60:02d}-{v[1] // 60:02d}:{v[1] % 60:02d}"
    if isinstance(v, tuple):
        return ",".join(v)
    return str(v)


def panel_settings_from_env(env: Mapping[str, str]) -> dict[str, str]:
    """Panel-Einstellungen aus einer (älteren) .env: beim ersten Start einmalig ins Panel
    übernommen, danach ignoriert. Karten nur, wenn abgeschaltet; an ist der Default."""
    out = {f.metadata["env"]: env[f.metadata["env"]].strip() for f in panel_fields()
           if (env.get(f.metadata["env"]) or "").strip()}
    out.update({k: v.strip() for k, v in env.items()
                if k.startswith(CARD_PREFIX) and len(k) > len(CARD_PREFIX) and v.strip() and not _bool(v)})
    return out
```

- [ ] **Step 4: Run `test_config.py`; expect pass. Ruff clean (line length!).**

Also run the whole suite once: the only failures left should be in tests that build `App` (fixed in Task 8).

- [ ] **Step 5: Commit**

```bash
git add server/flightpanel/config.py server/tests/test_config.py
git commit -m "Config: Server-Einstellungen aus .env, Panel-Einstellungen je Panel (for_panel)

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 8: `App` — database, panel row `home`, one-time JSON import, save once a minute

**Files:**
- Modify: `server/flightpanel/main.py` (docstring, imports, `SAVE_EVERY_S`, new `absorb_legacy`, `App.__init__`, `App.save`, `App.stop`)
- Modify: `server/flightpanel/db.py` (`DB_FILE`, `db_path()`), `server/flightpanel/fetch_logos.py` (uses `db_path`)
- Test: `server/tests/test_app.py` (new); `server/tests/test_web.py` and `test_dream.py` must pass unchanged

**Interfaces:**
- Consumes: `Db`, `Config.for_panel`, `panel_settings_from_env`, the stores' `load_legacy`/`set_tle_text` (Tasks 1–7).
- Produces: `App(cfg, env=None)` where `cfg` is the **server** config (`Config.from_env`) and `env` the mapping with possible legacy panel keys (default `os.environ`); attributes `server_cfg`, `cfg` (the LAN panel's config), `db`; `absorb_legacy(data_dir, cache, issues, stats, logos, iss) -> list[str]`; `App.save()` writes all stores in one transaction.

- [ ] **Step 1: Write `server/tests/test_app.py`**

```python
"""Start des Servers: Datenbank, Panel home, einmalige Übernahme alter JSON-Dateien und .env-Werte."""

import asyncio
import json
import os
import shutil
import time

import pytest

from conftest import FIXTURES, make_cfg
from flightpanel.main import App


def test_old_json_files_are_taken_over_once(tmp_path):
    t = time.time()
    (tmp_path / "logos").mkdir()
    (tmp_path / "cache.json").write_text(json.dumps({"route:EZY1": [t + 3600, {"origin": "LGW"}]}))
    (tmp_path / "issues.json").write_text(json.dumps({"since": t - 100, "items": {"exception:x": {
        "kind": "exception", "key": "x", "seen": 1, "days": 1, "first": t, "last": t, "callsigns": []}}}))
    (tmp_path / "stats.json").write_text(json.dumps({"since": t - 1000, "dex": {"easyJet": {
        "first": t, "last": t, "code": "EZY", "name": "easyJet", "known": True, "callsign": "EZY1", "fanfare": False}},
        "course": {"now": 249, "prev": None, "since": t, "seen": t}}))
    (tmp_path / "logos" / ".missing.json").write_text(json.dumps({"FX": t}))
    shutil.copy(os.path.join(FIXTURES, "iss.tle"), tmp_path / "tle.txt")
    cfg = make_cfg(mock=True, data_dir=str(tmp_path), metar=False, logo_fetch=False)

    async def run():
        app = App(cfg, env={})
        try:
            assert app.cache.get("route:EZY1") == {"origin": "LGW"}
            assert "exception:x" in app.issues.items and "easyJet" in app.stats.dex and app.stats.course == 249
            assert app.logos.missing() == ["FX"] and app.iss.tle is not None
            for name in ("cache.json", "issues.json", "stats.json", "tle.txt", "logos/.missing.json"):
                assert not (tmp_path / name).exists() and (tmp_path / f"{name}.imported").exists(), name
            again = App(cfg, env={})                      # zweiter Start: alles aus der Datenbank
            assert "easyJet" in again.stats.dex and again.cache.get("route:EZY1") and again.iss.tle == app.iss.tle
            assert again.issues.since == app.issues.since
            await again.session.close()
        finally:
            await app.session.close()

    asyncio.run(run())


def test_env_panel_keys_are_taken_over_on_the_first_start_then_ignored(tmp_path, caplog):
    cfg = make_cfg(mock=True, data_dir=str(tmp_path), metar=False, iss=False, logo_fetch=False)

    async def run():
        first = App(cfg, env={"HOME_LAT": "51.0", "CARD_PIGEON": "0", "CARD_CLOCK": "1"})
        try:
            assert first.cfg.home_lat == 51.0 and first.cfg.cards_off == {"pigeon"}
            assert first.db.panel_get("home")["settings"] == {"HOME_LAT": "51.0", "CARD_PIGEON": "0"}
            assert first.server_cfg.home_lat == cfg.home_lat          # der Server selbst bleibt, wie er war
        finally:
            await first.session.close()
        with caplog.at_level("WARNING"):
            second = App(cfg, env={"HOME_LAT": "40.0"})
        try:
            assert second.cfg.home_lat == 51.0
            assert "HOME_LAT" in caplog.text and "panels set home" in caplog.text
        finally:
            await second.session.close()

    asyncio.run(run())


def test_a_bad_old_env_value_stops_the_start_and_is_not_stored(tmp_path):
    """Wie früher bei .env: Unsinn bricht den Start mit dem Namen ab. Er landet aber nicht in
    der Datenbank, sonst hülfe es nicht mehr, die .env zu reparieren."""
    cfg = make_cfg(mock=True, data_dir=str(tmp_path), metar=False, iss=False, logo_fetch=False)

    async def run():
        with pytest.raises(ValueError, match="HOME_LAT='hier'"):
            App(cfg, env={"HOME_LAT": "hier"})
        app = App(cfg, env={"HOME_LAT": "51.0"})                # .env repariert: jetzt übernommen
        try:
            assert app.cfg.home_lat == 51.0
        finally:
            await app.session.close()

    asyncio.run(run())


def test_save_writes_everything_and_the_stores_reload_it(tmp_path):
    cfg = make_cfg(mock=True, data_dir=str(tmp_path), metar=False, iss=False, logo_fetch=False)

    async def run():
        app = App(cfg, env={})
        try:
            await app._poll(time.time())
            app.cache.set("route:X", {"origin": "LGW"}, ttl=600)
            app.issues.note("exception", "KeyError x.py:1")
            app.save()
            again = App(cfg, env={})
            assert again.cache.get("route:X") == {"origin": "LGW"} and "exception:KeyError x.py:1" in again.issues.items
            assert again.stats.last_obs == app.stats.last_obs > 0           # observe() lief und wurde geschrieben
            await again.session.close()
        finally:
            await app.session.close()

    asyncio.run(run())
```

- [ ] **Step 2: Run `test_app.py`; expect failures** (`App` has no `env`, stores still get paths).

- [ ] **Step 3: Change `server/flightpanel/main.py`**

Docstring: replace the last sentence `Zwischenstände speichern. web.py liefert … Alle Dateien liegen in DATA_DIR (Docker: ./data).` with `Geändertes in die Datenbank schreiben (db.py: DATA_DIR/flightpanel.db). web.py liefert Bilder und Zustand aus, gerendert wird bei jedem Abruf (render.py). Logos und mitgelieferte Daten liegen in DATA_DIR (Docker: ./data).`

One source for the file name: in `server/flightpanel/db.py`, below `log = logging.getLogger("db")`, add

```python
DB_FILE = "flightpanel.db"


def db_path(data_dir: str) -> str:
    """Die Datenbank in DATA_DIR; Server, fetch_logos und panels öffnen dieselbe Datei."""
    return os.path.join(data_dir, DB_FILE)
```

and use it in `server/flightpanel/fetch_logos.py` (`Db(db_path(cfg.data_dir), put_aside=False)`; drop `import os` there if it becomes unused).

Imports: add `from collections.abc import Mapping`; change `from .config import Config` to `from .config import Config, panel_settings_from_env`; add `from .db import Db, db_path` and `from .storage import read_json` (keep alphabetical order for ruff's isort).

Constant: `SAVE_EVERY_S = 60      # Geändertes in die Datenbank; auch die Nachtaufräumung hängt daran`.

Add before `class App`:

```python
def absorb_legacy(data_dir: str, cache: Cache, issues: Issues, stats: Stats, logos, iss: Iss | None) -> list[str]:
    """Zustand aus den JSON-Dateien früherer Fassungen (bis Oktober 2026) einmalig übernehmen.
    Gibt die übernommenen Dateien zurück; App benennt sie in *.imported um, sobald das
    Übernommene gespeichert ist. Eine unlesbare oder unbrauchbare Datei bleibt, wie sie ist,
    und steht im Log; der Start geht weiter."""
    taken = []
    for name, store in (("cache.json", cache), ("issues.json", issues), ("stats.json", stats),
                        (os.path.join("logos", ".missing.json"), logos)):
        path = os.path.join(data_dir, name)
        if not os.path.exists(path):
            continue
        raw = read_json(path)
        if not isinstance(raw, dict):                   # unlesbar (read_json hat es geloggt) oder kein Objekt
            log.warning("%s nicht übernommen, bleibt liegen", name)
            continue
        try:
            store.load_legacy(raw)
        except Exception as e:                          # von Hand verändert: lieber ohne als gar nicht starten
            log.warning("%s nicht übernommen (%s), bleibt liegen", name, e)
            continue
        taken.append(path)
    path = os.path.join(data_dir, "tle.txt")
    if iss is not None and os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            iss.set_tle_text(f.read(), os.path.getmtime(path))
        taken.append(path)
    if taken:
        log.info("Aus früheren Fassungen übernommen: %s", ", ".join(os.path.relpath(p, data_dir) for p in taken))
    return taken
```

Replace the beginning of `App.__init__` (up to and including the `# Anzeige` block) with:

```python
    def __init__(self, cfg: Config, env: Mapping[str, str] | None = None):
        """cfg: die Server-Einstellungen (Config.from_env). Das Panel am LAN-Port (LAN_PANEL)
        bekommt dazu seine Einstellungen aus der Datenbank; eine ältere .env mit Standort und
        Sichtfeld (env, sonst os.environ) wird beim ersten Start einmalig übernommen."""
        self.server_cfg = cfg
        data = cfg.data_dir
        self.db = Db(db_path(data))
        leftover = panel_settings_from_env(os.environ if env is None else env)
        if self.db.panel_get(cfg.lan_panel) is None:
            Config.for_panel(cfg, leftover)     # prüft: Unsinn in der alten .env bricht ab, ohne gespeichert zu werden
            self.db.panel_add(cfg.lan_panel, settings=leftover)
            if leftover:
                log.info("Panel-Einstellungen aus .env übernommen: %s", ", ".join(sorted(leftover)))
        elif leftover:
            log.warning("%s in .env gilt nicht mehr, Panel-Einstellungen liegen in der Datenbank. Ändern mit: "
                        "python -m flightpanel.panels set %s KEY=WERT", ", ".join(sorted(leftover)), cfg.lan_panel)
        self.cfg = cfg = Config.for_panel(cfg, self.db.panel_get(cfg.lan_panel)["settings"])
        self.session = aiohttp.ClientSession(headers={"User-Agent": USER_AGENT})
        self.issues = Issues(self.db)
        self.cache = Cache(self.db)
        self.names = load_airlines(data)

        # Quellen
        self.adsblol = AdsbLol(self.session)
        self.opensky = OpenSky(self.session, cfg.opensky_client_id, cfg.opensky_client_secret)
        self.adsb = AdsbDb(self.session, cfg.adsbdb_base, self.cache, self.issues)
        self.adsbim = AdsbIm(self.session, self.cache, self.issues) if cfg.route_adsbim else None
        self.origins = (Origins(self.session, self.cache, load_airports(data), self.issues,
                                auth=None if self.opensky.anonymous else self.opensky.auth_headers)
                        if cfg.track_origin else None)
        self.metar = MetarSource(self.session, cfg.metar_station, self.issues) if cfg.metar else None
        self.iss = Iss(cfg, self.db, self.session, self.issues) if cfg.iss else None

        # Anzeige
        self.logos = make_logo_store(cfg, self.session, self.db)
        self.stats = Stats(self.db, cfg.lan_panel, cfg, self.names, load_rare(data))
```

Keep the rest of `__init__` as it is, and append at its very end:

```python
        taken = absorb_legacy(data, self.cache, self.issues, self.stats, self.logos, self.iss)
        if taken:
            self.save()                         # erst gespeichert, dann umbenannt: ein Absturz dazwischen verliert nichts
            for path in taken:
                os.replace(path, path + ".imported")
```

Add to `server/tests/test_app.py`:

```python
def test_a_damaged_old_file_is_left_alone_and_the_start_goes_on(tmp_path, caplog):
    (tmp_path / "issues.json").write_text(json.dumps({"since": 1, "items": None}))     # von Hand verändert
    (tmp_path / "cache.json").write_text("[1, 2]")                                        # kein Objekt
    cfg = make_cfg(mock=True, data_dir=str(tmp_path), metar=False, iss=False, logo_fetch=False)

    async def run():
        with caplog.at_level("WARNING"):
            app = App(cfg, env={})
        try:
            assert app.issues.items == {} and len(app.cache) == 0
            assert (tmp_path / "issues.json").exists() and (tmp_path / "cache.json").exists()
            assert "issues.json nicht übernommen" in caplog.text and "cache.json nicht übernommen" in caplog.text
        finally:
            await app.session.close()

    asyncio.run(run())
```

Replace `save`:

```python
    def save(self) -> None:
        """Geändertes in die Datenbank, als eine Transaktion."""
        with self.db.transaction():
            self.cache.save()
            self.issues.save()
            self.stats.save()
```

In `stop`, after `await self.session.close()` add `self.db.close()`.

- [ ] **Step 4: Run the whole suite; expect everything green (`test_app`, `test_web`, `test_dream` included). Ruff clean.**

- [ ] **Step 5: Commit**

```bash
git add server/flightpanel/main.py server/flightpanel/db.py server/flightpanel/fetch_logos.py server/tests/test_app.py
git commit -m "Server: Panel home aus der Datenbank, alte JSON-Dateien und .env-Werte einmalig übernehmen

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 9: `panels` CLI

**Files:**
- Create: `server/flightpanel/panels.py`
- Modify: `server/flightpanel/db.py` (one line: re-export `DatabaseError`)
- Modify: `server/flightpanel/config.py` (the panel-id rule, once: `panel_id()` parser, used for `LAN_PANEL` and by `panels add`), `server/tests/test_config.py` (one test)
- Test: `server/tests/test_panels.py`

**Interfaces:**
- Consumes: `Db` panel rows, `Config.from_env`, `Config.for_panel`, `panel_fields`, `default_text`.
- Produces: `python -m flightpanel.panels {list,keys,show,set,unset,add,remove,backup}`; `main(argv=None) -> int` (0 ok, 2 on a `ValueError`, message on stderr starting with `Fehler:`).

- [ ] **Step 1: Write `server/tests/test_panels.py`**

```python
"""panels.py: Einstellungen je Panel setzen und prüfen, Panels anlegen und löschen."""

import re

import pytest

from flightpanel import panels
from flightpanel.db import Db


@pytest.fixture
def run(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.delenv("LAN_PANEL", raising=False)
    Db(str(tmp_path / "flightpanel.db")).panel_add("home")        # legt sonst der Server an

    def call(*argv):
        code = panels.main(list(argv))
        out = capsys.readouterr()
        return code, out.out, out.err

    return call


def test_set_show_unset(run):
    code, out, _ = run("set", "home", "home_lat=52.5", "VIEW_FROM=110", "CARD_PIGEON=0")
    assert code == 0 and "3 Einstellungen" in out
    assert run("show", "home")[1] == "CARD_PIGEON=0\nHOME_LAT=52.5\nVIEW_FROM=110\n"
    assert run("set", "home", "VIEW_FROM=")[0] == 0                   # leer: zurück auf den Default
    assert run("unset", "home", "card_pigeon")[0] == 0
    assert run("show", "home")[1] == "HOME_LAT=52.5\n"


def test_bad_values_name_the_setting_and_change_nothing(run):
    code, _, err = run("set", "home", "HOME_LAT=hier")
    assert code == 2 and "HOME_LAT='hier'" in err
    code, _, err = run("set", "home", "DATA_DIR=/x")
    assert code == 2 and "keine Panel-Einstellung: DATA_DIR" in err
    code, _, err = run("set", "home", "HOME_LAT")
    assert code == 2 and "KEY=WERT" in err
    code, _, err = run("set", "nirgends", "HOME_LAT=1")
    assert code == 2 and "gibt es nicht" in err
    assert run("show", "home")[1].startswith("home: nichts gesetzt")


def test_keys_lists_every_panel_setting_with_its_default(run):
    _, out, _ = run("keys")
    assert re.search(r"^HOME_LAT\s+52\.52\s+Standort: ", out, re.M)
    assert re.search(r"^QUIET_HOURS\s+00:00-06:30\s+Helligkeit: ", out, re.M)
    assert "CARD_<ID>" in out and "DATA_DIR" not in out and "POLL_INTERVAL" not in out


def test_add_list_remove(run):
    assert run("add", "anna", "--name", "Anna")[0] == 0
    assert run("add", "Anna!")[0] == 2 and run("add", "anna")[0] == 2
    _, out, _ = run("list")
    assert "home" in out and "anna" in out and "Anna" in out and "LAN-Port" in out
    code, _, err = run("remove", "anna")
    assert code == 2 and "--yes" in err
    assert run("remove", "anna", "--yes")[0] == 0 and "anna" not in run("list")[1]
    assert run("remove", "home", "--yes")[0] == 2                      # das Panel am LAN-Port bleibt


def test_unknown_cards_are_refused(run):
    """Ein Tippfehler in CARD_<ID> bliebe sonst still wirkungslos."""
    code, _, err = run("set", "home", "CARD_PIGEN=0")
    assert code == 2 and "unbekannte Karte: CARD_PIGEN" in err and "pigeon" in err
    assert run("set", "home", "card_pigeon=0")[0] == 0


def test_backup(run, tmp_path):
    assert run("backup", str(tmp_path / "copy.db"))[0] == 0 and (tmp_path / "copy.db").exists()


def test_a_broken_database_is_reported_and_left_alone(tmp_path, monkeypatch, capsys):
    """Der Befehl läuft neben dem Server: eine kaputte Datei nennt er und lässt sie liegen."""
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    junk = "kaputt, keine Datenbank " * 100
    (tmp_path / "flightpanel.db").write_text(junk)
    assert panels.main(["list"]) == 2
    err = capsys.readouterr().err
    assert err.startswith("Fehler: ") and "flightpanel.db" in err and "Traceback" not in err
    assert (tmp_path / "flightpanel.db").read_text() == junk and not list(tmp_path.glob("*.broken*"))
```

- [ ] **Step 2: Run to verify failure** (no module `flightpanel.panels`).

- [ ] **Step 3: Re-export the error in `server/flightpanel/db.py` and write `server/flightpanel/panels.py`**

In `db.py`, below `log = logging.getLogger("db")`, add (panels.py must not import sqlite3; the only-db-imports-sqlite3 test checks that):

```python
DatabaseError = sqlite3.DatabaseError       # für Werkzeuge neben dem Server (panels.py), ohne sqlite3-Import
```

In `config.py`, define the panel-id rule once (before `class Config`; add `import re` if missing) and use it as the parser of `LAN_PANEL`:

```python
PANEL_ID = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")


def panel_id(text: str) -> str:
    """Kennung eines Panels (LAN_PANEL, panels add): Kleinbuchstaben, Ziffern, - und _, höchstens 32 Zeichen."""
    if not PANEL_ID.match(text):
        raise ValueError("Kleinbuchstaben, Ziffern, - und _, höchstens 32 Zeichen")
    return text
```

```python
    lan_panel: str = _env("LAN_PANEL", "home", panel_id)       # das Panel am LAN-Port (:8080)
```

Test in `server/tests/test_config.py`:

```python
def test_lan_panel_follows_the_panel_id_rule():
    assert Config.from_env(env(LAN_PANEL="wohnzimmer")).lan_panel == "wohnzimmer"
    with pytest.raises(ValueError, match="LAN_PANEL"):
        Config.from_env(env(LAN_PANEL="Wohnzimmer"))
```

Then `panels.py`:

```python
"""Panels verwalten: python -m flightpanel.panels <Befehl> ...

  list                        alle Panels
  keys                        alle Panel-Einstellungen mit Default und Erklärung
  show <id>                   was bei einem Panel gesetzt ist
  set <id> KEY=WERT ...       setzen, geprüft wie .env (KEY= leer: zurück auf den Default)
  unset <id> KEY ...          zurück auf den Default
  add <id> [--name NAME]      weiteres Panel (das am LAN-Port, LAN_PANEL, legt der Server an)
  remove <id> --yes           Panel samt Album und Statistik löschen
  backup <datei>              Kopie der Datenbank, auch im laufenden Betrieb

Panel-Einstellungen sind Standort, Sichtfeld, Anzeige, Helligkeit und Ruhezeit und die
Leerlauf-Karten (CARD_<ID>=0); sie liegen je Panel in DATA_DIR/flightpanel.db. Alles
andere (Flughafen, Quellen, Logos, Betrieb) steht in .env.

Im Container:
  docker compose exec flightpanel python3 -m flightpanel.panels set home HOME_LAT=52.52 HOME_LON=13.40
Der Server liest die Einstellungen beim Start: danach docker compose restart flightpanel."""

import argparse
import sys
import time

from .cards import CARDS
from .config import CARD_PREFIX, Config, default_text, panel_fields, panel_id
from .db import DatabaseError, Db, db_path

CARD_IDS = sorted(c.id for c in CARDS)


def _check_card(key: str) -> None:
    """CARD_<ID> nur für Karten, die es gibt: ein Tippfehler bliebe sonst still wirkungslos."""
    if key.startswith(CARD_PREFIX) and key[len(CARD_PREFIX):].lower() not in CARD_IDS:
        raise ValueError(f"unbekannte Karte: {key}; bekannt: {', '.join(CARD_IDS)}")


def _need(db: Db, pid: str) -> dict:
    p = db.panel_get(pid)
    if p is None:
        have = ", ".join(x["id"] for x in db.panel_list()) or "-"
        raise ValueError(f"Panel {pid} gibt es nicht (vorhanden: {have})")
    return p


def cmd_list(db: Db, cfg: Config, args) -> None:
    for p in db.panel_list():
        when = time.strftime("%Y-%m-%d", time.localtime(p["created"]))
        lan = " (LAN-Port)" if p["id"] == cfg.lan_panel else ""
        print(f"{p['id']:<12} {p['name']:<20} {len(p['settings']):>2} Einstellungen, seit {when}{lan}")


def cmd_keys(db: Db, cfg: Config, args) -> None:
    for f in panel_fields():
        group, text = f.metadata["panel"]
        print(f"{f.metadata['env']:<18} {default_text(f):<22} {group}: {text}")
    print(f"{'CARD_<ID>':<18} {'1':<22} Karten: 0 schaltet eine Leerlauf-Karte ab ({', '.join(CARD_IDS)})")


def cmd_show(db: Db, cfg: Config, args) -> None:
    p = _need(db, args.id)
    for k, v in sorted(p["settings"].items()):
        print(f"{k}={v}")
    if not p["settings"]:
        print(f"{p['id']}: nichts gesetzt, alles auf Default (keys zeigt die Defaults)")


def _save(db: Db, cfg: Config, p: dict, new: dict[str, str]) -> None:
    Config.for_panel(cfg, new)                  # prüft; ValueError nennt die Einstellung
    db.panel_set_settings(p["id"], new)
    print(f"{p['id']}: {len(new)} Einstellungen gesetzt. Gilt nach: docker compose restart flightpanel")


def cmd_set(db: Db, cfg: Config, args) -> None:
    p = _need(db, args.id)
    new = dict(p["settings"])
    for item in args.pairs:
        key, sep, value = item.partition("=")
        key = key.strip().upper()
        if not sep or not key:
            raise ValueError(f"erwartet KEY=WERT, nicht {item!r}")
        if value.strip():
            _check_card(key)
            new[key] = value.strip()
        else:
            new.pop(key, None)
    _save(db, cfg, p, new)


def cmd_unset(db: Db, cfg: Config, args) -> None:
    p = _need(db, args.id)
    gone = {k.strip().upper() for k in args.keys}
    _save(db, cfg, p, {k: v for k, v in p["settings"].items() if k not in gone})


def cmd_add(db: Db, cfg: Config, args) -> None:
    try:
        panel_id(args.id)
    except ValueError as e:
        raise ValueError(f"id {args.id!r}: {e}") from None
    db.panel_add(args.id, args.name)
    print(f"{args.id} angelegt. Einstellungen: set {args.id} HOME_LAT=... HOME_LON=... VIEW_FROM=...")


def cmd_remove(db: Db, cfg: Config, args) -> None:
    p = _need(db, args.id)
    if p["id"] == cfg.lan_panel:
        raise ValueError(f"{p['id']} ist das Panel am LAN-Port (LAN_PANEL) und bleibt")
    if not args.yes:
        raise ValueError(f"löscht {p['id']} samt Album und Statistik; zum Bestätigen --yes")
    db.panel_remove(p["id"])
    print(f"{p['id']} gelöscht")


def cmd_backup(db: Db, cfg: Config, args) -> None:
    db.backup(args.file)
    print(f"Kopie: {args.file}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m flightpanel.panels", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)

    def add(name, func, *arguments):
        p = sub.add_parser(name)
        for flag, kw in arguments:
            p.add_argument(flag, **kw)
        p.set_defaults(func=func)

    add("list", cmd_list)
    add("keys", cmd_keys)
    add("show", cmd_show, ("id", {}))
    add("set", cmd_set, ("id", {}), ("pairs", {"nargs": "+", "metavar": "KEY=WERT"}))
    add("unset", cmd_unset, ("id", {}), ("keys", {"nargs": "+", "metavar": "KEY"}))
    add("add", cmd_add, ("id", {}), ("--name", {"default": ""}))
    add("remove", cmd_remove, ("id", {}), ("--yes", {"action": "store_true"}))
    add("backup", cmd_backup, ("file", {}))
    args = parser.parse_args(argv)
    try:
        cfg = Config.from_env()
    except ValueError as e:
        print(f"Fehler: {e}", file=sys.stderr)
        return 2
    path = db_path(cfg.data_dir)
    try:
        db = Db(path, put_aside=False)          # läuft neben dem Server: eine kaputte Datei bleibt liegen
    except DatabaseError as e:
        print(f"Fehler: {path}: {e}", file=sys.stderr)
        return 2
    try:
        args.func(db, cfg, args)
        return 0
    except ValueError as e:
        print(f"Fehler: {e}", file=sys.stderr)
        return 2
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run `test_panels.py`; expect pass. Ruff clean.**

- [ ] **Step 5: Commit**

```bash
git add server/flightpanel/panels.py server/flightpanel/db.py server/flightpanel/config.py server/tests/test_panels.py server/tests/test_config.py
git commit -m "panels: Panel-Einstellungen per Kommandozeile setzen, Panels anlegen, Datenbank sichern

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 10: Container hardening, CI as root, lint rule, docs for the new flow

**Files:**
- Modify: `server/Dockerfile`, `compose.yml`, `.github/workflows/server.yml`, `.github/workflows/escrow.yml`, `.gitignore`, `pyproject.toml`, `.env.example`, `README.md` (section "Einrichten"), `docs/screens.md` (two lines)

- [ ] **Step 1: `server/Dockerfile`** — create `/data` for uid 1000 and drop root:

```dockerfile
# Basis-Image fest per Digest; ein neueres übernimmt tools/escrow.py erst nach der Wartezeit
FROM python:3.12-slim@sha256:a6e34c598f2467ed0e9a8d349809fcd8b5c603269512df273a0bb1784edc11b1
RUN apt-get update && apt-get install -y --no-install-recommends fonts-dejavu-core \
 && rm -rf /var/lib/apt/lists/* \
 && mkdir -p /data && chown 1000:1000 /data
WORKDIR /app
COPY requirements.lock .
# Nur die Versionen und Dateien aus dem Lock (tools/escrow.py), geprüft per Prüfsumme
RUN pip install --no-cache-dir --require-hashes -r requirements.lock
COPY flightpanel ./flightpanel
ENV DATA_DIR=/data HTTP_PORT=8080 PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
# Läuft ohne Rechte: nur /data (und /tmp, siehe compose.yml) sind beschreibbar
USER 1000:1000
VOLUME ["/data"]
EXPOSE 8080
# /ok antwortet 503 mit Gründen, wenn etwas nicht stimmt (alte Daten, ESP32 weg, Fehler im Code)
HEALTHCHECK --interval=60s --timeout=5s --start-period=20s \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/ok', timeout=4)"
CMD ["python", "-m", "flightpanel.main"]
```

Keep the digest line exactly as it is in the current file (the one above is the current value; do not retype it from memory, copy it).

- [ ] **Step 2: `compose.yml`**

```yaml
services:
  flightpanel:
    build: ./server
    container_name: flightpanel
    restart: unless-stopped
    env_file: .env
    ports:
      - "8080:8080"
    volumes:
      - ./data:/data        # mitgelieferte Daten (airlines.json ...), Logos, Datenbank; gehört uid 1000
    # Härtung: nichts Schreibbares außer /data und /tmp, keine Rechte, keine neuen Rechte
    read_only: true
    tmpfs:
      - /tmp
    cap_drop:
      - ALL
    security_opt:
      - no-new-privileges:true
    labels:                 # ein Traefik mit Docker-Provider soll den Port ohne Anmeldung nicht veröffentlichen
      - traefik.enable=false
    logging:                # Docker-Log begrenzen (rund 1 MB am Tag), sonst wächst es ohne Ende
      driver: json-file
      options:
        max-size: "5m"
        max-file: "3"
```

- [ ] **Step 3: CI runs its `pip install` as root** — in `.github/workflows/server.yml` and in the `prepare` job of `.github/workflows/escrow.yml`, change `docker run --rm -v "$PWD:/repo"` to `docker run --rm --user root -v "$PWD:/repo"` (the image now drops to uid 1000, which cannot install into site-packages). Note for local use: rebuild `flightpanel-ci`/`flightpanel-ci-dev` the same way (`docker run --user root …` for the pip step).

- [ ] **Step 4: `.gitignore`** — under `# Im Betrieb erzeugt (Docker-Volume ./data)` add:

```
data/flightpanel.db*
data/*.imported
```

(keep the old `data/cache.json` … lines: leftovers of older installs stay ignored.)

- [ ] **Step 5: `pyproject.toml`** — `select = ["E", "F", "W", "B", "I", "UP", "RUF", "S608"]` with a comment line above: `# S608: SQL nur mit Platzhaltern, nie zusammengebaut (db.py und die Speicher)`.

- [ ] **Step 6: `.env.example`** — server settings only. Replace the whole file:

```
# Kopieren nach .env und anpassen. Was fehlt oder leer ist, hat den Default aus
# server/flightpanel/config.py (BER). Die .env steht nicht im Repo: Sie kann Zugangsdaten
# enthalten.
#
# Hier stehen nur Server-Einstellungen. Alles, was zum einzelnen Panel gehört (Standort,
# Sichtfeld, Anzeige, Helligkeit, Ruhezeit, Leerlauf-Karten), liegt in data/flightpanel.db
# und wird nach dem Start gesetzt:
#   docker compose exec flightpanel python3 -m flightpanel.panels keys           # alle, mit Default
#   docker compose exec flightpanel python3 -m flightpanel.panels set home HOME_LAT=52.52 HOME_LON=13.40
# Eine ältere .env mit diesen Werten wird beim ersten Start einmalig übernommen.

# --- Heimatflughafen ---
# IATA-Code. Position und METAR-Station kommen aus data/airports.json; nur für einen
# Flughafen, der dort fehlt, AIRPORT_LAT und AIRPORT_LON setzen (METAR_STATION = ICAO).
LOCAL_AIRPORT_IATA=BER
# AIRPORT_LAT=52.3617
# AIRPORT_LON=13.5023
# METAR_STATION=EDDB
AIRPORT_ALT_M=48
# Kurs im Endanflug einer Landerichtung, für Landefolge und Landerichtung
# (BER 25 = 249°, MUC 26 = 263°); die Gegenrichtung ergibt sich.
RUNWAY_COURSE=249
# Nachtflugverbot am Flughafen (BER: 0 bis 5 Uhr), für die Karte "curfew"
CURFEW_HOURS=00:00-05:00
# Wetter (METAR) für Wolken-, Nebel-, Gewitterkarten und den Wind auf der Pistenkarte
METAR=1

# --- Datenquellen ---
# MOCK=1 zeigt Fantasieflüge, ohne Netz. Zum Layout-Entwerfen und für den Hardwaretest.
MOCK=1
# adsblol: Community-Netz (ADS-B + MLAT), ohne Account und ohne Tageslimit, liefert
# auch Typ und Kategorie; fällt es aus, springt OpenSky ein. opensky: nur OpenSky.
SOURCE=adsblol
# Sekunden zwischen zwei Abrufen (OpenSky: länger, wenn das Tagesbudget sonst nicht reicht)
POLL_INTERVAL=20
# Routen: adsb.im zuerst (aktueller), dann adsbdb. Kennt keine den Flug, kommt der
# Abflughafen aus der OpenSky-Flugspur (wo sie am Boden beginnt). 0 schaltet ab.
ADSBIM=1
TRACK_ORIGIN=1
# OpenSky-Account -> API Clients -> neuen Client anlegen. Ohne: anonym, 400 Credits
# am Tag (als Ersatzquelle reicht das).
OPENSKY_CLIENT_ID=
OPENSKY_CLIENT_SECRET=
# ISS-Bahndaten von Celestrak (einmal am Tag), für die Karte "iss"
ISS=1

# --- Anzeige (2 x Waveshare P2.5 64x32 nebeneinander) ---
WIDTH=128
HEIGHT=32

# --- Logos ---
# Fehlende Logos werden beim ersten Auftauchen einer Airline geholt und in data/logos/
# abgelegt. Nur annähernd quadratische Bildmarken (bis LOGO_MAX_ASPECT); Schriftzüge
# sind bei 32 x 32 unlesbar, dann der Platzhalter. Tochterbetriebe zeigen das Logo
# der Marke: Logo-Code in data/airlines.json (KLM Cityhopper -> KL). Eigene Logos
# (tools/make_logo.py, liegen in data/logos/own/) gehen den abgerufenen vor.
LOGO_FETCH=1
LOGO_SIZE=32
LOGO_MAX_ASPECT=1.5
# Eigene Quellen, kommagetrennt, {iata} oder {icao}, optional |Seitenverhältnis.
# Leer = Kiwi.com, FlightAware, Google (logos.DEFAULT_URLS).
# LOGO_URLS=https://images.kiwi.com/airlines/128x128/{iata}.png,https://www.flightaware.com/images/airline_logos/90p/{icao}.png

# --- Betrieb ---
# Zeitzone für Ruhezeit, Tagesstatistik und Uhr
TZ=Europe/Berlin
HTTP_PORT=8080
LOG_LEVEL=INFO
# Das Panel am LAN-Port (:8080); der Server legt es beim ersten Start an
# LAN_PANEL=home
# Ohne frische Daten: nach STALE_AFTER Sekunden gedimmt, nach EMPTY_AFTER leer
# STALE_AFTER=120
# EMPTY_AFTER=600
```

Before writing it, diff the current file's "Heimatflughafen", "Datenquellen", "Logos" and "Betrieb" blocks against the text above and keep the current wording wherever it differs (only the panel sections are removed and the header, `ISS`, `TZ` and `LAN_PANEL` lines move or appear).

- [ ] **Step 7: `README.md`** — afterwards grep `README.md` and `docs/` for `stats.json`, `issues.json`, `cache.json`, `tle.txt` and `.missing.json`: where they are named as live state files, say `data/flightpanel.db` instead (older installs' files are imported once and kept as `*.imported`). Then replace the section from `## Einrichten` up to (not including) `## Was angezeigt wird` with:

~~~markdown
## Einrichten

Serverweites steht in `.env` (nicht im Repo; jede Einstellung ist in [.env.example](.env.example) erklärt): Heimatflughafen, Datenquellen, Logos, Betrieb. Für echte Daten `MOCK=0`. Alles, was zum einzelnen Panel gehört (Standort, Sichtfeld, Anzeige, Helligkeit, Ruhezeit, Karten), liegt in `data/flightpanel.db` und wird nach dem Start mit einem Befehl gesetzt.

**1. Heimatflughafen** (`.env`). `LOCAL_AIRPORT_IATA` genügt: Position, Höhe und METAR-Station kommen aus `data/airports.json`. Dazu `RUNWAY_COURSE`, der Kurs im Endanflug einer Landerichtung (BER 25: 249°, MUC 26: 263°), und `CURFEW_HOURS` für das Nachtflugverbot. Für einen Flughafen, der nicht in der Liste steht, `AIRPORT_LAT`, `AIRPORT_LON`, `AIRPORT_ALT_M` und `METAR_STATION` setzen.

**2. Starten.** `docker compose up -d --build`. Beim ersten Start entsteht das Panel `home`; eine ältere `.env` mit Standort und Sichtfeld wird dabei einmalig übernommen. `./data` muss dem Benutzer 1000 gehören (der Container läuft ohne Rechte): auf Linux `chown -R 1000:1000 data`, unter Docker Desktop passt es von selbst.

**3. Standort und Sichtfeld.**

```bash
docker compose exec flightpanel python3 -m flightpanel.panels set home HOME_LAT=52.52 HOME_LON=13.40 HOME_ALT_M=40
```

```bash
docker compose exec flightpanel python3 -m flightpanel.panels set home VIEW_FROM=110 VIEW_TO=190 VIEW_MIN_ELEV=0.5 VIEW_MAX_KM=60 VIEW_ONLY=1
```

`HOME_LAT`, `HOME_LON` sind Dezimalgrad, `HOME_ALT_M` die Augenhöhe am Fenster über dem Meer. Angezeigt wird, was höchstens `RADIUS_KM` weit weg und `MAX_ALT_M` hoch ist. Das Sichtfeld: Richtung im Uhrzeigersinn von `VIEW_FROM` bis `VIEW_TO` (0 = Nord, 90 = Ost), Mindesthöhe über dem Horizont `VIEW_MIN_ELEV` (Häuser, Bäume), Obergrenze `VIEW_MAX_ELEV` (Balkon darüber), Sichtweite `VIEW_MAX_KM`; `VIEW_ONLY=1` zeigt nur, was im Sichtfeld ist. Eichen: am Fenster ein Flugzeug suchen und in `/display.json` → `nearby` dessen Richtung (`az`) und Höhenwinkel (`elev`) ablesen. `… panels keys` zeigt alle Panel-Einstellungen mit Default und Erklärung, `… panels show home` die gesetzten; sie gelten nach `docker compose restart flightpanel`.

**4. Karten und Ruhe.** Ebenso per `set home`: `CARD_<ID>=0` schaltet eine Leerlauf-Karte ab, z. B. `CARD_PIGEON=0`. `CALM=1` ist der ruhige Modus: nichts blinkt außer einem Notfall, Infoseiten wechseln alle 10 s statt 4 s, unten stehen nur Landezeit und Höhe/Tempo. Jede dieser Einzelheiten ist auch einzeln einstellbar (`PAGE_SECONDS`, `FLIGHT_SECONDS`, `INFO_PAGES`, `BLINK`, `IDLE_SECONDS`), ebenso Helligkeit und Ruhezeit (`BRIGHTNESS_DAY`, `BRIGHTNESS_EVENING`, `DAY_HOURS`, `QUIET_HOURS`).

Dann einmal die Logos holen: `docker compose exec flightpanel python3 -m flightpanel.fetch_logos`. Unsinnige Werte brechen den Start ab (`.env`) oder werden vom Befehl abgelehnt (`panels set`), jeweils mit dem Namen der Einstellung; Verdächtiges, etwa Koordinaten, die nicht zum Flughafen passen, steht als Warnung im Log. Die Werte unter `/health` zeigen, was wirklich gilt.

~~~

- [ ] **Step 8: `docs/screens.md`** — line 7: replace `` Jede Karte mit `CARD_<ID>=0` in `.env`, auch Uhr und Notfall. `` with `` Jede Karte mit `panels set home CARD_<ID>=0` (siehe README), auch Uhr und Notfall. ``; line 66: leave `` (`CARD_<ID>=0`) `` and `` (`CALM=1`) `` as they are (they name the setting, not the file).

- [ ] **Step 9: Verify everything end to end**

1. Whole suite and ruff: `... sh -c "ruff check . && python -m pytest -p no:cacheprovider -q"` → all green (119 old + the new tests), no warnings.
2. Rendering unchanged: `MSYS_NO_PATHCONV=1 docker run --rm -v "$(pwd -W):/repo" -w /repo flightpanel-ci-dev python tools/screenshots.py` then `git status --short docs/images` → empty.
3. Container from the worktree, mock data, hardened (the user's own container `flightpanel` keeps running on 8080, so use another name and port):

   ```bash
   cp .env.example .env && docker build -t flightpanel-wt server && docker run -d --name fp-wt -p 8090:8080 --env-file .env --read-only --tmpfs /tmp --cap-drop ALL --security-opt no-new-privileges -v "$(pwd -W)/data:/data" flightpanel-wt
   ```

   Then: `curl -s localhost:8090/health | head -c 300` (200, JSON), `docker exec fp-wt python -m flightpanel.panels list` (shows `home (LAN-Port)`), `docker exec fp-wt python -m flightpanel.panels set home HOME_LAT=52.5 CARD_PIGEON=0`, `docker restart fp-wt`, `docker logs fp-wt 2>&1 | tail -5` (no traceback), `curl -s localhost:8090/health` again (200), `docker exec fp-wt sh -c "id -u"` → `1000`, `docker exec fp-wt touch /app/x` → fails (read-only). Finally `docker rm -f fp-wt && rm -f .env data/flightpanel.db* && docker rmi flightpanel-wt`.
4. `git status --short` shows nothing untracked but the intended files.

- [ ] **Step 10: Commit**

```bash
git add server/Dockerfile compose.yml .github/workflows/server.yml .github/workflows/escrow.yml .gitignore pyproject.toml .env.example README.md docs/screens.md
git commit -m "Container ohne Rechte, CI als root, .env nur noch Server-Einstellungen, README zum panels-Befehl

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

## After the last task

1. Whole-branch review (fresh reviewer on the most capable model; `/code-review` on the branch is fine) with the spec open: SQL only parameterized, nothing personal in the repo, the LAN panel's behavior unchanged, `.env` legacy import exactly once.
2. Hand over for merging: push `worktree-freunde-sqlite` and open a PR (or merge locally once the main checkout is clean — another session currently has uncommitted changes there). After the merge on the laptop's main checkout: `docker compose up -d --build`, then check `docker compose logs --tail 20 flightpanel` for "Aus früheren Fassungen übernommen: cache.json, issues.json, stats.json, …", `/stats` still lists the album, `data/*.imported` exist, `python3 -m flightpanel.panels show home` shows the values that used to be in `.env`.
3. Spec deviations to carry into the next plan (multi-panel polling): the database is flushed once a minute instead of after every poll (simpler, loses at most 60 s); `issues` and `rare` rows store JSON blobs rather than one column per field; the server reads panel settings at start (restart after `panels set`) — live reload is part of step 2.

