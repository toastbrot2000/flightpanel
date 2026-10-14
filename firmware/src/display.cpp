// HUB75-Ausgabe: Testbild, Bilder vom Server, Offline-Punkt und Text für das Einrichtungs-WLAN.
#include "display.h"

#include <Arduino.h>
#include <ESP32-HUB75-MatrixPanel-I2S-DMA.h>

#include "config.h"

static MatrixPanel_I2S_DMA* panel = nullptr;

// Pixel setzen, mit optionalem Tausch der Panelhälften
static inline void putPixel(int x, int y, uint8_t r, uint8_t g, uint8_t b) {
  if (SWAP_HALVES) x = (x + PANEL_RES_X) % WIDTH;
  panel->drawPixelRGB888(x, y, r, g, b);
}

static void show() {
  if (HUB75_DOUBLE_BUFFER) panel->flipDMABuffer();
}

namespace display {

bool begin() {
  HUB75_I2S_CFG::i2s_pins pins = {R1_PIN, G1_PIN, B1_PIN, R2_PIN, G2_PIN, B2_PIN,
                                  A_PIN, B_PIN, C_PIN, D_PIN, E_PIN, LAT_PIN, OE_PIN, CLK_PIN};
  HUB75_I2S_CFG cfg(PANEL_RES_X, PANEL_RES_Y, PANEL_CHAIN, pins);
  cfg.clkphase = HUB75_CLKPHASE;
  if (HUB75_LATCH_BLANKING > 0) cfg.latch_blanking = HUB75_LATCH_BLANKING;
  cfg.double_buff = HUB75_DOUBLE_BUFFER;
  panel = new MatrixPanel_I2S_DMA(cfg);
  const bool ok = panel->begin();
  gpio_set_drive_capability((gpio_num_t)CLK_PIN, (gpio_drive_cap_t)HUB75_CLK_DRIVE);   // nach begin(), das die Pins einrichtet
  if (!ok) Serial.println("Panel-Init fehlgeschlagen (zu wenig DMA-Speicher?)");
  panel->setBrightness8(TEST_BRIGHTNESS);
  clear();
  return ok;
}

void clear() {
  panel->clearScreen();
  show();
}

void testPattern() {
  panel->setBrightness8(TEST_BRIGHTNESS);
  // 1. jedes Panel in einer Farbe: links rot, rechts grün -> Kettenreihenfolge
  panel->fillScreenRGB888(0, 0, 0);
  panel->fillRect(0, 0, PANEL_RES_X, PANEL_RES_Y, panel->color565(255, 0, 0));
  panel->fillRect(PANEL_RES_X, 0, PANEL_RES_X, PANEL_RES_Y, panel->color565(0, 255, 0));
  show();
  delay(1200);
  // 2. Rahmen und Diagonale: Geometrie, Fuge, Scan-Fehler (verschobene Zeilen)
  panel->fillScreenRGB888(0, 0, 0);
  panel->drawRect(0, 0, WIDTH, HEIGHT, panel->color565(255, 120, 0));
  panel->drawLine(0, 0, WIDTH - 1, HEIGHT - 1, panel->color565(70, 120, 230));
  show();
  delay(1200);
  // 3. Schrift: Orientierung (oben/unten, links/rechts)
  panel->fillScreenRGB888(0, 0, 0);
  panel->setTextColor(panel->color565(255, 120, 0));
  panel->setCursor(2, 2);
  panel->print("flightpanel");
  show();
  delay(1500);
  panel->fillScreenRGB888(0, 0, 0);
  show();
}

void showFrame(const uint8_t* frame) {
  panel->setBrightness8(min<uint8_t>(frame[1], MAX_BRIGHTNESS));
  const uint8_t* p = frame + FRAME_HEADER;
  for (int y = 0; y < HEIGHT; y++) {
    for (int x = 0; x < WIDTH; x++) {
      putPixel(x, y, p[0], p[1], p[2]);
      p += 3;
    }
  }
  show();
}

void markOffline() {
  // Letztes Bild bleibt stehen, nur ein dunkelroter Punkt unten rechts dazu
  putPixel(WIDTH - 1, HEIGHT - 1, 60, 0, 0);
  if (HUB75_DOUBLE_BUFFER) { show(); putPixel(WIDTH - 1, HEIGHT - 1, 60, 0, 0); }   // beide Puffer
}

void showLines(const char* const lines[], int count) {
  // Schrift der Bibliothek: 6 x 8 Pixel je Zeichen, also 21 Zeichen und 4 Zeilen auf 128 x 32. Nur ASCII.
  panel->setBrightness8(TEST_BRIGHTNESS);
  panel->fillScreenRGB888(0, 0, 0);
  panel->setTextWrap(false);
  panel->setTextSize(1);
  for (int i = 0; i < count && i < HEIGHT / 8; i++) {
    panel->setTextColor(i % 2 ? panel->color565(255, 255, 255) : panel->color565(255, 120, 0));
    panel->setCursor(1, i * 8);
    panel->print(lines[i]);
  }
  show();
}

}  // namespace display
