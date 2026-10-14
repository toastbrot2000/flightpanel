// Einstellungen im NVS, Vorgaben aus secrets.h (siehe settings.h).
#include "settings.h"

#include <Preferences.h>
#include <WiFi.h>
#include <nvs_flash.h>

#include "defaults.h"

static const char* NS = "flightpanel";   // NVS-Namensraum (höchstens 15 Zeichen, ebenso die Schlüssel)

#ifdef WIFI_SSID
static const char* DEFAULT_SSID = WIFI_SSID;
#else
static const char* DEFAULT_SSID = "";
#endif
#ifdef WIFI_PASS
static const char* DEFAULT_PASS = WIFI_PASS;
#else
static const char* DEFAULT_PASS = "";
#endif
#if defined(SERVER_URL)
static const char* DEFAULT_SERVER = SERVER_URL;
#elif defined(FRAME_URL)
static const char* DEFAULT_SERVER = FRAME_URL;   // ältere secrets.h: .../frame, normalizeServer kürzt es
#else
static const char* DEFAULT_SERVER = "";
#endif
#ifdef ACCESS_CLIENT_ID
static const char* DEFAULT_CLIENT_ID = ACCESS_CLIENT_ID;
#else
static const char* DEFAULT_CLIENT_ID = "";
#endif
#ifdef ACCESS_CLIENT_SECRET
static const char* DEFAULT_CLIENT_SECRET = ACCESS_CLIENT_SECRET;
#else
static const char* DEFAULT_CLIENT_SECRET = "";
#endif

// putString meldet die geschriebene Länge, bei "" also 0 auch im Erfolgsfall: nur Fehler bei Nicht-Leerem zählen.
static bool put(Preferences& p, const char* key, const String& value) {
  return p.putString(key, value) == value.length();
}

namespace settings {

Settings load() {
  Preferences p;
  const bool open = p.begin(NS, false);   // schreibbar: legt den Namensraum beim ersten Start an
  auto get = [&](const char* key, const char* fallback) {
    return open && p.isKey(key) ? p.getString(key) : String(fallback);   // isKey: kein Fehler-Log bei Fehlen
  };
  Settings s;
  s.ssid = get("ssid", DEFAULT_SSID);
  s.pass = get("pass", DEFAULT_PASS);
  s.server = normalizeServer(get("server", DEFAULT_SERVER));
  s.clientId = get("cid", DEFAULT_CLIENT_ID);
  s.clientSecret = get("csecret", DEFAULT_CLIENT_SECRET);
  if (open) p.end();
  return s;
}

bool saveWifi(const String& ssid, const String& pass) {
  Preferences p;
  if (!p.begin(NS, false)) return false;
  const bool ok = put(p, "ssid", ssid) && put(p, "pass", pass);
  p.end();
  return ok;
}

bool saveServer(const String& server) {
  // Eine neue Adresse löscht das Token: Es gehört zum alten Server und soll nie an einen anderen gehen. Leer
  // gespeichert statt entfernt, damit auch eine Vorgabe aus secrets.h nicht wieder auflebt.
  Preferences p;
  if (!p.begin(NS, false)) return false;
  const bool ok = put(p, "server", server) && put(p, "cid", "") && put(p, "csecret", "");
  p.end();
  return ok;
}

bool saveToken(const String& clientId, const String& clientSecret) {
  Preferences p;
  if (!p.begin(NS, false)) return false;
  const bool ok = put(p, "cid", clientId) && put(p, "csecret", clientSecret);
  p.end();
  return ok;
}

void factoryReset() {
  // Der ganze NVS-Bereich, nicht nur der eigene Namensraum: auch was der WLAN-Treiber dort abgelegt hat.
  // WLAN vorher aus, damit der Treiber seine NVS-Einträge nicht mehr offen hält.
  Serial.println("Zurücksetzen: NVS wird gelöscht, Neustart");
  WiFi.mode(WIFI_OFF);
  const esp_err_t err = nvs_flash_erase();
  if (err != ESP_OK) Serial.printf("NVS löschen FEHLER %d\n", (int)err);
  delay(100);
  ESP.restart();
  while (true) {}
}

String normalizeServer(String url) {
  url.trim();
  String lower = url;
  lower.toLowerCase();
  int hostStart;
  if (lower.startsWith("http://")) hostStart = 7;
  else if (lower.startsWith("https://")) hostStart = 8;
  else return "";
  url = lower.substring(0, hostStart) + url.substring(hostStart);   // Schema klein, der Rest wie eingegeben
  while (url.endsWith("/")) url.remove(url.length() - 1);
  if (url.endsWith("/panel/frame")) url.remove(url.length() - 12);
  else if (url.endsWith("/frame")) url.remove(url.length() - 6);
  while (url.endsWith("/")) url.remove(url.length() - 1);
  if ((int)url.length() <= hostStart || url.length() > 200) return "";

  // Host[:Port] bis zum ersten /: Buchstaben, Ziffern, - . _; der Port 1 bis 65535. Kein Benutzer@ (würde
  // HTTPClient zu Basic-Auth machen), keine IPv6-Klammern (kann HTTPClient 2.0.17 nicht).
  int slash = url.indexOf('/', hostStart);
  if (slash < 0) slash = url.length();
  const int colon = url.indexOf(':', hostStart);
  const int hostEnd = (colon >= 0 && colon < slash) ? colon : slash;
  if (hostEnd == hostStart) return "";
  for (int i = hostStart; i < hostEnd; i++) {
    const char c = url[i];
    if (!isalnum((unsigned char)c) && c != '-' && c != '.' && c != '_') return "";
  }
  if (hostEnd == colon) {
    const String port = url.substring(colon + 1, slash);
    if (port.length() == 0 || port.length() > 5) return "";
    for (size_t i = 0; i < port.length(); i++) if (!isdigit((unsigned char)port[i])) return "";
    if (port.toInt() < 1 || port.toInt() > 65535) return "";
  }
  // Pfad dahinter: druckbar, ohne Leerzeichen, Anfrage (?) und Anker (#), da /panel/frame angehängt wird
  for (int i = slash; i < (int)url.length(); i++) {
    const unsigned char c = url[i];
    if (c <= ' ' || c >= 0x7f || c == '?' || c == '#' || c == '"' || c == '<' || c == '>' || c == '\\') return "";
  }
  return url;
}

bool validSsid(const String& ssid) {
  return ssid.length() >= 1 && ssid.length() <= 32;
}

bool validPass(const String& pass) {
  const size_t n = pass.length();
  if (n == 0) return true;   // offenes WLAN
  if (n == 64) {             // Schlüssel als 64 Hex-Ziffern
    for (size_t i = 0; i < n; i++) if (!isxdigit((unsigned char)pass[i])) return false;
    return true;
  }
  if (n < 8 || n > 63) return false;
  for (size_t i = 0; i < n; i++) {
    const unsigned char c = pass[i];
    if (c < ' ' || c > '~') return false;   // WPA2-Passphrase: druckbares ASCII
  }
  return true;
}

bool validToken(const String& value) {
  // Geht als HTTP-Header hinaus: druckbares ASCII ohne Leerzeichen, nie CR/LF
  if (value.length() < 1 || value.length() > 128) return false;
  for (size_t i = 0; i < value.length(); i++) {
    const unsigned char c = value[i];
    if (c <= ' ' || c > '~') return false;
  }
  return true;
}

}  // namespace settings
