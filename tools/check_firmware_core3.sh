#!/bin/sh
# Freiwillige Variante mit Arduino-ESP32 3.x (env:esp32dev_core3) prüfen und bauen; Bauanleitung, Abschnitt 4.
# In Docker wie tools/check_firmware.sh, aber im eigenen Volume flightpanel-pio-core3 (beim ersten Mal etwa 1 GB):
# Das Werkzeug für 2.0.17 im Volume flightpanel-pio bleibt unberührt, tools/check_firmware.sh baut weiter nur 2.0.17.
#
#   sh tools/check_firmware_core3.sh
#
#   1. Python-Werkzeug (PlatformIO-Fork pioarduino, esptool ...) nur aus firmware/platformio-core3.lock, mit
#      Prüfsummen. Die Plattform installiert sonst bei jedem Build ungeprüft die neuesten Versionen nach (ihr
#      builder/penv_setup.py); das unterbinden PLATFORMIO_OFFLINE=1 und, für uv, UV_OFFLINE=1.
#   2. Plattform, Framework und Werkzeug-Pakete nur mit der SHA-256 aus firmware/platformio-core3.sha256 (siehe dort).
#   3. Alles installieren, ohne zu bauen (pio pkg install), dann prüfen, bevor irgendetwas davon baut: Zugelassen ist
#      nur, was eine der Regeln bei check_packages trifft, alles andere bricht ab, auch eine unbekannte Quelle.
#   4. Build ohne include/fw_pubkey.h und mit der Vorlage (Signaturprüfung mit mbedTLS 3 muss bauen und linken),
#      danach dieselbe Prüfung noch einmal.
#   5. Build wie im Ordner (mit include/secrets.h und fw_pubkey.h, falls vorhanden). Images, espota.py von
#      Arduino 3.x (OTA auf ein Panel mit 3.x) und der esptool-Aufruf zum Flashen landen in firmware/.pio/core3/.
#
# Ohne eigene Prüfsumme bleiben wie bei 2.0.17: git aus Debian (signiert), die Bibliotheken aus platformio.ini (HUB75
# per Git-Commit, Adafruit mit der Prüfsumme der PlatformIO-Registry). Flashen per USB macht pio auf dem Rechner mit dem
# ESP32 nicht selbst (siehe Bauanleitung): Ein lokales pio würde die Plattform ungeprüft laden.
set -e

if [ "$1" != "--im-container" ]; then
  cd "$(dirname "$0")/.."
  IMAGE=$(grep -o 'python:3.12-slim@sha256:[0-9a-f]*' tools/check_firmware.sh)   # eine Quelle, tools/escrow.py pflegt sie
  ROOT="$(pwd -W 2>/dev/null || pwd)"   # pwd -W: Git Bash unter Windows
  mkdir -p firmware/.pio/core3
  MSYS_NO_PATHCONV=1 docker run --rm \
    -v "$ROOT/firmware:/src:ro" -v "$ROOT/server/flightpanel/certs:/certs:ro" -v "$ROOT/firmware/.pio/core3:/out" \
    -v "$ROOT/tools/check_firmware_core3.sh:/check.sh:ro" -v flightpanel-pio-core3:/root/.platformio \
    "$IMAGE" sh /check.sh --im-container
  exit
fi

# ---- ab hier im Container ----
rm -f /out/*   # nie Images eines früheren Laufs liegen lassen, wenn dieser scheitert
apt-get update -qq && DEBIAN_FRONTEND=noninteractive apt-get install -y -qq --no-install-recommends git >/dev/null
PIO=/root/.platformio
LIST=/src/platformio-core3.sha256

echo "== 1. Python-Werkzeug aus platformio-core3.lock (Prüfsummen)"
if ! cmp -s /src/platformio-core3.lock "$PIO/penv/flightpanel.lock" 2>/dev/null; then
  rm -rf "$PIO/penv"   # dort sucht die Plattform ihr Python; neu, sobald sich das Lock ändert
  python -m venv "$PIO/penv"
  "$PIO/penv/bin/pip" install -q --disable-pip-version-check --require-hashes -r /src/platformio-core3.lock
  cp /src/platformio-core3.lock "$PIO/penv/flightpanel.lock"
fi
# esptool installiert die Plattform per uv aus ihrem Paket: ohne Netz und mit setuptools aus dem Lock
export PATH="$PIO/penv/bin:$PATH" PLATFORMIO_OFFLINE=1 UV_OFFLINE=1 UV_NO_BUILD_ISOLATION=1

echo "== 2. Pakete aus platformio-core3.sha256 prüfen und in den Download-Cache von PlatformIO legen"
platform=$(sed -n '/^\[env:esp32dev_core3\]/,/^\[/s/^platform *= *//p' /src/platformio.ini)
if ! awk -v u="$platform" '$2 == u { f = 1 } END { exit !f }' "$LIST"; then
  echo "platformio.ini: Plattform $platform steht nicht in platformio-core3.sha256" >&2
  exit 1
fi
mkdir -p "$PIO/.cache/downloads"
while read -r sum url; do
  case "$sum" in '' | '#'*) continue ;; esac
  file="$PIO/.cache/downloads/$(printf %s "$url" | sha1sum | cut -c1-40)"   # so benennt PlatformIO seinen Cache
  if ! echo "$sum  $file" | sha256sum -c --status 2>/dev/null; then
    echo "   lade $url"
    python -I -c 'import sys, urllib.request; urllib.request.urlretrieve(sys.argv[1], sys.argv[2])' "$url" "$file.part"
    if ! echo "$sum  $file.part" | sha256sum -c --status; then
      rm -f "$file.part"
      echo "SHA-256 passt nicht zu platformio-core3.sha256: $url" >&2
      exit 1
    fi
    mv "$file.part" "$file"
  fi
done < "$LIST"

# Was pio installiert hat (Plattformen und Pakete), wird abgelehnt, außer es trifft eine dieser Regeln:
#   a) installiert von einer http(s)-URL aus platformio-core3.sha256 (Schema ohne Rücksicht auf Groß/klein);
#   b) ein Paket aus der platform.json der geprüften Plattform, dessen URL dort in der Liste steht, installiert über
#      sein tools.json (idf_tools.py, Quelle file://.../tools/<Name>) oder ohne .piopm (tool-esp_install);
#   c) aus der PlatformIO-Registry: .piopm mit owner und ohne uri (geprüft mit der Prüfsumme der Registry).
# Die Bibliotheken aus lib_deps liegen im Projekt, nicht hier, und laufen wie bei 2.0.17.
check_packages() {
  python -I - "$LIST" "$PIO" "$platform" <<'PY'
import glob, json, os, sys

listed = set()
for line in open(sys.argv[1], encoding="utf-8"):
    fields = line.split()
    if len(fields) == 2 and not fields[0].startswith("#"):
        listed.add(fields[1])
pio, platform = sys.argv[2], sys.argv[3]


def meta(d):  # Inhalt von .piopm: None ohne Datei, {} wenn unlesbar
    path = os.path.join(d, ".piopm")
    if not os.path.isfile(path):
        return None
    try:
        with open(path, encoding="utf-8") as f:
            m = json.load(f)
    except ValueError:
        return {}
    return m if isinstance(m, dict) else {}


def spec(m):
    s = (m or {}).get("spec")
    return s if isinstance(s, dict) else {}


def http(uri):
    return isinstance(uri, str) and uri.lower().startswith(("http://", "https://"))


def registry(m):
    return bool(spec(m).get("owner")) and not spec(m).get("uri")


bad, plats = [], []
for d in sorted(glob.glob(os.path.join(pio, "platforms", "*"))):
    m = meta(d)
    uri = spec(m).get("uri")
    if http(uri) and uri == platform and uri in listed:
        plats.append(d)
    elif not registry(m):
        bad.append(f"Plattform {os.path.basename(d)}: {uri or 'Quelle unbekannt'}")
packages = {}
if len(plats) == 1:
    with open(os.path.join(plats[0], "platform.json"), encoding="utf-8") as f:
        packages = json.load(f).get("packages", {})   # Teil der geprüften Plattform
else:
    bad.append(f"Plattform {platform}: nicht genau einmal installiert")
for d in sorted(glob.glob(os.path.join(pio, "packages", "*"))):
    m = meta(d)
    name = os.path.basename(d) if m is None else m.get("name")
    uri = spec(m).get("uri")
    entry = packages.get(name) if isinstance(name, str) else None
    url = entry.get("version") if isinstance(entry, dict) else None   # URL des Pakets laut Plattform
    if http(uri):
        ok = uri in listed                                                    # a)
    elif m is None or (isinstance(name, str) and uri == "file://" + os.path.join(pio, "tools", name)):
        ok = url in listed                                                    # b)
    else:
        ok = registry(m)                                                      # c)
    if not ok:
        bad.append(f"{os.path.basename(d)}: {uri or ('ohne .piopm' if m is None else 'Quelle unbekannt')}")
if bad:
    sys.exit("Nicht zugelassen (nicht aus platformio-core3.sha256, der geprüften Plattform oder der Registry):\n  "
             + "\n  ".join(bad))
print("   nur Zugelassenes")
PY
}

mkdir -p /build/firmware /build/server/flightpanel/certs
(cd /src && tar --exclude=./.pio -cf - .) | tar -C /build/firmware -xf -
cp /certs/roots.pem /build/server/flightpanel/certs/
cd /build/firmware

echo "== 3. Installieren, ohne zu bauen, und prüfen, bevor etwas davon baut"
pio pkg install -e esp32dev_core3
check_packages

echo "== 4. Build ohne include/fw_pubkey.h"
rm -f include/fw_pubkey.h
pio run -e esp32dev_core3

echo "== 4. Build mit include/fw_pubkey.h (Vorlage): Update-Prüfung mit mbedTLS 3 muss bauen und linken"
cp include/fw_pubkey.h.example include/fw_pubkey.h
PLATFORMIO_BUILD_FLAGS=-Wl,--require-defined=fwsigVerify pio run -e esp32dev_core3

echo "== 4. Nach den Builds dieselbe Prüfung"
check_packages

echo "== 5. Build wie im Ordner (secrets.h, fw_pubkey.h, falls vorhanden) für firmware/.pio/core3/"
rm -f include/fw_pubkey.h
if [ -f /src/include/fw_pubkey.h ]; then cp /src/include/fw_pubkey.h include/; fi
pio run -e esp32dev_core3
B=.pio/build/esp32dev_core3
F="$PIO/packages/framework-arduinoespressif32"
cp "$B/bootloader.bin" "$B/partitions.bin" "$B/firmware.bin" "$B/fw_version.txt" "$F/tools/partitions/boot_app0.bin" \
  "$F/tools/espota.py" /out/
# Adressen wie bei pio run -t upload; der NVS-Bereich (WLAN, Server, Token) dazwischen bleibt unberührt
cat > /out/flash.txt <<'EOF'
esptool.py --chip esp32 --port PORT write_flash 0x1000 .pio/core3/bootloader.bin 0x8000 .pio/core3/partitions.bin 0xe000 .pio/core3/boot_app0.bin 0x10000 .pio/core3/firmware.bin
EOF
echo "   Firmware $(cat "$B/fw_version.txt"): firmware/.pio/core3/, Flashen per USB (Bauanleitung, Abschnitt 4):"
echo "   pio pkg exec -p tool-esptoolpy -- $(cat /out/flash.txt)"
