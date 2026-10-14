"""Auffälligkeiten im Betrieb sammeln, zum späteren Durchsehen (GET /issues):

  unknown_operator   Callsign mit Betreibercode, der nicht in airlines.json steht
  name_mismatch      unser Name passt nicht zu adsbdb (alte Daten dort oder Fehler bei uns)
  route_implausible  Route passt nicht zur Position, verworfen
  route_unknown      gezeigter Airline-Flug ohne Route
  source_error       Fehler einer Quelle (adsb.lol, OpenSky, adsbdb, adsb.im, METAR, ISS)
  rate_limit         HTTP 429 einer Quelle
  fallback           adsb.lol aus, OpenSky eingesprungen
  stale              Bild gedimmt oder leer, weil keine frischen Daten kamen
  esp32_gap          der ESP32 hat länger keinen Frame geholt
  exception          Fehler im Code, mit Datei und Zeile
  setting            gespeicherte Panel-Einstellung gilt nicht (mehr), übergangen (main.App)
  backup             die nächtliche Kopie der Datenbank ist gescheitert
  database           die Datenbank war kaputt und liegt beiseite, der Start war leer (db.Db);
                     /ok bleibt rot, solange der Eintrag steht, die nächtliche Kopie ruht

Pro Eintrag: wie oft gesehen (je Abruf), an wie vielen Tagen, erstes und letztes
Mal, bis zu zehn Callsigns, ein Beispiel. Liegt in der Datenbank (Tabelle issues,
Beginn in state; main.App.save() schreibt Geändertes) und übersteht Neustarts;
POST /issues/clear fängt von vorn an.

Nachts (main.App.dream) vergisst die Liste von selbst: Einmaliges (nur an einem Tag
gesehen) nach ONE_OFF_DAYS, alles andere, wenn es FORGET_DAYS nicht mehr aufgetreten
ist; Fehler im Code (exception) und eine beiseitegelegte Datenbank (database) erst
dann. Ist sie trotzdem voll, macht der am längsten nicht mehr gesehene Eintrag Platz,
nur nie eine beiseitegelegte Datenbank (database): Sie ist die Warnung, dass das Album
leer startete. Neues geht nie verloren."""

import json
import logging
import re
import time

from .db import Db
from .storage import is_number

log = logging.getLogger("issues")

MAX_KEYS = 1000
MAX_CALLSIGNS = 10
ONE_OFF_DAYS = 7        # nur an einem Tag gesehen: nach einer Woche vergessen
FORGET_DAYS = 30        # alles andere: nach 30 Tagen ohne neues Auftreten
KEEP_KINDS = {"exception", "database"}   # auch einmalig erst nach FORGET_DAYS
PINNED_KINDS = {"database"}              # weichen auch bei vollem /issues nie (eher wächst die Liste über MAX_KEYS)
DAY_S = 24 * 3600


def _ts(t: float) -> str:
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(t))


def _day(t: float) -> str:
    return time.strftime("%Y-%m-%d", time.localtime(t))


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9 ]", "", s.lower()).strip()


def _readable(it) -> bool:
    """Ein Eintrag in der Form, die note() anlegt und report(), forget() und App.problems() lesen: Art und Schlüssel
    als Text, Zähler und Zeitpunkte als Zahlen, Callsigns als Liste. days darf fehlen (ältere Dateien)."""
    return (isinstance(it, dict) and isinstance(it.get("kind"), str) and isinstance(it.get("key"), str)
            and all(is_number(it.get(f)) for f in ("seen", "first", "last")) and is_number(it.get("days", 1))
            and isinstance(it.get("callsigns"), list))


def names_match(ours: str, theirs: str) -> bool:
    """Grob: unser Kurzname steckt im adsbdb-Namen oder umgekehrt, Leerzeichen egal,
    oder er sind dessen Anfangsbuchstaben. "Norwegian" ~ "Norwegian Air Sweden AOC" ja,
    "airBaltic" ~ "Air Baltic" ja, "SAS" ~ "Scandinavian Airlines System" ja,
    "Sundair" ~ "City Airline" nein."""
    a, b = _norm(ours), _norm(theirs)
    if not a or not b:
        return True
    initials = "".join(w[0] for w in b.split())
    a0, b0 = a.replace(" ", ""), b.replace(" ", "")
    return a0 in b0 or b0 in a0 or a.split()[0] in b.split() or a == initials


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
        """Alte issues.json übernehmen (main.absorb_legacy, einmalig). Einträge, die report(), forget() und problems()
        so nicht lesen könnten (von Hand verändert: kein Objekt, ein Feld fehlt oder ist Text), fallen weg; eine Warnung
        nennt, wie viele. Ein Beginn, der keine Zahl ist, bleibt der jetzige. Ist items gar kein Objekt, wirft es
        ValueError, bevor etwas übernommen ist: absorb_legacy lässt die Datei dann liegen."""
        if not isinstance(raw, dict):
            return
        items = raw.get("items", {})
        if not isinstance(items, dict):
            raise ValueError("items ist kein Objekt")
        usable = {k: it for k, it in items.items() if _readable(it)}
        if len(usable) < len(items):
            log.warning("issues.json: %d von %d Einträgen unbrauchbar, übergangen",
                        len(items) - len(usable), len(items))
        self.items.update(usable)
        self._dirty.update(usable)
        if is_number(raw.get("since")):
            self.since = raw["since"]
        self._since_dirty = True

    def note(self, kind: str, key: str, callsign: str = "", **example) -> None:
        k = f"{kind}:{key}"
        now = time.time()
        it = self.items.get(k)
        if it is None:
            if len(self.items) >= MAX_KEYS:
                oldest = min((i for i, e in self.items.items() if e["kind"] not in PINNED_KINDS),
                             key=lambda i: self.items[i]["last"], default=None)
                if oldest is not None:      # sonst nur Angeheftetes: lieber ein Eintrag mehr als eine Warnung weniger
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

    def forget(self, now: float) -> int:
        """Vergessen, was vorbei ist (siehe oben). Gibt zurück, wie viele Einträge."""
        def gone(it: dict) -> bool:
            idle_days = (now - it["last"]) / DAY_S
            one_off = it.get("days", 1) <= 1 and it["kind"] not in KEEP_KINDS
            return idle_days > FORGET_DAYS or (one_off and idle_days > ONE_OFF_DAYS)

        old = [k for k, it in self.items.items() if gone(it)]
        for k in old:
            del self.items[k]
            self._gone.add(k)
            self._dirty.discard(k)
        return len(old)

    def report(self, **extra) -> dict:
        kinds: dict[str, list] = {}
        for it in sorted(self.items.values(), key=lambda i: -i["seen"]):
            kinds.setdefault(it["kind"], []).append({
                "key": it["key"], "seen": it["seen"], "days": it.get("days", 1),
                "first": _ts(it["first"]), "last": _ts(it["last"]),
                "callsigns": it["callsigns"], **({"example": it["example"]} if "example" in it else {}),
            })
        return {"since": _ts(self.since), "counts": {k: len(v) for k, v in kinds.items()}, **extra,
                "issues": kinds}
