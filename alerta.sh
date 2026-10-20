#!/usr/bin/env bash
set -uo pipefail
cd "$(dirname "$0")"
exec 9>/tmp/votabot-alerta.lock; flock -n 9 || exit 0   # evita solapes del cron

SLACK_WEBHOOK="${SLACK_WEBHOOK:-$(grep -E '^SLACK_WEBHOOK=' .env | cut -d= -f2-)}"
FALLOS=/tmp/votabot-fallos

CID=$(docker compose ps -q votabot 2>/dev/null || true)
if [ -z "$CID" ]; then
  ESTADO="no-corre"
else
  ESTADO=$(docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}sin-healthcheck{{end}}' "$CID" 2>/dev/null || echo desconocido)
fi

avisar() { curl -s -X POST "$SLACK_WEBHOOK" -H 'Content-Type: application/json' -d "$1"; }

if [ "$ESTADO" = "healthy" ]; then
  echo 0 > "$FALLOS"; exit 0
fi

N=$(( $(cat "$FALLOS" 2>/dev/null || echo 0) + 1 )); echo "$N" > "$FALLOS"
echo "estado=$ESTADO fallos=$N"

if [ "$N" -ge 3 ]; then
  avisar "{\"text\":\"⚠️ *votabot* no está sano (\`$ESTADO\`) tras 3 chequeos. Reiniciando.\"}"
  docker compose restart votabot
  echo 0 > "$FALLOS"
fi