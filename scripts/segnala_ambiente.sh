#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
STATE_FILE="${ROOT_DIR}/.ambiente_operativo"

usage() {
  cat <<'EOF'
Uso:
  bash scripts/segnala_ambiente.sh locale
  bash scripts/segnala_ambiente.sh produzione
  bash scripts/segnala_ambiente.sh show
EOF
}

cmd="${1:-show}"
now="$(date '+%Y-%m-%d %H:%M:%S')"

case "$cmd" in
  locale)
    {
      echo "AMBIENTE=LOCALE"
      echo "AGGIORNATO_IL=${now}"
    } > "${STATE_FILE}"
    echo "[LOCALE] ambiente operativo impostato."
    ;;
  produzione)
    {
      echo "AMBIENTE=PRODUZIONE"
      echo "AGGIORNATO_IL=${now}"
    } > "${STATE_FILE}"
    echo "[PRODUZIONE] ambiente operativo impostato."
    ;;
  show)
    if [[ -f "${STATE_FILE}" ]]; then
      cat "${STATE_FILE}"
    else
      echo "AMBIENTE=NON_IMPOSTATO"
      echo "AGGIORNATO_IL="
    fi
    ;;
  *)
    usage
    exit 1
    ;;
esac

