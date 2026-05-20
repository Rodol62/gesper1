#!/usr/bin/env bash
# DEPRECATO — vedi deploy/DEPRECATED.md. Usa: gesper pull-data + gesper push-code (deploy/DEPLOY_STANDARD.md).
if [[ -z "${GESPER_ALLOW_DEPRECATED_SCRIPT:-}" ]]; then
  echo "DEPRECATO: remote-full-sync-to-gesper1.sh" >&2
  echo "  Usa ./deploy/gesper.sh pull-data e ./deploy/gesper.sh push-code" >&2
  echo "  Per forzare: GESPER_ALLOW_DEPRECATED_SCRIPT=1 …" >&2
  exit 1
fi
# Sincronizzazione "completa" locale → gesper1: codice (come deploy) + SQLite + albero media locale.
#
# ⚠️  Sovrascrive il DB e i file sotto la cartella media remota. Esegue backup su
#     ${GESPER_REMOTE_PROJECT_DIR}/backup_full_sync_YYYYMMDD_HHMMSS/ prima.
# ⚠️  Ferma gunicorn durante sostituzione DB. Pianificare la finestra.
#
# NON copia .env: i segreti restano in /etc/gesper.env sul server.
#
# Uso (obbligatorio GESPER_FULL_SYNC_CONFIRM=1):
#   GESPER_FULL_SYNC_CONFIRM=1 ./deploy/remote-full-sync-to-gesper1.sh
#
# Rilevamento automatico (come settings.py in dev):
#   DB: GESPER_SQLITE_PATH, oppure gesper/db.sqlite3, documento/db.sqlite3, htdocs/db.sqlite3
#   media: GESPER_MEDIA_ROOT se impostata, altrimenti htdocs/media, gesper/media, documento/media
# Stesse variabili di remote-rsync-django-gesper1.sh, più:
#   GESPER_REMOTE_DATA_ROOT=/var/www/gesper/documento   # default; deve coincidere con GESPER_DATA_ROOT sul VPS
#   GESPER_REMOTE_DB=...  GESPER_REMOTE_MEDIA=...  (override)
#   GESPER_LOCAL_DB=...   GESPER_LOCAL_MEDIA=...  (override)
#   GESPER_MEDIA_SYNC_DELETE=1   # rsync media con --delete (cancella sul server i file assenti in locale)
#   GESPER_SKIP_LOCAL_CHECK=1
#   GESPER_RSYNC_DRY_RUN=1        # nessun invio, solo anteprima
#
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
LOCAL_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
HOST="${GESPER_DEPLOY_HOST:-root@gesper1.plazapretoria.it}"
REMOTE_PROJ="${GESPER_REMOTE_PROJECT_DIR:-/var/www/gesper}"
DJANGO_SETTINGS="${GESPER_COLLECTSTATIC_SETTINGS:-settings_production}"

if [[ "${GESPER_FULL_SYNC_CONFIRM:-}" != "1" ]]; then
  echo "Imposta GESPER_FULL_SYNC_CONFIRM=1 per eseguire (sovrascrive DB e media sul server)." >&2
  exit 1
fi

# Stesso ordine di settings._sqlite_database_path: env, radice, documento, htdocs genitore
_LOCAL_HTDocs_MEDIA="$(cd "$LOCAL_ROOT/.." 2>/dev/null && pwd)/media"
if [[ -n "${GESPER_LOCAL_DB:-}" ]]; then
  LOCAL_DB="$GESPER_LOCAL_DB"
else
  if [[ -n "${GESPER_SQLITE_PATH:-}" && -f "${GESPER_SQLITE_PATH}" ]]; then
    LOCAL_DB="${GESPER_SQLITE_PATH}"
  elif [[ -f "$LOCAL_ROOT/db.sqlite3" ]]; then
    LOCAL_DB="$LOCAL_ROOT/db.sqlite3"
  elif [[ -f "$LOCAL_ROOT/documento/db.sqlite3" ]]; then
    LOCAL_DB="$LOCAL_ROOT/documento/db.sqlite3"
  elif [[ -f "$LOCAL_ROOT/../db.sqlite3" ]]; then
    LOCAL_DB="$(cd "$LOCAL_ROOT/.." && pwd)/db.sqlite3"
  else
    echo "Nessun db.sqlite3 trovato (radice, documento/, htdocs). Imposta GESPER_LOCAL_DB=" >&2
    exit 1
  fi
fi

# Stesso ordine di MEDIA_ROOT: GESPER_MEDIA_ROOT, htdocs/media, gesper/media, documento/media
if [[ -n "${GESPER_LOCAL_MEDIA:-}" ]]; then
  LOCAL_MEDIA="$GESPER_LOCAL_MEDIA"
else
  if [[ -n "${GESPER_MEDIA_ROOT:-}" && -d "${GESPER_MEDIA_ROOT}" ]]; then
    LOCAL_MEDIA="${GESPER_MEDIA_ROOT}"
  elif [[ -d "$_LOCAL_HTDocs_MEDIA" ]]; then
    LOCAL_MEDIA="$_LOCAL_HTDocs_MEDIA"
  elif [[ -d "$LOCAL_ROOT/media" ]]; then
    LOCAL_MEDIA="$LOCAL_ROOT/media"
  elif [[ -d "$LOCAL_ROOT/documento/media" ]]; then
    LOCAL_MEDIA="$LOCAL_ROOT/documento/media"
  else
    echo "Nessuna cartella media (htdocs/media, gesper/media, documento/media). Imposta GESPER_LOCAL_MEDIA=" >&2
    exit 1
  fi
fi

if [[ -n "${GESPER_REMOTE_DATA_ROOT:-}" ]]; then
  RDATA="${GESPER_REMOTE_DATA_ROOT}"
  REMOTE_DB="${RDATA}/db.sqlite3"
  REMOTE_MEDIA="${RDATA}/media"
else
  REMOTE_DB="${GESPER_REMOTE_DB:-/var/www/gesper/documento/db.sqlite3}"
  REMOTE_MEDIA="${GESPER_REMOTE_MEDIA:-/var/www/gesper/documento/media}"
fi

if [[ ! -f "$LOCAL_DB" ]]; then
  echo "File DB locale inesistente: $LOCAL_DB" >&2
  exit 1
fi
if [[ ! -d "$LOCAL_MEDIA" ]]; then
  echo "Cartella media locale inesistente: $LOCAL_MEDIA" >&2
  exit 1
fi

SSH_BASE=(ssh -o ConnectTimeout=30)
[[ -n "${GESPER_SSH_IDENTITY:-}" ]] && SSH_BASE+=(-i "${GESPER_SSH_IDENTITY}" -o IdentitiesOnly=yes)
if [[ -z "${GESPER_SSH_NO_TTY:-}" ]] && [[ -t 0 ]]; then
  SSH_BASE+=(-t)
fi
SSH_BASE+=("$HOST")
if [[ -n "${GESPER_SSH_IDENTITY:-}" ]]; then
  export RSYNC_RSH="ssh -i ${GESPER_SSH_IDENTITY} -o IdentitiesOnly=yes"
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

DRY=0
[[ -n "${GESPER_RSYNC_DRY_RUN:-}" ]] && DRY=1

echo "== Sincronizzazione completa → $HOST =="
echo "  Locale DB:      $LOCAL_DB"
echo "  Locale media:   $LOCAL_MEDIA"
echo "  Remoto DB:      $REMOTE_DB"
echo "  Remoto media:   $REMOTE_MEDIA"
echo "  Progetto remoto: $REMOTE_PROJ"
[[ "$DRY" -eq 1 ]] && echo "  (modalità DRY-RUN)"
echo ""

if [[ -z "${GESPER_SKIP_LOCAL_CHECK:-}" ]]; then
  echo "== check locale =="
  if [[ -x "$LOCAL_ROOT/.venv/bin/python" ]]; then
    (cd "$LOCAL_ROOT" && "$LOCAL_ROOT/.venv/bin/python" manage.py check)
  else
    echo "(salto: nessun .venv/bin/python; usa GESPER_SKIP_LOCAL_CHECK=1)" >&2
  fi
fi

if [[ "$DRY" -eq 1 ]]; then
  echo "== dry-run: rsync codice =="
  rsync -avzn "${RSYNC_EXCL[@]}" "$LOCAL_ROOT/" "$HOST:$REMOTE_PROJ/" || true
  echo "== dry-run: rsync media =="
  MFLAGS=(-avzn)
  [[ -n "${GESPER_MEDIA_SYNC_DELETE:-}" ]] && MFLAGS+=("--delete")
  rsync "${MFLAGS[@]}" "$LOCAL_MEDIA/" "$HOST:$REMOTE_MEDIA/" || true
  echo "== dry-run: DB =="
  echo "  rsync $LOCAL_DB → $HOST:$REMOTE_DB"
  exit 0
fi

echo "== backup su server =="
# shellcheck disable=SC2029
"${SSH_BASE[@]}" "set -e; TS=\$(date -u +%Y%m%d_%H%M%S); BK=\"${REMOTE_PROJ}/backup_full_sync_\$TS\"; mkdir -p \"\$BK\"; \
  if [ -f \"${REMOTE_DB}\" ]; then cp -a \"${REMOTE_DB}\" \"\$BK/db.sqlite3.remoto\" && echo \"Backup DB: \$BK/db.sqlite3.remoto\"; fi; \
  if [ -d \"${REMOTE_MEDIA}\" ]; then rsync -a \"${REMOTE_MEDIA}/\" \"\$BK/media_remoto/\" && echo \"Backup media: \$BK/media_remoto/\"; fi; \
  echo \"Directory backup: \$BK\""

echo "== stop gesper =="
"${SSH_BASE[@]}" "systemctl stop gesper || true"
sleep 1

echo "== rsync codice =="
RSYNC=(rsync -avz)
RSYNC+=("${RSYNC_EXCL[@]}")
RSYNC+=("$LOCAL_ROOT/" "$HOST:$REMOTE_PROJ/")
"${RSYNC[@]}"

echo "== parent directory e copia DB =="
# shellcheck disable=SC2029
"${SSH_BASE[@]}" "install -d -m 0755 \"\$(dirname \"${REMOTE_DB}\")\""
rsync -avz --chmod=Du=rwx,Dgo=rx,Fu=rw,Fgo=r \
  "$LOCAL_DB" "$HOST:$REMOTE_DB"

MEDIA_FLAGS=(-avz)
[[ -n "${GESPER_MEDIA_SYNC_DELETE:-}" ]] && MEDIA_FLAGS+=("--delete")
echo "== rsync media =="
# shellcheck disable=SC2029
"${SSH_BASE[@]}" "install -d -m 0755 \"${REMOTE_MEDIA}\""
rsync "${MEDIA_FLAGS[@]}" "$LOCAL_MEDIA/" "$HOST:$REMOTE_MEDIA/"

if [[ -n "${GESPER_REMOTE_CHOWN_USER:-}" ]]; then
  # es. www-data:www-data
  # shellcheck disable=SC2029
  "${SSH_BASE[@]}" "chown -R '${GESPER_REMOTE_CHOWN_USER}' \"${REMOTE_DB}\" \"${REMOTE_MEDIA}\" 2>/dev/null || true"
fi

REMOTE_PY="${REMOTE_PROJ}/.venv/bin/python"
REMOTE_PIP="${REMOTE_PROJ}/.venv/bin/pip"
REMOTE_SH="set -euo pipefail; cd '${REMOTE_PROJ}'"
REMOTE_SH+="; if [[ -f /etc/gesper.env ]]; then set -a; source /etc/gesper.env; set +a; fi"
if [[ -z "${GESPER_SKIP_PIP:-}" ]]; then
  REMOTE_SH+="; '${REMOTE_PIP}' install -r requirements.txt"
fi
REMOTE_SH+="; DJANGO_SETTINGS_MODULE='${DJANGO_SETTINGS}' '${REMOTE_PY}' manage.py migrate --noinput"
REMOTE_SH+="; DJANGO_SETTINGS_MODULE='${DJANGO_SETTINGS}' '${REMOTE_PY}' manage.py collectstatic --noinput"
REMOTE_SH+="; systemctl start gesper; systemctl is-active gesper"

echo "== $HOST: pip (se attivo) / migrate / collectstatic / start gesper =="
# shellcheck disable=SC2029
"${SSH_BASE[@]}" "bash -lc $(printf %q "$REMOTE_SH")"

echo ""
echo "OK. Verifica: bash deploy/verify-public-endpoints.sh"
echo "Controlla che in /etc/gesper.env il path DB/media (GESPER_DATA_ROOT) coincida con i percorsi usati sopra."
