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

Pro Eintrag: wie oft gesehen (je Abruf), erstes und letztes Mal, bis zu zehn
Callsigns, ein Beispiel. Liegt in data/issues.json und übersteht Neustarts;
POST /issues/clear fängt von vorn an."""

import logging
import re
import time

from .storage import read_json, write_json

log = logging.getLogger("issues")

MAX_KEYS = 1000
MAX_CALLSIGNS = 10


def _ts(t: float) -> str:
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(t))


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9 ]", "", s.lower()).strip()


def names_match(ours: str, theirs: str) -> bool:
    """Grob: unser Kurzname steckt im adsbdb-Namen oder umgekehrt.
    "Norwegian" ~ "Norwegian Air Sweden AOC" ja, "Sundair" ~ "City Airline" nein."""
    a, b = _norm(ours), _norm(theirs)
    if not a or not b:
        return True
    return a in b or b in a or a.split()[0] in b.split()


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
                return
            it = self.items[k] = {"kind": kind, "key": key, "seen": 0, "first": now, "callsigns": []}
            log.info("Auffällig: %s %s %s", k, callsign, example or "")
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

    def report(self, **extra) -> dict:
        kinds: dict[str, list] = {}
        for it in sorted(self.items.values(), key=lambda i: -i["seen"]):
            kinds.setdefault(it["kind"], []).append({
                "key": it["key"], "seen": it["seen"], "first": _ts(it["first"]), "last": _ts(it["last"]),
                "callsigns": it["callsigns"], **({"example": it["example"]} if "example" in it else {}),
            })
        return {"since": _ts(self.since), "counts": {k: len(v) for k, v in kinds.items()}, **extra,
                "issues": kinds}
