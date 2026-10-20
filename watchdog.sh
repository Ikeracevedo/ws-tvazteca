#!/usr/bin/env bash
cd "$(dirname "$0")"
ESTADO=$(docker inspect -f '{{.State.Health.Status}}' $(docker compose ps -q votabot) 2>/dev/null || echo "muerto")
if [ "$ESTADO" != "healthy" ]; then
  curl -s -X POST "$SLACK_WEBHOOK" -H 'Content-Type: application/json' \
    -d "{\"text\":\"⚠️ *votabot* no está sano: \`$ESTADO\`. Reiniciando...\"}"
  docker compose restart votabot
fi