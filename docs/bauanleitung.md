# Bauanleitung

Wie man das Panel nachbaut: Teile, Mechanik, Strom, Verdrahtung, Firmware und Inbetriebnahme. Server und Einstellungen stehen in der [README](../README.md), alle Bildschirme in [screens.md](screens.md).

> **Stand:** Die Firmware kompiliert, ist auf echter Hardware aber noch nicht gelaufen. Diese Anleitung beruht auf den Unterlagen der Panels und der Bibliothek ESP32-HUB75-MatrixPanel-DMA, nicht auf einem fertigen Aufbau. Wer es nachbaut und auf etwas stößt, das hier fehlt oder anders ist: Rückmeldung willkommen.

**Ergebnis:** 32 × 8 cm Leuchtfläche, 128 × 32 Pixel aus zwei HUB75-Panels, dahinter ein ESP32, versorgt von einem 5-V-Tischnetzteil. Nichts zu löten, keine Netzspannung im Eigenbau: Das einzige Teil mit 230 V ist ein fertiges, zertifiziertes Netzteil, das geschlossen bleibt.

**Aufwand:** ein Abend für Strom, Kabel und das erste Bild, ein Nachmittag für Trägerplatte und Montage. Material etwa 100 bis 120 € (Stand 2026).

---

## 1. Teile

| Teil | Menge | Wofür | Worauf achten |
|---|---|---|---|
| HUB75-Panel P2.5, 64 × 32 Pixel, 1/16 Scan, 160 × 80 mm | 2 | Anzeige, zusammen 128 × 32 Pixel | Ausgelegt ist alles auf das Waveshare RGB-Matrix-P2.5-64x32 (Magnete und M3-Gewinde auf der Rückseite, Flachband- und Stromkabel liegen bei). Andere Panels gehen, wenn Größe und Scan passen; sonst `config.h` anpassen. |
| ESP32, klassisch (WROOM-32, z. B. NodeMCU-Board) | 1 | holt die Bilder, steuert die Panels | Kein ESP32-C3 oder -S3: Die HUB75-Bibliothek nutzt das parallele I2S-DMA des klassischen ESP32. |
| Tischnetzteil 5 V, mindestens 4 A, mit 5,5-mm-Hohlstecker und Schraubadapter | 1 | Strom für Panels und ESP32 | Geschlossen und zertifiziert (CE). Mehr Ampere schaden nicht, der Bedarf liegt unter 3 A (siehe 3.2). |
| Dupont-Kabel male, 10 bis 20 cm | 14 | ESP32 → Flachbandkabel des ersten Panels | kurz halten |
| Wago 221 (3-fach) oder eine Lüsterklemme | 3 | 5-V-Verteilung: Netzteil → 2 Panels und ESP32 | |
| Trägerplatte 340 × 100 mm: Sperrholz 6 mm oder Stahlblech | 1 | Montage | Auf Stahlblech halten die Panels mit ihren Magneten; auf Holz 8 Schrauben M3 × 8. |
| Abstandshalter M3 oder Klebepad, Kabelbinder, Klettband | | ESP32 und Kabel auf der Rückseite | |

**Werkzeug:** Multimeter (Polung und Spannung prüfen), Bohrer für die Trägerplatte, ein Rechner mit [PlatformIO](https://platformio.org) oder Docker zum Flashen.

**Optional:**

| Teil | Wann |
|---|---|
| Rahmen und Front aus **getöntem** Acryl, 3 mm, rauchgrau (30 bis 50 % Transmission), 2 bis 4 mm vor den LEDs | wenn das Bild gefällt. Getönt, nicht opal: Die Tönung lässt ausgeschaltete LEDs wie eine schwarze Fläche wirken und schluckt Reflexe; opales Acryl würde die 2,5-mm-Pixel verwaschen. |
| 2 × 74HCT245 | nur, wenn das Bild mit 3,3-V-Logik instabil ist (siehe 3.4) |

---

## 2. Mechanik

**Maße:** 2 Panels nebeneinander, 320 × 80 mm Leuchtfläche, Tiefe etwa 15 mm plus ESP32 dahinter. Trägerplatte 340 × 100 mm, also 1 cm Rand. Gewicht unter 500 g.

**Panels montieren:**

1. Panels so legen, dass die Flachbandbuchsen beider Panels auf derselben Seite liegen und die mit **IN** (oder "DATA IN", Pfeil hinein) beschriftete Buchse des linken Panels zum ESP32 zeigt. Jedes Panel hat IN und OUT; die Pfeile auf der Rückseite zeigen die Datenrichtung.
2. Die Panels stoßen direkt aneinander. Waveshare-Panels sind auf 160 mm gefräst, die Fuge ist bei 2,5 mm Pitch praktisch unsichtbar.
3. Auf Stahlblech: ansetzen, die Magnete halten. Auf Holz: durch die Platte in die M3-Gewinde der Panelrückseite schrauben (Lochabstand am Panel ausmessen, Löcher 3,2 mm).

**Rückseite:** ESP32 mittig hinter dem linken Panel, Dupont-Kabel unter 20 cm zum HUB75-Eingang. Klemmen und Netzteilkabel mit Klettband oder Kabelbindern fixieren. Das Netzteil gehört nicht ans Panel, es liegt hinter dem Möbel oder hängt am Kabel.

**Aufhängen:** zwei Schrauben, Schlüssellochbleche oder zwei Haken. Oder das Panel steht im Regal, dafür reicht ein Standfuß aus zwei Holzklötzen.

---

## 3. Strom und Verdrahtung

### 3.1 Stromversorgung

Vom Schraubadapter des Netzteils zwei Litzen zu den Wago-Klemmen (+ und −). Kein Kabel abschneiden, nichts löten.

Jedes Panel hat einen 4-poligen Stromstecker (VH3.96, 2 × VCC, 2 × GND) und bringt das passende Kabel mit. Beide Panelkabel parallel auf die Klemmen. **Polung prüfen:** Am Panel ist VCC/GND neben dem Stecker aufgedruckt, rot gehört auf VCC. Verpolung zerstört die Treiber.

ESP32: VIN an +5 V, GND an GND, an denselben Klemmen. Ohne gemeinsame Masse zwischen ESP32 und Panels funktioniert keine Datenleitung. Eine Sicherung auf der 5-V-Seite braucht es nicht: Das Netzteil hat Überstrom- und Übertemperaturschutz, die Last liegt weit unter dem Limit.

### 3.2 Strombedarf

Waveshare gibt pro Panel 5 V / 2,5 A, maximal 12 W an (alles weiß, volle Helligkeit). Das kommt hier nie vor: kein Weiß als Textfarbe, 10 bis 20 % der Pixel an, Helligkeit gedeckelt (`MAX_BRIGHTNESS 160` in der Firmware, der Server schickt tagsüber 90). Realistisch sind 0,3 bis 1 A für beide Panels plus 0,15 A für den ESP32. Ein 5-V/4-A-Netzteil reicht also, ein größeres läuft entspannter.

### 3.3 Datenleitung ESP32 → HUB75

Der ESP32 steuert die Panels direkt über 13 Signalleitungen plus Masse. Panel 1 OUT → Panel 2 IN mit dem beiliegenden Flachbandkabel.

**Einfachster Anschluss:** Das zweite beiliegende Flachbandkabel in Panel 1 IN stecken. In die freie Buchse am anderen Ende passen Dupont-Stecker (male) direkt, Rastermaß 2,54 mm. Das ist mechanisch sauberer als Dupont-Buchsen auf den Stiftleisten am Panel.

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

Also 14 Kabel. Beschriftete Kabel oder ein Foto helfen; vertauschte Farbleitungen sieht man sofort am Testbild (rot ist dann grün), vertauschte Adressleitungen ergeben verwürfelte Zeilen. GPIO 12 und 15 sind Strapping-Pins des ESP32; das ist bei dieser Bibliothek Standard und funktioniert, weil die Panel-Eingänge hochohmig sind.

### 3.4 Pegel: 3,3 V reicht in der Regel

HUB75-Panels puffern die Eingänge mit 74HC245 an 5 V. Deren Schaltschwelle liegt bei etwa 2,5 V; die 3,3 V des ESP32 werden bei kurzen Leitungen in der Praxis sauber erkannt. Waveshare zeigt in der Doku zu diesem Panel ESP32 und Raspberry Pi (beide 3,3-V-Logik) direkt am HUB75-Eingang, ohne Pegelwandler, mit genau der hier verwendeten Bibliothek.

Falls doch Flackern, Geisterbilder oder Farbrauschen auftreten, die sich mit `HUB75_CLKPHASE` und `HUB75_LATCH_BLANKING` (Abschnitt 4) nicht beheben lassen: zwei 74HCT245 (je 8 Kanäle, 5-V-Versorgung, Richtung A→B) zwischen ESP32 und Panel. Mit 4-Kanal-Puffern wie dem 74AHCT125 gehen zwei Chips für die 8 kritischsten Leitungen (CLK, LAT, OE, A bis D, R1).

### 3.5 USB und VIN

Nie gleichzeitig über USB und VIN versorgen. Zum Flashen per USB das VIN-Kabel abziehen; die Panels dürfen dabei über das Netzteil laufen, GND bleibt verbunden. Nach dem ersten Flashen geht alles per WLAN (OTA), sobald ein OTA-Passwort gesetzt ist (Abschnitt 4).

---

## 4. Firmware

PlatformIO, Board `esp32dev`, Arduino-Framework, Bibliothek ESP32-HUB75-MatrixPanel-DMA 3.0.14, Adafruit GFX (nur fürs Testbild), ArduinoOTA. WLAN, Server-URL und OTA-Passwort stehen in `include/secrets.h`, die nicht ins Repo gehört. Ein leeres OTA-Passwort schaltet OTA ab: Das Panel öffnet dann keinen Port und meldet sich nicht als `flightpanel.local`, geflasht wird per USB. Fürs Hochladen per WLAN kommt dasselbe Passwort noch einmal in `platformio_local.ini` (Vorlage: `platformio_local.ini.example`, ebenfalls nicht im Repo; ohne sie baut die Firmware trotzdem).

```bash
cd firmware
cp include/secrets.h.example include/secrets.h   # WLAN (2,4 GHz!), Server-URL, OTA-Passwort (leer = kein OTA)
pio run -t upload                                # erstes Mal per USB, VIN dabei abziehen
pio device monitor
cp platformio_local.ini.example platformio_local.ini   # nur für OTA: dasselbe Passwort eintragen
pio run -e esp32dev_ota -t upload                # danach per WLAN
```

Ohne lokales PlatformIO prüft `sh tools/check_firmware.sh`, ob die Firmware kompiliert (in Docker). Alle Versionen sind fest: Plattform und Bibliotheken in `platformio.ini` (die HUB75-Bibliothek per Commit), PlatformIO selbst in `firmware/platformio.lock`; beim Bauen kommt nichts ungefragt neu dazu.

**Ablauf:** Nach dem Einschalten ein Testbild (Abschnitt 6; andere Neustarts bleiben still), dann WLAN und, falls ein OTA-Passwort gesetzt ist, OTA samt mDNS-Name (`flightpanel.local`). Danach alle 2 s `GET /frame`, Header prüfen, Helligkeit übernehmen (gedeckelt bei `MAX_BRIGHTNESS`), 4096 Pixel schreiben, Puffer tauschen. Die Bibliothek frischt die Panels per DMA im Hintergrund auf, der Prozessor ist dabei kaum beteiligt. Ohne Server bleibt das letzte Bild stehen; nach 60 s leuchtet ein dunkelroter Punkt unten rechts, nach 10 min startet der ESP32 neu.

**Schnittstelle:** `GET /frame` liefert 12 292 Byte, `application/octet-stream`:

| Byte | Inhalt |
|---|---|
| 0 | Protokollversion (1) |
| 1 | Helligkeit 0 bis 255 |
| 2 | Breite (128) |
| 3 | Höhe (32) |
| 4 … | 128 × 32 × 3 Byte RGB, zeilenweise von oben links |

Kein ETag, kein Diff, kein Websocket: 6 KB/s im WLAN sind nichts, und TCP liefert immer vollständige Bilder.

**Stellschrauben in `include/config.h`**, falls das Testbild nicht stimmt:

| Symptom | Schalter |
|---|---|
| Rechte Hälfte zeigt den linken Bildteil | `SWAP_HALVES true` (oder Panels tauschen) |
| Pixel um eine Spalte verschoben, Geisterbild rechts neben hellen Pixeln | `HUB75_CLKPHASE true` |
| Leichtes Nachleuchten in dunklen Zeilen | `HUB75_LATCH_BLANKING 2` bis `4` |
| Nur jede zweite Zeilengruppe, Bild gestaucht | Panel ist kein 1/16-Scan |
| Farben vertauscht | R/G/B-Kabel vertauscht |

---

## 5. Zusammenbau Schritt für Schritt

**Vorbereiten, ohne Hardware:**

1. Server starten: `cp .env.example .env`, `docker compose up -d --build`, Vorschau unter `http://<server>:8080` (Mock-Modus mit Fantasieflügen).
2. Logos holen: `docker compose exec flightpanel python3 -m flightpanel.fetch_logos`.
3. Flughafen und `MOCK=0` in der `.env` einrichten, Standort und Sichtfeld per `panels set home …` (README, Abschnitt "Einrichten"), neu starten. Ein paar Tage `/display.json` beobachten: Wie viele Flüge, wie hoch, wie oft leer? Damit `RADIUS_KM`, `MAX_ALT_M` und das Sichtfeld eichen. Das sind Panel-Einstellungen und stehen in der Datenbank, nicht in der `.env`; gesetzt werden sie mit `docker compose exec flightpanel python3 -m flightpanel.panels set home RADIUS_KM=30 MAX_ALT_M=4000` und gelten nach `docker compose restart flightpanel`.
4. PlatformIO installieren (VS-Code-Erweiterung oder `pip install platformio`), `firmware/include/secrets.h` anlegen.

**Erstes Bild (ein Abend):**

5. Panels ansehen: IN/OUT-Pfeile, Stromstecker, VCC/GND-Aufdruck.
6. Netzteil einstecken, am Schraubadapter 5,0 bis 5,2 V messen. Litzen in die Klemmen, Netzteil wieder ausstecken.
7. Ein Panel: Stromkabel an die Klemmen (Polung!), Flachbandkabel in IN, 14 Dupont-Kabel zum ESP32 nach 3.3, GND verbinden.
8. ESP32 per USB flashen (`pio run -t upload`), VIN bleibt ab. Netzteil ein. Testbild: Panel rot, dann Rahmen, dann lesbare Schrift. Falls nicht: Tabelle in Abschnitt 4.
9. Zweites Panel: Strom parallel, Flachband von Panel 1 OUT zu Panel 2 IN. Testbild: links rot, rechts grün, Rahmen über beide, Diagonale ohne Knick.
10. Serieller Monitor: WLAN verbunden, erstes `/frame`. Das erste echte Bild.

**Montage (ein Nachmittag):**

11. Trägerplatte zuschneiden, Panels montieren, ESP32 und Klemmen befestigen, USB ab, VIN dran.
12. OTA testen (Passwort in `secrets.h` und `platformio_local.ini`, Abschnitt 4): `pio run -e esp32dev_ota -t upload`.
13. An die Wand oder ins Regal.

**Dauertest:** 24 h laufen lassen. Netzteil und Panelrückseite nach einer Stunde anfassen: handwarm. `/health` zeigt `esp32.fetches` hochzählen.

---

## 6. Testbild und Gesamttest

Das Testbild läuft nur nach dem Einschalten (Netzteil an). Nach einem Neustart zwischendurch, etwa dem nach 10 min ohne Daten oder einem OTA-Update, bleibt das Panel still; wer es noch einmal sehen will, schaltet das Netzteil kurz aus und wieder ein.

| Bild | Bedeutung |
|---|---|
| Linkes Panel rot, rechtes grün | Kette stimmt. Umgekehrt: `SWAP_HALVES` |
| Orangefarbener Rahmen 128 × 32, blaue Diagonale durchgehend | Geometrie und Scan stimmen |
| Rahmen an der Fuge unterbrochen | Panel 2 nicht erkannt: Flachband OUT→IN prüfen |
| Schriftzug "flightpanel" lesbar, oben links | Orientierung stimmt |
| Schrift gespiegelt oder auf dem Kopf | Panels drehen, nicht die Software |
| Bunter Schnee, Flackern | Dupont-Kabel prüfen (CLK, LAT, OE, GND), dann `HUB75_CLKPHASE` |
| Nur obere oder untere Hälfte jedes Panels | R2/G2/B2 oder R1/G1/B1 nicht gesteckt |

**Gesamttest:**

- `/frame.png` im Browser und das Panel nebeneinander: gleich?
- `/health`: `esp32.alive` ist `true`, `source.active` ist `adsblol`, `logos` ist gefüllt.
- Helligkeit: Tagsüber ist 90 in einem Wohnraum deutlich, abends 35. Anpassen mit `panels set home BRIGHTNESS_DAY=90 BRIGHTNESS_EVENING=35` und einem Neustart des Servers (README, Abschnitt "Einrichten"), ohne Flashen.
- Strom: Multimeter in die Plus-Leitung am Schraubadapter, bei normalem Bild. Erwartung: unter 1,5 A.

---

## 7. Betrieb

### Vor dem Umzug auf den Server

1. **Feste Adresse für den Server.** Der ESP32 findet ihn nur über `FRAME_URL` in `secrets.h`. Im Router eine feste DHCP-Zuweisung einrichten oder den Namen nehmen, den der Router auflöst (bei einer FRITZ!Box `http://<name>.fritz.box:8080/frame`). Sonst heißt eine neue IP: neu flashen.
2. **OTA-Passwort setzen:** `OTA_PASSWORD` in `secrets.h` und dasselbe Passwort in `firmware/platformio_local.ini` (beide nicht im Repo). Ohne Passwort läuft kein OTA, damit nicht jeder im WLAN die Firmware tauschen kann; das Panel ließe sich dann nur noch per USB flashen.
3. **Mitnehmen:** `.env` (Flughafen, Quellen, `TZ`), die Datenbank `data/flightpanel.db` (Standort, Sichtfeld, Album; als Kopie, siehe 7) und `data/logos/own/` (eigene Logos samt Originalen in `own/src/`). Den Rest holt der Server selbst.
4. **Nur im Heimnetz:** Port 8080 nicht nach außen freigeben. `/display.json` zeigt Richtung und Entfernung der Flugzeuge vom Fenster aus; daraus ließe sich der Standort ableiten.
5. **Updates per cron:** `sh tools/update.sh` wöchentlich, Zeile in der README unter "Betrieb auf dem Server". Der Nutzer des cron-Jobs braucht die Gruppe `docker`.
6. **Überwachen statt nachsehen:** ein Uptime-Monitor (Uptime Kuma o. ä.) auf `http://<server>:8080/ok`; der meldet sich nur, wenn Daten fehlen, das Panel nichts mehr holt oder der Code Fehler wirft.
7. **Sichern:** `data/` ins Backup des Servers: eigene Logos, Statistik und Sammelalbum über das ganze Jahr. Die laufende Datenbank nicht kopieren (was noch in `flightpanel.db-wal` steht, fehlt): Der Server legt jede Nacht eine stimmige Kopie an, `data/flightpanel.db.bak`, die gehört ins Backup. Eine Kopie zu einem bestimmten Zeitpunkt (vor dem Umzug, vor einem Versuch) zieht `docker compose exec flightpanel python3 -m flightpanel.panels backup /data/sicherung-$(date +%F).db`; sie liegt danach in `data/` (von Git ignoriert). Eine vorhandene Datei ersetzt der Befehl nur mit `--force`, darum das Datum im Namen. In Git Bash unter Windows `MSYS_NO_PATHCONV=1` davorsetzen, sonst macht die Shell aus `/data/…` einen Windows-Pfad und der Befehl scheitert mit `Fehler: unable to open database file`; in PowerShell steht `$(Get-Date -Format yyyy-MM-dd)` statt `$(date +%F)`. Die Datenbank gehört nicht in einen synchronisierten Ordner (OneDrive, Dropbox …), siehe README, "Betrieb auf dem Server".

**Fehlerbilder:**

| Symptom | Ursache | Was tun |
|---|---|---|
| Komplett dunkel, Netzteil-LED aus | Netzteil oder Steckverbindung | Stecker, Klemmen |
| Komplett dunkel, Netzteil an | Quiet Hours, oder keine eingeschaltete Karte hat etwas zu zeigen | `QUIET_HOURS`, `CARD_*` (`panels show home`), `/idle` |
| Letztes Bild steht, roter Punkt unten rechts | ESP32 erreicht den Server nicht | `/health`, Container-Logs, WLAN, `FRAME_URL` in `secrets.h` |
| Rechte Hälfte dunkel | Flachband zwischen den Panels | Stecker nachdrücken |
| Flackern, Farbrauschen nach Monaten | Dupont-Kabel gelockert | nachstecken; bei Dauerproblem Pegelwandler (3.4) |
| Anzeige eingefroren | ESP32 hängt | startet nach 10 min selbst neu |
| Keine Flüge, obwohl welche da sind | Quelle ausgefallen oder Filter zu eng | `/health`, `/issues`, `/display.json` → `nearby` |
| Graues Flugzeug statt Airline-Logo | kein quadratisches Logo gefunden | `/health` → `logos_missing`; Tochterbetrieb: Logo-Code der Marke in `data/airlines.json`; sonst Logo von Hand (README, "Airline-Logos") |

**Updates:** Server mit `sh tools/update.sh` (wöchentlich per cron, siehe oben); Cache, Logos und Statistik bleiben in `data/`. Neue Versionen von Paketen und Basis-Image kommen nur nach 7 Tagen Wartezeit ins Repo (README, "Abhängigkeiten mit Wartezeit"). Aussehen ändern heißt Python ändern und neu bauen, nie flashen. Die Firmware braucht nur neue Pins, ein neues WLAN oder eine neue Server-URL (`pio run -e esp32dev_ota -t upload`).

**Verbrauch:** Die beiden Panels ziehen auch mit schwarzem Bild etwa 0,3 bis 0,5 A (Treiber-ICs), also 2 bis 3 W, dazu ESP32 0,7 W und Netzteilverlust etwa 1 W: rund 35 kWh im Jahr. Die Anzeige selbst kostet zusätzlich 2 bis 5 W. Eine Nachtabschaltung per Smart-Plug spart etwa ein Drittel; der ESP32 bootet in 10 s und holt sich das Bild.

---

## 8. Warum so

| Entscheidung | Begründung |
|---|---|
| HUB75 P2.5 statt WS2812-Panels | 16-mal so viele Pixel pro Fläche: Logo und drei Zeilen Text passen gleichzeitig aufs Bild. Fertige Module ohne Bastelei. Preis: eigene Firmware mit DMA-Bibliothek und 14 Leitungen statt einer, beides Standard. |
| 2 × 64 × 32 statt 128 × 64 | 128 × 32 trägt genau das Layout mit Logo und drei Zeilen. 128 × 64 kostet das Doppelte und braucht die E-Leitung, ohne dass mehr aufs Bild müsste. Erweiterbar: zweite Reihe, `PANEL_CHAIN 4` mit virtuellem Panel in der Bibliothek, `HEIGHT=64`; die Layouts im Server rechnen dann allerdings noch mit 32 Pixeln Höhe. |
| Server rendert, ESP32 zeigt nur an | Jede Änderung am Aussehen ohne Flashen, Vorschau im Browser, die Firmware bleibt klein. |
| HTTP-Abruf, 12 KB alle 2 s | Kein Broker, kein Websocket; TCP garantiert vollständige Bilder. |
| Klassischer ESP32 | Auf ihm ist die HUB75-Bibliothek am reifsten (paralleles I2S-DMA). |
| Kein Pegelwandler | 3,3 V reichen an HUB75-Eingängen in der Praxis; Rückfalloption in 3.4. |
| Tischnetzteil statt eingebautem | Schutzkleinspannung im ganzen Eigenbau, nichts mit 230 V zu verdrahten. |
| Front getönt statt opal, und erst später | Opal verwäscht die 2,5-mm-Pixel; getönt wirkt ausgeschaltet wie eine schwarze Fläche. Erst sehen, ob das nackte Panel gefällt. |
| OTA und Watchdog | Das Panel hängt an der Wand, USB ist dann unerreichbar. |

**Die Stellen, die Sorgfalt verlangen:** Polung der Panel-Stromkabel (Aufdruck lesen, messen, dann stecken); die 14 Dupont-Kabel (mit Liste und Foto fünf Minuten, das Testbild zeigt jeden Fehler); Panels einzeln testen, bevor sie fest montiert sind; WLAN am Montageort.
