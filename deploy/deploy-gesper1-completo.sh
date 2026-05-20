#!/usr/bin/env bash
# Implementazione di: ./deploy/gesper.sh push-code  (vedi deploy/DEPLOY_STANDARD.md)
# Un solo comando: check + test (locale) → deploy su gesper1 (rsync + pip/migrate/collectstatic/restart).
#
# Opzionali:
#   GESPER_DEPLOY_SKIP_TESTS=1              # salta i test prima del deploy
#   GESPER_DEPLOY_TEST_LABEL=app.tests      # default: rapporto_di_lavoro.tests
#   GESPER_DEPLOY_HOST=root@...             # propagato a remote-rsync-django-gesper1.sh
#   GESPER_RSYNC_DRY_RUN=1                  # solo simulazione rsync
#
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$ROOT"

PY="${ROOT}/.venv/bin/python"
if [[ ! -x "$PY" ]]; then
  PY="python3"
fi

echo "== Django check (locale) =="
"$PY" manage.py check

if [[ -z "${GESPER_DEPLOY_SKIP_TESTS:-}" ]]; then
  LABEL="${GESPER_DEPLOY_TEST_LABEL:-rapporto_di_lavoro.tests}"
  echo "== Django test: ${LABEL} =="
  "$PY" manage.py test "$LABEL" -v 1 --keepdb
fi

echo "== Deploy (rsync → gesper1, vedi deploy/remote-rsync-django-gesper1.sh) =="
exec bash "${SCRIPT_DIR}/remote-rsync-django-gesper1.sh"
