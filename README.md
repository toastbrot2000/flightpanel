# flightpanel

**Ein LED-Panel fürs Fenster, das zeigt, welches Flugzeug man gerade draußen sieht:** Airline-Logo, Route, Typ, Höhe und Minuten bis zur Landung. Ist keins zu sehen, wechseln Karten mit Abflügen und Landungen am Flughafen, dem nächsten Flieger, Radar, Wetter, ISS und ein paar Spielereien.

*An LED panel for the window that shows which plane you are looking at. Documentation in German.*

> [!WARNING]
> **Ein komplett vibe-codetes Wochenendprojekt.** Jede Zeile Code und Doku hier hat eine KI geschrieben (Claude). Keine Wartungszusage, keine Garantie. Nachbauen, Abschauen und Weitermachen ausdrücklich erwünscht, auf eigene Gefahr.
>
> *Fully vibe-coded weekend project: every line of code and documentation was written by an AI (Claude). No support, no warranty. Rebuilding, borrowing and building on it is explicitly welcome, at your own risk.*

![Sechs Beispiele vom Panel: Landung mit Logo und Himmelspunkten, Abflüge am BER, nächster Flieger, Radar, Abflug ohne Logo mit Platzhalter, ISS-Überflug](docs/images/overview.png)

Zwei HUB75-Panels P2.5 (64 × 32) nebeneinander ergeben 128 × 32 Pixel auf 32 × 8 cm. Ein ESP32 steuert sie an, ein kleiner Server (Docker, etwa im Homelab) macht den Rest. Teile, Verdrahtung und Zusammenbau: [docs/bauanleitung.md](docs/bauanleitung.md). Alle Bildschirme mit Bildern und Rangfolge: [docs/screens.md](docs/screens.md).

## Stand

- **Läuft:** der Server mit echten Daten im Docker-Container, am Fenster gegen den Himmel und Flightradar24 geprüft. Landefolge, Minuten bis zur Landung und Sichtfeld stimmen dort gut.
- **Ungetestet:** die Firmware auf echter Hardware (sie kompiliert) und ein Dauerbetrieb über Wochen.
- **Ortsgebunden:** entwickelt in Berlin für den Flughafen BER. Alles Ortsabhängige ist einstellbar.

## So funktioniert es

```
adsb.lol (Ersatz: OpenSky) ─┐
adsb.im, adsbdb (Routen) ───┤
OpenSky-Flugspur (Herkunft) ┼─> Server (Python, Docker) ──HTTP /frame──> ESP32 ──HUB75──> 2 Panels
METAR, Celestrak (ISS) ─────┤   ruft alle 20 s ab, rendert    12 KB alle 2 s     DMA-Refresh
Logo-CDNs ──────────────────┘   bei jedem Abruf ein Bild
```

Der Server rendert das komplette Bild. Der ESP32 holt es nur ab und schreibt es in die Panels; er kennt weder Flüge noch Schriften noch Logos. Jede Änderung am Aussehen passiert in Python, mit Vorschau im Browser und ohne Flashen.

## Schnellstart (ohne Hardware, ohne Netz)

```bash
cp .env.example .env            # MOCK=1 ist schon gesetzt: Fantasieflüge
docker compose up -d --build
open http://localhost:8080      # Vorschau, wechselt alle 2 s
```

Auf Linux läuft der Container ohne Rechte, als `PUID:PGID` aus `.env` (ohne Angabe 1000:1000), und muss `./data` beschreiben dürfen: In `.env` `PUID` und `PGID` auf den Besitzer von `data/` setzen (`id -u`, `id -g`; die Zeilen stehen als Kommentar schon in `.env.example`). Unter Docker Desktop (Windows, macOS) passt es von selbst.

Die Vorschauseite hat Links für die Layouts und jede Leerlauf-Karte; `/frame.png?card=radar&scale=8` geht auch direkt.

## Einrichten

Serverweites steht in `.env` (nicht im Repo; jede Einstellung ist in [.env.example](.env.example) erklärt): Heimatflughafen, Datenquellen, Logos, Betrieb. Für echte Daten `MOCK=0`. Alles, was zum einzelnen Panel gehört (Standort, Sichtfeld, Anzeige, Helligkeit, Ruhezeit, Karten), liegt in `data/flightpanel.db` und wird nach dem Start mit einem Befehl gesetzt.

**1. Heimatflughafen** (`.env`). `LOCAL_AIRPORT_IATA` genügt: Position, Höhe und METAR-Station kommen aus `data/airports.json`. Dazu `RUNWAY_COURSE`, der Kurs im Endanflug einer Landerichtung (BER 25: 249°, MUC 26: 263°), und `CURFEW_HOURS` für das Nachtflugverbot. Für einen Flughafen, der nicht in der Liste steht, `AIRPORT_LAT`, `AIRPORT_LON`, `AIRPORT_ALT_M` und `METAR_STATION` setzen.

**2. Starten.** `docker compose up -d --build`. Beim ersten Start entsteht das Panel `home`; eine ältere `.env` mit Standort und Sichtfeld wird dabei einmalig übernommen; diese Zeilen danach aus der `.env` löschen, sie gelten nicht mehr, und der Server warnt bei jedem Start vor ihnen. Auf Linux den Container als den Besitzer von `data/` laufen lassen: vor dem ersten Start in `.env` `PUID` und `PGID` setzen (`id -u`, `id -g`). Das macht ein `chown` überflüssig, und `git pull` (`tools/update.sh`) darf weiter in `data/` schreiben, was nach einem `chown` auf einen anderen Benutzer nicht ginge. Ohne compose (`docker run`) gehört `--user UID:GID` dazu oder `sudo chown -R 1000:1000 data`. Eine ältere Installation, deren Container als root lief, hat in `data/` noch Dateien von root: einmal `sudo chown -R "$(id -u):$(id -g)" data`. Unter Docker Desktop passt es von selbst. Sonst bricht der Start ab, und das Log nennt den Ordner, den Benutzer und was zu tun ist.

**3. Standort und Sichtfeld.**

```bash
docker compose exec flightpanel python3 -m flightpanel.panels set home HOME_LAT=52.52 HOME_LON=13.40 HOME_ALT_M=40
```

```bash
docker compose exec flightpanel python3 -m flightpanel.panels set home VIEW_FROM=110 VIEW_TO=190 VIEW_MIN_ELEV=0.5 VIEW_MAX_KM=60 VIEW_ONLY=1
```

`HOME_LAT`, `HOME_LON` sind Dezimalgrad, `HOME_ALT_M` die Augenhöhe am Fenster über dem Meer. Angezeigt wird, was höchstens `RADIUS_KM` weit weg und `MAX_ALT_M` hoch ist. Das Sichtfeld: Richtung im Uhrzeigersinn von `VIEW_FROM` bis `VIEW_TO` (0 = Nord, 90 = Ost), Mindesthöhe über dem Horizont `VIEW_MIN_ELEV` (Häuser, Bäume), Obergrenze `VIEW_MAX_ELEV` (Balkon darüber), Sichtweite `VIEW_MAX_KM`; `VIEW_ONLY=1` zeigt nur, was im Sichtfeld ist. Eichen: am Fenster ein Flugzeug suchen und in `/display.json` → `nearby` dessen Richtung (`az`) und Höhenwinkel (`elev`) ablesen. `… panels keys` zeigt alle Panel-Einstellungen mit Default und Erklärung, `… panels show home` die gesetzten; sie gelten nach `docker compose restart flightpanel`.

**4. Karten und Ruhe.** Ebenso per `set home`: `CARD_<ID>=0` schaltet eine Leerlauf-Karte ab, z. B. `CARD_PIGEON=0`. `CALM=1` ist der ruhige Modus: nichts blinkt außer einem Notfall, Infoseiten wechseln alle 10 s statt 4 s, unten stehen nur Landezeit und Höhe/Tempo. Jede dieser Einzelheiten ist auch einzeln einstellbar (`PAGE_SECONDS`, `FLIGHT_SECONDS`, `INFO_PAGES`, `BLINK`, `IDLE_SECONDS`), ebenso Helligkeit und Ruhezeit (`BRIGHTNESS_DAY`, `BRIGHTNESS_EVENING`, `DAY_HOURS`, `QUIET_HOURS`).

Dann einmal die Logos holen: `docker compose exec flightpanel python3 -m flightpanel.fetch_logos`. Unsinnige Werte brechen den Start ab (`.env`) oder werden vom Befehl abgelehnt (`panels set`), jeweils mit dem Namen der Einstellung; was schon in der Datenbank steht und nach einem Update nicht mehr gilt, übergeht der Server mit dem Default und meldet es in Log und `/issues`. Verdächtiges, etwa Koordinaten, die nicht zum Flughafen passen, steht als Warnung im Log. Die Werte unter `/health` zeigen, was wirklich gilt.

## Was angezeigt wird

Bei jedem Abruf ordnet der Server alle Flugzeuge im Radius:

1. **Sichtfeld zuerst.** Zählt auch, wer bis zum nächsten Abruf hineinfliegt (aus Tempo, Kurs und Steigrate weitergerechnet), sonst wären Anflüge schon durch, bevor sie auf dem Panel stehen.
2. **Wer hier landet oder startet, vor Überflügen, jeweils Airlines zuerst.** Sind mehr im Sichtfeld, als `MAX_FLIGHTS` zulässt, gilt: Airlines, die hier landen oder starten; Privatflieger, die das tun; Airline-Überflüge; Privat-Überflüge. Linien-, Charter- und Frachtflüge zählen als Airline; Privatflieger sind auch Geschäftsflugzeuge, Hubschrauber, Polizei und Militär (`"airline": false` in `data/airlines.json`). Landen und Starten erkennt der Server an der Landefolge, an der Route oder, bei Airlines ohne Route, an Höhe und Steigrate. Hubschrauber und Kleinflugzeuge mit Kennzeichen landen fast nie am BER und zählen als Überflug. Privatflieger stehen in der Rotation wie Airlines, statt des Logos mit Symbol (Kleinflugzeug, Hubschrauber); `SHOW_OTHER=0` blendet sie aus.
3. **Landefolge, dann kürzeste Sichtlinie.** Wer als nächstes landet, kommt zuerst. Die Landefolge schätzt der Server aus der Reststrecke bis zur Piste: im Endanflug die Entfernung, im Gegenanflug weiter bis zum Eindrehen und den ganzen Endanflug zurück, im Queranflug quer zur Achse plus Rest. Die Landerichtung erkennt er am Verkehr in Platznähe.

Bis zu `MAX_FLIGHTS` Flüge wechseln sich ab, jeder mit allen Infoseiten, mindestens `FLIGHT_SECONDS`.

![Landung mit Logo: Pfeil und Airline, Route und Typ, "landet in ~4 min"; darunter drei Himmelspunkte](docs/images/flight-logo.png)

**Layouts** (`LAYOUT`): **C** mit Logo links (32 × 32), rechts drei Zeilen; **A** ohne Logo über die ganze Breite; **B** zwei Flüge, je eine Zeile. Ohne Logo steht in C ein graues Symbol an seiner Stelle, damit Text und Himmelspunkte im Wechsel stehen bleiben: ein Flugzeug bei Airlines, ein Kleinflugzeug oder ein Hubschrauber bei Privatfliegern. Zeile 1: Pfeil (↓ Landung, ↑ Start, → Überflug) und Airline. Zeile 2: Route, sonst was der Flug ist (Luftrettung, Polizei, Kleinflugzeug: aus `airlines.json`, ADS-B-Kategorie oder Kennzeichen), sonst `?→BER` oder `BER→?`; rechts der Typ. Zeile 3 wechselt (`INFO_PAGES`): "landet in ~7 min", Höhe und Tempo, Entfernung und Kurs, Rufzeichen. Farben: Der Pfeil zeigt den Status (Landung amber, Start grün, Überflug blau), die Airline steht neutral in Grau, damit sie sich vom Logo abhebt; kein Weiß.

![Abflug ohne Logo: graues Flugzeug als Platzhalter, Eurowings, BER→PMI, A319; die Himmelspunkte stehen wie beim Flug mit Logo](docs/images/flight-nologo.png)

**Himmelspunkte:** Sind mindestens zwei gezeigte Flugzeuge im Sichtfeld, steht unter dem (dann 28 × 28 kleinen) Logo eine Punktreihe, ein Punkt pro Flug in Lesereihenfolge: links der, der zuerst landet. Der Punkt des gezeigten Flugs leuchtet. Seltene Typen und hoher Besuch (siehe Karte `rare`) haben einen goldenen Punkt statt der Statusfarbe. In dieser Reihenfolge geht das Panel die Flüge durch, und jeder behält seine volle Zeit, auch wenn vor ihm einer landet oder das Sichtfeld verlässt.

**Grundsätze:** Nichts steht doppelt (das Rufzeichen fällt aus dem Wechsel, wenn es oben schon steht). Text wird nie so gekürzt, dass sich der Sinn ändert: erst kleinere Schrift, dann eine kürzere Fassung. Alle Pixel werden genutzt; Text am Rand sitzt auf der ersten oder letzten Spalte. Privatflieger heißen nach ihrem Kennzeichen (D-EHGB), Rettungshubschrauber nach ihrer Nummer (Christoph 31); Halternamen kommen nie aufs Panel. Lieber ein ehrliches `?` als eine falsche Herkunft.

**Leerlauf:** Ohne Flug im Sichtfeld wechseln Karten alle `IDLE_SECONDS` in festen Stufen: Verkehr am Platz (Abflüge, Landungen, nächster Flieger) vor Informationen (Radar, Landerichtung, Wetter, Statistik, ISS, Sonne) vor Spielereien, die nur kommen, wenn am Flughafen seit 15 min nichts los war. Ein Notfall-Squawk (7700, 7600, 7500) geht allem vor. Um Mitternacht an Neujahr gehört das Panel fünf Minuten dem Feuerwerk, auch vor Flügen und in der Ruhezeit. Gezeichnet wird nur die Karte, die gerade dran ist, bei jedem Abruf des ESP32 neu.

| | |
|---|---|
| ![Landungen am BER mit Herkunft und Minuten](docs/images/arrivals.png) | ![Nächster Flieger: Sichtbar in ~3 min, von links](docs/images/next.png) |
| ![Luftrettung Christoph 31](docs/images/private.png) | ![Wolken auf 600 m, Flieger versteckt](docs/images/weather.png) |
| ![ISS-Überflug in 4 min](docs/images/iss.png) | ![Sammelalbum der Airlines](docs/images/pokedex.png) |

Alle Karten mit Bild, Stufe und Bedingung: [docs/screens.md](docs/screens.md).

## Datenquellen

| Was | Quelle | Bemerkung |
|---|---|---|
| Flugzeuge | [adsb.lol](https://adsb.lol) (`SOURCE=adsblol`) | Community-Netz aus ADS-B-Empfängern, ohne Account, alle `POLL_INTERVAL` Sekunden. Liefert auch Typ und ADS-B-Kategorie (Hubschrauber, Leichtflugzeug). Daten unter ODbL. |
| Ersatz | [OpenSky](https://opensky-network.org) | springt ein, wenn adsb.lol auch im zweiten Versuch nicht antwortet (der geht an einen anderen adsb.lol-Server), so oft das Tagesbudget erlaubt (anonym 400 Credits, mit Account 4000: `OPENSKY_CLIENT_ID`/`_SECRET`). `SOURCE=opensky` nimmt nur OpenSky. |
| Routen | [adsb.im](https://adsb.im), dann [adsbdb](https://www.adsbdb.com) | pro Callsign gespeichert und oft alt; liegt das Flugzeug weit neben der Strecke, verwirft der Server sie. Fliegt eine Airline hin und zurück unter einem Callsign (BER-NCE-BER), zählt die Teilstrecke, auf der das Flugzeug gerade ist. |
| Herkunft ohne Route | OpenSky-Flugspur | beginnt die Spur am Boden, ist der nächste Flughafen aus `data/airports.json` der Start; beginnt sie erst in der Luft, bleibt die Herkunft offen. |
| Wetter | aviationweather.gov, NOAA | METAR alle 20 min (`METAR=0` schaltet ab). |
| ISS | [Celestrak](https://celestrak.org) | Bahndaten einmal am Tag, Überflüge rechnet der Server (`ISS=0`). |
| Logos | Kiwi.com, FlightAware, Google | holt der Server beim ersten Auftauchen einer Airline; nicht im Repo. |
| Flughäfen | [OurAirports](https://ourairports.com) | gemeinfrei, `tools/make_airports.py` erzeugt `data/airports.json`. |

Fällt eine Quelle aus, fehlen nur ihre Teile: ohne Routen keine Herkunft, ohne METAR keine Wetterkarten. Ohne frische Flugdaten wird das Bild nach 2 min gedimmt und nach 10 min leer (`STALE_AFTER`, `EMPTY_AFTER`). Fehler stehen in `/issues` und `/health`.

## Airline-Logos

Nur quadratische Bildmarken: Schriftzüge sind bei 32 × 32 Pixeln unlesbar, dann steht dort der Platzhalter (graues Flugzeug). Der Server probiert die Quellen der Reihe nach, nimmt das erste Bild mit Seitenverhältnis bis `LOGO_MAX_ASPECT`, entfernt weißen Hintergrund, schneidet zu und legt `data/logos/<IATA>.png` ab (ohne IATA-Code `<ICAO>.png`). Platzhalterbilder der Quellen erkennt er. Fehlschläge stehen in `/health` → `logos_missing` und werden nach 7 Tagen erneut probiert.

- Alle auf einmal: `python3 -m flightpanel.fetch_logos` (im Container: `docker compose exec flightpanel …`), gezielt `… fetch_logos LH U2 FR`, `--force` holt abgerufene neu.
- Eigene Logos, wenn keins gefunden wird oder das abgerufene nicht gefällt (App-Icons, bei denen die Marke auf einer Kachel klein wird): `tools/make_logo.py bild.png U2` macht aus PNG, JPG, WebP oder SVG das passende Format und legt es in `data/logos/own/` ab, mit Vorschau in `own/preview/`; die Originale passen nach `own/src/`, dort sucht der Server nicht. Ohne Python auf dem Rechner: `docker compose run --rm -v "$PWD:/repo" -w /repo flightpanel python tools/make_logo.py bild.png U2`. Weißer oder hellgrauer Hintergrund fällt weg, `--boost` hellt dunkle Marken auf (LOT, Finnair); `--mark "#ff6600"` nimmt von einer farbigen Kachel nur die weiße Marke, in dieser Farbe (das e von easyJet). Eigene gehen den abgerufenen vor, der Abruf fasst sie nie an, auch nicht mit `--force`; löschen, um zum abgerufenen zurückzukehren. Code ist der Logo-Code aus `airlines.json`, eine Datei gilt also für alle Betriebe einer Marke.
- Logos bleiben lokal (sie sind Marken der Airlines und gehören nicht ins Repo). Beim Umzug auf einen anderen Rechner `data/logos/own/` mitnehmen, den Rest holt der Server neu.
- `data/airlines.json` bildet ICAO-Codes auf kurze Namen und den Logo-Code ab: die Marke, nicht unbedingt der Tochterbetrieb (Malta Air → `FR`, KLM Cityhopper → `KL`). So bekommt ein Tochterbetrieb ohne eigenes quadratisches Logo das der Marke. Leer heißt Logo per ICAO, wenn der IATA-Code doppelt vergeben ist. `"album": false` für Kleinbetriebe (CPH AirTaxi): Name und Logo ja, im Sammelalbum zählen sie nur wie Exoten.

## Endpunkte

| Pfad | Zweck |
|---|---|
| `/` | Vorschauseite |
| `/frame` | Binär für den ESP32: `[1, Helligkeit, 128, 32]` + 12 288 Byte RGB |
| `/frame.png` | Vorschau; `layout`, `card`, `scale`, `gap=0` |
| `/display.json` | Display-Modell, dazu `nearby`: alle Flugzeuge im Radius mit Richtung, Höhenwinkel, Sichtbarkeit, Landefolge |
| `/health` | Quellen, wirksame Anzeige-Einstellungen, Cache, Logos, letzter ESP32-Abruf (`esp32.alive=false`: Panel hängt), `problems`, nächtliches Aufräumen (`dream`), Datenbank (`database.copy`: Zeit der letzten nächtlichen Kopie, `database.put_aside`: beiseitegelegte kaputte Datenbankdateien) |
| `/ok` | `ok` mit 200, sonst 503 und die Gründe (keine frischen Flugdaten seit 15 min, ESP32 holt seit 10 min nichts, Fehler im Code, beiseitegelegte Datenbank); für den Docker-Healthcheck und einen Uptime-Monitor |
| `/issues` | gesammelte Auffälligkeiten (siehe unten), `POST /issues/clear` leert |
| `/stats` | Tagesstatistik, Sammelalbum, seltene Sichtungen, Landerichtung |
| `/idle` | Leerlauf-Karten: Stufe, ob sie gerade etwas zu sagen haben, welche dran ist |

## Betrieb auf dem Server

```bash
git clone https://github.com/toastbrot2000/flightpanel && cd flightpanel
cp /pfad/zur/alten/.env .env                            # und data/logos/own/ mitnehmen; Linux: PUID, PGID setzen
cp /pfad/zur/kopie.db data/flightpanel.db               # die Datenbank vom alten Rechner (s. u.)
docker compose up -d --build
docker compose exec flightpanel python3 -m flightpanel.fetch_logos
```

Die Datenbank zieht mit um: Standort, Sichtfeld, Album und Statistik stehen in `data/flightpanel.db`, nicht in der `.env`. Auf dem alten Rechner eine Kopie ziehen, `docker compose exec flightpanel python3 -m flightpanel.panels backup /data/umzug.db` (die Kopie liegt danach als `data/umzug.db` im Projektordner; in Git Bash unter Windows `MSYS_NO_PATHCONV=1` davor, Näheres in der Bauanleitung), oder bei gestopptem Server (`docker compose stop`) `data/flightpanel.db` kopieren. Auf dem neuen Rechner gehört sie vor dem ersten Start nach `data/flightpanel.db`. Eigene Logos (`data/logos/own/`) nimmt man wie bisher mit, die abgerufenen holt der Server neu.

Dann `/health` ansehen: `source.active` sollte `adsblol` sein. Cache, Statistik, Album und Auffälligkeiten liegen in `data/flightpanel.db`, Logos in `data/logos/` und bleiben über Updates. In den `QUIET_HOURS` ist das Panel schwarz und der Server ruft nichts ab. Die Checkliste für den Umzug steht in der [Bauanleitung](docs/bauanleitung.md#vor-dem-umzug-auf-den-server).

**Datenbank und Sicherung:** `data/flightpanel.db` wird laufend geschrieben (daneben `-wal` und `-shm`) und gehört deshalb nicht in einen synchronisierten Ordner (OneDrive, Dropbox …): Liegt das Projekt in einem, `data/` vom Abgleich ausnehmen. Das Sicherheitsnetz ist die nächtliche Kopie `data/flightpanel.db.bak` (siehe "Dauerbetrieb"). Zurück zu ihr: `docker compose stop`, `data/flightpanel.db.bak` als `data/flightpanel.db` kopieren, `flightpanel.db-wal` und `flightpanel.db-shm` daneben löschen, `docker compose start`. Das ist auch der Weg, wenn der Server eine kaputte Datenbank beiseitegelegt hat (`data/flightpanel.db.broken-<Datum>`, `/ok` ist rot, siehe `/issues`, Art `database`): Er startet dann leer, und das Log nennt diese Schritte. Weitere Kopien zieht `panels backup` (Bauanleitung, Abschnitt "Betrieb"), eine vorhandene Datei ersetzt der Befehl nur mit `--force`.

**Updates:** `sh tools/update.sh` holt den neuen Stand, baut neu und startet neu, nur wenn es einen neuen Commit gibt. Wöchentlich per cron, montags in der Ruhezeit: `0 6 * * 1  cd /pfad/zu/flightpanel && sh tools/update.sh >> data/update.log 2>&1`.

**Abhängigkeiten mit Wartezeit:** Neue Versionen von Python-Paketen, Basis-Image und Werkzeugen kommen erst ins Image, wenn sie seit 7 Tagen veröffentlicht sind; bis dahin sollte ein kompromittiertes Release aufgefallen sein. `server/requirements.lock` hält jede Version mit Prüfsummen (`pip --require-hashes`), das Basis-Image ist per Digest festgelegt, die GitHub-Actions per Commit, die Firmware-Bibliotheken per genauer Version. Einmal pro Woche übernimmt `.github/workflows/escrow.yml`, was alt genug ist, baut und testet wie auf dem Server und checkt nur ein, wenn alles grün ist. Dabei hat der Job, der fremden Code ausführt (die Tests), keine Schreibrechte; der Job, der eincheckt, rechnet die Änderung selbst nach und führt keine fremden Pakete aus. Der Server baut nur aus dem Repo und holt sich nie selbst die neueste Version. Von Hand: `sh tools/escrow.sh` (Docker).

**Ältere `.env`:** `IDLE_OFF` heißt jetzt `CARD_<ID>=0` je Karte, `LOGO_URL` heißt `LOGO_URLS`. `LAYOUT=auto` ist jetzt dasselbe wie `C`. Ausdrücklich gesetzte Werte gehen dem ruhigen Modus vor: Mit `CALM=1` die alte Zeile `PAGE_SECONDS=4` löschen (vor dem ersten Start in `.env`, danach mit `panels unset home PAGE_SECONDS`). `AIRPORT_LAT`, `AIRPORT_LON`, `METAR_STATION` nur noch setzen, wenn der Flughafen nicht in `data/airports.json` steht. Der Server sagt beim Start im Log, was davon er gefunden hat.

**Auffälligkeiten (`/issues`)** sammelt der Server im Betrieb, mit Zähler, erstem und letztem Auftreten, Callsigns und einem Beispiel:

| Art | Bedeutung | Was tun |
|---|---|---|
| `unknown_operator` | Betreibercode nicht in `airlines.json` | nur nötig, wenn der Name, den der Server selbst bei adsbdb holt (im Beispiel unter `adsbdb`), veraltet oder missverständlich ist; für das Logo einer Marke; für Rettung, Polizei, Militär (`"airline": false`) |
| `name_mismatch` | unser Name passt nicht zu adsbdb | meist alte Daten bei adsbdb (Sundair heißt dort City Airline): geprüft mit `"adsbdb": "Name dort"` eintragen, dann kommt er nicht wieder; sonst Fehler bei uns |
| `route_implausible` | Route passt nicht zur Position, verworfen | nichts, zur Kontrolle |
| `route_unknown` | gezeigter Airline-Flug ohne Route | nichts, keine Quelle kennt ihn |
| `source_error`, `rate_limit`, `fallback` | Fehler, HTTP 429, OpenSky eingesprungen | bei Häufung `POLL_INTERVAL` erhöhen |
| `stale` | Bild gedimmt oder leer mangels Daten | Quellen prüfen |
| `esp32_gap` | ESP32 hat länger keinen Frame geholt | WLAN, Stromversorgung |
| `exception` | Fehler im Code, mit Datei und Zeile | Bug, Traceback im Container-Log |
| `setting` | gespeicherte Panel-Einstellung gilt nicht (mehr), etwa nach einem Update; der Server nimmt den Default | den Befehl im Beispiel (`panels unset …`), dann neu setzen |
| `backup` | nächtliche Kopie der Datenbank gescheitert | Fehler im Beispiel: Platz und Rechte in `data/` prüfen |
| `database` | die Datenbank war kaputt: Der Server hat sie als `data/flightpanel.db.broken-<Datum>` beiseitegelegt und leer begonnen; `/ok` ist rot, solange der Eintrag steht, und die nächtliche Kopie ruht | der Weg zurück steht im Beispiel (`fix`): `data/flightpanel.db.bak` zurückkopieren; danach `/issues` leeren |

Liegt in `data/flightpanel.db`. Nach dem Durchsehen leeren: `curl -X POST http://server:8080/issues/clear`.

**Dauerbetrieb:** Der Server ist darauf ausgelegt, monatelang ohne Neustart zu laufen. Einmal pro Nacht, zu Beginn der `QUIET_HOURS` (ohne Ruhezeit um 4 Uhr), räumt er auf: `/issues` vergisst Einmaliges (nur an einem Tag gesehen) nach 7 Tagen und alles andere, wenn es 30 Tage nicht mehr aufgetreten ist; Fehler im Code und eine beiseitegelegte Datenbank bleiben mindestens 30 Tage. Ist `/issues` trotzdem voll (1000 Einträge), macht der älteste Platz. Der Cache wirft Abgelaufenes bei jedem Speichern weg. Dazu legt er jede Nacht eine stimmige Kopie der Datenbank an, `data/flightpanel.db.bak`, die die der Vornacht ersetzt (meldet `/issues` eine beiseitegelegte Datenbank, bleibt sie, wie sie ist: Sie ist der Weg zurück). Was die letzte Nacht getan hat, steht in `/health` unter `dream`. Das Docker-Log ist auf 15 MB begrenzt (`compose.yml`), alte Images räumt `tools/update.sh` weg. Damit man nicht nachsehen muss: Ein Uptime-Monitor im Homelab (Uptime Kuma o. ä.) auf `http://server:8080/ok` meldet sich nur, wenn wirklich etwas klemmt; `docker ps` zeigt den Container dann als `unhealthy`.

## Hardware und Firmware

PlatformIO, Board `esp32dev` (klassischer ESP32, WROOM-32), Bibliothek ESP32-HUB75-MatrixPanel-DMA 3.0.14. Der ESP32 holt alle 2 s `/frame` und zeigt es an; ohne Server bleibt das letzte Bild stehen, nach 60 s mit einem roten Punkt unten rechts, nach 10 min startet er neu.

```bash
cd firmware
cp include/secrets.h.example include/secrets.h   # WLAN (2,4 GHz!), Server-URL, OTA-Passwort
pio run -t upload                                # erstes Mal per USB, danach: pio run -e esp32dev_ota -t upload
```

Teile, Strom, Verdrahtung (Pinbelegung), Testbild und Stellschrauben stehen in der [Bauanleitung](docs/bauanleitung.md). Kompilieren ohne lokales PlatformIO: `sh tools/check_firmware.sh` (Docker).

## Entwicklung

```bash
pip install --require-hashes -r server/requirements-dev.lock   # dazu DejaVu-Schriften (fonts-dejavu-core)
pytest                                        # vom Repo-Wurzelverzeichnis, ohne Netz
ruff check .
MOCK=1 DATA_DIR=./data PYTHONPATH=server python3 -m flightpanel.main
```

Die Tests prüfen ohne Netz: Einstellungen, Sichtfeld und Vorhersage, Landefolge, Routen (auch hin und zurück unter einem Callsign), Kartenauswahl, Statistik, METAR, Sonne (gegen veröffentlichte Zeiten), ISS (gegen eine unabhängige Position), jede Karte mit Beispieldaten und jeden HTTP-Endpunkt. GitHub Actions baut bei jedem Push den Container und führt darin Lint und Tests aus; bei Änderungen an `firmware/` kompiliert es die Firmware. Die Bilder in dieser Doku erzeugt `tools/screenshots.py` aus Beispieldaten; nach Änderungen am Aussehen neu erzeugen.

Eine neue Abhängigkeit kommt in `server/requirements.txt` (bzw. `requirements-dev.txt`), dann `sh tools/escrow.sh`: Das schreibt die Locks neu, mit Versionen, die mindestens 7 Tage alt sind. Die `.lock`-Dateien nie von Hand ändern.

### Aufbau

```
server/flightpanel/
  main.py        Einstieg: App, Abruf-, Wetter-, ISS- und Speicherschleife
  web.py         HTTP-Endpunkte und Vorschauseite
  config.py      Server-Einstellungen aus .env (Config.from_env), Panel-Einstellungen aus der
                 Datenbank (Config.for_panel), ruhiger Modus, Helligkeit
  db.py          SQLite-Datenbank data/flightpanel.db: Panels, Statistik, Album, Cache, Auffälligkeiten
  panels.py      Befehl: Panel-Einstellungen setzen, Panels anlegen, Datenbank sichern
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
  airport.py     Landerichtung, Landefolge und -zeit, Abflüge, wer zuletzt landete oder startete
  stats.py       Tagesstatistik, Sammelalbum, seltene Flugzeuge, Flaute
  mock.py        Fantasieflüge für MOCK=1

  render.py      Fluglayouts, Himmelspunkte, Frame-Bytes und PNG
  draw.py        Zeilenraster und Texthelfer; fonts.py, colors.py, sprites.py
  idle.py        Auswahl der Leerlauf-Karten (Stufen, Gewichte, Dringendes)
  cards/         die Karten: traffic, runway, album, sky, fun; Liste in __init__.py
server/tests/    pytest
firmware/        ESP32: platformio.ini, src/main.cpp, include/config.h, secrets.h.example
tools/           make_logo.py, make_airports.py, screenshots.py, check_firmware.sh,
                 escrow.py/.sh (Abhängigkeiten mit Wartezeit), update.sh (Server aktualisieren)
data/            airlines.json, airports.json, rare.json;
                 im Betrieb logos/ und flightpanel.db (SQLite: Panels, Statistik, Album, Cache, Auffälligkeiten)
docs/            bauanleitung.md (Hardware), screens.md (alle Bildschirme), images/
```

### Eine Leerlauf-Karte hinzufügen

1. In der passenden Datei unter `server/flightpanel/cards/` zwei Funktionen: `foo_cond(ctx)` gibt `Hit(daten)` zurück oder `None`, wenn die Karte gerade nichts zu sagen hat; `foo_draw(img, ctx, daten, t_in, f)` zeichnet. Optional `foo_demo(ctx)` mit Beispieldaten. Was `ctx` mitbringt: `idle.Ctx`; Texthelfer: `draw.py`.
2. Eintrag in `CARDS` in `cards/__init__.py`, mit Stufe (`tier`) und Gewicht.
3. Ansehen: `/frame.png?card=foo`. Der Test `test_every_card_renders_with_demo_data` zeichnet sie automatisch mit.
4. Eine Zeile in `docs/screens.md`. Die Karte ist von Haus aus an, `panels set home CARD_FOO=0` schaltet sie ab (die ID kennt `panels` aus `CARDS`; in `.env.example` steht dafür nichts).

### Eine Airline nachtragen

In `data/airlines.json`: `"ICAO": {"name": "Kurzname", "iata": "XY"}`. Für Rettung, Polizei, Militär `"airline": false` und `"label": "Polizei"`; mit `"number": true` wird aus CHX31 "Christoph 31". Danach das Logo holen (`fetch_logos XY`) oder von Hand machen (`tools/make_logo.py`). Kandidaten sammelt `/issues` unter `unknown_operator`. Für einen Namen braucht es keinen Eintrag mehr: Kennt adsbdb das Callsign nicht, holt der Server den Namen zum Betreiber-Code (AWH91J → Aerowest) und kürzt ihn nach ganzen Wörtern. Ein Eintrag lohnt sich, wenn dieser Name veraltet oder missverständlich ist (AEG ist heute Airest, adsbdb kennt noch eine andere Firma), für das Logo einer Marke und für Rettung, Polizei, Militär. Der Name steht neben dem Logo in 94 px; ist er zu lang, eine kurze Fassung wählen ("CPH AirTaxi" statt "Copenhagen Airtaxi").

## Lizenz und Rechte

Code und Doku stehen unter der [MIT-Lizenz](LICENSE). Flugdaten von adsb.lol stehen unter ODbL, `data/airports.json` stammt von OurAirports (gemeinfrei). Airline-Namen und -Logos sind Marken ihrer Inhaber; Logos sind nicht Teil des Repos, der Server lädt sie für den privaten Gebrauch. "Demo Air" in den Beispielbildern ist erfunden.
