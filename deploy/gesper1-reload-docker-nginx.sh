#!/usr/bin/env bash
# Riavvia il container nginx Docker dopo che Gunicorn ricrea gunicorn.sock.
# Montare un socket Unix in Docker non segue il replace del file sul host: senza
# restart nginx, il proxy resta su un inode morto → 502 Bad Gateway.
#
# Installazione: deploy/install-gesper1-docker-nginx-hook.sh (sulla VPS).
set -u

COMPOSE_DIR="${GESPER_DOCKER_COMPOSE_DIR:-/opt/payroll/procedura_paghe}"
SOCKET="${GESPER_GUNICORN_SOCKET:-/home/deploy/gesper1/gunicorn.sock}"
LOG_TAG="gesper1-docker-nginx"

log() {
    logger -t "$LOG_TAG" "$*" 2>/dev/null || true
    printf '%s\n' "$*"
}

for _ in $(seq 1 30); do
    if [[ -S "$SOCKET" ]]; then
        break
    fi
    sleep 0.2
done

if [[ ! -S "$SOCKET" ]]; then
    log "WARN: socket ${SOCKET} assente — skip reload nginx Docker"
    exit 0
fi

if [[ ! -d "$COMPOSE_DIR" ]] || [[ ! -f "${COMPOSE_DIR}/docker-compose.yml" ]]; then
    log "INFO: ${COMPOSE_DIR} non trovato — skip (stack Docker assente)"
    exit 0
fi

if ! command -v docker >/dev/null 2>&1; then
    log "INFO: docker non disponibile — skip"
    exit 0
fi

cd "$COMPOSE_DIR" || exit 0

if ! docker compose ps nginx --status running -q 2>/dev/null | grep -q .; then
    log "INFO: container nginx non in esecuzione — skip"
    exit 0
fi

if docker compose restart nginx; then
    log "OK: nginx Docker riavviato (mount socket Gunicorn aggiornato)"
else
    log "WARN: docker compose restart nginx fallito (Gunicorn resta attivo)"
fi

exit 0
