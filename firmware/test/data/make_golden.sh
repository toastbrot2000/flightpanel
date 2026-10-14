#!/bin/sh
# Testdaten für test/test_manifest neu erzeugen: golden_manifest.txt (echt signiert) und test_pubkey.pem.
#
#   sh firmware/test/data/make_golden.sh
#
# Läuft in einem Wegwerf-Container mit openssl (dasselbe feste Image wie tools/check_firmware.sh). Der private
# Testschlüssel entsteht dort und verschwindet mit dem Container; heraus kommen nur der öffentliche Schlüssel und das
# signierte Manifest. Schreiben und Signieren mit denselben openssl-Befehlen wie tools/fw_release.sh, damit der Test
# dasselbe Format prüft, das ein Release hat.
#
# Das Manifest beschreibt 1 MiB Null-Bytes (head -c 1048576 /dev/zero): Größe und SHA-256 lassen sich nachrechnen.
# Ein Panel mit dem echten Schlüssel lehnt es ab (fremde Signatur), eines mit dem Testschlüssel auch (alte Version).
set -e
cd "$(dirname "$0")"
IMAGE=$(grep -o 'python:3.12-slim@sha256:[0-9a-f]*' ../../../tools/check_firmware.sh)   # eine Quelle (tools/escrow.py)
OUT="$(pwd -W 2>/dev/null || pwd)"   # pwd -W: Git Bash unter Windows
MSYS_NO_PATHCONV=1 docker run --rm -v "$OUT:/out" "$IMAGE" sh -c '
    set -e
    cd /tmp
    openssl genpkey -algorithm EC -pkeyopt ec_paramgen_curve:P-256 -pkeyopt ec_param_enc:named_curve -out key.pem
    openssl pkey -in key.pem -pubout -out pub.pem
    head -c 1048576 /dev/zero > image.bin
    printf "%s\n%s\n%s\n" 2610140000 "$(wc -c < image.bin)" "$(sha256sum image.bin | cut -d " " -f 1)" > signed.txt
    openssl dgst -sha256 -sign key.pem -out sig.der signed.txt
    rm key.pem
    openssl dgst -sha256 -verify pub.pem -signature sig.der signed.txt
    { cat signed.txt; printf "%s\n" "$(openssl base64 -A -in sig.der)"; } > manifest.txt
    cp pub.pem /out/test_pubkey.pem
    cp manifest.txt /out/golden_manifest.txt
  '
cat golden_manifest.txt
