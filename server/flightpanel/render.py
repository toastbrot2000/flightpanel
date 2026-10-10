"""Display-Modell -> 128x32-Bild -> Frame-Bytes für den ESP32 / PNG für den Browser.

Was gezeigt wird (render): Notfall-Squawk vor allem, dann Airline-Flüge, sonst
Leerlauf-Karten (idle.py, cards/), zu denen auch Privatflieger, Hubschrauber und
Militär im Sichtfeld gehören.

Layouts für Flüge (LAYOUT in .env):
  C    Logo links (32 x 32), rechts drei Zeilen: Airline, Route + Typ, wechselnde Info
  A    ohne Logo, drei Zeilen über die ganze Breite (Airline + Callsign, Route, Info)
  B    zwei Flüge, je eine Zeile
  AUTO C, wenn für den Flug ein Logo da ist, sonst A

Bei mehreren Flügen wechseln A und C nach jedem Durchlauf der Infozeilen zum
nächsten Flug, Landende in Landefolge zuerst. Sind mindestens zwei im Sichtfeld,
zeigen Himmelspunkte, welcher gerade dran ist."""

import io
from dataclasses import replace
from datetime import datetime

from PIL import Image

from .aircraft import second_line, shown_elsewhere
from .cards import CARDS
from .colors import CLOCK, INFO, MUTED, dim
from .config import INFO_PAGES, Config
from .draw import ROW1, ROW2, ROW3, STATUS_COLORS, line, right, status_glyph, text
from .fonts import FONT_BIG, FONT_MID, FONT_SMALL
from .geo import kompass
from .idle import IdleScreen
from .logos import LogoStore
from .model import DisplayModel, Flight

PROTO_VERSION = 1       # Byte 0 von /frame, muss zur Firmware passen
STALE_FACTOR = 0.4      # so hell bei alten Daten

# Himmelspunkte: ein Punkt pro Flug im Wechsel, der im Sichtfeld ist, in Lesereihenfolge:
# links landet zuerst, dann die Landefolge, danach der Rest; der gezeigte leuchtet.
# Dann wird das Logo kleiner.
DOT, DOT_PITCH, MAX_DOTS = 3, 5, 6
DOT_DIM = 0.3
LOGO_COL = 32           # Logospalte, Logo bündig in der Ecke
LOGO_WITH_DOTS = 28     # mit Himmelspunkten darunter (3 px Punkte, 1 px Abstand)


def alt_text(f: Flight) -> str:
    if f.alt_ft is None:
        return ""
    if f.alt_ft >= 18000:
        return f"FL{f.alt_ft // 100}"
    return f"{(f.alt_ft // 100) * 100}ft"


def info_pages(f: Flight, hide_callsign: bool = False, which: tuple[str, ...] = INFO_PAGES) -> list[str]:
    """Die Zeilen, die unten durchwechseln (max. ~15 Zeichen bei 94 px), in der
    Reihenfolge von which (INFO_PAGES in .env): eta "landet in ~7 min", alt Höhe und
    Tempo, dist Entfernung, Richtung und Kurs, callsign. Fehlende Daten lassen die
    Seite weg; das Rufzeichen auch, wenn es schon oben steht (nichts doppelt)."""
    pages: list[str] = []
    for page in which:
        if page == "eta" and f.land_eta_s is not None:
            pages.append("landet gleich" if f.land_eta_s < 60
                         else f"landet in ~{max(1, round(f.land_eta_s / 60))} min")
        elif page == "alt" and f.alt_ft is not None:
            vr = "↑" if (f.vrate_fpm or 0) > 300 else "↓" if (f.vrate_fpm or 0) < -300 else ""
            spd = f" {f.speed_kt}kt" if f.speed_kt is not None else ""
            pages.append(f"{alt_text(f)}{vr}{spd}")
        elif page == "dist":
            trk = f" Kurs {kompass(f.track)}" if f.track is not None else ""
            pages.append(f"{int(f.dist_km)}km {kompass(f.bearing)}{trk}")
        elif page == "callsign" and not hide_callsign:
            pages.append(f.callsign)
    return pages or [""]


def type_width(fl: Flight, line2: str, avail: int) -> int:
    """Platz für den Typ rechts in Zeile 2, oder 0, wenn er nicht neben die Zeile passt."""
    if not fl.actype:
        return 0
    tw = FONT_MID.text_width(fl.actype) + 6
    return tw if FONT_MID.text_width(line2) + tw <= avail else 0


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
    def render(self, model: DisplayModel, now: float, card: str | None = None) -> Image.Image:
        """Notfall-Squawk vor allem; dann Airline-Flüge; sonst Leerlauf-Karten.
        card erzwingt eine Leerlauf-Karte (Vorschau)."""
        img = Image.new("RGB", (self.cfg.width, self.cfg.height), (0, 0, 0))
        f = STALE_FACTOR if model.stale else 1.0

        if card:
            self.idle.draw(img, model, now, f, force=card, preview=True)
            return img
        if model.alert and not model.stale and self.idle.enabled("alert"):
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
        """Ein Flug in Layout C (mit Logo) oder A; B zeigt einzelne Flüge wie AUTO."""
        logo = self.logos.get(fl.airline_iata, fl.airline_icao)
        dots = self._sky_dots(model, fl)
        layout = self.cfg.layout
        if layout in ("AUTO", "B"):
            layout = "C" if logo is not None else "A"
        if layout == "C" and logo is not None:
            self._layout_c(img, fl, page, logo, f, dots)
        else:
            self._layout_a(img, fl, page, f, dots)

    def frame_bytes(self, img: Image.Image, brightness: int) -> bytes:
        """Für den ESP32: [Version, Helligkeit, Breite, Höhe] + RGB, zeilenweise."""
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

    # ------------------------------------------------------------ Auswahl --
    def _pick(self, model: DisplayModel, now: float) -> tuple[Flight, int]:
        """Welcher Flug und welche Infoseite gerade dran ist: Jeder Flug zeigt jede
        Infoseite einmal (die Landezeit nur, wenn einer landet), mindestens aber
        FLIGHT_SECONDS lang."""
        which = self.cfg.info_pages
        landing = any(fl.land_eta_s is not None for fl in model.flights)
        n_pages = max(1, len(which) - ("eta" in which and not landing))
        ps = self.cfg.page_seconds
        per_flight = max(n_pages * ps, self.cfg.flight_seconds)
        idx = int(now // per_flight) % len(model.flights)
        return model.flights[idx], int(now % per_flight // ps) % n_pages

    def _sky_dots(self, model: DisplayModel, fl: Flight) -> list[tuple[tuple[int, int, int], bool]]:
        """(Statusfarbe, ist der gezeigte Flug) für jeden Flug im Wechsel, der im
        Sichtfeld ist. Gelesen wird von links nach rechts: links der, der zuerst
        landet, dann die Landefolge; Starts und Überflüge danach, nach Richtung im
        Sichtfeld. (Vom Fenster aus gesehen landet oft der rechteste zuerst; nach
        dieser Geometrie zu sortieren war nicht intuitiv.) Nur Flüge, die das Panel
        auch zeigt: Jeder Punkt leuchtet irgendwann. Leer bei weniger als zwei oder
        wenn der gezeigte Flug nicht im Sichtfeld ist."""
        seen = sorted((f for f in model.flights if f.visible),
                      key=lambda f: (f.seq is None, f.seq or 0, (f.bearing - self.cfg.view_from) % 360))
        idx = next((i for i, f in enumerate(seen) if f is fl), None)
        if len(seen) < 2 or idx is None:
            return []
        lo = max(0, min(idx - MAX_DOTS // 2, len(seen) - MAX_DOTS))   # Fenster um den gezeigten
        return [(STATUS_COLORS[f.status], f is fl) for f in seen[lo:lo + MAX_DOTS]]

    @staticmethod
    def _draw_dots(img: Image.Image, x: int, y: int, dots, f: float) -> None:
        for i, (col, lit) in enumerate(dots):
            c = dim(col, f if lit else f * DOT_DIM)
            img.paste(c, (x + i * DOT_PITCH, y, x + i * DOT_PITCH + DOT, y + DOT))

    @staticmethod
    def _dots_width(dots) -> int:
        return len(dots) * DOT_PITCH - (DOT_PITCH - DOT) if dots else 0

    # ------------------------------------------------------------ Layouts --
    def _layout_c(self, img: Image.Image, fl: Flight, page: int, logo: Image.Image, f: float,
                  dots=()) -> None:
        if f < 1.0:
            logo = Image.eval(logo, lambda v: int(v * f))
        if logo.size != (LOGO_COL, LOGO_COL):
            logo = logo.resize((LOGO_COL, LOGO_COL), Image.LANCZOS)
        if dots:
            # Logo kleiner, darunter die Himmelspunkte auf der letzten Zeile
            logo = logo.resize((LOGO_WITH_DOTS, LOGO_WITH_DOTS), Image.LANCZOS)
            img.paste(logo, ((LOGO_COL - logo.width) // 2, 0))
            self._draw_dots(img, (LOGO_COL - self._dots_width(dots)) // 2, img.height - DOT, dots, f)
        else:
            img.paste(logo, (0, 0))
        x0 = LOGO_COL + 2               # Text beginnt immer gleich weit rechts
        avail = img.width - x0

        # Zeile 1: Pfeil + Airline (oder Callsign, wenn kein Name bekannt)
        name = fl.airline or fl.callsign
        text(img, FONT_BIG, x0, ROW1, f"{status_glyph(fl.status)} {name}", STATUS_COLORS[fl.status], f, avail)

        # Zeile 2: Route, sonst was der Flug ist, sonst ?→BER; rechtsbündig der Typ
        route = second_line(fl.origin, fl.dest, fl.status, fl.label, self.cfg.local_airport_iata)
        tw = type_width(fl, route, avail)
        line(img, x0, ROW2, route, INFO, f, avail - tw)
        if tw:
            right(img, FONT_MID, ROW2, fl.actype, MUTED, f)

        # Zeile 3: wechselnde Info, ohne das Rufzeichen, wenn es oben schon steht
        pages = info_pages(fl, shown_elsewhere(fl.callsign, name, route), self.cfg.info_pages)
        line(img, x0, ROW3, pages[page % len(pages)], INFO, f, avail)

    def _layout_a(self, img: Image.Image, fl: Flight, page: int, f: float, dots=()) -> None:
        W = img.width
        # Zeile 1: Pfeil + Name links, Rufzeichen rechts, wenn beides passt und der Name
        # es nicht schon ist. Sonst geht der Name vor (Christoph 31 statt "Christop CHX31"),
        # und das Rufzeichen kommt unten in den Wechsel.
        name = fl.airline or ""
        glyph = status_glyph(fl.status)
        head = f"{glyph} {name}".rstrip() if name else glyph
        cs = "" if shown_elsewhere(fl.callsign, name) else fl.callsign
        if cs and name and FONT_BIG.text_width(head) + 6 + FONT_BIG.text_width(cs) > W:
            cs = ""
        csw = FONT_BIG.text_width(cs) if cs else 0
        col = STATUS_COLORS[fl.status]
        text(img, FONT_BIG, 0, ROW1, head, col, f, W - (csw + 6 if cs else 0))
        if cs:
            right(img, FONT_BIG, ROW1, cs, col, f)

        # Zeile 2: Route, sonst was der Flug ist, sonst ?→BER; Typ rechts
        route = second_line(fl.origin, fl.dest, fl.status, fl.label, self.cfg.local_airport_iata)
        tw = type_width(fl, route, W)
        line(img, 0, ROW2, route, INFO, f, W - tw)
        if tw:
            right(img, FONT_MID, ROW2, fl.actype, MUTED, f)

        # Zeile 3: wechselnde Info, rechts die Himmelspunkte, wenn Platz ist; ohne
        # Rufzeichen, wenn es schon oben steht
        pages = info_pages(fl, bool(cs) or shown_elsewhere(fl.callsign, name, route), self.cfg.info_pages)
        dw = self._dots_width(dots)
        line(img, 0, ROW3, pages[page % len(pages)], INFO, f, W - (dw + 6 if dots else 0))
        if dots:
            self._draw_dots(img, W - dw, img.height - DOT, dots, f)

    def _layout_b(self, img: Image.Image, model: DisplayModel, now: float, f: float) -> None:
        W = img.width
        for i, y in enumerate((3, 19)):
            if i >= len(model.flights):
                if i == 1:
                    txt = datetime.fromtimestamp(now).strftime("%H:%M")
                    FONT_SMALL.draw(img, W - FONT_SMALL.text_width(txt), y, txt, dim(CLOCK, f))
                continue
            fl = model.flights[i]
            col = dim(STATUS_COLORS[fl.status], f)
            name = fl.airline or fl.callsign
            x = FONT_SMALL.draw(img, 0, y, f"{status_glyph(fl.status)} ", col)
            x = FONT_SMALL.draw(img, x, y, FONT_SMALL.fit(name, 60), col)
            info = f"{fl.other_airport[:3]} {alt_text(fl)}".strip()
            if fl.actype and FONT_SMALL.text_width(info + " " + fl.actype) <= W - x - 6:
                info = f"{info} {fl.actype}"
            while info and FONT_SMALL.text_width(info) > W - x - 4:
                info = info[:-1]
            FONT_SMALL.draw(img, W - FONT_SMALL.text_width(info), y, info, dim(INFO, f))
