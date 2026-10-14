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
