#!/usr/bin/env bash
# Eseguire sulla VPS come root, dalla root del progetto già deployato (es. cd /var/www/gesper).
# Crea cartelle dati demo, copia esempi systemd/env/nginx solo se mancano (non sovrascrive).
#
#   sudo bash deploy/bootstrap-gesper-demo-vps.sh
#
# Poi: editare /etc/gesper-demo.env, certbot per il sottodominio, nginx -t, migrate/seed (vedi PROCEDURA_DEPLOY §0.35).

set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

if [[ "$(id -u)" -ne 0 ]]; then
  echo "Esegui come root: sudo bash deploy/bootstrap-gesper-demo-vps.sh" >&2
  exit 1
fi

DEMO_ROOT="${GESPER_DEMO_ROOT:-/var/www/gesper-demo}"
DOC="${DEMO_ROOT}/documento"
mkdir -p "${DOC}/archivio" "${DEMO_ROOT}/logs"
echo "OK: create ${DOC} e ${DEMO_ROOT}/logs"

if [[ ! -f /etc/gesper-demo.env ]]; then
  cp "${ROOT}/deploy/gesper-demo.env.example" /etc/gesper-demo.env
  chmod 600 /etc/gesper-demo.env
  echo "Creato /etc/gesper-demo.env — MODIFICARE host, DJANGO_SECRET_KEY, password demo, GESPER_DATA_ROOT se diverso da ${DEMO_ROOT}/documento"
else
  echo "Esiste già /etc/gesper-demo.env (non toccato)."
fi

if [[ ! -f /etc/systemd/system/gesper-demo.service ]]; then
  cp "${ROOT}/deploy/gesper-demo.service.example" /etc/systemd/system/gesper-demo.service
  systemctl daemon-reload
  echo "Installato gesper-demo.service — abilitare dopo migrate/seed: systemctl enable --now gesper-demo"
else
  echo "Esiste già gesper-demo.service (non toccato)."
fi

NGX_DST="/etc/nginx/sites-available/gesper-demo"
if [[ ! -f "${NGX_DST}" ]]; then
  # Versione solo HTTP: ``nginx -t`` ok senza certificati; dopo DNS + ``certbot --nginx`` sostituire
  # con ``nginx-gesper-demo-vhost.example.conf`` (HTTPS) se serve.
  cp "${ROOT}/deploy/nginx-gesper-demo-80-only.example.conf" "${NGX_DST}"
  echo "Copiato ${NGX_DST} (solo HTTP) — adattare server_name, poi:"
  echo "  ln -sf ${NGX_DST} /etc/nginx/sites-enabled/"
  echo "  nginx -t && systemctl reload nginx"
  echo "  certbot --nginx -d TUO_DOMINIO_DEMO   # aggiunge TLS; poi opzionale: vhost HTTPS completo da repo"
else
  echo "Esiste già ${NGX_DST} (non toccato)."
fi

echo ""
echo "Prossimi comandi Django (stesso venv di /var/www/gesper):"
echo "  set -a; source /etc/gesper-demo.env; set +a"
echo "  cd ${ROOT} && ./.venv/bin/python manage.py migrate && ./.venv/bin/python manage.py gesper_sandbox_migrate && ./.venv/bin/python manage.py gesper_sandbox_seed"
echo "Opzionale dati + anon: gesper_sandbox_clone_operativo --yes ; gesper_sandbox_anonymize --yes"
