#pragma once
// Task-Watchdog auf dem loop()-Task (setup() läuft im selben Task). Füttert ihn WDT_TIMEOUT_S lang niemand,
// startet der ESP32 neu (Reset-Grund 6). Jede Schleife, die länger warten kann (Download, Einrichtungs-WLAN,
// OTA), ruft feed() bei jedem Fortschritt; ein echter Hänger füttert nicht mehr und wird so begrenzt.
#include <esp_task_wdt.h>

namespace watchdog {
void start();                                   // einmal am Anfang von setup()
inline void feed() { esp_task_wdt_reset(); }
void testHang();                                // Prüfstand: "h" + Enter im seriellen Monitor
}  // namespace watchdog

// Arduino 3.x (ESP-IDF 5, env:esp32dev_core3): Dort nimmt esp_task_wdt_init eine Struktur und lehnt den beim Start
// angelegten Watchdog ab. Diese Fassung mit der Form von IDF 4.4 (Timeout in s, Panic; watchdog.cpp) verdeckt die
// von IDF in namespace watchdog, so bleibt start() für beide Kerne derselbe Code.
#include <esp_idf_version.h>
#if ESP_IDF_VERSION_MAJOR >= 5
namespace watchdog {
esp_err_t esp_task_wdt_init(uint32_t timeoutS, bool panic);
}  // namespace watchdog
#endif
