"""Display-Modell -> 128x32-Bild -> Frame-Bytes für den ESP32 / PNG für den Browser.

Layouts (LAYOUT in .env):
  C    Logo links (30x30), rechts drei Zeilen: Airline, Route + Typ, wechselnde Info
  A    ohne Logo, drei Zeilen über die ganze Breite (Airline + Callsign, Route, Info)
  B    zwei Flüge, je eine Zeile
  auto C wenn für den Flug ein Logo da ist, sonst A

Bei mehreren Flügen wechseln A und C nach jedem Durchlauf der Infozeilen
zum nächsten Flug (nächster zuerst). Sonst Leerlauf-Karten (idle.py, cards.py):
Uhr, nächster Flieger, Radar, Statistik, Wetter, ISS, Privatflieger, Witze ...
"""

import io
from dataclasses import replace
from datetime import datetime

from PIL import Image

from .aircraft import second_line, shown_elsewhere
from .cards import CARDS
from .colors import ARR_C, CLOCK, DEP_C, INFO, MUTED, OVF_C, dim
from .config import Config, in_hours
from .fonts import FONT_BIG, FONT_MID, FONT_SMALL
from .geo import compass
from .idle import IdleScreen
from .logos import LogoStore
from .model import ARR, DEP, DisplayModel, Flight

# Satte Farben, kein Weiß als Textfarbe (Strom, Blendung), siehe colors.py.
COLORS = {ARR: ARR_C, DEP: DEP_C, "OVF": OVF_C}
STALE_FACTOR = 0.4

PROTO_VERSION = 1

# Zeilenraster für 32 px Höhe
ROW1, ROW2, ROW3 = 0, 12, 23

# Himmelspunkte: ein Punkt pro Flug im Wechsel, der im Sichtfeld ist, von links nach
# rechts wie vom Fenster aus gesehen; der gezeigte leuchtet. Dann wird das Logo kleiner.
DOT, DOT_PITCH, MAX_DOTS = 3, 5, 6
DOT_DIM = 0.3
LOGO_WITH_DOTS = 26


_dim = dim


def status_glyph(f: Flight) -> str:
    return "↓" if f.status == ARR else "↑" if f.status == DEP else "→"


def alt_text(f: Flight) -> str:
    if f.alt_ft is None:
        return ""
    if f.alt_ft >= 18000:
        return f"FL{f.alt_ft // 100}"
    return f"{(f.alt_ft // 100) * 100}ft"


WEEKDAYS = ["Mo", "Di", "Mi", "Do", "Fr", "Sa", "So"]


def info_pages(f: Flight, hide_callsign: bool = False) -> list[str]:
    """Die Zeilen, die unten durchwechseln (max. ~15 Zeichen bei 94 px).
    Fehlende Daten lassen die Seite weg; das Rufzeichen auch, wenn es schon
    oben steht (nichts doppelt)."""
    pages: list[str] = []
    if f.alt_ft is not None:
        vr = "↑" if (f.vrate_fpm or 0) > 300 else "↓" if (f.vrate_fpm or 0) < -300 else ""
        spd = f" {f.speed_kt}kt" if f.speed_kt is not None else ""
        pages.append(f"{alt_text(f)}{vr}{spd}")
    trk = f" Kurs {compass(f.track)}" if f.track is not None else ""
    pages.append(f"{int(f.dist_km)}km {compass(f.bearing)}{trk}")
    if not hide_callsign:
        pages.append(f.callsign)
    return pages


class Renderer:
    def __init__(self, cfg: Config, logos: LogoStore, idle: IdleScreen | None = None):
        self.cfg = cfg
        self.logos = logos
        self._grid_cache: dict[int, Image.Image] = {}
        if idle is None:
            idle = IdleScreen(cfg, CARDS, logos=logos)
        if idle.providers.get("draw_flight") is None:
            idle.providers["draw_flight"] = self.draw_flight
        self.idle = idle

    # ---------------------------------------------------------------- API --
    def _pick(self, model: DisplayModel, now: float) -> tuple[Flight, int]:
        """Welcher Flug und welche Infoseite gerade dran ist."""
        tick = int(now // self.cfg.page_seconds)
        n_pages = 3
        idx = (tick // n_pages) % len(model.flights)
        return model.flights[idx], tick % n_pages

    def render(self, model: DisplayModel, now: float, card: str | None = None) -> Image.Image:
        """Notfall-Squawk vor allem; dann Airline-Flüge; sonst Leerlauf-Karten, zu denen
        auch Privatflieger, Hubschrauber und Militär im Sichtfeld gehören (ohne Vorrang).
        card erzwingt eine Leerlauf-Karte (Vorschau)."""
        W, H = self.cfg.width, self.cfg.height
        img = Image.new("RGB", (W, H), (0, 0, 0))
        f = STALE_FACTOR if model.stale else 1.0

        if card:
            self.idle.draw(img, model, now, f, force=card, preview=True)
            return img
        if model.alert and not model.stale and "alert" not in self.cfg.idle_disabled:
            self.idle.draw(img, model, now, f, force="alert")
            return img
        airline = [fl for fl in model.flights if fl.kind == "airline"]
        if model.empty or not airline:
            self.idle.draw(img, model, now, f)
            return img
        if len(airline) != len(model.flights):
            model = replace(model, flights=airline)

        if self.cfg.layout == "B":
            self._layout_b(img, model, now, f)
            return img
        fl, page = self._pick(model, now)
        self.draw_flight(img, model, fl, page, f)
        return img

    def draw_flight(self, img: Image.Image, model: DisplayModel, fl: Flight, page: int, f: float) -> None:
        """Ein Flug in Layout C (mit Logo) oder A; B zeigt einzelne Flüge wie auto."""
        logo = self.logos.get(fl.airline_iata, fl.airline_icao)
        dots = self._sky_dots(model, fl)
        layout = self.cfg.layout
        if layout in ("AUTO", "B"):
            layout = "C" if logo is not None else "A"
        if layout == "C" and logo is not None:
            self._layout_c(img, fl, page, logo, f, dots)
        else:
            self._layout_a(img, fl, page, f, dots)

    def _sky_dots(self, model: DisplayModel, fl: Flight) -> list[tuple[tuple[int, int, int], bool]]:
        """(Statusfarbe, ist der gezeigte Flug) für jeden Flug im Wechsel, der im
        Sichtfeld ist, im Uhrzeigersinn ab VIEW_FROM sortiert, also von links nach
        rechts, wenn man ins Sichtfeld schaut. Nur Flüge, die das Panel auch zeigt:
        Jeder Punkt leuchtet irgendwann. Leer bei weniger als zwei oder wenn der
        gezeigte Flug nicht im Sichtfeld ist."""
        seen = sorted((f for f in model.flights if f.visible), key=lambda f: (f.bearing - self.cfg.view_from) % 360)
        idx = next((i for i, f in enumerate(seen) if f is fl), None)
        if len(seen) < 2 or idx is None:
            return []
        lo = max(0, min(idx - MAX_DOTS // 2, len(seen) - MAX_DOTS))   # Fenster um den gezeigten
        return [(COLORS[f.status], f is fl) for f in seen[lo:lo + MAX_DOTS]]

    def _draw_dots(self, img: Image.Image, x: int, y: int, dots, f: float) -> None:
        for i, (col, lit) in enumerate(dots):
            c = _dim(col, f if lit else f * DOT_DIM)
            img.paste(c, (x + i * DOT_PITCH, y, x + i * DOT_PITCH + DOT, y + DOT))

    @staticmethod
    def _dots_width(dots) -> int:
        return len(dots) * DOT_PITCH - (DOT_PITCH - DOT) if dots else 0

    def frame_bytes(self, img: Image.Image, brightness: int) -> bytes:
        W, H = img.size
        return bytes([PROTO_VERSION, max(0, min(255, brightness)), W, H]) + img.tobytes()

    def png(self, img: Image.Image, scale: int = 6, gap: bool = True) -> bytes:
        """Vergrößerte Vorschau. gap=True zeichnet jede LED als Quadrat mit
        dunklem Rand, das kommt dem echten Panel näher."""
        W, H = img.size
        big = img.resize((W * scale, H * scale), Image.NEAREST)
        if gap and scale >= 3:
            grid = self._grid_cache.get(scale)
            if grid is None:
                tile = Image.new("L", (scale, scale), 255)
                px = tile.load()
                for i in range(scale):
                    px[i, 0] = px[0, i] = px[i, scale - 1] = px[scale - 1, i] = 0
                grid = Image.new("L", (W * scale, H * scale))
                for y in range(0, H * scale, scale):
                    for x in range(0, W * scale, scale):
                        grid.paste(tile, (x, y))
                self._grid_cache[scale] = grid
            dark = Image.new("RGB", big.size, (14, 14, 14))
            big = Image.composite(big, dark, grid)
        buf = io.BytesIO()
        big.save(buf, format="PNG")
        return buf.getvalue()

    # ------------------------------------------------------------ Layouts --
    def _layout_c(self, img: Image.Image, fl: Flight, page: int, logo: Image.Image, f: float,
                  dots=()) -> None:
        if f < 1.0:
            logo = Image.eval(logo, lambda v: int(v * f))
        col_w = logo.width                       # Logospalte, Text beginnt immer gleich weit rechts
        if dots:
            # Logo kleiner, darunter die Himmelspunkte
            logo = logo.resize((LOGO_WITH_DOTS, LOGO_WITH_DOTS), Image.LANCZOS)
            img.paste(logo, (1 + (col_w - logo.width) // 2, 0))
            self._draw_dots(img, 1 + (col_w - self._dots_width(dots)) // 2, img.height - DOT - 1, dots, f)
        else:
            img.paste(logo, (1, (img.height - logo.height) // 2))
        x0 = col_w + 4
        avail = img.width - x0
        col = _dim(COLORS[fl.status], f)

        # Zeile 1: Pfeil + Airline (oder Callsign, wenn kein Name bekannt)
        name = fl.airline or fl.callsign
        FONT_BIG.draw(img, x0, ROW1, FONT_BIG.fit(f"{status_glyph(fl)} {name}", avail), col)

        # Zeile 2: Route, sonst was der Flug ist, sonst ?→BER; rechtsbündig der Typ
        route = second_line(fl.origin, fl.dest, fl.status, fl.label, self.cfg.local_airport_iata)
        FONT_MID.draw(img, x0, ROW2, route, _dim(INFO, f))
        if fl.actype:
            tw = FONT_MID.text_width(fl.actype)
            if x0 + FONT_MID.text_width(route) + 8 + tw <= img.width:
                FONT_MID.draw(img, img.width - tw, ROW2, fl.actype, _dim(MUTED, f))

        # Zeile 3: wechselnde Info, ohne das Rufzeichen, wenn es oben schon steht
        pages = info_pages(fl, hide_callsign=shown_elsewhere(fl.callsign, name, route))
        FONT_MID.draw(img, x0, ROW3, FONT_MID.fit(pages[page % len(pages)], avail), _dim(INFO, f))

    def _layout_a(self, img: Image.Image, fl: Flight, page: int, f: float, dots=()) -> None:
        col = _dim(COLORS[fl.status], f)
        W = img.width
        # Zeile 1: Pfeil + Name links, Rufzeichen rechts, wenn beides passt und der Name
        # es nicht schon ist. Sonst geht der Name vor (Christoph 31 statt "Christop CHX31"),
        # und das Rufzeichen kommt unten in den Wechsel.
        name = fl.airline or ""
        head = f"{status_glyph(fl)} {name}".rstrip() if name else status_glyph(fl)
        cs = "" if shown_elsewhere(fl.callsign, name) else fl.callsign
        if cs and name and FONT_BIG.text_width(head) + 6 + FONT_BIG.text_width(cs) > W:
            cs = ""
        csw = FONT_BIG.text_width(cs) if cs else 0
        left = FONT_BIG.fit(head, W - (csw + 6 if cs else 0))
        FONT_BIG.draw(img, 0, ROW1, left, col)
        if cs:
            FONT_BIG.draw(img, W - csw, ROW1, cs, col)

        # Zeile 2: Route, sonst was der Flug ist, sonst ?→BER; Typ rechts
        route = second_line(fl.origin, fl.dest, fl.status, fl.label, self.cfg.local_airport_iata)
        FONT_MID.draw(img, 0, ROW2, route, _dim(INFO, f))
        if fl.actype:
            tw = FONT_MID.text_width(fl.actype)
            FONT_MID.draw(img, W - tw, ROW2, fl.actype, _dim(MUTED, f))

        # Zeile 3: wechselnde Info, rechts die Himmelspunkte, wenn Platz ist; ohne
        # Rufzeichen, wenn es schon oben steht
        pages = info_pages(fl, hide_callsign=bool(cs) or shown_elsewhere(fl.callsign, name, route))
        dw = self._dots_width(dots)
        info = FONT_MID.fit(pages[page % len(pages)], W - (dw + 6 if dots else 0))
        FONT_MID.draw(img, 0, ROW3, info, _dim(INFO, f))
        if dots:
            self._draw_dots(img, W - dw, ROW3 + 3, dots, f)

    def _layout_b(self, img: Image.Image, model: DisplayModel, now: float, f: float) -> None:
        rows = [3, 19]
        W = img.width
        for i, y in enumerate(rows):
            if i >= len(model.flights):
                if i == 1:
                    txt = datetime.fromtimestamp(now).strftime("%H:%M")
                    FONT_SMALL.draw(img, W - FONT_SMALL.text_width(txt), y, txt, _dim(CLOCK, f))
                continue
            fl = model.flights[i]
            col = _dim(COLORS[fl.status], f)
            name = fl.airline or fl.callsign
            x = FONT_SMALL.draw(img, 0, y, f"{status_glyph(fl)} ", col)
            x = FONT_SMALL.draw(img, x, y, FONT_SMALL.fit(name, 60), col)
            right = f"{fl.other_airport[:3]} {alt_text(fl)}".strip()
            if fl.actype and FONT_SMALL.text_width(right + " " + fl.actype) <= W - x - 6:
                right = f"{right} {fl.actype}"
            while right and FONT_SMALL.text_width(right) > W - x - 4:
                right = right[:-1]
            FONT_SMALL.draw(img, W - FONT_SMALL.text_width(right), y, right, _dim(INFO, f))


def brightness_for(cfg: Config, now: float) -> int:
    """Helligkeit nach Uhrzeit. Quiet Hours -> 0 (Panel schwarz)."""
    t = datetime.fromtimestamp(now)
    minute = t.hour * 60 + t.minute
    if in_hours(cfg.quiet_hours, minute):
        return 0
    if in_hours(cfg.day_hours, minute):
        return cfg.brightness_day
    return cfg.brightness_evening


def active_seconds_per_day(cfg: Config) -> int:
    """Sekunden pro Tag außerhalb der Quiet Hours (in denen nicht gepollt wird)."""
    a, b = cfg.quiet_hours
    quiet = (b - a) % (24 * 60)
    return (24 * 60 - quiet) * 60
