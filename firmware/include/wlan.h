#pragma once
// WLAN als Station: verbinden, wieder verbinden, merken, seit wann es weg ist (für das Ausfall-Portal).
// Im Log steht nie der Name des WLANs.
#include <stdint.h>

struct Settings;

namespace wlan {
void init();                     // vor jedem anderen WLAN-Aufruf: Treiber speichert nichts, Hostname
void begin(const Settings& s);   // verbinden, bis zu 10 s warten
void loop();                     // ist es weg: alle WIFI_RETRY_MS neu verbinden
uint32_t downForMs();            // 0 = verbunden, sonst seit wann weg (ab Start, falls nie verbunden)
}  // namespace wlan
