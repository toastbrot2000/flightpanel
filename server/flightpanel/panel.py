"""Ein Panel: ein Fenster mit seinem LED-Panel. Der Server bedient mehrere mit einem Abruf (main.App).

Gemeinsam haben alle Panels Routen, Logos und die Bahndaten der ISS. Die Flugdaten teilen sich die Panels einer
Region (regions: derselbe Flughafen, oder nah genug beieinander), ein Abruf je Region in einem Umkreis um ihre
Fenster (fetch_circle, union_bbox); das Wetter und was am Flughafen geschieht (airfield.py) gelten je Flughafen
(App.metar_for, App.airfield_for). Jedes Panel hat seine Einstellungen (Config.for_panel aus der Datenbank, gesetzt
mit panels.py), sein Display-Modell, seinen Renderer und seine Leerlauf-Karten, seine Statistik und sein Album
(stats.py) und die Satelliten-Überflüge über seinem Fenster (astro.Tracker, je eingeschalteter Karte einer).
Das Panel am LAN-Port (LAN_PANEL) ist eines davon; weitere legt panels add an.

Ein Panel ruht in seiner Ruhezeit (QUIET_HOURS) und wenn es aus ist: Ein weiteres Panel, dessen Fenster weiter als
PANEL_MAX_KM von seinem Flughafen liegt (config.too_far), ruft nicht ab, statt den Abruf für alle zu vergrößern.

Sein ESP32 holt das Bild über web.py (/panel/frame): Panel.fetched zählt die Abrufe, meldet Lücken in /issues und
merkt sich die Diagnose, die es dabei schickt (vier Zahlen, web.parse_diag). Firmware bekommt es aus seinem Kanal
(channel: stable oder canary, DATA_DIR/firmware/<Kanal>/)."""

from dataclasses import replace
from typing import TYPE_CHECKING

from .astro import Tracker, TrainTracker
from .cards import CARDS
from .cards.sky import SATS
from .config import FW_CHANNELS, Config, in_hours
from .geo import haversine_km
from .idle import IdleScreen
from .model import DisplayModel, State
from .render import Renderer
from .stats import Stats

if TYPE_CHECKING:
    from .main import App

MAX_FETCH_KM = 250 * 1.852      # adsb.lol liefert höchstens 250 nm um einen Punkt
REGION_MERGE_KM = 150           # Panels verschiedener Flughäfen teilen sich einen Abruf, wenn er so klein bleibt
DAY_MIN = 24 * 60
ESP32_GAP_S = 30                # so lange ohne Abruf des Bildes: eine Lücke in /issues (esp32_gap)


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


def regions(cfgs: list[Config]) -> list[list[Config]]:
    """Wer sich einen Abruf teilt: Panels mit demselben region_key (Flughafen, sonst Fenster) immer; verschiedene
    Gruppen werden zusammengelegt, solange der gemeinsame Abruf (fetch_circle) höchstens REGION_MERGE_KM weit
    reicht. So bleibt Berlin ein Abruf, ein Panel 500 km weiter ein eigener, und nie zieht einer einen Kreis über
    beides. Reihenfolge wie cfgs."""
    groups: dict[str, list[Config]] = {}
    for c in cfgs:
        groups.setdefault(c.region_key, []).append(c)
    out: list[list[Config]] = []
    for g in groups.values():
        for o in out:
            if fetch_circle(o + g)[2] <= REGION_MERGE_KM:
                o.extend(g)
                break
        else:
            out.append(list(g))
    return out


def active_seconds(cfgs: list[Config]) -> int:
    """Sekunden pro Tag, in denen mindestens eines dieser Panels abruft (außerhalb seiner Ruhezeit): So lange muss
    das OpenSky-Budget reichen. Minute für Minute gezählt, Ruhezeiten über Mitternacht gehen auch."""
    return 60 * sum(1 for minute in range(DAY_MIN) if any(not in_hours(c.quiet_hours, minute) for c in cfgs))


def fw_channel(stored) -> str:
    """Der Firmware-Kanal aus der Datenbank (Spalte fw_channel): stable oder canary. Alles andere (von Hand
    geändert) gilt als stable; als Teil eines Pfades (DATA_DIR/firmware/<Kanal>/) kommt nur einer der beiden vor."""
    return stored if stored in FW_CHANNELS else "stable"


class Panel:
    def __init__(self, app: "App", pid: str, cfg: Config, created: float = 0.0, updated: float = 0.0,
                 off: str | None = None, channel: str = "stable"):
        """app: was alle Panels teilen (db, names, rare, logos, catalog) und je Flughafen (metar_for, airfield_for).
        created und updated: die Zeitstempel seiner Zeile in der Datenbank, an denen main.App Änderungen erkennt.
        off: warum es nicht abruft (config.too_far), sonst None. channel: sein Firmware-Kanal (fw_channel)."""
        self.app = app
        self.id = pid
        self.created = created
        self.stats = Stats(app.db, pid, cfg, app.names, app.rare)
        self.sats: dict[str, Tracker] = {}      # je eingeschalteter Satelliten-Karte (configure)
        self.iss: Tracker | None = None
        self.airfield = app.airfield_for(cfg.local_airport_iata)
        self.metar = app.metar_for(cfg)
        self.last_ok = 0.0              # letzter gelungener Abruf seiner Region (main.App._poll, _age_models)
        self.last_traffic = 0.0         # zuletzt etwas am Himmel; ohne Flughafen die "Bewegung" für idle.Ctx.quiet
        self.model = DisplayModel(empty=True, source="startup",
                                  landing_course=self.airfield.landing_course if self.airfield else None)
        self.renderer: Renderer | None = None
        self.last_frame_fetch = 0.0     # setzt fetched() bei jedem Abruf des Bildes
        self.frame_count = 0
        self.diag: dict[str, int] = {}  # was sein ESP32 beim letzten Abruf meldete: nur Zahlen (web.parse_diag)
        self.configure(cfg, updated, off, channel)

    def configure(self, cfg: Config, updated: float = 0.0, off: str | None = None, channel: str = "stable") -> None:
        """Einstellungen anwenden: beim Start und wenn panels.py sie geändert hat. Album und Statistik, das letzte
        Modell, die Abrufe des ESP32 und die Seite, die gerade dran ist, bleiben; Leerlauf-Karten und Renderer
        entstehen neu, die ISS-Überflüge rechnet der nächste Lauf von main.App.astro_loop neu."""
        self.cfg, self.updated, self.off = cfg, updated, off
        self.channel = fw_channel(channel)
        self.stats.cfg = cfg
        app = self.app
        if app.catalog is not None:         # Tracker nur für eingeschaltete Karten: Rechenzeit nur, wo es sie gibt
            self.sats = {cid: self.sats.get(cid) or (Tracker(cfg, app.catalog.orbit_for(sat.norad)) if sat.norad
                                                     else TrainTracker(cfg, app.catalog))
                         for cid, sat in SATS.items() if cid not in cfg.cards_off}
            for tr in self.sats.values():
                tr.cfg, tr.passes_at = cfg, 0
        self.iss = self.sats.get("iss")
        self.airfield = app.airfield_for(cfg.local_airport_iata)    # der Flughafen kann gewechselt haben
        self.metar = app.metar_for(cfg)
        self.idle = IdleScreen(cfg, CARDS, stats=self.stats, metar=self.metar, astro=self.sats, logos=app.logos,
                               names=app.names, airfield=self.airfield)
        self.renderer = Renderer(cfg, app.logos, idle=self.idle,
                                 rotation=self.renderer.rotation if self.renderer is not None else None)

    async def update_passes(self, now: float) -> None:
        """Die Überflüge jedes Satelliten über diesem Fenster (main.App.astro_loop)."""
        for tr in self.sats.values():
            await tr.update_passes(now)

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

    def fetched(self, now: float, diag: dict[str, int]) -> None:
        """Sein ESP32 hat das Bild abgerufen (web.py, /panel/frame; ein 304 zählt auch). Eine Lücke über ESP32_GAP_S
        kommt nach /issues, beim Panel am LAN-Port wie bisher unter "frames", sonst unter "frames <id>". diag: was es
        dabei meldete (web.parse_diag); es gilt, was der letzte Abruf schickte."""
        if self.last_frame_fetch and now - self.last_frame_fetch > ESP32_GAP_S:
            key = "frames" if self.id == self.cfg.lan_panel else f"frames {self.id}"
            self.app.issues.note("esp32_gap", key, gap_s=round(now - self.last_frame_fetch))
        self.last_frame_fetch = now
        self.frame_count += 1
        self.diag = diag

    def summary(self, now: float) -> dict:
        """Für /health: was es zeigt, ob es aus ist, wann sein ESP32 zuletzt da war und was es dabei meldete."""
        age = now - self.last_frame_fetch if self.last_frame_fetch else None
        return {"flights": len(self.model.flights), "model_stale": self.model.stale,
                "brightness_now": self.renderer.brightness(now), "off": self.off,
                "airport": self.cfg.local_airport_iata or "-",     # nie region_key: ohne Flughafen wäre das die Lage
                "esp32": {"last_fetch_age_s": round(age) if age is not None else None, "fetches": self.frame_count,
                          "alive": age is not None and age < 300, "diag": dict(self.diag)}}
