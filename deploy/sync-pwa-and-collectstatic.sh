#!/usr/bin/env bash
# 1) rsync cartella PWA gesper-app → server
# 2) collectstatic sul progetto Django (stesso STATIC_ROOT per gesper + gesper-www)
# Lanciare dal Mac (repo locale), non dalla VPS. Mac vs VPS, SSH root/ubuntu: deploy/PROCEDURA_DEPLOY.md §0.1
# Procedura generale: deploy/PROCEDURA_DEPLOY.md
#
# Variabili (oltre a quelle di sync-gesper-app.example.sh):
#   GESPER_REMOTE_PROJECT_DIR   default /var/www/gesper
#   GESPER_REMOTE_PYTHON        default $GESPER_REMOTE_PROJECT_DIR/.venv/bin/python
#   GESPER_COLLECTSTATIC_SETTINGS  default settings_production
#
# Esempio:
#   GESPER_SSH_IDENTITY=~/.ssh/id_ed25519 ./deploy/sync-pwa-and-collectstatic.sh
#   GESPER_REMOTE_APP_DIR=/var/www/gesper-app GESPER_REMOTE_PROJECT_DIR=/var/www/gesper ./deploy/sync-pwa-and-collectstatic.sh

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
HOST="${GESPER_DEPLOY_HOST:-ubuntu@gesper1.plazapretoria.it}"
REMOTE_GESPER="${GESPER_REMOTE_PROJECT_DIR:-/var/www/gesper}"
REMOTE_PY="${GESPER_REMOTE_PYTHON:-${REMOTE_GESPER}/.venv/bin/python}"
DJANGO_SETTINGS="${GESPER_COLLECTSTATIC_SETTINGS:-settings_production}"

export GESPER_DEPLOY_HOST="$HOST"
export GESPER_REMOTE_APP_DIR="${GESPER_REMOTE_APP_DIR:-/var/www/gesper-app}"

if [[ -n "${GESPER_SSH_IDENTITY:-}" ]]; then
  export RSYNC_RSH="ssh -i ${GESPER_SSH_IDENTITY} -o IdentitiesOnly=yes"
fi

echo "== rsync PWA → $HOST:${GESPER_REMOTE_APP_DIR} =="
bash "$SCRIPT_DIR/sync-gesper-app.example.sh"

SSH_BASE=(ssh -o ConnectTimeout=20)
[[ -n "${GESPER_SSH_IDENTITY:-}" ]] && SSH_BASE+=(-i "${GESPER_SSH_IDENTITY}" -o IdentitiesOnly=yes)
SSH_BASE+=("$HOST")

echo "== collectstatic ($REMOTE_GESPER, DJANGO_SETTINGS_MODULE=$DJANGO_SETTINGS) =="
"${SSH_BASE[@]}" "cd '${REMOTE_GESPER}' && DJANGO_SETTINGS_MODULE='${DJANGO_SETTINGS}' '${REMOTE_PY}' manage.py collectstatic --noinput"

echo ""
echo "OK. Suggerimento: sudo systemctl restart gesper gesper-www && sudo nginx -t && sudo systemctl reload nginx"
