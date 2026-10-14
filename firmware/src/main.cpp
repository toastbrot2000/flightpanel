// flightpanel ESP32-Firmware: holt alle POLL_MS ein fertiges Bild vom Server
// und schreibt es per DMA in die HUB75-Panels. Kennt keine Flüge, keine Fonts.
// Anzeige, Watchdog und OTA stehen in display.cpp, watchdog.cpp und ota.cpp.
//
// Frame-Format (Server -> ESP32): [version, brightness, width, height] + RGB

#include <Arduino.h>
#include <HTTPClient.h>
#include <WiFi.h>
#include <esp_system.h>      // esp_reset_reason()

#include "config.h"
#include "display.h"
#include "ota.h"
#include "secrets.h"
#include "watchdog.h"

static uint8_t frame[FRAME_LEN];
static uint32_t lastOk = 0;          // millis() des letzten gültigen Frames
static uint32_t lastPoll = 0;
static uint32_t lastWifiTry = 0;
static uint32_t failures = 0;
static bool offlineMarked = false;

// ---- WLAN -----------------------------------------------------------------
static void wifiConnect() {
  WiFi.mode(WIFI_STA);
  WiFi.setHostname(HOSTNAME);
  WiFi.setAutoReconnect(true);
  WiFi.begin(WIFI_SSID, WIFI_PASS);
  Serial.printf("WLAN: verbinde mit %s", WIFI_SSID);
  for (int i = 0; i < 40 && WiFi.status() != WL_CONNECTED; i++) { delay(250); Serial.print("."); }
  Serial.println();
  if (WiFi.status() == WL_CONNECTED) Serial.printf("WLAN: %s\n", WiFi.localIP().toString().c_str());
  else Serial.println("WLAN: keine Verbindung, versuche weiter im Loop");
}

// ---- Frame holen und anzeigen ---------------------------------------------
static bool fetchFrame() {
  if (WiFi.status() != WL_CONNECTED) return false;
  WiFiClient client;
  HTTPClient http;
  http.setTimeout(HTTP_TIMEOUT_MS);
  http.setUserAgent("flightpanel-esp32");
  if (!http.begin(client, FRAME_URL)) return false;
  const int code = http.GET();
  bool ok = false;
  if (code == HTTP_CODE_OK) {
    const int len = http.getSize();
    if (len == FRAME_LEN) {
      WiFiClient* s = http.getStreamPtr();
      size_t got = 0;
      const uint32_t t0 = millis();
      while (got < FRAME_LEN && millis() - t0 < HTTP_TIMEOUT_MS) {
        const int n = s->readBytes(frame + got, FRAME_LEN - got);
        if (n <= 0) { delay(1); continue; }
        got += n;
      }
      ok = (got == FRAME_LEN) && frame[0] == PROTO_VERSION && frame[2] == WIDTH && frame[3] == HEIGHT;
      if (!ok) Serial.printf("Frame ungültig: got=%u ver=%u %ux%u\n", (unsigned)got, frame[0], frame[2], frame[3]);
    } else {
      Serial.printf("Frame: falsche Länge %d (erwartet %d)\n", len, FRAME_LEN);
    }
  } else {
    Serial.printf("HTTP %d\n", code);
  }
  http.end();
  if (!ok) return false;
  display::showFrame(frame);
  return true;
}

// ---- Arduino ----------------------------------------------------------------
void setup() {
  Serial.begin(115200);
  delay(200);
  Serial.printf("\nflightpanel HUB75 %ux%u (%d Panels)\n", WIDTH, HEIGHT, PANEL_CHAIN);
  // Das Testbild gibt es nur nach dem Einschalten. Jeder andere Neustart (Software-Reset nach
  // RESTART_AFTER_MS ohne Daten, OTA, Watchdog, Unterspannung) bleibt still, sonst blitzt das
  // Panel bei einem Serverausfall die ganze Nacht alle 10 Minuten auf.
  const esp_reset_reason_t reason = esp_reset_reason();
  const bool coldBoot = (reason == ESP_RST_POWERON);
  Serial.printf("Reset-Grund %d%s\n", (int)reason, coldBoot ? " (Einschalten)" : "");
  watchdog::start();   // ab hier bewacht, auch Panel-Init, Testbild und WLAN-Aufbau (zusammen rund 15 s)

  display::begin();
  if (TEST_PATTERN_AT_BOOT && coldBoot) display::testPattern();

  wifiConnect();
  ota::begin();
  lastOk = millis();   // Neustart-Zähler erst ab jetzt
}

void loop() {
  watchdog::feed();   // einmal je Durchlauf
  if (Serial.available() && Serial.read() == 'h') watchdog::testHang();   // Prüfstand: "h" + Enter
  ota::handle();
  const uint32_t now = millis();

  if (WiFi.status() != WL_CONNECTED && now - lastWifiTry > 10000) {
    lastWifiTry = now;
    Serial.println("WLAN weg, reconnect");
    WiFi.disconnect();
    WiFi.begin(WIFI_SSID, WIFI_PASS);
  }

  if (now - lastPoll >= POLL_MS) {
    lastPoll = now;
    if (fetchFrame()) {
      lastOk = now;
      failures = 0;
      offlineMarked = false;
    } else {
      failures++;
    }
  }

  if (now - lastOk > OFFLINE_MARK_MS && !offlineMarked) {
    display::markOffline();
    offlineMarked = true;
    Serial.printf("offline seit %lus (%lu Fehler)\n", (unsigned long)((now - lastOk) / 1000), (unsigned long)failures);
  }
  if (now - lastOk > RESTART_AFTER_MS) {
    Serial.println("zu lange offline, Neustart");
    ESP.restart();
  }
  delay(5);
}
