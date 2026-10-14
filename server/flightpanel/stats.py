"""Statistik und Sammelalbum für die Leerlauf-Karten, aus jedem Abruf:

  Tag         verschiedene Flugzeuge und Airlines im Abrufradius, höchstes Flugzeug,
              längste Flaute, wie viele im Sichtfeld waren
  Sammelalbum jede Airline-Marke (Name aus airlines.json) mit erstem Sichtungsdatum.
              Unbekannte Betreiber zählen extra, wenn das Flugzeug groß ist.
  Flaute      seit wann kein Flug im Sichtfeld war. Eine Lücke im Abruf (Quiet
              Hours, Ausfall) startet die Uhr neu: Nachts ist keine Flaute.
  Selten      Typen aus data/rare.json (A380, Antonov, Beluga), Regierung und
              Militär (data.load_rare), solange sie in der Nähe sind

Liegt je Panel in der Datenbank (db.py: Tabellen album, days, rare, Spalten in panels),
GET /stats zeigt alles; die Landerichtung führt airfield.py für alle Panels.
main.App.save() schreibt, was sich geändert hat."""

import json
import logging
import re
import time

from .airlines import AIRLINER_CATEGORIES, callsign_airline
from .config import Config
from .data import rare_match
from .db import Db
from .model import DisplayModel, State
from .storage import is_number
from .view import sighting

log = logging.getLogger("stats")

GAP_RESET_S = 30 * 60        # so lange kein Abruf: Flaute-Uhr neu starten
NEW_GRACE_S = 6 * 3600       # so lange nach dem ersten Start keine NEU!-Fanfare
HISTORY_DAYS = 60
RARE_KEEP_S = 24 * 3600      # Sichtungen so lange in /stats
RARE_LOG = 30
DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")


def _date(t: float) -> str:
    return time.strftime("%Y-%m-%d", time.localtime(t))


def _new_day(date: str) -> dict:
    return {"date": date, "aircraft": set(), "airlines": set(), "shown": set(),
            "max_alt_m": 0, "max_alt_callsign": "", "quiet_record_s": 0}


def _counts(d: dict) -> dict:
    """Ein abgeschlossener Tag, wie er in history steht."""
    return {"aircraft": len(d["aircraft"]), "airlines": len(d["airlines"]), "shown": len(d["shown"]),
            "max_alt_m": d["max_alt_m"], "quiet_record_s": d["quiet_record_s"]}


def _album_entry(e) -> dict:
    """Ein Albumeintrag in der Form, die der Rest erwartet (Datenbank und alte Datei); was fehlt (ältere Dateien),
    bekommt den Wert eines neuen Eintrags."""
    return {"first": e["first"], "last": e.get("last", e["first"]), "code": e.get("code", ""),
            "name": e.get("name", ""), "known": bool(e.get("known")), "callsign": e.get("callsign", ""),
            "fanfare": bool(e.get("fanfare"))}


# Was load_legacy aus einer alten stats.json nimmt: Einträge, die so beschaffen sind, wie die Datenbank sie speichert
# und der Rest sie liest. Von Hand verändert (null, Text statt Zahl, ein Feld fehlt) hielten sie sonst jedes Speichern
# oder jede Anzeige an. Die ersten drei prüfen (Schlüssel, Eintrag), damit _legacy_part sie gleich aufruft.
def _usable_album(_key, e) -> bool:
    return (isinstance(e, dict) and is_number(e.get("first")) and is_number(e.get("last", e["first"]))
            and all(isinstance(e.get(k, ""), str) for k in ("code", "name", "callsign")))


def _usable_counts(date, h) -> bool:
    """Ein abgeschlossener Tag: ein Datum als Schlüssel, nur Zahlen darin."""
    return bool(DATE_RE.fullmatch(date)) and isinstance(h, dict) and all(is_number(v) for v in h.values())


def _usable_rare(_key, r) -> bool:
    return isinstance(r, dict) and is_number(r.get("first")) and is_number(r.get("last"))


def _legacy_part(raw: dict, name: str, usable) -> tuple[dict, int]:
    """Die brauchbaren Einträge des Abschnitts name einer alten Datei und wie viele wegfielen. Fehlt der Abschnitt,
    ist er leer; ist er kein Objekt, zählt er als ein weggefallener Eintrag."""
    part = raw.get(name)
    if part is None:
        return {}, 0
    if not isinstance(part, dict):
        return {}, 1
    good = {k: v for k, v in part.items() if usable(k, v)}
    return good, len(part) - len(good)


def _legacy_day(d: dict) -> dict | None:
    """Der laufende Tag einer alten Datei in der Form von _new_day, None wenn er so nicht brauchbar ist: Datum als
    Text, Listen von Text, Zahlen."""
    ids = {k: d.get(k, []) for k in ("aircraft", "airlines", "shown")}
    if not (isinstance(d.get("date"), str) and DATE_RE.fullmatch(d["date"])
            and all(isinstance(v, list) and all(isinstance(i, str) for i in v) for v in ids.values())
            and all(is_number(d.get(k, 0)) for k in ("max_alt_m", "quiet_record_s"))
            and isinstance(d.get("max_alt_callsign", ""), str)):
        return None
    return {**_new_day(d["date"]), "max_alt_m": d.get("max_alt_m", 0), "quiet_record_s": d.get("quiet_record_s", 0),
            "max_alt_callsign": d.get("max_alt_callsign", ""), **{k: set(v) for k, v in ids.items()}}


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
        self.dex = {r["brand"]: _album_entry(dict(r))
                    for r in self.db.query("SELECT * FROM album WHERE panel_id = ?", (pid,))}
        self._merge_listed()
        days = self.db.query("SELECT * FROM days WHERE panel_id = ? ORDER BY date", (pid,))
        # Der laufende Tag ist die jüngste Zeile mit ICAO-Mengen (ids), abgeschlossene Tage haben ids NULL und sind
        # History. Nicht einfach die jüngste Zeile: Ging die Uhr zurück (ein Rechner ohne Uhr, bis die Zeit
        # abgeglichen ist), steht ein abgeschlossener Tag mit späterem Datum dahinter, und der laufende gälte
        # sonst als History.
        cur = next((r for r in reversed(days) if r["ids"]), None)
        if cur is not None:
            ids = json.loads(cur["ids"])
            self.day = {**_new_day(cur["date"]), "max_alt_m": cur["max_alt_m"],
                        "max_alt_callsign": cur["max_alt_callsign"], "quiet_record_s": cur["quiet_record_s"],
                        **{k: set(ids.get(k, [])) for k in ("aircraft", "airlines", "shown")}}
        self.history = {r["date"]: {"aircraft": r["aircraft"], "airlines": r["airlines"], "shown": r["shown"],
                                    "max_alt_m": r["max_alt_m"], "quiet_record_s": r["quiet_record_s"]}
                        for r in days if r is not cur}
        self.rare = {r["icao24"]: json.loads(r["data"])
                     for r in self.db.query("SELECT icao24, data FROM rare WHERE panel_id = ?", (pid,))}

    def load_legacy(self, raw: dict) -> None:
        """Alte stats.json übernehmen (main.absorb_legacy, einmalig). Einzelne Einträge, die so nicht zu gebrauchen
        sind (von Hand verändert: kein Objekt, ohne Zeitpunkt oder Datum, Zahl als Text), fallen weg, ein Abschnitt, der
        kein Objekt ist, ganz; eine Warnung nennt, wie viele. Ist die Datei im Ganzen unbrauchbar (kein Objekt, dex kein
        Objekt), wirft sie ValueError, bevor etwas übernommen ist: absorb_legacy lässt sie dann liegen, statt eine
        halbe zu übernehmen und umzubenennen. Die Landerichtung darin übernimmt airfield.Airfield.load_legacy."""
        if not isinstance(raw, dict) or not isinstance(raw.get("dex", {}), dict):
            raise ValueError("stats.json ist kein Objekt oder dex darin keins")
        dex, skipped = _legacy_part(raw, "dex", _usable_album)
        history, bad = _legacy_part(raw, "history", _usable_counts)
        skipped += bad
        rare, bad = _legacy_part(raw, "rare", _usable_rare)
        skipped += bad
        d = raw.get("day")
        day = _legacy_day(d) if isinstance(d, dict) else None
        numbers = {k: raw[k] for k in ("since", "last_obs", "last_in_view") if is_number(raw.get(k))}
        if d and day is None:                           # {} oder fehlend: kein laufender Tag in der Datei
            skipped += 1
        skipped += sum(1 for k in ("since", "last_obs", "last_in_view") if raw.get(k) is not None) - len(numbers)

        for key, e in dex.items():
            self.dex.setdefault(key, _album_entry(e))
        self._merge_listed()
        if day is not None:
            if day["date"] >= self.day["date"]:
                self.day = day
            else:
                self.history[day["date"]] = _counts(day)
        self.history.update(history)
        self._finished.update(self.history)
        self.rare.update(rare)
        for attr, value in numbers.items():
            setattr(self, attr, value)
        if skipped:
            log.warning("stats.json: %d Einträge unbrauchbar, übergangen", skipped)
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
                self.dex.setdefault(b[0], {**e, "name": b[1], "known": b[2]})
            self.dirty = True
        # Marken, die nicht mehr im Album stehen (album false), zählen ab jetzt als Exot
        small = {n["name"] for n in self.names.values() if n["airline"] and not n.get("album", True)}
        for e in self.dex.values():
            if e.get("known") and e["name"] in small:
                e["known"] = False
                self.dirty = True

    def save(self) -> None:
        """Geändertes in die Datenbank schreiben (main.App.save(), in einer Transaktion mit den anderen
        Speichern). Was vorgemerkt war, gilt erst nach dem COMMIT als gespeichert (Db.after_commit);
        scheitert die Transaktion, schreibt der nächste Lauf es noch einmal."""
        if not self.dirty:
            return
        pid = self.panel_id
        finished, gone_days, gone_brands = dict(self._finished), set(self._gone_days), set(self._gone_brands)
        self.db.execute("UPDATE panels SET stats_since = ?, last_obs = ?, last_in_view = ? WHERE id = ?",
                        (self.since, self.last_obs, self.last_in_view, pid))
        for brand in gone_brands:
            self.db.execute("DELETE FROM album WHERE panel_id = ? AND brand = ?", (pid, brand))
        self.db.executemany(
            "INSERT INTO album (panel_id, brand, code, name, known, callsign, first, last, fanfare) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(panel_id, brand) DO UPDATE SET code = excluded.code, "
            "name = excluded.name, known = excluded.known, callsign = excluded.callsign, first = excluded.first, "
            "last = excluded.last, fanfare = excluded.fanfare",
            [(pid, b, e.get("code", ""), e.get("name", ""), int(bool(e.get("known"))), e.get("callsign", ""),
              e["first"], e.get("last", e["first"]), int(bool(e.get("fanfare")))) for b, e in self.dex.items()])
        for date in gone_days:
            self.db.execute("DELETE FROM days WHERE panel_id = ? AND date = ?", (pid, date))
        day_sql = ("INSERT INTO days (panel_id, date, aircraft, airlines, shown, max_alt_m, max_alt_callsign, "
                   "quiet_record_s, ids) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(panel_id, date) DO UPDATE SET "
                   "aircraft = excluded.aircraft, airlines = excluded.airlines, shown = excluded.shown, "
                   "max_alt_m = excluded.max_alt_m, max_alt_callsign = excluded.max_alt_callsign, "
                   "quiet_record_s = excluded.quiet_record_s, ids = excluded.ids")
        # .get mit 0: Einträge aus alten Fassungen (stats.json) haben nicht immer jedes Feld.
        self.db.executemany(day_sql, [(pid, date, h.get("aircraft", 0), h.get("airlines", 0), h.get("shown", 0),
                                       h.get("max_alt_m", 0), "", h.get("quiet_record_s", 0), None)
                                      for date, h in finished.items()])
        d = self.day
        ids = json.dumps({k: sorted(d[k]) for k in ("aircraft", "airlines", "shown")})
        self.db.execute(day_sql, (pid, d["date"], len(d["aircraft"]), len(d["airlines"]), len(d["shown"]),
                                  d["max_alt_m"], d["max_alt_callsign"], d["quiet_record_s"], ids))
        self.db.execute("DELETE FROM rare WHERE panel_id = ?", (pid,))
        self.db.executemany("INSERT INTO rare (panel_id, icao24, data) VALUES (?, ?, ?)",
                            [(pid, k, json.dumps(v, ensure_ascii=False)) for k, v in self.rare.items()])
        # Zwischen diesem save() und dem COMMIT ändert sich nichts: App.save() ist synchron (kein await),
        # observe() kommt nicht dazwischen. Darum darf dirty danach aus.
        self.db.after_commit(lambda: self._saved(finished, gone_days, gone_brands))

    def _saved(self, finished: dict[str, dict], gone_days: set[str], gone_brands: set[str]) -> None:
        """Nach dem COMMIT: das Geschriebene vergessen. Nur Speicher, darf nicht scheitern."""
        for date in finished:
            self._finished.pop(date, None)
        self._gone_days -= gone_days
        self._gone_brands -= gone_brands
        self.dirty = False

    # ---------------------------------------------------------------- Abruf --
    def brand(self, code: str) -> tuple[str, str, bool] | None:
        """(Schlüssel, Anzeigename, aus airlines.json) einer Airline, None wenn keine."""
        n = self.names.get(code)
        if n is not None:
            if not n["airline"]:
                return None
            name = n["name"] or code
            return name, name, n.get("album", True)        # album false: Kleinbetrieb, zählt als Exot
        return code, code, False

    def observe(self, states: list[State], model: DisplayModel, now: float) -> list[str]:
        """Einen Abruf einrechnen. Gibt die ICAO-Codes neu gesehener Airlines zurück
        (für den Logo-Abruf)."""
        date = _date(now)
        if date != self.day["date"]:
            self._roll(date)
        if not self.last_obs or now - self.last_obs > GAP_RESET_S:
            self.last_in_view = now
        self.last_obs = now
        if any(f.visible for f in model.flights):
            self.last_in_view = now
        self.day["quiet_record_s"] = max(self.day["quiet_record_s"], int(now - self.last_in_view))

        new: list[str] = []
        for s in states:
            if s.on_ground:
                continue
            self.day["aircraft"].add(s.icao24)
            alt = s.alt_m
            if alt is not None and alt > self.day["max_alt_m"]:
                self.day["max_alt_m"] = int(alt)
                self.day["max_alt_callsign"] = s.callsign or s.icao24.upper()
            code = callsign_airline(s.callsign)
            b = self.brand(code) if code and code not in self.rare_cfg["callsigns"] else None
            if b and (b[2] or s.category in AIRLINER_CATEGORIES):
                key, name, known = b
                self.day["airlines"].add(key)
                e = self.dex.get(key)
                if e is None:
                    fanfare = now - self.since > NEW_GRACE_S
                    e = self.dex[key] = {"first": now, "code": code, "name": name, "known": known,
                                         "callsign": s.callsign, "fanfare": fanfare}
                    new.append(code)
                    if fanfare:
                        log.info("Neue Airline: %s (%s)", name, s.callsign)
                e["last"] = now
            self._rare(s, now)
        for f in model.flights:
            if f.visible:
                self.day["shown"].add(f.icao24)
        self.rare = {k: v for k, v in self.rare.items() if now - v["last"] < RARE_KEEP_S}
        if len(self.rare) > RARE_LOG:
            keep = sorted(self.rare.items(), key=lambda kv: -kv[1]["last"])[:RARE_LOG]
            self.rare = dict(keep)
        self.dirty = True
        return new

    def set_name(self, key: str, name: str) -> None:
        """Namen für einen unbekannten Betreiber nachtragen (aus adsbdb)."""
        if key in self.dex and name and not self.dex[key]["known"]:
            self.dex[key]["name"] = name[:20]
            self.dirty = True

    def _roll(self, date: str) -> None:
        d = self.day
        self.history[d["date"]] = self._finished[d["date"]] = _counts(d)
        for k in sorted(self.history)[:-HISTORY_DAYS]:
            del self.history[k]
            self._finished.pop(k, None)
            self._gone_days.add(k)
        self.day = _new_day(date)

    def _rare(self, s: State, now: float) -> None:
        label, kind = rare_match(self.rare_cfg, s.callsign, s.actype)
        if not label:
            return
        alt = s.alt_m
        v = sighting(self.cfg, s.lat, s.lon, alt)
        code = callsign_airline(s.callsign)
        airline = self.names[code]["name"] if code in self.names and kind == "type" else ""
        e = self.rare.get(s.icao24)
        if e is None:
            e = self.rare[s.icao24] = {"first": now, "label": label, "kind": kind}
            log.info("Selten: %s %s %s", label, s.callsign, s.actype)
        e.update(last=now, callsign=s.callsign or s.icao24.upper(), type=s.actype, airline=airline,
                 dist_km=round(v.dist_km), az=round(v.az), elev=round(v.elev, 1),
                 alt_m=int(alt) if alt is not None else None)

    # ------------------------------------------------------------ Abfragen --
    def today(self) -> dict:
        d = self.day
        return {"date": d["date"], "aircraft": len(d["aircraft"]), "airlines": len(d["airlines"]),
                "shown": len(d["shown"]), "max_alt_m": d["max_alt_m"],
                "max_alt_callsign": d["max_alt_callsign"], "quiet_record_s": d["quiet_record_s"]}

    def dex_total(self) -> int:
        return len({n["name"] for n in self.names.values() if n["airline"] and n.get("album", True) and n["name"]})

    def dex_count(self) -> tuple[int, int, int]:
        """(gesehene Marken aus airlines.json, Marken in airlines.json, unbekannte Extras)"""
        known = sum(1 for e in self.dex.values() if e["known"])
        return known, self.dex_total(), len(self.dex) - known

    def newest(self, now: float, within: float) -> dict | None:
        """Die zuletzt neu gesehene Airline, wenn das höchstens within Sekunden her ist
        und es nicht die Erstbefüllung nach dem ersten Start war."""
        fresh = [e for e in self.dex.values() if e.get("fanfare") and now - e["first"] < within]
        return max(fresh, key=lambda e: e["first"]) if fresh else None

    def quiet_s(self, now: float) -> float | None:
        """Sekunden ohne Flug im Sichtfeld. None, wenn gerade keine Abrufe laufen."""
        if not self.last_obs or now - self.last_obs > 10 * 60:
            return None
        return now - self.last_in_view

    def rare_now(self, now: float, within: float = 300) -> list[dict]:
        """Seltene Flugzeuge, die in den letzten within Sekunden noch da waren, neueste zuerst."""
        return sorted((dict(e, icao24=k) for k, e in self.rare.items() if now - e["last"] < within),
                      key=lambda e: -e["first"])

    def report(self) -> dict:
        now = time.time()
        known, total, extra = self.dex_count()
        dex = sorted(self.dex.values(), key=lambda e: -e["first"])
        return {"since": time.strftime("%Y-%m-%d %H:%M", time.localtime(self.since)),
                "today": self.today(), "quiet_s": self.quiet_s(now),
                "dex": {"seen": known, "total": total, "extra": extra,
                        "latest": [{"name": e["name"], "code": e["code"], "callsign": e.get("callsign", ""),
                                    "first": time.strftime("%Y-%m-%d %H:%M", time.localtime(e["first"]))}
                                   for e in dex[:20]],
                        "missing": sorted({n["name"] for n in self.names.values() if n["airline"] and n["name"]}
                                          - {e["name"] for e in self.dex.values() if e["known"]})},
                "rare": sorted(self.rare.values(), key=lambda e: -e["last"]),
                "history": self.history}
