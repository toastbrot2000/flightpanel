// Tests auf dem Rechner für include/rescue.h: der Stand der Rettungs-Uhr im NVS (Schritte, Obergrenze, Rückweg).
//
//   pio test -e native        im Ordner firmware/ (tools/check_firmware.sh macht das in Docker)

#include <stdint.h>
#include <unity.h>

#include "rescue.h"

static const uint64_t HOUR = 3600000ULL;
static const uint64_t DAY = 24 * HOUR;   // RESCUE_AFTER_MS im Betrieb
static const uint64_t RETRY = HOUR;      // RESCUE_RETRY_MS

void setUp() {}
void tearDown() {}

// Bei 24 h ist ein Schritt eine Stunde; eine kurze Schwelle für den Prüfstand teilt sich genauso, nie in 0.
static void test_step_length() {
  TEST_ASSERT_EQUAL_UINT64(HOUR, rescue::stepMs(DAY));
  TEST_ASSERT_EQUAL_UINT64(12500, rescue::stepMs(300000));   // -DRESCUE_AFTER_MS=300000: 5 min
  TEST_ASSERT_EQUAL_UINT64(1, rescue::stepMs(10));
  TEST_ASSERT_EQUAL_UINT64(1, rescue::stepMs(0));
}

// Gespeichert werden nur volle Schritte, höchstens 24.
static void test_full_steps_only() {
  TEST_ASSERT_EQUAL_UINT8(0, rescue::steps(0, DAY));
  TEST_ASSERT_EQUAL_UINT8(0, rescue::steps(HOUR - 1, DAY));
  TEST_ASSERT_EQUAL_UINT8(1, rescue::steps(HOUR, DAY));
  TEST_ASSERT_EQUAL_UINT8(23, rescue::steps(DAY - 1, DAY));
  TEST_ASSERT_EQUAL_UINT8(24, rescue::steps(DAY, DAY));
  TEST_ASSERT_EQUAL_UINT8(24, rescue::steps(1000 * DAY, DAY));
  TEST_ASSERT_EQUAL_UINT8(24, rescue::steps(UINT64_MAX, DAY));
}

// Eine Ablehnung über zwei Tage, alle 2 s ein Abruf: genau 24 Schreibvorgänge, einer je Stunde, keiner danach.
static void test_at_most_one_write_per_step() {
  uint8_t saved = 0;
  int writes = 0;
  for (uint64_t t = 1; t <= 2 * DAY; t += 2000) {
    const uint8_t s = rescue::steps(t, DAY);
    if (s > saved) {
      TEST_ASSERT_EQUAL_UINT8(saved + 1, s);
      saved = s;
      writes++;
    }
  }
  TEST_ASSERT_EQUAL_INT(24, writes);
  TEST_ASSERT_EQUAL_UINT8(24, saved);
}

// Nach dem Einschalten: der Stand aus dem NVS, es fehlt höchstens der angefangene Schritt. Ein fremder Wert über 24
// zählt wie 24.
static void test_restore() {
  TEST_ASSERT_EQUAL_UINT64(0, rescue::restore(0, DAY));
  TEST_ASSERT_EQUAL_UINT64(5 * HOUR, rescue::restore(5, DAY));
  TEST_ASSERT_EQUAL_UINT64(DAY, rescue::restore(24, DAY));
  TEST_ASSERT_EQUAL_UINT64(DAY, rescue::restore(255, DAY));
  const uint64_t streaks[] = {0, 1, HOUR - 1, HOUR, 5 * HOUR + 59 * 60000, DAY - 1};
  for (uint64_t ms : streaks) {
    const uint64_t back = rescue::restore(rescue::steps(ms, DAY), DAY);
    TEST_ASSERT_TRUE(back <= ms);
    TEST_ASSERT_TRUE(ms - back < HOUR);
  }
}

// Ein Versuch erst nach der Schwelle, danach frühestens RETRY nach dem letzten. Ein letzter Versuch hinter der Uhr
// (kaputt) erlaubt keinen.
static void test_may_try() {
  TEST_ASSERT_FALSE(rescue::mayTry(DAY - 1, 0, DAY, RETRY));
  TEST_ASSERT_TRUE(rescue::mayTry(DAY, 0, DAY, RETRY));
  TEST_ASSERT_FALSE(rescue::mayTry(DAY + RETRY - 1, DAY, DAY, RETRY));
  TEST_ASSERT_TRUE(rescue::mayTry(DAY + RETRY, DAY, DAY, RETRY));
  TEST_ASSERT_FALSE(rescue::mayTry(DAY + RETRY, DAY + 2 * RETRY, DAY, RETRY));
}

// Der letzte Versuch nach dem Einschalten: "noch keiner" nur, wenn das ausdrücklich gespeichert ist; was fehlt, kaputt
// ist oder hinter der Uhr läge, zählt wie "eben versucht" (der Stand der Uhr).
static void test_restore_try() {
  TEST_ASSERT_EQUAL_UINT64(0, rescue::restoreTry(rescue::NO_TRY, 24, DAY));
  TEST_ASSERT_EQUAL_UINT64(DAY, rescue::restoreTry(24, 24, DAY));
  TEST_ASSERT_EQUAL_UINT64(20 * HOUR, rescue::restoreTry(20, 24, DAY));
  TEST_ASSERT_EQUAL_UINT64(DAY, rescue::restoreTry(rescue::UNKNOWN, 24, DAY));    // nichts gespeichert
  TEST_ASSERT_EQUAL_UINT64(DAY, rescue::restoreTry(25, 24, DAY));                 // kaputt
  TEST_ASSERT_EQUAL_UINT64(5 * HOUR, rescue::restoreTry(24, 5, DAY));             // hinter der Uhr
  TEST_ASSERT_EQUAL_UINT64(0, rescue::restoreTry(rescue::UNKNOWN, 0, DAY));       // Uhr steht: nichts zu begrenzen
}

// Ablehnung bis zum ersten Versuch, eine halbe Stunde weiter, dann Einschalten: Der nächste Versuch kommt erst eine
// Stunde Ablehnung nach dem letzten, nicht gleich nach dem Start. Ebenso, wenn der Vermerk fehlt. Nur mit
// ausdrücklich "noch keiner" darf er sofort.
static void test_limit_across_power_on() {
  uint64_t streak = 0, lastTry = 0;
  uint8_t savedSteps = 0, savedTry = rescue::NO_TRY;
  while (!rescue::mayTry(streak, lastTry, DAY, RETRY)) {
    streak += 2000;
    savedSteps = rescue::steps(streak, DAY);
  }
  lastTry = streak;                                     // Versuch, vorher vermerkt
  savedTry = rescue::steps(streak, DAY);
  streak += 30 * 60000;
  TEST_ASSERT_FALSE(rescue::mayTry(streak, lastTry, DAY, RETRY));

  const uint8_t records[] = {savedTry, rescue::UNKNOWN};
  for (uint8_t t : records) {                           // Einschalten: nur das NVS weiß noch etwas
    const uint64_t on = rescue::restore(savedSteps, DAY);
    const uint64_t onTry = rescue::restoreTry(t, savedSteps, DAY);
    TEST_ASSERT_FALSE(rescue::mayTry(on, onTry, DAY, RETRY));
    TEST_ASSERT_FALSE(rescue::mayTry(on + RETRY - 2000, onTry, DAY, RETRY));
    TEST_ASSERT_TRUE(rescue::mayTry(on + RETRY, onTry, DAY, RETRY));
  }
  const uint64_t on = rescue::restore(savedSteps, DAY);
  TEST_ASSERT_TRUE(rescue::mayTry(on, rescue::restoreTry(rescue::NO_TRY, savedSteps, DAY), DAY, RETRY));
}

int main(int, char**) {
  UNITY_BEGIN();
  RUN_TEST(test_step_length);
  RUN_TEST(test_full_steps_only);
  RUN_TEST(test_at_most_one_write_per_step);
  RUN_TEST(test_restore);
  RUN_TEST(test_may_try);
  RUN_TEST(test_restore_try);
  RUN_TEST(test_limit_across_power_on);
  return UNITY_END();
}
