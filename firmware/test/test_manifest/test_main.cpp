// Tests auf dem Rechner für include/manifest.h: Manifest-Parser, signierter Teil, Versionsvergleich.
//
//   pio test -e native        im Ordner firmware/ (tools/check_firmware.sh macht das in Docker)
//
// test/data/golden_manifest.txt ist ein echt signiertes Manifest (Testschlüssel test/data/test_pubkey.pem, erzeugt
// mit test/data/make_golden.sh; der private Schlüssel existiert nicht mehr). Dass die Signatur stimmt, prüft
// tools/check_firmware.sh mit openssl; hier geht es um das Format und darum, dass alles andere abgelehnt wird.

#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <unity.h>

#include <string>
#include <vector>

#include "manifest.h"

using manifest::Error;
using manifest::Manifest;

static const uint32_t SLOT = 0x1E0000;   // App-Slot von min_spiffs.csv: 1,875 MB
static const char* GOLDEN_PATH = "test/data/golden_manifest.txt";

// ---- Hilfen -----------------------------------------------------------------------------------------------------

static std::string golden() {
  static std::string cached;
  if (!cached.empty()) return cached;
  FILE* f = fopen(GOLDEN_PATH, "rb");
  if (f == nullptr) TEST_FAIL_MESSAGE("test/data/golden_manifest.txt fehlt: pio test im Ordner firmware/ aufrufen");
  char buf[1024];
  const size_t n = fread(buf, 1, sizeof buf, f);
  fclose(f);
  cached.assign(buf, n);
  return cached;
}

// Die vier Zeilen des Golden-Manifests ohne LF
static std::vector<std::string> goldenLines() {
  std::vector<std::string> out;
  const std::string g = golden();
  size_t start = 0;
  for (size_t i = 0; i < g.size(); i++) {
    if (g[i] != '\n') continue;
    out.push_back(g.substr(start, i - start));
    start = i + 1;
  }
  return out;
}

static std::string join(const std::vector<std::string>& lines) {
  std::string out;
  for (const std::string& l : lines) out += l + "\n";
  return out;
}

// Golden-Manifest mit ersetzter Zeile i (0 bis 3)
static std::string withLine(size_t i, const std::string& content) {
  std::vector<std::string> lines = goldenLines();
  lines[i] = content;
  return join(lines);
}

static Error parse(const std::string& text, uint32_t slot = SLOT) {
  Manifest m;
  return manifest::parse(text.data(), text.size(), slot, m);
}

static void expectError(Error want, const std::string& text, const char* what, uint32_t slot = SLOT) {
  TEST_ASSERT_EQUAL_STRING_MESSAGE(manifest::errorText(want), manifest::errorText(parse(text, slot)), what);
}

static std::string base64(const std::vector<uint8_t>& d) {
  static const char* A = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
  std::string out;
  size_t i = 0;
  for (; i + 3 <= d.size(); i += 3) {
    const uint32_t q = ((uint32_t)d[i] << 16) | ((uint32_t)d[i + 1] << 8) | d[i + 2];
    out += A[(q >> 18) & 63];
    out += A[(q >> 12) & 63];
    out += A[(q >> 6) & 63];
    out += A[q & 63];
  }
  if (d.size() - i == 1) {
    const uint32_t q = (uint32_t)d[i] << 16;
    out += A[(q >> 18) & 63];
    out += A[(q >> 12) & 63];
    out += "==";
  } else if (d.size() - i == 2) {
    const uint32_t q = ((uint32_t)d[i] << 16) | ((uint32_t)d[i + 1] << 8);
    out += A[(q >> 18) & 63];
    out += A[(q >> 12) & 63];
    out += A[(q >> 6) & 63];
    out += "=";
  }
  return out;
}

// ECDSA-Sig-Value in DER aus r und s, wie sie da stehen (auch absichtlich falsch codiert)
static std::vector<uint8_t> der(const std::vector<uint8_t>& r, const std::vector<uint8_t>& s) {
  std::vector<uint8_t> d = {0x30, (uint8_t)(4 + r.size() + s.size()), 0x02, (uint8_t)r.size()};
  d.insert(d.end(), r.begin(), r.end());
  d.push_back(0x02);
  d.push_back((uint8_t)s.size());
  d.insert(d.end(), s.begin(), s.end());
  return d;
}

static std::vector<uint8_t> bytes(size_t n, uint8_t first, uint8_t rest) {
  std::vector<uint8_t> v(n, rest);
  v[0] = first;
  return v;
}

static std::string withSig(const std::vector<uint8_t>& d) { return withLine(3, base64(d)); }

void setUp() {}
void tearDown() {}

// ---- Golden-Manifest -------------------------------------------------------------------------------------------

// So muss tools/fw_release.sh schreiben: nur LF, auch am Ende, vier Zeilen, kein CR, kein NUL.
static void test_golden_file_is_lf_only() {
  const std::string g = golden();
  TEST_ASSERT_TRUE(g.size() > 0 && g.size() <= manifest::MAX_LEN);
  size_t lf = 0;
  for (char c : g) {
    TEST_ASSERT_NOT_EQUAL('\r', c);
    TEST_ASSERT_NOT_EQUAL('\0', c);
    if (c == '\n') lf++;
  }
  TEST_ASSERT_EQUAL(4, lf);
  TEST_ASSERT_EQUAL('\n', g[g.size() - 1]);
}

static void test_golden_parses() {
  const std::string g = golden();
  Manifest m;
  TEST_ASSERT_EQUAL_STRING("ok", manifest::errorText(manifest::parse(g.data(), g.size(), SLOT, m)));
  TEST_ASSERT_EQUAL_STRING("2610140000", m.version);
  TEST_ASSERT_EQUAL_UINT32(1048576, m.size);
  // SHA-256 von 1 MiB Null-Bytes
  static const uint8_t sha[32] = {0x30, 0xe1, 0x49, 0x55, 0xeb, 0xf1, 0x35, 0x22, 0x66, 0xdc, 0x2f,
                                  0xf8, 0x06, 0x7e, 0x68, 0x10, 0x46, 0x07, 0xe7, 0x50, 0xab, 0xb9,
                                  0xd3, 0xb3, 0x65, 0x82, 0xb8, 0xaf, 0x90, 0x9f, 0xcb, 0x58};
  TEST_ASSERT_EQUAL_HEX8_ARRAY(sha, m.sha256, 32);
  // App-Slot der heutigen Partitionstabelle (default.csv, 1,25 MB) reicht auch
  TEST_ASSERT_EQUAL_STRING("ok", manifest::errorText(manifest::parse(g.data(), g.size(), 0x140000, m)));
}

// Die Signatur ist Base64 einer DER-Signatur in der Form von ECDSA P-256.
static void test_golden_signature_is_der() {
  const std::string g = golden();
  Manifest m;
  TEST_ASSERT_EQUAL(Error::None, manifest::parse(g.data(), g.size(), SLOT, m));
  TEST_ASSERT_TRUE(m.sigLen >= 8 && m.sigLen <= manifest::SIG_DER_MAX);
  TEST_ASSERT_EQUAL_HEX8(0x30, m.sig[0]);           // SEQUENCE
  TEST_ASSERT_EQUAL(m.sigLen - 2, m.sig[1]);       // kurze Länge, deckt den Rest genau
  TEST_ASSERT_EQUAL_HEX8(0x02, m.sig[2]);           // INTEGER r
  const size_t rLen = m.sig[3];
  TEST_ASSERT_TRUE(rLen >= 1 && rLen <= 33);
  TEST_ASSERT_EQUAL_HEX8(0x02, m.sig[4 + rLen]);    // INTEGER s
  const size_t sLen = m.sig[5 + rLen];
  TEST_ASSERT_TRUE(sLen >= 1 && sLen <= 33);
  TEST_ASSERT_EQUAL(m.sigLen, 6 + rLen + sLen);
  // und Base64 zurück ergibt genau die vierte Zeile: nur eine Schreibweise
  const std::vector<uint8_t> d(m.sig, m.sig + m.sigLen);
  TEST_ASSERT_EQUAL_STRING(goldenLines()[3].c_str(), base64(d).c_str());
}

// Signiert sind die ersten drei Zeilen samt ihrer LFs, nicht mehr und nicht weniger.
static void test_signed_span() {
  const std::string g = golden();
  const std::vector<std::string> lines = goldenLines();
  Manifest m;
  TEST_ASSERT_EQUAL(Error::None, manifest::parse(g.data(), g.size(), SLOT, m));
  const std::string head = lines[0] + "\n" + lines[1] + "\n" + lines[2] + "\n";
  TEST_ASSERT_EQUAL(head.size(), m.signedLen);
  TEST_ASSERT_EQUAL(m.signedLen, manifest::signedSpanLength(g.data(), g.size()));
  TEST_ASSERT_EQUAL_STRING(head.c_str(), g.substr(0, m.signedLen).c_str());
  TEST_ASSERT_EQUAL(6, manifest::signedSpanLength("a\nb\nc\nd\n", 8));
  TEST_ASSERT_EQUAL(0, manifest::signedSpanLength("a\nb\nc", 5));
  TEST_ASSERT_EQUAL(0, manifest::signedSpanLength("", 0));
}

// ---- abgelehnt: Länge, Zeichen, Zeilen -------------------------------------------------------------------------

// Jeder Anfang des Manifests, auch ohne das letzte LF, ist ungültig: Eine abgebrochene Übertragung fällt auf.
static void test_truncated() {
  const std::string g = golden();
  char what[64];
  for (size_t n = 0; n < g.size(); n++) {
    snprintf(what, sizeof what, "nach %u Byte abgeschnitten", (unsigned)n);
    TEST_ASSERT_TRUE_MESSAGE(parse(g.substr(0, n)) != Error::None, what);
  }
  expectError(Error::Length, "", "leer");
  expectError(Error::Lines, g.substr(0, g.size() - 1), "ohne LF am Ende");
  Manifest m;
  TEST_ASSERT_EQUAL(Error::Length, manifest::parse(nullptr, 0, SLOT, m));
}

static void test_oversized() {
  const std::string g = golden();
  expectError(Error::Length, g + std::string(manifest::MAX_LEN + 1 - g.size(), 'A'), "513 Byte");
  expectError(Error::Length, std::string(4096, 'A'), "4 KB");
  // 512 Byte sind erlaubt, aber dann stimmt die Form nicht mehr
  expectError(Error::Lines, g + std::string(manifest::MAX_LEN - g.size(), 'A'), "512 Byte");
}

// Ein unter Windows mit CRLF gespeichertes Manifest wird nie angenommen.
static void test_crlf() {
  const std::string g = golden();
  std::string crlf;
  for (char c : g) {
    if (c == '\n') crlf += '\r';
    crlf += c;
  }
  expectError(Error::Byte, crlf, "CRLF");
  expectError(Error::Byte, withLine(0, goldenLines()[0] + "\r"), "CR nach der Version");
  expectError(Error::Byte, g + "\r", "CR am Ende");
}

// CR, NUL, Leerzeichen, Tab, Steuerzeichen und Nicht-ASCII an jeder Stelle: abgelehnt, bevor ein Feld gelesen wird.
static void test_forbidden_bytes() {
  const std::string g = golden();
  const char bad[] = {'\0', ' ', '\t', '\r', '\x01', '\x7f', (char)0x80, (char)0xc3, (char)0xff};
  char what[64];
  for (size_t i = 0; i < g.size(); i++) {
    if (g[i] == '\n') continue;
    for (char b : bad) {
      std::string t = g;
      t[i] = b;
      snprintf(what, sizeof what, "Byte 0x%02x an Stelle %u", (unsigned)(unsigned char)b, (unsigned)i);
      expectError(Error::Byte, t, what);
    }
  }
  std::string nul = g;
  nul.insert(5, 1, '\0');
  expectError(Error::Byte, nul, "NUL eingefügt");
  expectError(Error::Byte, g + std::string(1, '\0'), "NUL nach dem letzten LF");
  expectError(Error::Byte, withLine(1, " 1048576"), "Leerzeichen vor der Größe");
  expectError(Error::Byte, withLine(1, "1048576 "), "Leerzeichen nach der Größe");
}

static void test_lines() {
  const std::string g = golden();
  const std::vector<std::string> lines = goldenLines();
  expectError(Error::Lines, g + "x\n", "fünfte Zeile");
  expectError(Error::Lines, g + "\n", "leere fünfte Zeile");
  expectError(Error::Lines, lines[0] + "\n" + lines[1] + "\n" + lines[2] + "\n", "ohne Signatur");
  expectError(Error::Lines, lines[0] + lines[1] + "\n" + lines[2] + "\n" + lines[3] + "\n", "zwei Zeilen in einer");
  expectError(Error::Lines, "\n" + g.substr(0, g.size() - 1), "LF am Anfang statt am Ende");
  expectError(Error::Version, "\n\n\n\n", "vier leere Zeilen");
  // Reihenfolge fest: Größe zuerst ist keine Version
  expectError(Error::Version, lines[1] + "\n" + lines[0] + "\n" + lines[2] + "\n" + lines[3] + "\n", "vertauscht");
}

// ---- abgelehnt: einzelne Felder --------------------------------------------------------------------------------

static void test_version_field() {
  expectError(Error::Version, withLine(0, "261014000"), "9 Ziffern");
  expectError(Error::Version, withLine(0, "26101400000"), "11 Ziffern");
  expectError(Error::Version, withLine(0, "261014000a"), "Buchstabe");
  expectError(Error::Version, withLine(0, "+261014000"), "Vorzeichen");
  expectError(Error::Version, withLine(0, "2610-14000"), "Bindestrich");
  expectError(Error::Version, withLine(0, ""), "leer");
  // Feste Breite: führende Nullen gehören zur Version (nur nie neuer als eine echte)
  expectError(Error::None, withLine(0, "0000000000"), "zehn Nullen");
}

static void test_size_field() {
  expectError(Error::Size, withLine(1, "0"), "null");
  expectError(Error::Size, withLine(1, "01048576"), "führende Null");
  expectError(Error::Size, withLine(1, ""), "leer");
  expectError(Error::Size, withLine(1, "+1048576"), "Vorzeichen");
  expectError(Error::Size, withLine(1, "-1"), "negativ");
  expectError(Error::Size, withLine(1, "1e6"), "Exponent");
  expectError(Error::Size, withLine(1, "0x100000"), "hex");
  expectError(Error::Size, withLine(1, "99999999999"), "11 Stellen");
  // Grenze Slot
  expectError(Error::None, withLine(1, "1966080"), "genau der Slot", SLOT);
  expectError(Error::Size, withLine(1, "1966081"), "ein Byte über dem Slot", SLOT);
  expectError(Error::Size, golden(), "Slot kleiner als das Image", 1048575);
  // Grenze uint32_t
  expectError(Error::None, withLine(1, "4294967295"), "größtes uint32_t", 0xFFFFFFFF);
  expectError(Error::Size, withLine(1, "4294967296"), "über uint32_t", 0xFFFFFFFF);
  expectError(Error::Size, withLine(1, "9999999999"), "zehn Neunen", 0xFFFFFFFF);
}

static void test_sha256_field() {
  const std::string sha = goldenLines()[2];
  expectError(Error::Sha256, withLine(2, sha.substr(1)), "63 Zeichen");
  expectError(Error::Sha256, withLine(2, sha + "0"), "65 Zeichen");
  std::string upper = sha;
  for (char& c : upper) {
    if (c >= 'a' && c <= 'f') c = (char)(c - 'a' + 'A');
  }
  expectError(Error::Sha256, withLine(2, upper), "Großbuchstaben");
  std::string g = sha;
  g[10] = 'g';
  expectError(Error::Sha256, withLine(2, g), "kein Hex");
  expectError(Error::Sha256, withLine(2, ""), "leer");
}

static void test_sig_base64() {
  const std::string sig = goldenLines()[3];
  TEST_ASSERT_EQUAL(96, sig.size());   // 72 Byte DER: die längste mögliche Signatur
  expectError(Error::Sig, withLine(3, ""), "leer");
  expectError(Error::Sig, withLine(3, sig + "AAAA"), "100 Zeichen");
  expectError(Error::Sig, withLine(3, sig.substr(0, 95)), "Länge kein Vielfaches von 4");
  expectError(Error::Sig, withLine(3, sig.substr(0, 92)), "abgeschnitten, aber Vielfaches von 4");
  std::string urlSafe = sig;
  urlSafe[10] = '-';
  expectError(Error::Sig, withLine(3, urlSafe), "URL-Alphabet");
  std::string padMid = sig;
  padMid[40] = '=';
  expectError(Error::Sig, withLine(3, padMid), "= mitten im Text");
  expectError(Error::Sig, withLine(3, sig.substr(0, 92) + "A==="), "drei =");
}

// Kürzere Signaturen (r oder s ohne Null davor) haben Auffüllung; die ungenutzten Bits müssen null sein.
static void test_sig_padding() {
  const std::vector<uint8_t> r32 = bytes(32, 0x7f, 0xab);
  const std::vector<uint8_t> s33 = bytes(33, 0x00, 0x91);
  const std::vector<uint8_t> s32 = bytes(32, 0x12, 0x34);
  const std::vector<uint8_t> d71 = der(r32, s33);   // 71 Byte: ein "="
  const std::vector<uint8_t> d70 = der(r32, s32);   // 70 Byte: zwei "="
  TEST_ASSERT_EQUAL(71, d71.size());
  TEST_ASSERT_EQUAL(70, d70.size());
  const std::string b71 = base64(d71);
  const std::string b70 = base64(d70);
  TEST_ASSERT_EQUAL('=', b71[95]);
  TEST_ASSERT_EQUAL('=', b70[94]);
  expectError(Error::None, withLine(3, b71), "71 Byte");
  expectError(Error::None, withLine(3, b70), "70 Byte");
  // dieselben Bytes in einer zweiten Schreibweise: ungenutzte Bits gesetzt
  std::string n71 = b71;
  n71[94] = (char)(n71[94] + 1);
  expectError(Error::Sig, withLine(3, n71), "ungenutzte Bits bei einem =");
  std::string n70 = b70;
  n70[93] = (char)(n70[93] + 1);
  expectError(Error::Sig, withLine(3, n70), "ungenutzte Bits bei zwei =");
}

// Nur die Form von ECDSA-Sig-Value in DER; ob die Signatur stimmt, prüft mbedTLS auf dem Gerät.
static void test_sig_der_shape() {
  const std::vector<uint8_t> r = bytes(32, 0x7f, 0xab);
  const std::vector<uint8_t> s = bytes(32, 0x12, 0x34);
  expectError(Error::None, withSig(der({0x01}, {0x01})), "kleinste Form");
  // 64 Byte r||s ohne DER (die andere übliche Codierung)
  std::vector<uint8_t> raw(r);
  raw.insert(raw.end(), s.begin(), s.end());
  expectError(Error::Sig, withSig(raw), "r||s ohne DER");
  std::vector<uint8_t> d = der(r, s);
  d.push_back(0x00);
  expectError(Error::Sig, withSig(d), "Byte hinter der SEQUENCE");
  d = der(r, s);
  d[1]++;
  expectError(Error::Sig, withSig(d), "SEQUENCE-Länge falsch");
  d = der(r, s);
  d[0] = 0x31;
  expectError(Error::Sig, withSig(d), "SET statt SEQUENCE");
  d = der(r, s);
  d[2] = 0x03;
  expectError(Error::Sig, withSig(d), "kein INTEGER");
  expectError(Error::Sig, withSig(der(bytes(32, 0x80, 0x01), s)), "r negativ");
  expectError(Error::Sig, withSig(der(bytes(33, 0x00, 0x01), s)), "r mit überflüssiger Null");
  expectError(Error::Sig, withSig(der(bytes(33, 0x01, 0x01), s)), "r mit 33 Byte ohne Null");
  expectError(Error::Sig, withSig(der({0x00}, s)), "r null");
  expectError(Error::Sig, withSig(der(r, bytes(34, 0x00, 0x80))), "s mit 34 Byte");
  expectError(Error::Sig, withSig(der(r, {})), "s ohne Bytes");
  // drei INTEGER (klein, damit die Länge passt und die DER-Prüfung greift)
  std::vector<uint8_t> three = der({0x01}, {0x01});
  three.insert(three.end(), {0x02, 0x01, 0x01});
  three[1] = (uint8_t)(three.size() - 2);
  expectError(Error::Sig, withSig(three), "drei INTEGER");
  expectError(Error::None, withSig(der({0x01}, {0x01})), "dieselben zwei ohne den dritten");
}

static void test_garbage() {
  expectError(Error::Byte, "<html><body>502 Bad Gateway</body></html>\n", "HTML-Fehlerseite");
  expectError(Error::Lines, "{\"version\":\"2610140000\"}\n", "JSON");
  expectError(Error::Lines, "2610140000\n", "nur die Version");
  expectError(Error::Version, "version=2610140000\nsize=1\nsha256=0\nsig=0\n", "mit Schlüsselnamen");
  // Rauschen, reproduzierbar
  uint32_t x = 2463534242u;
  char what[64];
  for (int round = 0; round < 200; round++) {
    std::string t(1 + round % 300, '\0');
    for (char& c : t) {
      x ^= x << 13;
      x ^= x >> 17;
      x ^= x << 5;
      c = (char)(x & 0xff);
    }
    snprintf(what, sizeof what, "Rauschen %d", round);
    TEST_ASSERT_TRUE_MESSAGE(parse(t) != Error::None, what);
  }
}

// Nach einem Fehler steht nichts Halbes in out.
static void test_output_cleared_on_error() {
  const std::string bad = withLine(3, "AAAA");
  Manifest m;
  memset(&m, 0xAA, sizeof m);
  TEST_ASSERT_EQUAL(Error::Sig, manifest::parse(bad.data(), bad.size(), SLOT, m));
  Manifest zero;
  memset(&zero, 0, sizeof zero);
  TEST_ASSERT_EQUAL_MEMORY(&zero, &m, sizeof m);
}

// ---- Versionen ---------------------------------------------------------------------------------------------------

static_assert(manifest::isVersion("2610141530"), "zehn Ziffern");
static_assert(!manifest::isVersion("261014153"), "neun Ziffern");
static_assert(!manifest::isVersion("26101415300"), "elf Ziffern");
static_assert(!manifest::isVersion("26101415x0"), "Buchstabe");

static void test_version_compare() {
  TEST_ASSERT_TRUE(manifest::isNewer("2610141530", "2610141529"));
  TEST_ASSERT_FALSE(manifest::isNewer("2610141530", "2610141530"));   // gleich: nicht neu installieren
  TEST_ASSERT_FALSE(manifest::isNewer("2610141529", "2610141530"));   // älter: kein Downgrade
  TEST_ASSERT_TRUE(manifest::isNewer("2701010000", "2612312359"));    // Jahreswechsel
  TEST_ASSERT_TRUE(manifest::isNewer("4294967296", "4294967295"));    // über 32 Bit, als Text richtig
  TEST_ASSERT_TRUE(manifest::isNewer("9999999999", "4294967296"));
  TEST_ASSERT_TRUE(manifest::isNewer("2610141530", "0000000000"));
  // kaputte Versionen: nie neuer, in beide Richtungen
  TEST_ASSERT_FALSE(manifest::isNewer("261014153", "2610141529"));
  TEST_ASSERT_FALSE(manifest::isNewer("26101415300", "2610141529"));
  TEST_ASSERT_FALSE(manifest::isNewer("9999999999", "261014152"));
  TEST_ASSERT_FALSE(manifest::isNewer("9999999999", "26101415x9"));
  TEST_ASSERT_FALSE(manifest::isNewer("9999999999", ""));
  TEST_ASSERT_FALSE(manifest::isNewer(nullptr, "2610141529"));
  TEST_ASSERT_FALSE(manifest::isNewer("9999999999", nullptr));
}

// Ein Manifest mit älterer oder gleicher Version als die laufende Firmware wird nicht installiert.
static void test_old_version_manifest() {
  const std::string g = golden();
  Manifest m;
  TEST_ASSERT_EQUAL(Error::None, manifest::parse(g.data(), g.size(), SLOT, m));
  TEST_ASSERT_TRUE(manifest::isNewer(m.version, "2610132359"));    // Panel älter: Update
  TEST_ASSERT_FALSE(manifest::isNewer(m.version, "2610140000"));   // dieselbe Version
  TEST_ASSERT_FALSE(manifest::isNewer(m.version, "2610141200"));   // Panel neuer: kein Downgrade
}

static void test_error_texts() {
  const Error all[] = {Error::None, Error::Length, Error::Byte, Error::Lines,
                       Error::Version, Error::Size, Error::Sha256, Error::Sig};
  TEST_ASSERT_EQUAL_STRING("ok", manifest::errorText(Error::None));
  for (Error e : all) {
    TEST_ASSERT_TRUE(strlen(manifest::errorText(e)) > 1);
    if (e != Error::None) TEST_ASSERT_TRUE(strcmp(manifest::errorText(e), "?") != 0);
  }
}

int main(int, char**) {
  UNITY_BEGIN();
  RUN_TEST(test_golden_file_is_lf_only);
  RUN_TEST(test_golden_parses);
  RUN_TEST(test_golden_signature_is_der);
  RUN_TEST(test_signed_span);
  RUN_TEST(test_truncated);
  RUN_TEST(test_oversized);
  RUN_TEST(test_crlf);
  RUN_TEST(test_forbidden_bytes);
  RUN_TEST(test_lines);
  RUN_TEST(test_version_field);
  RUN_TEST(test_size_field);
  RUN_TEST(test_sha256_field);
  RUN_TEST(test_sig_base64);
  RUN_TEST(test_sig_padding);
  RUN_TEST(test_sig_der_shape);
  RUN_TEST(test_garbage);
  RUN_TEST(test_output_cleared_on_error);
  RUN_TEST(test_version_compare);
  RUN_TEST(test_old_version_manifest);
  RUN_TEST(test_error_texts);
  return UNITY_END();
}
