#!/usr/bin/env bash
# Sincroniza desde Drive (texto + OCR de imágenes) y publica si hay cambios.
# Úsalo a mano o por cron. Cron cada 5 min (crontab -e):
#   */5 * * * * /home/alejo/projects/terremoto-venezuela/scripts/update.sh >> /tmp/sismo-sync.log 2>&1
set -euo pipefail
cd "$(dirname "$0")/.."
export PATH="$HOME/.local/ollama/bin:$PATH"

# Lock: evita que dos corridas (cron + manual, o cron solapado) se pisen.
exec 9>/tmp/sismo-sync.lock
flock -n 9 || { echo "Ya hay una sincronización en curso; salgo."; exit 0; }

# Si el árbol de trabajo está sucio (edición en curso), no toques nada.
if ! git diff --quiet || ! git diff --cached --quiet; then
  echo "Cambios sin commitear en el repo; salto esta corrida para no pisar trabajo."; exit 0
fi
git pull -q --rebase --autostash origin main || true

# 1) Asegura el servidor de Ollama (modelo de visión para OCR de fotos)
if ! curl -s http://localhost:11434/api/version >/dev/null 2>&1; then
  nohup ollama serve >/tmp/ollama.log 2>&1 &
  sleep 5
fi

# 2) OCR de imágenes nuevas (cacheado por hash; las ya vistas no se reprocesan)
python3 scripts/ocr_images.py || echo "(OCR omitido / sin Ollama)"

# 3) Fusiona todas las fuentes y regenera docs/data.json
python3 scripts/sync.py

# 4) Publica si cambió (Pages republica solo desde /docs)
if ! git diff --quiet -- docs/data.json; then
  git add docs/data.json docs/sitemap.xml data/manual/ocr-images.json
  git commit -m "datos: actualización $(date -u +'%Y-%m-%d %H:%M UTC')"
  git push
  echo "Actualizado y publicado."
else
  echo "Sin cambios."
fi
