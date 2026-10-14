#pragma once
// QR-Code für das Einrichtungs-WLAN (portal.cpp, display.cpp): Die Handykamera erkennt ihn und tritt dem WLAN ohne
// Tippen bei. Name und Passwort stehen weiter als Text rechts daneben, für Handys ohne QR-Erkennung.
//
// Inhalt im üblichen Schema WIFI:T:WPA;S:<Name>;P:<Passwort>;; (Kamera von iOS und Android). In Name und Passwort
// steht vor jedem \ ; , : " ein \. Codiert wird auf dem ESP32 mit dem qrcode-Baustein des ESP-IDF (qrcode.h, darin
// Nayukis qrcodegen), den Arduino-ESP32 2.0.17 und 3.x schon fertig mitlinken. Hier steht, was davor und danach kommt:
//
//   payload()   den Text bauen
//   ecc()       die höchste Fehlerkorrektur, mit der er noch in Version 3 passt (29 x 29 Module, 1 Pixel je Modul
//               auf 32 Zeilen). Passt er gar nicht, gibt es keinen Code: Das Panel zeigt nur den Text wie bisher.
//   Code        die Module; boxLit() legt sie mittig in ein helles Feld links (Ruhezone ringsum), textX() setzt
//               die Zeilen rechts daneben
//
// Der Text ist immer Byte-Modus (das ; darin gibt es weder im Ziffern- noch im Alphanumerik-Modus): Bei Version 1
// bis 3 kosten Modus und Länge 12 Bit, es passen also Datenwörter - 2 Byte.
//
// Reines C++11 wie manifest.h: dieselbe Datei auf dem ESP32 und in den Tests auf dem Rechner (test/test_wifiqr).

#include <stddef.h>
#include <stdint.h>
#include <string.h>

namespace wifiqr {

constexpr int MAX_VERSION = 3;
constexpr int MAX_SIZE = 4 * MAX_VERSION + 17;   // 29 Module je Seite
constexpr size_t SSID_MAX = 32;                  // Byte, wie settings::validSsid
constexpr size_t PASS_MAX = 64;                  // Byte, wie settings::validPass
// "WIFI:T:WPA;S:" + Name + ";P:" + Passwort + ";;", jedes Zeichen höchstens verdoppelt; ohne NUL
constexpr size_t PAYLOAD_MAX = 13 + 2 * SSID_MAX + 3 + 2 * PASS_MAX + 2;

// Fehlerkorrektur, gleich nummeriert wie ESP_QRCODE_ECC_LOW..HIGH (qrcode.h) und qrcodegen_Ecc
constexpr int ECC_NONE = -1;   // passt nicht in Version 3: kein Code
constexpr int ECC_LOW = 0, ECC_MEDIUM = 1, ECC_QUARTILE = 2, ECC_HIGH = 3;

// Datenwörter je Version (1 bis 3) und Stufe (LOW, MEDIUM, QUARTILE, HIGH), aus ISO/IEC 18004 Tabelle 7
constexpr uint8_t DATA_CODEWORDS[MAX_VERSION][4] = {{19, 16, 13, 9}, {34, 28, 22, 16}, {55, 44, 34, 26}};

// So viele Byte Text fasst Version version (1 bis 3) bei Stufe ecc im Byte-Modus
constexpr size_t capacity(int version, int ecc) { return DATA_CODEWORDS[version - 1][ecc] - 2u; }

// Höchste Stufe, mit der len Byte noch in Version MAX_VERSION passen, sonst ECC_NONE
inline int ecc(size_t len) {
  for (int e = ECC_HIGH; e >= ECC_LOW; e--) {
    if (len <= capacity(MAX_VERSION, e)) return e;
  }
  return ECC_NONE;
}

inline bool special(char c) { return c == '\\' || c == ';' || c == ',' || c == ':' || c == '"'; }

// s an out[n] anhängen, mit \ vor Sonderzeichen, wenn esc; false, wenn es mit NUL nicht in cap passt
inline bool append(char* out, size_t cap, size_t& n, const char* s, bool esc) {
  for (; *s; s++) {
    if (esc && special(*s)) {
      if (n + 1 >= cap) return false;
      out[n++] = '\\';
    }
    if (n + 1 >= cap) return false;
    out[n++] = *s;
  }
  out[n] = '\0';
  return true;
}

// Text für den QR-Code nach out (cap Byte samt NUL). Ergebnis: seine Länge ohne NUL, 0 und out leer, wenn cap nicht
// reicht. Mit cap = PAYLOAD_MAX + 1 reicht es für jeden gültigen Namen und jedes gültige Passwort.
inline size_t payload(char* out, size_t cap, const char* ssid, const char* pass) {
  if (cap == 0) return 0;
  size_t n = 0;
  const bool ok = append(out, cap, n, "WIFI:T:WPA;S:", false) && append(out, cap, n, ssid, true) &&
                  append(out, cap, n, ";P:", false) && append(out, cap, n, pass, true) &&
                  append(out, cap, n, ";;", false);
  if (!ok) {
    out[0] = '\0';
    return 0;
  }
  return n;
}

// Die Module eines Codes, Bit y * size + x, 1 = dunkel. size 0: kein Code. 107 Byte, einmal je Einrichtungs-WLAN.
struct Code {
  uint8_t size;
  uint8_t bits[(MAX_SIZE * MAX_SIZE + 7) / 8];

  void clear() {
    size = 0;
    memset(bits, 0, sizeof bits);
  }
  // außerhalb des Codes hell, wie die Ruhezone
  bool dark(int x, int y) const {
    if (x < 0 || y < 0 || x >= size || y >= size) return false;
    const int i = y * size + x;
    return (bits[i >> 3] >> (i & 7)) & 1;
  }
  void set(int x, int y, bool isDark) {
    if (x < 0 || y < 0 || x >= size || y >= size) return;
    const int i = y * size + x;
    if (isDark) bits[i >> 3] |= (uint8_t)(1u << (i & 7));
    else bits[i >> 3] &= (uint8_t)~(1u << (i & 7));
  }
};

// Anordnung auf dem Panel: links ein helles Feld über die volle Höhe, BOX_W Pixel breit, darin der Code mittig mit
// 1 Pixel je Modul. Bei 29 Modulen auf 32 Zeilen bleiben oben 1 und unten 2 Pixel Ruhezone, links und rechts QUIET_X.
// Rechts daneben passen genau die 16 Zeichen des Namens flightpanel-XXXX (16 x 6 Pixel, das letzte ohne Leerspalte).
constexpr int QUIET_X = 2;
constexpr int BOX_W = MAX_SIZE + 2 * QUIET_X;   // 33

// Pixel (x, y) des Felds, 0 <= x < BOX_W und 0 <= y < height: true = hell (Ruhezone oder helles Modul)
inline bool boxLit(const Code& c, int x, int y, int height) {
  return !c.dark(x - (BOX_W - c.size) / 2, y - (height - c.size) / 2);
}

// x einer Textzeile mit chars Zeichen der Standardschrift (6 Pixel je Zeichen) rechts vom Feld: mittig im Rest der
// Breite width. Was nicht passt, beginnt direkt am Feld und wird rechts abgeschnitten.
inline int textX(size_t chars, int width) {
  const int room = width - BOX_W;
  const int w = (int)chars * 6 - 1;   // das letzte Zeichen ohne seine Leerspalte
  return BOX_W + (w < room ? (room - w) / 2 : 0);
}

}  // namespace wifiqr
