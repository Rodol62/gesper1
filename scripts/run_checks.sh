#!/usr/bin/env bash
# Controlli locali pre-commit / pre-rilascio (vedi COMPLIANCE_CHECKLIST_RILASCIO.md sez. 9–10).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
if [[ -f .venv/bin/activate ]]; then
	# shellcheck source=/dev/null
	source .venv/bin/activate
fi
python3 manage.py check
python3 manage.py test --verbosity=1
echo "OK: django check + test"
