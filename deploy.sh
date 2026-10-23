#!/usr/bin/env bash
set -euo pipefail

sudo apt update
sudo apt install -y docker.io docker-compose-v2 git cron

[ -d ws-tvazteca ] || git clone https://github.com/Ikeracevedo/ws-tvazteca
cd ws-tvazteca
git pull --ff-only

mkdir -p votabot_data
[ -f votabot_data/config.json ] || cp config.json votabot_data/config.json
[ -f .env ] || printf 'SLACK_WEBHOOK=\n' > .env

sudo docker compose up -d --build
sudo docker compose logs -f
