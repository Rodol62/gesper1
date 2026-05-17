#!/usr/bin/env bash
# Tappa 2 — Coerenza dati su VPS: env, MEDIA_ROOT, DB, allineamento path Documento ↔ file.
# Eseguire sul server: cd /var/www/gesper && bash deploy/tappa2-check-dati-vps.sh
set -euo pipefail
PROJ="${GESPER_REMOTE_PROJECT_DIR:-/var/www/gesper}"
cd "$PROJ"

if [[ -f /etc/gesper.env ]]; then
  set -a
  # shellcheck source=/dev/null
  source /etc/gesper.env
  set +a
else
  echo "ATTENZIONE: /etc/gesper.env non trovato" >&2
fi

export DJANGO_SETTINGS_MODULE="${DJANGO_SETTINGS_MODULE:-settings_production}"
PY="${PROJ}/.venv/bin/python"

echo "=== GESPER_DATA_ROOT (da env) ==="
echo "${GESPER_DATA_ROOT:-<non impostato>}"
echo ""

echo "=== Django: DATABASE + MEDIA_ROOT ==="
"$PY" -c "
import os
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'settings_production')
import django
django.setup()
from django.conf import settings
print('MEDIA_ROOT =', settings.MEDIA_ROOT)
print('DB NAME    =', settings.DATABASES['default']['NAME'])
gdr = getattr(settings, 'GESPER_DATA_ROOT', None)
print('GESPER_DATA_ROOT (settings) =', gdr)
"

echo "=== Albero atteso: DB e media sotto stessa radice? ==="
"$PY" <<'PY'
import os
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "settings_production")
import django
django.setup()
from pathlib import Path
from django.conf import settings
db = Path(str(settings.DATABASES["default"]["NAME"])).resolve()
media = Path(str(settings.MEDIA_ROOT)).resolve()
print("db.parent == media.parent:", db.parent == media.parent, f"({db.parent})")
PY

echo ""
echo "=== verifica_path_documenti (sintesi; solo mancanti con limite) ==="
"$PY" manage.py verifica_path_documenti --limite 50 --solo-mancanti 2>&1 | tail -n 30

echo ""
echo "OK fine Tappa 2 (script). Confronta MEDIA_ROOT con alias Nginx /media/ in deploy."
echo "Se 'mancanti' > 0: verificare file su disco o path in DB; vedi comandi in PROCEDURA_DEPLOY."
