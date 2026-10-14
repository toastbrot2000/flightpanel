// Einrichtungs-WLAN (siehe portal.h). Die Seite ist für das Handy gebaut: eine Spalte, Felder ab 16 px (sonst
// zoomt iOS hinein), große Knöpfe, kein JavaScript. Alles, was von außen kommt (WLAN-Namen aus der Umgebung,
// gespeicherte Werte, Eingaben), geht nur escaped in die Seite. Passwort und Secret stehen nie darin.
#include "portal.h"

#include <DNSServer.h>
#include <WebServer.h>
#include <WiFi.h>
#include <esp_random.h>

#include <algorithm>
#include <vector>

#include "config.h"
#include "display.h"
#include "settings.h"
#include "watchdog.h"

namespace {

struct Network {
  String ssid;
  int32_t rssi;
};

struct State {
  portal::Mode mode = portal::Mode::Full;
  const Settings* current = nullptr;
  WebServer* server = nullptr;
  IPAddress ip;
  std::vector<Network> networks;
  uint32_t restartAt = 0;   // != 0: die Antwort ist unterwegs, danach Neustart
  bool wipe = false;        // vor dem Neustart alles löschen
};

State st;

const char CSS[] =
    ":root{color-scheme:light dark;--a:#d9771a}*{box-sizing:border-box}"
    "body{font:17px/1.45 system-ui,-apple-system,'Segoe UI',sans-serif;margin:0 auto;padding:16px;max-width:32rem}"
    "h1{font-size:1.4em;margin:.2em 0 .8em}h2{font-size:1.1em;margin:1.2em 0 .5em}"
    "label{display:block;margin:1em 0 .35em;font-weight:600}"
    "input[type=text],input[type=url],input[type=password]{width:100%;font-size:17px;padding:.7em;"
    "border:1px solid #8888;border-radius:8px}"
    ".n{display:flex;align-items:center;gap:.7em;margin:.45em 0;padding:.8em .7em;border:1px solid #8886;"
    "border-radius:10px;font-weight:400}.n input{width:1.3em;height:1.3em;margin:0;flex:none}"
    ".n span{flex:1;overflow-wrap:anywhere}.n small{opacity:.7}"
    ".h{font-size:.9em;opacity:.75;margin:.35em 0 0}.e{background:#b3261e;color:#fff;padding:.8em;border-radius:8px}"
    "button{display:block;width:100%;font:600 18px system-ui,sans-serif;padding:.9em;margin:1.4em 0 .4em;border:0;"
    "border-radius:10px;background:var(--a);color:#fff}.r{background:#b3261e}a{color:var(--a)}"
    "details{margin-top:1.6em;border-top:1px solid #8886;padding-top:.5em}"
    "summary{font-size:1.1em;font-weight:600;padding:.5em 0}";

void esc(String& out, const String& s) {
  for (size_t i = 0; i < s.length(); i++) {
    const unsigned char c = s[i];
    switch (c) {
      case '&': out += "&amp;"; break;
      case '<': out += "&lt;"; break;
      case '>': out += "&gt;"; break;
      case '"': out += "&quot;"; break;
      case '\'': out += "&#39;"; break;
      default: out += (c < 0x20 || c == 0x7f) ? '?' : (char)c;
    }
  }
}

// Wert eines Feldes: bei der erneut gezeigten Seite nach einem Fehler das Eingegebene, sonst das Gespeicherte
String field(const char* name, const String& stored) {
  return st.server->hasArg(name) ? st.server->arg(name) : stored;
}

void begin(String& out, const char* title) {
  out.reserve(8192);
  out = "<!doctype html><html lang=\"de\"><head><meta charset=\"utf-8\">"
        "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\"><title>";
  out += title;
  out += "</title><style>";
  out += CSS;
  out += "</style></head><body><h1>";
  out += title;
  out += "</h1>";
}

void send(const String& html) {
  WebServer& s = *st.server;
  s.sendHeader("Cache-Control", "no-store");
  s.sendHeader("X-Content-Type-Options", "nosniff");
  s.sendHeader("Referrer-Policy", "no-referrer");
  s.sendHeader("Content-Security-Policy",
               "default-src 'none'; style-src 'unsafe-inline'; form-action 'self'; frame-ancestors 'none'");
  s.send(200, "text/html; charset=utf-8", html);
}

void sendForm(const String& error) {
  const Settings& cur = *st.current;
  const bool full = st.mode == portal::Mode::Full;
  String p;
  begin(p, "flightpanel einrichten");
  if (error.length()) {
    p += "<p class=\"e\">";
    esc(p, error);
    p += "</p>";
  }
  if (!full) p += "<p>Das Panel findet sein WLAN nicht. Hier lässt sich ein neues eintragen.</p>";
  p += "<form method=\"post\" action=\"/save\"><h2>WLAN</h2>";
  const String chosen = field("ssid", cur.ssid);
  if (st.networks.empty()) p += "<p class=\"h\">Kein WLAN gefunden.</p>";
  for (const Network& n : st.networks) {
    p += "<label class=\"n\"><input type=\"radio\" name=\"ssid\" value=\"";
    esc(p, n.ssid);
    p += n.ssid == chosen ? "\" checked><span>" : "\"><span>";
    esc(p, n.ssid);
    p += "</span><small>";
    p += n.rssi >= -60 ? "stark" : n.rssi >= -72 ? "mittel" : "schwach";
    p += "</small></label>";
  }
  p += "<label for=\"o\">Anderes WLAN</label><input type=\"text\" id=\"o\" name=\"ssid_other\" maxlength=\"32\" "
       "autocapitalize=\"none\" autocorrect=\"off\" spellcheck=\"false\" placeholder=\"Name eintippen\" value=\"";
  esc(p, field("ssid_other", ""));
  p += "\"><p class=\"h\"><a href=\"/scan\">Neu suchen</a>. Nur 2,4-GHz-WLANs; versteckte bitte eintippen.</p>"
       "<label for=\"p\">WLAN-Passwort</label><input type=\"text\" id=\"p\" name=\"pass\" maxlength=\"64\" "
       "autocomplete=\"off\" autocapitalize=\"none\" autocorrect=\"off\" spellcheck=\"false\">";
  if (cur.ssid.length()) p += "<p class=\"h\">Leer lassen: Beim gleichen WLAN bleibt das gespeicherte Passwort.</p>";

  if (full) {
    p += cur.server.length() ? "<details><summary>Erweitert</summary>" : "<details open><summary>Erweitert</summary>";
    p += "<label for=\"s\">Server</label><input type=\"url\" id=\"s\" name=\"server\" "
         "placeholder=\"http://192.168.1.10:8080\" autocapitalize=\"none\" autocorrect=\"off\" spellcheck=\"false\" "
         "value=\"";
    esc(p, field("server", cur.server));
    p += "\"><p class=\"h\">Im eigenen Heimnetz http://&lt;Server&gt;:8080, über Cloudflare https://&lt;Name&gt;. "
         "Eine neue Adresse löscht das Token.</p>"
         "<label for=\"c\">Client-ID (Cloudflare Access)</label><input type=\"text\" id=\"c\" name=\"cid\" "
         "autocapitalize=\"none\" autocorrect=\"off\" spellcheck=\"false\" value=\"";
    esc(p, field("cid", cur.clientId));
    p += "\"><label for=\"k\">Client-Secret</label><input type=\"password\" id=\"k\" name=\"secret\" "
         "autocomplete=\"new-password\" placeholder=\"";
    p += cur.clientSecret.length() ? "gespeichert, leer lassen = behalten" : "nicht gesetzt";
    p += "\"><p class=\"h\">Nur mit https. Das Secret wird nie angezeigt, nur überschrieben. "
         "Client-ID leeren entfernt das Token.</p></details>";
  }
  p += "<button type=\"submit\">Speichern und neu starten</button></form>";
  if (full) {
    p += "<form method=\"post\" action=\"/reset\"><details><summary>Zurücksetzen</summary>"
         "<p>Löscht alles Gespeicherte (WLAN, Server, Token) und startet neu. Ohne Einrichtung öffnet das Panel "
         "danach wieder dieses Einrichtungs-WLAN, mit neuem Passwort auf dem Panel.</p>"
         "<label class=\"n\"><input type=\"checkbox\" name=\"confirm\" value=\"ja\" required>"
         "<span>Ja, alles löschen</span></label><button class=\"r\" type=\"submit\">Alles löschen</button>"
         "</details></form>";
  }
  p += "</body></html>";
  send(p);
}

void sendDone(const char* title, const String& text) {
  String p;
  begin(p, title);
  p += "<p>";
  p += text;   // schon escaped
  p += "</p></body></html>";
  send(p);
  st.restartAt = millis() + 1500;   // Antwort ausliefern, dann Neustart
}

void scan() {
  watchdog::feed();
  const int n = WiFi.scanNetworks(false, false, false, 300);   // aktiv, bis 300 ms je Kanal: höchstens ~4 s
  watchdog::feed();
  st.networks.clear();
  for (int i = 0; i < n; i++) {
    const String name = WiFi.SSID(i);
    if (name.isEmpty()) continue;   // verstecktes WLAN: Namen eintippen
    const int32_t rssi = WiFi.RSSI(i);
    auto same = std::find_if(st.networks.begin(), st.networks.end(), [&](const Network& x) { return x.ssid == name; });
    if (same == st.networks.end()) st.networks.push_back({name, rssi});
    else same->rssi = std::max(same->rssi, rssi);
  }
  WiFi.scanDelete();
  std::sort(st.networks.begin(), st.networks.end(), [](const Network& a, const Network& b) { return a.rssi > b.rssi; });
  if (st.networks.size() > 20) st.networks.resize(20);
}

void redirectHome() {
  st.server->sendHeader("Location", String("http://") + st.ip.toString() + "/", true);
  st.server->send(302, "text/plain", "");
}

void handleRoot() {
  // Abfragen der Handys auf fremde Namen (Captive-Portal-Erkennung) landen auf der eigenen Adresse
  if (st.server->hostHeader() != st.ip.toString()) return redirectHome();
  sendForm("");
}

void handleScan() {
  scan();
  redirectHome();
}

void handleSave() {
  WebServer& s = *st.server;
  const Settings& cur = *st.current;
  const bool full = st.mode == portal::Mode::Full;

  // WLAN: ein eingetippter Name gilt vor der Auswahl. Ohne beides bleibt das WLAN, wie es ist (nur im vollen
  // Portal erlaubt: Server und Token lassen sich vorab ohne WLAN eintragen, etwa bevor ein Panel verschenkt wird).
  String ssid = s.arg("ssid_other");
  if (ssid.isEmpty()) ssid = s.arg("ssid");
  String pass = s.arg("pass");
  if (ssid.isEmpty()) {
    if (!full) return sendForm("Bitte ein WLAN wählen oder seinen Namen eintippen.");
    if (pass.length()) return sendForm("Zum Passwort bitte auch das WLAN wählen oder eintippen.");
    ssid = cur.ssid;
    pass = cur.pass;
  } else if (!settings::validSsid(ssid)) {
    return sendForm("Der WLAN-Name ist zu lang (höchstens 32 Zeichen).");
  } else if (pass.isEmpty() && ssid == cur.ssid) {
    pass = cur.pass;   // gleiches WLAN, Feld leer: das gespeicherte Passwort bleibt
  } else if (!settings::validPass(pass)) {
    return sendForm("Das WLAN-Passwort hat 8 bis 63 Zeichen (oder ist leer bei einem offenen WLAN).");
  }

  // Erweitert: nur im vollen Portal. Im Ausfall-Portal werden diese Felder nicht einmal gelesen.
  String server = cur.server, cid = cur.clientId, secret = cur.clientSecret;
  if (full) {
    String raw = s.arg("server");
    raw.trim();
    server = settings::normalizeServer(raw);
    if (raw.length() && server.isEmpty())
      return sendForm("Die Server-Adresse beginnt mit http:// oder https://, etwa http://192.168.1.10:8080.");
    if (server.isEmpty()) return sendForm("Bitte unter „Erweitert“ die Server-Adresse eintragen.");
    if (server != cur.server) cid = secret = "";   // neue Adresse: das alte Token gehört zum alten Server
    String newCid = s.arg("cid"), newSecret = s.arg("secret");
    newCid.trim();
    newSecret.trim();
    if (newCid.isEmpty()) {
      if (newSecret.length()) return sendForm("Zum Secret gehört eine Client-ID.");
      cid = secret = "";   // Client-ID geleert: kein Token
    } else if (newSecret.length()) {
      if (!settings::validToken(newCid) || !settings::validToken(newSecret))
        return sendForm("Client-ID und Secret: ohne Leerzeichen, höchstens 128 Zeichen.");
      cid = newCid;
      secret = newSecret;
    } else if (newCid != cid) {
      return sendForm("Zur Client-ID gehört ihr Secret (bei neuer Server-Adresse beides neu eintragen, "
                      "oder die Client-ID leeren).");
    }
    if (cid.length() && !server.startsWith("https://"))
      return sendForm("Ein Token gilt nur mit einer https://-Adresse.");
  }

  bool ok = true;
  if (ssid != cur.ssid || pass != cur.pass) ok = settings::saveWifi(ssid, pass) && ok;
  if (server != cur.server) ok = settings::saveServer(server) && ok;   // löscht das Token im NVS mit
  if (server != cur.server || cid != cur.clientId || secret != cur.clientSecret)
    ok = settings::saveToken(cid, secret) && ok;
  if (!ok) return sendForm("Speichern fehlgeschlagen. Bitte noch einmal versuchen.");

  String text;
  if (ssid.length()) {
    text = "Das Panel startet neu und verbindet sich mit dem WLAN „";
    esc(text, ssid);
    text += "“. Dieses Einrichtungs-WLAN verschwindet dabei, das Handy geht zurück in sein gewohntes WLAN.</p>"
            "<p>Klappt die Verbindung nicht (Passwort vertippt?), öffnet das Panel nach 5 Minuten wieder ein "
            "Einrichtungs-WLAN, mit neuem Passwort auf dem Panel.";
  } else {
    text = "Ohne WLAN öffnet das Panel nach dem Neustart wieder dieses Einrichtungs-WLAN, mit neuem Passwort auf "
           "dem Panel. Dort lässt sich dann das WLAN eintragen.";
  }
  Serial.println("Einrichtungs-WLAN: gespeichert, Neustart");
  sendDone("Gespeichert", text);
}

void handleReset() {
  if (st.server->arg("confirm") != "ja") return sendForm("Zum Löschen bitte „Ja, alles löschen“ ankreuzen.");
  st.wipe = true;
  sendDone("Wird gelöscht", "Alles Gespeicherte wird gelöscht, das Panel startet neu.");
}

String apName() {
  const uint64_t mac = ESP.getEfuseMac();   // Byte 0 zuerst: die letzten zwei Byte unterscheiden die Panels
  char name[20];
  snprintf(name, sizeof name, "flightpanel-%02X%02X", (unsigned)(uint8_t)(mac >> 32), (unsigned)(uint8_t)(mac >> 40));
  return name;
}

String apPassword() {
  // Erst aufrufen, wenn das Funkteil läuft: Nur dann liefert esp_random() echten Zufall. Gleichverteilt über
  // alle 8 Ziffern, weil 4 000 000 000 ein Vielfaches von 100 000 000 ist.
  uint32_t r;
  do r = esp_random(); while (r >= 4000000000u);
  char pw[9];
  snprintf(pw, sizeof pw, "%08lu", (unsigned long)(r % 100000000u));
  return pw;
}

}  // namespace

namespace portal {

void run(Mode mode, const Settings& current) {
  st = State();
  st.mode = mode;
  st.current = &current;

  WiFi.setAutoReconnect(false);   // sonst sucht der Treiber pausenlos das alte WLAN, und der Access Point wackelt
  WiFi.disconnect();
  WiFi.mode(WIFI_STA);            // Funkteil an, noch ohne Access Point (der hätte sonst die offene Vorgabe)
  scan();
  const String name = apName();
  const String password = apPassword();
  // Schaltet den Access Point dazu (AP+STA, die Station bleibt zum Suchen und für neue Versuche)
  if (!WiFi.softAP(name.c_str(), password.c_str(), 1, 0, 2)) Serial.println("Einrichtungs-WLAN: Start FEHLER");
  st.ip = WiFi.softAPIP();

  DNSServer dns;
  dns.setErrorReplyCode(DNSReplyCode::NoError);
  dns.start(53, "*", st.ip);   // jeder Name zeigt auf das Panel: das Handy öffnet die Seite von selbst
  WebServer server(80);
  st.server = &server;
  server.on("/", HTTP_GET, handleRoot);
  server.on("/scan", HTTP_GET, handleScan);
  server.on("/save", HTTP_POST, handleSave);
  if (mode == Mode::Full) server.on("/reset", HTTP_POST, handleReset);
  server.onNotFound(redirectHome);
  server.begin();

  const char* lines[] = {"WLAN einrichten", name.c_str(), "Passwort", password.c_str()};
  display::showLines(lines, 4);
  Serial.printf("Einrichtungs-WLAN %s, Passwort %s (%s)\n", name.c_str(), password.c_str(),
                mode == Mode::Full ? "voll" : "nur WLAN");

  const bool mayClose = mode == Mode::Outage || current.configured();
  const uint32_t opened = millis();
  uint32_t lastTry = opened;
  while (true) {
    watchdog::feed();
    dns.processNextRequest();
    server.handleClient();
    if (st.restartAt && (int32_t)(millis() - st.restartAt) >= 0) {
      server.stop();
      if (st.wipe) settings::factoryReset();
      ESP.restart();
    }
    if (!st.restartAt && mode == Mode::Outage) {
      if (WiFi.status() == WL_CONNECTED) {
        Serial.println("WLAN wieder da: Einrichtungs-WLAN geschlossen");
        break;
      }
      // Das gespeicherte WLAN weiter versuchen, aber nicht, solange ein Handy verbunden ist: Die Suche wechselt
      // den Kanal und würde es abhängen.
      if (millis() - lastTry >= PORTAL_RETRY_MS && WiFi.softAPgetStationNum() == 0) {
        lastTry = millis();
        WiFi.begin(current.ssid.c_str(), current.pass.c_str());
      }
    }
    if (!st.restartAt && mayClose && millis() - opened >= PORTAL_CLOSE_MS) {
      Serial.println("Einrichtungs-WLAN: Zeit um, geschlossen");
      break;
    }
    delay(2);
  }

  server.stop();
  dns.stop();
  st = State();
  WiFi.softAPdisconnect(true);   // Access Point aus, die Station bleibt
  WiFi.setAutoReconnect(true);
  display::clear();
}

}  // namespace portal
