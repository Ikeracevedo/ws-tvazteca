#!/usr/bin/env bash
set -e
sudo apt update && sudo apt install -y docker.io docker-compose-v2 git
sudo usermod -aG docker "$USER"
git clone https://github.com/TU_USUARIO/tvazteca-votabot.git
cd tvazteca-votabot
sudo docker compose up -d --build
sudo docker compose logs -f