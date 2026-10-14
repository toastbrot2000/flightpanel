// Tests auf dem Rechner für include/rescue.h: der Stand der Rettungs-Uhr im NVS (Schritte, Obergrenze, Rückweg).
//
//   pio test -e native        im Ordner firmware/ (tools/check_firmware.sh macht das in Docker)

#include <stdint.h>
#include <unity.h>

#include "rescue.h"

static const uint64_t HOUR = 3600000ULL;
static const uint64_t DAY = 24 * HOUR;   // RESCUE_AFTER_MS im Betrieb

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

int main(int, char**) {
  UNITY_BEGIN();
  RUN_TEST(test_step_length);
  RUN_TEST(test_full_steps_only);
  RUN_TEST(test_at_most_one_write_per_step);
  RUN_TEST(test_restore);
  return UNITY_END();
}
