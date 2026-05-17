#!/usr/bin/env bash
# Verifica da Internet (senza SSH) che DNS + Nginx siano coerenti con deploy/PROCEDURA_DEPLOY.md
#
# Opzionale:
#   GESPER_EXPECT_GESPER_A=1.2.3.4   confronta dig +short gesper con IP VPS atteso
#   GESPER_EXPECT_WWW_A=1.2.3.4      confronta dig +short www (hosting o VPS a seconda della modalità)
#   WWW_ROOT=https://www.alt.it      override host www
#   GESPER_HOST=https://gesper...    override sottodominio GESPER
#
set -uo pipefail

WWW_ROOT="${WWW_ROOT:-https://www.plazapretoria.it}"
WWW_GESPER="${WWW_GESPER:-${WWW_ROOT%/}/gesper/}"
GESP_ROOT="${GESPER_HOST:-https://gesper1.plazapretoria.it}"
GESP_PWA="${GESP_PWA:-${GESP_ROOT%/}/gesper-app/}"

hdr() { printf '\n=== %s ===\n' "$1"; }

code_server() {
  local url=$1
  curl -sS -m 15 -D - -o /dev/null "$url" 2>&1 | awk 'BEGIN{IGNORECASE=1} /^HTTP/{c=$2} /^[Ss]erver:/{gsub(/\r/,"",$2); s=$2} END{print c " server=" (s?s:"?")}'
}

WWW_HOST="${WWW_ROOT#https://}"
WWW_HOST="${WWW_HOST#http://}"
GESP_HOST="${GESP_ROOT#https://}"
GESP_HOST="${GESP_HOST#http://}"

hdr "DNS gesper (record A) — primario in modalità separata"
if command -v dig >/dev/null 2>&1; then
  dig +short "$GESP_HOST" A 2>/dev/null | sed '/^$/d' || true
  if [[ -n "${GESPER_EXPECT_GESPER_A:-}" ]]; then
    got=$(dig +short "$GESP_HOST" A 2>/dev/null | head -1 | tr -d '\r')
    if [[ "$got" == "${GESPER_EXPECT_GESPER_A}" ]]; then
      echo "OK: A gesper coincide con GESPER_EXPECT_GESPER_A ($got)"
    else
      echo "ATTENZIONE: A gesper atteso ${GESPER_EXPECT_GESPER_A}, risultato dig (prima riga): ${got:-vuoto}"
    fi
  fi
else
  echo "(dig non installato — salto)"
fi

hdr "${GESP_ROOT}/accounts/login/"
line=$(code_server "${GESP_ROOT}/accounts/login/")
echo "$line"
login_body=$(curl -sS -m 20 "${GESP_ROOT}/accounts/login/" 2>/dev/null || true)
if printf '%s' "$login_body" | grep -q 'GESPER nginx OK (HTTPS)'; then
  echo "→ ERRORE: Nginx risponde con il placeholder di test, non con Django. Nel server { 443 } il blocco location / deve fare proxy a Gunicorn (es. proxy_pass http://127.0.0.1:8000;), vedi deploy/nginx-gesper-vps-standalone.conf. Verifica: systemctl status gesper"
elif printf '%s' "$login_body" | grep -qE '<!DOCTYPE[[:space:]]+html|<html[[:space:]]'; then
  echo "→ OK: pagina HTML Django (login) raggiungibile su gesper.*"
elif echo "$line" | grep -qi 'nginx'; then
  echo "→ Attenzione: Nginx c’è ma la risposta non sembra la login Django — controlla Gunicorn e la config location /"
else
  echo "→ Verifica TLS / DNS per gesper.*"
fi

hdr "${GESP_PWA}"
line=$(code_server "$GESP_PWA")
echo "$line"
if echo "$line" | grep -qi 'nginx'; then
  echo "→ OK: PWA servita da nginx"
fi

hdr "DNS www (record A)"
if command -v dig >/dev/null 2>&1; then
  dig +short "$WWW_HOST" A 2>/dev/null | sed '/^$/d' || true
  if [[ -n "${GESPER_EXPECT_WWW_A:-}" ]]; then
    got=$(dig +short "$WWW_HOST" A 2>/dev/null | head -1 | tr -d '\r')
    if [[ "$got" == "${GESPER_EXPECT_WWW_A}" ]]; then
      echo "OK: A www coincide con GESPER_EXPECT_WWW_A ($got)"
    else
      echo "ATTENZIONE: A www atteso ${GESPER_EXPECT_WWW_A}, risultato dig (prima riga): ${got:-vuoto}"
    fi
  fi
  hdr "DNS www (AAAA)"
  aaaa=$(dig +short "$WWW_HOST" AAAA 2>/dev/null | sed '/^$/d' | head -3)
  if [[ -z "${aaaa:-}" ]]; then
    echo "(nessun AAAA — ok se vuoi solo IPv4)"
  else
    echo "$aaaa"
    echo "Nota: con split Nginx, AAAA deve essere coerente con la VPS (o assente)."
  fi
  hdr "DNS apex (opzionale)"
  apex="${WWW_HOST#www.}"
  if [[ "$apex" != "$WWW_HOST" ]]; then
    echo "A @ ($apex):"
    dig +short "$apex" A 2>/dev/null | sed '/^$/d' || true
  fi
fi

hdr "${WWW_ROOT}/ (home www)"
echo "$(code_server "${WWW_ROOT}/")"

hdr "${WWW_GESPER} (legacy / split — opzionale)"
line=$(code_server "$WWW_GESPER")
echo "$line"
if echo "$line" | grep -qi 'aruba-proxy'; then
  echo "→ In modalità separata (www su hosting): aruba-proxy su /gesper/ è atteso. Con split Nginx (www→VPS) invece attendi Server: nginx."
elif echo "$line" | grep -qi 'nginx'; then
  echo "→ OK: Server nginx (split: traffico www verso la VPS per /gesper/)."
else
  echo "→ Controlla manualmente."
fi

echo ""
echo "Fine. Modalità separata: A gesper1 (o gesper) → VPS; link GESPER = https://${GESP_HOST}/"
echo "Split Nginx: A www → VPS; vedi deploy/nginx-gesper-production-split.conf e PROCEDURA_DEPLOY.md §9."
