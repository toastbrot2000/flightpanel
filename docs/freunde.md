# Panels für Freunde

Jedes Panel hängt im WLAN des Freundes und holt sein Bild über das Internet vom Server des Betreibers, ohne VPN. Dazwischen stehen Cloudflare Access und ein Tunnel. Server und Panel beschreiben die [README](../README.md) und die [Bauanleitung](bauanleitung.md); hier steht nur, was für Freunde dazukommt.

> **Stand:** Die Anleitung folgt dem [Entwurf](superpowers/specs/2026-10-13-freunde-multi-tenant-design.md) (englisch, mit allen Begründungen) und der Cloudflare-Dokumentation vom Oktober 2026. Durchgespielt hat sie noch niemand. Die Bezeichnungen im Dashboard ändern sich gelegentlich; maßgeblich ist, was eine Einstellung bewirkt. Wer etwas findet, das fehlt oder anders ist: Rückmeldung willkommen.

**Für wen:** für den Betreiber, der Freunden ein Panel baut. Wer ein Panel nur für sich betreibt, braucht nichts davon: Ohne die Schalter in der `.env` lädt der Server den Code dafür nicht einmal, es öffnet sich kein zweiter Port, und alles läuft wie in der README. Die Freunde wohnen am selben Flughafen. Ein Abruf bedient alle Panels, darum darf ihr Fenster höchstens `PANEL_MAX_KM` (60 km) davon entfernt liegen.

**Beispielwerte:** `flightpanel.example.com` steht für den Hostnamen, `<team>` für den Team-Namen in Zero Trust, `<AUD-Tag>` für die Kennung der Access-Anwendung, `anna@example.com` für die Adresse einer Freundin. Die eigenen Werte kommen nie ins Repo.

---

## Überblick

```
Panel beim Freund ──HTTPS + Service-Token──┐
Browser des Freundes ──E-Mail-Code─────────┴─> Cloudflare Access ─Tunnel─> cloudflared ─eigenes Netz─> Server :8081
Panel im Rettungsmodus ─HTTPS, ohne Token─> Cloudflare (nur /panel/firmware*) ─> cloudflared ────────> Server :8081
eigenes Panel, Browser im Heimnetz ─HTTP─────────────────────────────────────────────────────────────> Server :8080
```

Der Server hat zwei Ports. **:8080** ist der LAN-Port wie bisher: ohne Anmeldung, nur für das Panel am LAN-Port (`LAN_PANEL`, Standard `home`) und die Seiten `/health`, `/ok` und `/issues`. **:8081** (`PUBLIC_PORT`) gibt es nur mit den Schaltern aus Abschnitt 4. Er ist nicht veröffentlicht, nur cloudflared erreicht ihn, und er verlangt bei jeder Anfrage eine geprüfte Identität. Ausnahme sind die zwei Firmware-Dateien.

Die Reihenfolge: Abschnitte 1 bis 3 einmal in Cloudflare und im Docker-Netz, Abschnitt 4 am Server, Abschnitt 5 als Probe. Danach je Freund Abschnitt 6 und, wenn er aufhört, Abschnitt 7.

**Was Freunde bekommen:**

- Ein Panel im eigenen Fenster, mit eigenem Standort, Sichtfeld, eigener Helligkeit und Ruhezeit. Album und Statistik zählen je Panel, ab der Einrichtung.
- Eine Seite im Browser, auf dem Handy zuerst: Vorschau des eigenen Panels, Einstellungen, Eichen des Sichtfelds am Fenster. Angemeldet wird mit einem Code, der per E-Mail kommt; kein Konto, kein Passwort.
- Nichts von anderen Panels und nichts vom Server selbst: `/health`, `/ok` und `/issues` gibt es von außen nicht.

**Wie es abgesichert ist.** Nichts antwortet ohne bekannte Identität, bis auf zwei signierte Dateien. Drei Schichten prüfen, und keine verlässt sich auf eine andere:

1. **Cloudflare Access** lässt nur durch, wer sich per E-Mail-Code anmeldet und in der Gruppe steht (Browser) oder den Service-Token eines Panels vorzeigt (Panel).
2. **cloudflared** prüft den Access-Token noch einmal (`access.required`), bevor es etwas an den Server weitergibt.
3. **Der Server** prüft ihn ein drittes Mal selbst: Signatur gegen die Schlüssel von Cloudflare, Aussteller, Anwendung, Ablauf. Er kennt nur Adressen und Tokens, die der Betreiber per Befehl einem Panel zugeordnet hat; alles andere bekommt 403, auch mit gültigem Token.

Dazu das Netz: `PUBLIC_PORT` wird nie auf dem Host veröffentlicht, sondern hängt in einem eigenen Docker-Netz, in dem nur der Server und cloudflared sind. Und der LAN-Port lehnt mit gesetztem `PUBLIC_PORT` Anfragen ab, die Cloudflare-Header tragen: Wird er aus Versehen in den Tunnel eingetragen, bleibt er zu.

| Wer | Pfade |
|---|---|
| Browser eines Freundes (E-Mail-Code) | `/`, `/frame.png`, `/display.json`, `/stats`, `/idle`, mit `SETTINGS_PAGE=1` auch `/settings`; immer nur das eigene Panel |
| Panel (Service-Token) | `/panel/frame`, sonst nichts |
| jeder, ohne Anmeldung | `/panel/firmware.txt` und `/panel/firmware.bin`: signierte Dateien ohne Geheimnisse, der Rettungsweg für Panels (Abschnitt 9) |
| niemand von außen | `/health`, `/ok`, `/issues` |

Welches Panel gemeint ist, bestimmt allein die Anmeldung; Panel-Kennungen stehen nie in Adressen. Je Anmeldung gelten im Schnitt höchstens 2 Anfragen pro Sekunde, damit ein Browser nicht den ganzen Server ausbremst. Was ein Panel über sich meldet, sind vier Zahlen (Firmware-Version, Laufzeit, WLAN-Stärke, freier Speicher); Text von einem Panel wird weder gespeichert noch angezeigt.

**Was bleibt:**

- Wer ein Panel in die Hand bekommt, kann WLAN-Passwort und Token auslesen: Sie liegen unverschlüsselt im Speicher des ESP32. Der Token gibt nur den Bildabruf dieses einen Panels, und man löscht ihn bei Cloudflare (Abschnitt 7).
- Die Verschlüsselungsbibliothek der Firmware (mbedTLS 2.28) ist veraltet. Im WLAN des Freundes lauscht das Panel auf nichts, es ruft nur ab. Ersetzt wird die Bibliothek mit dem Wechsel auf Arduino 3.x, der vorgemerkt ist.
- Wer das Cloudflare-Konto übernimmt, kann Adressen freischalten (der Server kennt aber nur die eingetragenen) und den Tunnel auf andere Dienste im Homelab umbiegen. Darum der Hardware-Schlüssel in Abschnitt 1.

**Was gespeichert wird:** auf dem Server, in `data/flightpanel.db`, je Panel die Einstellungen (also die Position des Fensters, oft auf wenige Meter genau), die zugeordneten Adressen und Client-IDs, Statistik, Album und die vier Diagnosezahlen. Cloudflare protokolliert Anmeldungen. Das Panel selbst hält WLAN-Zugang und Token. Die Datenbank gehört nicht in einen synchronisierten Ordner und nicht in fremde Hände, Sicherungen (`panels backup`) auch nicht. Den Freunden vorher sagen, was gespeichert wird.

---

## 1. Cloudflare: Konto und Zone

Einmalig, im Dashboard von Cloudflare:

- **Konto:** Zwei-Faktor-Anmeldung mit einem Hardware-Schlüssel (Passkey oder Security Key, unter Profil → Authentication), nicht per App oder SMS. Keine API-Tokens mit Access-Rechten anlegen und aufbewahren, auch nicht "für später": Wer so einen Token hat, ändert Regeln und Identitäten.
- **Hostname:** eine Ebene unter der eigenen Domain, `flightpanel.example.com`. Das kostenlose Universal-Zertifikat deckt die Domain und eine Ebene darunter ab, tiefere Namen wie `panel.freunde.example.com` nicht.
- **Bot Fight Mode aus** (Security → Settings, Filter "Bot traffic"). Er lässt sich nicht per Regel für einen Host oder Pfad ausnehmen und würde die Panels als Bots herausfordern. Er gilt für die ganze Zone: Hängt dort etwas anderes davon ab, bekommt flightpanel eine eigene Domain. Auch sonst darf für den Host keine Challenge gelten (Under-Attack-Modus, "Managed Challenge" in WAF-Regeln): Ein Panel kann kein CAPTCHA lösen.
- **Minimale TLS-Version höchstens 1.2** (SSL/TLS → Edge Certificates → Minimum TLS Version). Die Firmware spricht nur TLS 1.2; mit 1.3 als Minimum blieben alle Panels draußen.

---

## 2. Cloudflare Access

Zero Trust ist im Konto eingerichtet (Team-Name gewählt). Die Menüpfade stehen, wenn nichts anderes dabeisteht, unter Zero Trust → Access controls.

**Gruppe** (Policies → Rule groups → Add a group): Name `flightpanel`, Include → Emails, zunächst nur die eigene Adresse. Hier kommen später die Adressen der Freunde dazu.

**Anwendung** (Applications → Create new application → Self-hosted and private):

1. Public hostname `flightpanel.example.com`, Pfad leer (gilt dann für alles).
2. Zwei Regeln:
   - Aktion **Allow**, Include → Rule groups → `flightpanel`. Das ist der Zugang für Browser.
   - Aktion **Service Auth**, Include → **Service Token**, dort jeden Panel-Token **einzeln** wählen (Abschnitt 6). Nie "Any Access Service Token": Das ließe jeden Token des Kontos durch, auch die für andere Dienste.
3. Anmeldemethode: nur der Code per E-Mail (One-time PIN).
4. Unter Advanced settings → Cookie settings: **SameSite auf Lax** stellen, ausdrücklich (der Standard ist None), und den **Binding-Cookie einschalten** (Enable binding cookie). Er bindet die Anmeldung an den Browser, ein gestohlenes Cookie nützt allein nichts.
5. Speichern. In den Grunddaten der Anwendung steht das **Application Audience (AUD) Tag**, 64 Hexziffern: Das ist `<AUD-Tag>` und kommt als `ACCESS_AUD` in die `.env` und in den Tunnel (Abschnitte 3 und 4). Der Team-Name, der Teil vor `.cloudflareaccess.com`, ist `<team>`.

**Der offene Pfad für die Firmware** (eine zweite Anwendung, ebenfalls Self-hosted): Hostname `flightpanel.example.com`, Pfad `/panel/firmware*`. Eine Regel: Aktion **Bypass**, Include → Everyone. Das ist die **einzige** Bypass-Regel und soll es bleiben: Bypass schaltet Access für diese Pfade ab und protokolliert nichts. Die zwei Dateien sind signiert und enthalten keine Geheimnisse; sie sind der Rettungsweg für Panels, deren Vertrauensliste veraltet ist (Abschnitt 9). Der Stern ist nötig, damit `firmware.txt` und `firmware.bin` getroffen werden. Für diese Pfade gilt die genauere, längere Anwendung; die Probe in Abschnitt 5 prüft das.

**Strikter Modus** (Access settings → Manage service tokens → **Strict service token authentication** einschalten): Eine Anfrage mit falschem Token bekommt dann 401 oder 403 statt einer Weiterleitung zur Anmeldeseite, und über Anfragen mit Token entscheiden nur Service-Auth-Regeln. Die Einstellung gilt für das ganze Konto. Wer es auch für andere Dienste nutzt, prüft vorher, dass deren Tokens in Service-Auth-Regeln stehen und nicht in Allow-Regeln.

**Lebensdauer der Tokens:** Beim Anlegen (Service credentials → Service Tokens → Create Service Token, Feld "Service Token Duration") die längste Dauer nehmen, die Cloudflare zulässt. Die Doku nennt `8760h` (ein Jahr) und `17520h` (zwei Jahre) als Beispiele, die Dauer ist aber frei wählbar: Zehn Jahre (`87600h`) zuerst versuchen, sonst zwei. Bleibt es bei einem Jahr, steht das Verlängern jährlich im Kalender. Dazu eine Benachrichtigung: Notifications → Add → "Expiring Access Service Token", sie kommt rund eine Woche vor dem Ablauf. Vor dem Ablauf bei jedem Token **Refresh**: Das verlängert um ein Jahr, Client-ID und Secret bleiben gleich, die Panels merken nichts. Pro Konto sind höchstens 50 Service-Tokens möglich, die anderer Dienste zählen mit.

---

## 3. Tunnel und Docker-Netz

cloudflared holt Anfragen von Cloudflare ab (eine ausgehende Verbindung, am Router bleibt alles zu) und gibt sie an den Server. Es erreicht ihn nicht über den Reverse-Proxy des Homelabs (Traefik o. ä.), sondern direkt, in einem eigenen Docker-Netz mit genau zwei Teilnehmern: dem Server und cloudflared.

**Tunnel anlegen** (Networking → Tunnels → Create a tunnel → Cloudflared), Name `flightpanel`. Ein eigener Tunnel nur dafür ist besser als der vorhandene des Homelabs: Der Token des Tunnels ist der Schlüssel zu allem, was durch ihn läuft. Das Dashboard zeigt ihn in der Docker-Zeile (`--token …`); den Befehl dort nicht ausführen, das übernimmt Compose. Der Token kommt in eine Datei `cloudflared.env` neben `compose.yml`, mit einer Zeile `TUNNEL_TOKEN=…`, und nicht in die `.env`: Die liest auch der Server-Container, und er braucht den Token nicht.

```bash
touch cloudflared.env && chmod 600 cloudflared.env     # dann mit einem Editor TUNNEL_TOKEN=… eintragen
```

**Zwei Routen** (Tunnel → Routes → Add route → Published application; der DNS-Eintrag entsteht dabei von selbst), in dieser Reihenfolge:

| # | Hostname | Pfad | Dienst | "Protect with Access" |
|---|---|---|---|---|
| 1 | `flightpanel.example.com` | `^/panel/firmware` | `http://flightpanel:8081` | aus |
| 2 | `flightpanel.example.com` | leer | `http://flightpanel:8081` | an, mit `<team>` und `<AUD-Tag>` |

"Protect with Access" steht unter "Additional application settings" und lässt cloudflared den Access-Token prüfen (`access.required`). In einer Konfigurationsdatei (lokal verwalteter Tunnel) sieht dasselbe so aus:

```yaml
ingress:
  - hostname: flightpanel.example.com
    path: ^/panel/firmware               # ohne Access: die zwei Firmware-Dateien
    service: http://flightpanel:8081
  - hostname: flightpanel.example.com
    service: http://flightpanel:8081
    originRequest:
      access:
        required: true
        teamName: <team>
        audTag:
          - <AUD-Tag>
  - service: http_status:404
```

Die Reihenfolge zählt: cloudflared nimmt die erste Regel, die passt, und der Pfad ist ein regulärer Ausdruck. Stünde die zweite Regel oben, bekämen Panels im Rettungsmodus ein 403. Die Probe in Abschnitt 5 zeigt, ob es stimmt. Als Dienst immer Port **8081** (`PUBLIC_PORT`), nie 8080: Der LAN-Port hat keine Anmeldung.

**Docker-Netz und cloudflared-Container** stehen in [compose.public.example.yml](../compose.public.example.yml). Die Datei als `compose.override.yml` kopieren; Compose liest sie ohne weiteres Zutun neben `compose.yml`, und sie steht in der `.gitignore`:

```bash
cp compose.public.example.yml compose.override.yml
```

Darin ersetzen: `<version>` und `<digest>` beim cloudflared-Image. Es muss festgenagelt sein, nie `:latest` oder ein Tag allein (die Regel des Projekts für alles, was ausgeführt wird: nichts, was nicht seit mindestens 7 Tagen veröffentlicht ist). Wie man Version und Digest holt, steht als Kommentar in der Datei.

Was die Datei sicherstellt:

- Für `PUBLIC_PORT` steht dort kein `ports:`, und es darf keins dazukommen. Veröffentlicht ist nur 8080.
- Das Netz `flightpanel-public` hat nur zwei Teilnehmer. Kein Traefik und kein anderer Dienst hängt daran.
- Der Server-Container trägt das Label `traefik.enable=false` (steht schon in `compose.yml`, nicht entfernen). Der Dockerfile setzt `EXPOSE 8080`, und ein Traefik mit Docker-Provider würde sonst den Port ohne Anmeldung veröffentlichen.
- Wer schon einen cloudflared betreibt und ihn weiterverwenden will: den Block `cloudflared` aus der Datei streichen und den vorhandenen Container nur an das Netz `flightpanel-public` hängen (in seiner Compose-Datei als `external: true` eintragen). Sein Token gehört auch dann nicht in die `.env` des Servers.

---

## 4. Server einschalten

In der `.env` des Servers. Die vier Angaben für den Zugang von außen gelten zusammen oder gar nicht: Fehlt eine, bricht der Start ab, und die Meldung sagt, welche.

```bash
PUBLIC_PORT=8081
ACCESS_TEAM=<team>
ACCESS_AUD=<AUD-Tag>
PUBLIC_HOST=flightpanel.example.com
SETTINGS_PAGE=1
```

| Schalter | Bedeutung |
|---|---|
| `PUBLIC_PORT` | Port für Cloudflare im Container, hier 8081. Muss ein anderer sein als `HTTP_PORT`. Wird nie veröffentlicht. |
| `ACCESS_TEAM` | der Team-Name aus Zero Trust (Teil vor `.cloudflareaccess.com`) |
| `ACCESS_AUD` | das AUD-Tag der Anwendung aus Abschnitt 2 |
| `PUBLIC_HOST` | der Hostname ohne `https://`. Speichern auf der Einstellungsseite klappt nur von dieser Adresse aus. |
| `SETTINGS_PAGE` | `1` schaltet die Einstellungsseite `/settings` ein: außen für die Freunde, am LAN-Port für das Panel am LAN-Port (dort ohne Anmeldung, wer im Heimnetz ist, darf umstellen). Geht auch ohne die vier Angaben oben. |
| `LAN_HOSTS` | weitere Namen, unter denen der LAN-Port erreichbar ist, kommagetrennt, etwa `nas.fritz.box`, den Namen, den die FRITZ!Box vergibt. Er nimmt sonst nur IP-Adressen, `localhost`, Namen ohne Punkt und Namen auf `.local`, `.home.arpa` und `.internal` an (gegen DNS-Rebinding aus dem Browser; `.box` ist eine öffentliche Endung, darum stehen Router-Namen nur, wenn man sie hier einträgt). Alles andere bekommt 421 mit einem Hinweis auf diese Einstellung; das gilt auch für das eigene Panel, wenn seine Server-Adresse im Einrichtungs-WLAN einen solchen Namen hat. |
| `PANEL_MAX_KM`, `PANEL_MAX_RADIUS_KM` | schon vorhanden (README, "Weitere Panels"): das Fenster eines Freundes höchstens 60 km vom Flughafen, sein Abrufradius höchstens 80 km. Beide gelten für alle Freunde. |

Die Firmware-Updates brauchen keinen Schalter: Liegen Dateien in `data/firmware/<kanal>/`, werden sie ausgeliefert, sonst antwortet der Server mit 404.

```bash
docker compose up -d
docker compose logs --tail 20 flightpanel cloudflared
```

`up -d` startet jetzt auch cloudflared; sein Log zeigt, dass der Tunnel steht (`Registered tunnel connection`).

---

## 5. Prüfen

Noch ohne Panel, vom Laptop aus. Ohne Anmeldung muss Cloudflare abweisen (302 zur Anmeldung oder 403), nie die App antworten. Nur die Firmware-Dateien kommen durch: mit 200, solange Dateien da sind, sonst mit 404 der App. Ein 403 wäre falsch.

```bash
curl -s -o /dev/null -w '%{http_code}\n' https://flightpanel.example.com/
curl -s -o /dev/null -w '%{http_code}\n' https://flightpanel.example.com/panel/frame
curl -s -o /dev/null -w '%{http_code}\n' https://flightpanel.example.com/panel/firmware.txt
```

Der Port 8081 darf aus dem Heimnetz nicht erreichbar sein, der LAN-Port schon:

```bash
curl -m 3 http://<server>:8081/          # muss scheitern (Verbindung abgelehnt oder Zeitüberschreitung)
curl http://<server>:8080/ok             # ok
docker compose ps                        # veröffentlicht ist nur 8080
```

Im Browser fragt `https://flightpanel.example.com/` nach einer E-Mail-Adresse und schickt einen Code. Mit der Adresse aus der Gruppe steht danach "nicht freigeschaltet" da, bis ein Panel sie hat (Abschnitt 6).

Wenn es nicht klappt:

| Symptom | Ursache | Was tun |
|---|---|---|
| Der Start bricht ab, "… gesetzt, es fehlt …" | nur ein Teil der vier Angaben | alle vier setzen oder alle löschen |
| `/panel/firmware.txt` kommt mit 403 zurück | Reihenfolge der Tunnel-Routen oder Pfad der Bypass-Anwendung | Abschnitt 3 und 2 prüfen: Route 1 steht oben, der Pfad der Anwendung endet auf `*` |
| Browser: kein Code kommt an, oder Cloudflare weist ab | Adresse nicht in der Gruppe `flightpanel` | Abschnitt 6, Schritt 3 |
| Browser: "nicht freigeschaltet" | Adresse keinem Panel zugeordnet | `panels email …` (Abschnitt 6, Schritt 2) |
| Panel holt nichts, `panels list` zeigt keinen Abruf | Token nicht in der Service-Auth-Regel, abgelaufen, oder nicht mit `panels token` zugeordnet | Access-Protokolle in Zero Trust ansehen; Abschnitt 6, Schritt 1 und 2 |
| `/settings` antwortet 404 | `SETTINGS_PAGE=1` fehlt | `.env`, dann `docker compose up -d` |
| Der LAN-Port antwortet 421 | Name mit Punkt (etwa `nas.fritz.box`), der nicht in `LAN_HOSTS` steht | `LAN_HOSTS` ergänzen |

---

## 6. Ein Panel für Anna einrichten

Ein Panel, ein Service-Token, eine Adresse. Die Befehle laufen auf dem Server im Projektordner.

1. **Service-Token anlegen** (Service credentials → Service Tokens → Create Service Token), Name `flightpanel-anna`, längste Dauer. Client-ID und Secret sofort kopieren: Das Secret zeigt Cloudflare nur einmal. Dann in der Anwendung `flightpanel` die Regel **Service Auth** öffnen und diesen Token als eigenen Eintrag ergänzen.
2. **Panel anlegen und zuordnen:**

   ```bash
   docker compose exec flightpanel python3 -m flightpanel.panels add anna --name "Anna"
   docker compose exec flightpanel python3 -m flightpanel.panels token anna <client-id>
   docker compose exec flightpanel python3 -m flightpanel.panels email anna anna@example.com
   ```

   Die Client-ID sieht aus wie `0123456789abcdef0123456789abcdef.access`. Mehrere Adressen für ein Panel gehen (`email` noch einmal), eine Adresse gehört aber nur zu einem Panel. Standort und Sichtfeld stellt Anna selbst ein (Schritt 5); vorab geht auch `panels set anna HOME_LAT=<breite> HOME_LON=<länge>`.
3. **Adresse in die Gruppe** `flightpanel` aufnehmen (Policies → Rule groups → `flightpanel` → Emails). Beides muss stimmen: Die Gruppe lässt Anna an Cloudflare vorbei, `panels email` sagt dem Server, welches Panel ihres ist. Fehlt eins, kommt sie nicht durch.
4. **Panel vorbereiten**, bevor es rausgeht: einmal per USB mit der Firmware für Freunde flashen (Abschnitt 9). Dann ins Portal: Ist noch nichts eingerichtet, öffnet es von selbst; sonst beim Einschalten die BOOT-Taste 3 Sekunden halten (sie muss im eingebauten Zustand erreichbar bleiben). Das Panel spannt das WLAN `flightpanel-XXXX` auf, das Passwort zeigt das LED-Panel. Mit dem Handy verbinden und unter **Erweitert** eintragen: Server `https://flightpanel.example.com`, Client-ID, Secret. Das Secret lässt sich nur überschreiben, nie anzeigen; ändert man den Server, löscht das Panel den Token. Anna bekommt weder Client-ID noch Secret je zu sehen.

   Probe zu Hause, mit dem eigenen WLAN: Das Panel holt über Cloudflare sein erstes Bild, und `panels list` zeigt "zuletzt …" mit Firmware-Version und WLAN-Stärke. Ohne Panel geht dieselbe Probe mit curl:

   ```bash
   read -rs SECRET        # Secret eintippen, es erscheint weder auf dem Bildschirm noch im Verlauf
   curl -s -o /dev/null -w '%{http_code}\n' -H 'CF-Access-Client-Id: <client-id>' -H "CF-Access-Client-Secret: $SECRET" https://flightpanel.example.com/panel/frame
   curl -s -o /dev/null -w '%{http_code}\n' -H 'CF-Access-Client-Id: <client-id>' -H "CF-Access-Client-Secret: $SECRET" https://flightpanel.example.com/settings
   ```

   Das erste muss 200 geben (mit dem ETag der Antwort als `If-None-Match` beim zweiten Mal 304 ohne Bild), das zweite 403: Ein Panel-Token darf nur `/panel/*`.
5. **Übergeben.** Anna schaltet das Panel ein. Es kennt ihr WLAN nicht und öffnet spätestens nach 5 Minuten ohne WLAN das Portal, diesmal nur mit den WLAN-Feldern: Server und Token lassen sich dort nicht ändern, damit in Funkreichweite niemand das Panel umleiten oder das Secret auslesen kann. Anna verbindet ihr Handy mit `flightpanel-XXXX`, wählt ihr WLAN (2,4 GHz, ein ESP32 kennt kein 5 GHz) und gibt das Passwort ein. Dann öffnet sie `https://flightpanel.example.com/settings`, gibt ihre Adresse ein, tippt den Code aus der Mail ein (er kommt von Cloudflare und landet manchmal im Spam-Ordner) und stellt Standort und Sichtfeld ein (Abschnitt 8).

---

## 7. Ein Panel wieder wegnehmen

Zuerst am Server, dann in Cloudflare, dann am Panel:

```bash
docker compose exec flightpanel python3 -m flightpanel.panels unlink anna@example.com    # nur die Adresse
docker compose exec flightpanel python3 -m flightpanel.panels unlink <client-id>         # nur den Token
docker compose exec flightpanel python3 -m flightpanel.panels remove anna --yes          # Panel samt Album, Statistik, Adressen und Token
```

`unlink` und `remove` sagen am Ende, was der Server nicht selbst erledigen kann:

1. **Service-Token in Zero Trust löschen** (Service credentials → Service Tokens) und aus der Service-Auth-Regel nehmen. Sonst ruft, wer das Panel hat, weiter ab, bis er abläuft.
2. **Adresse aus der Gruppe `flightpanel` nehmen.**
3. **Panel auf Werkseinstellungen zurücksetzen:** BOOT beim Einschalten halten, im Portal unter Erweitert. Der Token liegt unverschlüsselt im Speicher des Panels.

Bis 1 und 2 erledigt sind, erreicht der Ehemalige noch die Anmeldeseite und die zwei Firmware-Dateien. Daten nicht: Der Server kennt ihn nicht mehr.

---

## 8. Einstellungen und Sichtfeld eichen

Mit `SETTINGS_PAGE=1` gibt es `/settings`: für Freunde unter `https://flightpanel.example.com/settings`, für das Panel am LAN-Port unter `http://<server>:8080/settings`. Die Seite ist für das Handy gebaut, weil man damit am Fenster steht: eine Spalte, große Schaltflächen, eine Tabelle, die auch schmal lesbar bleibt.

Sie stellt dieselben Panel-Einstellungen ein wie `panels set` (`panels keys` listet sie auf) und kennt nur diese, nie Einstellungen der `.env`. Gespeichert gilt es auf dem Panel nach spätestens einer Minute, ohne Neustart. Ungültige Eingaben lehnt die Seite mit einem Hinweis am Feld ab und speichert dann nichts; ebenso ein Fenster, das weiter als `PANEL_MAX_KM` vom Flughafen liegt.

So eicht man das Sichtfeld, am Fenster, mit dem Handy in der Hand:

1. **Standort:** die Koordinaten der Stelle am Fenster aus der Karten-App kopieren (Stelle lange drücken) und einfügen; die Seite versteht die Schreibweise "Breite, Länge". Dazu `HOME_ALT_M`, die Höhe der Augen am Fenster über dem Meer.
2. **Flieger abwarten.** Die Tabelle zeigt alle Flugzeuge der Umgebung, alle 5 Sekunden neu: Rufzeichen, Typ, Richtung, Höhenwinkel über dem Horizont, Entfernung und ob es im Sichtfeld liegt.
3. **Ränder setzen:** Das Sichtfeld läuft im Uhrzeigersinn von Anfang bis Ende (0 = Nord, 90 = Ost). Verschwindet ein Flugzeug hinter einem Haus, in der Tabelle suchen und "als Anfang des Sichtfelds" oder "als Ende des Sichtfelds" tippen; die Richtung landet im Formular. "Als Mindesthöhe" übernimmt den Höhenwinkel, für Dächer und Bäume. Die Sichtweite ist die Entfernung, bis zu der man Flugzeuge noch sieht: Anfliegende erkennt man nachts mit ihren Landescheinwerfern oft aus 50 bis 60 km, hohe Überflüge sind blass, Wolken verdecken sie.
4. **Nur das Sichtfeld zeigen** einschalten: Das Panel zeigt dann nur, was man wirklich sieht.
5. **Speichern**, ein paar weitere Flieger ansehen und nachstellen. Echte Sichtungen taugen mehr als eine Skizze.

---

## 9. Firmware-Updates

Die Panels der Freunde aktualisieren sich selbst, aber nur mit Abbildern, die mit dem Schlüssel des Betreibers signiert sind. Sie fragen beim Start (nach dem ersten Bild) und alle 6 Stunden `/panel/firmware.txt` ab. Diese Beschreibung nennt Version (Bauzeit als `JJMMTTHHMM` in UTC), Größe, SHA-256 und Signatur. Ein Panel nimmt nur eine höhere Version an, nie eine ältere, und nur, wenn Signatur und Prüfsumme stimmen.

**Schlüssel anlegen (einmal)**, auf dem Laptop, in einem Ordner außerhalb des Repos und außerhalb jedes synchronisierten Ordners (OneDrive, Dropbox, iCloud; unter Windows legt OneDrive Dokumente, Desktop und Bilder ungefragt in die Cloud):

```bash
openssl genpkey -algorithm EC -pkeyopt ec_paramgen_curve:P-256 -aes-256-cbc -out fw-signing.pem
openssl pkey -in fw-signing.pem -pubout -out fw-pubkey.pem
```

Der erste Befehl fragt nach einer Passphrase: eine lange, die nur der Mensch kennt. `fw-signing.pem` bleibt auf dem Laptop. Eine Sicherung davon gehört offline aufbewahrt, etwa auf einem Stick im Schrank; die Passphrase liegt nicht daneben. Geht der Schlüssel verloren, nimmt kein Panel mehr ein Update an, und jedes braucht einen USB-Besuch. Der öffentliche Schlüssel `fw-pubkey.pem` darf überall liegen; er wird in die Firmware einkompiliert (`firmware/include/fw_pubkey.h`, nicht im Repo, Vorlage `fw_pubkey.h.example`). Wer aus dem Repo baut, nimmt einen eigenen Schlüssel oder keinen: Ohne `fw_pubkey.h` ist die Update-Funktion gar nicht einkompiliert.

**Erstes Flashen:** einmal per USB, bevor das Panel rausgeht, mit einem Bau ohne `secrets.h` (Bauen und Flashen: Bauanleitung, Abschnitt 4). Das gilt auch für die Partitionstabelle mit den zwei Speicherplätzen für Update und Rückfall; die ändert kein Update.

**Eine Version veröffentlichen.** Es gibt zwei Kanäle, `canary` und `stable`. Das Panel am LAN-Port ist der Prüfstand und steht auf `canary`; für Freunde gilt immer `stable`: Ihre Abrufe der Firmware-Dateien kommen über den offenen Pfad an, der Server kann sie keinem Panel zuordnen.

1. **Bauen und signieren**, auf dem Laptop:

   ```bash
   sh tools/fw_release.sh
   ```

   Das Skript baut die Firmware in der festgelegten Docker-Umgebung, schreibt die Beschreibung, signiert sie (dafür fragt es nach der Passphrase) und legt `firmware.txt` und `firmware.bin` in `data/firmware/canary/` ab. Den Pfad zum Schlüssel nennt der Kopf des Skripts. **Ein Mensch führt es aus, nie ein KI-Agent und keine Automatik:** Schlüssel und Passphrase dürfen nirgends liegen, wo so etwas drankommt.
2. **Auf den Server kopieren**, von Hand, in dessen `data/firmware/canary/` (der Ordner muss existieren und für den Benutzer des Containers lesbar sein, `PUID`/`PGID`):

   ```bash
   scp data/firmware/canary/firmware.bin data/firmware/canary/firmware.txt <server>:<pfad>/data/firmware/canary/
   ```
3. **Am eigenen Panel prüfen.** Es steht auf `canary` (einmalig: `panels channel home canary`), holt die neue Version beim nächsten Start oder spätestens nach 6 Stunden, und `panels list` zeigt die Firmware-Version je Panel.
4. **Befördern**, wenn sie einige Tage gut läuft: Das ist ein Kopieren, auf dem Server, erst das Abbild, zuletzt die Beschreibung (ein Panel liest die Beschreibung zuerst). Innerhalb von 6 Stunden holen sich dann die Panels der Freunde die Version.

   ```bash
   cd <pfad>/data/firmware && mkdir -p stable && cp canary/firmware.bin stable/ && cp canary/firmware.txt stable/
   ```

**Wenn eine Version nichts taugt:** Ein neues Abbild startet "unbestätigt" und gilt erst als gut, wenn es sein erstes Bild vom Server geholt hat. Stürzt es vorher ab oder holt es 10 Minuten lang nichts, startet das Panel wieder das vorige. Darum zuerst `canary` am eigenen Panel. Eine schlechte Version, die schon in `stable` liegt, nimmt man nicht zurück, denn ältere Versionen nimmt kein Panel an: Man baut eine neue, höhere mit dem alten Stand (`git revert`, dann `tools/fw_release.sh`). Zieht man eine Version aus `canary` oder `stable` zurück (Dateien gelöscht oder die vorigen zurückgelegt), muss danach auf jedem Kanal eine neue, höhere folgen, auch auf dem, auf dem sie nie lag. Die zurückgezogene bleibt echt signiert, und ihre zwei Dateien waren ohne Anmeldung abrufbar. Wer den Netzweg eines Panels kontrolliert, etwa sein WLAN, zwingt es mit 24 Stunden Zertifikatsfehlern in den Rettungsmodus und spielt ihm dort, ohne Zertifikatsprüfung, genau diese Version ein, solange sie höher ist als seine. Erst eine höhere auf jedem Kanal macht sie für jedes Panel zu einer älteren.

**Wenn Cloudflares Zertifikate wechseln:** Die Firmware vertraut nur einer festen Liste von Wurzelzertifikaten (`roots.pem`), nicht dem ganzen Web. Passt nach einem Wechsel keines mehr, scheitern die Panels an der Zertifikatsprüfung. Nach 24 Stunden holt ein Panel dann im Rettungsmodus nur Beschreibung und Abbild, ohne das Zertifikat zu prüfen und ohne Token; es vertraut allein Signatur, Prüfsumme und höherer Version. So holt eine neue Version mit aktualisierter Liste das Panel ohne USB-Besuch zurück. Deshalb sind die zwei Dateien ohne Anmeldung erreichbar. Als Vorwarnung prüft der Server einmal am Tag mit derselben Liste (`server/flightpanel/certs/roots.pem`), ob das Zertifikat seines Hostnamens noch dazu passt, so wie ein Panel: nur TLS 1.2, Name geprüft. Wenn nicht, meldet `/ok` "Root-Liste veraltet" und `/issues` trägt es unter `tls_roots` ein, lange bevor ein Panel dunkel bleibt. Ist dagegen nur das Netz weg, steht das unter `source_error`, und `/ok` bleibt grün.

---

## 10. Wartung und Überwachung

Was noch einen Menschen braucht:

| Wann | Was | Bleibt es aus |
|---|---|---|
| vor Ablauf der Service-Tokens (Mail von Cloudflare, dazu ein Kalendereintrag; jährlich, wenn nur ein Jahr geht) | in Zero Trust bei jedem Token **Refresh** | Die Panels der Freunde bleiben dunkel. Ungefährlich. |
| wenn `/ok` "Root-Liste veraltet" meldet | `tools/fw_release.sh` mit aktualisierter `roots.pem`; die Panels holen sie über den Rettungsmodus | Die Panels bleiben dunkel, bis es getan ist. Ungefährlich. |
| wenn ein Freund aufhört | die Liste aus Abschnitt 7: Token, Gruppe, Werkseinstellungen | Der Ehemalige erreicht weiter die Anmeldeseite und die zwei Firmware-Dateien, aber keine Daten. |
| alle paar Monate | cloudflared: eine Version nehmen, die mindestens 7 Tage alt ist, und den Digest in `compose.override.yml` ersetzen (Abschnitt 3) | Es läuft weiter, doch Cloudflare unterstützt alte Versionen irgendwann nicht mehr. |
| von selbst | Wechsel der Access-Schlüssel, Abhängigkeiten mit Wartezeit (Escrow), Basis-Image des Servers, Aufräumen der Datenbank, Rückfall der Firmware | – |

Ein Uptime-Monitor im Homelab (Uptime Kuma o. ä.) auf `http://<server>:8080/ok` macht aus jedem "ungefährlichen" Ausfall oben eine Nachricht, statt dass man Wochen später ein dunkles Panel findet. `/ok` zählt nur das Panel am LAN-Port: Zieht ein Freund den Stecker, wird es nicht rot. Wann ein Panel zuletzt abgerufen hat, zeigt `panels list`, je Panel mit Firmware-Version und WLAN-Stärke, und `/health` unter `panels`.

---

## 11. Die Befehle

Alle laufen als `docker compose exec flightpanel python3 -m flightpanel.panels <Befehl>`. Sie schreiben in die Datenbank, während der Server läuft; er übernimmt die Änderung spätestens nach einer Minute.

| Befehl | Wirkung |
|---|---|
| `list` | alle Panels: Einstellungen, Zugänge, letzter Abruf, Firmware, WLAN-Stärke |
| `keys` | alle Panel-Einstellungen mit Standardwert und Erklärung |
| `show <id>` | was bei einem Panel gesetzt ist |
| `set <id> KEY=WERT …` | setzen, geprüft wie in der `.env`; `KEY=` leer setzt zurück |
| `unset <id> KEY …` | zurück auf den Standard |
| `add <id> [--name NAME]` | weiteres Panel anlegen |
| `remove <id> --yes` | Panel samt Album, Statistik und Zugängen löschen |
| `email <id> <adresse>` | Adresse darf das Panel im Browser sehen und einstellen |
| `token <id> <client-id>` | Service-Token, mit dem das Panel abruft |
| `unlink <adresse\|client-id>` | Adresse oder Token wieder lösen |
| `channel <id> stable\|canary` | Firmware-Kanal des Panels |
| `backup <datei>` | Kopie der Datenbank, auch im Betrieb; eine vorhandene Datei ersetzt `--force` |

---

## 12. Hinweise

**Logos:** Die Airline-Logos holt der Server für den persönlichen Gebrauch von den CDNs Dritter (Kiwi.com, FlightAware, Google) und verteilt sie nicht weiter: Sie liegen nur in `data/logos/` auf dem Server, nicht im Repo, und Freunde bekommen nur das fertige Bild ihres Panels.

**Das Repo ist öffentlich.** Was eingecheckt wird, kann jeder lesen, samt Verlauf. Domain, Team-Name, Tokens, Adressen und Koordinaten stehen deshalb nur in `.env`, `compose.override.yml`, `cloudflared.env` und der Datenbank, die Git alle ignoriert; die Doku nimmt Platzhalter.

**Der Escrow-Lauf braucht Aktivität.** GitHub schaltet in öffentlichen Repositories geplante Workflows ab, wenn 60 Tage lang nichts im Repo passiert. Der wöchentliche Escrow-Lauf ([`escrow.yml`](../.github/workflows/escrow.yml)), der neue Abhängigkeiten nach der Wartezeit einspielt, ist so ein Workflow. Wird es still im Repo, braucht er einen Keepalive (etwa einen Workflow, der in ruhigen Zeiten gelegentlich einen Commit macht); sonst bleiben die Locks stehen, bis man den Workflow unter Actions wieder einschaltet.
