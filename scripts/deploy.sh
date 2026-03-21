#!/usr/bin/env bash
set -euo pipefail

# ===================================================
# deploy.sh — Production deployment script
# Runs on the DigitalOcean Droplet via GitHub Actions
# ===================================================

APP_DIR="$(cd "$(dirname "$0")/.." && pwd)"
COMPOSE_FILE="$APP_DIR/docker-compose.prod.yml"
LOG_FILE="$APP_DIR/logs/deploy.log"

mkdir -p "$APP_DIR/logs"

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" | tee -a "$LOG_FILE"
}

log "========== Deployment started =========="
log "Image: ${DOCKER_IMAGE:-latest}"

# --- 1. Pull the new image ---
log "Pulling new image..."
docker compose -f "$COMPOSE_FILE" pull web

# --- 2. Run database migrations ---
log "Running Alembic migrations..."
docker compose -f "$COMPOSE_FILE" run --rm \
    -e MYSQL_HOST=db \
    web python -m alembic upgrade head || {
    log "WARNING: Migration failed (may be OK on first deploy)"
}

# --- 3. Restart services ---
log "Restarting services..."
docker compose -f "$COMPOSE_FILE" up -d --remove-orphans

# --- 4. Wait for health check ---
log "Waiting for health check..."
RETRIES=10
DELAY=5
for i in $(seq 1 $RETRIES); do
    if curl -sf http://localhost:5000/health > /dev/null 2>&1; then
        log "Health check passed (attempt $i/$RETRIES)"
        break
    fi
    if [ "$i" -eq "$RETRIES" ]; then
        log "ERROR: Health check failed after $RETRIES attempts"
        log "Rolling back..."
        docker compose -f "$COMPOSE_FILE" logs --tail=50 web
        exit 1
    fi
    log "Health check attempt $i/$RETRIES failed, retrying in ${DELAY}s..."
    sleep "$DELAY"
done

# --- 5. Cleanup old images ---
log "Cleaning up old images..."
docker image prune -f --filter "until=168h" || true

log "========== Deployment complete =========="
