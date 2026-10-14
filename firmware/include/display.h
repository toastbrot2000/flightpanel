#pragma once
// Die beiden HUB75-Panels (ESP32-HUB75-MatrixPanel-DMA, Doppelpuffer). Die Bibliothek frischt per DMA im
// Hintergrund auf; gezeichnet wird in den hinteren Puffer, show() tauscht.
#include <stdint.h>

namespace display {
bool begin();                              // false, wenn der DMA-Speicher nicht reicht
void clear();
void testPattern();                        // Kettenfolge, Geometrie, Orientierung; nur nach dem Einschalten
void showFrame(const uint8_t* frame);      // Kopf schon geprüft: [Version, Helligkeit, Breite, Höhe] + RGB
void markOffline();                        // roter Punkt unten rechts auf dem letzten Bild
void showLines(const char* const lines[], int count);   // bis zu 4 Zeilen Text, z. B. Name und Passwort
}  // namespace display
