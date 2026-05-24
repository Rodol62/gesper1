#!/usr/bin/env bash
# Tappa 2 — Coerenza dati su VPS: env, MEDIA_ROOT, DB, path Documento ↔ file, dipendenze PDF.
#
# Sul server:
#   cd /home/deploy/gesper1 && bash deploy/tappa2-check-dati-vps.sh
# Dal Mac (dopo push dello script):
#   ./deploy/gesper.sh verify-remote
set -euo pipefail

_detect_proj() {
  local candidate
  for candidate in \
    "${GESPER_REMOTE_PROJECT_DIR:-}" \
    /home/deploy/gesper1 \
    /var/www/gesper; do
    [[ -n "$candidate" && -f "${candidate}/manage.py" ]] || continue
    echo "$candidate"
    return 0
  done
  return 1
}

PROJ="$(_detect_proj)" || {
  echo "ERRORE: manage.py non trovato (imposta GESPER_REMOTE_PROJECT_DIR)" >&2
  exit 1
}
cd "$PROJ"
echo "=== Progetto Django: ${PROJ} ==="
echo ""

_load_env() {
  if [[ -f /etc/gesper.env ]]; then
    set -a
    # shellcheck source=/dev/null
    source /etc/gesper.env
    set +a
    echo "Env caricato da /etc/gesper.env"
  elif [[ -f "${PROJ}/.env" ]]; then
    set -a
    # shellcheck source=/dev/null
    source "${PROJ}/.env"
    set +a
    echo "Env caricato da ${PROJ}/.env"
  else
    echo "ATTENZIONE: né /etc/gesper.env né ${PROJ}/.env trovati" >&2
  fi
}
_load_env
echo ""

_detect_python() {
  for v in "${PROJ}/venv/bin/python" "${PROJ}/.venv/bin/python"; do
    [[ -x "$v" ]] && echo "$v" && return 0
  done
  command -v python3
}
PY="$(_detect_python)"
echo "Python: ${PY}"
echo ""

UNIT="${GESPER_SYSTEMD_UNIT:-gesper1}"
if command -v systemctl >/dev/null 2>&1; then
  echo "=== systemd ${UNIT} (EnvironmentFile / variabili) ==="
  if systemctl cat "${UNIT}" 2>/dev/null | head -n 40; then
    echo ""
    systemctl show "${UNIT}" -p EnvironmentFiles -p FragmentPath 2>/dev/null || true
    echo ""
    echo "GESPER_DATA_ROOT nel processo (se unit attiva):"
    systemctl show "${UNIT}" -p Environment 2>/dev/null | tr ' ' '\n' | grep -E '^GESPER_|^DJANGO_' || echo "  (nessuna o unit non attiva)"
  else
    echo "  Unit ${UNIT} non trovata"
  fi
  echo ""
fi

export DJANGO_SETTINGS_MODULE="${DJANGO_SETTINGS_MODULE:-settings_production}"

echo "=== GESPER_DATA_ROOT (da shell env) ==="
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

echo ""
echo "=== Cartelle su disco ==="
"$PY" <<'PY'
import os
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "settings_production")
import django
django.setup()
from pathlib import Path
from django.conf import settings

media = Path(str(settings.MEDIA_ROOT))
db = Path(str(settings.DATABASES["default"]["NAME"]))
for label, p in [("MEDIA_ROOT", media), ("DB", db), ("buste_paghe", media / "buste_paghe")]:
    print(f"{label}: exists={p.exists()} is_dir={p.is_dir() if p.exists() else '-'} path={p}")
print("db.parent == media.parent:", db.parent.resolve() == media.parent.resolve(), f"({db.parent})")
PY

echo ""
echo "=== Dipendenze acquisizione PDF ==="
"$PY" -c "import pdfplumber; print('pdfplumber OK', pdfplumber.__version__)" 2>&1 || echo "ERRORE: pdfplumber non installato nel venv"
command -v pdftotext >/dev/null && echo "pdftotext OK: $(command -v pdftotext)" || echo "ATTENZIONE: pdftotext assente (fallback legacy/testo)"
echo ""

echo "=== verifica_path_documenti (solo mancanti, max 50) ==="
"$PY" manage.py verifica_path_documenti --limite 50 --solo-mancanti 2>&1 | tail -n 30

echo ""
echo "=== Ultima busta: prova acquisizione canonica ==="
"$PY" manage.py shell -c "
from documenti.models import Documento
from documenti.busta_acquisizione import acquisisci_busta_da_documento
d = Documento.objects.filter(tipo='busta_paga').exclude(file='').order_by('-id').first()
if not d:
    print('Nessun documento busta_paga con file.')
else:
    r = acquisisci_busta_da_documento(d)
    print('doc_id', d.pk, 'file', d.file.name)
    print('motore', r.motore or '-', 'errore', r.errore or '-', 'netto', r.netto, 'lordo', r.lordo)
" 2>&1

echo ""
echo "OK fine Tappa 2. Confronta MEDIA_ROOT con alias Nginx /media/ (deploy/nginx-gesper-vps-standalone.conf)."
echo "Se Gunicorn non ha GESPER_DATA_ROOT: copiare deploy/gesper1.service.example in /etc/systemd/system/."
