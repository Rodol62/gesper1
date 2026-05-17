#!/usr/bin/env bash
# Preflight sulla VPS (gesper1): Tappa 1 — Gunicorn, Nginx, spazio disco, log.
# Uso: ssh root@gesper1…  →  cd /var/www/gesper && bash deploy/check-gesper-vps-app.sh
set -u

echo "=== nginx -t (sintassi config) ==="
if nginx -t 2>&1; then
  echo "OK: nginx -t"
else
  echo "ERRORE: correggere la config Nginx prima di qualsiasi reload."
fi
echo ""

echo "=== Spazio disco (root e /var/www) ==="
df -h / 2>/dev/null | tail -1
[ -d /var/www ] && df -h /var/www 2>/dev/null | tail -1
echo "(se Use% oltre ~85%: pianificare pulizia o espansione)"
echo ""

echo "=== systemctl gesper ==="
if systemctl is-active gesper >/dev/null 2>&1; then
  echo "OK: gesper è active"
else
  echo "ERRORE: gesper non è active — systemctl status gesper / journalctl -u gesper"
fi
echo ""

tmpf=$(mktemp)
trap 'rm -f "$tmpf"' EXIT

echo "=== HTTP verso Gunicorn locale: GET /accounts/login/ (atteso HTML Django) ==="
if curl -sS -m 12 -o "$tmpf" http://127.0.0.1:8000/accounts/login/ 2>/dev/null; then
  if head -c 500 "$tmpf" | grep -qE '<!DOCTYPE[[:space:]]+html|<html[[:space:]]'; then
    echo "OK: risposta HTML su 127.0.0.1:8000"
  else
    echo "ATTENZIONE: su 8000 non arriva HTML tipico login. Prime righe:"
    head -3 "$tmpf"
  fi
else
  echo "ERRORE: connessione a 127.0.0.1:8000 fallita — Gunicorn non in ascolto o crash."
fi
echo ""

GESP_HOST="${GESPER_NGINX_HOST:-gesper1.plazapretoria.it}"
echo "=== HTTPS verso Nginx locale (-k): GET /accounts/login/ con Host: $GESP_HOST ==="
if command -v curl >/dev/null 2>&1; then
  if curl -skS -m 12 -o "$tmpf" -H "Host: $GESP_HOST" "https://127.0.0.1/accounts/login/" 2>/dev/null; then
    if head -c 400 "$tmpf" | grep -qE '<!DOCTYPE[[:space:]]+html|<html[[:space:]]'; then
      echo "OK: Nginx (443) inoltra a Django — coerente con Gunicorn"
    elif head -c 120 "$tmpf" | grep -q 'GESPER nginx OK'; then
      echo "ERRORE: Nginx risponde ancora con il placeholder (location / su 443 non fa proxy a 8000)."
      echo "→ Sostituire quel blocco con: deploy/snippets/gesper1-https-location-root-proxy.conf (poi nginx -t && reload)."
    else
      echo "ATTENZIONE: risposta inattesa. Prime righe:"
      head -3 "$tmpf"
    fi
  else
    echo "(curl https://127.0.0.1 fallito — verificare Nginx 443 o certificato)"
  fi
fi
echo ""

echo "=== Nginx: stringa 'GESPER nginx OK' in sites-enabled? ==="
if grep -R "GESPER nginx OK" /etc/nginx/sites-enabled/ 2>/dev/null; then
  echo "→ Incolla al posto del location / in HTTPS lo snippet: deploy/snippets/gesper1-https-location-root-proxy.conf"
else
  echo "(nessun match in grep — se il problema resta, controlla include o file fuori da sites-enabled)"
fi
echo ""

echo "=== journalctl -u gesper (ultime 20 righe) ==="
if command -v journalctl >/dev/null 2>&1; then
  journalctl -u gesper -n 20 --no-pager 2>/dev/null || echo "(journalctl non disponibile o unit name diverso)"
else
  echo "(journalctl assente)"
fi
echo ""
echo "=== Tappa 1: check manuale aggiuntivo (5 min navigazione) ==="
echo "Aprire https://.../ e /gesper-app/; se log sopra mostra errori ripetuti (Traceback, OOM, timeout) fermarsi e indagare."
echo "Fine script."
