#!/usr/bin/env bash
set -euo pipefail

# Sincronizza LOCALE -> PRODUZIONE:
# - codice applicativo
# - database sqlite
# - documenti/media
#
# Uso:
#   bash scripts/locale_a_produzione.sh
#   bash scripts/locale_a_produzione.sh --yes
#   bash scripts/locale_a_produzione.sh --code-only
#   bash scripts/locale_a_produzione.sh --db-only
#   bash scripts/locale_a_produzione.sh --media-only
#
# Se in produzione Django usa GESPER_DATA_ROOT (vedi /etc/gesper.env sulla VPS), allinea DB + media:
#   REMOTE_DATA_ROOT=/var/www/gesper/documento bash scripts/locale_a_produzione.sh --yes

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "${ROOT_DIR}"

# Media locale: allineato a settings.py — se esiste la cartella «genitore/media» (es. htdocs/media), usala;
# altrimenti gesper/media. Override: LOCAL_MEDIA_DIR=/percorso bash scripts/locale_a_produzione.sh
if [[ -n "${LOCAL_MEDIA_DIR:-}" ]]; then
  LOCAL_MEDIA_DIR="$(cd "${LOCAL_MEDIA_DIR}" && pwd)"
else
  _parent_media="$(cd "${ROOT_DIR}/.." && pwd)/media"
  if [[ -d "${_parent_media}" ]]; then
    LOCAL_MEDIA_DIR="${_parent_media}"
  else
    LOCAL_MEDIA_DIR="${ROOT_DIR}/media"
  fi
fi

REMOTE_HOST="${REMOTE_HOST:-root@gesper1.plazapretoria.it}"
# Radice progetto Django (coerente con WorkingDirectory di systemd gunicorn). Override: REMOTE_APP_DIR
REMOTE_APP_DIR="${REMOTE_APP_DIR:-/var/www/gesper}"
REMOTE_MEDIA_DIR="${REMOTE_MEDIA_DIR:-/var/www/media}"
# Se valorizzata (= GESPER_DATA_ROOT in produzione): DB → $REMOTE_DATA_ROOT/db.sqlite3, media → $REMOTE_DATA_ROOT/media/
REMOTE_DATA_ROOT="${REMOTE_DATA_ROOT:-}"
SSH_OPTS="${SSH_OPTS:-}"
SSH_COMMON_OPTS="-o ConnectTimeout=10 -o ControlMaster=auto -o ControlPersist=15m -o ControlPath=$HOME/.ssh/cm-%r@%h:%p"

DO_CODE=true
DO_DB=true
DO_MEDIA=true
AUTO_YES=false

for arg in "$@"; do
  case "$arg" in
    --help|-h)
      cat <<'EOF'
Uso:
  bash scripts/locale_a_produzione.sh [--yes] [--code-only|--db-only|--media-only]

Variabili (opzionali):
  REMOTE_HOST        default: root@gesper1.plazapretoria.it
  REMOTE_APP_DIR     default: /var/www/gesper
  REMOTE_MEDIA_DIR   default: /var/www/media (solo se REMOTE_DATA_ROOT è vuota)
  REMOTE_DATA_ROOT   es. /var/www/gesper/documento = GESPER_DATA_ROOT in produzione
EOF
      exit 0
      ;;
    --yes|-y) AUTO_YES=true ;;
    --code-only) DO_CODE=true; DO_DB=false; DO_MEDIA=false ;;
    --db-only) DO_CODE=false; DO_DB=true; DO_MEDIA=false ;;
    --media-only) DO_CODE=false; DO_DB=false; DO_MEDIA=true ;;
    *)
      echo "Argomento non riconosciuto: $arg"
      exit 1
      ;;
  esac
done

if [[ -n "${REMOTE_DATA_ROOT}" ]]; then
  REMOTE_DB_PATH="${REMOTE_DATA_ROOT%/}/db.sqlite3"
  REMOTE_MEDIA_SYNC_PATH="${REMOTE_DATA_ROOT%/}/media/"
else
  REMOTE_DB_PATH="${REMOTE_APP_DIR%/}/db.sqlite3"
  REMOTE_MEDIA_SYNC_PATH="${REMOTE_MEDIA_DIR%/}/"
fi

bash scripts/segnala_ambiente.sh locale >/dev/null
echo "[LOCALE] Avvio sincronizzazione verso PRODUZIONE"
echo "[PRODUZIONE] Destinazione: ${REMOTE_HOST}"
echo "  APP:         ${REMOTE_APP_DIR}"
echo "  MEDIA orig.: ${LOCAL_MEDIA_DIR}"
echo "  DB dest.:    ${REMOTE_DB_PATH}"
echo "  MEDIA dest.: ${REMOTE_MEDIA_SYNC_PATH}"
if [[ -n "${REMOTE_DATA_ROOT}" ]]; then
  echo "  (REMOTE_DATA_ROOT impostata — DB e media nella radice dati unificata)"
fi

if [[ "$AUTO_YES" != true ]]; then
  echo ""
  echo "ATTENZIONE: questa operazione può sovrascrivere dati in PRODUZIONE."
  echo "Componenti attive:"
  echo "  - CODICE: ${DO_CODE}"
  echo "  - DB:     ${DO_DB}"
  echo "  - MEDIA:  ${DO_MEDIA}"
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

if [[ "$DO_CODE" == true ]]; then
  echo "[1/4] Sync CODICE locale -> produzione"
  rsync -az --delete \
    --exclude '.git/' \
    --exclude '.venv/' \
    --exclude 'venv/' \
    --exclude '__pycache__/' \
    --exclude '*.pyc' \
    --exclude 'db.sqlite3' \
    --exclude 'db.sqlite3.*' \
    --exclude 'media/' \
    --exclude 'staticfiles/' \
    --exclude '.DS_Store' \
    --exclude '.ambiente_operativo' \
    -e "ssh ${SSH_OPTS} ${SSH_COMMON_OPTS}" \
    "${ROOT_DIR}/" "${REMOTE_HOST}:${REMOTE_APP_DIR}/"
fi

if [[ "$DO_DB" == true ]]; then
  [[ -f "${ROOT_DIR}/db.sqlite3" ]] || { echo "ERRORE: db.sqlite3 non trovato in locale"; exit 1; }
  echo "[2/4] Backup + sync DB locale -> produzione"
  TS="$(date +%F_%H%M%S)"
  ssh ${SSH_OPTS} ${SSH_COMMON_OPTS} "${REMOTE_HOST}" \
    "if [ -n '${REMOTE_DATA_ROOT}' ]; then install -d -m 0755 '${REMOTE_DATA_ROOT%/}'; fi; \
     if [ -f '${REMOTE_DB_PATH}' ]; then cp -f '${REMOTE_DB_PATH}' '${REMOTE_DB_PATH}.bak_${TS}'; fi"
  rsync -az \
    -e "ssh ${SSH_OPTS} ${SSH_COMMON_OPTS}" \
    "${ROOT_DIR}/db.sqlite3" "${REMOTE_HOST}:${REMOTE_DB_PATH}.new"
  ssh ${SSH_OPTS} ${SSH_COMMON_OPTS} "${REMOTE_HOST}" \
    "mv -f '${REMOTE_DB_PATH}.new' '${REMOTE_DB_PATH}' && \
     (id www-data >/dev/null 2>&1 && chown www-data:www-data '${REMOTE_DB_PATH}' || true) && \
     chmod 664 '${REMOTE_DB_PATH}' || true"
fi

if [[ "$DO_MEDIA" == true ]]; then
  [[ -d "${LOCAL_MEDIA_DIR}" ]] || { echo "ERRORE: cartella media locale non trovata: ${LOCAL_MEDIA_DIR}"; exit 1; }
  echo "[3/4] Sync MEDIA locale -> produzione (${LOCAL_MEDIA_DIR} -> ${REMOTE_MEDIA_SYNC_PATH})"
  ssh ${SSH_OPTS} ${SSH_COMMON_OPTS} "${REMOTE_HOST}" "install -d -m 0755 '${REMOTE_MEDIA_SYNC_PATH%/}'"
  rsync -az --delete \
    --exclude '.DS_Store' \
    -e "ssh ${SSH_OPTS} ${SSH_COMMON_OPTS}" \
    "${LOCAL_MEDIA_DIR}/" "${REMOTE_HOST}:${REMOTE_MEDIA_SYNC_PATH}"
fi

echo "[4/4] Post-deploy produzione (migrate, collectstatic, check, restart)"
# venv: default in REMOTE_APP_DIR; se manca, prova cartella padre (es. /var/www/gesper/.venv con app in gesper-app/)
ssh ${SSH_OPTS} ${SSH_COMMON_OPTS} "${REMOTE_HOST}" "cd '${REMOTE_APP_DIR}' && \
  if [ -f .venv/bin/activate ]; then . .venv/bin/activate; \
  elif [ -f ../.venv/bin/activate ]; then . ../.venv/bin/activate; \
  fi && \
  python3 manage.py migrate && \
  python3 manage.py collectstatic --noinput && \
  python3 manage.py check && \
  systemctl restart gesper && \
  systemctl is-active gesper"

echo "Completato: LOCALE -> PRODUZIONE"

