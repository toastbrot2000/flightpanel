#pragma once
// Die eine Verbindung zum Server: für das Bild (frame.cpp) und für alles, was später dazukommt (Updates).
//
// Es gibt genau einen HTTPClient und genau einen Client darunter, beide global, damit Keep-alive hält (lokale
// Objekte schließen den Socket). Bei https ist das ein WiFiClientSecure mit der eingebetteten Vertrauensliste
// (server/flightpanel/certs/roots.pem) und TLS-Handschlag höchstens TLS_HANDSHAKE_S; bei http ein WiFiClient,
// und der TLS-Code läuft nie. Die Access-Header (CF-Access-Client-Id/-Secret) gehen nur über https hinaus,
// nie über http, damit eine geänderte oder herabgestufte Adresse das Secret nicht verrät. Umleitungen folgt
// der Client nicht. Nur eine TLS-Verbindung zur Zeit; stop() vor dem Einrichtungs-WLAN gibt ihren Speicher frei.
//
// Ablauf einer Anfrage:
//   if (net::beginRequest("/panel/frame")) {            // begin() samt Access-Headern (nur https)
//     net::http().addHeader(...);                       // eigene Header nach beginRequest, jedes Mal neu
//     const int code = net::get();                      // GET; meldet neue Verbindungen samt Speicher
//     ... net::http().getSize(), writeToStream(...) ...
//     net::endRequest(ok);                               // ok: Verbindung offen lassen, sonst schließen
//   }
#include <Arduino.h>
#include <HTTPClient.h>
#include <WiFiClient.h>
#include <WiFiClientSecure.h>

struct Settings;

namespace net {
void begin(const Settings& s);       // einmal nach dem Laden der Einstellungen
const String& baseUrl();             // ohne / am Ende, z. B. https://flightpanel.example.com
bool isHttps();
WiFiClient& client();                // der gemeinsame Client (bei https der WiFiClientSecure)
WiFiClientSecure* tls();             // der gemeinsame TLS-Client, nullptr bei http
HTTPClient& http();                  // der gemeinsame HTTPClient (Keep-alive)
const char* trustedRoots();          // eingebettete Vertrauensliste (PEM), für setCACert

bool beginRequest(const char* path); // http().begin(client(), baseUrl() + path) und addAccessHeaders()
void addAccessHeaders();             // nur bei https und gesetztem Token; nach jedem begin() nötig
int get();                           // http().GET(), protokolliert Verbindungsaufbau und Fehler
void endRequest(bool keepAlive);     // http().end(); keepAlive = false schließt die Verbindung
void stop();                         // Verbindung schließen, TLS-Puffer frei
bool connected();                    // offene Keep-alive-Verbindung?
int tlsError(char* msg, size_t len); // mbedTLS-Fehler des letzten Verbindungsaufbaus (< 0), 0 = keiner
void logHeap(const char* when);      // freier Heap, größter Block, Minimum seit dem Start
}  // namespace net
