// Bild vom Server holen (siehe frame.h). Frame-Format: [Version, Helligkeit, Breite, Höhe] + 128 x 32 x 3 RGB.
#include "frame.h"

#include <Arduino.h>
#include <WiFi.h>
#include <esp_timer.h>

#include "config.h"
#include "fw_version.h"
#include "net.h"
#include "watchdog.h"

static char etag[48] = "";   // ETag des angezeigten Bildes, wie der Server es schickt (mit Anführungszeichen)

// Nimmt den Rumpf auf, den HTTPClient::writeToStream liefert, ob mit Content-Length oder in Chunks. Mehr als
// passt nimmt sie nicht: write() gibt dann 0 zurück, writeToStream bricht mit Fehler ab. Jedes Paket füttert den
// Watchdog; wer nach FRAME_DEADLINE_MS noch schickt, ist zu langsam (ein Server, der tröpfelt).
class FrameSink : public Stream {
 public:
  FrameSink(uint8_t* buf, size_t cap) : buf_(buf), cap_(cap), t0_(millis()) {}
  size_t write(const uint8_t* data, size_t n) override {
    watchdog::feed();
    if (n > cap_ - len_ || millis() - t0_ > FRAME_DEADLINE_MS) return 0;
    memcpy(buf_ + len_, data, n);
    len_ += n;
    return n;
  }
  size_t write(uint8_t b) override { return write(&b, 1); }
  int available() override { return 0; }
  int read() override { return -1; }
  int peek() override { return -1; }
  size_t length() const { return len_; }

 private:
  uint8_t* buf_;
  size_t cap_;
  size_t len_ = 0;
  uint32_t t0_;
};

// Ein ETag geht beim nächsten Abruf als Header zurück: nur druckbares ASCII ohne Leerzeichen, nie CR/LF
static void keepEtag(const String& tag) {
  bool ok = tag.length() > 0 && tag.length() < sizeof(etag);
  for (size_t i = 0; ok && i < tag.length(); i++) {
    const unsigned char c = tag[i];
    ok = c > ' ' && c <= '~';
  }
  if (ok) memcpy(etag, tag.c_str(), tag.length() + 1);
  else etag[0] = '\0';
}

namespace frame {

void forgetEtag() { etag[0] = '\0'; }

Result fetch(uint8_t* buf) {
  if (WiFi.status() != WL_CONNECTED) return Result::Failed;
  if (!net::beginRequest("/panel/frame")) {
    net::endRequest(false);
    return Result::Failed;
  }
  HTTPClient& http = net::http();
  static const char* keys[] = {"ETag"};
  http.collectHeaders(keys, 1);   // jedes Mal: ein anderer Aufrufer kann eine eigene Liste gesetzt haben
  if (etag[0]) http.addHeader("If-None-Match", etag);
  // Diagnose: Firmware, Laufzeit in s, Empfang in dBm, kleinster freier Heap seit dem Start. Der Server liest
  // daraus nur vier begrenzte Zahlen.
  char diag[80];
  snprintf(diag, sizeof diag, "fw=%s up=%lu rssi=%d heap=%u", FW_VERSION,
           (unsigned long)(esp_timer_get_time() / 1000000), (int)WiFi.RSSI(), (unsigned)ESP.getMinFreeHeap());
  http.addHeader("X-Panel", diag);

  const int code = net::get();
  if (code == HTTP_CODE_NOT_MODIFIED && etag[0]) {   // ohne Rumpf: die Verbindung bleibt brauchbar
    net::endRequest(true);
    return Result::Same;
  }
  if (code != HTTP_CODE_OK) {
    // Fehler beim Verbinden hat net::get() schon gemeldet. Zu 421 und 404 steht dabei, was zu tun ist.
    if (code == 421) Serial.println("HTTP 421: Server kennt diesen Namen nicht (LAN_HOSTS in .env des Servers)");
    else if (code == HTTP_CODE_NOT_FOUND) Serial.println("HTTP 404: kein /panel/frame hier (Server zu alt?)");
    else if (code > 0) Serial.printf("HTTP %d\n", code);
    net::endRequest(false);
    return Result::Failed;
  }
  const int size = http.getSize();   // -1: ohne Content-Length (Chunks)
  if (size != -1 && size != FRAME_LEN) {
    Serial.printf("Frame: falsche Länge %d (erwartet %d)\n", size, FRAME_LEN);
    net::endRequest(false);
    return Result::Failed;
  }
  FrameSink sink(buf, FRAME_LEN);
  const int got = http.writeToStream(&sink);
  const bool ok = got == FRAME_LEN && sink.length() == FRAME_LEN && buf[0] == PROTO_VERSION && buf[2] == WIDTH &&
                  buf[3] == HEIGHT;
  if (!ok) {
    Serial.printf("Frame ungültig: %d/%u Byte, Version %u, %ux%u\n", got, (unsigned)sink.length(), buf[0], buf[2],
                  buf[3]);
    net::endRequest(false);
    return Result::Failed;
  }
  keepEtag(http.header("ETag"));
  net::endRequest(true);
  return Result::Fresh;
}

}  // namespace frame
