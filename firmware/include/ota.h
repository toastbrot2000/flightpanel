#pragma once
// ArduinoOTA (Flashen per WLAN) nur mit echtem Passwort aus secrets.h. Ohne eines öffnet das Panel keinen Port
// und meldet sich nicht als flightpanel.local.
namespace ota {
void begin();    // nach dem WLAN-Aufbau
void handle();   // in loop()
}  // namespace ota
