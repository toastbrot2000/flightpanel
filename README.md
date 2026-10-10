# flightpanel

LED-Anzeige fürs Wohnzimmer, die das Flugzeug in der Nähe zeigt, mit Airline-Logo (Vorbild: The Flight Wall). 2 × Waveshare HUB75 P2.5 64 × 32 nebeneinander = 128 × 32 Pixel auf 32 × 8 cm, ESP32, Server im Homelab. Der komplette Bauplan liegt in `docs/flugpanel-bauplan.md`.

```
adsb.lol ─┐  (Ersatz: OpenSky)
adsbdb ───┼─> server (Python, Container) ──HTTP /frame──> ESP32 ──HUB75──> 2 Panels
Logo-CDNs ┘    pollt, cachet, holt Logos,   12 KB alle 2 s     DMA-Refresh
               rendert 128x32
```

Der Server rendert das komplette Bild. Der ESP32 holt es ab und schreibt es in die Panels; er kennt weder Flüge noch Fonts noch Logos. Jede Layoutänderung passiert in Python, Vorschau im Browser, kein Flashen.

## Schnellstart (ohne Hardware, ohne API-Zugang)

```bash
cp .env.example .env            # MOCK=1 ist schon gesetzt
docker compose up -d --build
open http://localhost:8080      # Vorschau, wechselt alle 2 s
docker compose exec flightpanel python3 -m flightpanel.fetch_logos   # Logos für ~130 Betreiber holen
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
| `/display.json` | Display-Modell (Flüge, Status, Route, Höhe, Airline-Codes …), dazu `nearby`: alle Flugzeuge im Radius mit Richtung (`az`), Höhenwinkel (`elev`) und `in_view` |
| `/health` | Quelle, OpenSky-Credits, Cache, vorhandene/fehlende Logos, letzter ESP32-Abruf, Anzahl Auffälligkeiten. `esp32.alive=false` heißt: Panel hängt. |
| `/issues` | gesammelte Auffälligkeiten (siehe unten), `POST /issues/clear` leert |
| `/stats` | Tagesstatistik, Sammelalbum, seltene Sichtungen, Landerichtung |
| `/idle` | Leerlauf-Karten: welche gerade etwas zu sagen haben, welche dran ist |

## Layouts

Drei Zeilen à 11 px, DejaVu Sans als harte Pixelschrift, Pfeile als Pixelmuster.

- **C** Logo links (30 × 30), rechts: Pfeil + Airline, Route + Typ, wechselnde Infozeile (Höhe/Speed, Entfernung/Kurs, Callsign)
- **A** ohne Logo, volle Breite, Callsign rechts oben
- **B** zwei Flüge, je eine Zeile
- **auto** C, wenn für die Airline ein Logo liegt, sonst A

Bei zwei Flügen im Modell wechseln A und C alle 12 s zum nächsten Flug. Ohne Airline im Sichtfeld: Leerlauf-Karten (siehe unten). Farben: Ankunft amber, Abflug grün, Überflug blau, kein Weiß als Textfarbe.

## Was angezeigt wird

Pro Abruf ordnet der Server alle Flugzeuge im Radius (unter `MAX_ALT_M`, nicht am Boden):

1. **Sichtfeld zuerst.** `VIEW_FROM`/`VIEW_TO` (Richtung im Uhrzeigersinn, 0 = Nord, 90 = Ost), `VIEW_MIN_ELEV` (Grad über dem Horizont) und `VIEW_MAX_KM` beschreiben, was man vom Fenster aus sieht. Zählt auch, wer bis zum nächsten Abruf hineinfliegt (aus Geschwindigkeit, Kurs und Steigrate weitergerechnet), sonst wären Anflüge schon durch, bevor sie auf dem Panel stehen. `VIEW_ONLY=1` zeigt nur das Sichtfeld und sonst die Uhr, `VIEW_ONLY=0` zeigt Sichtbares zuerst und sonst das nächste Flugzeug. Default: rundum.
2. **Airlines vor dem Rest.** Linien-, Charter- und Frachtflüge zählen als Airline: Betreiber aus `data/airlines.json` oder mit Route bei adsbdb. Privatflüge (Callsign = Kennzeichen), Rettungshubschrauber, Polizei und Luftwaffe (`"airline": false` in `airlines.json`) kommen nur dran, wenn keine Airline zu sehen ist; `SHOW_OTHER=0` blendet sie ganz aus.
3. **Landefolge, dann kürzeste Sichtlinie.** Wer als nächstes landet, kommt zuerst. Die Landefolge schätzt der Server aus der Reststrecke bis zur Piste: im Endanflug die Entfernung, im Gegenanflug weiter bis zum Eindrehen plus Endanflug, im Queranflug quer zur Achse plus Rest. Die Landerichtung (BER 25 oder 07) erkennt er am Verkehr in Platznähe. Fluglotsen können umsortieren, für Flugzeuge im Endanflug stimmt die Folge aber praktisch immer.

Bis zu `MAX_FLIGHTS` Flüge wechseln sich ab. Sind mindestens zwei Flugzeuge im Sichtfeld, steht unter dem (dann kleineren) Logo eine Punktreihe: ein Punkt pro gezeigtem Flug im Sichtfeld, in der Farbe seines Status, in Lesereihenfolge: links der, der zuerst landet, dann die Landefolge, danach Starts und Überflüge. Der Punkt des gerade gezeigten Flugs leuchtet hell. (Vom Balkon aus nach Süden gesehen steht der, der zuerst landet, am weitesten rechts, Richtung BER.) Ohne Logo (Layout A) stehen die Punkte rechts unten.

Eichen: am Fenster ein Flugzeug suchen und in `/display.json` → `nearby` dessen `az` und `elev` ablesen. `seq` ist der Platz in der Landefolge.

**Routen**, der Reihe nach: adsb.im (der Routendienst hinter den tar1090-Karten, aktuell, eine Abfrage für alle Flüge), dann adsbdb. Beide speichern Routen pro Callsign, und Airlines verwenden Callsigns für andere Strecken weiter; liegt das Flugzeug weit neben der Strecke, verwirft der Server sie. Kennt keine Quelle den Flug, sucht der Server für gezeigte Flüge den Abflughafen aus der OpenSky-Flugspur: Beginnt sie tief, ist der nächste Linienflughafen (`data/airports.json`, aus OurAirports) der Start. Landende enden am eigenen Platz. `ADSBIM=0`, `TRACK_ORIGIN=0` schalten ab.

**Zeile 2** zeigt die Route, sonst was der Flug ist (Luftrettung, Polizei, Kleinflugzeug, Hubschrauber, aus `airlines.json` mit `"label"`, ADS-B-Kategorie oder deutschem Kennzeichen), sonst bei Landung `?→BER`, bei Start `BER→?`, sonst nichts. Nichts steht doppelt: Das Rufzeichen fällt aus den wechselnden Infoseiten, wenn es oben schon steht. Landende haben eine Infoseite mehr, "landet in ~N min": Reststrecke bis zur Piste nach Flugphase, mit Tempo zwischen jetzt und Endanflug (Landende werden langsamer).

**Alle Pixel:** Logos 32 x 32 bündig in der Ecke (mit Himmelspunkten 28 x 28, Punkte auf der letzten Zeile), Zeilen bei 0, 12 und 24. Die untere Zeile sitzt auf der Kante; Text mit Unterlängen (g, p, y) rückt dort so weit hoch, dass nichts abgeschnitten wird. Text am Rand beginnt bzw. endet auf der ersten bzw. letzten Spalte (`Font.draw`, `Font.text_width`). Privatflieger heißen nach ihrem Kennzeichen (D-EHGB), Rettungshubschrauber nach ihrer Nummer (Christoph 31); Halternamen kommen nie aufs Panel.

## Leerlauf: wenn keine Airline im Sichtfeld ist

Statt nur Uhr und Datum wechseln Karten, alle `IDLE_SECONDS` (12 s), in festen Stufen (Übersicht und Entscheidungen: `docs/screens.md`): **2 Verkehr am BER** (Abflüge, Landungen, nächster Flieger, Privatflieger und Hubschrauber im Sichtfeld, Pistenwechsel) vor **3 wenig los** (Radar, Landerichtung, Wetter, Statistik, ISS, Sonne, Saison) vor **4 Spielereien** (Witze, Verspätungsgründe, Animationen). Eine Stufe kommt nur dran, wenn keine höhere etwas zu zeigen hat; Spielereien nur, wenn seit 15 min am BER nichts gestartet oder gelandet ist und in den nächsten 10 min keiner landet. Die Uhr ist in jeder Stufe dabei, etwa in einem Drittel der Fälle (`IDLE_CLOCK_SHARE`). Dringendes bricht durch.

| Karte | Wann | Was |
|---|---|---|
| `alert` | Squawk 7700/7600/7500 im Abrufradius | Vollbild, vor allem anderen, auch vor Flügen |
| `departures` | am BER rollt oder startet ein Flug mit Route ab BER, oder ist vor wenigen Minuten gestartet | "↑ Abflug BER", Piste, bis zu zwei Flüge mit Ziel und "rollt" / "startet" / "vor 2 min" |
| `arrivals` | Landende in den nächsten 15 min | "↓ Landung BER", Piste, die nächsten zwei mit Herkunft und Minuten |
| `next` | ein Flugzeug kommt in 15 s bis 10 min ins Sichtfeld, unter 3 min dringend | wer, "Sichtbar in ~N min" (Zeit bis zum Sichtfeld, nicht bis zur Landung), auf welcher Seite des Fensters (← →) |
| `private` | Privatflieger, Hubschrauber, Militär im Sichtfeld | wie ein Flug in Layout A/C |
| `radar` | Flugzeuge im Radius | Rundumblick, Sichtfeld als Band in der Mitte, Punkte nach Richtung und Höhenwinkel in Statusfarbe; der nächste blinkt |
| `runway` | Landerichtung seit 30 min stabil, in den letzten 2 h vom Verkehr bestätigt, laut METAR kein Rückenwind | "BER landet auf 25", Wind, ob Landungen oder Starts durchs Sichtfeld kommen |
| `runway_change` | bis 1 h nach einem Pistenwechsel | neue Richtung, was das fürs Fenster heißt |
| `weather` | METAR erklärt den leeren Himmel: Gewitter, Nebel/Sicht unter 2 km, Wolken unter 1500 m, Schnee | z. B. "Wolken auf 600 m, Flieger versteckt, 3 Flieger darüber" |
| `curfew` | `CURFEW_HOURS` (BER 0-5 Uhr) | nur zu sehen, wenn `QUIET_HOURS` das Panel dann nicht abschaltet |
| `scoreboard` | ab 10 Flugzeugen am Tag | Flugzeuge, Airlines, wie viele im Sichtfeld, das höchste |
| `pokedex` | ab der ersten Airline | Sammelalbum: gesehene Marken aus `airlines.json`, dazu Exoten |
| `new` | Airline zum ersten Mal gesehen, 2 min dringend | "NEU!" mit Logo. Nicht in den ersten 6 h nach dem ersten Start, da füllt sich das Album still |
| `rare` | Typ oder Betreiber aus `data/rare.json` im Abrufradius | "Weit weg, aber selten: A380 Emirates, 70 km SO"; Typen 2 min dringend |
| `quiet` | ab 10 min ohne Flug im Sichtfeld | Flaute und Tagesrekord; nach einer Abruflücke (Quiet Hours) beginnt die Uhr neu |
| `iss` | sichtbarer ISS-Überflug in den nächsten 30 min, oder er läuft gerade (dringend) | Richtung, Höhe, ob er durchs Fenster kommt |
| `sunset`, `sunrise` | 2 h vor Sonnenuntergang, 90 min vor Sonnenaufgang | Uhrzeit; "Gegenlicht", wenn die Sonne im Sichtfeld steht |
| `joke`, `delay`, `txl`, `ber_opening`, `plane`, `pigeon` | immer, mit kleinem Gewicht | aus `data/jokes.json`; Jahrestage (BER 31.10., letzter Tegel-Flug 8.11., Luftbrücke) kommen an ihrem Tag öfter |
| `santa`, `fireworks` | 24.12. ab 15 Uhr, 31.12. ab 18 Uhr bis 2 Uhr | Schlitten 1 im Anflug, Feuerwerk |

**Nächster Flieger:** Der Server rechnet jedes Flugzeug im Radius 10 min weiter (Tempo, Kurs, Steigrate). Landende fliegen aber nicht geradeaus, sie drehen in den Endanflug. Liegt der Endanflug der aktuellen Landerichtung im Sichtfeld, zählt für sie die Reststrecke bis dorthin. Welche Teile von An- und Abflug im Sichtfeld liegen, rechnet der Server aus dem Sichtfeld (3°-Gleitpfad, Steigen mit 10 %).

**Quellen und Ausfälle:** Wetter ist das METAR von aviationweather.gov, ersatzweise vom NOAA-Server, alle 20 min (`METAR=0` schaltet ab). Die ISS-Bahndaten kommen einmal am Tag von Celestrak, ersatzweise von einer zweiten Quelle, liegen in `data/tle.txt` und gelten 14 Tage (`ISS=0`). Sonne und Überflüge rechnet der Server selbst. Fällt eine Quelle aus, fehlen nur ihre Karten; Fehler stehen in `/issues` (`source_error: metar` oder `tle`) und in `/health` → `metar`, `iss`. Karten, die frische Flugdaten brauchen (`next`, `radar`, `quiet`, `private`), pausieren, solange das Bild gedimmt ist. Stürzt eine Karte ab, kommt die Uhr und das Log bekommt einen Traceback.

**Vorschau:** Die Vorschauseite hat Links für jede Karte, oder direkt `/frame.png?card=radar`. Passt die Lage gerade nicht, zeigt die Karte Beispieldaten. `/idle` listet alle Karten: aktiv, dringend, abgeschaltet, mit ihren Daten. `IDLE_OFF=joke,pigeon` schaltet Karten ab.

**Statistik:** `data/stats.json` (übersteht Neustarts, `GET /stats`): heute, die letzten 60 Tage, Sammelalbum mit fehlenden Airlines, seltene Sichtungen, Landerichtung. `SCAN_RADIUS_KM` (Default wie `RADIUS_KM`) vergrößert den Abruf für Statistik, seltene Flugzeuge und Notfälle, ohne mehr anzuzeigen. Seltene Typen und Betreiber stehen in `data/rare.json`, Witze und Verspätungsgründe in `data/jokes.json`.

## Airline-Logos

Nur quadratische Bildmarken: Schriftzüge sind bei 30 × 30 Pixeln unlesbar, dann zeigt das Panel Layout A ohne Logo. Der Server holt fehlende Logos selbst und probiert die Quellen der Reihe nach: Kiwi.com (App-Icons, per IATA), dann FlightAware (Bildmarken, per ICAO). Er nimmt das erste Bild mit Seitenverhältnis bis `LOGO_MAX_ASPECT` (1,5), entfernt weißen Hintergrund, skaliert auf 30 × 30 und legt `data/logos/<IATA>.png` ab (ohne IATA-Code `<ICAO>.png`). Platzhalterbilder der Quellen (graues Flugzeug) erkennt er, indem er jede Quelle einmal nach einem Code fragt, den es nicht gibt. Fehlschläge stehen in `/health` → `logos_missing` und werden nach 7 Tagen erneut probiert.

- Alles auf einmal: `python3 -m flightpanel.fetch_logos` (alle Airlines aus `data/airlines.json`), oder gezielt `… fetch_logos LH U2 FR`, `--force` überschreibt.
- Handgemachte Logos haben Vorrang: eine 30 × 30-PNG als `data/logos/<IATA>.png` oder `<ICAO>.png` ablegen. Neue Dateien werden im Betrieb erkannt.
- `tools/make_logo.py bild.svg LH` macht aus beliebigen Bildern (PNG/JPG/SVG) das passende Format, mit Vorschau. `--bg "#ff6600"` legt eine Markenfarbe hinter das Logo, `--crop 10` schneidet Rand ab.
- `LOGO_FETCH=0` schaltet den Abruf ab, `LOGO_URLS` ersetzt die Quellen (kommagetrennt, `{iata}` oder `{icao}`).

`data/airlines.json` bildet ICAO-Codes auf kurze Anzeigenamen und den Logo-Code ab (adsbdb liefert sonst "Deutsche Lufthansa AG"). Der Logo-Code ist die IATA-Marke, nicht unbedingt die des Tochterbetriebs: Malta Air → `FR`, DHL-Betriebe → `D0`. Leer heißt: Logo per ICAO, weil der IATA-Code doppelt vergeben ist (Cargolux `CV` ist bei Kiwi Air Chathams). Rund 130 Einträge: alles, was in Berlin regelmäßig fliegt, große Überflieger und Frachter.

## Echte Daten

In `.env` `MOCK=0`, `HOME_LAT`, `HOME_LON` setzen, `docker compose up -d --build`. Kein Account nötig.

**Quelle `SOURCE=adsblol` (Default):** [adsb.lol](https://adsb.lol), ein Community-Netz aus ADS-B-Empfängern plus MLAT. Ohne Account und ohne Tageslimit, Abruf alle `POLL_INTERVAL` Sekunden (20 s). Liefert auch den Flugzeugtyp und die ADS-B-Kategorie, daran erkennt der Server Hubschrauber (`A7`) und Leichtflugzeuge. adsb.lol kündigt an, die API künftig nur noch mit Key herauszugeben (für Leute, die einen Empfänger beisteuern).

**Ersatz OpenSky:** Fällt adsb.lol aus (Timeout, Fehler, Key-Pflicht), springt OpenSky ein, aber nur so oft, wie dessen Tagesbudget erlaubt. adsb.lol wird bei jedem Abruf weiter probiert, ein Aussetzer kostet also nur einen Abruf. Anonym gibt OpenSky 400 Credits am Tag (rund alle 2,5 min), mit Account 4000:

1. OpenSky-Account anlegen, unter *API Clients* einen Client erzeugen.
2. In `.env`: `OPENSKY_CLIENT_ID`, `OPENSKY_CLIENT_SECRET`. `SOURCE=opensky` nimmt nur OpenSky.

Das OpenSky-Intervall rechnet der Server selbst aus: nie kürzer als `POLL_INTERVAL`, aber so lang, dass das Tagesbudget für die Stunden außerhalb `QUIET_HOURS` reicht (in den Quiet Hours ist das Panel schwarz, es wird nicht gepollt). Bei unter 10 % Rest-Credits verdoppelt er das Intervall, bei HTTP 429 wartet er so lange, wie OpenSky es sagt. `/health` → `source` zeigt, welche Quelle gerade liefert.

Routen und Flugzeugtypen kommen von adsbdb.com (kostenlos, ohne Account) und werden 24 h bzw. 30 Tage in `data/cache.json` gehalten.

## Betrieb auf dem Server

```bash
git clone https://github.com/toastbrot2000/flightpanel && cd flightpanel
cp /pfad/zur/alten/.env .env       # steht nicht im Repo: Standort, Sichtfeld, Zugangsdaten
docker compose up -d --build
docker compose exec flightpanel python3 -m flightpanel.fetch_logos
```

Dann `/health` ansehen: `source.active` sollte `adsblol` sein, `poll_interval_s` 20. Der ESP32 zeigt mit `FRAME_URL` in `secrets.h` auf diesen Server. Updates: `git pull && docker compose up -d --build`; Cache, Logos und Auffälligkeiten liegen in `data/` und bleiben.

### Auffälligkeiten: `/issues`

Der Server sammelt im Betrieb, was nachgepflegt oder angesehen werden sollte, mit Zähler, erstem und letztem Auftreten, Callsigns und einem Beispiel:

| Art | Bedeutung | Was tun |
|---|---|---|
| `unknown_operator` | Betreibercode nicht in `airlines.json` | eintragen (Name, Logo-Code), `"airline": false` für Rettung/Polizei/Militär |
| `name_mismatch` | unser Name passt nicht zu adsbdb | meist alte Daten bei adsbdb (SDR = Sundair), sonst Fehler bei uns |
| `route_implausible` | adsbdb-Route passt nicht zur Position, verworfen | nichts, zur Kontrolle |
| `route_unknown` | gezeigter Airline-Flug ohne Route | nichts, weder adsb.im noch adsbdb noch die Flugspur kennen ihn |
| `source_error`, `rate_limit`, `fallback` | Fehler, HTTP 429, OpenSky eingesprungen | bei Häufung `POLL_INTERVAL` erhöhen |
| `stale` | Bild gedimmt oder leer mangels Daten | Quellen prüfen |
| `esp32_gap` | ESP32 hat länger keinen Frame geholt | WLAN, Stromversorgung |
| `exception` | Fehler im Code, mit Datei und Zeile | Bug, Traceback im Container-Log |

Dazu `logos_missing` und Abrufe seit dem Start. Liegt in `data/issues.json`, übersteht Neustarts. Nach dem Durchsehen leeren: `curl -X POST http://server:8080/issues/clear`. `/health` → `issues` zeigt die Anzahl je Art.

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

Die Firmware ist gegen die Bibliotheksquelle 3.0.14 geschrieben. Kompilieren lässt sie sich auch ohne lokale PlatformIO-Installation in Docker, siehe `tools/check_firmware.sh`.

## Struktur

```
server/flightpanel/   config, geo, fonts, opensky, adsblol, adsbdb, model, logos, fetch_logos, render, main
                      Leerlauf: idle (Auswahl), cards (Karten), sprites, stats, metar, astro (Sonne, ISS)
server/tests/         pytest
firmware/             platformio.ini, src/main.cpp, include/config.h, include/secrets.h.example
tools/make_logo.py    Bild -> data/logos/<CODE>.png
data/                 airlines.json, rare.json, jokes.json, logos/; im Betrieb cache.json, issues.json,
                      stats.json, tle.txt (Volume)
docs/                 Bauplan
```

## Tests

```bash
pip install -r server/requirements-dev.txt
python -m pytest server/tests
```

Ohne Netz: Scheduler, Vorhersage, Pistengeometrie, Statistik, METAR-Parser, Sonne (gegen veröffentlichte Auf- und Untergangszeiten), ISS (gegen eine unabhängige Positionsangabe) und jede Karte mit Beispieldaten.
