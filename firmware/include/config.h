#pragma once
// Hardware- und Anzeige-Konfiguration. Zugangsdaten stehen in secrets.h.

// ---- Panel-Geometrie ------------------------------------------------------
#define PANEL_RES_X    64       // Pixel eines Panels, horizontal
#define PANEL_RES_Y    32       // Pixel eines Panels, vertikal
#define PANEL_CHAIN    2        // Panels in Reihe (ESP32 -> Panel 1 OUT -> Panel 2 IN)

#define WIDTH          (PANEL_RES_X * PANEL_CHAIN)   // 128
#define HEIGHT         PANEL_RES_Y                   // 32

// Panel 1 (am ESP32) muss links hängen. Hängt es rechts: SWAP_HALVES true.
#define SWAP_HALVES    false

// ---- HUB75-Pins (Standardbelegung der Bibliothek für den Classic-ESP32) ----
// HUB75-Stecker, Blick auf die Buchse am Panel, Nase oben:
//   R1  G1  B1  GND R2  G2  B2  E       obere Reihe  (E = GND bei 1/16-Scan)
//   A   B   C   D   CLK LAT OE  GND     untere Reihe
#define R1_PIN   25
#define G1_PIN   26
#define B1_PIN   27
#define R2_PIN   14
#define G2_PIN   12
#define B2_PIN   13
#define A_PIN    23
#define B_PIN    19
#define C_PIN     5
#define D_PIN    17
#define E_PIN    -1     // 1/16-Scan braucht kein E
#define LAT_PIN   4
#define OE_PIN   15
#define CLK_PIN  16

// ---- Bildqualität ---------------------------------------------------------
#define MAX_BRIGHTNESS   160    // Obergrenze (0-255), egal was der Server schickt
#define HUB75_CLKPHASE   false  // bei Geisterbildern/verschobenen Pixeln: true
#define HUB75_LATCH_BLANKING 0  // 0 = Automatik der Bibliothek; bei Ghosting 2..4 probieren
#define HUB75_DOUBLE_BUFFER  true

// ---- Verhalten ------------------------------------------------------------
#define POLL_MS            2000     // Abstand der /frame-Abrufe
#define HTTP_TIMEOUT_MS    3000
#define OFFLINE_MARK_MS    60000    // nach so lange ohne Frame: roter Punkt unten rechts
#define RESTART_AFTER_MS   600000   // nach so lange ohne Frame: Neustart
#define TEST_PATTERN_AT_BOOT true   // Panels einfärben, Rahmen, Schrift; nur nach dem Einschalten, nicht nach Neustarts
#define TEST_BRIGHTNESS    40

#define FRAME_HEADER       4
#define FRAME_LEN          (FRAME_HEADER + WIDTH * HEIGHT * 3)   // 12292
#define PROTO_VERSION      1
