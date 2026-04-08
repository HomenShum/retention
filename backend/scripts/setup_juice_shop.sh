#!/usr/bin/env bash
# setup_juice_shop.sh — Pull and start OWASP Juice Shop via Docker.
set -euo pipefail

JUICE_SHOP_URL="http://localhost:3000"
MAX_RETRIES=30
RETRY_INTERVAL=2

# 1. Check Docker is available
if ! command -v docker &>/dev/null; then
    echo "ERROR: Docker is not installed or not in PATH."
    echo "Install Docker Desktop from https://www.docker.com/products/docker-desktop and retry."
    exit 1
fi

if ! docker info &>/dev/null; then
    echo "ERROR: Docker daemon is not running."
    echo "Start Docker Desktop and retry."
    exit 1
fi

echo "Docker OK"

# 2. Check if juice-shop is already running on port 3000
if curl -sf --max-time 3 "$JUICE_SHOP_URL" >/dev/null 2>&1; then
    echo "Juice Shop is already running at $JUICE_SHOP_URL"
    exit 0
fi

# Check if the container exists but is stopped
EXISTING=$(docker ps -a --filter "name=juice-shop" --format "{{.Names}}" 2>/dev/null || true)
if [ -n "$EXISTING" ]; then
    echo "Found existing juice-shop container — starting it..."
    docker start juice-shop
else
    # 3. Run a fresh container
    echo "Starting Juice Shop container..."
    docker run -d \
        --name juice-shop \
        -p 3000:3000 \
        --restart unless-stopped \
        bkimminich/juice-shop
fi

# 4. Wait for health check
echo "Waiting for Juice Shop to become ready..."
attempt=0
while [ $attempt -lt $MAX_RETRIES ]; do
    if curl -sf --max-time 3 "$JUICE_SHOP_URL" >/dev/null 2>&1; then
        echo ""
        echo "Juice Shop ready at $JUICE_SHOP_URL"
        exit 0
    fi
    attempt=$((attempt + 1))
    printf "."
    sleep $RETRY_INTERVAL
done

echo ""
echo "ERROR: Juice Shop did not become ready after $((MAX_RETRIES * RETRY_INTERVAL))s."
echo "Check Docker logs: docker logs juice-shop"
exit 1
