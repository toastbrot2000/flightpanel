// Task-Watchdog: Arduino-ESP32 2.0.17 (IDF 4.4) legt ihn beim Start mit 5 s und Panic an und überwacht nur den
// Leerlauf-Task von Core 0, loop() nicht. Hier wird er auf WDT_TIMEOUT_S gesetzt (esp_task_wdt_init ändert bei
// laufendem Watchdog Timeout und Panic) und der loop()-Task kommt dazu. RESTART_AFTER_MS fängt nur einen Server
// ohne Bilder, keinen Hänger in loop(). 60 s liegen über jeder einzelnen Wartezeit hier: DNS 15 s, Verbindung
// 5 s, Antwort 3 s, in setup() WLAN-Aufbau 10 s und Testbild 4 s. Länger dauern können nur eine tröpfelnde
// Antwort (readBytes() gibt erst nach 3 s ohne Byte auf) und ein OTA-Upload; dessen onProgress-Rückrufe füttern.
// API von IDF 4.4, beim Umstieg auf Arduino 3.x neu prüfen.
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
