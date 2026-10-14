# PlatformIO-Skript (extra_scripts = pre:embed_roots.py): bettet die Vertrauensliste für HTTPS ein.
#
# Quelle ist server/flightpanel/certs/roots.pem (tools/make_roots.py), dieselbe Datei, mit der der Server prüft.
# Daraus entsteht vor jedem Bauen $BUILD_DIR/generated/roots_pem.h mit den PEM-Blöcken als C-String, ohne die
# Kommentare; net.cpp bindet sie ein. Im Quellbaum wird nichts erzeugt oder verändert (board_build.embed_txtfiles
# würde die Datei beim Bauen kurz umschreiben und ihren Symbolnamen aus dem absoluten Pfad bilden). Fehlt die
# Datei, bricht der Build ab: Ohne Liste könnte das Panel kein https.
import os
import re

Import("env")  # noqa: F821 (Import und env stellt PlatformIO bereit)
env = env  # noqa: F821

PROJECT = env.subst("$PROJECT_DIR")
SRC = os.path.normpath(os.path.join(PROJECT, "..", "server", "flightpanel", "certs", "roots.pem"))
OUT_DIR = os.path.join(env.subst("$BUILD_DIR"), "generated")
OUT = os.path.join(OUT_DIR, "roots_pem.h")

if not os.path.isfile(SRC):
    print(f"FEHLER: {SRC} fehlt (tools/make_roots.py); die Firmware bettet die Vertrauensliste ein")
    env.Exit(1)

with open(SRC, encoding="utf-8") as f:
    text = f.read().replace("\r\n", "\n")
blocks = re.findall(r"-----BEGIN CERTIFICATE-----\n[A-Za-z0-9+/=\n]+?-----END CERTIFICATE-----\n", text)
if not blocks or len(blocks) != text.count("-----BEGIN CERTIFICATE-----"):
    print(f"FEHLER: {SRC} enthält keine oder unlesbare Zertifikate")
    env.Exit(1)

lines = "".join(blocks).splitlines()
header = (
    "// Erzeugt von firmware/embed_roots.py aus server/flightpanel/certs/roots.pem, nicht von Hand ändern.\n"
    "#pragma once\n"
    f"// {len(blocks)} Wurzelzertifikate (PEM) für WiFiClientSecure::setCACert\n"
    "static const char ROOTS_PEM[] =\n"
    + "\n".join(f'    "{line}\\n"' for line in lines)
    + ";\n"
)
os.makedirs(OUT_DIR, exist_ok=True)
old = None
if os.path.isfile(OUT):
    with open(OUT, encoding="utf-8") as f:
        old = f.read()
if old != header:  # nur bei Änderung schreiben, sonst baut net.cpp jedes Mal neu
    with open(OUT, "w", encoding="utf-8", newline="\n") as f:
        f.write(header)
env.Append(CPPPATH=[OUT_DIR])
