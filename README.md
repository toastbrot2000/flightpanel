# flightpanel

LED-Anzeige fürs Wohnzimmer, die das Flugzeug in der Nähe zeigt, mit Airline-Logo (Vorbild: The Flight Wall). 2 × Waveshare HUB75 P2.5 64 × 32 nebeneinander = 128 × 32 Pixel auf 32 × 8 cm, ESP32, Server im Homelab. Der komplette Bauplan liegt in `docs/flugpanel-bauplan.md`.

```
OpenSky ──┐
adsbdb ───┼─> server (Python, Container) ──HTTP /frame──> ESP32 ──HUB75──> 2 Panels
Logo-CDN ─┘    pollt, cachet, holt Logos,   12 KB alle 2 s     DMA-Refresh
               rendert 128x32
```

Der Server rendert das komplette Bild. Der ESP32 holt es ab und schreibt es in die Panels; er kennt weder Flüge noch Fonts noch Logos. Jede Layoutänderung passiert in Python, Vorschau im Browser, kein Flashen.

## Schnellstart (ohne Hardware, ohne API-Zugang)

```bash
cp .env.example .env            # MOCK=1 ist schon gesetzt
docker compose up -d --build
open http://localhost:8080      # Vorschau, wechselt alle 2 s
docker compose exec flightpanel python3 -m flightpanel.fetch_logos   # Logos für ~110 Airlines holen
```

Oder ohne Docker (braucht DejaVu-Fonts, `fonts-dejavu-core`):

```bash
cd server && pip install -r requirements.txt
cd .. && MOCK=1 DATA_DIR=./data PYTHONPATH=server python3 -m flightpanel.main
```

Die Vorschauseite hat Links für die Layouts. `/frame.png?layout=C&scale=8&gap=0` geht auch direkt.

## Endpoints

| Pfad | Zweck |
|---|---|
| `/` | Vorschauseite |
| `/frame` | Binär für den ESP32: `[1, helligkeit, 128, 32]` + 12 288 Byte RGB |
| `/frame.png` | Vorschau, Parameter `layout`, `scale`, `gap` |
| `/display.json` | Display-Modell (Flüge, Status, Route, Höhe, Airline-Codes …) |
| `/health` | OpenSky-Credits, Cache, vorhandene/fehlende Logos, letzter ESP32-Abruf. `esp32.alive=false` heißt: Panel hängt. |

## Layouts

Drei Zeilen à 11 px, DejaVu Sans als harte Pixelschrift, Pfeile als Pixelmuster.

- **C** Logo links (30 × 30), rechts: Pfeil + Airline, Route + Typ, wechselnde Infozeile (Höhe/Speed, Entfernung/Kurs, Callsign)
- **A** ohne Logo, volle Breite, Callsign rechts oben
- **B** zwei Flüge, je eine Zeile
- **auto** C, wenn für die Airline ein Logo liegt, sonst A

Bei zwei Flügen im Modell wechseln A und C alle 12 s zum nächsten Flug. Leer: Uhr und Datum. Farben: Ankunft amber, Abflug grün, Überflug blau, kein Weiß als Textfarbe.

## Airline-Logos

Der Server holt fehlende Logos selbst: adsbdb liefert zum Callsign den IATA-Code der Airline (`U2`, `LH`, `FR`), der Server lädt `https://pics.avs.io/200/200/<IATA>.png`, entfernt den weißen Hintergrund, schneidet zu, skaliert auf 30 × 30 und legt `data/logos/<IATA>.png` ab. Fehlschläge stehen in `/health` → `logos_missing` und werden nach 7 Tagen erneut probiert.

- Alles auf einmal: `python3 -m flightpanel.fetch_logos` (alle Airlines aus `data/airlines.json`), oder gezielt `… fetch_logos LH U2 FR`, `--force` überschreibt.
- Handgemachte Logos haben Vorrang: eine 30 × 30-PNG als `data/logos/<IATA>.png` oder `<ICAO>.png` ablegen. Neue Dateien werden im Betrieb erkannt.
- `tools/make_logo.py bild.svg LH` macht aus beliebigen Bildern (PNG/JPG/SVG) das passende Format, mit Vorschau. `--bg "#ff6600"` legt eine Markenfarbe hinter das Logo, `--crop 10` schneidet Rand ab.
- `LOGO_FETCH=0` schaltet den Abruf ab, `LOGO_URL` tauscht die Quelle (`{code}` wird ersetzt).

`data/airlines.json` bildet ICAO-Codes auf kurze Anzeigenamen und IATA-Codes ab (adsbdb liefert sonst "Deutsche Lufthansa AG" und manchmal keinen IATA-Code). Rund 110 Einträge: alles, was in Berlin regelmäßig fliegt, plus große Überflieger und Frachter.

## Echte Daten

1. OpenSky-Account anlegen, unter *API Clients* einen Client erzeugen.
2. In `.env`: `MOCK=0`, `OPENSKY_CLIENT_ID`, `OPENSKY_CLIENT_SECRET`, `HOME_LAT`, `HOME_LON`.
3. `docker compose up -d --build`, dann `/health` ansehen: `credits_remaining` sollte um 4000 liegen, bei 30 s Intervall reicht das für den Tag. Unter 500 Credits schaltet der Server selbst auf 60 s.
4. Ein paar Tage `/display.json` beobachten und `RADIUS_KM` / `MAX_ALT_M` einstellen.

Routen und Flugzeugtypen kommen von adsbdb.com (kostenlos, ohne Account) und werden 24 h bzw. 30 Tage in `data/cache.json` gehalten.

## Firmware

PlatformIO, Board `esp32dev` (NodeMCU ESP32, WROOM-32), Bibliothek ESP32-HUB75-MatrixPanel-DMA 3.0.14.

```bash
cd firmware
cp include/secrets.h.example include/secrets.h   # WLAN (2,4 GHz!), Server-URL, OTA-Passwort
pio run -t upload                                # erstes Mal per USB, VIN-Pin dabei abstecken
pio device monitor
pio run -e esp32dev_ota -t upload                # danach per WLAN
```

Verdrahtung ESP32 → HUB75 (Standard der Bibliothek, Tabelle auch im Bauplan 4.3):

| HUB75 | GPIO | HUB75 | GPIO |
|---|---|---|---|
| R1 | 25 | A | 23 |
| G1 | 26 | B | 19 |
| B1 | 27 | C | 5 |
| R2 | 14 | D | 17 |
| G2 | 12 | E | – |
| B2 | 13 | LAT | 4 |
| CLK | 16 | OE | 15 |

Beim Boot zeigt die Firmware ein Testbild: linkes Panel rot, rechtes grün (Kettenreihenfolge), dann Rahmen plus Diagonale (Geometrie), dann Schriftzug (Orientierung). Danach holt sie alle 2 s `/frame`. Schalter in `include/config.h`: `SWAP_HALVES`, `HUB75_CLKPHASE`, `HUB75_LATCH_BLANKING`, `MAX_BRIGHTNESS 160`. Ohne Server-Kontakt bleibt das letzte Bild stehen und nach 60 s leuchtet ein dunkelroter Punkt unten rechts, nach 10 min startet der ESP32 neu.

Die Firmware ist gegen die Bibliotheksquelle 3.0.14 geschrieben, aber in der Entwicklungsumgebung nicht kompiliert worden (keine Registry). Der erste `pio run` ist der Test.

## Struktur

```
server/flightpanel/   config, geo, fonts, opensky, adsbdb, model, logos, fetch_logos, render, main
firmware/             platformio.ini, src/main.cpp, include/config.h, include/secrets.h.example
tools/make_logo.py    Bild -> data/logos/<CODE>.png
data/                 airlines.json, logos/, cache.json (Volume)
docs/                 Bauplan
```
