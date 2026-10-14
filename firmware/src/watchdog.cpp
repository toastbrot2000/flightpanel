// Task-Watchdog: Arduino-ESP32 2.0.17 (IDF 4.4) legt ihn beim Start mit 5 s und Panic an und überwacht nur den
// Leerlauf-Task von Core 0, loop() nicht. Hier wird er auf WDT_TIMEOUT_S gesetzt (esp_task_wdt_init ändert bei
// laufendem Watchdog Timeout und Panic) und der loop()-Task kommt dazu. RESTART_AFTER_MS fängt nur einen Server
// ohne Bilder, keinen Hänger in loop(). 60 s liegen über jeder einzelnen Wartezeit: DNS bis 15 s, Verbindung 5 s,
// TLS-Handschlag 15 s, Antwort 3 s je Zeile; in setup() WLAN-Aufbau 10 s, Testbild samt BOOT-Fenster 6 s, Suche
// nach WLANs 4 s. Länger dauern können ein Bild-Download (jedes Paket füttert, FRAME_DEADLINE_MS begrenzt), das
// Einrichtungs-WLAN (die Schleife füttert) und ein OTA-Upload (jeder Block füttert). Hängt eine Antwort ohne
// Daten, aber bei offener Verbindung, wartet HTTPClient::writeToStream ohne eigene Frist: Das fängt erst der
// Watchdog. Arduino 3.x (IDF 5) will die Einstellungen als Struktur: Dafür steht am Ende eine eigene Fassung.
#include "watchdog.h"

#include <Arduino.h>

#include "config.h"

static_assert(WDT_TEST_HANG_MS > WDT_TIMEOUT_S * 1000, "WDT_TEST_HANG_MS muss länger dauern als WDT_TIMEOUT_S");

namespace watchdog {

void start() {
  const esp_err_t init = esp_task_wdt_init(WDT_TIMEOUT_S, true);
  const esp_err_t add = esp_task_wdt_add(nullptr);   // nullptr = der aufrufende Task
  if (init == ESP_OK && add == ESP_OK) Serial.printf("Watchdog: %d s auf loop()\n", WDT_TIMEOUT_S);
  else Serial.printf("Watchdog FEHLER (init %d, add %d): loop() ohne Aufsicht\n", (int)init, (int)add);
}

// Prüfstand: lässt loop() WDT_TEST_HANG_MS stehen, ohne zu füttern. Nach WDT_TIMEOUT_S muss der ESP32 neu
// starten (Reset-Grund 6, kein Testbild).
void testHang() {
  Serial.printf("Watchdog-Test: loop() steht jetzt %d s still, Neustart nach etwa %d s erwartet\n",
                WDT_TEST_HANG_MS / 1000, WDT_TIMEOUT_S);
  for (int s = 1; s <= WDT_TEST_HANG_MS / 1000; s++) { delay(1000); Serial.printf("%d s\n", s); }
  Serial.println("Watchdog-Test: KEIN Neustart, der Watchdog greift nicht");
}

}  // namespace watchdog

#if ESP_IDF_VERSION_MAJOR >= 5
// esp_task_wdt_init mit der Form von IDF 4.4 für IDF 5 (Arduino 3.x, watchdog.h). Auch dort legt ihn der Kern beim
// Start an (sdkconfig: 5 s, Panic, Leerlauf-Task von Core 0), und esp_task_wdt_reconfigure ändert Timeout und Panic.
// Die Maske der Leerlauf-Tasks bleibt die vom Start (dieselben sdkconfig-Schalter), sonst meldete reconfigure sie ab
// und neu an.
esp_err_t watchdog::esp_task_wdt_init(uint32_t timeoutS, bool panic) {
  esp_task_wdt_config_t cfg = {};
  cfg.timeout_ms = timeoutS * 1000;
  cfg.trigger_panic = panic;
#if CONFIG_ESP_TASK_WDT_CHECK_IDLE_TASK_CPU0
  cfg.idle_core_mask |= 1u << 0;
#endif
#if CONFIG_ESP_TASK_WDT_CHECK_IDLE_TASK_CPU1
  cfg.idle_core_mask |= 1u << 1;
#endif
  const esp_err_t err = ::esp_task_wdt_reconfigure(&cfg);
  return err == ESP_ERR_INVALID_STATE ? ::esp_task_wdt_init(&cfg) : err;   // noch keiner da: anlegen
}
#endif
