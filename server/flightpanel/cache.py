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
            written = set(self._dirty)
            self.db.executemany(
                "INSERT INTO cache (key, expires, value) VALUES (?, ?, ?) "
                "ON CONFLICT(key) DO UPDATE SET expires = excluded.expires, value = excluded.value",
                [(k, self.data[k][0], json.dumps(self.data[k][1], ensure_ascii=False)) for k in written])
            # Erst nach dem COMMIT vergessen: Scheitert die umgebende Transaktion (App.save() schreibt alle Speicher
            # in einer), bleibt alles fürs nächste Mal vorgemerkt.
            self.db.after_commit(lambda: self._dirty.difference_update(written))

    def get(self, key: str):
        """Wert oder None, wenn unbekannt oder abgelaufen."""
        item = self.data.get(key)
        if item is None:
            return None
        exp, v = item
        if exp < time.time():
            del self.data[key]
            self._dirty.discard(key)
            return None
        return v

    def set(self, key: str, value, ttl: int) -> None:
        self.data[key] = (time.time() + ttl, value)
        self._dirty.add(key)

    def __len__(self) -> int:
        return len(self.data)
