// WLAN als Station (siehe wlan.h).
#include "wlan.h"

#include <WiFi.h>

#include "config.h"
#include "defaults.h"
#include "settings.h"
#include "watchdog.h"

static String ssid, pass;
static bool up = false;
static uint32_t downSince = 0;   // millis(), seit das WLAN weg ist (gilt nur bei !up)
static uint32_t lastTry = 0;

namespace wlan {

void init() {
  // Der Treiber legt Zugangsdaten sonst zusätzlich in seinem eigenen NVS-Bereich ab; sie stehen nur in settings.
  WiFi.persistent(false);
  WiFi.setHostname(HOSTNAME);
}

void begin(const Settings& s) {
  ssid = s.ssid;
  pass = s.pass;
  downSince = lastTry = millis();
  WiFi.mode(WIFI_STA);
  WiFi.setAutoReconnect(true);
  WiFi.begin(ssid.c_str(), pass.c_str());
  Serial.print("WLAN: verbinde");
  for (int i = 0; i < 40 && WiFi.status() != WL_CONNECTED; i++) {
    delay(250);
    Serial.print(".");
  }
  Serial.println();
  watchdog::feed();
  up = WiFi.status() == WL_CONNECTED;
  if (up) Serial.printf("WLAN: %s\n", WiFi.localIP().toString().c_str());
  else Serial.println("WLAN: keine Verbindung, versuche weiter im Loop");
}

void loop() {
  const uint32_t now = millis();
  if (WiFi.status() == WL_CONNECTED) {
    if (!up) Serial.printf("WLAN wieder da: %s\n", WiFi.localIP().toString().c_str());
    up = true;
    return;
  }
  if (up) {
    up = false;
    downSince = now;
    Serial.println("WLAN weg, verbinde neu");
  }
  if (now - lastTry >= WIFI_RETRY_MS) {
    lastTry = now;
    WiFi.disconnect();
    WiFi.begin(ssid.c_str(), pass.c_str());
  }
}

uint32_t downForMs() { return up ? 0 : millis() - downSince; }

}  // namespace wlan
