#!/usr/bin/env bash
set -e
sudo apt update && sudo apt install -y docker.io docker-compose-v2 git cron
sudo usermod -aG docker "$USER"
git clone https://github.com/Ikeracevedo/ws-tvazteca && cd tvazteca-votabot
echo "SLACK_WEBHOOK=..." > .env
sudo docker compose up -d --build
sudo docker compose logs -f