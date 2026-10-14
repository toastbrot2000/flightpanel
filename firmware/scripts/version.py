# FW_VERSION für die Firmware: die Bauzeit in UTC als JJMMTTHHMM, ein Text aus genau 10 Ziffern.
#
# platformio.ini bindet das Skript für die ESP32-Builds ein (extra_scripts = post:..., nicht für env:native). Das Makro
# gilt nur für die eigenen Quellen in src/ (projenv): Arduino-Kern und Bibliotheken bleiben gebaut, ein neuer Build
# übersetzt nur src/ neu.
#
#   FW_VERSION=2610010000 pio run    baut mit genau dieser Version (Prüfstand: älter oder neuer als die laufende)
#
# Die Version steht nach dem Build auch in .pio/build/<env>/fw_version.txt. tools/fw_release.sh schreibt sie von dort
# ins Manifest, so laufen Manifest und Firmware nie auseinander: Stünde im Manifest eine neuere Version als in der
# Firmware, hielte das Panel jedes Mal das gerade installierte Image für ein Update.
import os
import re
import time

Import("env", "projenv")  # noqa: F821 (SCons)

version = os.environ.get("FW_VERSION") or time.strftime("%y%m%d%H%M", time.gmtime())
if not re.fullmatch(r"[0-9]{10}", version):
    print(f"FW_VERSION={version!r}: genau 10 Ziffern erwartet (JJMMTTHHMM)")
    env.Exit(1)  # noqa: F821

projenv.Append(CPPDEFINES=[("FW_VERSION", env.StringifyMacro(version))])  # noqa: F821
build_dir = env.subst("$BUILD_DIR")  # noqa: F821
os.makedirs(build_dir, exist_ok=True)
with open(os.path.join(build_dir, "fw_version.txt"), "w", encoding="ascii", newline="\n") as f:
    f.write(version + "\n")
print(f"FW_VERSION {version}")
