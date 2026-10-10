"""Display-Modell -> 128x32-Bild -> Frame-Bytes für den ESP32 / PNG für den Browser.

Layouts (LAYOUT in .env):
  C    Logo links (30x30), rechts drei Zeilen: Airline, Route + Typ, wechselnde Info
  A    ohne Logo, drei Zeilen über die ganze Breite (Airline + Callsign, Route, Info)
  B    zwei Flüge, je eine Zeile
  auto C wenn für den Flug ein Logo da ist, sonst A

Bei mehreren Flügen wechseln A und C nach jedem Durchlauf der Infozeilen
zum nächsten Flug (nächster zuerst). Leer: Uhr.
"""

import io
from datetime import datetime

from PIL import Image

from .config import Config
from .fonts import FONT_BIG, FONT_CLOCK, FONT_MID, FONT_SMALL
from .geo import compass
from .logos import LogoStore
from .model import ARR, DEP, DisplayModel, Flight

# Satte Farben, kein Weiß als Textfarbe (Strom, Blendung).
COLORS = {
    ARR: (255, 120, 0),
    DEP: (0, 210, 70),
    "OVF": (70, 120, 230),
}
INFO = (120, 150, 230)
MUTED = (150, 150, 150)
CLOCK = (140, 70, 0)
STALE_FACTOR = 0.4

PROTO_VERSION = 1

# Zeilenraster für 32 px Höhe
ROW1, ROW2, ROW3 = 0, 12, 23

# Himmelspunkte: ein Punkt pro Flugzeug im Sichtfeld, von links nach rechts wie vom
# Fenster aus gesehen; der gezeigte Flug leuchtet. Dann wird das Logo kleiner.
DOT, DOT_PITCH, MAX_DOTS = 3, 5, 6
DOT_DIM = 0.3
LOGO_WITH_DOTS = 26


def _dim(c: tuple[int, int, int], f: float) -> tuple[int, int, int]:
    return tuple(max(0, min(255, int(v * f))) for v in c)


def status_glyph(f: Flight) -> str:
    return "↓" if f.status == ARR else "↑" if f.status == DEP else "→"


def alt_text(f: Flight) -> str:
    if f.alt_ft is None:
        return ""
    if f.alt_ft >= 18000:
        return f"FL{f.alt_ft // 100}"
    return f"{(f.alt_ft // 100) * 100}ft"


def route_text(f: Flight) -> str:
    if not (f.origin or f.dest):
        return ""
    return f"{f.origin or '---'}→{f.dest or '---'}"


WEEKDAYS = ["Mo", "Di", "Mi", "Do", "Fr", "Sa", "So"]


def info_pages(f: Flight) -> list[str]:
    """Die Zeilen, die unten durchwechseln (max. ~15 Zeichen bei 94 px).
    Fehlende Daten lassen die Seite weg."""
    pages: list[str] = []
    if f.alt_ft is not None:
        vr = "↑" if (f.vrate_fpm or 0) > 300 else "↓" if (f.vrate_fpm or 0) < -300 else ""
        spd = f" {f.speed_kt}kt" if f.speed_kt is not None else ""
        pages.append(f"{alt_text(f)}{vr}{spd}")
    trk = f" Kurs {compass(f.track)}" if f.track is not None else ""
    pages.append(f"{int(f.dist_km)}km {compass(f.bearing)}{trk}")
    pages.append(f.callsign)
    return pages


class Renderer:
    def __init__(self, cfg: Config, logos: LogoStore):
        self.cfg = cfg
        self.logos = logos
        self._grid_cache: dict[int, Image.Image] = {}

    # ---------------------------------------------------------------- API --
    def _pick(self, model: DisplayModel, now: float) -> tuple[Flight, int]:
        """Welcher Flug und welche Infoseite gerade dran ist."""
        tick = int(now // self.cfg.page_seconds)
        n_pages = 3
        idx = (tick // n_pages) % len(model.flights)
        return model.flights[idx], tick % n_pages

    def render(self, model: DisplayModel, now: float) -> Image.Image:
        W, H = self.cfg.width, self.cfg.height
        img = Image.new("RGB", (W, H), (0, 0, 0))
        f = STALE_FACTOR if model.stale else 1.0

        if model.empty or not model.flights:
            self._clock(img, now, f)
            return img

        layout = self.cfg.layout
        if layout == "B":
            self._layout_b(img, model, now, f)
            return img

        fl, page = self._pick(model, now)
        logo = self.logos.get(fl.airline_iata, fl.airline_icao)
        dots = self._sky_dots(model, fl)
        if layout == "AUTO":
            layout = "C" if logo is not None else "A"
        if layout == "C" and logo is not None:
            self._layout_c(img, fl, page, logo, f, dots)
        else:
            self._layout_a(img, fl, page, f, dots)
        return img

    def _sky_dots(self, model: DisplayModel, fl: Flight) -> list[tuple[tuple[int, int, int], bool]]:
        """(Farbe, ist der gezeigte Flug) für jedes Flugzeug im Sichtfeld, sortiert
        im Uhrzeigersinn ab VIEW_FROM, also von links nach rechts, wenn man ins
        Sichtfeld schaut. Landende amber, der Rest grau. Leer, wenn höchstens eins
        zu sehen ist oder der gezeigte Flug nicht dabei ist."""
        seen = [n for n in model.nearby if n.get("in_view") in ("now", "soon")]
        seen.sort(key=lambda n: (n["az"] - self.cfg.view_from) % 360)
        idx = next((i for i, n in enumerate(seen) if n.get("icao24") == fl.icao24), None)
        if len(seen) < 2 or idx is None:
            return []
        lo = max(0, min(idx - MAX_DOTS // 2, len(seen) - MAX_DOTS))   # Fenster um den gezeigten
        return [(COLORS[fl.status] if n.get("icao24") == fl.icao24 else COLORS[ARR] if n.get("seq") else MUTED,
                 n.get("icao24") == fl.icao24) for n in seen[lo:lo + MAX_DOTS]]

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
    def _clock(self, img: Image.Image, now: float, f: float) -> None:
        t = datetime.fromtimestamp(now)
        txt = t.strftime("%H:%M")
        w = FONT_CLOCK.text_width(txt)
        FONT_CLOCK.draw(img, (img.width - w) // 2, 3, txt, _dim(CLOCK, f))
        date = f"{WEEKDAYS[t.weekday()]} {t.strftime('%d.%m.')}"
        w2 = FONT_SMALL.text_width(date)
        FONT_SMALL.draw(img, (img.width - w2) // 2, 22, date, _dim(MUTED, 0.6 * f))

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

        # Zeile 2: Route, rechtsbündig der Typ
        route = route_text(fl) or fl.callsign
        FONT_MID.draw(img, x0, ROW2, route, _dim(INFO, f))
        if fl.actype:
            tw = FONT_MID.text_width(fl.actype)
            if x0 + FONT_MID.text_width(route) + 8 + tw <= img.width:
                FONT_MID.draw(img, img.width - tw, ROW2, fl.actype, _dim(MUTED, f))

        # Zeile 3: wechselnde Info
        pages = info_pages(fl)
        FONT_MID.draw(img, x0, ROW3, FONT_MID.fit(pages[page % len(pages)], avail), _dim(INFO, f))

    def _layout_a(self, img: Image.Image, fl: Flight, page: int, f: float, dots=()) -> None:
        col = _dim(COLORS[fl.status], f)
        W = img.width
        # Zeile 1: Pfeil + Airline links, Callsign rechts
        name = fl.airline or ""
        cs = fl.callsign
        csw = FONT_BIG.text_width(cs)
        left = FONT_BIG.fit(f"{status_glyph(fl)} {name}".rstrip(), W - csw - 6) if name else status_glyph(fl)
        FONT_BIG.draw(img, 0, ROW1, left, col)
        FONT_BIG.draw(img, W - csw, ROW1, cs, col)

        # Zeile 2: Route links, Typ rechts
        route = route_text(fl) or "Route unbekannt"
        FONT_MID.draw(img, 0, ROW2, route, _dim(INFO, f))
        if fl.actype:
            tw = FONT_MID.text_width(fl.actype)
            FONT_MID.draw(img, W - tw, ROW2, fl.actype, _dim(MUTED, f))

        # Zeile 3: wechselnde Info, rechts die Himmelspunkte, wenn Platz ist
        pages = info_pages(fl)
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

    def inside(rng: tuple[int, int]) -> bool:
        a, b = rng
        return a <= minute < b if a <= b else (minute >= a or minute < b)

    if inside(cfg.quiet_hours):
        return 0
    if inside(cfg.day_hours):
        return cfg.brightness_day
    return cfg.brightness_evening


def active_seconds_per_day(cfg: Config) -> int:
    """Sekunden pro Tag außerhalb der Quiet Hours (in denen nicht gepollt wird)."""
    a, b = cfg.quiet_hours
    quiet = (b - a) % (24 * 60)
    return (24 * 60 - quiet) * 60
