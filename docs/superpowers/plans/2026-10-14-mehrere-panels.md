# Mehrere Panels mit einem Abruf — Implementation Plan (Schritt 2)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** One server process serves several panels from one fetch: shared sources, cache, logos, METAR, ISS orbit data and airport state; per panel its own settings, model, renderer, idle cards, stats/album and ISS passes. Panels added or changed with the `panels` CLI apply while the server runs. The LAN panel behaves exactly as today.

**Architecture:** New `airfield.py` holds what every panel shares about the airport (recent arrivals/departures, landing course with its 3-poll confirmation, last movement); `astro.py` splits into a shared `Orbit` (TLE) and a per-window `Iss` (passes). New `panel.py` holds a `Panel` (cfg, model, renderer, idle, stats, iss, ESP32 bookkeeping) and the area helpers (`fetch_circle`, `union_bbox`, `active_seconds`). `main.App` keeps the shared parts, builds `self.panels` from the database (`_sync_panels`, also every poll and before every save), fetches once for all active panels and builds each panel's model with a shared per-poll `noted` set so `/issues` counts once per poll. `web.py` serves the LAN panel (`app.lan`). Config gets two server limits for added panels (`PANEL_MAX_KM`, `PANEL_MAX_RADIUS_KM`), enforced by the CLI and, tolerantly, by the server. Error texts lose request URLs (they carry the fetch centre, i.e. roughly the window).

**Tech Stack:** Python 3.12, aiohttp, stdlib sqlite3, pytest + ruff inside the Docker image `flightpanel-ci-dev`. No new dependencies.

**Spec:** `docs/superpowers/specs/2026-10-13-freunde-multi-tenant-design.md` — "Server §2 Shared layer and per-panel layer", "§6 CLI" (live apply), "Order → step 2" and "Follow-ups … Step 2" (strip request URLs from error text). Step 1 (`docs/superpowers/plans/2026-10-13-sqlite-panels-cli.md`) is done and on `main`. Not in this plan: the public listener, identities, settings page, `/panel/*` routes, firmware (steps 3–7).

## Global Constraints

- No new Python dependencies. Only `server/flightpanel/db.py` imports `sqlite3`. Every SQL statement parameterized (ruff `S608`).
- Code comments, docstrings, commit messages and docs are **German**, identifiers English, line length 120. Tests must pass with `filterwarnings = error::DeprecationWarning:flightpanel.*`.
- **The LAN panel behaves exactly as today**: rendering code (render.py, draw.py, cards except `runway.py`'s data source) is untouched; `tools/screenshots.py` output (`docs/images`) must not change; the golden-frame matrix in Task 7 must be byte-identical to `main`.
- With one panel, the fetch is exactly today's: adsb.lol around the home with `fetch_radius_km`, OpenSky with `cfg.bbox`, OpenSky budget from the panel's quiet hours.
- Nothing location-specific or personal in the repo: no coordinates of the owner, no `.env`, no database files. Test coordinates are generic public places (Berlin-Mitte default, BER, Munich airport, open countryside).
- Work happens in the git worktree `.claude/worktrees/freunde-sqlite` (branch `worktree-freunde-sqlite`). Never `cd` or `git -C` to the main checkout; never use bare `git stash`. Commit after every task, German message, trailer `Co-Authored-By: Claude <Model> <noreply@anthropic.com>` naming the model that wrote the commit (subagents: `Claude Sonnet 5.5`). Do not push.
- Never start, stop or restart Docker Desktop; never touch the running `flightpanel` container or port 8080. Only `docker run --rm …` (and, in Task 7, a uniquely named throwaway container on port 18080 that is removed afterwards).
- **Running tests** (Git Bash, from the worktree root):

  ```bash
  MSYS_NO_PATHCONV=1 docker run --rm -v "$(pwd -W):/repo" -w /repo flightpanel-ci-dev sh -c "ruff check . && python -m pytest -p no:cacheprovider server/tests/test_airfield.py"
  ```

  Replace the pytest target per step; the whole suite is `python -m pytest -p no:cacheprovider` (no extra `-q`: pyproject already adds one, and `-qq` hides the summary line). Baseline before this plan: 304 passed, ruff clean (branch head 20826f7).

## Review Focus

1. **Two panels whose fetch circles overlap** (friends 10 km apart): an aircraft in both circles must count in both panels' statistics, but `/issues` must count it once per poll, and the landing sequence/landing course must be identical on both panels — pinned in Task 6 (`test_one_poll_serves_every_panel_with_one_fetch`) and Task 3.
2. **`panels set/add/remove` while the server runs**: the change applies within one loop pass (≤ 1 min, also in quiet hours), the album of a changed panel survives, a removed panel never makes the shared save fail, and remove + re-add of the same id starts a fresh album — pinned in Task 6 (`test_panels_cli_changes_apply_without_a_restart`).
3. **One panel in its quiet hours, another awake**: the resting panel is dark and does not widen the fetch; METAR and the OpenSky budget follow "any panel awake"; with every panel resting nothing is fetched — pinned in Task 6 (`test_a_resting_panel_is_dark_and_outside_the_fetch`) and `test_panel.py`.
4. **A friend's panel pointing at the wrong place** (typo puts the window in Munich, or `PANEL_MAX_KM` lowered later): the CLI refuses it; if it is in the database anyway, that panel does not fetch, is dark, and `/issues` and `panels list` say why — the fetch for everyone else stays as it was — pinned in Task 5 and Task 6 (`test_a_panel_too_far_from_the_airport_stays_dark_and_widens_nothing`).
5. **An OpenSky or adsb.lol error with the full request URL** (it carries the fetch centre): never in the log, `/health` or `/issues` — pinned in Task 4.

## File Structure

| File | Responsibility |
|---|---|
| `server/flightpanel/airfield.py` (new) | `Airfield`: recent arrivals/departures, landing course (confirmed after 3 polls), last movement at the airport; persisted course in `state` |
| `server/flightpanel/panel.py` (new) | `Panel` (per-panel parts), `fetch_circle`, `union_bbox`, `active_seconds`, `MAX_FETCH_KM` |
| `server/flightpanel/astro.py` | `Orbit` (shared TLE: load, refresh, store) and `Iss(cfg, orbit)` (per window: state, passes) |
| `server/flightpanel/stats.py` | loses the landing course (now `airfield.py`) |
| `server/flightpanel/build.py` | `recent.update` moves out; shared `noted` set |
| `server/flightpanel/config.py` | `PANEL_MAX_KM`, `PANEL_MAX_RADIUS_KM`, `for_panel(..., remote=False)`, `too_far()`; `active_seconds_per_day` moves to `panel.active_seconds` |
| `server/flightpanel/issues.py` | `without_urls()`, `error_text()`; `note()` strips URLs from examples |
| `server/flightpanel/main.py` | `App` with `panels`, `lan`, `_sync_panels`, `poll_once`, one fetch for all, per-panel observe |
| `server/flightpanel/web.py` | handlers serve `app.lan`; `/stats` adds the shared course |
| `server/flightpanel/panels.py` | limits for added panels, `list` says which panel does not fetch, messages: no restart needed |
| `server/flightpanel/cards/runway.py`, `idle.py` | runway cards read `ctx.airfield` |
| `server/flightpanel/adsblol.py`, `opensky.py`, `adsbdb.py`, `adsbim.py`, `origins.py` | `last_error = error_text(e)` |
| `server/flightpanel/airport.py` | `_PATH_CACHE` capped |
| tests: `test_airfield.py`, `test_panel.py` (new); `test_stats.py`, `test_cards.py`, `test_routes.py`, `test_astro.py`, `test_config.py`, `test_panels.py`, `test_sources.py`, `test_app.py`, `test_web.py`, `test_dream.py` (adapted) | |
| `README.md`, `docs/bauanleitung.md`, `.env.example` | no restart after `panels set`, limits for added panels, module overview |

---

### Task 1: `airfield.py` — what every panel shares about the airport

**Files:**
- Create: `server/flightpanel/airfield.py`, `server/tests/test_airfield.py`
- Modify: `server/flightpanel/stats.py`, `server/flightpanel/build.py` (lines 46–87), `server/flightpanel/cards/runway.py` (51–82), `server/flightpanel/idle.py` (52–62), `server/flightpanel/main.py`, `server/flightpanel/web.py` (148–149)
- Test (adapt): `server/tests/test_stats.py`, `test_cards.py`, `test_routes.py`, `test_app.py`

**Interfaces:**
- Produces: `class Airfield(db: Db)` with attributes `recent: airport.Recent`, `landing_course: float | None` (start value for the next `build_model`), `last_move: float`, `course: float | None`, `prev_course: float | None`, `course_since: float`, `course_seen: float`, `dirty: bool`; methods `observe(model: DisplayModel, now: float) -> None`, `load_legacy(raw: dict) -> None`, `save() -> None` (inside `App.save()`'s transaction, forgets dirty via `Db.after_commit`), `report() -> dict` (`{"now", "prev", "since"}` as `/stats` showed it). Constants `COURSE_CONFIRM = 3` (moved from stats.py), `LANDED_S = 90` (moved from main.py). `idle.Ctx.airfield`. `build_model(...)` no longer calls `recent.update`. `main.absorb_legacy(data_dir, cache, issues, stats, airfield, logos, iss)`.

- [ ] **Step 1: Write the failing tests**

Create `server/tests/test_airfield.py`:

```python
"""Flughafen über Abrufe hinweg, für alle Panels gleich: Landerichtung (ein Wechsel gilt erst nach drei Abrufen),
wer zuletzt landete oder startete, letzte Bewegung am Platz. Die Landerichtung übersteht Neustarts."""

from datetime import datetime

import pytest

from conftest import make_db, state
from flightpanel.airfield import Airfield
from flightpanel.model import DisplayModel


def ts(*a):
    return datetime(*a).timestamp()


def test_course_needs_three_polls_to_flip(tmp_path):
    af = Airfield(make_db(tmp_path))
    t = ts(2026, 10, 10, 12, 0)
    af.observe(DisplayModel(landing_course=249, course_votes=2), t)
    assert af.course == 249 and af.course_since == t
    for i in (1, 2):
        af.observe(DisplayModel(landing_course=69, course_votes=1), t + i * 20)
        assert af.course == 249
    af.observe(DisplayModel(landing_course=69, course_votes=1), t + 60)
    assert af.course == 69 and af.prev_course == 249 and af.course_since == t + 60
    af.observe(DisplayModel(landing_course=249, course_votes=0), t + 80)   # ohne Stimmen: nichts
    assert af.course == 69


def test_the_last_course_is_the_next_start_value_and_landings_count_as_moves(tmp_path):
    af = Airfield(make_db(tmp_path))
    assert af.landing_course is None and af.last_move == 0
    t = ts(2026, 10, 10, 12, 0)
    landing = {"icao24": "3c56ec", "callsign": "EWG9SK", "eta_s": 60}
    af.observe(DisplayModel(landing_course=69, course_votes=0, arrivals=[landing], generated_at=t), t)
    assert af.landing_course == 69                       # Startwert des nächsten Abrufs, auch ohne Stimmen
    assert af.last_move == t                             # setzt gleich auf: Bewegung am Platz
    assert af.recent.hint(state(icao="3c56ec", cs="EWG9SK")) == "arr"
    af.observe(DisplayModel(landing_course=69, arrivals=[{**landing, "eta_s": 600}], generated_at=t + 20), t + 20)
    assert af.last_move == t                             # noch zehn Minuten: keine Bewegung


def test_the_course_survives_a_restart_and_a_hand_edited_one_is_ignored(tmp_path, caplog):
    db = make_db(tmp_path)
    af = Airfield(db)
    t = ts(2026, 10, 10, 12, 0)
    af.observe(DisplayModel(landing_course=249, course_votes=3), t)
    af.observe(DisplayModel(landing_course=69, course_votes=1), t + 60)
    af.save()
    again = Airfield(db)
    assert (again.course, again.prev_course, again.course_since, again.course_seen) == (249, None, t, t + 60)
    assert again.landing_course == 249                   # nach einem Neustart: die bestätigte als Startwert
    db.state_set("course", {"now": "249"})               # von Hand verändert
    with caplog.at_level("WARNING"):
        broken = Airfield(db)
    assert broken.course is None and "Landerichtung" in caplog.text


def test_a_failed_save_is_written_next_time(tmp_path):
    db = make_db(tmp_path)
    af = Airfield(db)
    af.observe(DisplayModel(landing_course=249, course_votes=3), ts(2026, 10, 10, 12, 0))
    with pytest.raises(RuntimeError):
        with db.transaction():
            af.save()
            raise RuntimeError("Platte voll")
    assert Airfield(db).course is None
    af.save()
    assert Airfield(db).course == 249


def test_the_course_of_an_old_stats_json_is_taken_over(tmp_path, caplog):
    t = ts(2026, 10, 10, 12, 0)
    db = make_db(tmp_path)
    af = Airfield(db)
    af.load_legacy({"dex": {}, "course": {"now": 249, "prev": 69, "since": t - 7200, "seen": t}})
    assert af.landing_course == 249
    af.save()
    again = Airfield(db)
    assert (again.course, again.prev_course, again.course_since, again.course_seen) == (249, 69, t - 7200, t)
    odd = Airfield(make_db(tmp_path / "odd"))
    with caplog.at_level("WARNING"):
        odd.load_legacy({"course": {"now": "249", "prev": 69}})
    assert odd.course is None and "Landerichtung" in caplog.text
    odd.load_legacy({"dex": {}})                          # ohne Landerichtung: nichts zu tun
    assert odd.course is None and not odd.dirty


def test_report_shows_the_course_like_stats_did(tmp_path):
    af = Airfield(make_db(tmp_path))
    assert af.report() == {"now": None, "prev": None, "since": None}
    af.observe(DisplayModel(landing_course=249, course_votes=2), ts(2026, 10, 10, 12, 0))
    assert af.report() == {"now": 249, "prev": None, "since": "2026-10-10 12:00"}
```

- [ ] **Step 2: Run them to see them fail**

Run: `… python -m pytest -p no:cacheprovider server/tests/test_airfield.py`
Expected: FAIL with `ModuleNotFoundError: No module named 'flightpanel.airfield'`.

- [ ] **Step 3: Write `airfield.py`**

Create `server/flightpanel/airfield.py`:

```python
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
```

- [ ] **Step 4: Run the new tests**

Run: `… python -m pytest -p no:cacheprovider server/tests/test_airfield.py`
Expected: PASS (6 passed).

- [ ] **Step 5: Take the course out of `stats.py`**

In `server/flightpanel/stats.py`:
- Module docstring: delete the line block `Piste       Landerichtung und seit wann sie gilt, …` (two lines) and change the last paragraph to: `Liegt je Panel in der Datenbank (db.py: Tabellen album, days, rare, Spalten in panels), GET /stats zeigt alles; die Landerichtung führt airfield.py für alle Panels. main.App.save() schreibt, was sich geändert hat.`
- Delete `COURSE_CONFIRM = 3 …`, `from .geo import angle_diff`, and the function `_usable_course`.
- In `Stats.__init__` delete the five lines setting `self.course`, `self.prev_course`, `self.course_since`, `self.course_seen`, `self._pending`.
- In `_load` delete the last three lines (`c = self.db.state_get("course") or {}` and the two assignments).
- In `load_legacy` delete everything about the course: `c = raw.get("course")` (keep `d = raw.get("day")`), `course = c if … else None`, `if c is not None and course is None: skipped += 1`, and the block `if course is not None: …`. Add to its docstring: `Die Landerichtung darin übernimmt airfield.Airfield.load_legacy.`
- In `save` delete the `self.db.state_set("course", …)` statement.
- In `observe` delete the line `self._course(model, now)`; delete the method `_course`.
- In `report` delete the `"course": {…}` entry.

- [ ] **Step 6: `build.py` leaves `recent` to the caller**

In `server/flightpanel/build.py`, `build_model`: delete

```python
    if recent is not None:
        recent.update(model.arrivals, model.departures, model.generated_at)
```

and change the docstring line about `recent` to:

```python
    recent: wer zuletzt hier landete oder startete, über Abrufe hinweg (airport.Recent). Nachgeführt wird es
    danach, einmal je Abruf (airfield.Airfield.observe): Bauen mehrere Panels im selben Abruf, sehen alle denselben
    Stand.
```

- [ ] **Step 7: Runway cards read `ctx.airfield`**

In `server/flightpanel/idle.py`, class `Ctx`, after the `stats` field add:

```python
    airfield: Any = None                          # airfield.Airfield: Landerichtung, für alle Panels gleich
```

In `server/flightpanel/cards/runway.py`, in `runway_cond` and `runway_change_cond` replace `st = ctx.stats` with `st = ctx.airfield` (the rest of both functions stays as it is).

- [ ] **Step 8: Wire it into `main.py` and `web.py`**

`server/flightpanel/main.py`:
- Imports: delete `from .airport import Recent`; add `from .airfield import Airfield` (after the `.adsblol` import). Delete the constant `LANDED_S` (now in airfield.py).
- `absorb_legacy`: new signature and loop; the rest of the function stays:

```python
def absorb_legacy(data_dir: str, cache: Cache, issues: Issues, stats: Stats, airfield: Airfield, logos: LogoStore,
                  iss: Iss | None) -> list[str]:
    """Zustand aus den JSON-Dateien früherer Fassungen (bis Oktober 2026) einmalig übernehmen. Aus stats.json nimmt
    Stats Album und Statistik, Airfield die Landerichtung.
    Gibt die übernommenen Dateien zurück; App benennt sie in *.imported um, sobald das
    Übernommene gespeichert ist. Eine unlesbare oder unbrauchbare Datei bleibt, wie sie ist,
    und steht im Log; der Start geht weiter."""
    taken = []
    for name, stores in (("cache.json", (cache,)), ("issues.json", (issues,)), ("stats.json", (stats, airfield)),
                         (os.path.join("logos", ".missing.json"), (logos,))):
        path = os.path.join(data_dir, name)
        if not os.path.exists(path):
            continue
        raw = read_json(path)
        if not isinstance(raw, dict):                   # unlesbar (read_json hat es geloggt) oder kein Objekt
            log.warning("%s nicht übernommen, bleibt liegen", name)
            continue
        try:
            for store in stores:                        # Stats zuerst: Wirft es, nimmt auch Airfield nichts
                store.load_legacy(raw)
        except Exception as e:                          # von Hand verändert: lieber ohne als gar nicht starten
            log.warning("%s nicht übernommen (%s), bleibt liegen", name, e)
            continue
        taken.append(path)
    # (tle.txt-Teil unverändert)
```

- `App.__init__`: after `self.rare = load_rare(data, self.names)` add `self.airfield = Airfield(self.db)`; call `absorb_legacy(data, self.cache, self.issues, self.stats, self.airfield, self.logos, self.iss)`; change the comment above it to `… Das muss vor dem Modell geschehen: Dessen Landerichtung zu Beginn kommt aus airfield (aus stats.json übernommen).`; add `airfield=self.airfield` to the `IdleScreen(...)` call; startup model: `self.model = DisplayModel(empty=True, source="startup", landing_course=self.airfield.course)`; delete `self.last_airport_move = 0.0` and `self.recent = Recent()`.
- `_poll`: mock branch becomes

```python
        if self.cfg.mock:
            self.model = mock_model(self.cfg, seed=int(t0 // 20))
            self.airfield.observe(self.model, t0)
            self.stats.observe([], self.model, t0)
```

and the live branch, from `build_model` to `_observe`:

```python
            self.model = await build_model(self.cfg, states, self.adsb, self.names,
                                           horizon_s=self.refresh_period(), prev_course=self.airfield.landing_course,
                                           issues=self.issues, adsbim=self.adsbim, origins=self.origins,
                                           source=self.active_source, recent=self.airfield.recent, rare=self.rare)
            self.airfield.observe(self.model, t0)       # wer landet und startet, Landerichtung: einmal je Abruf
            self.counters[self.active_source] += 1
            self.model.last_airport_move = self.airfield.last_move
            await self._observe(states, t0)
```

- `save()`: after `self.issues.save()` add `self.airfield.save()`.

`server/flightpanel/web.py`, handler `stats`:

```python
    async def stats(self, req: web.Request) -> web.Response:
        p = self.panel
        return _pretty({**p.stats.report(), "course": p.airfield.report()})     # Landerichtung: für alle Panels gleich
```

- [ ] **Step 9: Adapt the existing tests**

`server/tests/test_stats.py`:
- Module docstring: `"""Statistik: Tag, Sammelalbum, Flaute, seltene Flugzeuge, Datenbank."""`
- Delete `test_course_needs_three_polls_to_flip` (now in test_airfield.py).
- `test_save_load_roundtrip_and_broken_file`: `assert again.course == 249 and "r" in again.rare …` → `assert "r" in again.rare and again.since == st.since`; `assert broken.dex == {} and broken.course is None` → `assert broken.dex == {} and broken.rare == {}`.
- `test_old_stats_json_is_taken_over`: `… and again.course == 249 and again.prev_course == 69` → drop those two conditions.
- `test_album_and_day_survive_the_database_unchanged`: delete the two course assertions at its end.
- `test_old_stats_json_with_odd_rare_course_and_numbers_keeps_the_good_ones`: rename to `test_old_stats_json_with_odd_rare_and_numbers_keeps_the_good_ones`, expect `"3 Einträge"` (comment `# since, r2, r3`), last line `assert set(st.rare) == {"r1"}`. The `"course"` key stays in `raw`: Stats ignores it.

`server/tests/test_cards.py`: replace `from flightpanel.stats import Stats` with `from flightpanel.airfield import Airfield` (keep `Stats` if other tests still use it — they do: keep both imports), replace the helper `_stats` by

```python
def _airfield(tmp_path, course=249, since_min=60, seen_min=5, prev=None, now=None):
    af = Airfield(make_db(tmp_path))
    af.course, af.prev_course = course, prev
    af.course_since = now - since_min * 60
    af.course_seen = now - seen_min * 60
    return af
```

and in `test_runway_card_only_when_stable` every `stats=_stats(...)` by `airfield=_airfield(...)` (same arguments).

`server/tests/test_routes.py`, `test_returning_plane_keeps_its_direction_through_level_flight_and_landing`: the helper `poll` becomes

```python
    def poll(**kw):
        s = state(icao="3c56ec", cs="EWG9SK", **kw)
        m = asyncio.run(build_model(cfg, [s], adsb, NAMES, recent=recent))
        recent.update(m.arrivals, m.departures, m.generated_at)     # einmal je Abruf, wie airfield.Airfield.observe
        return m
```

and add below it:

```python
def test_build_model_leaves_recent_to_the_caller():
    """Bauen mehrere Panels im selben Abruf ihr Modell, sehen alle denselben Stand; nachgeführt wird danach einmal
    (airfield.Airfield.observe). Sonst sähe das zweite Panel schon, was das erste eingetragen hat."""
    recent = Recent()
    lat, lon = destination(BER[0], BER[1], 69, 15)
    s = state(icao="3c56ec", cs="EWG9SK", lat=lat, lon=lon, alt=800, track=249, vrate=-4)
    m = asyncio.run(build_model(make_cfg(), [s], FakeAdsb({"EWG9SK": OUTBOUND}), NAMES, recent=recent))
    assert m.arrivals and recent.hint(s) is None
```

`server/tests/test_app.py`, `test_old_json_files_are_taken_over_once`: `app.stats.course == 249` → `app.airfield.course == 249`; `assert app.model.landing_course == 249  # Startwert …` → `assert app.airfield.landing_course == 249  # Startwert des ersten Abrufs ist schon die übernommene Piste`; `again.model.landing_course == 249` → `again.airfield.landing_course == 249`.

- [ ] **Step 10: Run the whole suite**

Run: `… sh -c "ruff check . && python -m pytest -p no:cacheprovider"`
Expected: all pass, ruff clean.

- [ ] **Step 11: Commit**

```bash
git add server/flightpanel/airfield.py server/tests/test_airfield.py server/flightpanel/stats.py server/flightpanel/build.py server/flightpanel/cards/runway.py server/flightpanel/idle.py server/flightpanel/main.py server/flightpanel/web.py server/tests/test_stats.py server/tests/test_cards.py server/tests/test_routes.py server/tests/test_app.py
git commit -m "Flughafen für alle Panels: Landerichtung, letzte Starts und Landungen in airfield.py" -m "Was am Platz geschieht, hängt nicht vom Fenster ab. Die Landerichtung (mit drei Abrufen Bestätigung) zieht aus der Statistik um, Recent und die letzte Bewegung aus App; build_model trägt Recent nicht mehr selbst nach, das geschieht einmal je Abruf. /stats zeigt die Landerichtung wie bisher." -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 2: `Orbit` for everyone, `Iss` per window

**Files:**
- Modify: `server/flightpanel/astro.py` (ISS part, lines 93–116 and 197–346), `server/flightpanel/airport.py` (58–82), `server/flightpanel/main.py`
- Test (adapt/add): `server/tests/test_astro.py`, `server/tests/test_model.py`, `server/tests/test_app.py`

**Interfaces:**
- Consumes: nothing new.
- Produces: `class Orbit(db=None, session=None, issues=None, tle_text=None)` with `tle`, `sat`, `tle_fetched`, `version: int` (+1 per new TLE), `last_error`, `set_tle_text(text, fetched) -> bool`, `usable(now) -> bool`, `needs_tle(now) -> bool`, `async refresh_tle() -> bool`, `position(t) -> tuple | None` (TEME km). `class Iss(cfg, orbit: Orbit)` with `cfg`, `orbit`, `passes`, `passes_at` (setting it to 0 forces a recompute), `usable(now)`, `state(t)`, `subpoint(t)`, `compute_passes(start, hours)`, `async update_passes(now)`, `next_pass(now)`. `astro.SUN_CACHE_MAX = 64`, `airport.PATH_CACHE_MAX = 256`. `main.absorb_legacy(..., orbit: Orbit | None)`; `App.orbit`, `App.iss` (the LAN window's, until Task 6).

- [ ] **Step 1: Write the failing tests**

Append to `server/tests/test_astro.py` (it already has `_tle_text()` and `BERLIN`; add `Orbit` to the `from flightpanel.astro import …` line):

```python
def test_two_windows_share_one_orbit_and_recompute_after_new_data():
    """Bahndaten einmal für alle Panels; Stand und Überflüge rechnet jedes Fenster selbst, und neu, sobald neue
    Bahndaten da sind (auch innerhalb der Stunde)."""
    orbit = Orbit(tle_text=_tle_text())
    berlin = Iss(make_cfg(home_lat=BERLIN[0], home_lon=BERLIN[1]), orbit)
    madrid = Iss(make_cfg(home_lat=40.42, home_lon=-3.70), orbit)
    t = 1791654544
    assert berlin.state(t)[:2] != madrid.state(t)[:2]                 # dieselbe ISS, ein anderer Himmel
    now = tle_epoch(orbit.tle[0]) + 86400
    asyncio.run(berlin.update_passes(now))
    assert berlin.passes_at == now
    asyncio.run(berlin.update_passes(now + 60))                       # innerhalb der Stunde: nichts neu
    assert berlin.passes_at == now
    assert orbit.set_tle_text(_tle_text(), now + 120)                 # neue Bahndaten
    asyncio.run(berlin.update_passes(now + 180))
    assert berlin.passes_at == now + 180


def test_the_sun_cache_has_room_for_many_windows():
    from flightpanel import astro
    astro._SUN_CACHE.clear()
    for i in range(20):
        sun_day(1791654544, 52.0 + i / 100, 13.4)
    assert len(astro._SUN_CACHE) == 20
```

(Import `sun_day` too if the file does not yet.) Append to `server/tests/test_model.py`:

```python
def test_the_path_cache_does_not_grow_without_bound():
    """Jede Änderung am Sichtfeld (panels set, bald die Einstellungsseite) gibt neue Schlüssel."""
    from flightpanel import airport
    airport._PATH_CACHE.clear()
    for i in range(airport.PATH_CACHE_MAX + 10):
        path_in_view(make_cfg(home_lat=52.5 + i / 10000), 249, "approach")
    assert 0 < len(airport._PATH_CACHE) <= airport.PATH_CACHE_MAX
```

(`path_in_view` is already imported there for `test_path_in_view_depends_on_cone`; check and import if needed.)

- [ ] **Step 2: Run them to see them fail**

Run: `… python -m pytest -p no:cacheprovider server/tests/test_astro.py server/tests/test_model.py`
Expected: FAIL (`ImportError: cannot import name 'Orbit'`, `AttributeError: … PATH_CACHE_MAX`).

- [ ] **Step 3: Split `Iss` in `astro.py`**

Module docstring, ISS paragraph: `ISS: Bahnelemente (TLE) von Celestrak, ersatzweise einer zweiten Quelle, einmal am Tag für alle Panels (Orbit); zwischengespeichert in der Datenbank (state) und bis 14 Tage nach ihrer Epoche genutzt. Daraus je Fenster (Iss) mit SGP4 die Überflüge der nächsten 24 h, die man sehen kann: ISS mindestens 10° hoch, von der Sonne beschienen, beim Beobachter schon dunkel. Ohne gültige Bahnelemente gibt es keine ISS-Karten, sonst ändert sich nichts.`

Sun cache: add `SUN_CACHE_MAX = 64      # Platz für viele Fenster (Panels) über ein paar Tage` after `_SUN_CACHE: dict[tuple, dict] = {}`, and in `sun_day` replace `if len(_SUN_CACHE) > 8:` by `if len(_SUN_CACHE) >= SUN_CACHE_MAX:`.

Replace the whole `class Iss` by:

```python
class Orbit:
    """Die Bahndaten der ISS (TLE), einmal für alle Panels: von Celestrak, ersatzweise einer zweiten Quelle, einmal
    am Tag; zwischengespeichert in der Datenbank (state: tle, tle_fetched). Wo die ISS von einem Fenster aus steht
    und wann man sie dort sieht, rechnet Iss."""

    def __init__(self, db=None, session: aiohttp.ClientSession | None = None, issues=None,
                 tle_text: str | None = None):
        """db: Bahndaten aus der Datenbank (state: tle, tle_fetched); tle_text: fertige
        Bahndaten, für Tests."""
        self.db = db
        self.session = session
        self.issues = issues
        self.tle: tuple[str, str] | None = None
        self.sat = None
        self.tle_fetched: float = 0
        self.version = 0                # zählt neue Bahndaten: Iss rechnet seine Überflüge dann neu
        self.last_error: str | None = None
        if tle_text is not None:
            self.set_tle_text(tle_text, time.time())
        elif db is not None:
            self._load()

    def _load(self) -> None:
        text = self.db.state_get("tle")
        tle = parse_tle(text) if text else None
        if tle:
            self._set(tle)
            self.tle_fetched = self.db.state_get("tle_fetched", 0)

    def set_tle_text(self, text: str, fetched: float) -> bool:
        """Bahndaten übernehmen (Celestrak-Antwort oder alte tle.txt) und merken."""
        tle = parse_tle(text)
        if not tle:
            return False
        self._set(tle)
        self.tle_fetched = fetched
        if self.db is not None:
            self.db.state_set("tle", "ISS (ZARYA)\n" + "\n".join(tle) + "\n")
            self.db.state_set("tle_fetched", fetched)
        return True

    def _set(self, tle: tuple[str, str]) -> None:
        from sgp4.api import Satrec
        self.sat = Satrec.twoline2rv(*tle)
        self.tle = tle
        self.version += 1

    def usable(self, now: float) -> bool:
        return self.sat is not None and now - tle_epoch(self.tle[0]) < TLE_MAX_AGE_D * 86400

    async def refresh_tle(self) -> bool:
        # unverändert aus der bisherigen Iss.refresh_tle übernehmen (Schleife über TLE_URLS, last_error, issues)
        ...

    def needs_tle(self, now: float) -> bool:
        return self.sat is None or now - self.tle_fetched > TLE_REFRESH_S

    def position(self, t: float) -> tuple[float, float, float] | None:
        """Ort der ISS zur Zeit t (TEME, km); None ohne Bahndaten oder wenn SGP4 scheitert."""
        if self.sat is None:
            return None
        days = t / 86400.0
        err, r, _ = self.sat.sgp4(2440587.5 + math.floor(days), days - math.floor(days))
        return None if err else r


class Iss:
    """Die ISS von einem Fenster aus (cfg: Standort und Sichtfeld eines Panels), aus den gemeinsamen Bahndaten
    (orbit): wo sie gerade steht und die Überflüge der nächsten 24 h, die man von dort sieht."""

    def __init__(self, cfg, orbit: Orbit):
        self.cfg = cfg
        self.orbit = orbit
        self.passes: list[Pass] = []
        self.passes_at: float = 0       # 0: beim nächsten update_passes neu rechnen (auch nach neuen Einstellungen)
        self._version = -1              # aus welchem Stand der Bahndaten passes stammt

    def usable(self, now: float) -> bool:
        return self.orbit.usable(now)

    def state(self, t: float) -> tuple[float, float, float, bool] | None:
        """(Richtung, Höhenwinkel, Entfernung km, beschienen) vom Standort aus."""
        r = self.orbit.position(t)
        if r is None:
            return None
        az, el, rng = look_angles(self.cfg.home_lat, self.cfg.home_lon, self.cfg.home_alt_m, teme_to_ecef(r, t))
        return az, el, rng, sunlit(r, t)

    def subpoint(self, t: float) -> tuple[float, float, float] | None:
        """(Breite, Länge, Höhe km) unter der ISS."""
        r = self.orbit.position(t)
        return None if r is None else ecef_to_geodetic(*teme_to_ecef(r, t))

    # _visible, _in_view und compute_passes: unverändert aus der bisherigen Iss übernehmen

    async def update_passes(self, now: float) -> None:
        if not self.usable(now):
            self.passes = []
            return
        if now - self.passes_at < 3600 and self._version == self.orbit.version:
            return
        version = self.orbit.version
        self.passes = await asyncio.to_thread(self.compute_passes, now - 600)
        self.passes_at, self._version = now, version
        log.info("ISS: %d sichtbare Überflüge in %d h", len(self.passes), PASS_HOURS)

    # next_pass: unverändert übernehmen
```

(The `...` and the comments mark code that moves **verbatim** from the old class; the implementer copies it, it is not new code.)

- [ ] **Step 4: Cap the path cache in `airport.py`**

After `_PATH_CACHE: dict[…] = {}` add `PATH_CACHE_MAX = 256    # jede Änderung am Sichtfeld gibt neue Schlüssel (panels set, Einstellungsseite)`, and in `path_in_view` before `_PATH_CACHE[key] = out`:

```python
    if len(_PATH_CACHE) >= PATH_CACHE_MAX:
        _PATH_CACHE.clear()
```

- [ ] **Step 5: Use it in `main.py`**

- Import `from .astro import Iss, Orbit`.
- `absorb_legacy(..., orbit: Orbit | None)`: rename the last parameter, use `orbit.set_tle_text(...)` in the tle.txt part (`if orbit is not None and os.path.exists(path)`).
- `App.__init__`: replace `self.iss = Iss(cfg, self.db, self.session, self.issues) if cfg.iss else None` by

```python
        self.orbit = Orbit(self.db, self.session, self.issues) if cfg.iss else None
        self.iss = Iss(cfg, self.orbit) if self.orbit else None     # die ISS vom Fenster des Panels am LAN-Port
```

  and pass `self.orbit` to `absorb_legacy`.
- `astro_loop`: `if self.orbit.needs_tle(now): await self.orbit.refresh_tle()` then `await self.iss.update_passes(now)`.
- `health()["iss"]`:

```python
            "iss": None if not self.orbit else {
                "usable": self.orbit.usable(now),
                "tle_fetched_age_h": round((now - self.orbit.tle_fetched) / 3600, 1) if self.orbit.tle_fetched else None,
                "last_error": self.orbit.last_error,
                "visible_passes_24h": len(self.iss.passes),
            },
```

- `start()`: `if self.orbit and not self.cfg.mock:` before `loops.append(self.astro_loop())`.

- [ ] **Step 6: Adapt the existing tests**

`server/tests/test_astro.py`: every `Iss(cfg, tle_text=X)` becomes `Iss(cfg, Orbit(tle_text=X))`; `Iss(cfg, db=db, tle_text=X)` becomes `Iss(cfg, Orbit(db, tle_text=X))`; the TLE tests that construct `Iss(make_cfg(), db=db[, session=…][, issues=…])` only to fetch/store TLEs construct `Orbit(db[, session=…][, issues=…])` and assert on it (`orbit.sat`, `orbit.needs_tle`, `orbit.set_tle_text`, `orbit.refresh_tle`, `orbit.last_error`, `orbit.tle_fetched`); `iss.tle[0]` becomes `iss.orbit.tle[0]`.

`server/tests/test_app.py`: `app.iss.tle` → `app.orbit.tle` (in `test_old_json_files_are_taken_over_once` and `test_an_unusable_tle_file_is_left_alone_and_the_rest_is_taken`), `again.iss.tle == app.iss.tle` → `again.orbit.tle == app.orbit.tle`.

- [ ] **Step 7: Run the whole suite**

Expected: all pass, ruff clean.

- [ ] **Step 8: Commit**

```bash
git add server/flightpanel/astro.py server/flightpanel/airport.py server/flightpanel/main.py server/tests/test_astro.py server/tests/test_model.py server/tests/test_app.py
git commit -m "ISS: Bahndaten einmal für alle (Orbit), Überflüge je Fenster (Iss)" -m "Vorbereitung für mehrere Panels: Celestrak wird einmal am Tag gefragt, jedes Fenster rechnet seine Überflüge selbst und neu, sobald neue Bahndaten da sind. Sonnen- und Pfad-Cache haben Platz für mehr Fenster, der Pfad-Cache wächst nicht mehr ohne Grenze." -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 3: one `noted` set per poll

**Files:**
- Modify: `server/flightpanel/build.py` (`build_model` signature, `_Builder.__init__`, `note`, `flight`)
- Test: `server/tests/test_routes.py`

**Interfaces:**
- Produces: `build_model(..., noted: set[str] | None = None)`; `_Builder(..., noted=None)` with `_once(key: str) -> bool`. Keys: operator notes use `s.icao24` (as before), route notes `f"route_implausible:{callsign}"` and `f"route_unknown:{callsign}"`.

- [ ] **Step 1: Write the failing test**

Append to `server/tests/test_routes.py`:

```python
def test_panels_in_one_poll_count_each_issue_once(tmp_path):
    """Mehrere Panels bauen im selben Abruf ihr Modell (main.App._poll) mit einem gemeinsamen noted: Jede
    Auffälligkeit zählt einmal je Abruf, nicht einmal je Panel."""
    from flightpanel.issues import Issues
    issues = Issues(Db(str(tmp_path / "i.db")))
    fck = state(icao="3cce71", cs="FCK2FT", lat=52.3239, lon=12.8735, alt=690, track=43, vrate=0.3,
                category="A1", actype="B350")                                     # unbekannter Betreiber
    odd = state(icao="3c0002", cs="EZY77", lat=52.45, lon=13.45, alt=2000)       # Route passt nicht zur Position
    adsb = FakeAdsb({"EZY77": {"origin": "LIS", "dest": "MAD", "origin_pos": [38.77, -9.13],
                               "dest_pos": [40.47, -3.56], "airline": "easyJet", "airline_icao": "EZY"}})
    noted: set[str] = set()
    for cfg in (make_cfg(), make_cfg(home_lat=52.40, home_lon=13.30)):
        asyncio.run(build_model(cfg, [fck, odd], adsb, NAMES, issues=issues, noted=noted))
    assert issues.items["unknown_operator:FCK"]["seen"] == 1
    assert issues.items["route_implausible:EZY77"]["seen"] == 1
    assert issues.items["route_unknown:EZY"]["seen"] == 1
    asyncio.run(build_model(make_cfg(), [fck, odd], adsb, NAMES, issues=issues))     # der nächste Abruf: wieder
    assert issues.items["unknown_operator:FCK"]["seen"] == 2
```

- [ ] **Step 2: Run it to see it fail**

Expected: FAIL with `TypeError: build_model() got an unexpected keyword argument 'noted'`.

- [ ] **Step 3: Implement**

`build_model`: add the parameter `noted: set[str] | None = None` after `rare`, extend the docstring with
`noted: was in diesem Abruf schon nach /issues gemeldet ist. Panels, die im selben Abruf ihr Modell bauen, teilen es (main.App._poll): So zählt jede Auffälligkeit einmal je Abruf, nicht je Panel.`
and pass it on: `b = _Builder(cfg, adsb, names, issues, origins, recent, rare, noted)`.

`_Builder.__init__`: new last parameter `noted: set[str] | None = None`; replace `self._noted: set[str] = set()  # schon nach /issues gemeldet` by

```python
        self._noted: set[str] = set() if noted is None else noted   # in diesem Abruf schon nach /issues gemeldet
```

Add the method:

```python
    def _once(self, key: str) -> bool:
        """Zum ersten Mal in diesem Abruf (für alle Panels, die ihn teilen)? Dann gilt es ab jetzt als gemeldet."""
        if key in self._noted:
            return False
        self._noted.add(key)
        return True
```

`note`: `if self.issues is not None and self._once(s.icao24):` then `note_operator(...)` (drop the explicit `self._noted.add`).

`flight`: the two route notes become

```python
            if issues is not None and self._once(f"route_implausible:{s.callsign}"):
                issues.note("route_implausible", s.callsign, s.callsign, route=f"{origin}-{dest}",
                            pos=[round(s.lat, 2), round(s.lon, 2)])
```

and

```python
        if airline and not (origin or dest) and issues is not None and self._once(f"route_unknown:{s.callsign}"):
            issues.note("route_unknown", callsign_airline(s.callsign) or s.callsign, s.callsign)
```

(the `log.info("Route … verworfen")` line above the first note stays unconditional).

- [ ] **Step 4: Run the whole suite**, expected all green.

- [ ] **Step 5: Commit**

```bash
git add server/flightpanel/build.py server/tests/test_routes.py
git commit -m "Auffälligkeiten einmal je Abruf, auch wenn mehrere Panels ihr Modell bauen" -m "build_model nimmt ein gemeinsames noted für alle Panels eines Abrufs; Betreiber und Routenfehler zählen so nicht doppelt." -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 4: no request URLs in error texts

**Files:**
- Modify: `server/flightpanel/issues.py`, `adsblol.py` (line 91), `opensky.py` (71), `adsbdb.py` (57), `adsbim.py` (145), `origins.py` (90), `main.py` (`_note_poll_error`, `_task_ended`)
- Test: `server/tests/test_sources.py`, `server/tests/test_app.py`

**Interfaces:**
- Produces: `issues.without_urls(text: str) -> str`, `issues.error_text(e: BaseException) -> str`. `Issues.note()` strips URLs from string example values.

- [ ] **Step 1: Write the failing tests**

Append to `server/tests/test_sources.py` (add imports `import json`, `import aiohttp`, `from multidict import CIMultiDict, CIMultiDictProxy`, `from yarl import URL`, `from conftest import make_db`, `from flightpanel.issues import Issues, error_text, without_urls` as needed — all three libraries come with aiohttp):

```python
def response_error(url: str) -> aiohttp.ClientResponseError:
    """Ein Fehler, wie raise_for_status() ihn wirft: Er nennt die ganze Anfrage samt Parametern."""
    info = aiohttp.RequestInfo(URL(url), "GET", CIMultiDictProxy(CIMultiDict()), URL(url))
    return aiohttp.ClientResponseError(info, (), status=400, message="Bad Request")


def test_error_texts_name_the_server_but_not_the_request():
    """OpenSky und adsb.lol tragen Mittelpunkt und Umkreis des Abrufs in der Adresse, also ungefähr das Fenster."""
    e = response_error("https://opensky-network.org/api/states/all?lamin=52.1234&lomin=13.0001&lamax=52.9&lomax=13.9")
    assert "52.1234" in str(e)                                   # so käme es sonst in Log, /health und /issues
    text = error_text(e)
    assert "52.1234" not in text and "13.0001" not in text
    assert "400" in text and "https://opensky-network.org/…" in text
    point = "Fehler bei https://api.adsb.lol/v2/point/52.5200/13.4000/22, nochmal"
    assert without_urls(point) == "Fehler bei https://api.adsb.lol/…, nochmal"
    assert error_text(TimeoutError()) == "TimeoutError"


def test_issues_never_store_a_request_url(tmp_path):
    issues = Issues(make_db(tmp_path))
    issues.source_error("opensky", "400, url='https://opensky-network.org/api/states/all?lamin=52.1234'")
    issues.note("source_error", "adsblol", error="https://api.adsb.lol/v2/point/52.5200/13.4000/22")
    text = json.dumps(issues.report())
    assert "52.1234" not in text and "52.5200" not in text and "opensky-network.org" in text
```

Append to `server/tests/test_app.py` (it imports `make_cfg`; import `response_error` from `test_sources`, or copy the helper):

```python
def test_a_failing_opensky_request_leaves_no_position_in_log_or_issues(tmp_path, caplog):
    cfg = make_cfg(mock=True, data_dir=str(tmp_path), metar=False, iss=False, logo_fetch=False)

    async def run():
        app = App(cfg, env={})
        try:
            e = response_error("https://opensky-network.org/api/states/all?lamin=52.1234&lomin=13.0001")
            with caplog.at_level("WARNING"):
                app._note_poll_error(e)
            assert "opensky-network.org" in caplog.text and "52.1234" not in caplog.text
            assert "52.1234" not in json.dumps(app.issues_report()) and app.issues.items
        finally:
            await app.session.close()

    asyncio.run(run())
```

If `aiohttp.RequestInfo` takes different positional arguments in the locked aiohttp version, adapt the helper (keyword arguments `url=`, `method=`, `headers=`, `real_url=`); the assertions stay.

- [ ] **Step 2: Run them to see them fail** (`ImportError: cannot import name 'error_text'`).

- [ ] **Step 3: Implement**

`server/flightpanel/issues.py`, after the constants:

```python
# Eine Adresse in einem Text: Schema und Server, dann Pfad und Parameter bis zum nächsten Leerzeichen oder
# Anführungszeichen (aiohttp schreibt url='...')
_URL = re.compile(r"\b([a-z][a-z0-9+.-]*://[^/\s'\"?#]+)[^\s'\"]*", re.IGNORECASE)


def without_urls(text: str) -> str:
    """Adressen in einem Text auf ihren Server kürzen: "https://opensky-network.org/…". aiohttp nennt in Fehlern die
    ganze Anfrage samt Parametern, und bei adsb.lol und OpenSky stehen darin Mittelpunkt und Umkreis des Abrufs,
    also ungefähr das Fenster. Log, /health und /issues werden gern geteilt; dort hat das nichts verloren."""
    return _URL.sub(r"\1/…", text)


def error_text(e: BaseException) -> str:
    """Ein Fehler als Text für Log, /health und /issues: ohne Adressen (without_urls), ohne Text sein Typ."""
    return without_urls(str(e)) or type(e).__name__
```

`Issues.note`: first line of the body:

```python
        example = {k: without_urls(v) if isinstance(v, str) else v for k, v in example.items()}
```

Sources: replace `self.last_error = str(e) or type(e).__name__` by `self.last_error = error_text(e)` in `adsblol.py`, `opensky.py`, `adsbdb.py`, `adsbim.py`, `origins.py`, importing `error_text` from `.issues` (extend the existing `from .issues import Issues` where there is one).

`main.py`: import `error_text` from `.issues`; in `_note_poll_error` `msg = error_text(e)`; in `_task_ended` `error=error_text(e)[:300]` (the `log.error("Hintergrundschleife beendet: %s", e, …)` line: `error_text(e)` instead of `e`).

- [ ] **Step 4: Run the whole suite**, expected all green.

- [ ] **Step 5: Commit**

```bash
git add server/flightpanel/issues.py server/flightpanel/adsblol.py server/flightpanel/opensky.py server/flightpanel/adsbdb.py server/flightpanel/adsbim.py server/flightpanel/origins.py server/flightpanel/main.py server/tests/test_sources.py server/tests/test_app.py
git commit -m "Fehlertexte ohne Anfrage-Adressen: kein Fenster-Standort in Log, /health und /issues" -m "aiohttp nennt in Fehlern die ganze Anfrage, bei OpenSky und adsb.lol samt Mittelpunkt des Abrufs. Adressen werden auf den Server gekürzt, in den Quellen, im Abruffehler und in jedem Beispiel, das /issues speichert." -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 5: limits for added panels

**Files:**
- Modify: `server/flightpanel/config.py`, `server/flightpanel/panels.py`, `.env.example`
- Test: `server/tests/test_config.py`, `server/tests/test_panels.py`

**Interfaces:**
- Produces: `Config.panel_max_km: float` (env `PANEL_MAX_KM`, default 60), `Config.panel_max_radius_km: float` (env `PANEL_MAX_RADIUS_KM`, default 80), both server scope; `Config.for_panel(server, settings, problems=None, remote=False)`; `config.too_far(server: Config, panel: Config) -> str | None`. CLI: `panels._check(cfg, pid, settings)`.

- [ ] **Step 1: Write the failing tests**

Append to `server/tests/test_config.py` (imports: `replace` from dataclasses, `too_far` from flightpanel.config):

```python
def test_added_panels_have_a_bounded_radius():
    """Ein Abruf deckt alle Panels ab: Weitere Panels (für Freunde) rufen höchstens PANEL_MAX_RADIUS_KM weit ab.
    Das Panel am LAN-Port nicht (remote=False): Wer für sich allein betreibt, bestimmt selbst."""
    server = make_cfg()
    assert Config.for_panel(server, {"RADIUS_KM": "80"}, remote=True).radius_km == 80
    with pytest.raises(ValueError, match=r"RADIUS_KM='81': für weitere Panels höchstens 80 km \(PANEL_MAX_RADIUS_KM\)"):
        Config.for_panel(server, {"RADIUS_KM": "81"}, remote=True)
    with pytest.raises(ValueError, match="SCAN_RADIUS_KM='200'"):
        Config.for_panel(server, {"SCAN_RADIUS_KM": "200"}, remote=True)
    assert Config.for_panel(server, {"RADIUS_KM": "200"}).radius_km == 200
    problems: list[tuple[str, str]] = []
    tolerant = Config.for_panel(server, {"RADIUS_KM": "200", "VIEW_FROM": "90"}, problems, remote=True)
    assert tolerant.radius_km == server.radius_km and tolerant.view_from == 90
    assert [k for k, _ in problems] == ["RADIUS_KM"]
    small = replace(server, panel_max_radius_km=30)
    assert Config.for_panel(small, {}, remote=True).fetch_radius_km == 30          # auch der Default bleibt drunter


def test_added_panels_stay_near_the_airport():
    server = make_cfg()                                                              # BER
    near = Config.for_panel(server, {"HOME_LAT": "52.40", "HOME_LON": "14.05"}, remote=True)
    assert too_far(server, near) is None
    munich = Config.for_panel(server, {"HOME_LAT": "48.35", "HOME_LON": "11.79"}, remote=True)
    why = too_far(server, munich)
    assert "PANEL_MAX_KM" in why and "BER" in why and "km" in why
    assert too_far(replace(server, panel_max_km=500), munich) is None


def test_the_limits_are_server_settings():
    cfg = Config.from_env({"PANEL_MAX_KM": "30", "PANEL_MAX_RADIUS_KM": "50", "DATA_DIR": DATA})
    assert (cfg.panel_max_km, cfg.panel_max_radius_km) == (30, 50)
    with pytest.raises(ValueError, match="keine Panel-Einstellung: PANEL_MAX_KM"):
        Config.for_panel(cfg, {"PANEL_MAX_KM": "999"})
```

(`DATA` comes from conftest; import it if test_config does not yet.)

Append to `server/tests/test_panels.py`:

```python
def test_added_panels_stay_near_the_airport_with_a_bounded_radius(run):
    """Weitere Panels stehen höchstens PANEL_MAX_KM vom Flughafen und rufen höchstens PANEL_MAX_RADIUS_KM weit ab;
    das Panel am LAN-Port nicht."""
    assert run("add", "anna")[0] == 0
    code, _, err = run("set", "anna", "HOME_LAT=48.35", "HOME_LON=11.79")         # München, der Flughafen ist BER
    assert code == 2 and "PANEL_MAX_KM" in err and "BER" in err
    code, _, err = run("set", "anna", "RADIUS_KM=120")
    assert code == 2 and "RADIUS_KM='120': für weitere Panels höchstens 80 km (PANEL_MAX_RADIUS_KM)" in err
    assert run("show", "anna")[1].startswith("anna: nichts gesetzt")              # nichts davon gespeichert
    assert run("set", "anna", "HOME_LAT=52.40", "HOME_LON=14.05", "RADIUS_KM=30")[0] == 0
    assert run("set", "home", "HOME_LAT=48.35", "HOME_LON=11.79", "RADIUS_KM=120")[0] == 0   # LAN-Port: frei


def test_list_names_a_panel_that_does_not_fetch(run, tmp_path):
    """Steht ein weiteres Panel trotzdem zu weit weg (PANEL_MAX_KM später verkleinert, Handänderung), sagt list es."""
    run("add", "anna")
    Db(str(tmp_path / "flightpanel.db")).panel_set_settings("anna", {"HOME_LAT": "48.35", "HOME_LON": "11.79"})
    _, out, _ = run("list")
    assert re.search(r"^anna .*ruft nicht ab: HOME_LAT/HOME_LON liegen \d+ km von BER", out, re.M)
    assert re.search(r"^home .*LAN-Port", out, re.M)
```

- [ ] **Step 2: Run them to see them fail.**

- [ ] **Step 3: Implement in `config.py`**

New server fields, in the section "Betrieb" after `lan_panel`:

```python
    # Weitere Panels (panels add, nicht das am LAN-Port): Ein Abruf deckt alle ab. Damit er klein bleibt, steht ihr
    # Fenster höchstens PANEL_MAX_KM vom Flughafen, und sie rufen höchstens PANEL_MAX_RADIUS_KM weit ab.
    panel_max_km: float = _env("PANEL_MAX_KM", 60.0, _km)
    panel_max_radius_km: float = _env("PANEL_MAX_RADIUS_KM", 80.0, _km)
```

`for_panel`: new keyword parameter `remote: bool = False`; docstring addition: `remote: ein weiteres Panel (nicht das am LAN-Port). Dann gelten RADIUS_KM und SCAN_RADIUS_KM höchstens bis PANEL_MAX_RADIUS_KM: Ein größerer Wert ist ein Fehler wie jeder ungültige (ohne problems bricht er ab, mit problems bleibt er weg), und auch was ohne Angabe gälte, bleibt darunter. Ob das Fenster nah genug am Flughafen liegt, sagt too_far().` Body, after `values = _parse(panel, settings, problems)`:

```python
        if remote:
            _cap_radii(server, settings, values, problems)
        if "radius_km" in values and "scan_radius_km" not in values:
            values["scan_radius_km"] = values["radius_km"]
        if remote:
            for key in ("radius_km", "scan_radius_km"):
                values[key] = min(values.get(key, getattr(server, key)), server.panel_max_radius_km)
```

(the existing two `radius_km`/`scan_radius_km` lines are the middle part; keep the CALM and CARD code after them unchanged).

Module-level functions (after `_env_name`):

```python
def _cap_radii(server: Config, settings: Mapping[str, str], values: dict,
               problems: list[tuple[str, str]] | None) -> None:
    """Weitere Panels: ausdrücklich gesetzte Radien über PANEL_MAX_RADIUS_KM sind ungültig, wie in _parse."""
    cap = server.panel_max_radius_km
    for key in ("radius_km", "scan_radius_km"):
        if values.get(key, 0) > cap:
            name = _env_name(key)
            msg = f"{name}={_text(settings[name])!r}: für weitere Panels höchstens {cap:g} km (PANEL_MAX_RADIUS_KM)"
            if problems is None:
                raise ValueError(msg)
            problems.append((name, msg))
            del values[key]


def too_far(server: Config, panel: Config) -> str | None:
    """Liegt das Fenster eines weiteren Panels (nicht das am LAN-Port) zu weit vom Heimatflughafen? Ein Abruf deckt
    alle Panels ab, und ein Fenster weit weg machte ihn für alle größer. None, wenn es passt; sonst der Satz für
    panels.py, Log und /issues."""
    d = haversine_km(server.airport_lat, server.airport_lon, panel.home_lat, panel.home_lon)
    if d <= server.panel_max_km:
        return None
    return (f"HOME_LAT/HOME_LON liegen {d:.0f} km von {server.local_airport_iata}, für weitere Panels höchstens "
            f"{server.panel_max_km:g} km (PANEL_MAX_KM)")
```

- [ ] **Step 4: Implement in `panels.py`**

Import `too_far`. Add before `_save`:

```python
def _check(cfg: Config, pid: str, settings: dict[str, str]) -> None:
    """Prüft die Einstellungen eines Panels mit denselben Parsern wie der Server. Für weitere Panels (nicht das am
    LAN-Port) gelten dazu die Grenzen aus .env: Abrufradius höchstens PANEL_MAX_RADIUS_KM, das Fenster höchstens
    PANEL_MAX_KM vom Flughafen. ValueError nennt, was nicht passt."""
    remote = pid != cfg.lan_panel
    panel = Config.for_panel(cfg, settings, remote=remote)
    why = too_far(cfg, panel) if remote else None
    if why:
        raise ValueError(f"{why}. HOME_LAT und HOME_LON zusammen aufs Fenster setzen, oder PANEL_MAX_KM in .env "
                         "erhöhen")
```

`_save`: replace `Config.for_panel(cfg, new)` by `_check(cfg, p["id"], new)`.

`cmd_list`:

```python
def cmd_list(db: Db, cfg: Config, args) -> None:
    for p in db.panel_list():
        when = time.strftime("%Y-%m-%d", time.localtime(p["created"]))
        print(f"{p['id']:<12} {p['name']:<20} {len(p['settings']):>2} Einstellungen, seit {when}{_note(cfg, p)}")


def _note(cfg: Config, p: dict) -> str:
    """Für list: das Panel am LAN-Port, oder ein weiteres, das nicht abruft, weil sein Fenster zu weit vom Flughafen
    liegt (geprüft wie im Server; Einstellungen, die er übergeht, zählen dabei nicht)."""
    if p["id"] == cfg.lan_panel:
        return " (LAN-Port)"
    why = too_far(cfg, Config.for_panel(cfg, p["settings"], [], remote=True))
    return f" (ruft nicht ab: {why})" if why else ""
```

- [ ] **Step 5: `.env.example`**, after the `# LAN_PANEL=home` lines:

```
# Weitere Panels (python -m flightpanel.panels add …): ihr Fenster höchstens so weit vom Flughafen,
# ihr Abrufradius (RADIUS_KM, SCAN_RADIUS_KM) höchstens so groß. Ein Abruf deckt alle Panels ab.
# PANEL_MAX_KM=60
# PANEL_MAX_RADIUS_KM=80
```

- [ ] **Step 6: Run the whole suite**, expected all green.

- [ ] **Step 7: Commit**

```bash
git add server/flightpanel/config.py server/flightpanel/panels.py .env.example server/tests/test_config.py server/tests/test_panels.py
git commit -m "Grenzen für weitere Panels: nah am Flughafen, begrenzter Abrufradius" -m "PANEL_MAX_KM (60) und PANEL_MAX_RADIUS_KM (80) aus .env halten den gemeinsamen Abruf klein. panels lehnt Werte darüber ab, list nennt ein Panel, das deshalb nicht abruft. Das Panel am LAN-Port bleibt frei." -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 6: many panels in one `App`

**Files:**
- Create: `server/flightpanel/panel.py`, `server/tests/test_panel.py`
- Modify: `server/flightpanel/main.py`, `server/flightpanel/web.py`, `server/flightpanel/config.py` (delete `active_seconds_per_day`), `server/flightpanel/panels.py` (messages)
- Test (adapt/add): `server/tests/test_app.py`, `server/tests/test_web.py`, `server/tests/test_dream.py`

**Interfaces:**
- Consumes: `Airfield` (Task 1), `Orbit`, `Iss(cfg, orbit)` (Task 2), `build_model(..., noted=)` (Task 3), `error_text` (Task 4), `Config.for_panel(..., remote=)`, `too_far` (Task 5).
- Produces: `panel.Panel(app, pid, cfg, created=0.0, updated=0.0, off=None)` with `id, cfg, created, updated, off, stats, iss, idle, renderer, model, last_frame_fetch, frame_count`, `configure(cfg, updated=0.0, off=None)`, `active(now) -> bool`, `rest()`, `covers(s: State) -> bool`, `summary(now) -> dict`; `panel.fetch_circle(cfgs) -> (lat, lon, radius_km)`, `panel.union_bbox(cfgs) -> (lamin, lomin, lamax, lomax)`, `panel.active_seconds(cfgs) -> int`, `panel.MAX_FETCH_KM`. `App.panels: dict[str, Panel]`, `App.lan` (property), `App._sync_panels()`, `App.poll_once(t0) -> float` (seconds until the next pass), `App._poll(t0, active=None)`, `App._observe(p, states, t0)`, `App._age_models(panels)`, `main.REST_S = 60`. `App` no longer has `cfg`, `stats`, `model`, `iss`, `idle`, `renderer`, `frame_count`, `last_frame_fetch` (they live on panels; the server settings are `App.server_cfg`).

- [ ] **Step 1: Write the failing tests for the area helpers**

Create `server/tests/test_panel.py`:

```python
"""Was alle Panels gemeinsam brauchen: ein Abruf, der jedes abdeckt, und die Zeit, in der eines abruft. Mit einem
Panel genau wie bisher."""

from dataclasses import replace

from conftest import make_cfg
from flightpanel.geo import haversine_km
from flightpanel.panel import MAX_FETCH_KM, active_seconds, fetch_circle, union_bbox


def test_one_panel_fetches_exactly_as_before():
    cfg = make_cfg(radius_km=30, scan_radius_km=45)
    assert fetch_circle([cfg]) == (cfg.home_lat, cfg.home_lon, cfg.fetch_radius_km)
    assert union_bbox([cfg]) == cfg.bbox
    for quiet in ((0, 390), (1380, 390), (0, 0), (300, 300)):
        c = replace(cfg, quiet_hours=quiet)
        assert active_seconds([c]) == (24 * 60 - (quiet[1] - quiet[0]) % (24 * 60)) * 60, quiet


def test_the_fetch_covers_every_panel_and_stays_within_what_adsb_lol_serves():
    a = make_cfg()
    b = make_cfg(home_lat=52.40, home_lon=14.05, radius_km=10, scan_radius_km=10)
    lat, lon, r = fetch_circle([a, b])
    for c in (a, b):
        assert haversine_km(lat, lon, c.home_lat, c.home_lon) + c.fetch_radius_km <= r + 1e-9
    lamin, lomin, lamax, lomax = union_bbox([a, b])
    for c in (a, b):
        box = c.bbox
        assert lamin <= box[0] and lomin <= box[1] and lamax >= box[2] and lomax >= box[3]
    far = make_cfg(home_lat=60.17, home_lon=24.94)                     # Helsinki
    assert fetch_circle([a, far])[2] == MAX_FETCH_KM


def test_the_fetch_runs_while_any_panel_is_awake():
    early = make_cfg(quiet_hours=(22 * 60, 6 * 60))                    # 22:00-06:00 dunkel
    late = make_cfg(quiet_hours=(0, 8 * 60))                           # 00:00-08:00 dunkel
    owl = make_cfg(quiet_hours=(0, 0))                                 # nie
    assert active_seconds([early]) == 16 * 3600
    assert active_seconds([early, late]) == 18 * 3600                  # alle dunkel nur 00:00-06:00
    assert active_seconds([early, owl]) == 24 * 3600
```

Run: `… python -m pytest -p no:cacheprovider server/tests/test_panel.py` → FAIL (`ModuleNotFoundError: flightpanel.panel`).

- [ ] **Step 2: Write `panel.py`**

Create `server/flightpanel/panel.py`:

```python
"""Ein Panel: ein Fenster mit seinem LED-Panel. Der Server bedient mehrere mit einem Abruf (main.App).

Gemeinsam haben alle Panels die Flugdaten (ein Abruf in einem Umkreis um alle: fetch_circle, union_bbox), Routen,
Wetter, Logos, die Bahndaten der ISS und was am Flughafen geschieht (airfield.py). Jedes Panel hat seine
Einstellungen (Config.for_panel aus der Datenbank, gesetzt mit panels.py), sein Display-Modell, seinen Renderer und
seine Leerlauf-Karten, seine Statistik und sein Album (stats.py) und die ISS-Überflüge über seinem Fenster
(astro.Iss). Das Panel am LAN-Port (LAN_PANEL) ist eines davon; weitere legt panels add an.

Ein Panel ruht in seiner Ruhezeit (QUIET_HOURS) und wenn es aus ist: Ein weiteres Panel, dessen Fenster weiter als
PANEL_MAX_KM vom Flughafen liegt (config.too_far), ruft nicht ab, statt den Abruf für alle zu vergrößern."""

from dataclasses import replace
from typing import TYPE_CHECKING

from .astro import Iss
from .cards import CARDS
from .config import Config, in_hours
from .geo import haversine_km
from .idle import IdleScreen
from .model import DisplayModel, State
from .render import Renderer
from .stats import Stats

if TYPE_CHECKING:
    from .main import App

MAX_FETCH_KM = 250 * 1.852      # adsb.lol liefert höchstens 250 nm um einen Punkt
DAY_MIN = 24 * 60


def fetch_circle(cfgs: list[Config]) -> tuple[float, float, float]:
    """(Breite, Länge, Radius km) eines Abrufs, der den Abrufradius jedes dieser Panels abdeckt: um den Schwerpunkt
    ihrer Fenster, so weit, dass der fernste Rand drin ist, höchstens MAX_FETCH_KM. Mit einem Panel genau dessen
    Abruf wie bisher: sein Fenster und sein Abrufradius."""
    lat = sum(c.home_lat for c in cfgs) / len(cfgs)
    lon = sum(c.home_lon for c in cfgs) / len(cfgs)
    radius = max(haversine_km(lat, lon, c.home_lat, c.home_lon) + c.fetch_radius_km for c in cfgs)
    return lat, lon, min(radius, MAX_FETCH_KM)


def union_bbox(cfgs: list[Config]) -> tuple[float, float, float, float]:
    """Die Box für OpenSky, die die Box jedes dieser Panels enthält (Config.bbox); mit einem Panel dessen Box."""
    boxes = [c.bbox for c in cfgs]
    return (min(b[0] for b in boxes), min(b[1] for b in boxes), max(b[2] for b in boxes), max(b[3] for b in boxes))


def active_seconds(cfgs: list[Config]) -> int:
    """Sekunden pro Tag, in denen mindestens eines dieser Panels abruft (außerhalb seiner Ruhezeit): So lange muss
    das OpenSky-Budget reichen. Minute für Minute gezählt, Ruhezeiten über Mitternacht gehen auch."""
    return 60 * sum(1 for minute in range(DAY_MIN) if any(not in_hours(c.quiet_hours, minute) for c in cfgs))


class Panel:
    def __init__(self, app: "App", pid: str, cfg: Config, created: float = 0.0, updated: float = 0.0,
                 off: str | None = None):
        """app: was alle Panels teilen (db, names, rare, logos, metar, orbit, airfield). created und updated: die
        Zeitstempel seiner Zeile in der Datenbank, an denen main.App Änderungen erkennt. off: warum es nicht
        abruft (config.too_far), sonst None."""
        self.app = app
        self.id = pid
        self.created = created
        self.stats = Stats(app.db, pid, cfg, app.names, app.rare)
        self.iss = Iss(cfg, app.orbit) if app.orbit is not None else None
        self.model = DisplayModel(empty=True, source="startup", landing_course=app.airfield.landing_course)
        self.renderer: Renderer | None = None
        self.last_frame_fetch = 0.0     # setzt web.py bei jedem /frame
        self.frame_count = 0
        self.configure(cfg, updated, off)

    def configure(self, cfg: Config, updated: float = 0.0, off: str | None = None) -> None:
        """Einstellungen anwenden: beim Start und wenn panels.py sie geändert hat. Album und Statistik, das letzte
        Modell, die Abrufe des ESP32 und die Seite, die gerade dran ist, bleiben; Leerlauf-Karten und Renderer
        entstehen neu, die ISS-Überflüge rechnet der nächste Lauf von main.App.astro_loop neu."""
        self.cfg, self.updated, self.off = cfg, updated, off
        self.stats.cfg = cfg
        if self.iss is not None:
            self.iss.cfg, self.iss.passes_at = cfg, 0
        app = self.app
        self.idle = IdleScreen(cfg, CARDS, stats=self.stats, metar=app.metar, astro=self.iss, logos=app.logos,
                               names=app.names, airfield=app.airfield)
        self.renderer = Renderer(cfg, app.logos, idle=self.idle,
                                 rotation=self.renderer.rotation if self.renderer is not None else None)

    def active(self, now: float) -> bool:
        """Ruft es gerade ab? Nicht in seiner Ruhezeit (im Mock-Modus nie) und nicht, wenn es aus ist."""
        return self.off is None and (self.cfg.mock or self.cfg.brightness(now) > 0)

    def rest(self) -> None:
        """Ruhezeit oder aus: Panel schwarz, nichts abrufen. Das letzte Lagebild gilt nicht mehr: Flüge weg, Daten
        als alt markiert, sonst zeigte die Vorschau bis zum Morgen dieselben Flüge."""
        if not self.model.stale:
            self.model = replace(self.model, flights=[], empty=True, stale=True)

    def covers(self, s: State) -> bool:
        """Liegt das Flugzeug in seinem Abrufradius? Nur das zählt in seine Statistik; der gemeinsame Abruf reicht
        für die anderen Panels weiter."""
        return haversine_km(self.cfg.home_lat, self.cfg.home_lon, s.lat, s.lon) <= self.cfg.fetch_radius_km

    def summary(self, now: float) -> dict:
        """Für /health: was es zeigt, ob es aus ist, wann sein ESP32 zuletzt da war."""
        age = now - self.last_frame_fetch if self.last_frame_fetch else None
        return {"flights": len(self.model.flights), "model_stale": self.model.stale,
                "brightness_now": self.renderer.brightness(now), "off": self.off,
                "esp32": {"last_fetch_age_s": round(age) if age is not None else None, "fetches": self.frame_count,
                          "alive": age is not None and age < 300}}
```

Run test_panel.py → PASS. Delete `Config.active_seconds_per_day` from `config.py` (its only user, `opensky_interval`, moves to `active_seconds` below).

- [ ] **Step 3: Write the failing App tests**

Append to `server/tests/test_app.py` (imports: `from conftest import DATA, FakeAdsb, state`, `from flightpanel.geo import destination, haversine_km`, `from flightpanel.main import REST_S`; `BER` from conftest):

```python
def panel_rows(tmp_path, **panels) -> None:
    """Panels in der Datenbank, wie panels.py sie anlegt: home und die übergebenen (id=Einstellungen)."""
    db = Db(db_path(str(tmp_path)))
    for pid, settings in {"home": {}, **panels}.items():
        db.panel_add(pid, settings=settings)
    db.close()


ANNA = {"HOME_LAT": "52.40", "HOME_LON": "14.05", "RADIUS_KM": "10", "SCAN_RADIUS_KM": "10"}   # östlich von BER


def test_one_poll_serves_every_panel_with_one_fetch(tmp_path):
    """Zwei Panels, ein Abruf: Sein Umkreis deckt beide ab. Landefolge und Landerichtung sind in beiden gleich, die
    Statistik zählt je Panel nur, was in seinem Abrufradius war, und /issues zählt einmal je Abruf."""
    shutil.copy(os.path.join(DATA, "airlines.json"), tmp_path)
    panel_rows(tmp_path, anna=ANNA)
    cfg = make_cfg(data_dir=str(tmp_path), metar=False, iss=False, logo_fetch=False, quiet_hours=(0, 0),
                   route_adsbim=False, track_origin=False)
    lat, lon = destination(BER[0], BER[1], 69, 15)
    states = [state(icao="a1", cs="EZY1", lat=52.55, lon=13.42, alt=1500),          # nur bei home
              state(icao="b1", cs="DLH2", lat=52.40, lon=14.06, alt=1500),          # nur bei anna
              # landet, Betreiber XYZ steht nicht in airlines.json: beide Panels sehen ihn in der Landefolge
              state(icao="3c56ec", cs="XYZ9SK", lat=lat, lon=lon, alt=800, track=249, vrate=-4)]
    calls = []

    async def fetch(lat, lon, radius_km):
        calls.append((lat, lon, radius_km))
        return states

    async def run():
        app = App(cfg, env={})
        try:
            app.adsb = FakeAdsb()
            app.adsblol.fetch = fetch
            await app.poll_once(time.time())
            (lat0, lon0, r), = calls
            home, anna = app.panels["home"], app.panels["anna"]
            for p in (home, anna):
                assert haversine_km(lat0, lon0, p.cfg.home_lat, p.cfg.home_lon) + p.cfg.fetch_radius_km <= r + 1e-9
            assert home.model.landing_course == anna.model.landing_course
            assert [a["callsign"] for a in home.model.arrivals] == [a["callsign"] for a in anna.model.arrivals]
            assert "XYZ9SK" in [a["callsign"] for a in home.model.arrivals]
            assert "a1" in home.stats.day["aircraft"] and "a1" not in anna.stats.day["aircraft"]
            assert "b1" in anna.stats.day["aircraft"] and "b1" not in home.stats.day["aircraft"]
            assert app.issues.items["unknown_operator:XYZ"]["seen"] == 1          # einmal je Abruf, nicht je Panel
            assert app.counters["polls"] == 1 and app.last_model_ok > 0
        finally:
            await app.session.close()

    asyncio.run(run())


def test_panels_cli_changes_apply_without_a_restart(tmp_path):
    """panels.py schreibt neben dem laufenden Server; der übernimmt es beim nächsten Durchgang: neue Panels,
    geänderte Einstellungen (Album und Statistik bleiben), gelöschte Panels (ohne dass das Speichern scheitert).
    Ein gelöschtes und gleich wieder angelegtes Panel beginnt mit leerem Album."""
    cfg = make_cfg(mock=True, data_dir=str(tmp_path), metar=False, iss=False, logo_fetch=False, quiet_hours=(0, 0))

    async def run():
        app = App(cfg, env={})
        cli = Db(db_path(str(tmp_path)), put_aside=False)             # wie panels.py, neben dem laufenden Server
        try:
            t = time.time()
            await app.poll_once(t)
            home, stats = app.lan, app.lan.stats
            cli.panel_set_settings("home", {"VIEW_FROM": "90"})
            cli.panel_add("anna", settings={"HOME_LAT": "52.40", "HOME_LON": "14.05"})
            await app.poll_once(t + 20)
            assert app.lan is home and home.cfg.view_from == 90 and home.stats is stats
            anna = app.panels["anna"]
            assert anna.cfg.home_lon == 14.05 and anna.model.source == "mock"
            cli.panel_remove("anna")
            app.save()                                                # schreibt für anna nichts mehr: kein Fehler
            assert set(app.panels) == {"home"}
            cli.panel_add("anna")
            await app.poll_once(t + 40)
            assert app.panels["anna"] is not anna and app.panels["anna"].stats.dex == {}
        finally:
            cli.close()
            await app.session.close()

    asyncio.run(run())


def test_a_resting_panel_is_dark_and_outside_the_fetch(tmp_path):
    """home in der Ruhezeit, anna wach: Abgerufen wird nur für anna, home ist schwarz. Ruhen beide, ruft der Server
    nichts ab und sieht in einer Minute wieder nach."""
    panel_rows(tmp_path, anna={**ANNA, "QUIET_HOURS": "00:00-00:00"})
    cfg = make_cfg(data_dir=str(tmp_path), metar=False, iss=False, logo_fetch=False, quiet_hours=(0, 24 * 60),
                   route_adsbim=False, track_origin=False)
    calls = []

    async def fetch(lat, lon, radius_km):
        calls.append((lat, lon, radius_km))
        return []

    async def run():
        app = App(cfg, env={})
        try:
            app.adsb = FakeAdsb()
            app.adsblol.fetch = fetch
            await app.poll_once(time.time())
            anna = app.panels["anna"]
            assert calls == [(anna.cfg.home_lat, anna.cfg.home_lon, anna.cfg.fetch_radius_km)]
            assert app.lan.model.stale and app.lan.model.empty
            anna.cfg = replace(anna.cfg, quiet_hours=(0, 24 * 60))   # nun ruhen beide
            assert await app.poll_once(time.time()) == REST_S and len(calls) == 1
        finally:
            await app.session.close()

    asyncio.run(run())


def test_a_panel_too_far_from_the_airport_stays_dark_and_widens_nothing(tmp_path):
    """Ein weiteres Panel in München (BER ist der Flughafen; panels.py ließe das nicht zu): Es ruft nicht ab, ist
    schwarz, /issues und Log sagen warum; der Abruf ist der von home allein."""
    panel_rows(tmp_path, far={"HOME_LAT": "48.35", "HOME_LON": "11.79"})
    cfg = make_cfg(data_dir=str(tmp_path), metar=False, iss=False, logo_fetch=False, quiet_hours=(0, 0),
                   route_adsbim=False, track_origin=False)
    calls = []

    async def fetch(lat, lon, radius_km):
        calls.append((lat, lon, radius_km))
        return []

    async def run():
        app = App(cfg, env={})
        try:
            app.adsb = FakeAdsb()
            app.adsblol.fetch = fetch
            far = app.panels["far"]
            assert "PANEL_MAX_KM" in far.off and not far.active(time.time())
            assert "PANEL_MAX_KM" in app.issues.items["setting:far HOME"]["example"]["error"]
            await app.poll_once(time.time())
            home = app.lan.cfg
            assert calls == [(home.home_lat, home.home_lon, home.fetch_radius_km)]
            assert far.model.stale and app.health()["panels"]["far"]["off"] == far.off
        finally:
            await app.session.close()

    asyncio.run(run())


def test_only_the_lan_panels_esp32_counts_for_ok(tmp_path):
    """Ein Freund zieht den Stecker: kein Grund, den Container ungesund zu melden. Das ESP32 am LAN-Port schon."""
    panel_rows(tmp_path, anna=ANNA)
    cfg = make_cfg(mock=True, data_dir=str(tmp_path), metar=False, iss=False, logo_fetch=False, quiet_hours=(0, 0))

    async def run():
        app = App(cfg, env={})
        try:
            now = time.time()
            app.last_model_ok = now
            anna = app.panels["anna"]
            anna.frame_count, anna.last_frame_fetch = 5, now - 3600
            assert app.problems(now) == []
            app.lan.frame_count, app.lan.last_frame_fetch = 5, now - 3600
            assert any("ESP32" in p for p in app.problems(now))
            assert set(app.health()["panels"]) == {"home", "anna"}
        finally:
            await app.session.close()

    asyncio.run(run())
```

If a geometric assertion fails only because a test aircraft sits on a radius edge, move the aircraft (not the assertion) — the coordinates above were chosen with margin: a1 is 4 km from home and 46 km from anna, b1 is 1 km from anna and 46 km from home.

Run them: FAIL (`App` has no `poll_once`, `panels`).

- [ ] **Step 4: Rewrite `main.py` for many panels**

Module docstring:

```python
"""Einstiegspunkt: python -m flightpanel.main

App verbindet alles: Flugzeuge abrufen (adsb.lol, sonst OpenSky), einmal für alle Panels in einem Umkreis um alle
(panel.py), daraus je Panel das Display-Modell bauen (build.py), Statistik führen, Wetter und ISS nachladen,
Geändertes in die Datenbank schreiben (db.py: DATA_DIR/flightpanel.db). Was die Panels teilen (Quellen,
Zwischenspeicher, Logos, Wetter, ISS-Bahndaten, der Flughafen: airfield.py), hält App einmal. Die Panels und ihre
Einstellungen kommen aus der Datenbank, auch im laufenden Betrieb (panels.py). web.py liefert Bilder und Zustand
des Panels am LAN-Port aus, gerendert wird bei jedem Abruf (render.py). Logos und mitgelieferte Daten liegen in
DATA_DIR (Docker: ./data)."""
```

Imports: drop `CARDS`, `IdleScreen`, `DisplayModel`, `Renderer`, `Iss` (only `Orbit` stays from `.astro`); add `from .config import Config, panel_settings_from_env, too_far`, `from .panel import Panel, active_seconds, fetch_circle, union_bbox`; keep `Stats` (type of `absorb_legacy`). Constants: add after `ISS_EVERY_S`:

```python
REST_S = 60                 # ruhen alle Panels: so oft nachsehen, auch nach Änderungen aus panels.py
```

`dream_due`: docstring addition `cfg: das Panel am LAN-Port (App.save_loop); aufgeräumt wird in der Nacht dessen, dem der Server gehört. Es kostet Millisekunden und stört kein anderes Panel.` — the code stays.

Add before `class App`:

```python
def _seen(p: Panel) -> str:
    """Was ein Panel aus einem Abruf macht, für die Zeile im Log."""
    m = p.model
    shown = ", ".join(f"{f.callsign}#{f.seq}" if f.seq else f.callsign for f in m.flights) or "-"
    in_view = sum(1 for n in m.nearby if n["in_view"] != "no")
    return f"{p.id}: {len(m.nearby)} im Radius, {in_view} im Sichtfeld, angezeigt: {shown}"
```

`class App` — the methods not listed here (`_note_aside`, `_asides`, `_note_skipped`, `dream`, `_copy`, `_copy_time`, `issues_report`, `stop`) keep their bodies; every `self.cfg` in them that means a **server** setting becomes `self.server_cfg`:

```python
class App:
    """Muss in der laufenden Event-Loop entstehen (aiohttp-Session). Die Datenbankverbindung gehört dem Thread
    der Loop: Nichts, was in asyncio.to_thread läuft, darf sie benutzen."""

    def __init__(self, cfg: Config, env: Mapping[str, str] | None = None):
        """cfg: die Server-Einstellungen (Config.from_env). Die Panels bekommen dazu ihre Einstellungen aus der
        Datenbank. Das am LAN-Port (LAN_PANEL) legt der erste Start an, mit Standort und Sichtfeld aus einer
        älteren .env (env, sonst os.environ), einmalig."""
        self.server_cfg = cfg
        data = cfg.data_dir
        self.db = Db(db_path(data))
        # (Anlegen des LAN-Panels aus der alten .env und die Warnung: unverändert)
        self.session = aiohttp.ClientSession(headers={"User-Agent": USER_AGENT})
        self.issues = Issues(self.db)
        if self.db.aside:
            self._note_aside(self.db.aside)
        self.cache = Cache(self.db)
        self.names = load_airlines(data)

        # Quellen, für alle Panels gemeinsam
        # (adsblol, opensky, adsb, adsbim, origins, metar: unverändert)
        self.orbit = Orbit(self.db, self.session, self.issues) if cfg.iss else None

        # Anzeige, für alle Panels gemeinsam
        self.logos = make_logo_store(cfg, self.session, self.db)
        self.rare = load_rare(data, self.names)
        self.airfield = Airfield(self.db)

        # Zustand
        self.started = time.time()
        self.active_source = cfg.source         # woher der letzte Abruf kam
        self.adsblol_failing = False
        self.opensky_last_try = 0.0
        self.last_model_ok = 0.0                # letzter gelungener Abruf, für alle Panels
        self.counters = {"polls": 0, "ok": 0, "adsblol": 0, "opensky": 0}
        self.last_dream: dict | None = None     # was das letzte nächtliche Aufräumen getan hat
        self._dream_day: str | None = None
        self._stop = asyncio.Event()
        self._tasks: list[asyncio.Task] = []

        # Die Panels: das am LAN-Port und die mit panels add angelegten
        self.panels: dict[str, Panel] = {}
        self._sync_panels()

        # Alte JSON-Dateien einmalig übernehmen: Album und Statistik ins Panel am LAN-Port, die Landerichtung an
        # den Flughafen (Startwert des ersten Abrufs)
        taken = absorb_legacy(data, self.cache, self.issues, self.lan.stats, self.airfield, self.logos, self.orbit)
        # (Speichern und Umbenennen danach: unverändert)

    @property
    def lan(self) -> Panel:
        """Das Panel am LAN-Port (LAN_PANEL): /frame, die Vorschau, /stats und /idle auf :8080."""
        return self.panels[self.server_cfg.lan_panel]

    def _sync_panels(self) -> None:
        """Die Panels, wie sie in der Datenbank stehen. panels.py schreibt neben dem laufenden Server, und was es
        ändert, gilt so beim nächsten Durchgang der Abrufschleife (spätestens nach einer Minute), ohne Neustart:
        Neue Panels kommen dazu, geänderte bekommen ihre Einstellungen (Album und Statistik bleiben), gelöschte
        fallen weg. Ein gelöschtes und gleich wieder angelegtes Panel (anderes created) beginnt neu, statt das
        alte Album in die neue Zeile zu schreiben. Das Panel am LAN-Port bleibt immer: Fehlt seine Zeile (von Hand
        gelöscht), bedient der Server es mit dem, was er hat."""
        rows = {r["id"]: r for r in self.db.panel_list()}
        lan = self.server_cfg.lan_panel
        for pid in [pid for pid in self.panels if pid not in rows and pid != lan]:
            del self.panels[pid]
            log.info("Panel %s gelöscht", pid)
        for pid, row in rows.items():
            p = self.panels.get(pid)
            if p is not None and (p.created, p.updated) == (row["created"], row["updated"]):
                continue
            remote = pid != lan
            skipped: list[tuple[str, str]] = []
            cfg = Config.for_panel(self.server_cfg, row["settings"], skipped, remote=remote)
            if skipped:
                self._note_skipped(pid, skipped)
            off = too_far(self.server_cfg, cfg) if remote else None
            if off:
                self._note_off(pid, off)
            if p is None or p.created != row["created"]:
                self.panels[pid] = Panel(self, pid, cfg, row["created"], row["updated"], off)
                log.info("Panel %s: %d Einstellungen", pid, len(row["settings"]))
            else:
                p.configure(cfg, row["updated"], off)
                log.info("Panel %s: Einstellungen übernommen", pid)

    def _note_off(self, pid: str, why: str) -> None:
        """Ein weiteres Panel, dessen Fenster zu weit vom Flughafen liegt (config.too_far): Es ruft nicht ab, statt
        den Abruf für alle zu vergrößern. panels.py lässt das nicht zu; so kommt es nur, wenn PANEL_MAX_KM später
        kleiner wurde oder jemand die Datenbank von Hand geändert hat."""
        fix = f"python -m flightpanel.panels set {pid} HOME_LAT=… HOME_LON=…"
        log.warning("Panel %s ruft nicht ab: %s. Beheben: %s, oder PANEL_MAX_KM in .env erhöhen", pid, why, fix)
        self.issues.note("setting", f"{pid} HOME", error=why, fix=fix)

    # ------------------------------------------------------------ Abrufen --
    def poll_interval(self) -> int:
        """Abstand zwischen zwei Abrufen. adsb.lol hat kein Tageslimit: POLL_INTERVAL,
        auch wenn es gerade ausfällt (OpenSky springt dann seltener ein, siehe
        fetch_states). Nur mit SOURCE=opensky gilt dessen Budget."""
        cfg = self.server_cfg
        if cfg.mock or cfg.source == "adsblol":
            return cfg.poll_interval
        return self.opensky_interval()

    def refresh_period(self) -> int:
        """Wie oft das Modell tatsächlich neu wird: bei ausgefallenem adsb.lol so
        selten, wie OpenSky einspringen darf. Für Vorschau und Stale-Schwellen."""
        if self.server_cfg.source == "adsblol" and self.adsblol_failing:
            return self.opensky_interval()
        return self.poll_interval()

    def opensky_interval(self) -> int:
        """Mindestabstand zwischen OpenSky-Abrufen: nie kürzer als POLL_INTERVAL, aber so lang, dass das
        Tagesbudget für alle Stunden reicht, in denen ein Panel abruft (außerhalb seiner Ruhezeit; anonym: 400
        Credits -> rund alle 2,5 min). Wird es trotzdem knapp, doppelt so lang."""
        budget = self.opensky.daily_credits
        need = active_seconds([p.cfg for p in self.panels.values() if p.off is None]) / (budget * 0.95)
        interval = max(self.server_cfg.poll_interval, int(need + 0.999))
        rem = self.opensky.credits_remaining
        if rem is not None and rem < budget * 0.1:
            interval *= 2
        return interval

    async def fetch_states(self, cfgs: list[Config]) -> list[State]:
        """Die Flugzeuge für diese Panels (cfgs): adsb.lol, wenn gewählt, in einem Umkreis um alle (fetch_circle).
        Fällt es aus, springt OpenSky ein (eine Box um alle, union_bbox), aber nur so oft, wie dessen Tagesbudget
        erlaubt; adsb.lol wird weiter bei jedem Abruf probiert, ein kurzer Aussetzer kostet also nur einen Abruf."""
        if self.server_cfg.source == "adsblol":
            try:
                states = await self.adsblol.fetch(*fetch_circle(cfgs))
                self.active_source = "adsblol"
                self.adsblol_failing = False
                return states
            except Exception as e:
                # (unverändert: adsblol_failing, source_error, Wartezeit, Warnung, fallback)
                ...
        self.opensky_last_try = time.time()
        self.active_source = "opensky"
        return await self.opensky.fetch(union_bbox(cfgs))

    async def poll_loop(self) -> None:
        while not self._stop.is_set():
            await asyncio.sleep(await self.poll_once(time.time()))

    async def poll_once(self, t0: float) -> float:
        """Ein Durchgang der Abrufschleife: Panels nachziehen (panels.py), die ruhenden schwarz, für die anderen
        ein Abruf. Gibt zurück, wie lange bis zum nächsten."""
        try:
            self._sync_panels()
        except Exception as e:              # etwa Datenbank gesperrt: mit den bisherigen Panels weiter
            self._note_poll_error(e)
        active = [p for p in self.panels.values() if p.active(t0)]
        for p in self.panels.values():
            if p not in active:
                p.rest()
        if not active:                      # alle in der Ruhezeit (oder aus): nichts abrufen
            return REST_S
        self.counters["polls"] += 1
        try:
            await self._poll(t0, active)
            self.last_model_ok = t0
            self.counters["ok"] += 1
        except Exception as e:
            self._note_poll_error(e)
            self._age_models(active)
        return max(5, self.poll_interval() - (time.time() - t0))

    async def _poll(self, t0: float, active: list[Panel] | None = None) -> None:
        """Ein Abruf für die Panels, die gerade nicht ruhen (ohne Angabe: alle solchen): Flugzeuge einmal holen,
        je Panel das Modell bauen, einmal den Flughafen nachführen, je Panel die Statistik."""
        if active is None:
            active = [p for p in self.panels.values() if p.active(t0)]
        if not active:
            return
        if self.server_cfg.mock:
            for p in active:
                p.model = mock_model(p.cfg, seed=int(t0 // 20))
            self.airfield.observe(active[0].model, t0)
            for p in active:
                p.stats.observe([], p.model, t0)
        else:
            states = await self.fetch_states([p.cfg for p in active])
            noted: set[str] = set()         # jede Auffälligkeit einmal je Abruf, nicht je Panel
            for p in active:
                p.model = await build_model(p.cfg, states, self.adsb, self.names, horizon_s=self.refresh_period(),
                                            prev_course=self.airfield.landing_course, issues=self.issues,
                                            adsbim=self.adsbim, origins=self.origins, source=self.active_source,
                                            recent=self.airfield.recent, rare=self.rare, noted=noted)
            self.airfield.observe(active[0].model, t0)     # Landefolge, Abflüge, Landerichtung: in jedem gleich
            self.counters[self.active_source] += 1
            for p in active:
                p.model.last_airport_move = self.airfield.last_move
                await self._observe(p, states, t0)
            log.info("%s: %d empfangen. %s. Nächster Abruf in %d s", self.active_source, len(states),
                     "; ".join(_seen(p) for p in active), self.poll_interval())
        for p in active:
            for f in p.model.flights:
                if f.kind == "airline":
                    self.logos.ensure(f.airline_iata, f.airline_icao)   # holt fehlende Logos im Hintergrund

    def _age_models(self, panels: list[Panel]) -> None:
        """Ohne frische Daten erst dimmen, dann leeren, in jedem Panel, das gerade abruft. Die Schwellen sind nie
        kürzer als ein paar Abrufintervalle, sonst dimmt das Bild bei langsamem (anonymem) Abruf nach jedem
        Fehlversuch. In /issues einmal, nicht je Panel."""
        age = time.time() - self.last_model_ok
        period = self.refresh_period()
        cfg = self.server_cfg
        if age > max(cfg.empty_after, 4 * period):
            if any(not p.model.empty for p in panels):
                self.issues.note("stale", "empty", age_s=round(age))
            for p in panels:
                p.model = replace(p.model, flights=[], empty=True, stale=True)
        elif age > max(cfg.stale_after, 2 * period):
            if any(not p.model.stale for p in panels):
                self.issues.note("stale", "dimmed", age_s=round(age))
            for p in panels:
                p.model = replace(p.model, stale=True)

    async def _observe(self, p: Panel, states: list[State], t0: float) -> None:
        """Statistik eines Panels nachführen, mit den Flugzeugen in seinem Abrufradius (der Abruf deckt alle Panels
        ab); neue Airlines bekommen ihr Logo, unbekannte Betreiber ihren Namen von adsbdb. Ein Fehler hier darf den
        Abruf nicht kosten."""
        try:
            mine = [s for s in states if p.covers(s)]
            for code in p.stats.observe(mine, p.model, t0):
                if code in self.names:
                    self.logos.ensure(self.names[code]["iata"], code)
                else:
                    cs = next((s.callsign for s in mine if s.callsign.startswith(code)), "")
                    route = await self.adsb.route(cs) if cs else {}
                    name = route.get("airline", "") or await self.adsb.airline(code)
                    p.stats.set_name(code, short_airline(name, code, self.names))
        except Exception as e:
            self._note_poll_error(e)

    # _note_poll_error: unverändert (msg = error_text(e) aus Aufgabe 4)

    # ---------------------------------------------------- Wetter, ISS, Platte --
    async def metar_loop(self) -> None:
        """Alle 20 min das METAR, solange ein Panel abruft; ruhen alle, nur, solange noch keins da ist."""
        while not self._stop.is_set():
            now = time.time()
            if any(p.active(now) for p in self.panels.values()) or self.metar.current is None:
                try:
                    await self.metar.refresh()
                except Exception as e:
                    log.warning("METAR: %s", e)
            await asyncio.sleep(METAR_EVERY_S)

    async def astro_loop(self) -> None:
        """Bahndaten der ISS einmal am Tag für alle, die Überflüge je Panel stündlich (astro.Iss)."""
        while not self._stop.is_set():
            now = time.time()
            try:
                if self.orbit.needs_tle(now):
                    await self.orbit.refresh_tle()
                for p in list(self.panels.values()):
                    await p.iss.update_passes(now)
            except Exception as e:
                log.warning("ISS: %s", e)
            await asyncio.sleep(ISS_EVERY_S)

    async def save_loop(self) -> None:
        while not self._stop.is_set():
            await asyncio.sleep(SAVE_EVERY_S)
            try:
                if dream_due(self.lan.cfg, self._dream_day, time.time()):
                    self.dream(time.time())
                self.save()
            except Exception as e:
                log.warning("Speichern: %s", e)

    def save(self) -> None:
        """Geändertes in die Datenbank, als eine Transaktion. Vorher die Panels nachziehen: Ein mit panels remove
        gelöschtes Panel schriebe sonst in Zeilen, die es nicht mehr gibt, und die Transaktion scheiterte für alle."""
        self._sync_panels()
        with self.db.transaction():
            self.cache.save()
            self.issues.save()
            self.airfield.save()
            for p in self.panels.values():
                p.stats.save()

    # ------------------------------------------------------------- Zustand --
    def problems(self, now: float) -> list[str]:
        """Was gerade nicht stimmt, für GET /ok (Docker-Healthcheck, Uptime-Monitor); leer heißt alles gut. Alte
        Flugdaten zählen, solange ein Panel hell ist; ein ESP32, das nicht mehr abruft, nur am LAN-Port: Ein
        Freund, der den Stecker zieht, macht den Server nicht ungesund. In der Ruhezeit ist dunkel normal."""
        out = []
        if any(p.off is None and p.cfg.brightness(now) > 0 for p in self.panels.values()):
            age = now - (self.last_model_ok or self.started)
            if age > OK_STALE_S:
                out.append(f"keine frischen Flugdaten seit {age / 60:.0f} min")
        lan = self.lan
        if lan.cfg.brightness(now) > 0 and lan.frame_count and now - lan.last_frame_fetch > OK_ESP32_S:
            out.append(f"ESP32 holt seit {(now - lan.last_frame_fetch) / 60:.0f} min keine Bilder")
        out += [f"Fehler im Code: {it['key']}" for it in self.issues.items.values()
                if it["kind"] == "exception" and now - it["last"] < OK_EXCEPTION_S]
        out += [f"Datenbank beiseitegelegt, das Album startete leer: {name} (siehe /issues)"
                for name in self._asides()]
        return out

    def health(self) -> dict:
        """Für GET /health: Quellen, Logos, das Panel am LAN-Port (Modell, Anzeige, ESP32) und eine Zeile je Panel."""
        now = time.time()
        lan = self.lan
        cfg = lan.cfg
        esp_age = now - lan.last_frame_fetch if lan.last_frame_fetch else None
        return {
            # alle bisherigen Schlüssel, mit diesen Quellen:
            #   mock, layout, calm, display, brightness_now (lan.renderer), model_stale, flights, idle_card
            #   (lan.idle.last_pick), esp32 (lan.frame_count, esp_age): vom Panel am LAN-Port (cfg = lan.cfg);
            #   source, opensky, *_last_error, cache_entries, issues, dream, database, metar, logos: wie bisher;
            #   iss: aus self.orbit (usable, tle_fetched_age_h, last_error) und lan.iss.passes;
            # dazu:
            "panels": {pid: p.summary(now) for pid, p in self.panels.items()},
        }

    # ----------------------------------------------------------- Lebenslauf --
    async def start(self) -> web.AppRunner:
        cfg = self.server_cfg
        if not cfg.mock and cfg.source == "opensky" and self.opensky.anonymous:
            log.warning("Keine OpenSky-Zugangsdaten: anonymer Zugriff (400 Credits/Tag), "
                        "Abruf nur alle %d s. Mit OPENSKY_CLIENT_ID/SECRET alle %d s.",
                        self.poll_interval(), cfg.poll_interval)
        runner = web.AppRunner(make_web_app(self), access_log=None)
        await runner.setup()
        await web.TCPSite(runner, "0.0.0.0", cfg.http_port).start()
        loops = [self.poll_loop(), self.save_loop()]
        if self.metar and not cfg.mock:
            loops.append(self.metar_loop())
        if self.orbit and not cfg.mock:
            loops.append(self.astro_loop())
        self._tasks = [asyncio.create_task(c) for c in loops]
        for t in self._tasks:
            t.add_done_callback(self._task_ended)
        log.info("flightpanel läuft auf :%d (mock=%s, %dx%d), Panels: %s, am LAN-Port %s (layout=%s)",
                 cfg.http_port, cfg.mock, cfg.width, cfg.height, ", ".join(self.panels), cfg.lan_panel,
                 self.lan.cfg.layout)
        return runner
```

The `health()` body above is described, not spelled out, because it is today's dictionary with the sources named in the comment: write it out completely, keeping every existing key and its meaning, and add `"panels"`. `_task_ended` and `stop` stay as they are.

- [ ] **Step 5: `web.py` serves the LAN panel**

Docstring first lines: `HTTP: Bild für den ESP32, Vorschau im Browser, Zustand und Statistik. Bedient wird das Panel am LAN-Port (LAN_PANEL); /health, /ok und /issues gelten für den ganzen Server.` In `make_web_app(app: "App")` and `_Handlers` rename the attribute `self.panel` to `self.app`; every handler that shows a panel uses `p = self.app.lan`:

```python
    def _render(self, layout: str | None = None, card: str | None = None) -> Image.Image:
        p = self.app.lan
        r = (Renderer(replace(p.cfg, layout=layout), self.app.logos, idle=p.idle, rotation=p.renderer.rotation)
             if layout else p.renderer)
        return r.render(p.model, time.time(), card=card or None)

    async def frame(self, req: web.Request) -> web.Response:
        app, p = self.app, self.app.lan
        now = time.time()
        if p.last_frame_fetch and now - p.last_frame_fetch > ESP32_GAP_S:
            app.issues.note("esp32_gap", "frames", gap_s=round(now - p.last_frame_fetch))
        p.last_frame_fetch = now
        p.frame_count += 1
        body = p.renderer.frame_bytes(self._render(), p.renderer.brightness(now))
        return web.Response(body=body, content_type="application/octet-stream", headers={"Cache-Control": "no-store"})
```

`frame_png`: `self.app.lan.renderer.png(...)`; `index`: `p = self.app.lan` (uses `p.cfg.layout`, `p.idle.cards`); `display`: `self.app.lan.model.to_json()`; `ok`, `health`, `issues`, `issues_clear`: `self.app…`; `stats`: `_pretty({**self.app.lan.stats.report(), "course": self.app.airfield.report()})`; `idle`: `p = self.app.lan; return _pretty(p.idle.status(p.model, time.time()))`.

- [ ] **Step 6: `panels.py` says that no restart is needed**

- Module docstring, last line: `Der laufende Server übernimmt Änderungen spätestens nach einer Minute, ohne Neustart.`
- `_save`: `print(f"{p['id']}: {len(new)} Einstellungen gesetzt; der Server übernimmt sie spätestens nach einer Minute")`
- `cmd_add`: `print(f"{args.id} angelegt; der Server bedient es spätestens nach einer Minute. Standort: set {args.id} HOME_LAT=... HOME_LON=... VIEW_FROM=...")`
- `cmd_remove`: `print(f"{p['id']} gelöscht; der Server lässt es spätestens nach einer Minute fallen")`

- [ ] **Step 7: Adapt the existing tests**

Mechanical, in `test_app.py`, `test_web.py`, `test_dream.py`: what was the LAN panel's now lives on `app.lan`: `app.cfg` → `app.lan.cfg` (also `first.cfg`, `second.cfg`), `app.stats` → `app.lan.stats` (also `again.stats`), `app.model` → `app.lan.model`, `app.frame_count`/`app.last_frame_fetch` → `app.lan.…` (also `night.…`), `app._observe(states, t)` → `app._observe(app.lan, states, t)`. `test_the_lan_panel_is_the_one_named_in_the_config`: `app.stats.panel_id` → `app.lan.stats.panel_id`. `test_quiet_hours_drop_the_old_picture` (test_web.py) becomes:

```python
def test_quiet_hours_drop_the_old_picture(tmp_path):
    async def run():
        cfg = make_cfg(data_dir=str(tmp_path), metar=False, iss=False, logo_fetch=False,
                       quiet_hours=(0, 24 * 60))            # rund um die Uhr Ruhezeit
        app = App(cfg)
        try:
            p = app.lan
            p.model = mock_model(cfg, 3)
            p.model.flights = p.model.flights or [p.model.upcoming]
            assert not p.active(time.time())
            assert await app.poll_once(time.time()) == REST_S          # ruht: nichts abgerufen, Bild weg
            assert p.model.flights == [] and p.model.empty and p.model.stale
            day = App(replace(cfg, quiet_hours=(0, 0)))       # keine Ruhezeit
            assert day.lan.active(time.time())
            await day.session.close()
        finally:
            await app.session.close()

    asyncio.run(run())
```

(import `REST_S` from `flightpanel.main` there).

- [ ] **Step 8: Run the whole suite**

Expected: all green, ruff clean. Then grep that nothing still reaches for the old attributes: `grep -rn "app\.\(cfg\|stats\|model\|iss\|frame_count\|last_frame_fetch\)\b\|self\.cfg\b" server/flightpanel/main.py server/flightpanel/web.py server/tests` must find nothing in main.py/web.py, and in tests only `app.lan.…`.

- [ ] **Step 9: Commit**

```bash
git add server/flightpanel/panel.py server/tests/test_panel.py server/flightpanel/main.py server/flightpanel/web.py server/flightpanel/config.py server/flightpanel/panels.py server/tests/test_app.py server/tests/test_web.py server/tests/test_dream.py
git commit -m "Mehrere Panels mit einem Abruf; panels-Änderungen gelten ohne Neustart" -m "App hält, was alle teilen, und je Panel Einstellungen, Modell, Renderer, Leerlauf-Karten, Statistik und ISS-Überflüge (panel.py). Ein Abruf in einem Umkreis um alle wachen Panels; ruhende sind schwarz und vergrößern ihn nicht, ein weiteres Panel zu weit vom Flughafen ruft nicht ab. Panels kommen aus der Datenbank, bei jedem Durchgang und vor jedem Speichern nachgezogen. /ok zählt nur das ESP32 am LAN-Port, /health hat eine Zeile je Panel." -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 7: docs and final verification

**Files:**
- Modify: `README.md` (lines ~64, 132, 135, 206–234), `docs/bauanleitung.md` (~196)
- No new tests; verification only.

- [ ] **Step 1: Docs**

- README "Einrichten", end of the paragraph after the `panels set` examples: replace `sie gelten nach \`docker compose restart flightpanel\`` by `der laufende Server übernimmt sie spätestens nach einer Minute, ohne Neustart`.
- README "Endpunkte", `/health` row: add `je Panel eine Zeile unter \`panels\``; `/ok` row: `ESP32 holt seit 10 min nichts` → `das ESP32 am LAN-Port holt seit 10 min nichts`.
- README "Aufbau": add after `stats.py`: `  airfield.py    was am Flughafen geschieht, für alle Panels: Landerichtung, letzte Starts und Landungen` and `  panel.py       ein Panel (Einstellungen, Modell, Renderer, Statistik) und der gemeinsame Abrufumkreis`; `main.py` line: `Einstieg: App (ein Abruf für alle Panels), Abruf-, Wetter-, ISS- und Speicherschleife`; `airport.py` line: drop `, wer zuletzt landete oder startete`.
- `docs/bauanleitung.md` (~line 196): `… mit \`panels set home BRIGHTNESS_DAY=90 BRIGHTNESS_EVENING=35\` und einem Neustart des Servers (README …)` → `… mit \`panels set home BRIGHTNESS_DAY=90 BRIGHTNESS_EVENING=35\`; der Server übernimmt es spätestens nach einer Minute (README …)`.
- Grep for leftover restart hints: `grep -rn "restart flightpanel\|Neustart des Servers" README.md docs/*.md server/flightpanel` — only places unrelated to panel settings may remain.

- [ ] **Step 2: Full suite and ruff** — all green.

- [ ] **Step 3: Golden frames, byte-identical to `main`**

`tools/screenshots.py` renders every doc image from demo data; run it with the worktree (see its docstring for the exact invocation, e.g. in `flightpanel-ci-dev` with the repo mounted and `PYTHONPATH=/repo/server`), then `git status --short docs/images` must be empty.

Then render a fixed scenario matrix with `main`'s server code and with the worktree's, and compare bytes. Put the script outside the repo (the session scratchpad), mount both trees, and run it once per tree:

```python
"""Golden frames: dieselben Szenarien mit zwei Ständen des Servers rendern (Pfad als erstes Argument), PNGs nach
argv[2]. Vergleich danach mit cmp."""
import os
import sys

sys.path.insert(0, sys.argv[1])
from flightpanel.cards import CARDS  # noqa: E402
from flightpanel.config import Config  # noqa: E402
from flightpanel.idle import IdleScreen  # noqa: E402
from flightpanel.logos import LogoStore  # noqa: E402
from flightpanel.mock import mock_model  # noqa: E402
from flightpanel.render import Renderer  # noqa: E402

out = sys.argv[2]
os.makedirs(out, exist_ok=True)
logos = LogoStore(os.path.join(out, "nologos"), 32, fetch=False)
T0 = 1791654544.0                                       # fester Zeitpunkt (Oktober 2026, nachmittags)
for layout in ("A", "B", "C"):
    cfg = Config(layout=layout, quiet_hours=(0, 0))
    idle = IdleScreen(cfg, CARDS, logos=logos)
    r = Renderer(cfg, logos, idle=idle)
    for seed in range(12):
        model = mock_model(cfg, seed)
        for dt in (0, 5, 9, 13):
            img = r.render(model, T0 + seed * 60 + dt)
            img.save(os.path.join(out, f"{layout}-{seed:02d}-{dt:02d}.png"))
    for card in sorted(idle.cards):
        img = r.render(mock_model(cfg, 0), T0, card=card)
        img.save(os.path.join(out, f"{layout}-card-{card}.png"))
```

Run it with `git archive main server | tar -x -C <scratch>/main` as the first tree and `server` of the worktree as the second, inside `flightpanel-ci-dev` (fonts are there), with `TZ=Europe/Berlin`. Every PNG must be byte-identical (`cmp` over all files; report the count). If a file differs, find out why before going on; a difference in a runway card would mean the airfield move changed behavior.

- [ ] **Step 4: Mock container with a second panel (throwaway, never port 8080)**

```bash
docker build -t flightpanel-step2-smoke server
mkdir -p "<scratch>/smoke-data" && cp data/airlines.json data/airports.json data/rare.json "<scratch>/smoke-data/"
MSYS_NO_PATHCONV=1 docker run -d --name flightpanel-step2-smoke -p 18080:8080 --read-only --tmpfs /tmp:size=64m \
  --cap-drop ALL --security-opt no-new-privileges --user 1000:1000 \
  -e MOCK=1 -e DATA_DIR=/data -v "<scratch-windows-path>/smoke-data:/data" flightpanel-step2-smoke
docker exec flightpanel-step2-smoke python3 -m flightpanel.panels add anna
docker exec flightpanel-step2-smoke python3 -m flightpanel.panels set anna HOME_LAT=52.40 HOME_LON=14.05
```

Wait at most 90 s (bounded loop on `curl -s localhost:18080/health`), then check: `/health` → `panels` has `home` and `anna`, both `"model_stale": false` and `flights` ≥ 0, `"off": null`; `/ok` 200 (or only a reason that is expected in mock mode); `/frame` 12 292 bytes; `docker exec … python3 -m flightpanel.panels list` shows both; `docker exec … id -u` is 1000. Then `docker rm -f flightpanel-step2-smoke` and `docker rmi flightpanel-step2-smoke`, and delete the scratch data. Never touch the container named `flightpanel`.

- [ ] **Step 5: Commit the docs**

```bash
git add README.md docs/bauanleitung.md
git commit -m "Doku: Panel-Einstellungen gelten ohne Neustart, mehrere Panels im Aufbau" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```
