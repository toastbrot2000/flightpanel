// Signatur eines Firmware-Manifests prüfen: ECDSA P-256 über SHA-256, mit dem eingebetteten öffentlichen Schlüssel
// aus include/fw_pubkey.h. Ohne die Datei ist FW_UPDATES 0 und hier wird nur FW_VERSION geprüft.
//
// mbedTLS gehört zum Arduino-Kern. Verwendet werden nur Aufrufe, die es auch in mbedTLS 3 (Arduino 3.x) gleich gibt:
// mbedtls_md, mbedtls_pk_parse_public_key, mbedtls_pk_can_do, mbedtls_pk_get_bitlen, mbedtls_pk_verify.

#include "fwsig.h"

#include "manifest.h"

// FW_VERSION setzt scripts/version.py (Bauzeit JJMMTTHHMM in UTC). Ohne gültige Version gäbe es keinen Vergleich mit
// dem Manifest, also lieber gar keinen Build.
#ifndef FW_VERSION
#error "FW_VERSION fehlt: scripts/version.py gehört in extra_scripts der Umgebung (platformio.ini)"
#endif
static_assert(manifest::isVersion(FW_VERSION), "FW_VERSION muss genau 10 Ziffern haben (JJMMTTHHMM)");

#if FW_UPDATES
#include <Arduino.h>
#include <mbedtls/md.h>
#include <mbedtls/pk.h>

#include "fw_pubkey.h"

namespace {

constexpr bool startsWith(const char* s, const char* p) {
  return *p == '\0' || (*s == *p && startsWith(s + 1, p + 1));
}

constexpr bool contains(const char* s, const char* p) {
  return startsWith(s, p) || (*s != '\0' && contains(s + 1, p));
}

}  // namespace

// Die Firmware ist öffentlich (der Rettungskanal liefert firmware.bin ohne Anmeldung): Ein versehentlich eingefügter
// privater Schlüssel stünde in jedem Image. Darum bricht schon der Build ab.
static_assert(!contains(FW_PUBKEY_PEM, "PRIVATE"),
              "fw_pubkey.h enthält einen PRIVATEN Schlüssel. Nur den öffentlichen einfügen (openssl pkey -pubout)!");
static_assert(contains(FW_PUBKEY_PEM, "-----BEGIN PUBLIC KEY-----") && contains(FW_PUBKEY_PEM, "-----END PUBLIC KEY-----"),
              "fw_pubkey.h: öffentlicher Schlüssel im PEM-Format erwartet (BEGIN/END PUBLIC KEY)");

extern "C" bool fwsigVerify(const uint8_t* msg, size_t msgLen, const uint8_t* sig, size_t sigLen) {
  if (msg == nullptr || sig == nullptr || sigLen == 0) return false;
  uint8_t hash[32];
  const mbedtls_md_info_t* md = mbedtls_md_info_from_type(MBEDTLS_MD_SHA256);
  if (md == nullptr || mbedtls_md(md, msg, msgLen, hash) != 0) {
    Serial.println("Update: SHA-256 nicht verfügbar");
    return false;
  }

  mbedtls_pk_context pk;
  mbedtls_pk_init(&pk);
  bool ok = false;
  // PEM: die Länge zählt die abschließende Null mit, so verlangt es mbedTLS
  int rc = mbedtls_pk_parse_public_key(&pk, reinterpret_cast<const unsigned char*>(FW_PUBKEY_PEM),
                                       sizeof(FW_PUBKEY_PEM));
  if (rc != 0) {
    Serial.printf("Update: Schlüssel in fw_pubkey.h nicht lesbar (mbedTLS -0x%04x)\n", (unsigned)-rc);
  } else if (!mbedtls_pk_can_do(&pk, MBEDTLS_PK_ECDSA) || mbedtls_pk_get_bitlen(&pk) != 256) {
    Serial.println("Update: Schlüssel in fw_pubkey.h ist kein P-256-Schlüssel");
  } else {
    rc = mbedtls_pk_verify(&pk, MBEDTLS_MD_SHA256, hash, sizeof hash, sig, sigLen);
    ok = (rc == 0);
    if (!ok) Serial.printf("Update: Signatur ungültig (mbedTLS -0x%04x)\n", (unsigned)-rc);
  }
  mbedtls_pk_free(&pk);
  return ok;
}
#endif
