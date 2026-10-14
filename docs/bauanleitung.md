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

PlatformIO, Board `esp32dev`, Arduino-Framework, Bibliothek ESP32-HUB75-MatrixPanel-DMA 3.0.14, Adafruit GFX (Testbild und Text auf dem Panel), ArduinoOTA. WLAN, Server-Adresse und Token stehen nicht in der Firmware, sondern im Speicher des Panels (NVS); eingetragen werden sie über sein Einrichtungs-WLAN vom Handy aus. Ohne `secrets.h` (unten) passt ein Build für jedes Panel.

```bash
cd firmware
pio run -t upload      # per USB, VIN dabei abziehen
pio device monitor
```

Ohne lokales PlatformIO prüft `sh tools/check_firmware.sh`, ob die Firmware kompiliert (ohne `secrets.h` wie für Freunde), mit und ohne Update-Schlüssel, und lässt die Tests auf dem Rechner laufen (`pio test -e native`, Manifest der signierten Updates); alles in Docker. Alle Versionen sind fest: Plattform und Bibliotheken in `platformio.ini` (die HUB75-Bibliothek per Commit), PlatformIO selbst in `firmware/platformio.lock`; beim Bauen kommt nichts ungefragt neu dazu.

**Partitionstabelle:** `min_spiffs.csv`, zwei App-Bereiche zu 1,875 MB (vorher `default.csv` mit 1,25 MB): Platz für https, Einrichtungs-WLAN und Updates. Eine neue Tabelle kommt nur per USB aufs Panel. Ein Panel mit älterer Firmware also einmal per USB flashen; WLAN und Einstellungen im NVS bleiben dabei erhalten, danach geht OTA wie gewohnt.

**Einrichten:** Ein Panel ohne Einrichtung öffnet nach dem Start das WLAN `flightpanel-XXXX`; Name und Passwort (8 Ziffern, bei jedem Öffnen neu) stehen auf dem Panel. Mit dem Handy hinein, die Seite öffnet sich von selbst (sonst `http://192.168.4.1`). WLAN wählen (nur 2,4 GHz), Passwort eintippen, unter "Erweitert" die Server-Adresse: für den eigenen Server `http://<server>:8080`, über Cloudflare `https://<name>` mit Client-ID und Secret des Service-Tokens. "Speichern und neu starten", fertig.

- **Für Freunde:** Server und Token vor der Übergabe selbst eintragen, das WLAN leer lassen. Beim Freund öffnet das Panel nach dem Einstecken sofort das Einrichtungs-WLAN, er wählt nur noch sein WLAN. Das Secret zeigt die Seite nie an, es lässt sich nur überschreiben; eine neue Server-Adresse löscht das Token.
- **Später ändern:** Netzteil einstecken (oder EN drücken), während des Testbilds BOOT drücken und halten, bis "WLAN einrichten" auf dem Panel steht (3 s): volles Einrichtungs-WLAN mit "Erweitert" und "Zurücksetzen" (löscht alles, danach wie neu). BOOT schon beim Einstecken gedrückt startet den ESP32 im Flash-Modus, das Panel bleibt dunkel: einfach noch einmal. Ohne Eingabe schließt es nach 10 min.
- **WLAN weg:** Findet das Panel sein WLAN 5 min lang nicht (neuer Router), öffnet es das Einrichtungs-WLAN nur mit WLAN-Feldern; Server und Token lassen sich darin nicht ändern. Es versucht weiter das alte WLAN und schließt, sobald es wieder da ist, sonst nach 10 min (Neustart, dann von vorn). Ein vertipptes Passwort kostet so 5 min, oder BOOT wie oben.

**Prüfstand (freiwillig):** `cp include/secrets.h.example include/secrets.h` liefert Vorgaben für WLAN und Server, solange im Panel nichts gespeichert ist, und das OTA-Passwort. Ein leeres OTA-Passwort (und ein Build ohne `secrets.h`) schaltet OTA ab: Das Panel öffnet dann im WLAN keinen Port und meldet sich nicht als `flightpanel.local`. Fürs Hochladen per WLAN kommt dasselbe Passwort noch einmal in `platformio_local.ini` (Vorlage `platformio_local.ini.example`, nicht im Repo), dann `pio run -e esp32dev_ota -t upload`.

**https:** Die Firmware bettet `server/flightpanel/certs/roots.pem` ein (Wurzeln von Let's Encrypt, Google, SSL.com, Sectigo, dazu Reserve; erzeugt von `tools/make_roots.py`). Das Token geht nur über https hinaus, nie über http.

**Ablauf:** Nach dem Einschalten ein Testbild (Abschnitt 6; andere Neustarts bleiben still), dann WLAN und, falls ein OTA-Passwort gesetzt ist, OTA samt mDNS-Name (`flightpanel.local`). Danach alle 2 s `GET /panel/frame` mit dem letzten ETag: Ein unverändertes Bild kommt als 304 ohne Inhalt und bleibt stehen, ein neues wird geprüft, die Helligkeit übernommen (gedeckelt bei `MAX_BRIGHTNESS`), 4096 Pixel geschrieben, der Puffer getauscht. Die Bibliothek frischt die Panels per DMA im Hintergrund auf, der Prozessor ist dabei kaum beteiligt. Ohne Server bleibt das letzte Bild stehen; nach 60 s leuchtet ein dunkelroter Punkt unten rechts, nach 10 min startet der ESP32 neu. Hängt dagegen `loop()` selbst (WLAN-Stack, Treiber, ein Server, der mitten im Bild verstummt), setzt der Task-Watchdog den ESP32 nach 60 s zurück (`WDT_TIMEOUT_S`); Downloads und ein OTA-Upload füttern ihn mit jedem Paket. Zum Testen lässt `h` und Enter im seriellen Monitor die Schleife 70 s stehen (Abschnitt 6).

**Schnittstelle:** `GET /panel/frame` (auf dem LAN-Port auch `/frame`) liefert 12 292 Byte, `application/octet-stream`, dazu ein `ETag`; mit passendem `If-None-Match` antwortet der Server 304. Im Header `X-Panel` schickt das Panel Firmware-Version, Laufzeit, Empfang und den kleinsten freien Speicher mit, für die Diagnose am Server.

| Byte | Inhalt |
|---|---|
| 0 | Protokollversion (1) |
| 1 | Helligkeit 0 bis 255 |
| 2 | Breite (128) |
| 3 | Höhe (32) |
| 4 … | 128 × 32 × 3 Byte RGB, zeilenweise von oben links |

Kein Diff, kein Websocket: 6 KB/s im WLAN sind nichts, und TCP liefert immer vollständige Bilder; das ETag spart nur die unveränderten.

**Stellschrauben in `include/config.h`**, falls das Testbild nicht stimmt:

| Symptom | Schalter |
|---|---|
| Rechte Hälfte zeigt den linken Bildteil | `SWAP_HALVES true` (oder Panels tauschen) |
| Pixel um eine Spalte verschoben, Geisterbild rechts neben hellen Pixeln | `HUB75_CLKPHASE true` |
| Leichtes Nachleuchten in dunklen Zeilen | `HUB75_LATCH_BLANKING 2` bis `4` |
| Nur jede zweite Zeilengruppe, Bild gestaucht | Panel ist kein 1/16-Scan |
| Farben vertauscht | R/G/B-Kabel vertauscht |

**Signierte Updates.** Panels bei Freunden sind per OTA nicht erreichbar: Sie holen neue Firmware selbst vom Server und installieren nur, was mit dem eigenen Schlüssel signiert und neuer ist als ihre Version (`FW_VERSION`, die Bauzeit JJMMTTHHMM in UTC; die Konsole nennt sie beim Start). Eingebaut ist das nur mit `firmware/include/fw_pubkey.h`; ohne die Datei holt ein Panel nie Firmware vom Server.

- **Ablauf:** Nach dem ersten Bild und dann alle 6 h holt das Panel `/panel/firmware.txt`, über dieselbe Verbindung wie das Bild (bei https mit Token). Es prüft Form und Signatur (Format: `firmware/include/manifest.h`) und nimmt nur eine neuere Version. Dann lädt es `/panel/firmware.bin?v=<Version>` in den freien App-Bereich, rechnet dabei SHA-256 und vergleicht Größe und Hash, bevor das Image startbar wird; sonst bricht es ab, und das laufende bleibt. Danach startet es neu, still. Während des Downloads (rund eine Minute) bleibt das letzte Bild stehen.
- **Rollback:** Der vorgebaute Bootloader dieses Kerns kann es (geprüft: `CONFIG_BOOTLOADER_APP_ROLLBACK_ENABLE=y` in der sdkconfig des SDK; die Rollback-Teile stecken im Bootloader und in der OTA-Bibliothek). Ein neues Image startet unbestätigt, die Konsole zeigt `PENDING_VERIFY`, und bestätigt sich mit dem ersten Bild. Startet es vorher neu (Absturz, Watchdog, 10 min ohne Bild, auch Stecker raus), startet der Bootloader wieder das alte. Dieselbe Version versucht das Panel höchstens dreimal, im Abstand von 6 h; eine neuere jederzeit. Das gilt auch für Uploads per OTA: Auch sie müssen innerhalb von 10 min ein Bild holen.
- **Rettungsmodus:** Lehnt das Panel das Zertifikat des Servers 24 h lang ab, passt die eingebettete Vertrauensliste nicht mehr zu Cloudflare. Dann holt es, höchstens einmal pro Stunde, nur Manifest und Image ohne Zertifikatsprüfung und ohne Token und vertraut dabei allein Signatur, SHA-256 und steigender Version. Ein Release mit neuer `roots.pem` (`tools/make_roots.py`) holt es so ohne USB zurück. Die 24 h zählen über Neustarts hinweg, nach dem Ausschalten von vorn.

1. **Schlüssel, einmal**, außerhalb des Repos und außerhalb jedes synchronisierten Ordners (OneDrive, Dropbox …; `fw_release.sh` lehnt solche Pfade ab):
   ```bash
   mkdir -p ~/flightpanel-signing
   openssl genpkey -algorithm EC -pkeyopt ec_paramgen_curve:P-256 -aes-256-cbc -out ~/flightpanel-signing/fw_signing.pem
   openssl pkey -in ~/flightpanel-signing/fw_signing.pem -pubout
   ```
   openssl fragt nach einer Passphrase; ohne sie nimmt `fw_release.sh` den Schlüssel nicht. Die Ausgabe des letzten Befehls kommt in `firmware/include/fw_pubkey.h` (Vorlage `fw_pubkey.h.example`, nicht im Repo); gibt es die Datei, baut die Firmware die Update-Prüfung ein. Sicherung offline: die verschlüsselte Schlüsseldatei auf einen USB-Stick, die Passphrase getrennt davon. Ist der Schlüssel weg, kommt neue Firmware nur noch per USB aufs Panel.
2. **Release:** `sh tools/fw_release.sh ~/flightpanel-signing/fw_signing.pem` in einem eigenen Terminal. Es baut in Docker ohne `secrets.h` (die Images sind öffentlich), schreibt das Manifest, fragt nach der Passphrase, signiert, prüft die Signatur gegen `fw_pubkey.h` und legt `firmware.txt` und `firmware.bin` nach `data/firmware/canary/`. `--dry-run` baut nur und zeigt das Manifest, ohne Schlüssel.
3. **canary, dann stable:** Das eigene Panel steht auf `canary` und bekommt das Release zuerst. Läuft es, beide Dateien nach `data/firmware/stable/` kopieren, erst `firmware.bin`, dann `firmware.txt`; die anderen Panels folgen.

Signieren ist Sache eines Menschen: KI-Agenten führen `fw_release.sh` nie aus. Das Skript bricht ohne Terminal und in einer Claude-Code-Sitzung ab, die Passphrase tippt der Besitzer des Schlüssels.

**Prüfstand für Updates**, bevor ein Freund ein Panel bekommt: das eigene Panel per USB mit dem eigenen `fw_pubkey.h` flashen, seriellen Monitor offen, Kanal `canary` (`docker compose exec flightpanel python3 -m flightpanel.panels channel home canary`). Die Konsole meldet jeden Schritt mit `Update:`.

1. **Gültig:** Release bauen. Das Panel lädt, startet neu, zeigt `PENDING_VERIFY` und nach dem ersten Bild `bestätigt`; ein weiterer Neustart bleibt bei der neuen Version (`VALID`).
2. **Manipuliert:** eine Ziffer in der zweiten Zeile von `data/firmware/canary/firmware.txt` ändern (`Signatur ungültig`) oder ein Byte in `firmware.bin` (`SHA-256 stimmt nicht`). Nichts wird installiert.
3. **CRLF:** `firmware.txt` mit CRLF speichern (`sed -i 's/$/\r/'`): `Manifest abgelehnt: unerlaubtes Zeichen`.
4. **Älter:** die Dateien eines früheren Release nach `canary` legen: `Update: keins`.
5. **Rollback:** ein Release, das nie ein Bild holen kann (vorübergehend `PROTO_VERSION 99` in `config.h`, danach zurück). Das Panel installiert es, zeigt `PENDING_VERIFY`, startet nach 10 min ohne Bild neu und läuft wieder mit der alten Version (`Verworfenes Image`, `kam zurück`). Es versucht dieselbe Version danach höchstens zweimal, im Abstand von 6 h; zum Aufräumen ein gutes, neueres Release bauen.
6. **Rettungsmodus** (Panel über https): `server/flightpanel/certs/roots.pem` vorübergehend durch eine Liste ersetzen, die nicht zu Cloudflare passt (nicht einchecken), und mit kurzer Schwelle per USB flashen: `PLATFORMIO_BUILD_FLAGS="-DRESCUE_AFTER_MS=300000" pio run -t upload` (5 min). Liste zurück, danach ein Release bauen und nach `stable` kopieren: Ohne Token kommt jede Anfrage als Rettungskanal an und bekommt `stable`, darum nur, solange noch kein Freund ein Panel hat. Das Panel lehnt das Zertifikat ab, meldet nach 5 min `Rettungsmodus`, holt das Release ohne Prüfung, startet neu und holt wieder Bilder.

---

## 5. Zusammenbau Schritt für Schritt

**Vorbereiten, ohne Hardware:**

1. Server starten: `cp .env.example .env`, `docker compose up -d --build`, Vorschau unter `http://<server>:8080` (Mock-Modus mit Fantasieflügen).
2. Logos holen: `docker compose exec flightpanel python3 -m flightpanel.fetch_logos`.
3. Flughafen und `MOCK=0` in der `.env` einrichten, Standort und Sichtfeld per `panels set home …` (README, Abschnitt "Einrichten"), neu starten. Ein paar Tage `/display.json` beobachten: Wie viele Flüge, wie hoch, wie oft leer? Damit `RADIUS_KM`, `MAX_ALT_M` und das Sichtfeld eichen. Das sind Panel-Einstellungen und stehen in der Datenbank, nicht in der `.env`; gesetzt werden sie mit `docker compose exec flightpanel python3 -m flightpanel.panels set home RADIUS_KM=30 MAX_ALT_M=4000` und gelten ohne Neustart: Der laufende Server übernimmt sie spätestens nach einer Minute.
4. PlatformIO installieren (VS-Code-Erweiterung oder `pip install platformio`); `firmware/include/secrets.h` nur für OTA (Abschnitt 4).

**Erstes Bild (ein Abend):**

5. Panels ansehen: IN/OUT-Pfeile, Stromstecker, VCC/GND-Aufdruck.
6. Netzteil einstecken, am Schraubadapter 5,0 bis 5,2 V messen. Litzen in die Klemmen, Netzteil wieder ausstecken.
7. Ein Panel: Stromkabel an die Klemmen (Polung!), Flachbandkabel in IN, 14 Dupont-Kabel zum ESP32 nach 3.3, GND verbinden.
8. ESP32 per USB flashen (`pio run -t upload`), VIN bleibt ab. Netzteil ein. Testbild: Panel rot, dann Rahmen, dann lesbare Schrift. Falls nicht: Tabelle in Abschnitt 4.
9. Zweites Panel: Strom parallel, Flachband von Panel 1 OUT zu Panel 2 IN. Testbild: links rot, rechts grün, Rahmen über beide, Diagonale ohne Knick.
10. Das Panel zeigt "WLAN einrichten": mit dem Handy einrichten (Abschnitt 4). Serieller Monitor: WLAN verbunden, erstes Bild.

**Montage (ein Nachmittag):**

11. Trägerplatte zuschneiden, Panels montieren, ESP32 und Klemmen befestigen, USB ab, VIN dran.
12. OTA testen (Passwort in `secrets.h` und `platformio_local.ini`, Abschnitt 4): `pio run -e esp32dev_ota -t upload`.
13. An die Wand oder ins Regal.

**Dauertest:** 24 h laufen lassen. Netzteil und Panelrückseite nach einer Stunde anfassen: handwarm. `/health` zeigt `esp32.fetches` hochzählen.

---

## 6. Testbild und Gesamttest

Das Testbild läuft nur nach dem Einschalten (Netzteil an). Nach einem Neustart zwischendurch, etwa dem nach 10 min ohne Daten, einem OTA-Update oder einem Watchdog-Reset, bleibt das Panel still; wer es noch einmal sehen will, schaltet das Netzteil kurz aus und wieder ein.

| Bild | Bedeutung |
|---|---|
| Linkes Panel rot, rechtes grün | Kette stimmt. Umgekehrt: `SWAP_HALVES` |
| Orangefarbener Rahmen 128 × 32, blaue Diagonale durchgehend | Geometrie und Scan stimmen |
| Rahmen an der Fuge unterbrochen | Panel 2 nicht erkannt: Flachband OUT→IN prüfen |
| Schriftzug "flightpanel" lesbar, oben links | Orientierung stimmt |
| Schrift gespiegelt oder auf dem Kopf | Panels drehen, nicht die Software |
| Bunter Schnee, Flackern | Dupont-Kabel prüfen (CLK, LAT, OE, GND), dann `HUB75_CLKPHASE` |
| Nur obere oder untere Hälfte jedes Panels | R2/G2/B2 oder R1/G1/B1 nicht gesteckt |
| Rechtes Panel blau statt grün, Schrift rosa statt orange | Panel mit Farbfolge R-B-G (kommt je nach Charge vor): am ESP32 G1 und B1 tauschen (Kabel 2 an G27, Kabel 3 an G26), ebenso G2 und B2 (Kabel 6 an G13, Kabel 7 an G12) |

**Prüfstand beim ersten Einschalten** (ESP32 per USB, `pio device monitor`, 115200 Baud). Die Konsole nennt mit `Reset-Grund N`, warum der ESP32 gestartet ist: 1 Einschalten, 3 Software-Neustart (10-Minuten-Regel, OTA), 4 Absturz, 5 bis 7 Watchdog (6 = Task-Watchdog), 9 Unterspannung.

1. **Einschalten:** Strom weg und wieder da; am Prüfstand USB-Stecker ziehen und stecken, die Panels dürfen am Netzteil bleiben (3.5). Die Konsole zeigt `Reset-Grund 1 (Einschalten)`, danach läuft das Testbild.
2. **EN-Taste, Flashen per USB:** Beide setzen den Chip über die EN-Leitung zurück, je nach Board auch das Öffnen des Monitors (DTR/RTS). Der klassische ESP32 meldet das wahrscheinlich ebenfalls als `1`; dann läuft das Testbild auch hier. Am Prüfstand bestätigen: Kommt eine andere Nummer, bleibt das Testbild bei EN aus.
3. **OTA aus:** `OTA_PASSWORD` in `secrets.h` leer lassen, per USB flashen. Die Konsole sagt `OTA aus (OTA_PASSWORD leer)`, `flightpanel.local` meldet sich nicht.
4. **OTA an:** Passwort in `secrets.h` und dasselbe in `platformio_local.ini` (Vorlage dort, Abschnitt 4), per USB flashen, dann `pio run -e esp32dev_ota -t upload`. Das Panel wird dunkel (`OTA start`), die Konsole meldet `OTA fertig`, der ESP32 startet neu: `Reset-Grund 3`, kein Testbild. Mit einem anderen Passwort in `platformio_local.ini` scheitert der Upload an der Anmeldung (`OTA Fehler 0`), das Panel läuft weiter.
5. **Watchdog:** `h` und Enter im seriellen Monitor. Die Konsole meldet `Watchdog-Test: loop() steht jetzt 70 s still` und zählt Sekunden, das Bild bleibt stehen. Bei etwa 60 s steht `Task watchdog got triggered ... loopTask` da und der ESP32 startet neu: `Reset-Grund 6`, kein Testbild, nach dem WLAN-Aufbau wieder das normale Bild. Zählt die Konsole bis 70, greift der Watchdog nicht. Beim Start meldet sie `Watchdog: 60 s auf loop()`; steht dort `FEHLER`, ist `loop()` nicht überwacht.

**Gesamttest:**

- `/frame.png` im Browser und das Panel nebeneinander: gleich?
- `/health`: `esp32.alive` ist `true`, `source.active` ist `adsblol`, `logos` ist gefüllt.
- Helligkeit: Tagsüber ist 90 in einem Wohnraum deutlich, abends 35. Anpassen mit `panels set home BRIGHTNESS_DAY=90 BRIGHTNESS_EVENING=35`; der Server übernimmt es spätestens nach einer Minute (README, Abschnitt "Einrichten"), ohne Flashen.
- Strom: Multimeter in die Plus-Leitung am Schraubadapter, bei normalem Bild. Erwartung: unter 1,5 A.

---

## 7. Betrieb

### Vor dem Umzug auf den Server

1. **Feste Adresse für den Server.** Der ESP32 findet ihn nur über die Server-Adresse aus dem Einrichtungs-WLAN. Im Router eine feste DHCP-Zuweisung einrichten oder den Namen nehmen, den der Router auflöst (bei einer FRITZ!Box `http://<name>.fritz.box:8080`). Sonst heißt eine neue IP: BOOT nach dem Einschalten halten und die neue Adresse eintragen (Abschnitt 4). Der Server antwortet nur unter IP-Adressen und Namen aus dem LAN (ohne Punkt, `*.local`, `*.home.arpa`, `*.internal`). Steht als Server-Adresse ein Name wie `<name>.fritz.box` im Panel, gehört derselbe Name auch in die `.env` des Servers: `LAN_HOSTS=<name>.fritz.box`. Sonst bekommt das Panel 421 statt eines Bildes.
2. **OTA-Passwort setzen:** `OTA_PASSWORD` in `secrets.h` und dasselbe Passwort in `firmware/platformio_local.ini` (beide nicht im Repo). Ohne Passwort läuft kein OTA, damit nicht jeder im WLAN die Firmware tauschen kann; das Panel ließe sich dann nur noch per USB flashen.
3. **Mitnehmen:** `.env` (Flughafen, Quellen, `TZ`), die Datenbank `data/flightpanel.db` (Standort, Sichtfeld, Album; als Kopie, siehe 7) und `data/logos/own/` (eigene Logos samt Originalen in `own/src/`). Den Rest holt der Server selbst. Lief der alte Rechner noch mit einer Fassung vor Oktober 2026, gibt es dort keine Datenbank, sondern JSON-Dateien: dann den Server dort anhalten und `data/stats.json` (Album und Statistik), `data/cache.json`, `data/issues.json`, `data/tle.txt` und `data/logos/.missing.json` mitnehmen. Der erste Start übernimmt sie einmalig, samt Standort und Sichtfeld aus der `.env` (Log: "Aus früheren Fassungen übernommen"), und benennt sie in `*.imported` um; danach die Panel-Zeilen aus der `.env` löschen.
4. **Nur im Heimnetz:** Port 8080 nicht nach außen freigeben. `/display.json` zeigt Richtung und Entfernung der Flugzeuge vom Fenster aus; daraus ließe sich der Standort ableiten.
5. **Updates per cron:** `sh tools/update.sh` wöchentlich, Zeile in der README unter "Betrieb auf dem Server". Der Nutzer des cron-Jobs braucht die Gruppe `docker`.
6. **Überwachen statt nachsehen:** ein Uptime-Monitor (Uptime Kuma o. ä.) auf `http://<server>:8080/ok`; der meldet sich nur, wenn Daten fehlen, das Panel nichts mehr holt oder der Code Fehler wirft.
7. **Sichern:** `data/` ins Backup des Servers: eigene Logos, Statistik und Sammelalbum über das ganze Jahr. Die laufende Datenbank nicht kopieren (was noch in `flightpanel.db-wal` steht, fehlt): Der Server legt jede Nacht eine stimmige Kopie an, `data/flightpanel.db.bak`, die gehört ins Backup. Eine Kopie zu einem bestimmten Zeitpunkt (vor dem Umzug, vor einem Versuch) zieht `docker compose exec flightpanel python3 -m flightpanel.panels backup /data/sicherung-$(date +%F).db`; sie liegt danach in `data/` (von Git ignoriert). Eine vorhandene Datei ersetzt der Befehl nur mit `--force`, darum das Datum im Namen. In Git Bash unter Windows `MSYS_NO_PATHCONV=1` davorsetzen, sonst macht die Shell aus `/data/…` einen Windows-Pfad und der Befehl scheitert mit `Fehler: unable to open database file`; in PowerShell steht `$(Get-Date -Format yyyy-MM-dd)` statt `$(date +%F)`. Die Datenbank gehört nicht in einen synchronisierten Ordner (OneDrive, Dropbox …), siehe README, "Betrieb auf dem Server".

**Fehlerbilder:**

| Symptom | Ursache | Was tun |
|---|---|---|
| Komplett dunkel, Netzteil-LED aus | Netzteil oder Steckverbindung | Stecker, Klemmen |
| Komplett dunkel, Netzteil an | Quiet Hours, oder keine eingeschaltete Karte hat etwas zu zeigen | `QUIET_HOURS`, `CARD_*` (`panels show home`), `/idle` |
| Letztes Bild steht, roter Punkt unten rechts | ESP32 erreicht den Server nicht | `/health`, Container-Logs, WLAN, Server-Adresse (Abschnitt 4); bei https der serielle Monitor (`TLS-Fehler`) |
| Panel zeigt "WLAN einrichten" | nicht eingerichtet, oder das WLAN ist seit 5 min weg | Abschnitt 4; ohne Eingabe schließt es nach 10 min und versucht das alte WLAN weiter |
| Rechte Hälfte dunkel | Flachband zwischen den Panels | Stecker nachdrücken |
| Flackern, Farbrauschen nach Monaten | Dupont-Kabel gelockert | nachstecken; bei Dauerproblem Pegelwandler (3.4) |
| Anzeige eingefroren | ESP32 hängt in `loop()` | setzt sich nach 60 s selbst zurück (Task-Watchdog, im Monitor `Reset-Grund 6`). Kommen nur keine Bilder vom Server, startet er nach 10 min neu |
| Keine Flüge, obwohl welche da sind | Quelle ausgefallen oder Filter zu eng | `/health`, `/issues`, `/display.json` → `nearby` |
| Graues Flugzeug statt Airline-Logo | kein quadratisches Logo gefunden | `/health` → `logos_missing`; Tochterbetrieb: Logo-Code der Marke in `data/airlines.json`; sonst Logo von Hand (README, "Airline-Logos") |

**Updates:** Server mit `sh tools/update.sh` (wöchentlich per cron, siehe oben); Cache, Logos und Statistik bleiben in `data/`. Neue Versionen von Paketen und Basis-Image kommen nur nach 7 Tagen Wartezeit ins Repo (README, "Abhängigkeiten mit Wartezeit"). Aussehen ändern heißt Python ändern und neu bauen, nie flashen. Neu flashen nur für neue Pins oder eine neue Firmware (`pio run -e esp32dev_ota -t upload`); WLAN und Server ändert das Einrichtungs-WLAN.

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
| Einrichtungs-WLAN statt `secrets.h` | Ein Build für alle Panels; WLAN und Server ändert das Handy, nicht der Compiler. Wer das Panel geschenkt bekommt, trägt nur sein WLAN ein. |

**Die Stellen, die Sorgfalt verlangen:** Polung der Panel-Stromkabel (Aufdruck lesen, messen, dann stecken); die 14 Dupont-Kabel (mit Liste und Foto fünf Minuten, das Testbild zeigt jeden Fehler); Panels einzeln testen, bevor sie fest montiert sind; WLAN am Montageort.
