"""Zwischenspeicher für Antworten der Routen- und Flugzeugdienste: Schlüssel ->
Wert mit Ablaufzeit. Lebt im Speicher; main.py schreibt ihn alle 10 min nach
data/cache.json. Abgelaufenes fällt beim Laden und bei jedem Speichern weg, sonst
wüchse er im Dauerbetrieb mit jedem Flugzeug, das je vorbeikam.

Schlüssel: "route:<callsign>", "ac:<icao24>" (adsbdb), "im:<callsign>" (adsb.im),
"trk:<icao24>:<callsign>" (Flugspur)."""

import logging
import time

from .storage import read_json, write_json

log = logging.getLogger("cache")


class Cache:
    def __init__(self, path: str):
        self.path = path
        self.data: dict[str, tuple[float, object]] = {}
        self.dirty = False
        self.load()

    def load(self) -> None:
        raw = read_json(self.path)
        if raw is None:
            return
        try:
            now = time.time()
            self.data = {k: (exp, v) for k, (exp, v) in raw.items() if exp > now}
            log.info("Cache geladen: %d Einträge", len(self.data))
        except (AttributeError, TypeError, ValueError) as e:
            log.warning("cache.json unbrauchbar (%s), starte leer", e)

    def prune(self) -> int:
        """Abgelaufenes entfernen; gibt zurück, wie viele Einträge."""
        now = time.time()
        old = [k for k, (exp, _) in self.data.items() if exp < now]
        for k in old:
            del self.data[k]
        self.dirty = self.dirty or bool(old)
        return len(old)

    def save(self) -> None:
        self.prune()
        if self.dirty:
            write_json(self.path, self.data)
            self.dirty = False

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
        self.dirty = True

    def __len__(self) -> int:
        return len(self.data)
