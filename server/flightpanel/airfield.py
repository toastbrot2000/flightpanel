"""Was am Heimatflughafen über Abrufe hinweg gilt, für alle Panels gleich. main.App hält eines und rechnet jeden
Abruf einmal ein (observe): Landefolge, Abflüge und Landerichtung hängen nur vom Flughafen ab, nicht vom Fenster.

  recent          wer in den letzten Minuten hier landete oder startete (airport.Recent), für build_model
  landing_course  Landerichtung des letzten Abrufs, Startwert für den nächsten (build_model prev_course)
  course          bestätigte Landerichtung für die Pistenkarten (cards/runway.py): Ein Wechsel gilt erst nach
                  COURSE_CONFIRM Abrufen in Folge. Dazu die vorige, seit wann sie gilt (course_since) und wann
                  Verkehr sie zuletzt bestätigt hat (course_seen). Liegt in der Datenbank (state "course") und
                  übersteht Neustarts; nach einem Neustart ist sie auch der erste Startwert.
  last_move       letzter Start oder letzte Landung am Platz (idle.Ctx.quiet: erst danach Spielereien)"""

import logging
import time

from .airport import Recent
from .db import Db
from .geo import angle_diff
from .model import DisplayModel
from .storage import is_number

log = logging.getLogger("airfield")

COURSE_CONFIRM = 3      # so viele Abrufe in Folge, bis eine neue Landerichtung gilt
LANDED_S = 90           # Landezeit darunter zählt als Bewegung am Platz


def _usable(c) -> bool:
    """Eine Landerichtung, wie save() sie schreibt und alte stats.json-Dateien sie hatten; von Hand verändert (Text
    statt Zahl) nicht: Sie hielte sonst die Pistenkarten an."""
    return (isinstance(c, dict) and all(c.get(k) is None or is_number(c[k]) for k in ("now", "prev"))
            and all(is_number(c.get(k, 0)) for k in ("since", "seen")))


class Airfield:
    def __init__(self, db: Db):
        self.db = db
        self.recent = Recent()
        self.last_move = 0.0
        self.course: float | None = None
        self.prev_course: float | None = None
        self.course_since = 0.0
        self.course_seen = 0.0
        self._pending: tuple[float, int] | None = None
        self.dirty = False
        c = db.state_get("course")
        if c is not None and not _usable(c):
            log.warning("Gespeicherte Landerichtung unbrauchbar (%s), beginne ohne", c)
        elif c is not None:
            self.course, self.prev_course = c.get("now"), c.get("prev")
            self.course_since, self.course_seen = c.get("since", 0.0), c.get("seen", 0.0)
        self.landing_course = self.course

    def load_legacy(self, raw: dict) -> None:
        """Die Landerichtung einer alten stats.json übernehmen (main.absorb_legacy, einmalig, nach
        Stats.load_legacy). Von Hand verändert (Text statt Zahl) bleibt sie weg, mit einer Warnung."""
        c = raw.get("course")
        if c is None:
            return
        if not _usable(c):
            log.warning("stats.json: Landerichtung unbrauchbar, übergangen")
            return
        self.course, self.prev_course = c.get("now", self.course), c.get("prev", self.prev_course)
        self.course_since = c.get("since", self.course_since)
        self.course_seen = c.get("seen", self.course_seen)
        self.landing_course = self.course
        self.dirty = True

    def observe(self, model: DisplayModel, now: float) -> None:
        """Einen Abruf einrechnen, einmal je Abruf; aus welchem Panel das Modell kommt, ist gleich."""
        self.recent.update(model.arrivals, model.departures, model.generated_at)
        self.landing_course = model.landing_course
        if model.departures or any(a["eta_s"] <= LANDED_S for a in model.arrivals):
            self.last_move = now
        self._course(model, now)

    def _course(self, model: DisplayModel, now: float) -> None:
        c = model.landing_course
        if c is None or not model.course_votes:
            return
        self.course_seen = now
        self.dirty = True
        if self.course is None:
            self.course, self.course_since = c, now
            return
        if angle_diff(c, self.course) < 90:
            self._pending = None
            return
        n = self._pending[1] + 1 if self._pending and angle_diff(self._pending[0], c) < 90 else 1
        self._pending = (c, n)
        if n >= COURSE_CONFIRM:
            self.prev_course, self.course, self.course_since = self.course, c, now
            self._pending = None
            log.info("Landerichtung gewechselt: %03.0f°", c)

    def save(self) -> None:
        """Die bestätigte Landerichtung schreiben (main.App.save(), in einer Transaktion mit den anderen Speichern).
        Als gespeichert gilt sie erst nach dem COMMIT (Db.after_commit); scheitert die Transaktion, schreibt der
        nächste Lauf sie noch einmal. Zwischen save() und COMMIT kommt kein observe() dazwischen (App.save() ist
        synchron)."""
        if not self.dirty:
            return
        self.db.state_set("course", {"now": self.course, "prev": self.prev_course, "since": self.course_since,
                                     "seen": self.course_seen})
        self.db.after_commit(self._saved)

    def _saved(self) -> None:
        self.dirty = False

    def report(self) -> dict:
        """Für GET /stats ("course"): jetzige und vorige Landerichtung, seit wann."""
        return {"now": self.course, "prev": self.prev_course,
                "since": time.strftime("%Y-%m-%d %H:%M", time.localtime(self.course_since))
                if self.course_since else None}
