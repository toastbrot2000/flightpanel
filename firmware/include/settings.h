#pragma once
// Einstellungen im NVS (Preferences, Namensraum "flightpanel"): WLAN, Server-Adresse und das Service-Token für
// Cloudflare Access. Fehlt ein Wert im NVS, gilt die Vorgabe aus include/secrets.h (defaults.h), sonst leer.
// Der Flash ist nicht verschlüsselt: Wer das Panel in der Hand hat, kann das alles auslesen. Darum gilt ein
// Token nur für das Bild dieses einen Panels und wird bei Cloudflare gelöscht, wenn ein Freund aufhört.
#include <Arduino.h>

struct Settings {
  String ssid, pass;                 // WLAN (2,4 GHz)
  String server;                     // Basis-Adresse ohne / am Ende: http://192.168.1.10:8080, https://...
  String clientId, clientSecret;     // Cloudflare-Access-Service-Token, gesendet nur über https
  bool configured() const { return ssid.length() > 0 && server.length() > 0; }
};

namespace settings {
Settings load();
// Schreiben; jede Funktion prüft nichts mehr, das tut das Einrichtungs-WLAN vorher (valid*).
bool saveWifi(const String& ssid, const String& pass);
bool saveServer(const String& server);                           // löscht das gespeicherte Token mit
bool saveToken(const String& clientId, const String& clientSecret);
[[noreturn]] void factoryReset();                                 // ganzer NVS-Bereich, dann Neustart

// Prüfen und vereinheitlichen. normalizeServer: http(s)://Host[:Port][/Pfad], ohne / am Ende; eine alte
// Adresse mit /frame oder /panel/frame am Ende wird gekürzt. Ungültig: "".
String normalizeServer(String url);
bool validSsid(const String& ssid);
bool validPass(const String& pass);                               // leer (offenes WLAN), 8-63 Zeichen, 64 hex
bool validToken(const String& value);                             // druckbares ASCII ohne Leerzeichen, <= 128
}  // namespace settings
