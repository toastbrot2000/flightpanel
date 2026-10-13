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
        self.execute("INSERT INTO state (key, value) VALUES (?, ?) "
                     "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
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
