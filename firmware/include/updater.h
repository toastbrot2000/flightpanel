#pragma once
// Signierte Updates vom Server (Spezifikation Firmware §3), nur mit include/fw_pubkey.h (FW_UPDATES):
//
//   - Prüfung nach dem ersten Bild, dann alle UPDATE_CHECK_MS: /panel/firmware.txt (höchstens 512 Byte) über die
//     Verbindung von net, Format prüfen (manifest.h), Signatur prüfen (fwsig.h), nur eine neuere Version als
//     FW_VERSION. Dann /panel/firmware.bin?v=<Version> in den freien App-Slot schreiben, dabei SHA-256 rechnen;
//     stimmen Größe und Hash nicht, wird abgebrochen, bevor das Image startbar ist. Sonst Neustart.
//   - Rollback: Ein neues Image startet unbestätigt (PENDING_VERIFY) und bestätigt sich mit dem ersten Bild;
//     scheitert das, versucht es jedes weitere Bild wieder, und bis dahin prüft es keine Updates.
//     Startet es vorher neu (Absturz, Watchdog, 10 min ohne Bild, Stromausfall), startet der Bootloader wieder das
//     alte. Eine Version, die so scheitert, versucht das Panel höchstens UPDATE_TRIES_MAX-mal, im Abstand von
//     UPDATE_CHECK_MS.
//   - Rettungsmodus: Lehnt das Panel das Zertifikat des Servers länger als RESCUE_AFTER_MS ab (veraltete
//     Vertrauensliste), holt es nur Manifest und Image ohne Zertifikatsprüfung und ohne Token und vertraut dann
//     allein Signatur, SHA-256 und steigender Version. Höchstens einmal je RESCUE_RETRY_MS.
//
// Ohne fw_pubkey.h sind beide Funktionen leer, und der Arduino-Kern bestätigt ein neues Image wie bisher sofort.
#include "frame.h"
#include "fwsig.h"

namespace updater {
#if FW_UPDATES
void begin();                       // in setup() nach net::begin(): Zustand des Images, gescheiterte Versuche
void afterFrame(frame::Result r);   // in loop() nach jedem Bildabruf: bestätigen, Update prüfen, Rettungsmodus
#else
inline void begin() {}
inline void afterFrame(frame::Result) {}
#endif
}  // namespace updater
