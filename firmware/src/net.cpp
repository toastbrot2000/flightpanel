// Die eine Verbindung zum Server (siehe net.h).
#include "net.h"

#include <WiFi.h>
#include <esp_heap_caps.h>

#include "config.h"
#include "roots_pem.h"   // ROOTS_PEM: erzeugt von embed_roots.py aus server/flightpanel/certs/roots.pem
#include "settings.h"
#include "watchdog.h"
static_assert(TLS_HANDSHAKE_S <= 15, "TLS-Handschlag höchstens 15 s, sonst reicht WDT_TIMEOUT_S nicht für alles");
// Der mbedTLS-Fehler des letzten Aufbaus bleibt stehen, wenn schon DNS scheitert: vor jedem Aufbau auf 0 (tlsError).
#if ESP_ARDUINO_VERSION_MAJOR >= 3   // Arduino 3.x: Er steht im TLS-Kontext von NetworkClientSecure.
class TlsClient : public WiFiClientSecure { public: void clearError() { sslclient->last_error = 0; } };
#else
class TlsClient : public WiFiClientSecure {
 public:
  void clearError() { _lastError = 0; }
};
#endif
static WiFiClient plain;
static TlsClient* secure = nullptr;   // erst bei https angelegt
static HTTPClient httpc;
static String base;
static bool https = false;
static String clientId, clientSecret;   // nur bei https gefüllt

static void logConnectError(int code) {
  char msg[96];
  const int err = net::tlsError(msg, sizeof msg);
  if (err == -1) Serial.println("Verbindung fehlgeschlagen: TCP-Aufbau oder TLS-Handschlag, Zeit um oder abgelehnt");
  else if (err) Serial.printf("Verbindung fehlgeschlagen: TLS-Fehler %d (-0x%04X) %s\n", err, -err, msg);
  else Serial.printf("Verbindung fehlgeschlagen: %s (%d)\n", HTTPClient::errorToString(code).c_str(), code);
}

namespace net {

void begin(const Settings& s) {
  base = s.server;
  https = base.startsWith("https://");
  if (https) {
    clientId = s.clientId;
    clientSecret = s.clientSecret;
    if (!secure) secure = new TlsClient();
    secure->setCACert(ROOTS_PEM);
    secure->setHandshakeTimeout(TLS_HANDSHAKE_S);
    // Kopfzeilen und Chunk-Längen liest HTTPClient mit readStringUntil; dessen Frist ist bei WiFiClientSecure
    // sonst 1 s, weil setTimeout() dort nur den Socket betrifft.
    secure->Stream::setTimeout(HTTP_TIMEOUT_MS);
  } else {
    clientId = "";
    clientSecret = "";
  }
  httpc.setReuse(true);
  httpc.setTimeout(HTTP_TIMEOUT_MS);
  httpc.setConnectTimeout(HTTP_CONNECT_MS);
  httpc.setFollowRedirects(HTTPC_DISABLE_FOLLOW_REDIRECTS);   // nie mit Token zu einem anderen Ziel
  httpc.setUserAgent("flightpanel-esp32");

  // Adresse und Token stehen nie im Log, nur was eingerichtet ist
  const bool token = clientId.length() && clientSecret.length();
  if (base.isEmpty()) Serial.println("Server: keiner eingerichtet");
  else if (https) Serial.printf("Server: https, %s\n", token ? "mit Access-Token" : "ohne Access-Token");
  else Serial.println("Server: http");
  if (!https && s.clientId.length()) Serial.println("Access-Token gespeichert, geht über http aber nie hinaus");
}

const String& baseUrl() { return base; }
bool isHttps() { return https; }
WiFiClient& client() { return https ? static_cast<WiFiClient&>(*secure) : plain; }
WiFiClientSecure* tls() { return https ? secure : nullptr; }
HTTPClient& http() { return httpc; }
const char* trustedRoots() { return ROOTS_PEM; }

bool beginRequest(const char* path) {
  if (base.isEmpty()) return false;
  if (!httpc.begin(client(), base + path)) return false;
  addAccessHeaders();
  return true;
}

void addAccessHeaders() {
  if (!https || clientId.isEmpty() || clientSecret.isEmpty()) return;
  httpc.addHeader("CF-Access-Client-Id", clientId);
  httpc.addHeader("CF-Access-Client-Secret", clientSecret);
}

int get() {
  watchdog::feed();
  const bool reused = connected();
  if (!reused && https) secure->clearError();
  const uint32_t t0 = millis();
  const int code = httpc.GET();   // DNS, TCP, TLS-Handschlag und Kopfzeilen: zusammen unter WDT_TIMEOUT_S
  watchdog::feed();
  if (!reused) {
    if (code > 0) {
      char what[48];
      snprintf(what, sizeof what, "%s verbunden, Antwort nach %lu ms", https ? "TLS" : "HTTP",
               (unsigned long)(millis() - t0));
      logHeap(what);
    } else {
      logConnectError(code);
    }
  } else if (code < 0) {
    Serial.printf("HTTP-Fehler: %s (%d)\n", HTTPClient::errorToString(code).c_str(), code);
  }
  return code;
}

void endRequest(bool keepAlive) {
  // Ist die Antwort nicht ganz gelesen, läse die nächste Anfrage ihre Reste: dann Verbindung zu
  if (!keepAlive) client().stop();
  httpc.end();
}

void stop() { endRequest(false); }

bool connected() { return https ? secure->connected() : plain.connected(); }

int tlsError(char* msg, size_t len) {
  if (msg && len) msg[0] = '\0';
  if (!https) return 0;
  char buf[96];
  const int err = secure->lastError(buf, sizeof buf);   // >= 0: kein Fehler (nach Erfolg die Socket-Nummer)
  if (err >= 0) return 0;
  if (msg && len) snprintf(msg, len, "%s", buf);
  return err;
}

void logHeap(const char* when) {
  Serial.printf("%s: Heap frei %u, größter Block %u, Minimum %u\n", when, (unsigned)ESP.getFreeHeap(),
                (unsigned)heap_caps_get_largest_free_block(MALLOC_CAP_8BIT), (unsigned)ESP.getMinFreeHeap());
}

}  // namespace net
