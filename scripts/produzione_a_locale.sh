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
#   bash scripts/produzione_a_locale.sh --data-only   # DB + media (senza codice/static)
#
# Se in produzione usi GESPER_DATA_ROOT (es. /var/www/gesper/documento o /var/www/documento),
# imposta la stessa radice qui così DB e media coincidono con Django in produzione:
#   REMOTE_DATA_ROOT=/var/www/gesper/documento bash scripts/produzione_a_locale.sh --db-only --yes
# (su gesper1 verifica: grep GESPER_DATA_ROOT /etc/gesper.env)

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "${ROOT_DIR}"

REMOTE_HOST="${REMOTE_HOST:-root@gesper1.plazapretoria.it}"
REMOTE_APP_DIR="${REMOTE_APP_DIR:-/var/www/gesper}"
REMOTE_MEDIA_DIR="${REMOTE_MEDIA_DIR:-/var/www/media}"
# Se valorizzata (stesso path di GESPER_DATA_ROOT sulla VPS): DB = $REMOTE_DATA_ROOT/db.sqlite3, media = $REMOTE_DATA_ROOT/media/
REMOTE_DATA_ROOT="${REMOTE_DATA_ROOT:-}"
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
  bash scripts/produzione_a_locale.sh [--yes] [--code-only|--db-only|--media-only|--data-only]

Variabili (opzionali):
  REMOTE_HOST              default: root@gesper1.plazapretoria.it
  REMOTE_APP_DIR           default: /var/www/gesper
  REMOTE_MEDIA_DIR         default: /var/www/media (solo se REMOTE_DATA_ROOT è vuota)
  REMOTE_DATA_ROOT         es. /var/www/gesper/documento = GESPER_DATA_ROOT in /etc/gesper.env
                             → DB e media da quella radice (consigliato se produzione unificata)
EOF
      exit 0
      ;;
    --yes|-y) AUTO_YES=true ;;
    --code-only) DO_CODE=true; DO_DB=false; DO_MEDIA=false; DO_STATIC=false ;;
    --db-only) DO_CODE=false; DO_DB=true; DO_MEDIA=false; DO_STATIC=false ;;
    --media-only) DO_CODE=false; DO_DB=false; DO_MEDIA=true; DO_STATIC=false ;;
    --data-only) DO_CODE=false; DO_DB=true; DO_MEDIA=true; DO_STATIC=false ;;
    *)
      echo "Argomento non riconosciuto: $arg"
      exit 1
      ;;
  esac
done

bash scripts/segnala_ambiente.sh locale >/dev/null

if [[ -n "${REMOTE_DATA_ROOT}" ]]; then
  REMOTE_DB_PATH="${REMOTE_DATA_ROOT%/}/db.sqlite3"
  REMOTE_MEDIA_SYNC_PATH="${REMOTE_DATA_ROOT%/}/media/"
else
  REMOTE_DB_PATH="${REMOTE_APP_DIR%/}/db.sqlite3"
  REMOTE_MEDIA_SYNC_PATH="${REMOTE_MEDIA_DIR%/}/"
fi

echo "[LOCALE] Avvio sincronizzazione da PRODUZIONE"
echo "[PRODUZIONE] Sorgente: ${REMOTE_HOST}"
echo "  APP:        ${REMOTE_APP_DIR}"
echo "  DB remoto:  ${REMOTE_DB_PATH}"
echo "  Media rem.: ${REMOTE_MEDIA_SYNC_PATH}"
if [[ -n "${REMOTE_DATA_ROOT}" ]]; then
  echo "  (REMOTE_DATA_ROOT impostata — allineato a GESPER_DATA_ROOT in produzione)"
fi

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
  mkdir -p "${ROOT_DIR}/documento"
  if [[ -f "${ROOT_DIR}/documento/db.sqlite3" ]]; then
    cp -f "${ROOT_DIR}/documento/db.sqlite3" "${ROOT_DIR}/documento/db.sqlite3.backup_pre_prod_pull_${TS}"
  fi
  rsync -az \
    -e "ssh ${SSH_OPTS} ${SSH_COMMON_OPTS}" \
    "${REMOTE_HOST}:${REMOTE_DB_PATH}" "${ROOT_DIR}/db.sqlite3"
  cp -f "${ROOT_DIR}/db.sqlite3" "${ROOT_DIR}/documento/db.sqlite3"
  echo "    DB copiato in: ${ROOT_DIR}/db.sqlite3 e ${ROOT_DIR}/documento/db.sqlite3 (stesso file; settings usa il primo se esiste, altrimenti documento/)."
fi

if [[ "$DO_MEDIA" == true ]]; then
  echo "[3/4] Backup locale + sync MEDIA produzione -> locale"
  if [[ -d "${ROOT_DIR}/media" ]]; then
    cp -R "${ROOT_DIR}/media" "${ROOT_DIR}/media.backup_pre_prod_pull_${TS}"
  fi
  if [[ -d "${ROOT_DIR}/documento/media" ]]; then
    cp -R "${ROOT_DIR}/documento/media" "${ROOT_DIR}/documento/media.backup_pre_prod_pull_${TS}"
  fi
  mkdir -p "${ROOT_DIR}/media" "${ROOT_DIR}/documento/media"
  rsync -az --delete \
    --exclude '.DS_Store' \
    -e "ssh ${SSH_OPTS} ${SSH_COMMON_OPTS}" \
    "${REMOTE_HOST}:${REMOTE_MEDIA_SYNC_PATH}" "${ROOT_DIR}/media/"
  rsync -az --delete \
    --exclude '.DS_Store' \
    -e "ssh ${SSH_OPTS} ${SSH_COMMON_OPTS}" \
    "${REMOTE_HOST}:${REMOTE_MEDIA_SYNC_PATH}" "${ROOT_DIR}/documento/media/"
  echo "    Media allineati in: ${ROOT_DIR}/media/ e ${ROOT_DIR}/documento/media/ (MEDIA_ROOT dipende da settings.py)."
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

