"""SQLite-Datenbank für alles, was sich im Betrieb ändert: DATA_DIR/flightpanel.db.

Darin: Panels mit ihren Einstellungen (config.py: Panel-Felder), je Panel Statistik und
Sammelalbum (stats.py), der Zwischenspeicher der Routen- und Flugzeugdienste (cache.py),
Auffälligkeiten (issues.py), fehlende Logos (logos.py), Bahndaten der ISS (astro.py) und
Kleinkram in "state". Mitgelieferte Daten (airlines.json ...) bleiben JSON, Logos Dateien.

Nur dieses Modul importiert sqlite3. Die Speicher halten ihren Zustand im Arbeitsspeicher
und schreiben Geändertes in App.save() (main.py) in einer Transaktion; was sie sich dafür
vorgemerkt haben, vergessen sie erst per after_commit, wenn die Transaktion festgeschrieben
ist, sonst ginge es bei einem ROLLBACK verloren. Jedes SQL ist parametrisiert.
Schema-Änderungen kommen als weiteres Skript in MIGRATIONS, PRAGMA
user_version merkt sich den Stand der Datei. Jede Migration läuft samt neuem Stand in einer
eigenen Transaktion; wer die Schreibsperre bekommt, prüft den Stand noch einmal. So tun
Server und panels.py, die gleichzeitig eine neue Datei öffnen, nichts doppelt.

WAL, damit panels.py schreiben kann, während der Server läuft; geht das nicht
(Netzlaufwerk), tut es das normale Journal auch. Nur eine Datei, die SQLite für keine
Datenbank hält oder die beschädigt ist, wird als .broken beiseitegelegt, der Server startet
dann leer statt gar nicht. Das tut nur der Server (put_aside=True). Werkzeuge, die neben ihm
laufen (panels.py, fetch_logos.py), übergeben put_aside=False und brechen mit dem Fehler ab:
Der Server hat die Datei noch offen und schriebe sonst in die umbenannte weiter. Bei allem
anderen (gesperrt, Ein-/Ausgabefehler, scheiternde Migration) bleibt die Datei liegen und
der Start schlägt laut fehl: lieber kein Server als ein stillschweigend leeres Album."""

import json
import logging
import os
import sqlite3
import time
from collections.abc import Callable
from contextlib import contextmanager, suppress

log = logging.getLogger("db")

DatabaseError = sqlite3.DatabaseError       # für Werkzeuge neben dem Server (panels.py), ohne sqlite3-Import

DB_FILE = "flightpanel.db"
SIDE_FILES = ("", "-wal", "-shm", "-journal")     # die Datenbank und was SQLite neben ihr anlegt
COPY = ".bak"           # die nächtliche Kopie daneben, flightpanel.db.bak (main.App.dream)


def db_path(data_dir: str) -> str:
    """Die Datenbank in DATA_DIR; Server, fetch_logos und panels öffnen dieselbe Datei."""
    return os.path.join(data_dir, DB_FILE)


BUSY_MS = 5000        # so lange wartet SQLite auf eine fremde Sperre, bevor es "database is locked" meldet

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
    def __init__(self, path: str, put_aside: bool = True):
        self.path = path
        self._after_commit: list[Callable[[], None]] | None = None     # nur innerhalb von transaction()
        try:
            self.conn = self._connect()
        except sqlite3.DatabaseError as e:
            # Nur der Server (put_aside) legt eine Datei beiseite, und nur eine, die keine Datenbank oder beschädigt
            # ist. Gesperrt, Ein-/Ausgabefehler oder eine scheiternde Migration sagen nichts über die Datei: laut
            # scheitern, nichts verschieben. Ein Werkzeug daneben würde dem Server die offene Datei umbenennen.
            if not put_aside or _code(e) not in (sqlite3.SQLITE_NOTADB, sqlite3.SQLITE_CORRUPT):
                raise
            broken = self.path + ".broken"
            log.error("%s ist keine lesbare Datenbank (%s): liegt jetzt als %s beiseite, starte leer",
                      os.path.basename(self.path), e, os.path.basename(broken))
            for suffix in SIDE_FILES:
                try:
                    os.replace(self.path + suffix, broken + suffix)
                except FileNotFoundError:
                    pass
            self.conn = self._connect()

    def _connect(self) -> sqlite3.Connection:
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        conn = sqlite3.connect(self.path, autocommit=True)           # Transaktionen ausdrücklich, s. u.
        try:
            conn.row_factory = sqlite3.Row
            conn.execute(f"PRAGMA busy_timeout = {BUSY_MS}")
            deadline = time.monotonic() + BUSY_MS / 1000
            while True:                             # beim Umschalten auf WAL wartet busy_timeout nicht immer
                try:
                    mode = conn.execute("PRAGMA journal_mode = WAL").fetchone()[0]
                    break
                except sqlite3.OperationalError as e:
                    if _code(e) not in (sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED) or time.monotonic() > deadline:
                        raise
                    time.sleep(0.05)                # ein anderer Öffner schaltet gerade um
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
        # Der Stand wird erst ohne Sperre gelesen: Im Normalfall ist schon alles fertig und nichts wird gesperrt.
        while (version := _version(conn)) < len(MIGRATIONS):
            with _immediate(conn):                  # ab hier schreibt kein anderer Öffner dazwischen
                if _version(conn) == version:       # sonst war einer schneller, dann gilt sein Stand
                    conn.executescript(MIGRATIONS[version])      # bleibt in der Transaktion (autocommit=True)
                    conn.execute(f"PRAGMA user_version = {version + 1}")
                    log.info("Datenbank auf Stand %d", version + 1)

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
        with _immediate(self.conn):
            # Erst nach dem BEGIN: Ein (nicht erlaubtes) verschachteltes transaction() scheitert schon dort
            # und lässt die Warteschlange der äußeren Transaktion in Ruhe.
            queue = self._after_commit = []
            try:
                yield
            finally:
                self._after_commit = None
        for fn in queue:        # erst hier, nach gelungenem COMMIT: bei jedem Fehler kommt der Ablauf nicht bis dahin
            fn()

    def after_commit(self, fn: Callable[[], None]) -> None:
        """fn läuft, sobald die laufende Transaktion festgeschrieben ist; wird sie zurückgerollt (auch wenn erst
        das COMMIT scheitert), entfällt es. Außerhalb einer Transaktion ist alles schon für sich festgeschrieben:
        fn läuft sofort. So vergisst ein Speicher Geändertes erst, wenn es sicher in der Datenbank liegt."""
        if self._after_commit is None:
            fn()
        else:
            self._after_commit.append(fn)

    def state_get(self, key: str, default=None):
        row = self.one("SELECT value FROM state WHERE key = ?", (key,))
        return json.loads(row["value"]) if row else default

    def state_set(self, key: str, value) -> None:
        self.execute("INSERT INTO state (key, value) VALUES (?, ?) "
                     "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                     (key, json.dumps(value, ensure_ascii=False)))

    def backup(self, dest: str) -> None:
        """Kopie der Datenbank, auch im laufenden Betrieb, in sich stimmig (SQLite-Backup). Sie entsteht neben dest
        (dest.tmp) und ersetzt dest erst, wenn sie fertig ist: Eine abgebrochene Kopie lässt die vorige stehen, eine
        kaputte vorige hält die neue nicht auf. Nie auf die Datenbank selbst oder ihr -wal/-shm, auch nicht unter
        anderem Namen für dieselbe Datei: Die Kopie zerstörte sie."""
        if any(_same_file(dest, self.path + suffix) for suffix in SIDE_FILES):
            raise ValueError(f"{dest} ist die Datenbank selbst oder gehört zu ihr: Die Kopie braucht einen anderen "
                             "Namen")
        tmp = dest + ".tmp"
        _remove(tmp, tmp + "-journal")                  # Reste eines abgebrochenen Laufs
        try:
            out = sqlite3.connect(tmp)
            try:
                self.conn.backup(out)
            finally:
                out.close()
            os.replace(tmp, dest)
        except BaseException:
            _remove(tmp, tmp + "-journal")
            raise

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


def _code(e: sqlite3.Error) -> int:
    """Der SQLite-Fehlercode ohne die erweiterten Bits; 0, wenn der Fehler nicht von SQLite selbst kommt."""
    return getattr(e, "sqlite_errorcode", 0) & 0xFF


def _same_file(a: str, b: str) -> bool:
    """Dieselbe Datei, auch unter anderem Namen (Groß- und Kleinschreibung auf einem Windows-Laufwerk, Links); gibt
    es eine der beiden nicht, entscheidet der Pfad."""
    try:
        return os.path.samefile(a, b)
    except OSError:
        return os.path.realpath(a) == os.path.realpath(b)


def _remove(*paths: str) -> None:
    for path in paths:
        with suppress(OSError):
            os.remove(path)


def _version(conn: sqlite3.Connection) -> int:
    return conn.execute("PRAGMA user_version").fetchone()[0]


@contextmanager
def _immediate(conn: sqlite3.Connection):
    """BEGIN IMMEDIATE ... COMMIT. Scheitert etwas, auch das COMMIT selbst (etwa an einem aufgeschobenen
    Fremdschlüssel), wird zurückgerollt, soweit noch etwas offen ist: Sonst bliebe die Verbindung mitten in
    der Transaktion hängen und jede weitere scheiterte mit "cannot start a transaction within a transaction"."""
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield
        conn.execute("COMMIT")
    except BaseException:
        if conn.in_transaction:
            conn.execute("ROLLBACK")
        raise


def _panel(row: sqlite3.Row) -> dict:
    d = dict(row)
    d["settings"] = json.loads(d["settings"] or "{}")
    return d
