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

Pro Eintrag: wie oft gesehen (je Abruf), an wie vielen Tagen, erstes und letztes
Mal, bis zu zehn Callsigns, ein Beispiel. Liegt in data/issues.json und übersteht
Neustarts; POST /issues/clear fängt von vorn an.

Nachts (main.App.dream) vergisst die Liste von selbst: Einmaliges (nur an einem Tag
gesehen) nach ONE_OFF_DAYS, alles andere, wenn es FORGET_DAYS nicht mehr aufgetreten
ist; Fehler im Code (exception) erst dann. Ist sie trotzdem voll, macht der am
längsten nicht mehr gesehene Eintrag Platz, Neues geht nie verloren."""

import logging
import re
import time

from .storage import read_json, write_json

log = logging.getLogger("issues")

MAX_KEYS = 1000
MAX_CALLSIGNS = 10
ONE_OFF_DAYS = 7        # nur an einem Tag gesehen: nach einer Woche vergessen
FORGET_DAYS = 30        # alles andere: nach 30 Tagen ohne neues Auftreten
KEEP_KINDS = {"exception"}   # auch einmalig erst nach FORGET_DAYS
DAY_S = 24 * 3600


def _ts(t: float) -> str:
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(t))


def _day(t: float) -> str:
    return time.strftime("%Y-%m-%d", time.localtime(t))


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9 ]", "", s.lower()).strip()


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
    def __init__(self, path: str):
        self.path = path
        self.items: dict[str, dict] = {}
        self.since = time.time()
        self.dirty = False
        raw = read_json(path)
        if isinstance(raw, dict):
            self.items = raw.get("items", {})
            self.since = raw.get("since", self.since)

    def note(self, kind: str, key: str, callsign: str = "", **example) -> None:
        k = f"{kind}:{key}"
        now = time.time()
        it = self.items.get(k)
        if it is None:
            if len(self.items) >= MAX_KEYS:
                del self.items[min(self.items, key=lambda i: self.items[i]["last"])]
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
        self.dirty = True

    def source_error(self, source: str, error: str, **example) -> None:
        """Fehler einer Quelle: HTTP 429 als rate_limit, alles andere als source_error."""
        self.note("rate_limit" if "429" in error else "source_error", source, error=error, **example)

    def save(self) -> None:
        if self.dirty:
            write_json(self.path, {"since": self.since, "items": self.items})
            self.dirty = False

    def clear(self) -> None:
        self.items = {}
        self.since = time.time()
        self.dirty = True

    def forget(self, now: float) -> int:
        """Vergessen, was vorbei ist (siehe oben). Gibt zurück, wie viele Einträge."""
        def gone(it: dict) -> bool:
            idle_days = (now - it["last"]) / DAY_S
            one_off = it.get("days", 1) <= 1 and it["kind"] not in KEEP_KINDS
            return idle_days > FORGET_DAYS or (one_off and idle_days > ONE_OFF_DAYS)

        old = [k for k, it in self.items.items() if gone(it)]
        for k in old:
            del self.items[k]
        self.dirty = self.dirty or bool(old)
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
