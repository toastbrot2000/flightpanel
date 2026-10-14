#pragma once
// Vorgaben aus include/secrets.h, wenn es die Datei gibt (Prüfstand, eigene Builds). Sie gelten nur, solange im
// NVS nichts Eigenes steht (settings.cpp). Ohne die Datei baut die Firmware trotzdem: Ein frisch geflashtes
// Panel öffnet dann das Einrichtungs-WLAN, und OTA ist aus. So bekommen Freunde die Firmware.
#if __has_include("secrets.h")
#include "secrets.h"
#endif

// OTA gibt es nur mit Passwort. Ohne eines wäre das Flashen im WLAN für jeden offen; dann startet ArduinoOTA gar
// nicht (kein offener Port, kein mDNS-Name flightpanel.local).
#ifndef OTA_PASSWORD
#define OTA_PASSWORD ""
#endif

#ifndef HOSTNAME
#define HOSTNAME "flightpanel"   // Name im Router (DHCP), mit OTA auch flightpanel.local
#endif
