#!/usr/bin/env bash
# Installa / aggiorna il vhost Nginx per gesper1.plazapretoria.it dal file versionato nel repo.
# Sostituisce il contenuto di sites-available con deploy/nginx-gesper-vps-standalone.conf
# (proxy Django su 8000, static, media, PWA, /deploy-docs/ con Basic Auth).
#
# Prerequisiti sulla VPS:
#   - Progetto in GESPER_REPO_ROOT (default /var/www/gesper), dopo git pull
#   - /etc/nginx/.htpasswd-gesper-deploy (stesso requisito di server-install-deploy-docs-nginx.sh)
#   - Certificati Let’s Encrypt nei path indicati nel file repo (o nginx -t fallirà e si ripristina il backup)
#
# Uso (root):
#   cd /var/www/gesper && git pull
#   sudo bash deploy/install-nginx-gesper1-vhost-from-repo.sh
#
# Opzionali:
#   GESPER_REPO_ROOT=/percorso/gesper
#   NGINX_GESPER_SITE=/etc/nginx/sites-available/gesper1.conf
#   NGINX_GESPER_ENABLED_NAME=gesper1.conf   # nome del symlink in sites-enabled
#   GESPER_ALLOW_NO_HTPASSWD=1               # solo se sai cosa stai facendo: salta check htpasswd
#
set -euo pipefail

if [[ "${EUID:-0}" -ne 0 ]]; then
  echo "Eseguire come root: sudo bash $0" >&2
  exit 1
fi

REPO="${GESPER_REPO_ROOT:-/var/www/gesper}"
SRC="${REPO}/deploy/nginx-gesper-vps-standalone.conf"
DEST="${NGINX_GESPER_SITE:-/etc/nginx/sites-available/gesper1.conf}"
ENABLED_DIR="/etc/nginx/sites-enabled"
ENABLED_NAME="${NGINX_GESPER_ENABLED_NAME:-gesper1.conf}"
ENABLED_PATH="${ENABLED_DIR}/${ENABLED_NAME}"

if [[ ! -f "$SRC" ]]; then
  echo "Manca $SRC — esegui git pull in $REPO o imposta GESPER_REPO_ROOT." >&2
  exit 1
fi

if [[ ! -f /etc/nginx/.htpasswd-gesper-deploy && -z "${GESPER_ALLOW_NO_HTPASSWD:-}" ]]; then
  echo "Manca /etc/nginx/.htpasswd-gesper-deploy (necessario per /deploy-docs/ nello stesso vhost)." >&2
  echo "Crea l’utente, poi rilancia lo script. Esempio:" >&2
  echo "  apt-get update && apt-get install -y apache2-utils" >&2
  echo "  htpasswd -c /etc/nginx/.htpasswd-gesper-deploy TUO_UTENTE" >&2
  echo "Oppure, se /deploy-docs/ non ti serve ancora, export GESPER_ALLOW_NO_HTPASSWD=1 (nginx -t potrebbe fallire)." >&2
  exit 1
fi

TS="$(date +%Y%m%d%H%M%S)"
if [[ -f "$DEST" ]]; then
  cp -a "$DEST" "${DEST}.bak.${TS}"
  echo "Backup: ${DEST}.bak.${TS}"
else
  echo "Nota: $DEST non esisteva — verrà creato."
fi

cp -a "$SRC" "$DEST"
echo "Installato $DEST da $SRC"

if [[ ! -e "$ENABLED_PATH" ]]; then
  ln -s "$DEST" "$ENABLED_PATH"
  echo "Creato symlink $ENABLED_PATH -> $DEST"
else
  echo "Symlink/file $ENABLED_PATH già presente — non modifico."
fi

if ! nginx -t; then
  echo "" >&2
  echo "ERRORE: nginx -t fallito. Ripristino il file precedente (se c’era backup)." >&2
  if [[ -f "${DEST}.bak.${TS}" ]]; then
    cp -a "${DEST}.bak.${TS}" "$DEST"
    if nginx -t; then
      echo "Ripristinato ${DEST} dal backup. Nessun reload eseguito." >&2
    else
      echo "ATTENZIONE: neppure il backup passa nginx -t — controlla la config a mano." >&2
    fi
  fi
  exit 1
fi

systemctl reload nginx
echo "OK: Nginx ricaricato. Verifica: curl -sI https://gesper1.plazapretoria.it/accounts/login/"
