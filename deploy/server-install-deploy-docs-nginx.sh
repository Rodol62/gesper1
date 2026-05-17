#!/usr/bin/env bash
# Eseguire sulla VPS gesper1 come root, dopo aver sincronizzato il repo in /var/www/gesper.
# Inserisce "include … nginx-snippet-deploy-docs.conf" nel primo blocco che ha listen 443 ssl,
# immediatamente prima della prima "location / {" di quel file.
#
#   sudo bash /var/www/gesper/deploy/server-install-deploy-docs-nginx.sh
#
set -euo pipefail

CONF="${NGINX_GESPER_SITE:-/etc/nginx/sites-available/gesper1.conf}"
SNIP="/var/www/gesper/deploy/nginx-snippet-deploy-docs.conf"
INCLUDE_LINE='    include /var/www/gesper/deploy/nginx-snippet-deploy-docs.conf;'

if [[ ! -f "$SNIP" ]]; then
  echo "Manca $SNIP — sincronizza il progetto in /var/www/gesper" >&2
  exit 1
fi
if [[ ! -f "$CONF" ]]; then
  echo "Manca $CONF — imposta NGINX_GESPER_SITE se il vhost ha altro nome" >&2
  exit 1
fi
if [[ ! -f /etc/nginx/.htpasswd-gesper-deploy ]]; then
  echo "Manca /etc/nginx/.htpasswd-gesper-deploy — crea con htpasswd (vedi PROCEDURA_DEPLOY.md)" >&2
  exit 1
fi

if grep -qE 'nginx-snippet-deploy-docs\.conf|location \^~ /deploy-docs/' "$CONF"; then
  echo "deploy-docs già configurato in $CONF — nessuna modifica."
else
  cp -a "$CONF" "${CONF}.bak.$(date +%Y%m%d%H%M%S)"
  # Riconosce listen 443 ssl sia IPv4 sia [::]:443 (prima il vecchio script non matchava solo-IPv6).
  awk -v inc="$INCLUDE_LINE" '
    /^[[:space:]]*listen[[:space:]]+(\[::\]:)?443[[:space:]]+ssl/ { in443=1 }
    in443 && /^[[:space:]]*location[[:space:]]+\/[[:space:]]*\{/ && !inserted {
      print inc
      inserted=1
    }
    { print }
  ' "$CONF" > "${CONF}.new"
  if ! grep -qF "nginx-snippet-deploy-docs.conf" "${CONF}.new"; then
    echo "ERRORE: non inserito include. In $CONF serve una riga listen … 443 … ssl e poi location / {" >&2
    echo "Apri il file e aggiungi a mano, prima di location / {:" >&2
    echo "    include /var/www/gesper/deploy/nginx-snippet-deploy-docs.conf;" >&2
    rm -f "${CONF}.new"
    exit 1
  fi
  mv "${CONF}.new" "$CONF"
  echo "Aggiornato $CONF (backup .bak.* creato)."
fi

nginx -t
systemctl reload nginx
echo "OK. Test: curl -sSI https://gesper1.plazapretoria.it/deploy-docs/ | grep -i www-authenticate"
