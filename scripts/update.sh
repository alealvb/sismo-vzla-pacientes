#!/usr/bin/env bash
# Sincroniza desde Drive y publica si hay cambios. Úsalo a mano o por cron.
# Cron cada 5 min (crontab -e):
#   */5 * * * * /home/alejo/projects/terremoto-venezuela/scripts/update.sh >> /tmp/sismo-sync.log 2>&1
set -euo pipefail
cd "$(dirname "$0")/.."
python3 scripts/sync.py
if ! git diff --quiet -- docs/data.json; then
  git add docs/data.json
  git commit -m "datos: actualización $(date -u +'%Y-%m-%d %H:%M UTC')"
  git push
  echo "Actualizado y publicado."
else
  echo "Sin cambios."
fi
