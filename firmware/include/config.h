#pragma once
// Hardware-, Anzeige- und Netz-Konfiguration. WLAN, Server und Token stehen im NVS (Einrichtungs-WLAN);
// include/secrets.h liefert auf dem Prüfstand nur Vorgaben dafür und das OTA-Passwort (defaults.h).

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
// Treiberstärke des CLK-Pins: schwach (0 = ca. 5 mA). Bei voller Stärke schwingt die Flanke auf Dupont-Kabeln
// nach, das Panel zählt doppelt, Zeilen rutschen um ein Pixel (Farbsäume cyan/magenta). Mit kurzen Leitungen
// oder Pegelwandler geht auch 2 (Vorgabe des ESP32, ca. 20 mA).
#define HUB75_CLK_DRIVE      0

// ---- Verhalten ------------------------------------------------------------
#define POLL_MS            2000     // Abstand der /panel/frame-Abrufe
#define HTTP_TIMEOUT_MS    3000     // Antwort: so lange darf zwischen zwei Zeilen oder Paketen Ruhe sein
#define HTTP_CONNECT_MS    5000     // TCP-Verbindungsaufbau
#define TLS_HANDSHAKE_S    15       // TLS-Handschlag (die Bibliothek wartet sonst 120 s)
#define FRAME_DEADLINE_MS  10000    // ganzes Bild empfangen; ein Server, der nur tröpfelt, ist ein Fehler
#define OFFLINE_MARK_MS    60000    // nach so lange ohne Frame: roter Punkt unten rechts
#define RESTART_AFTER_MS   600000   // nach so lange ohne Frame: Neustart
#define WDT_TIMEOUT_S      60       // Task-Watchdog auf loop(): hängt ein Durchlauf so lange, Neustart (OTA füttert mit)
#define WDT_TEST_HANG_MS   70000    // Prüfstand: "h" + Enter im seriellen Monitor lässt loop() so lange stehen; > WDT_TIMEOUT_S
#define TEST_PATTERN_AT_BOOT true   // Panels einfärben, Rahmen, Schrift; nur nach dem Einschalten, nicht nach Neustarts
#define TEST_BRIGHTNESS    40
// Helles Feld um den QR-Code des Einrichtungs-WLANs (wifiqr.h): Grau je Kanal (0-255), bei TEST_BRIGHTNESS. Mit
// vollem Weiß überstrahlen die hellen Pixel in der Handykamera die dunklen Module dazwischen (1 Pixel je Modul).
#define QR_WHITE           160

// ---- WLAN und Einrichtungs-WLAN (portal.cpp) ---------------------------------
#define WIFI_RETRY_MS      10000    // WLAN weg: so oft neu verbinden
#define OUTAGE_PORTAL_MS   300000   // so lange ohne WLAN: Einrichtungs-WLAN, nur WLAN-Felder (5 min)
#define PORTAL_CLOSE_MS    600000   // Einrichtungs-WLAN schließt nach 10 min; ohne Einrichtung bleibt es offen
#define PORTAL_RETRY_MS    30000    // im Ausfall-Portal: das gespeicherte WLAN so oft wieder versuchen
#define BOOT_BUTTON_PIN    0        // BOOT-Taste (GPIO0, gedrückt = LOW); BOOT_PIN gibt es in Arduino 3.x schon
#define BOOT_HOLD_MS       3000     // so lange halten: volles Einrichtungs-WLAN
#define BOOT_WINDOW_MS     6000     // nach dem Einschalten: bis dahin muss das Drücken beginnen (Testbild ~4 s)

// ---- Signierte Updates (nur mit include/fw_pubkey.h, updater.cpp) --------------------------------------------
#define UPDATE_CHECK_MS    21600000UL   // Update-Prüfung nach dem ersten Bild, dann alle 6 h
#define UPDATE_DEADLINE_MS 300000UL     // Image laden und schreiben: höchstens 5 min, sonst Abbruch
#define UPDATE_TRIES_MAX   3            // so oft eine Version installieren, die danach kein Bild holt (Rollback)
// Rettungsmodus: so lange lehnt das Panel das Zertifikat des Servers ab, bevor es Manifest und Image ohne Prüfung
// holt, und so lange wartet es danach bis zum nächsten Versuch. Für den Prüfstand kürzer bauen, etwa mit
// PLATFORMIO_BUILD_FLAGS="-DRESCUE_AFTER_MS=300000" pio run -t upload (5 min).
#ifndef RESCUE_AFTER_MS
#define RESCUE_AFTER_MS    86400000ULL  // 24 h
#endif
#ifndef RESCUE_RETRY_MS
#define RESCUE_RETRY_MS    3600000ULL   // 1 h
#endif

#define FRAME_HEADER       4
#define FRAME_LEN          (FRAME_HEADER + WIDTH * HEIGHT * 3)   // 12292
#define PROTO_VERSION      1
