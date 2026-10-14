#!/bin/sh
# Firmware prüfen, ohne PlatformIO lokal zu installieren (Docker):
#   1. setInsecure() (TLS ohne Zertifikatsprüfung) steht nur im Rettungsmodus, in einer Funktion mit "rescue" im Namen;
#   2. das Testmanifest firmware/test/data/golden_manifest.txt ist echt signiert (openssl, Testschlüssel);
#   3. Tests auf dem Rechner (pio test -e native): Manifest-Parser und Versionsvergleich, Rettungs-Uhr, WLAN-QR-Code;
#   4. Build ohne include/fw_pubkey.h (ohne signierte Updates) und mit (die Vorlage als Testschlüssel).
# Geflasht wird mit pio auf dem Rechner, an dem der ESP32 hängt.
# Toolchain und Bibliotheken landen im Docker-Volume flightpanel-pio (einmal ~1 GB).
# Gebaut wird, was im Ordner liegt: ohne include/secrets.h wie für Freunde (Einrichtungs-WLAN, kein OTA).
# Die Firmware bettet ../server/flightpanel/certs/roots.pem ein; der Container bekommt nur diese Datei dazu.
#
#   sh tools/check_firmware.sh
set -e
cd "$(dirname "$0")/.."

# 1. Nur eigene Quellen, ohne //-Kommentare. Funktionen beginnen hier in Spalte 0; jede Zeile mit setInsecure gehört
#    zur letzten Zeile davor, die in Spalte 0 eine Funktion beginnt.
insecure=$(find firmware/src firmware/include -type f \( -name '*.c' -o -name '*.cpp' -o -name '*.h' -o -name '*.hpp' \
  -o -name '*.ino' \) -exec awk '
    FNR == 1 { fn = "" }
    /^[A-Za-z_]/ && /[A-Za-z0-9_][ \t]*\(/ && !/;[ \t]*$/ { fn = $0 }
    { code = $0; sub(/\/\/.*/, "", code) }
    code ~ /setInsecure/ && tolower(fn) !~ /rescue[a-z0-9_]*[ \t]*\(/ { print FILENAME ":" FNR ": " $0 }
  ' {} +)
if [ -n "$insecure" ]; then
  echo "setInsecure() außerhalb des Rettungsmodus (erlaubt nur in einer Funktion mit rescue im Namen):" >&2
  echo "$insecure" >&2
  exit 1
fi

ROOT="$(pwd -W 2>/dev/null || pwd)"   # pwd -W: Git Bash unter Windows
MSYS_NO_PATHCONV=1 docker run --rm \
  -v "$ROOT/firmware:/src:ro" -v "$ROOT/server/flightpanel/certs:/certs:ro" -v flightpanel-pio:/root/.platformio \
  python:3.12-slim@sha256:a6e34c598f2467ed0e9a8d349809fcd8b5c603269512df273a0bb1784edc11b1 sh -c '
    set -e
    apt-get update -qq && DEBIAN_FRONTEND=noninteractive apt-get install -y -qq --no-install-recommends git g++ >/dev/null
    pip install -q --root-user-action=ignore --require-hashes -r /src/platformio.lock
    mkdir -p /build/firmware /build/server/flightpanel/certs
    cp -r /src/. /build/firmware && cp /certs/roots.pem /build/server/flightpanel/certs/
    cd /build/firmware
    rm -rf .pio
    echo "== Testmanifest: Signatur über die ersten drei Zeilen (openssl)"
    head -n 3 test/data/golden_manifest.txt > /tmp/signed.txt
    tail -n 1 test/data/golden_manifest.txt | tr -d "\n" | openssl base64 -d -A > /tmp/sig.der
    openssl dgst -sha256 -verify test/data/test_pubkey.pem -signature /tmp/sig.der /tmp/signed.txt

    echo "== Tests auf dem Rechner (env:native)"
    pio test -e native

    echo "== Build ohne include/fw_pubkey.h: keine signierten Updates"
    rm -f include/fw_pubkey.h
    pio run -e esp32dev

    echo "== Build mit include/fw_pubkey.h (Vorlage): Update-Prüfung muss mitgebaut und gelinkt werden"
    cp include/fw_pubkey.h.example include/fw_pubkey.h
    PLATFORMIO_BUILD_FLAGS=-Wl,--require-defined=fwsigVerify pio run -e esp32dev
  '
