#pragma once
// Rettungs-Uhr über das Ausschalten hinweg (updater.cpp). Die genaue Uhr liegt im RTC-Speicher, und den löscht jedes
// Einschalten: Ein Panel an einer Steckdose mit Nachtabschaltung käme sonst nie auf RESCUE_AFTER_MS. Darum steht ihr
// Stand grob im NVS, in Schritten zu RESCUE_AFTER_MS / STEPS (bei 24 h eine Stunde). Geschrieben wird nur, wenn ein
// Schritt voll ist, also höchstens STEPS-mal je Ablehnung, und einmal 0, wenn das Zertifikat wieder durchgeht. Nach
// dem Einschalten fehlt so höchstens der angefangene Schritt.
//
// Reines C++11 wie manifest.h: dieselbe Datei auf dem ESP32 und in den Tests auf dem Rechner (test/test_rescue).

#include <stdint.h>

namespace rescue {

constexpr uint8_t STEPS = 24;

// Länge eines Schritts zur Schwelle afterMs, mindestens 1 ms
constexpr uint64_t stepMs(uint64_t afterMs) { return afterMs / STEPS ? afterMs / STEPS : 1; }

// Volle Schritte einer Ablehnung von streakMs, höchstens STEPS: das steht im NVS
constexpr uint8_t steps(uint64_t streakMs, uint64_t afterMs) {
  return streakMs / stepMs(afterMs) < (uint64_t)STEPS ? (uint8_t)(streakMs / stepMs(afterMs)) : STEPS;
}

// Stand der Uhr nach dem Einschalten aus dem NVS; ein Wert über STEPS (fremd oder kaputt) zählt wie STEPS
constexpr uint64_t restore(uint8_t saved, uint64_t afterMs) {
  return (saved < STEPS ? saved : STEPS) * stepMs(afterMs);
}

}  // namespace rescue
