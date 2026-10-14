// Signierte Updates holen und installieren, neue Images bestätigen, Rettungsmodus (siehe updater.h).
#include "updater.h"

#if FW_UPDATES
#include <Arduino.h>
#include <HTTPClient.h>
#include <Preferences.h>
#include <Update.h>
#include <WiFiClientSecure.h>
#include <esp_attr.h>
#include <esp_ota_ops.h>
#include <esp_system.h>
#include <mbedtls/md.h>
#include <mbedtls/x509.h>

#include "config.h"
#include "fw_version.h"
#include "manifest.h"
#include "net.h"
#include "rescue.h"
#include "watchdog.h"

// Arduino-Kern (esp32-hal-misc.c, initArduino vor setup()): Mit true bleibt ein neues Image unbestätigt
// (PENDING_VERIFY), bis confirm() es nach dem ersten Bild bestätigt. Sonst bestätigte der Kern es sofort, und ein
// Image, das nie ein Bild holt, bliebe für immer. Gilt genauso für ein Image, das per ArduinoOTA kommt. Der
// vorgebaute Bootloader dieses Kerns kann das (CONFIG_BOOTLOADER_APP_ROLLBACK_ENABLE, docs/bauanleitung.md).
extern "C" bool verifyRollbackLater() { return true; }

namespace {

// ---- Versuche je Version (NVS) ----------------------------------------------------------------------------------
// Holt ein neues Image kein Bild, startet der Bootloader wieder das alte, und das fände dieselbe Version gleich
// wieder. Darum zählt das NVS die Versuche: höchstens UPDATE_TRIES_MAX je Version, im Abstand von UPDATE_CHECK_MS.
// Geschrieben wird nur vor dem Neustart in ein neues Image und wenn es sich bestätigt.
const char* const NVS_NS = "fwupdate";

struct Tries {
  char version[manifest::VERSION_LEN + 1];   // "" = kein offener Versuch
  uint8_t count;
};
Tries tries = {};

Tries loadTries() {
  Tries t = {};
  Preferences p;
  if (p.begin(NVS_NS, false)) {
    const String v = p.getString("version", "");
    if (manifest::isVersion(v.c_str())) {
      memcpy(t.version, v.c_str(), sizeof t.version);
      t.count = p.getUChar("count", 0);
    }
    p.end();
  }
  return t;
}

void saveTries(const char* version, uint8_t count) {
  Preferences p;
  if (!p.begin(NVS_NS, false)) return;
  p.putString("version", version);
  p.putUChar("count", count);
  p.end();
  snprintf(tries.version, sizeof tries.version, "%s", version);
  tries.count = count;
}

void clearTries() {
  Preferences p;
  if (p.begin(NVS_NS, false)) {
    p.clear();
    p.end();
  }
  tries = Tries();
}

// ---- Rettungs-Uhr (RTC-Speicher, grob im NVS) -------------------------------------------------------------------
// Wie lange das Panel das Zertifikat des Servers schon ablehnt, über Neustarts hinweg: Ohne Bilder startet es alle
// 10 min neu (RESTART_AFTER_MS), eine Uhr im RAM käme nie auf 24 h. RTC_NOINIT übersteht Neustart, Watchdog und
// Absturz, nicht das Ausschalten; dafür steht ihr Stand stündlich im NVS (rescue.h), samt dem letzten Rettungsversuch,
// und nach dem Einschalten zählt sie von dort weiter. Gezählt wird Laufzeit, keine Uhrzeit (das Panel kennt keine).
// Andere Fehler (WLAN, DNS, HTTP) halten die Uhr nicht an. Auf 0 geht sie, sobald das Zertifikat wieder durchgeht: mit
// einem Bild oder einer anderen Antwort über die geprüfte Verbindung.
const uint32_t CLOCK_MAGIC = 0x46505243;   // "FPRC"
const char* const RESCUE_NS = "fwrescue";  // eigener NVS-Namensraum: clearTries() leert "fwupdate" ganz
uint8_t savedSteps = 0;                    // Stand im NVS (rescue::steps)
uint8_t savedTry = rescue::UNKNOWN;        // Schritt des letzten Rettungsversuchs im NVS (rescue::restoreTry)

void loadSaved() {
  Preferences p;
  if (p.begin(RESCUE_NS, false)) {   // schreibbar: legt den Namensraum an (lesend: Fehler im Log, solange er fehlt)
    savedSteps = p.getUChar("steps", 0);
    savedTry = p.getUChar("try", rescue::UNKNOWN);
    p.end();
  }
}

// Ein Byte in RESCUE_NS; true, wenn es geschrieben ist
bool putSaved(const char* key, uint8_t value) {
  Preferences p;
  bool ok = false;
  if (p.begin(RESCUE_NS, false)) {
    ok = p.putUChar(key, value) == 1;
    p.end();
  }
  return ok;
}

// savedSteps folgt auch, wenn das Schreiben scheitert: kein neuer Versuch bei jedem Abruf, erst beim nächsten Schritt
void saveSteps(uint8_t s) {
  putSaved("steps", s);
  savedSteps = s;
}

// Vermerkt einen Rettungsversuch, bevor er beginnt; steht derselbe Schritt schon da, wird nichts geschrieben.
// false: nicht im NVS, dann kein Versuch (ein Einschalten erlaubte sonst gleich den nächsten)
bool saveRescueTry(uint8_t step) {
  if (savedTry == step) return true;
  if (!putSaved("try", step)) return false;
  savedTry = step;
  return true;
}

struct RescueClock {
  uint32_t magic;
  uint32_t check;
  uint64_t streakMs;    // so lange schon abgelehnt, Stand der letzten Beobachtung; 0 = nicht
  uint64_t lastTryMs;   // streakMs beim letzten Rettungsversuch; 0 = noch keiner
};
RTC_NOINIT_ATTR RescueClock rtcClock;

uint32_t clockCheck(const RescueClock& c) {
  return c.magic ^ 0xA5A5A5A5u ^ (uint32_t)c.streakMs ^ (uint32_t)(c.streakMs >> 32) ^ (uint32_t)c.lastTryMs ^
         (uint32_t)(c.lastTryMs >> 32);
}

void saveClock(uint64_t streakMs, uint64_t lastTryMs) {
  rtcClock.magic = CLOCK_MAGIC;
  rtcClock.streakMs = streakMs;
  rtcClock.lastTryMs = lastTryMs;
  rtcClock.check = clockCheck(rtcClock);
}

uint32_t streakT0 = 0;   // millis() der letzten Beobachtung in diesem Start (anfangs: begin())

// Wieder eine Ablehnung: Läuft die Uhr, zählt sie die Zeit seit der letzten Beobachtung dazu (beim ersten Mal in
// diesem Start: seit begin()). Sonst beginnt sie jetzt, mit 1 ms, denn 0 heißt "läuft nicht".
uint64_t streakTick(uint32_t now) {
  const uint64_t streak = rtcClock.streakMs ? rtcClock.streakMs + (uint32_t)(now - streakT0) : 1;
  streakT0 = now;
  saveClock(streak, rtcClock.lastTryMs);
  const uint8_t steps = rescue::steps(streak, RESCUE_AFTER_MS);
  if (steps > savedSteps) saveSteps(steps);   // ins NVS nur mit jedem vollen Schritt, höchstens 24-mal
  return streak;
}

void streakReset() {
  if (rtcClock.streakMs || rtcClock.lastTryMs) saveClock(0, 0);
  if (savedSteps) saveSteps(0);
  if (savedTry != rescue::NO_TRY) {   // einmal; scheitert es, bleibt im NVS der alte Vermerk, und der hält nur auf
    putSaved("try", rescue::NO_TRY);
    savedTry = rescue::NO_TRY;
  }
}

// ---- Laufendes Image --------------------------------------------------------------------------------------------
bool confirmed = false;     // dieses Image hat ein Bild geholt und ist bestätigt
bool confirmFailed = false; // das Bestätigen schlug schon fehl (steht einmal im Log, nicht bei jedem Bild)
bool checkDue = true;       // erste Prüfung mit dem ersten Bild
uint32_t nextCheck = 0;     // danach: millis() der nächsten

const char* stateText(esp_ota_img_states_t s) {
  switch (s) {
    case ESP_OTA_IMG_NEW: return "NEW";
    case ESP_OTA_IMG_PENDING_VERIFY: return "PENDING_VERIFY (unbestätigt: ohne Bild zurück zum alten Image)";
    case ESP_OTA_IMG_VALID: return "VALID";
    case ESP_OTA_IMG_INVALID: return "INVALID";
    case ESP_OTA_IMG_ABORTED: return "ABORTED";
    case ESP_OTA_IMG_UNDEFINED: return "UNDEFINED (per USB geflasht)";
  }
  return "?";
}

bool pendingVerify() {
  esp_ota_img_states_t st;
  return esp_ota_get_state_partition(esp_ota_get_running_partition(), &st) == ESP_OK &&
         st == ESP_OTA_IMG_PENDING_VERIFY;
}

// Erstes Bild: Dieses Image funktioniert. Ein unbestätigtes wird jetzt gültig, sonst startete der Bootloader beim
// nächsten Neustart wieder das alte. Erst danach darf ein Update kommen: Aus einem unbestätigten Image heraus
// könnte ein zweites Update am Ende kein startbares Image übrig lassen. Schlägt das Bestätigen fehl, bleibt es
// unbestätigt (ohne Update-Prüfung, siehe afterFrame), und das nächste Bild versucht es wieder.
void confirm() {
  if (pendingVerify()) {
    const esp_err_t err = esp_ota_mark_app_valid_cancel_rollback();
    if (err != ESP_OK) {
      if (!confirmFailed) Serial.printf("Firmware %s: Bestätigen fehlgeschlagen (%d), versucht es mit jedem Bild\n",
                                        FW_VERSION, (int)err);
      confirmFailed = true;
      return;
    }
    Serial.printf("Firmware %s bestätigt (erstes Bild)\n", FW_VERSION);
  }
  confirmed = true;
  if (strcmp(tries.version, FW_VERSION) == 0) clearTries();   // das Update, das gerade versucht wurde, läuft
}

// ---- Abruf ------------------------------------------------------------------------------------------------------
// Nimmt den Rumpf von firmware.txt auf (HTTPClient::writeToStream, mit Content-Length oder in Chunks). Ein Byte
// mehr als ein Manifest haben darf, nimmt sie noch, dann nichts mehr: zu lang, das sagt danach manifest::parse.
class TextSink : public Stream {
 public:
  TextSink(char* buf, size_t cap) : buf_(buf), cap_(cap) {}
  size_t write(const uint8_t* data, size_t n) override {
    watchdog::feed();
    const size_t take = n < cap_ - len_ ? n : cap_ - len_;
    memcpy(buf_ + len_, data, take);
    len_ += take;
    return take;
  }
  size_t write(uint8_t b) override { return write(&b, 1); }
  int available() override { return 0; }
  int read() override { return -1; }
  int peek() override { return -1; }
  size_t length() const { return len_; }

 private:
  char* buf_;
  size_t cap_;
  size_t len_ = 0;
};

// Schreibt das Image in den freien App-Slot (Update) und rechnet dabei SHA-256. Mehr Bytes als im Manifest, ein
// Schreibfehler oder UPDATE_DEADLINE_MS: write() gibt 0 zurück, writeToStream bricht ab. Jedes Paket füttert den
// Watchdog; bleibt der Server stumm, startet der ESP32 nach WDT_TIMEOUT_S mit dem alten Image neu.
class ImageSink : public Stream {
 public:
  ImageSink(uint32_t size, mbedtls_md_context_t* md) : size_(size), md_(md), t0_(millis()) {}
  size_t write(const uint8_t* data, size_t n) override {
    watchdog::feed();
    if (failed_ || n > size_ - got_ || millis() - t0_ > UPDATE_DEADLINE_MS) {
      failed_ = true;
      return 0;
    }
    if (mbedtls_md_update(md_, data, n) != 0 || Update.write(const_cast<uint8_t*>(data), n) != n) {
      failed_ = true;
      return 0;
    }
    const uint32_t before = got_;
    got_ += n;
    if (before / 262144 != got_ / 262144) Serial.printf("Update: %u von %u Byte\n", (unsigned)got_, (unsigned)size_);
    return n;
  }
  size_t write(uint8_t b) override { return write(&b, 1); }
  int available() override { return 0; }
  int read() override { return -1; }
  int peek() override { return -1; }
  uint32_t received() const { return got_; }
  bool failed() const { return failed_; }

 private:
  uint32_t size_;
  uint32_t got_ = 0;
  mbedtls_md_context_t* md_;
  uint32_t t0_;
  bool failed_ = false;
};

// Rumpf von firmware.txt lesen (Status 200 schon da) und prüfen: Format mit der Größe des freien App-Slots,
// dann die Signatur über die ersten drei Zeilen. true: m ist echt; ob es installiert wird, sagt wanted().
// Der Puffer ist statisch: Die Signaturprüfung läuft, solange er gebraucht wird, und braucht selbst Stack.
char manifestText[manifest::MAX_LEN + 1];

bool readManifest(HTTPClient& http, manifest::Manifest& m) {
  TextSink sink(manifestText, sizeof manifestText);
  const int got = http.writeToStream(&sink);
  if (got < 0 && sink.length() < sizeof manifestText) {
    Serial.printf("Update: Manifest nicht gelesen (%s)\n", HTTPClient::errorToString(got).c_str());
    return false;
  }
  const esp_partition_t* slot = esp_ota_get_next_update_partition(nullptr);
  const manifest::Error e = manifest::parse(manifestText, sink.length(), slot ? slot->size : 0, m);
  if (e != manifest::Error::None) {
    Serial.printf("Update: Manifest abgelehnt: %s\n", manifest::errorText(e));
    return false;
  }
  return fwsigVerify(reinterpret_cast<const uint8_t*>(manifestText), m.signedLen, m.sig, m.sigLen);   // meldet selbst
}

// Ein echtes Manifest: installieren? Nur eine neuere Version (nie zurück), und keine, die schon
// UPDATE_TRIES_MAX-mal nach dem Neustart kein Bild geholt hat.
bool wanted(const manifest::Manifest& m) {
  if (!manifest::isNewer(m.version, FW_VERSION)) {
    Serial.printf("Update: keins (Server %s, Panel %s)\n", m.version, FW_VERSION);
    return false;
  }
  if (strcmp(tries.version, m.version) == 0 && tries.count >= UPDATE_TRIES_MAX) {
    Serial.printf("Update: %s ist %u-mal zurückgerollt, erst eine neuere Version\n", m.version, (unsigned)tries.count);
    return false;
  }
  return true;
}

// Image aus der Antwort (Status 200 schon da) in den freien App-Slot. true: Größe und SHA-256 stimmen, Update.end()
// hat das Image geprüft (Kopf, Prüfsumme) und als Startpartition gesetzt. Sonst Update.abort(): Das erste Byte des
// Images fehlt dann im Slot, er ist nicht startbar, das laufende Image bleibt.
bool install(HTTPClient& http, const manifest::Manifest& m) {
  const int len = http.getSize();   // -1: in Chunks, dann zählt die Senke
  if (len != -1 && (uint32_t)len != m.size) {
    Serial.printf("Update: Image hat %d Byte, Manifest sagt %u\n", len, (unsigned)m.size);
    return false;
  }
  if (!Update.begin(m.size)) {
    Serial.printf("Update: Slot nicht bereit (%s)\n", Update.errorString());
    return false;
  }
  Serial.printf("Update: lade %s (%u Byte)\n", m.version, (unsigned)m.size);
  mbedtls_md_context_t md;
  mbedtls_md_init(&md);
  bool ok = mbedtls_md_setup(&md, mbedtls_md_info_from_type(MBEDTLS_MD_SHA256), 0) == 0 && mbedtls_md_starts(&md) == 0;
  ImageSink sink(m.size, &md);
  const int got = ok ? http.writeToStream(&sink) : -1;
  uint8_t digest[manifest::SHA256_LEN];
  ok = ok && !sink.failed() && got == (int)m.size && sink.received() == m.size && mbedtls_md_finish(&md, digest) == 0;
  mbedtls_md_free(&md);
  if (!ok) {
    Serial.printf("Update: abgebrochen nach %u von %u Byte (%s)\n", (unsigned)sink.received(), (unsigned)m.size,
                  Update.hasError() ? Update.errorString() : "Größe, Frist oder Verbindung");
    Update.abort();
    return false;
  }
  if (memcmp(digest, m.sha256, sizeof digest) != 0) {
    Serial.println("Update: SHA-256 stimmt nicht mit dem Manifest überein, abgebrochen");
    Update.abort();
    return false;
  }
  if (!Update.end()) {
    Serial.printf("Update: Image abgelehnt (%s)\n", Update.errorString());
    return false;
  }
  return true;
}

// Ins neue Image: Versuch zählen (vor dem Neustart, damit ein Rollback ihn findet), dann neu starten.
void restartInto(const manifest::Manifest& m) {
  const uint8_t count = strcmp(tries.version, m.version) == 0 ? (uint8_t)(tries.count + 1) : 1;
  saveTries(m.version, count);
  Serial.printf("Update %s installiert (Versuch %u), Neustart; es bestätigt sich mit dem ersten Bild\n", m.version,
                (unsigned)count);
  net::stop();
  delay(200);
  ESP.restart();
}

// Normaler Weg: über net, bei https samt Token, mit Zertifikatsprüfung; dieselbe Verbindung wie das Bild.
void checkForUpdate() {
  manifest::Manifest m;
  if (!net::beginRequest("/panel/firmware.txt")) {
    net::endRequest(false);
    return;
  }
  int code = net::get();
  if (code != HTTP_CODE_OK) {
    if (code == HTTP_CODE_NOT_FOUND) Serial.println("Update: kein Release auf dem Server");
    else if (code > 0) Serial.printf("Update: Manifest HTTP %d\n", code);
    net::endRequest(false);
    return;
  }
  const bool ok = readManifest(net::http(), m);
  net::endRequest(ok);   // ganz gelesen: Verbindung bleibt für das Image oder das nächste Bild
  if (!ok || !wanted(m)) return;

  char path[48];
  snprintf(path, sizeof path, "/panel/firmware.bin?v=%s", m.version);
  if (!net::beginRequest(path)) {
    net::endRequest(false);
    return;
  }
  code = net::get();
  if (code != HTTP_CODE_OK && code > 0) Serial.printf("Update: Image HTTP %d\n", code);
  const bool installed = code == HTTP_CODE_OK && install(net::http(), m);
  net::endRequest(false);
  if (installed) restartInto(m);
}

// Rettungsmodus: nur Manifest und Image, ohne Zertifikatsprüfung und ohne Token. Darum nicht net::beginRequest (das
// hängt das Token an), sondern begin() auf derselben Verbindung, die vorher geschlossen wird. Echt ist das Image
// dann allein durch Signatur, SHA-256 und steigende Version. Am Ende wird die ungeprüfte Verbindung geschlossen und
// die Prüfung wiederhergestellt, bevor irgendetwas sonst eine Verbindung aufbaut. setInsecure() steht nur hier
// (tools/check_firmware.sh prüft das). true: Image installiert, m beschreibt es.
bool rescueUpdate(manifest::Manifest& m) {
  WiFiClientSecure* tls = net::tls();
  if (tls == nullptr) return false;
  net::stop();
  tls->setInsecure();
  HTTPClient& http = net::http();
  bool installed = false;
  if (http.begin(net::client(), net::baseUrl() + "/panel/firmware.txt")) {
    int code = net::get();
    if (code != HTTP_CODE_OK && code > 0) Serial.printf("Rettung: Manifest HTTP %d\n", code);
    const bool ok = code == HTTP_CODE_OK && readManifest(http, m) && wanted(m);
    http.end();
    if (ok && http.begin(net::client(), net::baseUrl() + "/panel/firmware.bin?v=" + m.version)) {
      code = net::get();
      if (code != HTTP_CODE_OK && code > 0) Serial.printf("Rettung: Image HTTP %d\n", code);
      installed = code == HTTP_CODE_OK && install(http, m);
    }
  }
  net::stop();                           // die ungeprüfte Verbindung nie weiterverwenden
  tls->setCACert(net::trustedRoots());   // ab jetzt wieder mit Prüfung (setzt auch setInsecure zurück)
  return installed;
}

}  // namespace

namespace updater {

void begin() {
  // Rettungs-Uhr: nach dem Einschalten steht Zufall im RTC-Speicher, dann gilt der grobe Stand aus dem NVS, samt
  // letztem Rettungsversuch (der nächste also frühestens RESCUE_RETRY_MS danach, nicht gleich nach dem Start)
  loadSaved();
  const bool clockOk = esp_reset_reason() != ESP_RST_POWERON && rtcClock.magic == CLOCK_MAGIC &&
                       rtcClock.check == clockCheck(rtcClock);
  if (!clockOk) {
    saveClock(rescue::restore(savedSteps, RESCUE_AFTER_MS),
              rescue::restoreTry(savedTry, savedSteps, RESCUE_AFTER_MS));
  }
  streakT0 = millis();
  if (rtcClock.streakMs) {
    Serial.printf("Zertifikat des Servers seit %lu min abgelehnt, Rettungsmodus ab %lu min\n",
                  (unsigned long)(rtcClock.streakMs / 60000), (unsigned long)(RESCUE_AFTER_MS / 60000));
  }

  // Zustand des laufenden Images, für den Prüfstand: PENDING_VERIFY heißt, es bestätigt sich mit dem ersten Bild
  const esp_partition_t* running = esp_ota_get_running_partition();
  esp_ota_img_states_t st;
  if (running && esp_ota_get_state_partition(running, &st) == ESP_OK) {
    Serial.printf("Firmware %s in %s: %s\n", FW_VERSION, running->label, stateText(st));
  } else {
    Serial.printf("Firmware %s in %s: kein OTA-Zustand\n", FW_VERSION, running ? running->label : "?");
  }
  const esp_partition_t* invalid = esp_ota_get_last_invalid_partition();
  if (invalid) Serial.printf("Verworfenes Image in %s (Rollback)\n", invalid->label);

  // Ein offener Versuch mit einer anderen Version heißt: Das Update kam zurück. Nächster Versuch erst nach
  // UPDATE_CHECK_MS. Ein älterer Versuch (seitdem anders geflasht) gilt nicht mehr.
  tries = loadTries();
  if (tries.version[0] && !manifest::isNewer(tries.version, FW_VERSION)) {
    if (strcmp(tries.version, FW_VERSION) != 0) clearTries();
  } else if (tries.version[0]) {
    Serial.printf("Update auf %s kam zurück (Versuch %u von %d), läuft %s; nächster Versuch in %lu h\n",
                  tries.version, (unsigned)tries.count, UPDATE_TRIES_MAX, FW_VERSION,
                  (unsigned long)(UPDATE_CHECK_MS / 3600000UL));
    checkDue = false;
    nextCheck = millis() + UPDATE_CHECK_MS;
  }
}

void afterFrame(frame::Result r) {
  const uint32_t now = millis();
  if (r != frame::Result::Failed) {
    if (!confirmed) confirm();
    streakReset();
    if (!confirmed) return;   // Bestätigen fehlgeschlagen: kein Update aus einem unbestätigten Image
    if (checkDue || (int32_t)(now - nextCheck) >= 0) {
      checkDue = false;
      nextCheck = now + UPDATE_CHECK_MS;
      checkForUpdate();
    }
    return;
  }

  // Rettungsmodus: Nur ein abgelehntes Zertifikat des Servers zählt, kein Verbindungs- oder HTTP-Fehler. Kam eine
  // Antwort über die geprüfte Verbindung, nur eben kein Bild (etwa 403 oder 503), ging das Zertifikat durch.
  if (frame::answered()) {
    streakReset();
    return;
  }
  if (!net::isHttps() || net::tlsError(nullptr, 0) != MBEDTLS_ERR_X509_CERT_VERIFY_FAILED) return;
  const uint64_t streak = streakTick(now);
  if (!rescue::mayTry(streak, rtcClock.lastTryMs, RESCUE_AFTER_MS, RESCUE_RETRY_MS)) return;
  if (pendingVerify()) return;   // ein unbestätigtes Image rollt der Bootloader zurück; daraus kein Update
  saveClock(streak, streak);     // vor dem Versuch: Ein Absturz darin führt nicht gleich zum nächsten
  if (!saveRescueTry(rescue::steps(streak, RESCUE_AFTER_MS))) {   // ... und ein Einschalten auch nicht (NVS)
    Serial.printf("Rettungsmodus: Versuch nicht im NVS vermerkt, nächster in %lu min\n",
                  (unsigned long)(RESCUE_RETRY_MS / 60000));
    return;
  }
  Serial.printf("Rettungsmodus: Zertifikat seit %lu min abgelehnt, Update ohne Zertifikatsprüfung und ohne Token\n",
                (unsigned long)(streak / 60000));
  manifest::Manifest m;
  if (rescueUpdate(m)) restartInto(m);
}

}  // namespace updater
#endif
