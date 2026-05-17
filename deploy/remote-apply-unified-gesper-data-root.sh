#!/usr/bin/env bash
# Applica su gesper1 la radice dati unificata (Django + Nginx allineati):
#   GESPER_DATA_ROOT=/var/www/gesper/documento
#   → DB:  .../db.sqlite3
#   → media: .../media/  (Nginx: alias /media/ → stesso path)
#   → archivio: .../archivio/
#
# Cosa fa (dopo backup):
#  - ferma gesper
#  - unisce sotto la radice i file da /var/www/media/ (se esiste) e copia il DB attuale da /var/www/gesper/db.sqlite3
#  - imposta o aggiorna GESPER_DATA_ROOT in /etc/gesper.env; commenta GESPER_MEDIA_ROOT se presente
#  - aggiorna in sites-enabled l'alias Nginx /media/ verso .../gesper/documento/media/ (pattern legacy)
#  - nginx -t, reload, avvio gesper, migrate
#
# NON elimina /var/www/media/ (puoi archiviarla a mano dopo verifica). NON invia .env da locale.
#
# Uso (da Mac, nella cartella del repo o con PATH noto):
#   GESPER_UNIFIED_CONFIRM=1 GESPER_SSH_NO_TTY=1 ./deploy/remote-apply-unified-gesper-data-root.sh
#
# Opzionali:
#   GESPER_UNIFIED_DATA_ROOT=/var/www/gesper/documento
#   GESPER_DEPLOY_HOST=root@gesper1.plazapretoria.it
#   GESPER_REMOTE_NGINX_SITE=/etc/nginx/sites-available/gesper1.conf  # default su gesper1
#   GESPER_UNIFIED_DRY_RUN=1    # mostra comandi, non li esegue (tranne test connessione)
#
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
LOCAL_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
HOST="${GESPER_DEPLOY_HOST:-root@gesper1.plazapretoria.it}"
DATA_ROOT="${GESPER_UNIFIED_DATA_ROOT:-/var/www/gesper/documento}"
LEGACY_MEDIA="/var/www/media"
LEGACY_DB="/var/www/gesper/db.sqlite3"
NGINX_SITE="${GESPER_REMOTE_NGINX_SITE:-/etc/nginx/sites-available/gesper1.conf}"
ENVFILE="/etc/gesper.env"
REMOTE_PROJ="${GESPER_REMOTE_PROJECT_DIR:-/var/www/gesper}"

if [[ "${GESPER_UNIFIED_CONFIRM:-}" != "1" ]]; then
  echo "Imposta GESPER_UNIFIED_CONFIRM=1 per eseguire la migrazione unificata su $HOST" >&2
  exit 1
fi

SSH_BASE=(ssh -o ConnectTimeout=30)
[[ -n "${GESPER_SSH_IDENTITY:-}" ]] && SSH_BASE+=(-i "${GESPER_SSH_IDENTITY}" -o IdentitiesOnly=yes)
if [[ -z "${GESPER_SSH_NO_TTY:-}" ]] && [[ -t 0 ]]; then
  SSH_BASE+=(-t)
fi
SSH_BASE+=("$HOST")

REMOTE_SCRIPT=$(cat <<'EOS'
set -euo pipefail
DATA_ROOT="__DATA_ROOT__"
LEGACY_MEDIA="__LEG_M__"
LEGACY_DB="__LEG_DB__"
NGINX_SITE="__NGX__"
ENVFILE="__ENV__"
REMOTE_PROJ="__RPROJ__"
DRY="__DRY__"

ts=$(date -u +%Y%m%d_%H%M%S)
BK="${REMOTE_PROJ}/backup_unified_data_root_${ts}"
[[ "$DRY" == "1" ]] && { echo "DRY-RUN: uscita prima delle modifiche"; echo "Esempio backup: $BK"; exit 0; }

echo "== backup in $BK =="
mkdir -p "$BK"
[[ -d "$DATA_ROOT" ]] && cp -a "$DATA_ROOT" "$BK/documento_prima" 2>/dev/null || true
[[ -f "$LEGACY_DB" ]] && cp -a "$LEGACY_DB" "$BK/db.gesper_prima.sqlite3" && echo "Snapshot DB legato a gesper/ OK"
[[ -d "$LEGACY_MEDIA" ]] && rsync -a "$LEGACY_MEDIA/" "$BK/media_www_prima/" && echo "Snapshot /var/www/media OK"

echo "== stop gesper =="
systemctl stop gesper || true
sleep 1

echo "== crea albero $DATA_ROOT =="
install -d -m 0755 "$DATA_ROOT"
install -d -m 0755 "$DATA_ROOT/media"
install -d -m 0755 "$DATA_ROOT/archivio"

if [[ -d "$LEGACY_MEDIA" ]]; then
  echo "== merge media: $LEGACY_MEDIA → $DATA_ROOT/media/ =="
  rsync -a "$LEGACY_MEDIA/" "$DATA_ROOT/media/"
fi

if [[ -f "$LEGACY_DB" ]]; then
  echo "== copia DB attuale in $DATA_ROOT/db.sqlite3 =="
  install -D -m 0644 "$LEGACY_DB" "$DATA_ROOT/db.sqlite3"
else
  echo "ATTENZIONE: $LEGACY_DB non trovato; il DB in $DATA_ROOT resta invariato o va creato da migrate" >&2
fi

if [[ -f "$ENVFILE" ]]; then
  if grep -q '^[[:space:]]*GESPER_DATA_ROOT=' "$ENVFILE" 2>/dev/null; then
    sed -i "s#^[[:space:]]*GESPER_DATA_ROOT=.*#GESPER_DATA_ROOT=${DATA_ROOT}#" "$ENVFILE"
  else
    echo "" >> "$ENVFILE"
    echo "# Radice unificata (applicata da remote-apply-unified-gesper-data-root.sh $ts)" >> "$ENVFILE"
    echo "GESPER_DATA_ROOT=${DATA_ROOT}" >> "$ENVFILE"
  fi
  if grep -q '^[[:space:]]*GESPER_MEDIA_ROOT=' "$ENVFILE" 2>/dev/null; then
    sed -i '/^[[:space:]]*GESPER_MEDIA_ROOT=/s/^/# /' "$ENVFILE" 2>/dev/null || true
  fi
else
  echo "GESPER_DATA_ROOT=${DATA_ROOT}" > "$ENVFILE"
  chmod 0600 "$ENVFILE"
fi

if [[ -f "$NGINX_SITE" ]]; then
  echo "== aggiorna alias /media/ in $NGINX_SITE =="
  sed -i 's#alias[[:space:]]\+/var/www/media/;#alias '"${DATA_ROOT//\//\\/}"'/media/;#g' "$NGINX_SITE" || true
  sed -i 's#alias[[:space:]]\+/var/www/documento/media/;#alias '"${DATA_ROOT//\//\\/}"'/media/;#g' "$NGINX_SITE" || true
else
  echo "Nginx: file $NGINX_SITE assente; aggiornare a mano location /media/ → alias ${DATA_ROOT}/media/" >&2
fi

echo "== nginx -t =="
nginx -t

echo "== reload nginx =="
systemctl reload nginx

echo "== start gesper + migrate + collectstatic =="
REMOTE_PY="${REMOTE_PROJ}/.venv/bin/python"
DJANGO_SETTINGS=settings_production
systemctl start gesper
sleep 1
( cd "$REMOTE_PROJ" && set -a; [[ -f "$ENVFILE" ]] && . "$ENVFILE"; set +a; DJANGO_SETTINGS_MODULE=$DJANGO_SETTINGS "$REMOTE_PY" manage.py migrate --noinput )
( cd "$REMOTE_PROJ" && set -a; [[ -f "$ENVFILE" ]] && . "$ENVFILE"; set +a; DJANGO_SETTINGS_MODULE=$DJANGO_SETTINGS "$REMOTE_PY" manage.py collectstatic --noinput ) || true
systemctl restart gesper
systemctl is-active gesper
echo "OK. Verifica: https://gesper1.plazapretoria.it/  e pannello Archivio documenti (percorsi coerenti)."
echo "Suggerimento: dopo controlli, rinomina o svuota $LEGACY_MEDIA (backup già sotto $BK) per evitare doppioni."
EOS
)
REMOTE_SCRIPT="${REMOTE_SCRIPT//__DATA_ROOT__/${DATA_ROOT}}"
REMOTE_SCRIPT="${REMOTE_SCRIPT//__LEG_M__/${LEGACY_MEDIA}}"
REMOTE_SCRIPT="${REMOTE_SCRIPT//__LEG_DB__/${LEGACY_DB}}"
REMOTE_SCRIPT="${REMOTE_SCRIPT//__NGX__/${NGINX_SITE}}"
REMOTE_SCRIPT="${REMOTE_SCRIPT//__ENV__/${ENVFILE}}"
REMOTE_SCRIPT="${REMOTE_SCRIPT//__RPROJ__/${REMOTE_PROJ}}"
REMOTE_SCRIPT="${REMOTE_SCRIPT//__DRY__/${GESPER_UNIFIED_DRY_RUN:-0}}"

echo "== Connessione $HOST, radice $DATA_ROOT =="
# shellcheck disable=SC2029
"${SSH_BASE[@]}" "bash -s" <<< "$REMOTE_SCRIPT"

if [[ -z "${GESPER_UNIFIED_DRY_RUN:-}" ]]; then
  echo "== prossimo passo: deploy codice + vhost da repo (se il server non è già allineato) =="
  echo "  GESPER_SSH_NO_TTY=1 ./deploy/remote-rsync-django-gesper1.sh"
  echo "  ./deploy/remote-apply-nginx-gesper1.sh"
fi
