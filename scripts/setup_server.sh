#!/bin/bash
# Helium Multi-Tenant Demo — Server Setup Script
# Run on a fresh Ubuntu 24.04 t3.medium EC2 instance
#
# Usage:
#   ssh -i helium-key.pem ubuntu@<IP>
#   curl -sSL https://raw.githubusercontent.com/bob-nzelu/helium-multitenant-demo/main/scripts/setup_server.sh | bash

set -euo pipefail

echo "=== Helium Multi-Tenant Demo — Server Setup ==="

# 1. System updates
echo "[1/6] Updating system..."
sudo apt-get update -qq
sudo apt-get upgrade -y -qq

# 2. Install Docker
echo "[2/6] Installing Docker..."
if ! command -v docker &>/dev/null; then
    curl -fsSL https://get.docker.com | sudo sh
    sudo usermod -aG docker ubuntu
    echo "Docker installed. You may need to re-login for group changes."
else
    echo "Docker already installed."
fi

# 3. Install Docker Compose v2
echo "[3/6] Installing Docker Compose..."
if ! docker compose version &>/dev/null; then
    sudo apt-get install -y -qq docker-compose-plugin
fi
echo "Docker Compose: $(docker compose version 2>/dev/null || echo 'will work after re-login')"

# 4. Clone repo + side-by-side helium-services (Gap A — HEARTBEAT_BUILD_CONTEXT)
echo "[4/6] Cloning repositories..."
cd /home/ubuntu
if [ -d "helium-multitenant-demo" ]; then
    cd helium-multitenant-demo
    git pull origin main
else
    git clone https://github.com/bob-nzelu/helium-multitenant-demo.git
    cd helium-multitenant-demo
fi
# Gap A: the demo's ./services/heartbeat was deleted — HeartBeat builds from a
# side-by-side checkout of helium-services. Clone it next to this repo.
SERVICES_BRANCH="${SERVICES_BRANCH:-sika/deploy-integration}"
if [ ! -d "../helium-services/.git" ]; then
    git clone --branch "${SERVICES_BRANCH}" \
        https://github.com/bob-nzelu/helium-services.git ../helium-services
fi
export HEARTBEAT_BUILD_CONTEXT="$(cd ../helium-services/HeartBeat && pwd)"

# 5. Create .env
echo "[5/6] Creating .env..."
if [ ! -f .env ]; then
    cp .env.example .env
    echo ".env created from .env.example — review and update if needed."
else
    echo ".env already exists."
fi

# 6. Build and start
# NOTE: For a Sika single-tenant box, prefer ./deploy_sika.sh — it closes all
# four compose gaps (build context, license, s2s keys, tenants.json) and uses
# .env.sika. This generic path is the multi-tenant demo bootstrap.
echo "[6/6] Building and starting services..."
docker compose build --no-cache
docker compose up -d

echo ""
echo "=== Setup Complete ==="
echo "Services:"
docker compose ps --format "table {{.Name}}\t{{.Status}}\t{{.Ports}}"
echo ""
echo "Health checks:"
echo "  HeartBeat: curl http://localhost:9000/health"
echo "  Relay:     curl http://localhost:8082/health"
echo "  Core:      curl http://localhost:8080/health"
echo "  Simulator: curl http://localhost:8090/health"
