#pragma once
// Firmware-Manifest firmware.txt lesen, den signierten Teil bestimmen, Versionen vergleichen.
//
// Reines C++11 ohne Arduino und ohne mbedTLS: Dieselbe Datei läuft auf dem ESP32 und in den Tests auf dem Rechner
// (pio test -e native, test/test_manifest). Die Signatur prüft src/fwsig.cpp auf dem Gerät.
//
// Format (tools/fw_release.sh schreibt es; test/data/golden_manifest.txt ist ein echtes Beispiel):
//
//   2610141530      Version: genau 10 Ziffern, die Bauzeit JJMMTTHHMM in UTC (FW_VERSION der Firmware)
//   1834560         Größe von firmware.bin in Byte: Dezimalzahl ohne führende Null, 0 < Größe <= Slot
//   9f86d081...     SHA-256 von firmware.bin: genau 64 Zeichen 0-9 und a-f (klein)
//   MEUCIQD...      Signatur: Base64 (Standardalphabet, mit "="-Auffüllung) einer DER-codierten
//                   ECDSA-P-256-Signatur (SHA-256) über die ersten drei Zeilen samt ihrer LFs; höchstens 96 Zeichen
//
// Genau vier Zeilen, jede mit LF (0x0A) abgeschlossen, auch die letzte: Fehlt das letzte LF, ist die Übertragung
// unvollständig. Kein CR, kein Leerzeichen, kein Tab, kein NUL, nur druckbares ASCII, höchstens 512 Byte. Was
// davon abweicht, wird abgelehnt, bevor ein Feld gedeutet wird. Ein mit CRLF gespeichertes Manifest nähme also kein
// Panel an; fw_release.sh prüft darum vor dem Ablegen, dass nur LF darin steht. Der Server liest nur die erste
// Zeile: Zu dieser Version liefert er firmware.bin?v=<Version> aus.
//
// Versionen sind Text fester Breite, nie int: 2610141530 passt nicht in 32 Bit. Bei gleicher Breite und nur Ziffern
// ordnet memcmp wie die Zahl.
//
// Ablauf auf dem Panel: parse() -> fwsigVerify(text, m.signedLen, m.sig, m.sigLen) -> isNewer(m.version, FW_VERSION)
// -> firmware.bin?v=<m.version> laden, dabei SHA-256 rechnen, mit m.sha256 und m.size vergleichen.

#include <stddef.h>
#include <stdint.h>
#include <string.h>

namespace manifest {

constexpr size_t MAX_LEN = 512;          // ganzes Manifest in Byte
constexpr size_t VERSION_LEN = 10;       // JJMMTTHHMM
constexpr size_t SIZE_DIGITS_MAX = 10;   // mehr Stellen hat kein uint32_t
constexpr size_t SHA256_LEN = 32;        // Byte, im Manifest 64 Zeichen hex
constexpr size_t SIG_B64_MAX = 96;       // Zeichen Base64
constexpr size_t SIG_DER_MAX = 72;       // Byte: SEQUENCE aus zwei INTEGER zu je höchstens 33 Byte

enum class Error : uint8_t {
  None = 0,
  Length,    // leer oder länger als MAX_LEN
  Byte,      // Zeichen außer druckbarem ASCII und LF: CR, NUL, Leerzeichen, Tab, Steuerzeichen, Nicht-ASCII
  Lines,     // nicht genau vier Zeilen, oder das letzte LF fehlt
  Version,   // Zeile 1
  Size,      // Zeile 2
  Sha256,    // Zeile 3
  Sig,       // Zeile 4: kein strenges Base64 oder keine DER-Signatur in der Form von ECDSA P-256
};

// Für die serielle Ausgabe
inline const char* errorText(Error e) {
  switch (e) {
    case Error::None: return "ok";
    case Error::Length: return "leer oder länger als 512 Byte";
    case Error::Byte: return "unerlaubtes Zeichen (CR, NUL, Leerzeichen, Steuerzeichen oder Nicht-ASCII)";
    case Error::Lines: return "nicht genau vier Zeilen mit LF am Ende";
    case Error::Version: return "Zeile 1: Version nicht genau 10 Ziffern";
    case Error::Size: return "Zeile 2: Größe ungültig oder größer als der Slot";
    case Error::Sha256: return "Zeile 3: SHA-256 nicht genau 64 Zeichen 0-9a-f";
    case Error::Sig: return "Zeile 4: Signatur kein Base64 einer DER-Signatur";
  }
  return "?";
}

struct Manifest {
  char version[VERSION_LEN + 1];   // 10 Ziffern und NUL
  uint32_t size;                   // Byte, 0 < size <= Slot
  uint8_t sha256[SHA256_LEN];      // aus Zeile 3
  uint8_t sig[SIG_DER_MAX];        // DER, so wie mbedtls_pk_verify sie erwartet
  size_t sigLen;
  size_t signedLen;                // signiert sind die ersten signedLen Byte des Manifests (drei Zeilen samt LF)
};

// Genau n Ziffern 0-9, danach das Ende des Texts. constexpr, damit fwsig.cpp FW_VERSION schon beim Übersetzen prüft.
constexpr bool digitsOnly(const char* s, size_t n) {
  return n == 0 ? *s == '\0' : (*s >= '0' && *s <= '9' && digitsOnly(s + 1, n - 1));
}

constexpr bool isVersion(const char* s) { return s != nullptr && digitsOnly(s, VERSION_LEN); }

// true, wenn candidate neuer ist als current. Beide müssen genau 10 Ziffern sein, sonst false: lieber kein Update
// als eines wegen einer kaputten Version. Gleich ist nicht neuer, ältere Versionen gibt es nie (kein Downgrade).
inline bool isNewer(const char* candidate, const char* current) {
  return isVersion(candidate) && isVersion(current) && memcmp(candidate, current, VERSION_LEN) > 0;
}

// Länge des signierten Teils: alles bis einschließlich zum dritten LF, also die ersten drei Zeilen. 0, wenn der Text
// keine drei LF hat. parse() setzt Manifest::signedLen damit; gültig ist der Wert erst nach einem erfolgreichen parse().
inline size_t signedSpanLength(const char* text, size_t len) {
  size_t lf = 0;
  for (size_t i = 0; i < len; i++) {
    if (text[i] == '\n' && ++lf == 3) return i + 1;
  }
  return 0;
}

namespace detail {

inline bool isDigit(char c) { return c >= '0' && c <= '9'; }

inline int hexValue(char c) {
  if (c >= '0' && c <= '9') return c - '0';
  if (c >= 'a' && c <= 'f') return c - 'a' + 10;
  return -1;   // auch A-F: nur eine Schreibweise
}

inline int base64Value(char c) {
  if (c >= 'A' && c <= 'Z') return c - 'A';
  if (c >= 'a' && c <= 'z') return c - 'a' + 26;
  if (c >= '0' && c <= '9') return c - '0' + 52;
  if (c == '+') return 62;
  if (c == '/') return 63;
  return -1;
}

// Strenges Base64 (openssl base64 -A): Länge ein Vielfaches von 4, "=" nur als Auffüllung am Ende (höchstens zwei),
// ungenutzte Bits null, damit es zu jeder Bytefolge nur eine Schreibweise gibt. Gibt die Zahl der Bytes zurück,
// 0 bei einem Fehler oder wenn out zu klein ist.
inline size_t base64Decode(const char* s, size_t n, uint8_t* out, size_t outMax) {
  if (n == 0 || n % 4 != 0) return 0;
  size_t pad = 0;
  if (s[n - 1] == '=') pad = (s[n - 2] == '=') ? 2 : 1;
  if (n / 4 * 3 - pad > outMax) return 0;
  size_t o = 0;
  for (size_t i = 0; i < n; i += 4) {
    const bool last = (i + 4 == n);
    const size_t padHere = last ? pad : 0;
    uint32_t quad = 0;
    for (size_t k = 0; k < 4; k++) {
      int v = 0;
      if (k >= 4 - padHere) {
        if (s[i + k] != '=') return 0;
      } else {
        v = base64Value(s[i + k]);
        if (v < 0) return 0;   // auch "=" mitten im Text
      }
      quad = (quad << 6) | (uint32_t)v;
    }
    const size_t bytes = 3 - padHere;
    if ((bytes == 1 && (quad & 0xFFFF) != 0) || (bytes == 2 && (quad & 0xFF) != 0)) return 0;
    out[o++] = (uint8_t)(quad >> 16);
    if (bytes > 1) out[o++] = (uint8_t)(quad >> 8);
    if (bytes > 2) out[o++] = (uint8_t)quad;
  }
  return o;
}

// Ein INTEGER von ECDSA-Sig-Value ab d[pos]: Tag 0x02, Länge 1 bis 33, positiv, minimal codiert, nicht null. Ein
// Wert von P-256 hat höchstens 32 Byte; ein 33. Byte gibt es nur als Null davor, wenn sonst das oberste Bit gesetzt
// wäre. Rückt pos hinter den Wert.
inline bool derInteger(const uint8_t* d, size_t n, size_t& pos) {
  if (pos + 2 > n || d[pos] != 0x02) return false;
  const size_t len = d[pos + 1];
  const size_t start = pos + 2;
  if (len < 1 || len > 33 || start + len > n) return false;
  const uint8_t* v = d + start;
  if (v[0] & 0x80) return false;                                // negativ
  if (len > 1 && v[0] == 0x00 && !(v[1] & 0x80)) return false;  // überflüssige Null davor
  if (len == 33 && v[0] != 0x00) return false;                  // mehr als 256 Bit
  if (len == 1 && v[0] == 0x00) return false;                   // null
  pos = start + len;
  return true;
}

// ECDSA-Sig-Value in DER: SEQUENCE (0x30, kurze Länge) mit genau zwei INTEGER r und s, nichts dahinter. Nur die Form;
// ob r und s zur Kurve passen und die Signatur stimmt, prüft mbedTLS auf dem Gerät.
inline bool derSignature(const uint8_t* d, size_t n) {
  if (n < 8 || n > SIG_DER_MAX || d[0] != 0x30 || (size_t)d[1] != n - 2) return false;
  size_t pos = 2;
  return derInteger(d, n, pos) && derInteger(d, n, pos) && pos == n;
}

}  // namespace detail

// Prüft text[0, len) gegen das Format oben und füllt out. slotSize: Größe der App-Partition, in die das Image
// geschrieben würde (Byte). Bei einem Fehler ist out leer (Nullen); gültig ist out nur bei Error::None.
inline Error parse(const char* text, size_t len, uint32_t slotSize, Manifest& out) {
  out = Manifest();
  if (text == nullptr || len == 0 || len > MAX_LEN) return Error::Length;

  // 1. Zeichen und Zeilen, bevor irgendein Feld gelesen wird
  size_t lfs = 0;
  for (size_t i = 0; i < len; i++) {
    const unsigned char c = (unsigned char)text[i];
    if (c == '\n') lfs++;
    else if (c < 0x21 || c > 0x7E) return Error::Byte;
  }
  if (lfs != 4 || text[len - 1] != '\n') return Error::Lines;
  const char* line[4] = {nullptr, nullptr, nullptr, nullptr};
  size_t lineLen[4] = {0, 0, 0, 0};
  size_t start = 0, k = 0;
  for (size_t i = 0; i < len; i++) {
    if (text[i] != '\n') continue;
    line[k] = text + start;
    lineLen[k] = i - start;
    k++;
    start = i + 1;
  }

  // 2. Felder, jedes mit fester Breite oder fester Obergrenze, in feste Puffer
  if (lineLen[0] != VERSION_LEN) return Error::Version;
  for (size_t i = 0; i < VERSION_LEN; i++) {
    if (!detail::isDigit(line[0][i])) return Error::Version;
  }

  if (lineLen[1] < 1 || lineLen[1] > SIZE_DIGITS_MAX || line[1][0] == '0') return Error::Size;
  uint64_t size = 0;
  for (size_t i = 0; i < lineLen[1]; i++) {
    if (!detail::isDigit(line[1][i])) return Error::Size;
    size = size * 10 + (uint64_t)(line[1][i] - '0');   // höchstens 10 Stellen: kein Überlauf
  }
  if (size == 0 || size > slotSize) return Error::Size;

  if (lineLen[2] != 2 * SHA256_LEN) return Error::Sha256;
  uint8_t sha[SHA256_LEN];
  for (size_t i = 0; i < SHA256_LEN; i++) {
    const int hi = detail::hexValue(line[2][2 * i]);
    const int lo = detail::hexValue(line[2][2 * i + 1]);
    if (hi < 0 || lo < 0) return Error::Sha256;
    sha[i] = (uint8_t)((hi << 4) | lo);
  }

  if (lineLen[3] > SIG_B64_MAX) return Error::Sig;
  uint8_t sig[SIG_DER_MAX];
  const size_t sigLen = detail::base64Decode(line[3], lineLen[3], sig, sizeof sig);
  if (sigLen == 0 || !detail::derSignature(sig, sigLen)) return Error::Sig;

  // 3. alles gültig: erst jetzt nach out
  memcpy(out.version, line[0], VERSION_LEN);
  out.version[VERSION_LEN] = '\0';
  out.size = (uint32_t)size;
  memcpy(out.sha256, sha, SHA256_LEN);
  memcpy(out.sig, sig, sigLen);
  out.sigLen = sigLen;
  out.signedLen = signedSpanLength(text, len);
  return Error::None;
}

}  // namespace manifest
