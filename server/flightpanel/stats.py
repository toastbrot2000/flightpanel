"""Statistik und Sammelalbum für die Leerlauf-Karten, aus jedem Abruf:

  Tag         verschiedene Flugzeuge und Airlines im Abrufradius, höchstes Flugzeug,
              längste Flaute, wie viele im Sichtfeld waren
  Sammelalbum jede Airline-Marke (Name aus airlines.json) mit erstem Sichtungsdatum.
              Unbekannte Betreiber zählen extra, wenn das Flugzeug groß ist.
  Flaute      seit wann kein Flug im Sichtfeld war. Eine Lücke im Abruf (Quiet
              Hours, Ausfall) startet die Uhr neu: Nachts ist keine Flaute.
  Selten      Typen und Betreiber aus data/rare.json (A380, Antonov, Beluga,
              Regierung, Militär), solange sie in der Nähe sind
  Piste       Landerichtung und seit wann sie gilt, für die Pistenkarte und als
              Startwert nach einem Neustart

Liegt in data/stats.json (Muster wie issues.py), GET /stats zeigt alles."""

import json
import logging
import os
import time

from .config import Config
from .model import AIRLINER_CATEGORIES, DisplayModel, callsign_airline, sighting
from .opensky import State

log = logging.getLogger("stats")

GAP_RESET_S = 30 * 60        # so lange kein Abruf: Flaute-Uhr neu starten
NEW_GRACE_S = 6 * 3600       # so lange nach dem ersten Start keine NEU!-Fanfare
HISTORY_DAYS = 60
RARE_KEEP_S = 24 * 3600      # Sichtungen so lange in /stats
RARE_LOG = 30
COURSE_CONFIRM = 3           # so viele Abrufe in Folge, bis eine neue Landerichtung gilt


def load_rare(data_dir: str) -> dict:
    """data/rare.json: {"types": {"A388": "A380"}, "callsigns": {"GAF": "Luftwaffe"}}."""
    try:
        with open(os.path.join(data_dir, "rare.json"), encoding="utf-8") as f:
            raw = json.load(f)
    except FileNotFoundError:
        return {"types": {}, "callsigns": {}}
    except Exception as e:
        log.warning("rare.json nicht lesbar: %s", e)
        return {"types": {}, "callsigns": {}}
    return {"types": {k.upper(): v for k, v in raw.get("types", {}).items() if not k.startswith("_")},
            "callsigns": {k.upper(): v for k, v in raw.get("callsigns", {}).items() if not k.startswith("_")}}


def _date(t: float) -> str:
    return time.strftime("%Y-%m-%d", time.localtime(t))


def _angle(a: float, b: float) -> float:
    return abs((a - b + 180) % 360 - 180)


def _new_day(date: str) -> dict:
    return {"date": date, "aircraft": set(), "airlines": set(), "shown": set(),
            "max_alt_m": 0, "max_alt_callsign": "", "quiet_record_s": 0}


class Stats:
    def __init__(self, path: str, cfg: Config, names: dict[str, dict], rare: dict | None = None):
        self.path = path
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
        self.dirty = False
        self._load()

    # --------------------------------------------------------- Laden/Speichern --
    def _load(self) -> None:
        try:
            with open(self.path, encoding="utf-8") as f:
                raw = json.load(f)
        except FileNotFoundError:
            return
        except Exception as e:  # defekte Datei soll den Start nicht verhindern
            log.warning("stats.json nicht lesbar (%s), starte leer", e)
            return
        try:
            self.since = raw.get("since", self.since)
            self.dex = raw.get("dex", {})
            d = raw.get("day") or {}
            if d.get("date"):
                self.day = {**_new_day(d["date"]), **d,
                            **{k: set(d.get(k, [])) for k in ("aircraft", "airlines", "shown")}}
            self.history = raw.get("history", {})
            self.last_obs = raw.get("last_obs", 0.0)
            self.last_in_view = raw.get("last_in_view", 0.0)
            self.rare = raw.get("rare", {})
            c = raw.get("course") or {}
            self.course = c.get("now")
            self.prev_course = c.get("prev")
            self.course_since = c.get("since", 0.0)
            self.course_seen = c.get("seen", 0.0)
        except Exception as e:
            log.warning("stats.json unvollständig (%s), lese nur einen Teil", e)

    def to_dict(self) -> dict:
        d = {**self.day, **{k: sorted(self.day[k]) for k in ("aircraft", "airlines", "shown")}}
        return {"since": self.since, "dex": self.dex, "day": d, "history": self.history,
                "last_obs": self.last_obs, "last_in_view": self.last_in_view, "rare": self.rare,
                "course": {"now": self.course, "prev": self.prev_course, "since": self.course_since,
                           "seen": self.course_seen}}

    def save(self) -> None:
        if not self.dirty:
            return
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.to_dict(), f, ensure_ascii=False)
        os.replace(tmp, self.path)
        self.dirty = False

    # ---------------------------------------------------------------- Abruf --
    def brand(self, code: str) -> tuple[str, str, bool] | None:
        """(Schlüssel, Anzeigename, aus airlines.json) einer Airline, None wenn keine."""
        n = self.names.get(code)
        if n is not None:
            if not n["airline"]:
                return None
            name = n["name"] or code
            return name, name, True
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
            alt = s.geo_alt_m if s.geo_alt_m is not None else s.baro_alt_m
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
        self._course(model, now)
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
        self.history[d["date"]] = {"aircraft": len(d["aircraft"]), "airlines": len(d["airlines"]),
                                   "shown": len(d["shown"]), "max_alt_m": d["max_alt_m"],
                                   "quiet_record_s": d["quiet_record_s"]}
        for k in sorted(self.history)[:-HISTORY_DAYS]:
            del self.history[k]
        self.day = _new_day(date)

    def _rare(self, s: State, now: float) -> None:
        label, kind = "", ""
        if s.actype and s.actype in self.rare_cfg["types"]:
            label, kind = self.rare_cfg["types"][s.actype], "type"
        else:
            cs = s.callsign or ""
            for prefix, lab in self.rare_cfg["callsigns"].items():
                if cs.startswith(prefix):
                    label, kind = lab, "gov"
                    break
        if not label:
            return
        alt = s.geo_alt_m if s.geo_alt_m is not None else s.baro_alt_m
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

    def _course(self, model: DisplayModel, now: float) -> None:
        c = model.landing_course
        if c is None or not model.course_votes:
            return
        self.course_seen = now
        if self.course is None:
            self.course, self.course_since = c, now
            return
        if _angle(c, self.course) < 90:
            self._pending = None
            return
        n = self._pending[1] + 1 if self._pending and _angle(self._pending[0], c) < 90 else 1
        self._pending = (c, n)
        if n >= COURSE_CONFIRM:
            self.prev_course, self.course, self.course_since = self.course, c, now
            self._pending = None
            log.info("Landerichtung gewechselt: %03.0f°", c)

    # ------------------------------------------------------------ Abfragen --
    def today(self) -> dict:
        d = self.day
        return {"date": d["date"], "aircraft": len(d["aircraft"]), "airlines": len(d["airlines"]),
                "shown": len(d["shown"]), "max_alt_m": d["max_alt_m"],
                "max_alt_callsign": d["max_alt_callsign"], "quiet_record_s": d["quiet_record_s"]}

    def dex_total(self) -> int:
        return len({n["name"] for n in self.names.values() if n["airline"] and n["name"]})

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
                "course": {"now": self.course, "prev": self.prev_course,
                           "since": time.strftime("%Y-%m-%d %H:%M", time.localtime(self.course_since))
                           if self.course_since else None},
                "history": self.history}
