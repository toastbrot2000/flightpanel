#!/bin/sh
# Server auf den Stand des Repos bringen: holen, neu bauen, neu starten. Neue
# Abhängigkeiten kommen nur über die Locks und den Image-Digest im Repo, also erst nach
# der Wartezeit (tools/escrow.py); der Bau holt nichts Neues, was dort nicht steht.
#
#   sh tools/update.sh            # nur, wenn es einen neuen Commit gibt
#   sh tools/update.sh --force    # auch ohne
#
# Wöchentlich in der Ruhezeit des Panels, nach dem Escrow-Lauf am Montagmorgen
# (crontab -e, als Nutzer in der Gruppe docker):
#   0 6 * * 1  cd /pfad/zu/flightpanel && sh tools/update.sh >> data/update.log 2>&1
set -e
cd "$(dirname "$0")/.."
old=$(git rev-parse --short HEAD)
git pull --ff-only --quiet
new=$(git rev-parse --short HEAD)
if [ "$old" = "$new" ] && [ "$1" != "--force" ]; then
  echo "$(date '+%F %T') nichts Neues ($new)"
  exit 0
fi
docker compose build --quiet
docker compose up -d
docker image prune -f > /dev/null       # alte Images der früheren Bauten
echo "$(date '+%F %T') aktualisiert: $old -> $new"
