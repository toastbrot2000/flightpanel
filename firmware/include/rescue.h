#pragma once
// Rettungs-Uhr über das Ausschalten hinweg (updater.cpp). Die genaue Uhr liegt im RTC-Speicher, und den löscht jedes
// Einschalten: Ein Panel an einer Steckdose mit Nachtabschaltung käme sonst nie auf RESCUE_AFTER_MS. Darum steht ihr
// Stand grob im NVS, in Schritten zu RESCUE_AFTER_MS / STEPS (bei 24 h eine Stunde). Geschrieben wird nur, wenn ein
// Schritt voll ist, also höchstens STEPS-mal je Ablehnung, und einmal 0, wenn das Zertifikat wieder durchgeht. Nach
// dem Einschalten fehlt so höchstens der angefangene Schritt.
//
// Ebenso der letzte Rettungsversuch: Sein Schritt steht im NVS, bevor er beginnt (einmal je Versuch, also höchstens
// stündlich). Sonst erlaubte jedes Einschalten nach der Schwelle sofort den nächsten Download ohne Zertifikatsprüfung,
// statt höchstens einen je RESCUE_RETRY_MS. Was dabei fehlt oder nicht passt, zählt wie "eben versucht": Es schiebt
// den nächsten Versuch hinaus, nie vor.
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

// Letzter Rettungsversuch im NVS: NO_TRY heißt ausdrücklich "in dieser Ablehnung noch keiner" (geschrieben, wenn die
// Uhr auf 0 geht), UNKNOWN ist die Vorgabe beim Lesen, wenn nichts gespeichert ist.
constexpr uint8_t NO_TRY = 0;
constexpr uint8_t UNKNOWN = 0xFF;

// Stand der Uhr beim letzten Versuch nach dem Einschalten (lastTryMs, 0 = noch keiner), aus dem gespeicherten Schritt
// savedTry und dem Stand savedSteps. Nur NO_TRY gibt 0; fehlt der Schritt, ist er kaputt oder läge er hinter der Uhr,
// gilt der Stand der Uhr selbst ("eben versucht").
constexpr uint64_t restoreTry(uint8_t savedTry, uint8_t savedSteps, uint64_t afterMs) {
  return savedTry == NO_TRY ? 0
                            : restore(savedTry <= STEPS && savedTry <= savedSteps ? savedTry : savedSteps, afterMs);
}

// Darf ein Rettungsversuch beginnen? Nach afterMs Ablehnung, und frühestens retryMs nach dem letzten (lastTryMs,
// 0 = noch keiner). Ein letzter Versuch hinter der Uhr (kaputt) erlaubt keinen.
constexpr bool mayTry(uint64_t streakMs, uint64_t lastTryMs, uint64_t afterMs, uint64_t retryMs) {
  return streakMs >= afterMs && (lastTryMs == 0 || (lastTryMs <= streakMs && streakMs - lastTryMs >= retryMs));
}

}  // namespace rescue
