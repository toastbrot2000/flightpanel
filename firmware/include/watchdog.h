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
