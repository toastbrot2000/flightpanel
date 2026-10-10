# flightpanel

LED-Anzeige fürs Wohnzimmer: zeigt das Flugzeug, das man gerade vor dem Fenster sieht, mit Airline-Logo, Route, Typ, Höhe und Minuten bis zur Landung (Vorbild: The Flight Wall). Zwei Waveshare-HUB75-Panels P2.5 (64 × 32) nebeneinander ergeben 128 × 32 Pixel auf 32 × 8 cm. Ein ESP32 steuert sie an, ein kleiner Server im Homelab macht den Rest. Ist gerade kein Flugzeug zu sehen, wechseln Karten: Abflüge und Landungen am Flughafen, der nächste Flieger, Radar, Wetter, ISS, Statistik und ein paar Spielereien.

Hardware, Mechanik und Stromversorgung: [docs/flugpanel-bauplan.md](docs/flugpanel-bauplan.md). Alle Bildschirme und ihre Rangfolge: [docs/screens.md](docs/screens.md).

```
adsb.lol (Ersatz: OpenSky) ─┐
adsb.im, adsbdb (Routen) ───┤
OpenSky-Flugspur (Herkunft) ┼─> Server (Python, Docker) ──HTTP /frame──> ESP32 ──HUB75──> 2 Panels
METAR, Celestrak (ISS) ─────┤   ruft alle 20 s ab, rendert    12 KB alle 2 s     DMA-Refresh
Logo-CDNs ──────────────────┘   128 x 32 bei jedem Abruf
```

Der Server rendert das komplette Bild. Der ESP32 holt es nur ab und schreibt es in die Panels; er kennt weder Flüge noch Schriften noch Logos. Jede Änderung am Aussehen passiert in Python, mit Vorschau im Browser und ohne Flashen.

## Schnellstart (ohne Hardware, ohne Netz)

```bash
cp .env.example .env            # MOCK=1 ist schon gesetzt: Fantasieflüge
docker compose up -d --build
open http://localhost:8080      # Vorschau, wechselt alle 2 s
```

Die Vorschauseite hat Links für die Layouts und jede Leerlauf-Karte; `/frame.png?card=radar&scale=8` geht auch direkt.

## Einrichten

Alles Ortsabhängige steht in `.env` (nicht im Repo; jede Einstellung ist in [.env.example](.env.example) erklärt). Für echte Daten: `MOCK=0`, dann vier Dinge.

**1. Standort.** `HOME_LAT`, `HOME_LON` (Dezimalgrad), `HOME_ALT_M` (Augenhöhe am Fenster über dem Meer). Angezeigt wird, was höchstens `RADIUS_KM` weit weg und `MAX_ALT_M` hoch ist.

**2. Sichtfeld.** Was man vom Fenster aus sieht: Richtung im Uhrzeigersinn von `VIEW_FROM` bis `VIEW_TO` (0 = Nord, 90 = Ost), Mindesthöhe über dem Horizont `VIEW_MIN_ELEV` (Häuser, Bäume), Obergrenze `VIEW_MAX_ELEV` (Balkon darüber), Sichtweite `VIEW_MAX_KM`. `VIEW_ONLY=1` zeigt nur, was im Sichtfeld ist. Eichen: am Fenster ein Flugzeug suchen und in `/display.json` → `nearby` dessen Richtung (`az`) und Höhenwinkel (`elev`) ablesen.

**3. Heimatflughafen.** `LOCAL_AIRPORT_IATA` genügt: Position und METAR-Station kommen aus `data/airports.json`. Dazu `RUNWAY_COURSE`, der Kurs im Endanflug einer Landerichtung (BER 25: 249°, MUC 26: 263°), und `CURFEW_HOURS` für das Nachtflugverbot. Die Berlin-Karten (Tegel, BER-Eröffnung) und die Witze in `data/jokes.json` gelten nur am BER; für einen anderen Flughafen eine eigene `jokes.json` schreiben.

**4. Karten und Ruhe.** Jede Leerlauf-Karte lässt sich abschalten: `CARD_<ID>=0`, z. B. `CARD_JOKE=0`. `CALM=1` ist der ruhige Modus: nichts blinkt außer einem Notfall, Infoseiten wechseln alle 10 s statt 4 s, unten stehen nur Landezeit und Höhe/Tempo. Jede dieser Einzelheiten ist auch einzeln einstellbar (`PAGE_SECONDS`, `FLIGHT_SECONDS`, `INFO_PAGES`, `BLINK`, `IDLE_SECONDS`).

Dann `docker compose up -d --build` und einmal die Logos holen: `docker compose exec flightpanel python3 -m flightpanel.fetch_logos`.

## Was angezeigt wird

Bei jedem Abruf ordnet der Server alle Flugzeuge im Radius:

1. **Sichtfeld zuerst.** Zählt auch, wer bis zum nächsten Abruf hineinfliegt (aus Tempo, Kurs und Steigrate weitergerechnet), sonst wären Anflüge schon durch, bevor sie auf dem Panel stehen.
2. **Airlines vor dem Rest.** Linien-, Charter- und Frachtflüge zählen als Airline. Privatflieger, Hubschrauber, Polizei und Militär (`"airline": false` in `data/airlines.json`) kommen nur dran, wenn keine Airline zu sehen ist, dann als Leerlauf-Karte; `SHOW_OTHER=0` blendet sie aus.
3. **Landefolge, dann kürzeste Sichtlinie.** Wer als nächstes landet, kommt zuerst. Die Landefolge schätzt der Server aus der Reststrecke bis zur Piste: im Endanflug die Entfernung, im Gegenanflug weiter bis zum Eindrehen und den ganzen Endanflug zurück, im Queranflug quer zur Achse plus Rest. Die Landerichtung erkennt er am Verkehr in Platznähe.

Bis zu `MAX_FLIGHTS` Flüge wechseln sich ab, jeder mit allen Infoseiten, mindestens `FLIGHT_SECONDS`.

**Layouts** (`LAYOUT`): **C** Logo links (32 × 32), rechts drei Zeilen; **A** ohne Logo über die ganze Breite; **B** zwei Flüge, je eine Zeile; **auto** C mit Logo, sonst A. Zeile 1: Pfeil (↓ Landung, ↑ Start, → Überflug) und Airline. Zeile 2: Route, sonst was der Flug ist (Luftrettung, Polizei, Kleinflugzeug: aus `airlines.json`, ADS-B-Kategorie oder Kennzeichen), sonst `?→BER` oder `BER→?`; rechts der Typ. Zeile 3 wechselt (`INFO_PAGES`): "landet in ~7 min", Höhe und Tempo, Entfernung und Kurs, Rufzeichen. Farben: Landung amber, Start grün, Überflug blau, kein Weiß.

**Himmelspunkte:** Sind mindestens zwei gezeigte Flugzeuge im Sichtfeld, steht unter dem (dann 28 × 28 kleinen) Logo eine Punktreihe, ein Punkt pro Flug in Lesereihenfolge: links der, der zuerst landet. Der Punkt des gezeigten Flugs leuchtet.

**Grundsätze:** Nichts steht doppelt (das Rufzeichen fällt aus dem Wechsel, wenn es oben schon steht). Text wird nie so gekürzt, dass sich der Sinn ändert: erst kleinere Schrift, dann eine kürzere Fassung. Alle Pixel werden genutzt; Text am Rand sitzt auf der ersten oder letzten Spalte. Privatflieger heißen nach ihrem Kennzeichen (D-EHGB), Rettungshubschrauber nach ihrer Nummer (Christoph 31); Halternamen kommen nie aufs Panel.

**Leerlauf:** Ohne Airline im Sichtfeld wechseln Karten alle `IDLE_SECONDS` in festen Stufen: Verkehr am Platz (Abflüge, Landungen, nächster Flieger, Privatflieger) vor Informationen (Radar, Landerichtung, Wetter, Statistik, ISS, Sonne) vor Spielereien, die nur kommen, wenn am Flughafen seit 15 min nichts los war. Ein Notfall-Squawk (7700, 7600, 7500) geht allem vor. Liste und Regeln: [docs/screens.md](docs/screens.md).

## Datenquellen

| Was | Quelle | Bemerkung |
|---|---|---|
| Flugzeuge | [adsb.lol](https://adsb.lol) (`SOURCE=adsblol`) | Community-Netz aus ADS-B-Empfängern, ohne Account, alle `POLL_INTERVAL` Sekunden. Liefert auch Typ und ADS-B-Kategorie (Hubschrauber, Leichtflugzeug). Daten unter ODbL. |
| Ersatz | [OpenSky](https://opensky-network.org) | springt ein, wenn adsb.lol ausfällt, so oft das Tagesbudget erlaubt (anonym 400 Credits, mit Account 4000: `OPENSKY_CLIENT_ID`/`_SECRET`). `SOURCE=opensky` nimmt nur OpenSky. |
| Routen | adsb.im, dann adsbdb.com | pro Callsign gespeichert und oft alt; liegt das Flugzeug weit neben der Strecke, verwirft der Server sie. Fliegt eine Airline hin und zurück unter einem Callsign (BER-NCE-BER), zählt die Teilstrecke, auf der das Flugzeug gerade ist. |
| Herkunft ohne Route | OpenSky-Flugspur | beginnt die Spur am Boden, ist der nächste Flughafen aus `data/airports.json` der Start. |
| Wetter | aviationweather.gov, NOAA | METAR alle 20 min (`METAR=0` schaltet ab). |
| ISS | Celestrak | Bahndaten einmal am Tag, Überflüge rechnet der Server (`ISS=0`). |
| Logos | Kiwi.com, FlightAware, Google | holt der Server beim ersten Auftauchen einer Airline; nicht im Repo. |
| Flughäfen | [OurAirports](https://ourairports.com) | gemeinfrei, `tools/make_airports.py` erzeugt `data/airports.json`. |

Fällt eine Quelle aus, fehlen nur ihre Teile: ohne Routen keine Herkunft, ohne METAR keine Wetterkarten. Ohne frische Flugdaten wird das Bild nach 2 min gedimmt und nach 10 min leer (`STALE_AFTER`, `EMPTY_AFTER`). Fehler stehen in `/issues` und `/health`.

## Airline-Logos

Nur quadratische Bildmarken: Schriftzüge sind bei 32 × 32 Pixeln unlesbar, dann zeigt das Panel Layout A ohne Logo. Der Server probiert die Quellen der Reihe nach, nimmt das erste Bild mit Seitenverhältnis bis `LOGO_MAX_ASPECT`, entfernt weißen Hintergrund, schneidet zu und legt `data/logos/<IATA>.png` ab (ohne IATA-Code `<ICAO>.png`). Platzhalterbilder der Quellen erkennt er. Fehlschläge stehen in `/health` → `logos_missing` und werden nach 7 Tagen erneut probiert.

- Alle auf einmal: `python3 -m flightpanel.fetch_logos` (im Container: `docker compose exec flightpanel …`), gezielt `… fetch_logos LH U2 FR`, `--force` überschreibt.
- Handgemachte Logos haben Vorrang: `tools/make_logo.py bild.svg LH` macht aus PNG, JPG oder SVG das passende Format, mit Vorschau. Ins Repo kommen nur solche, mit `git add -f data/logos/LH.png` (abgerufene sind in `.gitignore`).
- `data/airlines.json` bildet ICAO-Codes auf kurze Namen und den Logo-Code ab: die Marke, nicht unbedingt der Tochterbetrieb (Malta Air → `FR`). Leer heißt Logo per ICAO, wenn der IATA-Code doppelt vergeben ist.

## Endpunkte

| Pfad | Zweck |
|---|---|
| `/` | Vorschauseite |
| `/frame` | Binär für den ESP32: `[1, Helligkeit, 128, 32]` + 12 288 Byte RGB |
| `/frame.png` | Vorschau; `layout`, `card`, `scale`, `gap=0` |
| `/display.json` | Display-Modell, dazu `nearby`: alle Flugzeuge im Radius mit Richtung, Höhenwinkel, Sichtbarkeit, Landefolge |
| `/health` | Quellen, Cache, Logos, letzter ESP32-Abruf (`esp32.alive=false`: Panel hängt) |
| `/issues` | gesammelte Auffälligkeiten (siehe unten), `POST /issues/clear` leert |
| `/stats` | Tagesstatistik, Sammelalbum, seltene Sichtungen, Landerichtung |
| `/idle` | Leerlauf-Karten: Stufe, ob sie gerade etwas zu sagen haben, welche dran ist |

## Betrieb auf dem Server

```bash
git clone https://github.com/toastbrot2000/flightpanel && cd flightpanel
cp /pfad/zur/alten/.env .env
docker compose up -d --build
docker compose exec flightpanel python3 -m flightpanel.fetch_logos
```

Dann `/health` ansehen: `source.active` sollte `adsblol` sein. Updates: `git pull && docker compose up -d --build`. Cache, Logos, Statistik und Auffälligkeiten liegen in `data/` und bleiben. In den `QUIET_HOURS` ist das Panel schwarz und der Server ruft nichts ab.

**Auffälligkeiten (`/issues`)** sammelt der Server im Betrieb, mit Zähler, erstem und letztem Auftreten, Callsigns und einem Beispiel:

| Art | Bedeutung | Was tun |
|---|---|---|
| `unknown_operator` | Betreibercode nicht in `airlines.json` | eintragen (Name, Logo-Code), `"airline": false` für Rettung, Polizei, Militär |
| `name_mismatch` | unser Name passt nicht zu adsbdb | meist alte Daten bei adsbdb, sonst Fehler bei uns |
| `route_implausible` | Route passt nicht zur Position, verworfen | nichts, zur Kontrolle |
| `route_unknown` | gezeigter Airline-Flug ohne Route | nichts, keine Quelle kennt ihn |
| `source_error`, `rate_limit`, `fallback` | Fehler, HTTP 429, OpenSky eingesprungen | bei Häufung `POLL_INTERVAL` erhöhen |
| `stale` | Bild gedimmt oder leer mangels Daten | Quellen prüfen |
| `esp32_gap` | ESP32 hat länger keinen Frame geholt | WLAN, Stromversorgung |
| `exception` | Fehler im Code, mit Datei und Zeile | Bug, Traceback im Container-Log |

Liegt in `data/issues.json`. Nach dem Durchsehen leeren: `curl -X POST http://server:8080/issues/clear`.

## Firmware

PlatformIO, Board `esp32dev` (NodeMCU ESP32, WROOM-32), Bibliothek ESP32-HUB75-MatrixPanel-DMA 3.0.14.

```bash
cd firmware
cp include/secrets.h.example include/secrets.h   # WLAN (2,4 GHz!), Server-URL, OTA-Passwort
pio run -t upload                                # erstes Mal per USB, VIN dabei abstecken
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

Beim Boot zeigt die Firmware ein Testbild: linkes Panel rot, rechtes grün (Kettenreihenfolge), dann Rahmen und Diagonale (Geometrie), dann Schrift (Orientierung). Danach holt sie alle 2 s `/frame`. Schalter in `include/config.h`: `SWAP_HALVES`, `HUB75_CLKPHASE`, `HUB75_LATCH_BLANKING`, `MAX_BRIGHTNESS`. Ohne Server bleibt das letzte Bild stehen, nach 60 s leuchtet ein dunkelroter Punkt unten rechts, nach 10 min startet der ESP32 neu. Kompilieren ohne lokales PlatformIO: `sh tools/check_firmware.sh` (Docker).

## Entwicklung

```bash
pip install -r server/requirements-dev.txt    # dazu DejaVu-Schriften (fonts-dejavu-core), wie im Container
pytest                                        # vom Repo-Wurzelverzeichnis, ohne Netz
ruff check .
MOCK=1 DATA_DIR=./data PYTHONPATH=server python3 -m flightpanel.main
```

Die Tests prüfen ohne Netz: Einstellungen, Sichtfeld und Vorhersage, Landefolge, Routen, Kartenauswahl, Statistik, METAR, Sonne (gegen veröffentlichte Zeiten), ISS (gegen eine unabhängige Position), jede Karte mit Beispieldaten und jeden HTTP-Endpunkt. GitHub Actions baut bei jedem Push den Container und führt darin Lint und Tests aus; bei Änderungen an `firmware/` kompiliert es die Firmware.

### Aufbau

```
server/flightpanel/
  main.py        Einstieg: App, Abruf-, Wetter-, ISS- und Speicherschleife
  web.py         HTTP-Endpunkte und Vorschauseite
  config.py      Einstellungen aus .env (Config.from_env), ruhiger Modus, Helligkeit
  data.py        mitgelieferte Daten laden; storage.py Dateien atomar schreiben
  issues.py      Auffälligkeiten sammeln (/issues); cache.py Antworten mit Ablaufzeit

  Quellen        adsblol.py, opensky.py (Flugzeuge), adsbim.py, adsbdb.py (Routen),
                 origins.py (Herkunft aus der Flugspur), metar.py (Wetter), astro.py (Sonne, ISS),
                 logos.py, logo_image.py, fetch_logos.py (Logos)
  model.py       State (ein Flugzeug von der Quelle), Flight (fürs Panel), DisplayModel
  build.py       baut bei jedem Abruf das DisplayModel
  airlines.py    Betreiber, Airline oder nicht, Routenprüfung
  aircraft.py    Kennzeichen, Flugzeugklasse, Text für Zeile 2
  view.py        Sichtfeld: Richtung, Höhenwinkel, wann einer hineinfliegt
  airport.py     Landerichtung, Landefolge und -zeit, Abflüge
  stats.py       Tagesstatistik, Sammelalbum, seltene Flugzeuge, Flaute
  mock.py        Fantasieflüge für MOCK=1

  render.py      Fluglayouts, Himmelspunkte, Frame-Bytes und PNG
  draw.py        Zeilenraster und Texthelfer; fonts.py, colors.py, sprites.py
  idle.py        Auswahl der Leerlauf-Karten (Stufen, Gewichte, Dringendes)
  cards/         die Karten: traffic, runway, album, sky, fun; Liste in __init__.py
server/tests/    pytest
firmware/        ESP32: platformio.ini, src/main.cpp, include/config.h, secrets.h.example
tools/           make_logo.py, make_airports.py, check_firmware.sh
data/            airlines.json, airports.json, rare.json, jokes.json, logos/;
                 im Betrieb cache.json, issues.json, stats.json, tle.txt
docs/            Bauplan (Hardware), screens.md (alle Bildschirme)
```

### Eine Leerlauf-Karte hinzufügen

1. In der passenden Datei unter `server/flightpanel/cards/` zwei Funktionen: `foo_cond(ctx)` gibt `Hit(daten)` zurück oder `None`, wenn die Karte gerade nichts zu sagen hat; `foo_draw(img, ctx, daten, t_in, f)` zeichnet. Optional `foo_demo(ctx)` mit Beispieldaten. Was `ctx` mitbringt: `idle.Ctx`; Texthelfer: `draw.py`.
2. Eintrag in `CARDS` in `cards/__init__.py`, mit Stufe (`tier`) und Gewicht.
3. Ansehen: `/frame.png?card=foo`. Der Test `test_every_card_renders_with_demo_data` zeichnet sie automatisch mit.
4. `CARD_FOO=1` in `.env.example` und eine Zeile in `docs/screens.md`.

### Eine Airline nachtragen

In `data/airlines.json`: `"ICAO": {"name": "Kurzname", "iata": "XY"}`. Für Rettung, Polizei, Militär `"airline": false` und `"label": "Polizei"`; mit `"number": true` wird aus CHX31 "Christoph 31". Danach das Logo holen (`fetch_logos XY`) oder von Hand machen (`tools/make_logo.py`). Kandidaten sammelt `/issues` unter `unknown_operator`.
