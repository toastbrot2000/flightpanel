#!/usr/bin/env python3
"""Abhängigkeiten mit Wartezeit ("Escrow"), gegen Angriffe über die Lieferkette: Eine
neue Version kommt erst ins Image, wenn sie seit ESCROW_DAYS Tagen veröffentlicht ist.
Bis dahin sollte ein kompromittiertes Release aufgefallen und zurückgezogen sein.

    python3 tools/escrow.py                                  # Stand von vor 7 Tagen
    python3 tools/escrow.py --days 10
    python3 tools/escrow.py --cutoff 2026-10-04T20:00:00Z    # fester Stichtag (Workflow)

Ändert nur Dateien im Repo; bauen, testen und einchecken macht
.github/workflows/escrow.yml (wöchentlich). Der Server baut nur aus dem Repo und
holt sich nie selbst die neueste Version von irgendetwas.

- Locks (LOCKS): aus den requirements-Dateien mit uv pip compile --exclude-newer
  aufgelöst, jede Version mit Prüfsummen. pip installiert mit --require-hashes nur
  genau diese Dateien. Auch uv selbst kommt so ins Haus (tools/escrow.lock).
- Basis-Image (IMAGE), fest per Digest in IMAGE_FILES: Ein neuer Digest wird erst
  übernommen, wenn das Tag auf Docker Hub seit ESCROW_DAYS nicht mehr verschoben wurde.

Braucht uv (im Workflow fest per Version). Gibt aus, was sich geändert hat."""

import argparse
import json
import re
import subprocess
import sys
import urllib.request
from datetime import UTC, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ESCROW_DAYS = 7
PYTHON = "3.12"
IMAGE = "python:3.12-slim"
IMAGE_FILES = ("server/Dockerfile", "tools/check_firmware.sh")
LOCKS = (("server/requirements.txt", "server/requirements.lock"),
         ("server/requirements-dev.txt", "server/requirements-dev.lock"),
         ("firmware/platformio-requirements.txt", "firmware/platformio.lock"),
         ("tools/escrow-requirements.txt", "tools/escrow.lock"))


def cutoff(days: int, now: datetime | None = None) -> datetime:
    return (now or datetime.now(UTC)) - timedelta(days=days)


def parse_time(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def stamp(t: datetime) -> str:
    return t.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def adopt(published: datetime, cut: datetime) -> bool:
    """Lange genug veröffentlicht, um es zu übernehmen?"""
    return published <= cut


def pinned(text: str, image: str) -> str | None:
    """Der Digest, auf den image in text festgelegt ist."""
    m = re.search(re.escape(image) + r"@(sha256:[0-9a-f]{64})", text)
    return m.group(1) if m else None


def repin(text: str, image: str, digest: str) -> str:
    return re.sub(re.escape(image) + r"@sha256:[0-9a-f]{64}", f"{image}@{digest}", text)


def versions(lock_text: str) -> dict[str, str]:
    """{"pillow": "12.3.0"} aus einem Lock."""
    return dict(re.findall(r"^([A-Za-z0-9_.-]+)==([^\s;\\]+)", lock_text, re.M))


def diff(before: dict[str, str], after: dict[str, str]) -> list[str]:
    return [f"{n} {before.get(n, '-')} -> {after.get(n, '-')}"
            for n in sorted(before.keys() | after.keys()) if before.get(n) != after.get(n)]


def compile_lock(src: str, dst: str, cut: datetime) -> None:
    subprocess.run(["uv", "pip", "compile", "--quiet", "--universal", "--python-version", PYTHON,
                    "--generate-hashes", "--exclude-newer", stamp(cut),
                    "--custom-compile-command", "python3 tools/escrow.py", "-o", dst, src],
                   cwd=ROOT, check=True)


def hub_tag(image: str) -> tuple[str, datetime]:
    """Aktueller Digest des Tags auf Docker Hub und wann das Tag zuletzt verschoben wurde."""
    repo, tag = image.split(":")
    url = f"https://hub.docker.com/v2/repositories/library/{repo}/tags/{tag}"
    with urllib.request.urlopen(url, timeout=30) as r:
        d = json.load(r)
    return d["digest"], parse_time(d["tag_last_pushed"])


def main() -> int:
    ap = argparse.ArgumentParser(description="Abhängigkeiten nach Wartezeit aktualisieren")
    ap.add_argument("--days", type=int, default=ESCROW_DAYS, help="Mindestalter neuer Versionen in Tagen")
    ap.add_argument("--cutoff", help="fester Stichtag (UTC, ISO), statt jetzt minus --days")
    a = ap.parse_args()
    cut = parse_time(a.cutoff) if a.cutoff else cutoff(a.days)
    changes: list[str] = []
    for src, dst in LOCKS:
        path = ROOT / dst
        before = versions(path.read_text(encoding="utf-8")) if path.exists() else {}
        compile_lock(src, dst, cut)
        changes += diff(before, versions(path.read_text(encoding="utf-8")))

    digest, pushed = hub_tag(IMAGE)
    files = [ROOT / f for f in IMAGE_FILES]
    current = pinned(files[0].read_text(encoding="utf-8"), IMAGE)
    if digest != current:
        if adopt(pushed, cut):
            for f in files:
                f.write_text(repin(f.read_text(encoding="utf-8"), IMAGE, digest), encoding="utf-8", newline="\n")
            changes.append(f"{IMAGE} {(current or '-')[:19]} -> {digest[:19]} (Tag seit {pushed:%Y-%m-%d})")
        else:
            print(f"{IMAGE}: neuer Digest seit {pushed:%Y-%m-%d}, wartet noch", file=sys.stderr)
    print("\n".join(dict.fromkeys(changes)) or "nichts Neues, das alt genug ist")
    return 0


if __name__ == "__main__":
    sys.exit(main())
