#pragma once
// Einrichtungs-WLAN: Access Point flightpanel-XXXX (aus der MAC) mit WPA2-Passwort aus 8 Zufallsziffern, beides
// auf dem Panel, links davon ein QR-Code zum Beitreten per Handykamera (wifiqr.h). Jede Adresse führt per DNS und Umleitung auf eine Seite fürs Handy (nur Kern-Bibliotheken
// WebServer und DNSServer). Läuft nie neben einer TLS-Verbindung: run() schließt sie zuerst.
//
//   Full    BOOT nach dem Einschalten 3 s gehalten, oder noch nichts eingerichtet: WLAN und "Erweitert" mit
//           Server, Client-ID, Secret (nur überschreibbar, nie angezeigt) und Zurücksetzen.
//   Outage  nach OUTAGE_PORTAL_MS ohne WLAN: nur WLAN-Felder. Wer in Funkreichweite ist, während beim Freund
//           der Router neu startet, kann so weder den Server umbiegen noch das Token anfassen. Versucht weiter
//           das gespeicherte WLAN und schließt, sobald es wieder da ist.
//
// Nach dem Speichern startet der ESP32 neu. run() kehrt nur ohne Änderung zurück: Outage, wenn das WLAN wieder
// da ist oder nach PORTAL_CLOSE_MS; Full nach PORTAL_CLOSE_MS, wenn schon etwas eingerichtet ist (sonst nie).
struct Settings;

namespace portal {
enum class Mode { Full, Outage };
void run(Mode mode, const Settings& current);
}  // namespace portal
