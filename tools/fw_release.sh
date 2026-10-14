#!/bin/sh
# Signiertes Firmware-Release für die Panels (Spezifikation: Firmware §3, signierte Updates).
#
#   sh tools/fw_release.sh <privater-schlüssel.pem>    bauen, signieren, nach data/firmware/canary/ legen
#   sh tools/fw_release.sh --dry-run                   nur bauen und das Manifest ohne Signatur zeigen
#
# Nur ein Mensch führt dieses Skript aus, denn nur ein Mensch tippt die Passphrase. KI-Agenten führen es nie aus:
# Ohne Terminal oder mit gesetztem CLAUDECODE (Claude Code) bricht es ab. --dry-run signiert nichts, legt nichts ab
# und braucht keinen Schlüssel.
#
# Was es tut:
#   1. Den Schlüssel prüfen: mit Passphrase verschlüsselt, nicht im Repo, nicht in einem Git-Arbeitsverzeichnis,
#      nicht in einem synchronisierten Ordner (OneDrive, Dropbox ...). Er gehört nur auf diesen Rechner, mit einer
#      Sicherung offline.
#   2. In derselben festen Docker-Toolchain bauen wie tools/check_firmware.sh, ohne include/secrets.h und ohne
#      platformio_local.ini: firmware.bin ist öffentlich, der Rettungskanal liefert sie ohne Anmeldung. Ohne
#      include/fw_pubkey.h bricht es ab: Ein Image ohne Update-Prüfung käme nur per USB wieder vom Panel. Passt das
#      Image nicht in den App-Slot, scheitert schon der Build.
#   3. Das Manifest schreiben (Format: firmware/include/manifest.h): Version aus dem Build (fw_version.txt, siehe
#      firmware/scripts/version.py), Größe, SHA-256, nur LF; jede Zeile wird gegen das Format geprüft.
#   4. Die ersten drei Zeilen mit openssl dgst -sha256 -sign signieren, die Signatur gegen den Schlüssel in
#      include/fw_pubkey.h prüfen, so wie es das Panel tut, und als vierte Zeile anhängen.
#   5. firmware.bin und firmware.txt nach data/firmware/canary/ legen, erst das Image, dann das Manifest.
#
# Danach holt das eigene Panel (Kanal canary) das Update. Läuft es dort, bekommen es die anderen (Kanal stable):
#
#   mkdir -p data/firmware/stable
#   cp data/firmware/canary/firmware.bin data/firmware/stable/ && cp data/firmware/canary/firmware.txt data/firmware/stable/
#
# Der Server liest DATA_DIR/firmware/<Kanal>/; läuft er auf einem anderen Rechner, gehören die Dateien dorthin.
set -e
cd "$(dirname "$0")/.."
REPO="$(pwd -P)"
IMAGE=$(grep -o 'python:3.12-slim@sha256:[0-9a-f]*' tools/check_firmware.sh)   # eine Quelle, tools/escrow.py pflegt sie
PUB=firmware/include/fw_pubkey.h
DEST=data/firmware/canary

die() {
  echo "fw_release: $*" >&2
  exit 1
}

usage() {
  echo "Aufruf: sh tools/fw_release.sh <privater-schlüssel.pem>   bauen, signieren, nach $DEST legen" >&2
  echo "        sh tools/fw_release.sh --dry-run                  nur bauen, Manifest ohne Signatur zeigen" >&2
  exit 2
}

# Eine Manifest-Datei gegen das Format prüfen (firmware/include/manifest.h). $2: 3 Zeilen (signierter Teil) oder 4.
# Ein CR darin hieße: Kein Panel nähme das Update je an.
check_manifest() {
  if LC_ALL=C grep -q "$(printf '\r')" "$1"; then die "CR im Manifest: nur LF erlaubt"; fi
  [ "$(wc -l < "$1" | tr -d ' ')" = "$2" ] || die "Manifest hat nicht genau $2 Zeilen"
  [ "$(tail -c 1 "$1" | od -An -c | tr -d ' ')" = '\n' ] || die "Manifest endet nicht mit LF"
  [ "$(wc -c < "$1" | tr -d ' ')" -le 512 ] || die "Manifest länger als 512 Byte"
  sed -n 1p "$1" | LC_ALL=C grep -Eq '^[0-9]{10}$' || die "Zeile 1: Version nicht genau 10 Ziffern"
  sed -n 2p "$1" | LC_ALL=C grep -Eq '^[1-9][0-9]{0,9}$' || die "Zeile 2: Größe keine Zahl ohne führende Null"
  sed -n 3p "$1" | LC_ALL=C grep -Eq '^[0-9a-f]{64}$' || die "Zeile 3: SHA-256 nicht 64 Zeichen 0-9a-f"
  if [ "$2" = 4 ]; then
    sed -n 4p "$1" | LC_ALL=C grep -Eq '^([A-Za-z0-9+/]{4}){1,23}([A-Za-z0-9+/]{4}|[A-Za-z0-9+/]{3}=|[A-Za-z0-9+/]{2}==)$' \
      || die "Zeile 4: Signatur kein Base64 bis 96 Zeichen"
  fi
}

dry=0
key=
case "${1:-}" in
  --dry-run) dry=1 ;;
  "" | -h | --help) usage ;;
  -*) echo "fw_release: unbekannte Option $1" >&2; usage ;;
  *) key=$1 ;;
esac
[ $# -le 1 ] || usage
[ -n "$IMAGE" ] || die "Image-Digest in tools/check_firmware.sh nicht gefunden"

# 1. Mensch, Schlüssel, Firmware-Schlüssel
if [ "$dry" = 0 ]; then
  [ -z "${CLAUDECODE:-}" ] || die "läuft in Claude Code: KI-Agenten führen dieses Skript nie aus (nur --dry-run)"
  { [ -t 0 ] && [ -t 2 ]; } || die "nur an einem Terminal: Die Passphrase tippt ein Mensch"
  { [ -f "$key" ] && [ -r "$key" ]; } || die "Schlüssel nicht gefunden oder nicht lesbar: $key"
  keydir=$(cd "$(dirname "$key")" && pwd -P)
  keyabs="$keydir/$(basename "$key")"
  case "$keyabs" in "$REPO"/*) die "der Schlüssel liegt im Repo ($keyabs), er gehört außerhalb" ;; esac
  if git -C "$keydir" rev-parse --is-inside-work-tree > /dev/null 2>&1; then
    die "der Schlüssel liegt in einem Git-Arbeitsverzeichnis ($keydir), er gehört außerhalb"
  fi
  lower=$(printf '%s' "$keyabs" | tr '[:upper:]' '[:lower:]')
  for sync in onedrive dropbox "google drive" googledrive icloud clouddocs nextcloud owncloud seafile syncthing \
    pcloud megasync "box sync"; do
    case "$lower" in
      *"$sync"*) die "der Schlüssel liegt in einem synchronisierten Ordner ($keyabs): nur auf diesen Rechner, Sicherung offline" ;;
    esac
  done
  grep -q -e "-----BEGIN ENCRYPTED PRIVATE KEY-----" -e "^Proc-Type: 4,ENCRYPTED" "$key" \
    || die "der Schlüssel hat keine Passphrase (erwartet: BEGIN ENCRYPTED PRIVATE KEY); neu anlegen, Bauanleitung Abschnitt 4"
  [ -f "$PUB" ] || die "$PUB fehlt: Ein Release ohne Update-Prüfung käme nie wieder vom Panel (Vorlage: fw_pubkey.h.example)"
  pubsrc=$PUB
elif [ -f "$PUB" ]; then
  pubsrc=$PUB
else
  echo "fw_release: --dry-run ohne $PUB, gebaut wird mit der Vorlage" >&2
  pubsrc=$PUB.example
fi

tmp=$(mktemp -d)
cleanup() {
  stty echo 2> /dev/null || true
  rm -rf "$tmp"
}
trap cleanup EXIT
trap 'exit 1' HUP INT TERM
# Der öffentliche Schlüssel, so wie die Firmware ihn einbaut
sed -n '/-----BEGIN PUBLIC KEY-----/,/-----END PUBLIC KEY-----/p' "$pubsrc" | tr -d '\r' > "$tmp/pub.pem"
[ -s "$tmp/pub.pem" ] || die "kein BEGIN/END PUBLIC KEY in $pubsrc"

# 2. Bauen. -Wl,--require-defined: Ohne die Update-Prüfung (fw_pubkey.h nicht erkannt) scheitert der Build.
SRC="$(pwd -W 2> /dev/null || pwd)/firmware"   # pwd -W: Git Bash unter Windows
OUT="$(cd "$tmp" && { pwd -W 2> /dev/null || pwd; })"
echo "== Build in Docker (feste Toolchain wie tools/check_firmware.sh)"
MSYS_NO_PATHCONV=1 docker run --rm \
  -v "$SRC:/src:ro" -v "$OUT:/out" -v flightpanel-pio:/root/.platformio \
  "$IMAGE" sh -c '
    set -e
    apt-get update -qq && DEBIAN_FRONTEND=noninteractive apt-get install -y -qq --no-install-recommends git >/dev/null
    pip install -q --root-user-action=ignore --require-hashes -r /src/platformio.lock
    mkdir /build && cp -r /src/. /build && cd /build
    rm -rf .pio
    # Nichts Eigenes ins öffentliche Image: keine WLAN-Daten, kein OTA-Passwort (OTA bleibt damit aus).
    rm -f include/secrets.h platformio_local.ini
    cp include/secrets.h.example include/secrets.h   # nur bis secrets.h optional ist (Schritt 4); nichts Geheimes darin
    [ -f include/fw_pubkey.h ] || cp include/fw_pubkey.h.example include/fw_pubkey.h   # nur bei --dry-run
    PLATFORMIO_BUILD_FLAGS=-Wl,--require-defined=fwsigVerify pio run -e esp32dev
    cp .pio/build/esp32dev/firmware.bin .pio/build/esp32dev/fw_version.txt /out/
  '

bin="$tmp/firmware.bin"
version=$(tr -d '\r\n' < "$tmp/fw_version.txt")
size=$(wc -c < "$bin" | tr -d ' ')
sha=$(openssl dgst -sha256 -r "$bin" | cut -d ' ' -f 1)
commit=$(git describe --always --dirty 2> /dev/null || echo "?")
# Jede Zeile des öffentlichen Schlüssels steht im Image: Die Update-Prüfung ist drin, mit diesem Schlüssel. Nach
# "BEGIN PUBLIC KEY" allein zu suchen reicht nicht, mbedTLS bringt diese Zeile selbst mit.
while IFS= read -r l; do
  LC_ALL=C grep -a -q -F -e "$l" "$bin" || die "firmware.bin enthält den Schlüssel aus $pubsrc nicht: Update-Prüfung fehlt"
done < "$tmp/pub.pem"
# Keine Zeile der Schlüsseldatei im Image (falls sie statt des öffentlichen Schlüssels in fw_pubkey.h gelandet ist;
# das Übersetzen bricht dann schon ab, src/fwsig.cpp). Nicht nach "PRIVATE KEY" suchen: Auch das bringt mbedTLS mit.
if [ "$dry" = 0 ]; then
  grep -v -e "-----" -e ":" "$key" | tr -d '\r' > "$tmp/keylines"
  while IFS= read -r l; do
    if [ -n "$l" ] && LC_ALL=C grep -a -q -F -e "$l" "$bin"; then die "firmware.bin enthält Teile von $key: so nie veröffentlichen"; fi
  done < "$tmp/keylines"
fi
for ch in canary stable; do
  f="data/firmware/$ch/firmware.txt"
  [ -f "$f" ] || continue
  old=$(head -n 1 "$f" | tr -d '\r')
  newer=$(awk -v a="$version" -v b="$old" 'BEGIN { print ((a "") > (b "")) ? 1 : 0 }')   # als Text, nie als Zahl
  [ "$newer" = 1 ] || die "Version $version ist nicht neuer als $ch ($old): Kein Panel nähme sie an"
done

# 3. Signierter Teil: drei Zeilen, nur LF
printf '%s\n%s\n%s\n' "$version" "$size" "$sha" > "$tmp/signed.txt"
check_manifest "$tmp/signed.txt" 3

if [ "$dry" = 1 ]; then
  echo "== --dry-run: Manifest ohne Signatur, nichts signiert, nichts abgelegt (Quelle $commit)"
  cat "$tmp/signed.txt"
  exit 0
fi

# 4. Signieren. Die Passphrase liest das Skript selbst (ohne Echo) und reicht sie über eine Pipe an openssl: kein
#    Kommandozeilenargument, keine Datei, und es geht auch dort, wo openssl selbst nicht nachfragen kann (Git Bash).
printf 'Passphrase für %s: ' "$key" >&2
stty -echo || die "stty -echo geht nicht, die Passphrase wäre sichtbar"
IFS= read -r pass || pass=
stty echo
echo >&2
if ! printf '%s\n' "$pass" | openssl dgst -sha256 -sign "$key" -passin stdin -out "$tmp/sig.der" "$tmp/signed.txt"; then
  pass=
  die "Signieren fehlgeschlagen (Passphrase falsch?)"
fi
pass=
if ! openssl dgst -sha256 -verify "$tmp/pub.pem" -signature "$tmp/sig.der" "$tmp/signed.txt" > /dev/null 2>&1; then
  die "die Signatur passt nicht zum Schlüssel in $PUB (anderer Schlüssel, oder noch die Vorlage?): Kein Panel nähme sie an"
fi
sig=$(openssl base64 -A -in "$tmp/sig.der" | tr -d '\r\n')
{ cat "$tmp/signed.txt"; printf '%s\n' "$sig"; } > "$tmp/firmware.txt"
check_manifest "$tmp/firmware.txt" 4

# 5. Ablegen: erst das Image, dann das Manifest, jeweils als ganze Datei (umbenennen)
mkdir -p "$DEST"
cp "$bin" "$DEST/.firmware.bin.tmp"
mv -f "$DEST/.firmware.bin.tmp" "$DEST/firmware.bin"
cp "$tmp/firmware.txt" "$DEST/.firmware.txt.tmp"
mv -f "$DEST/.firmware.txt.tmp" "$DEST/firmware.txt"
echo "Release $version ($size Byte, Quelle $commit) liegt in $DEST/:"
cat "$DEST/firmware.txt"
echo "Läuft es auf dem eigenen Panel (canary), für alle (stable), erst das Image, dann das Manifest:"
echo "  mkdir -p data/firmware/stable && cp $DEST/firmware.bin data/firmware/stable/ && cp $DEST/firmware.txt data/firmware/stable/"
