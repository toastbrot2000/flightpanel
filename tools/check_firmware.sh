#!/bin/sh
# Firmware kompilieren, ohne PlatformIO lokal zu installieren (Docker).
# Prüft nur, ob sie baut; geflasht wird mit pio auf dem Rechner, an dem der ESP32 hängt.
# Toolchain und Bibliotheken landen im Docker-Volume flightpanel-pio (einmal ~1 GB).
#
#   sh tools/check_firmware.sh
set -e
cd "$(dirname "$0")/.."
SRC="$(pwd -W 2>/dev/null || pwd)/firmware"   # pwd -W: Git Bash unter Windows
MSYS_NO_PATHCONV=1 docker run --rm \
  -v "$SRC:/src:ro" -v flightpanel-pio:/root/.platformio \
  python:3.12-slim@sha256:a6e34c598f2467ed0e9a8d349809fcd8b5c603269512df273a0bb1784edc11b1 sh -c '
    set -e
    apt-get update -qq && apt-get install -y -qq --no-install-recommends git >/dev/null
    pip install -q --root-user-action=ignore --require-hashes -r /src/platformio.lock
    mkdir /build && cp -r /src/. /build && cd /build
    rm -rf .pio
    [ -f include/secrets.h ] || cp include/secrets.h.example include/secrets.h
    pio run -e esp32dev
  '
