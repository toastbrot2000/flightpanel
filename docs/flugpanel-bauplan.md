# LED-Flugpanel: Bau- und Betriebsplan

Stand: 10. Oktober 2026, Version 4. Format 128 × 32 Pixel aus zwei HUB75-Panels P2.5 (32 × 8 cm), Airline-Logos, Schutzkleinspannung, klassischer ESP32.

Was sich gegenüber Version 3 geändert hat: Statt vier WS2812-Panels mit 10 mm Pitch kommen zwei Waveshare-HUB75-Panels mit 2,5 mm Pitch. Gleiche Fläche in der Breite, ein Viertel der Höhe, 16-mal so viele Pixel. Damit passen Logo, Airline-Name, Route, Typ und Höhe gleichzeitig aufs Bild, wie beim Vorbild. Die Berrybase-Bestellung (ESP32, Levelshifter) bleibt und passt; die Levelshifter werden voraussichtlich nicht gebraucht.

---

## 1. Ziel

Ein 32 × 8 cm großes LED-Display im Wohnzimmer, das das Flugzeug zeigt, das gerade in der Nähe an- oder abfliegt: Airline-Logo, Airline, Route, Flugzeugtyp, Höhe, Geschwindigkeit, Entfernung. Daten kommen aus dem Netz (OpenSky, adsbdb), Logos von einem Logo-CDN, die Logik läuft in einem Container im Homelab, der ESP32 am Panel zeigt nur an.

Nicht Ziel für Version 1: Scrollen, Animationen, Balkonbetrieb, eigener ADS-B-Empfänger.

Der gesamte Eigenbau läuft mit 5 V Schutzkleinspannung. Das einzige Bauteil mit Netzspannung ist ein zertifiziertes Tischnetzteil, das nicht geöffnet wird.

---

## 2. Hardware

### 2.1 Bestellt

| Teil | Menge | Funktion | Preis |
|---|---|---|---|
| Waveshare RGB-Matrix-P2.5-64x32 (B0BQYDLHY9): 64 × 32 Pixel, 2,5 mm Pitch, 160 × 80 mm, 1/16 Scan, HUB75, Magnete und M3-Gewinde auf der Rückseite, 16-poliges Flachbandkabel und Stromkabel liegen bei | 2 | Anzeige, 128 × 32 Pixel | 59,98 € |
| BTF-LIGHTING Tischnetzteil 5 V / 10 A, CE, mit DC-Schraubadapter | 1 | Stromversorgung | 35,99 € |
| ESP32 NodeMCU Development Board (WROOM-32, Dual-Core) | 1 | Controller | 6,80 € |
| SN74AHCT125N | 2 | voraussichtlich nicht nötig, siehe 4.4 | 1,08 € |

Amazon 95,97 € (Lieferung Montag, 12. Oktober), Berrybase 12,83 € inkl. Versand. Gesamt 108,80 €.

### 2.2 Vorhanden

Lötkolben und Zubehör, Dupont-Kabel (vermutlich, aus dem Arduino-Fundus; sonst 2 € dazu), Multimeter.

### 2.3 Noch zu besorgen (Baumarkt oder Bastelkiste, unter 10 €)

| Teil | Hinweis |
|---|---|
| Trägerplatte 34 × 10 cm: Sperrholz 6 mm, oder ein Stahlblech, dann halten die Panels mit ihren Magneten | Bei Holz: 8 Schrauben M3 × 8 in die Gewinde auf der Panelrückseite, oder die Magnete auf angeschraubte Unterlegscheiben setzen |
| 3 Wago 221 (3-fach) oder eine Lüsterklemme | 5-V-Verteilung: Netzteil → 2 Panels + ESP32 |
| 20 Dupont-Kabel female-female, 10 bis 20 cm | ESP32 → HUB75-Stecker |
| 2 Abstandshalter M3 + Schrauben oder ein Klebepad | ESP32 auf der Rückseite |
| Kabelbinder, Klettband | Netzteil und Kabel |

### 2.4 Später, bei Bedarf

| Teil | ca. | Wann |
|---|---|---|
| Rahmen: 4 Holzleisten oder ein 3D-gedruckter Rahmen; Front aus **getöntem** Acryl 3 mm (rauchgrau, 30 bis 50 % Transmission), 2 bis 4 mm vor den LEDs | 10 bis 15 € | Wenn das Bild gefällt. Getönt, nicht opal: Die Tönung lässt die LEDs aus wirken wie eine schwarze Fläche und schluckt Reflexe, opales Acryl würde die 2,5-mm-Pixel verwaschen. |
| Helligkeitssensor BH1750 | 2,50 € | Wenn der Zeitplan nicht reicht |
| 2 × 74HCT245 | 1 € | Nur, wenn das Bild bei 3,3-V-Logik instabil ist (4.4) |

---

## 3. Mechanik

### 3.1 Layout und Maße

- 2 Panels nebeneinander → 128 × 32 Pixel, 320 × 80 mm Leuchtfläche, Tiefe ca. 15 mm plus ESP32 dahinter
- Trägerplatte 340 × 100 mm, also 1 cm Rand
- Gewicht unter 500 g

### 3.2 Panelmontage

1. Panels so legen, dass die Flachbandbuchsen beider Panels auf derselben Seite liegen und die mit **IN** (oder "DATA IN", Pfeil hinein) beschriftete Buchse des linken Panels zum ESP32 zeigt. Jedes Panel hat IN und OUT; die Pfeile auf der Rückseite zeigen die Datenrichtung.
2. Die beiden Panels stoßen direkt aneinander. Waveshare-Panels sind auf 160 mm gefräst, die Fuge ist bei 2,5 mm Pitch praktisch unsichtbar.
3. Auf Stahlblech: einfach ansetzen, die Magnete halten. Auf Holz: durch die Platte in die M3-Gewinde der Panelrückseite schrauben (Lochabstand am Panel ausmessen, Löcher 3,2 mm).

### 3.3 Elektronik auf der Rückseite

- ESP32 mittig hinter dem linken Panel, Dupont-Kabel kurz (unter 20 cm) zum HUB75-Eingang.
- Wago-Klemmen und Netzteilkabel mit Klettband oder Kabelbinder fixieren.
- Tischnetzteil nicht ans Panel bauen, es liegt am Boden hinter dem Möbel oder hängt am Kabel. Das DC-Kabel des Netzteils ist 1,5 m lang.

### 3.4 Wandmontage

Zwei Schrauben, Schlüssellochbleche oder zwei Haken. Oder das Display steht im Regal, dafür reicht ein kleiner Standfuß aus zwei Holzklötzen.

---

## 4. Elektrik

### 4.1 Stromversorgung

Das Tischnetzteil hat einen 5,5-mm-Hohlstecker und bringt einen Schraubadapter (Hohlbuchse → zwei Schraubklemmen) mit. Von dort zwei Litzen zu den Wago-Klemmen (+ und −). Der Hohlstecker ist für 5 A ausgelegt; dieser Aufbau zieht im Betrieb unter 3 A (8.4). Kein Kabel abschneiden, nichts löten.

Jedes Panel hat einen 4-poligen Stromstecker (VH3.96, 2 × VCC, 2 × GND) und bringt das passende Kabel mit, am anderen Ende Gabelschuhe oder offene Enden. Beide Panelkabel parallel auf die Wago-Klemmen. **Polung prüfen:** Am Panel ist VCC/GND neben dem Stecker aufgedruckt, rot auf VCC. Verpolung zerstört die Treiber.

ESP32: VIN an +5 V, GND an GND, an denselben Wago-Klemmen. Ohne gemeinsame Masse zwischen ESP32 und Panels funktioniert keine Datenleitung.

Keine Sicherung auf der 5-V-Seite: Netzteil mit Überstrom- und Übertemperaturschutz, Last weit unter dem Limit.

### 4.2 Strombedarf

Waveshare gibt pro Panel 5 V / 2,5 A, maximal 12 W an (Vollweiß, volle Helligkeit). Das kommt in diesem Projekt nie vor:

- Layouts ohne Weiß als Textfarbe, 10 bis 20 % der Pixel an, Helligkeit per Software gedeckelt (`MAX_BRIGHTNESS 160` in der Firmware, Server schickt tagsüber 90).
- Realistisch: 0,3 bis 1 A für beide Panels zusammen, plus 0,15 A ESP32.
- Selbst der Herstellermaximalwert beider Panels (5 A) plus ESP32 liegt unter dem, was das Netzteil laut Rezensionen dauerhaft liefert (bei 7 A sehr warm, bei 8,5 A Abschaltung). Im Normalbetrieb: Reserve Faktor 5.

Das Netzteil ist damit deutlich überdimensioniert. Es ist schon bestellt und kostet im Leerlauf nichts extra; ein 5 V / 4 A-Netzteil hätte gereicht.

### 4.3 Datenleitung ESP32 → HUB75

Der ESP32 steuert die Panels direkt über 13 Signalleitungen plus Masse (HUB75). Panel 1 OUT → Panel 2 IN mit dem beiliegenden Flachbandkabel.

**Einfachster Anschluss:** Das zweite beiliegende Flachbandkabel in Panel 1 IN stecken. In die freie IDC-Buchse am anderen Ende lassen sich Dupont-Stecker (male) direkt stecken, Rastermaß 2,54 mm. Das ist mechanisch sauberer als Dupont-Buchsen auf den Stiftleisten am Panel.

HUB75-Belegung, Blick auf die Buchse am Kabelende, Nase (Polarisierung) links:

```
 R1 | G1
 B1 | GND
 R2 | G2
 B2 | E        E ist bei 1/16-Scan (64x32) unbenutzt, frei lassen oder GND
 A  | B
 C  | D
CLK | LAT
 OE | GND
```

Belegung am ESP32 (Standard der Bibliothek, in `firmware/include/config.h`):

| HUB75 | ESP32 GPIO | HUB75 | ESP32 GPIO |
|---|---|---|---|
| R1 | 25 | A | 23 |
| G1 | 26 | B | 19 |
| B1 | 27 | C | 5 |
| R2 | 14 | D | 17 |
| G2 | 12 | E | frei |
| B2 | 13 | LAT | 4 |
| CLK | 16 | OE | 15 |
| GND | GND (beide GND-Pins verbinden) | | |

Also 14 Dupont-Kabel. Beschriftete Kabel oder ein Foto helfen; vertauschte Farbleitungen sieht man sofort am Testbild (rot ist dann grün), vertauschte Adressleitungen ergeben verwürfelte Zeilen.

GPIO 12 und 15 sind Strapping-Pins des ESP32. Das ist bei dieser Bibliothek Standard und funktioniert, weil die Panel-Eingänge hochohmig sind. Beim Flashen über USB trotzdem die Panel-Verbindung gesteckt lassen dürfen: ja, das geht; nur VIN abziehen, solange USB dran ist.

### 4.4 Pegel: 3,3 V reicht in der Regel

HUB75-Panels puffern die Eingänge mit 74HC245 an 5 V. Deren Schaltschwelle liegt bei etwa 2,5 V; die 3,3 V des ESP32 werden in der Praxis bei kurzen Leitungen sauber erkannt. Waveshare selbst zeigt in der Doku zu diesem Panel den NodeMCU-32S und den Raspberry Pi (beide 3,3-V-Logik) direkt am HUB75-Eingang, ohne Pegelwandler, und liefert die ESP32-Demos mit genau der hier verwendeten Bibliothek.

Falls doch Flackern, Geisterbilder oder Farbrauschen auftreten, das sich mit `clkphase` und `latch_blanking` (5.4) nicht beheben lässt: zwei 74HCT245 (je 8 Kanäle, 5-V-Versorgung, Richtung A→B) zwischen ESP32 und Panel. Die bestellten 74AHCT125N haben nur 4 Kanäle pro Chip; mit zwei Chips ließen sich die 8 kritischsten Leitungen (CLK, LAT, OE, A bis D, R1) puffern, das wäre die Notlösung mit vorhandenem Material.

### 4.5 USB und VIN

Nicht gleichzeitig über USB und über VIN versorgen. Zum Flashen per USB den VIN-Dupont abziehen, Panels dürfen über das Netzteil laufen (GND bleibt verbunden). Danach nur noch OTA.

---

## 5. Software

### 5.1 Architektur

```
OpenSky API ──┐
adsbdb.com ───┼──> Container "flightpanel" ──HTTP /frame──> ESP32 ──HUB75──> 2 Panels
Logo-CDN ─────┘    pollt, cachet, holt Logos,   12 KB alle 2 s    DMA-Refresh
                   rendert 128x32
```

Der Server rendert das komplette Bild als Bitmap. Der ESP32 holt es ab und schreibt es in den DMA-Puffer der Panels. Er kennt weder Flüge noch Fonts noch Logos:

- Jede Änderung am Aussehen passiert in Python, ohne Flashen. Vorschau im Browser unter `http://server:8080/`.
- Die Firmware ist 200 Zeilen und muss nach dem ersten Flashen praktisch nie angefasst werden.

### 5.2 Server (Container)

**Stack:** Python 3.12, aiohttp, Pillow, DejaVu-Fonts. Keine Datenbank. Ein Container, ein Volume (`./data`).

```
server/flightpanel/
  main.py          Endpoints, Poll-Schleife, Logo-Nachladen
  opensky.py       OAuth2-Token, Polling, Rate-Limit
  adsbdb.py        Route (Callsign) und Flugzeug (icao24), TTL-Cache als JSON
  model.py         Filtern, Klassifizieren, Sortieren, Mock-Flüge
  logos.py         Logo laden / nachladen / aufbereiten
  fetch_logos.py   Seed: alle Logos aus airlines.json auf einmal holen
  fonts.py         DejaVu Sans als 1-Bit-Pixelschrift, Pixel-Pfeile
  render.py        Modell -> 128x32-Bild -> Frame-Bytes / PNG
  config.py        .env
data/
  airlines.json    ICAO -> Anzeigename + IATA (Logo-Code), ~120 Airlines
  logos/           <IATA>.png, 30x30, automatisch gefüllt
  cache.json       adsbdb-Cache (Volume)
```

**Datenfluss, alle `POLL_INTERVAL` Sekunden (Default 30):**

1. OpenSky `GET /api/states/all` mit Bounding Box aus `HOME_LAT/LON` ± `RADIUS_KM`. Bei 40 km unter 1 Quadratgrad → 1 Credit pro Abruf. Budget mit Account 4000 Credits/Tag, anonym 400. Der Server rechnet das Intervall aus: nie kürzer als `POLL_INTERVAL`, aber so lang, dass das Budget für die Stunden außerhalb der Quiet Hours reicht (dort wird nicht gepollt). Mit Account also 20 bis 30 s, anonym rund 2,5 min. Unter 10 % Rest-Credits doppelt so lang, bei 429 so lange Pause, wie OpenSky im Header sagt. OAuth2 Client Credentials, Token 30 Minuten.
2. Filter: nicht am Boden, Höhe unter `MAX_ALT_M` (6000 m), Distanz unter `RADIUS_KM`.
3. Pro Flugzeug: Distanz und Peilung; adsbdb Route (Herkunft, Ziel, Airline mit ICAO- und IATA-Code, Cache 24 h) und Flugzeug (Typ, Cache 30 Tage). Klassifizieren: Ziel == BER → Ankunft, Herkunft == BER → Abflug, sonst Höhe und Vertikalrate, sonst Überflug.
4. Sortieren, die vordersten `MAX_FLIGHTS` (3) ins Modell: erst was im Sichtfeld vom Fenster ist (`VIEW_*` in `.env`; zählt auch, wer bis zum nächsten Abruf hineinfliegt), dann Airlines vor Privatflügen, Hubschraubern und Militär (Frachtflüge zählen als Airline; Sonstige nur, wenn keine Airline zu sehen ist), dann die geschätzte Landefolge (Reststrecke bis zur Piste nach Flugphase), dann die kürzeste Sichtlinie. Routen, die nicht zur Position passen (adsbdb kennt alte Strecken desselben Callsigns), werden verworfen. Datenquelle ist adsb.lol (ohne Account, alle 20 s), OpenSky springt bei Ausfall ein.
5. Für jede Airline im Modell: Logo da? Sonst im Hintergrund holen (5.5).
6. Rendern bei jedem `/frame`-Abruf (billig, 128 × 32).

**Endpoints:**

| Pfad | Zweck |
|---|---|
| `/` | Vorschauseite, wechselt alle 2 s, Links für die Layouts |
| `/frame` | Binär, für den ESP32 (5.3) |
| `/frame.png?layout=C&scale=6&gap=1` | Dasselbe Bild als PNG, LED-Optik |
| `/display.json` | Display-Modell |
| `/health` | OpenSky-Credits, Cache, vorhandene und fehlende Logos, letzter ESP32-Abruf |

`/health` → `esp32.alive=false` heißt: Panel hängt. Das ist das gesamte Monitoring. `logos_missing` listet Airlines, für die der Abruf nichts gefunden hat; die kann man von Hand ablegen.

**Helligkeitsplan:** `BRIGHTNESS_DAY` (90), `BRIGHTNESS_EVENING` (35), `QUIET_HOURS` → 0. Der ESP32 übernimmt den Wert aus dem Frame-Header und deckelt bei `MAX_BRIGHTNESS`.

**Ausfälle:** OpenSky weg → letztes Modell behalten, nach 2 Minuten gedimmt (`stale`), nach 10 Minuten leer (Leerlauf-Karten, die keine frischen Flugdaten brauchen). METAR oder ISS-Bahndaten weg → deren Karten fallen weg, die anderen bleiben. adsbdb weg → Flüge ohne Route. Logo-CDN weg → Layout A ohne Logo, nächster Versuch nach 10 Minuten.

### 5.3 Schnittstelle Server → ESP32

`GET /frame` liefert `application/octet-stream`:

| Byte | Inhalt |
|---|---|
| 0 | Protokollversion (1) |
| 1 | Helligkeit 0 bis 255 |
| 2 | Breite (128) |
| 3 | Höhe (32) |
| 4 … | 128 × 32 × 3 Byte RGB, zeilenweise von oben links |

Gesamt 12 292 Byte. Der ESP32 pollt alle `POLL_MS` (2000). Kein ETag, kein Diff, kein Websocket. 6 KB/s im WLAN sind nichts.

### 5.4 Firmware ESP32

**Stack:** PlatformIO, Board `esp32dev`, Arduino-Framework, Bibliothek ESP32-HUB75-MatrixPanel-DMA (mrcodetastic, Version 3.0.14), Adafruit GFX (nur fürs Testbild), ArduinoOTA. WLAN und Server-URL in `secrets.h` (gitignored).

```
setup:
  HUB75_I2S_CFG(64, 32, chain=2, pins), double_buff
  Testbild: links rot / rechts grün (Kettenreihenfolge), Rahmen + Diagonale (Geometrie),
            Schriftzug (Orientierung)
  WLAN, OTA, mDNS flightpanel.local

loop:
  OTA.handle()
  alle 2 s: GET /frame → Header prüfen → setBrightness8(min(b, MAX_BRIGHTNESS))
            → 4096 × drawPixelRGB888 → flipDMABuffer
  60 s ohne Frame: dunkelroter Punkt unten rechts, Bild bleibt stehen
  10 min ohne Frame: ESP.restart()
```

Die Bibliothek refresht die Panels per I2S-DMA im Hintergrund (mehrere hundert Hz), der Prozessor ist dabei fast unbeteiligt. Die 4096 Pixel pro Frame sind in unter 2 ms geschrieben.

Stellschrauben in `config.h`, falls das Testbild nicht stimmt:

| Symptom | Schalter |
|---|---|
| Rechte Hälfte zeigt den linken Bildteil | `SWAP_HALVES true` (oder Panels tauschen) |
| Pixel um eine Spalte verschoben, Geisterbild rechts neben hellen Pixeln | `HUB75_CLKPHASE true` |
| Leichtes Nachleuchten in dunklen Zeilen | `HUB75_LATCH_BLANKING 2` bis `4` |
| Nur jede zweite Zeilengruppe, Bild gestaucht | Panel ist kein 1/16-Scan (bei Waveshare P2.5 64x32 nicht der Fall) |
| Farben vertauscht | R/G/B-Dupont-Kabel vertauscht |

**Alternative ohne eigene Firmware:** keine sinnvolle. WLED kann HUB75 nur über Umwege, und die 200 Zeilen hier sind der einfachere Weg.

### 5.5 Logos

Der Server holt fehlende Logos selbst, beim ersten Auftauchen einer Airline:

1. adsbdb liefert zum Callsign den IATA-Code der Airline (`U2`, `LH`, `FR`). Fehlt er, kommt er aus `data/airlines.json` (ICAO → IATA).
2. Quellen der Reihe nach: `https://images.kiwi.com/airlines/128x128/{IATA}.png` (quadratische App-Icons), dann FlightAware per ICAO (Bildmarken). Genommen wird nur, was annähernd quadratisch ist (`LOGO_MAX_ASPECT` 1,5): Schriftzüge sind bei 30 × 30 unlesbar, dann lieber Layout A. Platzhalterbilder der Quellen werden erkannt. `LOGO_URLS` in `.env` ersetzt die Liste.
3. Aufbereitung: weißer Hintergrund per Flood-Fill vom Rand weg (weiße Flächen im Logo bleiben), auf den Inhalt zuschneiden, quadratisch einpassen, auf 30 × 30 skalieren, auf Schwarz legen.
4. Ablegen als `data/logos/{IATA}.png`. Fehlschläge in `.missing.json`, nächster Versuch nach 7 Tagen.

Vor dem ersten Flug alles auf einmal: `docker compose exec flightpanel python3 -m flightpanel.fetch_logos` holt die rund 110 Airlines aus `airlines.json` (alles, was in Berlin regelmäßig landet, dazu die großen Überflieger und Frachter). Danach fehlen nur Exoten, und die holt der Server im Betrieb nach.

Handgemachte Logos haben Vorrang: eine 30 × 30-PNG als `data/logos/{IATA}.png` oder `{ICAO}.png` ablegen, `tools/make_logo.py` macht aus beliebigen Bildern (auch SVG) das passende Format. Bei 30 × 30 funktionieren Bildmarken gut (Kranich, Harfe, Tulpe), Schriftzüge werden zu Streifen. Für die wichtigsten fünf Airlines lohnt sich ein Blick auf die Vorschau, ob das automatische Logo taugt oder ob man die Bildmarke von Hand ausschneidet.

### 5.6 Display-Layout

Drei Zeilen à 11 px bei 32 px Höhe, Schrift DejaVu Sans (Bold 12 für Zeile 1, Regular 11 darunter), hart geschwellt statt geglättet. Pfeile als feste Pixelmuster.

**Layout C (Standard, wenn ein Logo da ist):**

```
┌──────┐ ↓ easyJet
│ Logo │ LGW→BER            A320
│30x30 │ 5700ft↓ 418kt      (wechselt: Entfernung/Kurs, Callsign)
└──────┘
```

**Layout A (kein Logo):** wie C, über die volle Breite, Callsign rechts in Zeile 1.

**Layout B (zwei Flüge):** je eine Zeile pro Flug: Pfeil, Airline, Flughafen, Höhe, Typ.

Farben: Ankunft amber, Abflug grün, Überflug blau, Infozeilen hellblau, Typ grau. Kein Weiß als Textfarbe. Sind zwei oder mehr Flugzeuge im Sichtfeld, schrumpft das Logo auf 26 × 26 und darunter steht eine Punktreihe: ein Punkt pro Flugzeug, von links nach rechts wie vom Fenster aus gesehen; der gezeigte Flug leuchtet hell. Bei zwei Flügen im Modell wechseln A und C nach jedem Durchlauf der drei Infoseiten (12 s) zum nächsten Flug. Ist keine Airline im Sichtfeld, wechseln Leerlauf-Karten (Uhr, nächster Flieger, Radar, Landerichtung, Wetter, Statistik, ISS, Witze; Privatflieger sind eine Karte davon), siehe README.

Alle drei Layouts sind in `render.py` je 20 Zeilen. Anpassen, Container neu starten, Browser-Vorschau, fertig.

### 5.7 Konfiguration (`.env`)

```
HOME_LAT=52.5xxx            HOME_LON=13.5xxx        HOME_ALT_M=60
VIEW_FROM=140               VIEW_TO=190             VIEW_MIN_ELEV=0.5   VIEW_MAX_KM=40
VIEW_ONLY=1                 SHOW_OTHER=1            POLL_INTERVAL=20
RADIUS_KM=40                MAX_ALT_M=6000          LOCAL_AIRPORT_IATA=BER
MOCK=0                      OPENSKY_CLIENT_ID=…     OPENSKY_CLIENT_SECRET=…
WIDTH=128                   HEIGHT=32               LAYOUT=auto
LOGO_FETCH=1                LOGO_SIZE=30
BRIGHTNESS_DAY=90           BRIGHTNESS_EVENING=35   QUIET_HOURS=23:00-06:30
TZ=Europe/Berlin
```

OpenSky-Client anlegen: im OpenSky-Account unter API Clients. Ohne eigenen Feeder 4000 Credits am Tag.

---

## 6. Bauablauf

**Phase 0, jetzt, vor der Lieferung (1 Abend):**

1. Repo auf den Homelab-Host, `cp .env.example .env`, `docker compose up -d --build`, Vorschau unter `:8080` ansehen (Mock-Modus mit Fantasieflügen).
2. `docker compose exec flightpanel python3 -m flightpanel.fetch_logos` → `data/logos/` füllt sich. In der Vorschau mit `?layout=C` prüfen, welche Logos bei 30 × 30 taugen.
3. OpenSky-Client anlegen, `.env` füllen, `MOCK=0`, neu starten. `/display.json` ein paar Tage beobachten: Wie viele Flüge, wie hoch, wie oft leer? Kalibriert `RADIUS_KM` und `MAX_ALT_M`.
4. PlatformIO installieren (VS-Code-Erweiterung oder `pip install platformio`), `firmware/include/secrets.h` anlegen.

**Phase 1, Montag, Lieferung (1 Abend):**

5. Panels auspacken, Rückseite ansehen: IN/OUT-Pfeile, Stromstecker, VCC/GND-Aufdruck.
6. Netzteil einstecken, am Schraubadapter 5,0 bis 5,2 V messen. Litzen in die Wago-Klemmen, Netzteil ausstecken.
7. Ein Panel: Stromkabel an die Wago-Klemmen (Polung!), Flachbandkabel in IN, 14 Dupont-Kabel zum ESP32 nach 4.3, GND verbinden.
8. ESP32 per USB flashen (`pio run -t upload`), VIN bleibt ab. Netzteil ein. Testbild: Panel rot, dann Rahmen, dann Schriftzug lesbar. Falls nicht: Tabelle in 5.4.
9. Zweites Panel: Strom parallel, Flachband von Panel 1 OUT zu Panel 2 IN. Testbild: links rot, rechts grün, Rahmen über beide, Diagonale ohne Knick.
10. Serial Monitor: WLAN verbunden, erstes `/frame`. Das erste echte Bild.

**Phase 2, ein Nachmittag:**

11. Trägerplatte zuschneiden, Panels montieren, ESP32 und Klemmen befestigen, USB ab, VIN dran.
12. OTA-Test: `pio run -e esp32dev_ota -t upload`.
13. An die Wand oder ins Regal.

**Phase 3, Burn-in:** 24 h laufen lassen. Netzteil und Panelrückseite nach 1 h anfassen: handwarm. `/health` ansehen: `esp32.fetches` zählt hoch.

**Phase 4, später:** Rahmen mit getönter Front, falls gewünscht.

---

## 7. Inbetriebnahme und Tests

### 7.1 Testbild lesen

| Bild | Bedeutung |
|---|---|
| Linkes Panel rot, rechtes grün | Kette stimmt. Umgekehrt: `SWAP_HALVES` |
| Orangefarbener Rahmen 128 × 32, blaue Diagonale durchgehend | Geometrie und Scan stimmen |
| Rahmen an der Fuge unterbrochen | Panel 2 nicht erkannt: Flachband OUT→IN prüfen |
| Schriftzug "flightpanel" lesbar, oben links | Orientierung stimmt |
| Schrift gespiegelt oder auf dem Kopf | Panels drehen, nicht die Software |
| Bunter Schnee, Flackern | Dupont-Kabel prüfen (CLK, LAT, OE, GND), dann `HUB75_CLKPHASE` |
| Nur obere oder untere Hälfte jedes Panels | R2/G2/B2 oder R1/G1/B1 nicht gesteckt |

### 7.2 Gesamttest

- `/frame.png` im Browser und Panel nebeneinander: gleich?
- `/health`: `esp32.alive=true`, `credits_remaining` um 4000 minus Tagesverbrauch, `logos` gefüllt.
- Helligkeit: tagsüber 90 ist im Wohnzimmer deutlich, abends 35. Bei Bedarf in `.env` ändern, kein Flashen nötig.
- Strom: Multimeter in die Plus-Leitung am Schraubadapter, bei normalem Bild. Erwartung unter 1,5 A.

---

## 8. Betrieb

### 8.1 Normalbetrieb

Panel an Dauerstrom. Server pollt OpenSky, ESP32 pollt Server. In den Quiet Hours ist die Anzeige schwarz (die Panels bleiben versorgt, Ruhestrom siehe 8.4).

### 8.2 Fehlerbilder

| Symptom | Ursache | Maßnahme |
|---|---|---|
| Komplett dunkel, Netzteil-LED aus | Netzteil oder Steckverbindung | Stecker, Wago-Klemmen |
| Letztes Bild steht, roter Punkt unten rechts | ESP32 erreicht Server nicht | `/health`, Container-Logs, WLAN, Server-URL in secrets.h |
| Rechte Hälfte dunkel | Flachband zwischen den Panels | Stecker nachdrücken |
| Flackern, Farbrauschen nach Monaten | Dupont-Kabel gelockert | Nachstecken; bei Dauerproblem 74HCT245 (4.4) |
| Anzeige eingefroren | ESP32 hängt | Nach 10 min startet er selbst neu |
| Keine Flüge, obwohl welche da sind | OpenSky-Credits leer oder Filter zu eng | `/health`, `/display.json` |
| Flüge ohne Route (`---`) | adsbdb kennt den Callsign nicht | Normal für 15 bis 30 % der Flüge, meist Privat- und Geschäftsflieger |
| Airline ohne Logo (Layout A) | Logo-CDN kennt den Code nicht | `/health` → `logos_missing`, Logo von Hand ablegen |

### 8.3 Updates

- Server: `git pull`, `docker compose up -d --build`. Cache und Logos überleben auf dem Volume.
- Layout ändern: `render.py` anpassen, neu bauen, im Browser prüfen, fertig. Kein Flashen.
- ESP32: `pio run -e esp32dev_ota -t upload`. Nur nötig, wenn sich Pins, WLAN oder Server-URL ändern.

### 8.4 Verbrauch

- Ruhestrom der zwei Panels (Treiber-ICs, schwarzes Bild): etwa 0,3 bis 0,5 A → 2 bis 3 W, plus ESP32 0,7 W, plus Netzteilverlust 1 W. Etwa 35 kWh im Jahr, 10 €.
- Anzeige: zusätzlich 2 bis 5 W.
- Eine Nachtabschaltung per Smart-Plug spart ein Drittel, 3 € im Jahr. Nur, wenn du sie ohnehin willst. Der ESP32 bootet in 10 s und holt sich das Bild.

---

## 9. Review

### 9.1 Machbarkeit

Das ist der einfachste Aufbau der vier Versionen: zwei fertige Module, ein Flachbandkabel dazwischen, 14 Steckkabel zum ESP32, drei Klemmen für den Strom. Nichts zu löten, keine 230 V, keine Pegelwandlerplatine, keine Fugen zu kaschieren. Der Bauabend am Montag endet mit einem fertigen Bild; der Rest ist Gehäuse und Geschmack.

Die Stellen, die Sorgfalt verlangen:

1. **Polung der Panelstromkabel.** Aufdruck lesen, Multimeter, dann einstecken.
2. **14 Dupont-Kabel richtig stecken.** Mit Liste und Foto fünf Minuten; das Testbild zeigt jeden Fehler.
3. **Logos begutachten.** Automatische Logos sind nicht alle schön bei 30 × 30. Die fünf häufigsten Airlines lohnen einen Blick.

Aufwand: Software läuft (Mock heute, echte Daten nach dem OpenSky-Client), Hardware 1 Abend, Gehäuse 1 Nachmittag.

### 9.2 Entscheidungen

| Entscheidung | Bewertung | Begründung |
|---|---|---|
| HUB75 P2.5 statt WS2812 P10 | richtig | 16-mal so viele Pixel pro Fläche, Logos und drei Zeilen Text, fertige Module ohne Bastelei, zusammen billiger als die vier WS2812-Panels. Preis: eigene Firmware mit DMA-Bibliothek statt FastLED, 14 Leitungen statt einer. Beides Standard. |
| 2 × 64 × 32 statt 128 × 64 oder 4 Panels | richtig | 128 × 32 trägt genau das Flight-Wall-Layout. 128 × 64 würde den Preis verdoppeln und ein E-Signal brauchen, ohne dass mehr Information aufs Bild müsste. Erweiterung später möglich: zweite Reihe, `PANEL_CHAIN 4` plus virtuelles Panel in der Bibliothek, `HEIGHT=64`. |
| Server rendert, ESP32 zeigt nur an | richtig | Iteration ohne Flashen, Browser-Preview, minimale Firmware. |
| HTTP-Polling, 12 KB alle 2 s | richtig | Kein Broker, TCP garantiert vollständige Frames. |
| Logos automatisch vom CDN | richtig, mit Vorbehalt | Null Handarbeit für 95 % der Airlines. Der CDN ist ein Fremddienst ohne Zusage; fällt er weg, läuft alles weiter (Layout A), und `LOGO_URL` ist austauschbar. Für den Privatgebrauch unkritisch. |
| Klassischer ESP32 | richtig | Die HUB75-Bibliothek ist auf dem Classic-ESP32 am reifsten (I2S-Parallel-DMA). Ein C3 könnte das nicht. Die 6,80 € waren gut angelegt. |
| Kein Levelshifter | richtig, mit Rückfalloption | 3,3 V reicht an HUB75-Eingängen in der Praxis. Die bestellten 74AHCT125 bleiben in der Kiste. |
| Tischnetzteil 5 V / 10 A | überdimensioniert, unschädlich | Bedarf unter 3 A. Schon bestellt, zertifiziert, läuft im Leerlauf kühl. |
| Diffusor: getönt statt opal, später | richtig | Opal würde die 2,5-mm-Auflösung verwaschen; getöntes Acryl macht aus den LEDs im Aus-Zustand eine schwarze Fläche. Erst sehen, ob das nackte Panel gefällt. |
| OTA + Watchdog | richtig | Panel an der Wand, USB unerreichbar. |

### 9.3 Was fehlt

- **Gehäuse.** Bewusst später. Die Panels sind von vorn schon fertig (schwarze LED-Maske), von der Seite sieht man die Platine.
- **Helligkeitssensor.** Zeitplan reicht; BH1750 nachrüstbar.
- **Firmware auf echter Hardware.** Die Firmware kompiliert (`tools/check_firmware.sh`, PlatformIO in Docker), ist aber noch nicht auf einem ESP32 mit Panels gelaufen. Das Testbild am Montag ist der Test.

### 9.4 Risiken

| Risiko | Wahrscheinlichkeit | Folge | Umgang |
|---|---|---|---|
| 3,3-V-Pegel reicht bei diesen Panels nicht | niedrig | Flackern, Geisterbilder | `clkphase`, kürzere Kabel, dann 74HCT245 für 1 € |
| Panel DOA | niedrig | ein Panel | Testbild einzeln, Amazon-Rückgabe |
| Logo-CDN liefert für wichtige Airlines nichts oder nur Schriftzug | mittel | Layout A oder unschönes Logo | `logos_missing` ansehen, fünf Logos von Hand |
| Fuge zwischen den Panels sichtbar | niedrig | kosmetisch | Panels sind maßhaltig; bei Bedarf getönte Front |
| OpenSky-Abdeckung oder Credits | niedrig | leeres Panel | Phase-0-Logging |
| WLAN-Abdeckung am Montageort | niedrig | roter Punkt | ESP32 reconnectet selbst; sonst Repeater |

### 9.5 Netzteil

Unverändert zur Vorversion: Tischnetzteil, Schutzkleinspannung, kein Laptop-Netzteil mit Wandler, kein offenes Mean Well. Neu ist nur, dass die Dimensionierung jetzt großzügig ist: 10 A Nennwert gegen unter 3 A Bedarf. Dafür gibt es keine Abschaltungen, kein warmes Gehäuse und keine Diskussion über den Hohlstecker.

### 9.6 Fazit

Version 4 ist die Version, die aussieht wie das Vorbild und sich wie ein Bausatz zusammenstecken lässt. Die Software ist fertig und im Mock-Modus geprüft, die Logos holt sie sich selbst. Am Montag kommen die Panels, der Abend endet mit dem ersten Flug auf dem Display.
