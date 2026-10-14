// flightpanel ESP32-Firmware: holt alle POLL_MS ein fertiges Bild vom Server
// und schreibt es per DMA in die HUB75-Panels. Kennt keine Flüge, keine Fonts.
//
// Frame-Format (Server -> ESP32): [version, brightness, width, height] + RGB

#include <Arduino.h>
#include <ArduinoOTA.h>
#include <ESP32-HUB75-MatrixPanel-I2S-DMA.h>
#include <HTTPClient.h>
#include <WiFi.h>
#include <esp_system.h>      // esp_reset_reason()
#include <esp_task_wdt.h>    // Task-Watchdog

#include "config.h"
#include "secrets.h"

// OTA gibt es nur mit Passwort. Ohne eines wäre das Flashen im WLAN für jeden offen;
// dann startet ArduinoOTA gar nicht (kein offener Port, kein mDNS-Name flightpanel.local).
// "changeme" stand bis Oktober 2026 in der Vorlage und im öffentlichen Repo: zählt wie keins.
#ifndef OTA_PASSWORD
#define OTA_PASSWORD ""   // ältere secrets.h ohne diese Zeile: kein OTA
#endif
static constexpr bool sameText(const char* a, const char* b) {
  return *a == *b && (*a == '\0' || sameText(a + 1, b + 1));
}
static const bool OTA_OLD_DEFAULT = sameText(OTA_PASSWORD, "changeme");
static const bool OTA_ENABLED = OTA_PASSWORD[0] != '\0' && !OTA_OLD_DEFAULT;   // zur Compile-Zeit bekannt

static MatrixPanel_I2S_DMA* panel = nullptr;
static uint8_t frame[FRAME_LEN];
static uint32_t lastOk = 0;          // millis() des letzten gültigen Frames
static uint32_t lastPoll = 0;
static uint32_t lastWifiTry = 0;
static uint32_t failures = 0;
static bool offlineMarked = false;

// ---- Pixel setzen, mit optionalem Tausch der Panelhälften ------------------
static inline void putPixel(int x, int y, uint8_t r, uint8_t g, uint8_t b) {
  if (SWAP_HALVES) x = (x + PANEL_RES_X) % WIDTH;
  panel->drawPixelRGB888(x, y, r, g, b);
}

static void show() {
  if (HUB75_DOUBLE_BUFFER) panel->flipDMABuffer();
}

// ---- Watchdog ---------------------------------------------------------------
// Task-Watchdog auf dem loop()-Task (setup() läuft darin). Arduino-ESP32 2.0.17 (IDF 4.4) legt ihn beim
// Start mit 5 s und Panic an und überwacht nur den Leerlauf-Task von Core 0, loop() nicht. Hier wird er
// auf WDT_TIMEOUT_S gesetzt (esp_task_wdt_init ändert bei laufendem Watchdog Timeout und Panic) und der
// loop()-Task kommt dazu. Füttert loop() ihn so lange nicht, folgt Panic und Neustart (Reset-Grund 6).
// RESTART_AFTER_MS fängt nur einen Server ohne Bilder, keinen Hänger in loop(). 60 s liegen über jeder
// einzelnen Wartezeit hier: DNS 15 s, Verbindung 5 s, Antwort 3 s, in setup() WLAN-Aufbau 10 s und
// Testbild 4 s. Länger dauern können nur eine tröpfelnde Antwort (readBytes() gibt erst nach 3 s ohne
// Byte auf) und ein OTA-Upload; dessen onProgress-Rückrufe füttern. API von IDF 4.4, beim Umstieg auf
// Arduino 3.x neu prüfen.
static_assert(WDT_TEST_HANG_MS > WDT_TIMEOUT_S * 1000, "WDT_TEST_HANG_MS muss länger dauern als WDT_TIMEOUT_S");

static void watchdogStart() {
  const esp_err_t init = esp_task_wdt_init(WDT_TIMEOUT_S, true);
  const esp_err_t add = esp_task_wdt_add(nullptr);   // nullptr = der aufrufende Task
  if (init == ESP_OK && add == ESP_OK) Serial.printf("Watchdog: %d s auf loop()\n", WDT_TIMEOUT_S);
  else Serial.printf("Watchdog FEHLER (init %d, add %d): loop() ohne Aufsicht\n", (int)init, (int)add);
}

static inline void feedWatchdog() { esp_task_wdt_reset(); }

// Prüfstand: "h" + Enter im seriellen Monitor lässt loop() WDT_TEST_HANG_MS stehen, ohne zu füttern.
// Nach WDT_TIMEOUT_S muss der ESP32 neu starten (Reset-Grund 6, kein Testbild).
static void watchdogTestHang() {
  Serial.printf("Watchdog-Test: loop() steht jetzt %d s still, Neustart nach etwa %d s erwartet\n",
                WDT_TEST_HANG_MS / 1000, WDT_TIMEOUT_S);
  for (int s = 1; s <= WDT_TEST_HANG_MS / 1000; s++) { delay(1000); Serial.printf("%d s\n", s); }
  Serial.println("Watchdog-Test: KEIN Neustart, der Watchdog greift nicht");
}

// ---- Testbild beim Boot ---------------------------------------------------
static void testPattern() {
  panel->setBrightness8(TEST_BRIGHTNESS);
  // 1. jedes Panel in einer Farbe: links rot, rechts grün -> Kettenreihenfolge
  panel->fillScreenRGB888(0, 0, 0);
  panel->fillRect(0, 0, PANEL_RES_X, PANEL_RES_Y, panel->color565(255, 0, 0));
  panel->fillRect(PANEL_RES_X, 0, PANEL_RES_X, PANEL_RES_Y, panel->color565(0, 255, 0));
  show();
  delay(1200);
  // 2. Rahmen und Diagonale: Geometrie, Fuge, Scan-Fehler (verschobene Zeilen)
  panel->fillScreenRGB888(0, 0, 0);
  panel->drawRect(0, 0, WIDTH, HEIGHT, panel->color565(255, 120, 0));
  panel->drawLine(0, 0, WIDTH - 1, HEIGHT - 1, panel->color565(70, 120, 230));
  show();
  delay(1200);
  // 3. Schrift: Orientierung (oben/unten, links/rechts)
  panel->fillScreenRGB888(0, 0, 0);
  panel->setTextColor(panel->color565(255, 120, 0));
  panel->setCursor(2, 2);
  panel->print("flightpanel");
  show();
  delay(1500);
  panel->fillScreenRGB888(0, 0, 0);
  show();
}

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

  panel->setBrightness8(min<uint8_t>(frame[1], MAX_BRIGHTNESS));
  const uint8_t* p = frame + FRAME_HEADER;
  for (int y = 0; y < HEIGHT; y++) {
    for (int x = 0; x < WIDTH; x++) {
      putPixel(x, y, p[0], p[1], p[2]);
      p += 3;
    }
  }
  show();
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
  watchdogStart();   // ab hier bewacht, auch Panel-Init, Testbild und WLAN-Aufbau (zusammen rund 15 s)

  HUB75_I2S_CFG::i2s_pins pins = {R1_PIN, G1_PIN, B1_PIN, R2_PIN, G2_PIN, B2_PIN,
                                  A_PIN, B_PIN, C_PIN, D_PIN, E_PIN, LAT_PIN, OE_PIN, CLK_PIN};
  HUB75_I2S_CFG cfg(PANEL_RES_X, PANEL_RES_Y, PANEL_CHAIN, pins);
  cfg.clkphase = HUB75_CLKPHASE;
  if (HUB75_LATCH_BLANKING > 0) cfg.latch_blanking = HUB75_LATCH_BLANKING;
  cfg.double_buff = HUB75_DOUBLE_BUFFER;
  panel = new MatrixPanel_I2S_DMA(cfg);
  if (!panel->begin()) {
    Serial.println("Panel-Init fehlgeschlagen (zu wenig DMA-Speicher?)");
  }
  panel->setBrightness8(TEST_BRIGHTNESS);
  panel->clearScreen();
  show();

  if (TEST_PATTERN_AT_BOOT && coldBoot) testPattern();

  wifiConnect();

  if (OTA_ENABLED) {
    ArduinoOTA.setHostname(HOSTNAME);
    ArduinoOTA.setPassword(OTA_PASSWORD);
    ArduinoOTA.onStart([]() { panel->clearScreen(); show(); Serial.println("OTA start"); });
    // Ein Upload hält loop() länger als WDT_TIMEOUT_S an: jeder empfangene Block füttert den Watchdog.
    ArduinoOTA.onProgress([](unsigned int, unsigned int) { feedWatchdog(); });
    ArduinoOTA.onEnd([]() { Serial.println("OTA fertig"); });
    ArduinoOTA.onError([](ota_error_t e) { Serial.printf("OTA Fehler %u\n", e); });
    ArduinoOTA.begin();
  } else {
    Serial.println(OTA_OLD_DEFAULT ? "OTA aus: OTA_PASSWORD ist noch \"changeme\" aus der alten Vorlage, "
                                     "ein eigenes setzen (secrets.h und platformio_local.ini)"
                                   : "OTA aus (OTA_PASSWORD leer)");
  }

  lastOk = millis();   // Neustart-Zähler erst ab jetzt
}

void loop() {
  feedWatchdog();   // einmal je Durchlauf
  if (Serial.available() && Serial.read() == 'h') watchdogTestHang();   // Prüfstand: "h" + Enter
  if (OTA_ENABLED) ArduinoOTA.handle();
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
    // Letztes Bild bleibt stehen, nur ein dunkelroter Punkt unten rechts dazu
    putPixel(WIDTH - 1, HEIGHT - 1, 60, 0, 0);
    if (HUB75_DOUBLE_BUFFER) { show(); putPixel(WIDTH - 1, HEIGHT - 1, 60, 0, 0); }   // beide Puffer
    offlineMarked = true;
    Serial.printf("offline seit %lus (%lu Fehler)\n", (unsigned long)((now - lastOk) / 1000), (unsigned long)failures);
  }
  if (now - lastOk > RESTART_AFTER_MS) {
    Serial.println("zu lange offline, Neustart");
    ESP.restart();
  }
  delay(5);
}
