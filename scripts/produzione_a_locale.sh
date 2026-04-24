#!/usr/bin/env bash
set -euo pipefail

# Sincronizza PRODUZIONE -> LOCALE:
# - codice applicativo
# - database sqlite
# - documenti/media
# - staticfiles
#
# Uso:
#   bash scripts/produzione_a_locale.sh
#   bash scripts/produzione_a_locale.sh --yes
#   bash scripts/produzione_a_locale.sh --code-only
#   bash scripts/produzione_a_locale.sh --db-only
#   bash scripts/produzione_a_locale.sh --media-only

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "${ROOT_DIR}"

REMOTE_HOST="${REMOTE_HOST:-root@94.177.201.223}"
REMOTE_APP_DIR="${REMOTE_APP_DIR:-/var/www/gesper}"
REMOTE_MEDIA_DIR="${REMOTE_MEDIA_DIR:-/var/www/media}"
SSH_OPTS="${SSH_OPTS:-}"
SSH_COMMON_OPTS="-o ConnectTimeout=10 -o ControlMaster=auto -o ControlPersist=15m -o ControlPath=$HOME/.ssh/cm-%r@%h:%p"

DO_CODE=true
DO_DB=true
DO_MEDIA=true
DO_STATIC=true
AUTO_YES=false

for arg in "$@"; do
  case "$arg" in
    --help|-h)
      cat <<'EOF'
Uso:
  bash scripts/produzione_a_locale.sh [--yes] [--code-only|--db-only|--media-only]
EOF
      exit 0
      ;;
    --yes|-y) AUTO_YES=true ;;
    --code-only) DO_CODE=true; DO_DB=false; DO_MEDIA=false; DO_STATIC=false ;;
    --db-only) DO_CODE=false; DO_DB=true; DO_MEDIA=false; DO_STATIC=false ;;
    --media-only) DO_CODE=false; DO_DB=false; DO_MEDIA=true; DO_STATIC=false ;;
    *)
      echo "Argomento non riconosciuto: $arg"
      exit 1
      ;;
  esac
done

bash scripts/segnala_ambiente.sh locale >/dev/null
echo "[LOCALE] Avvio sincronizzazione da PRODUZIONE"
echo "[PRODUZIONE] Sorgente: ${REMOTE_HOST}"
echo "  APP:   ${REMOTE_APP_DIR}"
echo "  MEDIA: ${REMOTE_MEDIA_DIR}"

if [[ "$AUTO_YES" != true ]]; then
  echo ""
  echo "ATTENZIONE: questa operazione può sovrascrivere dati in LOCALE."
  echo "Componenti attive:"
  echo "  - CODICE:     ${DO_CODE}"
  echo "  - DB:         ${DO_DB}"
  echo "  - MEDIA:      ${DO_MEDIA}"
  echo "  - STATICFILES:${DO_STATIC}"
  read -r -p "Digita SI per confermare: " ans
  [[ "$ans" == "SI" ]] || { echo "Annullato."; exit 1; }
fi

cleanup_ssh_master() {
  ssh ${SSH_OPTS} ${SSH_COMMON_OPTS} -O exit "${REMOTE_HOST}" >/dev/null 2>&1 || true
}
trap cleanup_ssh_master EXIT

if ! ssh ${SSH_OPTS} ${SSH_COMMON_OPTS} -O check "${REMOTE_HOST}" >/dev/null 2>&1; then
  ssh ${SSH_OPTS} ${SSH_COMMON_OPTS} -MNf "${REMOTE_HOST}"
fi

ssh ${SSH_OPTS} ${SSH_COMMON_OPTS} "${REMOTE_HOST}" "echo '[PRODUZIONE] Host raggiunto:' \$(hostname)"

TS="$(date +%Y%m%d_%H%M%S)"

if [[ "$DO_CODE" == true ]]; then
  echo "[1/4] Sync CODICE produzione -> locale"
  rsync -az --delete \
    --exclude '.git/' \
    --exclude '.venv/' \
    --exclude '__pycache__/' \
    --exclude '*.pyc' \
    --exclude 'db.sqlite3' \
    --exclude 'db.sqlite3.*' \
    --exclude 'media/' \
    --exclude 'staticfiles/' \
    --exclude '.DS_Store' \
    --exclude '.ambiente_operativo' \
    -e "ssh ${SSH_OPTS} ${SSH_COMMON_OPTS}" \
    "${REMOTE_HOST}:${REMOTE_APP_DIR}/" "${ROOT_DIR}/"
fi

if [[ "$DO_DB" == true ]]; then
  echo "[2/4] Backup locale + sync DB produzione -> locale"
  if [[ -f "${ROOT_DIR}/db.sqlite3" ]]; then
    cp -f "${ROOT_DIR}/db.sqlite3" "${ROOT_DIR}/db.sqlite3.backup_pre_prod_pull_${TS}"
  fi
  rsync -az \
    -e "ssh ${SSH_OPTS} ${SSH_COMMON_OPTS}" \
    "${REMOTE_HOST}:${REMOTE_APP_DIR}/db.sqlite3" "${ROOT_DIR}/db.sqlite3"
fi

if [[ "$DO_MEDIA" == true ]]; then
  echo "[3/4] Backup locale + sync MEDIA produzione -> locale"
  if [[ -d "${ROOT_DIR}/media" ]]; then
    cp -R "${ROOT_DIR}/media" "${ROOT_DIR}/media.backup_pre_prod_pull_${TS}"
  fi
  rsync -az --delete \
    --exclude '.DS_Store' \
    -e "ssh ${SSH_OPTS} ${SSH_COMMON_OPTS}" \
    "${REMOTE_HOST}:${REMOTE_MEDIA_DIR}/" "${ROOT_DIR}/media/"
fi

if [[ "$DO_STATIC" == true ]]; then
  echo "[4/4] Sync STATICFILES produzione -> locale"
  rsync -az --delete \
    --exclude '.DS_Store' \
    -e "ssh ${SSH_OPTS} ${SSH_COMMON_OPTS}" \
    "${REMOTE_HOST}:${REMOTE_APP_DIR}/staticfiles/" "${ROOT_DIR}/staticfiles/"
fi

echo "Verifica Django locale (check)"
if [[ -x "${ROOT_DIR}/.venv/bin/python" ]]; then
  "${ROOT_DIR}/.venv/bin/python" manage.py check
else
  python3 manage.py check
fi

echo "Completato: PRODUZIONE -> LOCALE"

