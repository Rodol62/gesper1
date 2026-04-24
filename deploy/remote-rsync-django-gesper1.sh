#!/usr/bin/env bash
# Dal Mac: rsync del progetto Django → /var/www/gesper sulla VPS, poi pip/migrate/collectstatic/restart gesper.
# Non usa --delete (evita di cancellare file presenti solo sul server). Esclude venv, git, DB locale, media, .env.
#
#   GESPER_DEPLOY_HOST=root@gesper1.plazapretoria.it ./deploy/remote-rsync-django-gesper1.sh
#
# Opzionali:
#   GESPER_REMOTE_PROJECT_DIR=/var/www/gesper
#   GESPER_SSH_IDENTITY=~/.ssh/id_ed25519
#   GESPER_SKIP_LOCAL_CHECK=1     # non eseguire manage.py check in locale prima
#   GESPER_SKIP_PIP=1
#   GESPER_SKIP_MIGRATE=1
#   GESPER_SKIP_COLLECTSTATIC=1
#   GESPER_RSYNC_DRY_RUN=1     # solo simulazione rsync; non modifica il server (niente pip/migrate/restart)
#   GESPER_COLLECTSTATIC_SETTINGS=settings_production
#
# Auth / candidati (2026): dopo rsync eseguire migrate (non usare GESPER_SKIP_MIGRATE=1) per applicare
# accounts 0024–0026 — verifica e-mail al login, TOTP web, disattivazione flag SMS in DB; registrazione
# candidato solo OTP e-mail + template aggiornati.
# Dopo migrate/restart: python manage.py verifica_registrazione_candidato
# (se serve solo DB: stesso comando con --fix-db per sms_abilitato=False).
# In /etc/gesper.env: GESPER_REDIS_URL=redis://127.0.0.1:6379/1 se Redis è installato (OTP tra worker Gunicorn).
#
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
LOCAL_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
HOST="${GESPER_DEPLOY_HOST:-root@gesper1.plazapretoria.it}"
REMOTE="${GESPER_REMOTE_PROJECT_DIR:-/var/www/gesper}"
DJANGO_SETTINGS="${GESPER_COLLECTSTATIC_SETTINGS:-settings_production}"

SSH_BASE=(ssh -o ConnectTimeout=30)
[[ -n "${GESPER_SSH_IDENTITY:-}" ]] && SSH_BASE+=(-i "${GESPER_SSH_IDENTITY}" -o IdentitiesOnly=yes)
if [[ -z "${GESPER_SSH_NO_TTY:-}" ]] && [[ -t 0 ]]; then
  SSH_BASE+=(-t)
fi
SSH_BASE+=("$HOST")

if [[ -n "${GESPER_SSH_IDENTITY:-}" ]]; then
  export RSYNC_RSH="ssh -i ${GESPER_SSH_IDENTITY} -o IdentitiesOnly=yes"
fi

if [[ -z "${GESPER_SKIP_LOCAL_CHECK:-}" ]]; then
  echo "== check locale =="
  if [[ -x "$LOCAL_ROOT/.venv/bin/python" ]]; then
    (cd "$LOCAL_ROOT" && "$LOCAL_ROOT/.venv/bin/python" manage.py check)
  else
    echo "(salto: manca $LOCAL_ROOT/.venv/bin/python — attiva venv o imposta GESPER_SKIP_LOCAL_CHECK=1)" >&2
  fi
fi

RSYNC_EXCL=(
  --exclude '.venv/'
  --exclude '.git/'
  --exclude '__pycache__/'
  --exclude '*.pyc'
  --exclude 'logs/'
  --exclude 'db.sqlite3'
  --exclude 'media/'
  --exclude 'documento/'
  --exclude '.env'
  --exclude '.env.*'
  --exclude '.DS_Store'
  --exclude 'htmlcov/'
  --exclude '.pytest_cache/'
  --exclude 'node_modules/'
  --exclude '.cursor/'
)

echo "== rsync progetto → $HOST:$REMOTE/ (senza --delete) =="
RSYNC=(rsync -avz)
[[ -n "${GESPER_RSYNC_DRY_RUN:-}" ]] && RSYNC+=(--dry-run)
RSYNC+=("${RSYNC_EXCL[@]}")
RSYNC+=("$LOCAL_ROOT/" "$HOST:$REMOTE/")
"${RSYNC[@]}"

if [[ -n "${GESPER_RSYNC_DRY_RUN:-}" ]]; then
  echo "== Dry-run: nessun file inviato; non eseguo pip / migrate / collectstatic / restart sul server. =="
  exit 0
fi

REMOTE_PY="${REMOTE}/.venv/bin/python"
REMOTE_PIP="${REMOTE}/.venv/bin/pip"

REMOTE_SH="set -euo pipefail; cd '${REMOTE}'"
# Stesso DB e cache di systemd (GESPER_DATA_ROOT, GESPER_REDIS_URL, …)
REMOTE_SH+="; if [[ -f /etc/gesper.env ]]; then set -a; source /etc/gesper.env; set +a; fi"
if [[ -z "${GESPER_SKIP_PIP:-}" ]]; then
  REMOTE_SH+="; '${REMOTE_PIP}' install -r requirements.txt"
fi
if [[ -z "${GESPER_SKIP_MIGRATE:-}" ]]; then
  REMOTE_SH+="; DJANGO_SETTINGS_MODULE='${DJANGO_SETTINGS}' '${REMOTE_PY}' manage.py migrate --noinput"
fi
if [[ -z "${GESPER_SKIP_COLLECTSTATIC:-}" ]]; then
  REMOTE_SH+="; DJANGO_SETTINGS_MODULE='${DJANGO_SETTINGS}' '${REMOTE_PY}' manage.py collectstatic --noinput"
fi
REMOTE_SH+="; systemctl restart gesper; systemctl is-active gesper"

echo "== $HOST: pip / migrate / collectstatic / restart gesper =="
# shellcheck disable=SC2029
"${SSH_BASE[@]}" "bash -lc $(printf %q "$REMOTE_SH")"

echo "OK. Verifica: bash deploy/verify-public-endpoints.sh"
