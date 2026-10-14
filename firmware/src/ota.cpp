// ArduinoOTA mit Passwort (siehe ota.h).
#include "ota.h"

#include <Arduino.h>
#include <ArduinoOTA.h>

#include "defaults.h"
#include "display.h"
#include "frame.h"
#include "watchdog.h"

// "changeme" stand bis Oktober 2026 in der Vorlage und im öffentlichen Repo: zählt wie keins.
static constexpr bool sameText(const char* a, const char* b) {
  return *a == *b && (*a == '\0' || sameText(a + 1, b + 1));
}
static const bool OTA_OLD_DEFAULT = sameText(OTA_PASSWORD, "changeme");
static const bool OTA_ENABLED = OTA_PASSWORD[0] != '\0' && !OTA_OLD_DEFAULT;   // zur Compile-Zeit bekannt

namespace ota {

void begin() {
  if (!OTA_ENABLED) {
    Serial.println(OTA_OLD_DEFAULT ? "OTA aus: OTA_PASSWORD ist noch \"changeme\" aus der alten Vorlage, "
                                     "ein eigenes setzen (secrets.h und platformio_local.ini)"
                                   : "OTA aus (OTA_PASSWORD leer)");
    return;
  }
  ArduinoOTA.setHostname(HOSTNAME);
  ArduinoOTA.setPassword(OTA_PASSWORD);
  // Ein Upload macht das Panel schwarz. Scheitert er, läuft das alte Image weiter, und mit dem ETag des letzten Bildes
  // bekäme es nur 304 und bliebe schwarz, bis sich das Bild ändert: ETag vergessen, der nächste Abruf holt es ganz.
  ArduinoOTA.onStart([]() { display::clear(); frame::forgetEtag(); Serial.println("OTA start"); });
  // Ein Upload hält loop() länger als WDT_TIMEOUT_S an: jeder empfangene Block füttert den Watchdog.
  ArduinoOTA.onProgress([](unsigned int, unsigned int) { watchdog::feed(); });
  ArduinoOTA.onEnd([]() { Serial.println("OTA fertig"); });
  ArduinoOTA.onError([](ota_error_t e) { frame::forgetEtag(); Serial.printf("OTA Fehler %u\n", e); });
  ArduinoOTA.begin();
}

void handle() {
  if (OTA_ENABLED) ArduinoOTA.handle();
}

}  // namespace ota
