#!/usr/bin/env bash
# Deploy produzione Hetzner — webhook GitHub o manuale.
# Allinea il codice a origin/main; migrate, collectstatic, restart gesper1.
#
#   bash /home/deploy/deploy_gesper1.sh
#
# Variabili: GESPER_APP_DIR, GESPER_SYSTEMD_UNIT, GESPER_GIT_BRANCH
set -euo pipefail

APP_DIR="${GESPER_APP_DIR:-/home/deploy/gesper1}"
UNIT="${GESPER_SYSTEMD_UNIT:-gesper1}"
BRANCH="${GESPER_GIT_BRANCH:-main}"
LOG_TAG="[deploy_gesper1]"

log() { echo "${LOG_TAG} $*"; }

if [[ ! -d "${APP_DIR}" ]]; then
  log "ERRORE: cartella assente: ${APP_DIR}" >&2
  exit 1
fi

cd "${APP_DIR}"
git config --global --add safe.directory "${APP_DIR}" 2>/dev/null || true

# Chiave GitHub su root (Hetzner); il webhook di solito gira come root.
if [[ "$(id -un)" == "root" && -f /root/.ssh/github_gesper1 ]]; then
  export GIT_SSH_COMMAND="ssh -i /root/.ssh/github_gesper1 -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new"
fi

log "Fetch origin/${BRANCH}…"
git fetch origin "${BRANCH}"

log "Reset hard a origin/${BRANCH}…"
git reset --hard "origin/${BRANCH}"
git clean -fd \
  -e .env -e venv -e .venv -e media -e documento -e staticfiles -e logs 2>/dev/null || true
log "Commit: $(git log -1 --oneline)"

if [[ "$(id -un)" == "root" ]]; then
  chown -R deploy:www-data "${APP_DIR}"
fi

_run_app() {
  local cmd=("$@")
  if [[ "$(id -un)" == "deploy" ]]; then
    "${cmd[@]}"
  else
    sudo -u deploy -- bash -lc "cd '${APP_DIR}' && ${cmd[*]}"
  fi
}

if [[ ! -x "${APP_DIR}/venv/bin/python" ]]; then
  log "Creazione venv…"
  _run_app "python3 -m venv venv"
  [[ "$(id -un)" == "root" ]] && chown -R deploy:www-data "${APP_DIR}/venv"
fi

DEPLOY_SH="set -euo pipefail
cd '${APP_DIR}'
source venv/bin/activate
[[ -f .env ]] && set -a && source .env && set +a
export DJANGO_SETTINGS_MODULE=\${DJANGO_SETTINGS_MODULE:-settings_production}
pip install -r requirements.txt -q
python manage.py migrate --noinput
python manage.py collectstatic --noinput
"

log "pip / migrate / collectstatic…"
if [[ "$(id -un)" == "deploy" ]]; then
  eval "${DEPLOY_SH}"
else
  sudo -u deploy -- bash -lc "${DEPLOY_SH}"
fi

log "restart ${UNIT}…"
systemctl restart "${UNIT}"
sleep 2
systemctl is-active "${UNIT}" || { log "ERRORE: ${UNIT} non active" >&2; exit 1; }

log "Deploy completato."
