#!/usr/bin/env bash
# Eseguire SUL SERVER (con sudo) prima di modificare la config Nginx del sito GESPER.
#
#   sudo bash deploy/backup-nginx-site.sh
#   sudo NGINX_SITE_FILE=/etc/nginx/sites-enabled/gesper bash deploy/backup-nginx-site.sh
#
set -euo pipefail
if [[ "${EUID:-0}" -ne 0 ]]; then
  echo "Eseguire come root: sudo bash $0" >&2
  exit 1
fi
SITE="${NGINX_SITE_FILE:-/etc/nginx/sites-enabled/gesper}"
if [[ ! -f "$SITE" ]]; then
  echo "File non trovato: $SITE" >&2
  exit 1
fi
TS="$(date +%Y%m%d%H%M%S)"
DEST="${SITE}.bak.${TS}"
cp -a "$SITE" "$DEST"
echo "OK: backup salvato in $DEST"
