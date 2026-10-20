#!/usr/bin/env bash
set -e
sudo apt update && sudo apt install -y docker.io docker-compose-v2 git cron
sudo usermod -aG docker "$USER"
[ -d ws-tvazteca ] || git clone https://github.com/Ikeracevedo/ws-tvazteca
cd ws-tvazteca
git pull
[ -f .env ] || echo "SLACK_WEBHOOK=..." > .env
sudo docker compose up -d --build
sudo docker compose logs -f
