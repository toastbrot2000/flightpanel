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
  python:3.12-slim sh -c '
    set -e
    apt-get update -qq && apt-get install -y -qq --no-install-recommends git >/dev/null
    pip install -q --root-user-action=ignore platformio==6.1.16
    mkdir /build && cp -r /src/. /build && cd /build
    rm -rf .pio
    [ -f include/secrets.h ] || cp include/secrets.h.example include/secrets.h
    pio run -e esp32dev
  '
