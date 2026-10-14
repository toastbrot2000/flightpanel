#pragma once
// Signierte Firmware-Updates: eingebaut, wenn include/fw_pubkey.h existiert (nicht im Repo; Vorlage und Anleitung in
// include/fw_pubkey.h.example). Ohne die Datei baut die Firmware ohne Updates und FW_UPDATES ist 0.

#include <stddef.h>
#include <stdint.h>

#if defined(__has_include)
#if __has_include("fw_pubkey.h")
#define FW_UPDATES 1
#endif
#endif
#ifndef FW_UPDATES
#define FW_UPDATES 0
#endif

#if FW_UPDATES
// true, wenn sig eine gültige ECDSA-P-256-Signatur (SHA-256, DER) über msg[0, msgLen) ist, gemacht mit dem privaten
// Schlüssel zu FW_PUBKEY_PEM. Für das Manifest (include/manifest.h, nach erfolgreichem manifest::parse):
//   fwsigVerify((const uint8_t*)text, m.signedLen, m.sig, m.sigLen)
// Den Grund einer Ablehnung meldet die Funktion seriell. C-Name, damit tools/check_firmware.sh und fw_release.sh sie
// mit -Wl,--require-defined=fwsigVerify in den Build zwingen können, auch solange sie noch niemand aufruft.
extern "C" bool fwsigVerify(const uint8_t* msg, size_t msgLen, const uint8_t* sig, size_t sigLen);
#endif
