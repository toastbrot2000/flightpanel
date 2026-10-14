// Tests auf dem Rechner für include/wifiqr.h: Text für den WLAN-QR-Code, Fehlerkorrektur bis Version 3, Rückfall auf
// reinen Text, Anordnung auf dem Panel.
//
//   pio test -e native        im Ordner firmware/ (tools/check_firmware.sh macht das in Docker)
//
// Codiert wird nur auf dem ESP32 (qrcode-Baustein des ESP-IDF); dass das Bild mit der Kamera lesbar ist, wurde
// einmal auf dem Rechner mit Nayukis qrcodegen und einem Decoder geprüft, nicht hier.

#include <stddef.h>
#include <string.h>
#include <unity.h>

#include <string>

#include "wifiqr.h"

using wifiqr::Code;

void setUp() {}
void tearDown() {}

static std::string payload(const char* ssid, const char* pass) {
  char buf[wifiqr::PAYLOAD_MAX + 1];
  const size_t n = wifiqr::payload(buf, sizeof buf, ssid, pass);
  TEST_ASSERT_EQUAL_size_t(strlen(buf), n);
  return buf;
}

// Das Einrichtungs-WLAN: Name aus der MAC, 8 Ziffern Passwort. Nichts davon braucht ein \.
static void test_portal_payload() {
  const std::string p = payload("flightpanel-A1B2", "12345678");
  TEST_ASSERT_EQUAL_STRING("WIFI:T:WPA;S:flightpanel-A1B2;P:12345678;;", p.c_str());
  TEST_ASSERT_EQUAL_size_t(42, p.size());
}

// Jedes der fünf Sonderzeichen bekommt ein \ davor, in Name und Passwort; alles andere bleibt, auch UTF-8.
static void test_escape_every_special_char() {
  const char specials[] = {'\\', ';', ',', ':', '"'};
  for (char c : specials) {
    const std::string in = std::string("a") + c + "b";
    const std::string esc = std::string("a\\") + c + "b";
    TEST_ASSERT_TRUE(wifiqr::special(c));
    TEST_ASSERT_EQUAL_STRING(("WIFI:T:WPA;S:" + esc + ";P:pw;;").c_str(), payload(in.c_str(), "pw").c_str());
    TEST_ASSERT_EQUAL_STRING(("WIFI:T:WPA;S:ssid;P:" + esc + ";;").c_str(), payload("ssid", in.c_str()).c_str());
  }
  TEST_ASSERT_EQUAL_STRING("WIFI:T:WPA;S:\\\\\\;\\,\\:\\\";P:\\\"\\:\\,\\;\\\\;;",
                           payload("\\;,:\"", "\":,;\\").c_str());
  TEST_ASSERT_EQUAL_STRING("WIFI:T:WPA;S:a b-c_d'e!;P:x=y&z;;", payload("a b-c_d'e!", "x=y&z").c_str());
  TEST_ASSERT_EQUAL_STRING("WIFI:T:WPA;S:K\xc3\xbc" "che;P:;;", payload("K\xc3\xbc" "che", "").c_str());
  for (int c = 1; c < 256; c++) {
    if (c != '\\' && c != ';' && c != ',' && c != ':' && c != '"') TEST_ASSERT_FALSE(wifiqr::special((char)c));
  }
}

// Was in Version 3 passt: Byte-Modus, Datenwörter - 2 (ISO/IEC 18004: 53/42/32/24 Byte bei L/M/Q/H).
static void test_capacity_version_3() {
  TEST_ASSERT_EQUAL_size_t(53, wifiqr::capacity(3, wifiqr::ECC_LOW));
  TEST_ASSERT_EQUAL_size_t(42, wifiqr::capacity(3, wifiqr::ECC_MEDIUM));
  TEST_ASSERT_EQUAL_size_t(32, wifiqr::capacity(3, wifiqr::ECC_QUARTILE));
  TEST_ASSERT_EQUAL_size_t(24, wifiqr::capacity(3, wifiqr::ECC_HIGH));
  TEST_ASSERT_EQUAL_size_t(17, wifiqr::capacity(1, wifiqr::ECC_LOW));
  TEST_ASSERT_EQUAL_size_t(14, wifiqr::capacity(2, wifiqr::ECC_HIGH));
  TEST_ASSERT_EQUAL_INT(29, wifiqr::MAX_SIZE);
}

// Die höchste Stufe, die noch passt; darüber kein Code.
static void test_ecc_steps() {
  TEST_ASSERT_EQUAL_INT(wifiqr::ECC_HIGH, wifiqr::ecc(1));
  TEST_ASSERT_EQUAL_INT(wifiqr::ECC_HIGH, wifiqr::ecc(24));
  TEST_ASSERT_EQUAL_INT(wifiqr::ECC_QUARTILE, wifiqr::ecc(25));
  TEST_ASSERT_EQUAL_INT(wifiqr::ECC_QUARTILE, wifiqr::ecc(32));
  TEST_ASSERT_EQUAL_INT(wifiqr::ECC_MEDIUM, wifiqr::ecc(33));
  TEST_ASSERT_EQUAL_INT(wifiqr::ECC_MEDIUM, wifiqr::ecc(42));
  TEST_ASSERT_EQUAL_INT(wifiqr::ECC_LOW, wifiqr::ecc(43));
  TEST_ASSERT_EQUAL_INT(wifiqr::ECC_LOW, wifiqr::ecc(53));
  TEST_ASSERT_EQUAL_INT(wifiqr::ECC_NONE, wifiqr::ecc(54));
  TEST_ASSERT_EQUAL_INT(wifiqr::ECC_NONE, wifiqr::ecc(wifiqr::PAYLOAD_MAX));
}

// Der längste Name und das längste Passwort, die das Panel je zeigt (flightpanel-XXXX, 8 Ziffern), passen mit Stufe
// MEDIUM (15 %). Mit LOW gingen noch 11 Zeichen mehr, etwa ein Passwort mit 19 statt 8 Zeichen.
static void test_longest_portal_fits() {
  TEST_ASSERT_EQUAL_INT(wifiqr::ECC_MEDIUM, wifiqr::ecc(payload("flightpanel-FFFF", "99999999").size()));
  TEST_ASSERT_EQUAL_INT(wifiqr::ECC_LOW, wifiqr::ecc(payload("flightpanel-FFFF", "9999999999999999999").size()));
  TEST_ASSERT_EQUAL_INT(wifiqr::ECC_NONE, wifiqr::ecc(payload("flightpanel-FFFF", "99999999999999999999").size()));
  // Sonderzeichen zählen doppelt: drei davon im Namen, 45 Byte, das passt nur noch mit LOW
  TEST_ASSERT_EQUAL_INT(wifiqr::ECC_LOW, wifiqr::ecc(payload("flight;panel:A,B", "99999999").size()));
}

// Name und Passwort in voller Länge, alles Sonderzeichen: Der Puffer reicht, nur der Code nicht -> reiner Text.
// Ebenso ein langer Name ohne Sonderzeichen.
static void test_fallback_when_too_long() {
  const std::string ssid(wifiqr::SSID_MAX, ';');
  const std::string pass(wifiqr::PASS_MAX, '"');
  const std::string p = payload(ssid.c_str(), pass.c_str());
  TEST_ASSERT_EQUAL_size_t(wifiqr::PAYLOAD_MAX, p.size());
  TEST_ASSERT_EQUAL_INT(wifiqr::ECC_NONE, wifiqr::ecc(p.size()));
  TEST_ASSERT_EQUAL_INT(wifiqr::ECC_NONE, wifiqr::ecc(payload(std::string(32, 'x').c_str(), "12345678").size()));
}

// Zu kleiner Puffer: 0 und leer, nie abgeschnitten (ein halber Text gäbe einen Code für ein anderes WLAN).
static void test_buffer_too_small() {
  char buf[43];
  TEST_ASSERT_EQUAL_size_t(42, wifiqr::payload(buf, sizeof buf, "flightpanel-A1B2", "12345678"));
  memset(buf, 'x', sizeof buf);
  TEST_ASSERT_EQUAL_size_t(0, wifiqr::payload(buf, 42, "flightpanel-A1B2", "12345678"));
  TEST_ASSERT_EQUAL_STRING("", buf);
  char esc[19];   // "WIFI:T:WPA;S:a\;b" + NUL = 18, für ";P:;;" fehlt Platz
  TEST_ASSERT_EQUAL_size_t(0, wifiqr::payload(esc, sizeof esc, "a;b", ""));
  TEST_ASSERT_EQUAL_STRING("", esc);
  TEST_ASSERT_EQUAL_size_t(0, wifiqr::payload(buf, 0, "a", "b"));
}

static void test_code_bits() {
  Code c;
  c.clear();
  c.size = wifiqr::MAX_SIZE;
  TEST_ASSERT_FALSE(c.dark(0, 0));
  c.set(0, 0, true);
  c.set(28, 28, true);
  c.set(7, 3, true);
  TEST_ASSERT_TRUE(c.dark(0, 0));
  TEST_ASSERT_TRUE(c.dark(28, 28));
  TEST_ASSERT_TRUE(c.dark(7, 3));
  TEST_ASSERT_FALSE(c.dark(3, 7));
  c.set(7, 3, false);
  TEST_ASSERT_FALSE(c.dark(7, 3));
  c.set(29, 0, true);   // außerhalb: nichts
  c.set(-1, 0, true);
  TEST_ASSERT_FALSE(c.dark(29, 0));
  TEST_ASSERT_FALSE(c.dark(-1, 0));
  TEST_ASSERT_FALSE(c.dark(0, 29));
  TEST_ASSERT_EQUAL_size_t(107, sizeof(Code));
}

// Version 3 auf 32 Zeilen: Feld 33 Pixel breit, Ruhezone oben 1, unten 2, links und rechts 2 Pixel.
static void test_layout_version_3() {
  Code c;
  c.clear();
  c.size = wifiqr::MAX_SIZE;
  for (int y = 0; y < c.size; y++)
    for (int x = 0; x < c.size; x++) c.set(x, y, true);   // ganz dunkel: hell bleibt nur die Ruhezone
  TEST_ASSERT_EQUAL_INT(33, wifiqr::BOX_W);
  int lit = 0;
  for (int y = 0; y < 32; y++) {
    for (int x = 0; x < wifiqr::BOX_W; x++) {
      const bool quiet = x < 2 || x >= 31 || y < 1 || y >= 30;
      TEST_ASSERT_EQUAL_INT(quiet, wifiqr::boxLit(c, x, y, 32));
      lit += quiet;
    }
  }
  TEST_ASSERT_EQUAL_INT(33 * 32 - 29 * 29, lit);
  c.clear();
  c.size = wifiqr::MAX_SIZE;
  c.set(0, 0, true);
  c.set(28, 28, true);
  TEST_ASSERT_FALSE(wifiqr::boxLit(c, 2, 1, 32));     // Modul (0, 0) liegt auf Pixel (2, 1)
  TEST_ASSERT_FALSE(wifiqr::boxLit(c, 30, 29, 32));   // Modul (28, 28) auf (30, 29)
  TEST_ASSERT_TRUE(wifiqr::boxLit(c, 3, 1, 32));
}

// Ein kleinerer Code (Version 1, 21 Module) steht mittig, die Ruhezone wird größer.
static void test_layout_smaller_code() {
  Code c;
  c.clear();
  c.size = 21;
  c.set(0, 0, true);
  TEST_ASSERT_FALSE(wifiqr::boxLit(c, 6, 5, 32));
  TEST_ASSERT_TRUE(wifiqr::boxLit(c, 5, 5, 32));
  TEST_ASSERT_TRUE(wifiqr::boxLit(c, 6, 4, 32));
  c.clear();   // kein Code: alles hell (zeichnet display aber nicht, es fällt auf reinen Text zurück)
  TEST_ASSERT_TRUE(wifiqr::boxLit(c, 0, 0, 32));
}

// Rechts vom Feld: der Name (16 Zeichen) genau bis zum Rand, kürzere Zeilen mittig, zu lange ab dem Feld.
static void test_text_right_of_box() {
  TEST_ASSERT_EQUAL_INT(33, wifiqr::textX(16, 128));
  TEST_ASSERT_EQUAL_INT(128, wifiqr::textX(16, 128) + 16 * 6 - 1);
  TEST_ASSERT_EQUAL_INT(36, wifiqr::textX(15, 128));   // "WLAN einrichten"
  TEST_ASSERT_EQUAL_INT(57, wifiqr::textX(8, 128));    // "Passwort", 8 Ziffern
  TEST_ASSERT_EQUAL_INT(33, wifiqr::textX(17, 128));
  TEST_ASSERT_EQUAL_INT(33, wifiqr::textX(40, 128));
}

int main(int, char**) {
  UNITY_BEGIN();
  RUN_TEST(test_portal_payload);
  RUN_TEST(test_escape_every_special_char);
  RUN_TEST(test_capacity_version_3);
  RUN_TEST(test_ecc_steps);
  RUN_TEST(test_longest_portal_fits);
  RUN_TEST(test_fallback_when_too_long);
  RUN_TEST(test_buffer_too_small);
  RUN_TEST(test_code_bits);
  RUN_TEST(test_layout_version_3);
  RUN_TEST(test_layout_smaller_code);
  RUN_TEST(test_text_right_of_box);
  return UNITY_END();
}
