#!/bin/sh
# tools/escrow.py im festgelegten Python-Image, mit uv aus tools/escrow.lock (Prüfsummen):
# Auch das Werkzeug kommt nur nach der Wartezeit ins Haus.
#
#   sh tools/escrow.sh                          # Stichtag: vor 7 Tagen
#   sh tools/escrow.sh 2026-10-04T20:00:00Z     # fester Stichtag (Workflow)
set -e
cd "$(dirname "$0")/.."
IMAGE=$(grep -o 'python:3.12-slim@sha256:[0-9a-f]*' server/Dockerfile)
ROOT="$(pwd -W 2>/dev/null || pwd)"       # pwd -W: Git Bash unter Windows
MSYS_NO_PATHCONV=1 docker run --rm -v "$ROOT:/repo" -w /repo "$IMAGE" sh -c "
  pip install -q --root-user-action=ignore --disable-pip-version-check --require-hashes -r tools/escrow.lock
  python tools/escrow.py ${1:+--cutoff $1}"
