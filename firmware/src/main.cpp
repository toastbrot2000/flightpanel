// flightpanel ESP32-Firmware: holt alle POLL_MS ein fertiges Bild vom Server und schreibt es per DMA in die
// HUB75-Panels. Kennt keine Flüge, keine Fonts. Dieses File verbindet nur die Teile:
//
//   display   HUB75-Ausgabe            settings  WLAN, Server, Token im NVS
//   wlan      WLAN als Station         portal    Einrichtungs-WLAN (voll oder nur WLAN)
//   ota       ArduinoOTA mit Passwort  watchdog  Task-Watchdog auf loop()
//
// Frame-Format (Server -> ESP32): [Version, Helligkeit, Breite, Höhe] + RGB

#include <Arduino.h>
#include <HTTPClient.h>
#include <WiFi.h>
#include <esp_system.h>   // esp_reset_reason()

#include "config.h"
#include "display.h"
#include "ota.h"
#include "portal.h"
#include "settings.h"
#include "watchdog.h"
#include "wlan.h"

static Settings cfg;
static uint8_t frame[FRAME_LEN];
static uint32_t lastOk = 0;          // millis() des letzten gültigen Frames
static uint32_t lastPoll = 0;
static uint32_t failures = 0;
static bool offlineMarked = false;

// BOOT (GPIO0) nach dem Einschalten gedrückt halten öffnet das volle Einrichtungs-WLAN. Gedrückt schon beim
// Einschalten selbst startet der ESP32 in den Flash-Modus (GPIO0 ist ein Strapping-Pin); darum zählt ein Drücken,
// das bis windowEnd beginnt, also während des Testbilds. Gehalten werden muss es BOOT_HOLD_MS.
static bool bootHeld(uint32_t windowEnd) {
  pinMode(BOOT_PIN, INPUT_PULLUP);
  while ((int32_t)(millis() - windowEnd) < 0) {
    if (digitalRead(BOOT_PIN) == LOW) {
      const uint32_t t0 = millis();
      while (digitalRead(BOOT_PIN) == LOW) {
        if (millis() - t0 >= BOOT_HOLD_MS) return true;
        delay(10);
      }
    }
    delay(10);
  }
  return false;
}

// ---- Frame holen und anzeigen ---------------------------------------------
static bool fetchFrame() {
  if (WiFi.status() != WL_CONNECTED) return false;
  WiFiClient client;
  HTTPClient http;
  http.setTimeout(HTTP_TIMEOUT_MS);
  http.setUserAgent("flightpanel-esp32");
  if (!http.begin(client, cfg.server + "/frame")) return false;
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
  const uint32_t start = millis();
  Serial.printf("\nflightpanel HUB75 %ux%u (%d Panels)\n", WIDTH, HEIGHT, PANEL_CHAIN);
  // Das Testbild gibt es nur nach dem Einschalten. Jeder andere Neustart (Software-Reset nach RESTART_AFTER_MS ohne
  // Daten, OTA, Watchdog, Unterspannung, Einrichtungs-WLAN) bleibt still, sonst blitzt das Panel bei einem
  // Serverausfall die ganze Nacht alle 10 Minuten auf.
  const esp_reset_reason_t reason = esp_reset_reason();
  const bool coldBoot = (reason == ESP_RST_POWERON);
  Serial.printf("Reset-Grund %d%s\n", (int)reason, coldBoot ? " (Einschalten)" : "");
  watchdog::start();   // ab hier bewacht: Panel-Init, Testbild, BOOT-Fenster, WLAN-Aufbau, Einrichtungs-WLAN

  display::begin();
  if (TEST_PATTERN_AT_BOOT && coldBoot) display::testPattern();

  wlan::init();
  cfg = settings::load();
  const bool held = coldBoot && cfg.configured() && bootHeld(start + BOOT_WINDOW_MS);   // sonst ohnehin voll
  if (held || !cfg.configured()) {
    Serial.println(held ? "BOOT gehalten: volles Einrichtungs-WLAN" : "Noch nicht eingerichtet: Einrichtungs-WLAN");
    portal::run(portal::Mode::Full, cfg);   // zurück nur ohne Änderung (Zeit um, Einrichtung vorhanden)
  }

  wlan::begin(cfg);
  ota::begin();
  lastOk = millis();   // Neustart-Zähler erst ab jetzt
}

void loop() {
  watchdog::feed();   // einmal je Durchlauf
  if (Serial.available() && Serial.read() == 'h') watchdog::testHang();   // Prüfstand: "h" + Enter
  ota::handle();
  wlan::loop();

  if (wlan::downForMs() >= OUTAGE_PORTAL_MS) {
    portal::run(portal::Mode::Outage, cfg);
    if (WiFi.status() != WL_CONNECTED) {
      Serial.println("Einrichtungs-WLAN ohne Ergebnis zu, WLAN weiter weg: Neustart");
      ESP.restart();   // still; danach wieder 5 min Versuche, dann wieder das Einrichtungs-WLAN
    }
    lastOk = millis();   // WLAN wieder da: Fristen neu beginnen
    offlineMarked = false;
    return;
  }

  const uint32_t now = millis();
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
