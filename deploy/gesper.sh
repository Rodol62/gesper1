#!/usr/bin/env bash
# Punto unico deploy / sync GESPER — vedi deploy/DEPLOY_STANDARD.md
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$ROOT"

# Default allineati a produzione Hetzner / gesper1
export GESPER_DEPLOY_HOST="${GESPER_DEPLOY_HOST:-root@178.105.161.77}"
export GESPER_REMOTE_PROJECT_DIR="${GESPER_REMOTE_PROJECT_DIR:-/home/deploy/gesper1}"
export GESPER_SYSTEMD_UNIT="${GESPER_SYSTEMD_UNIT:-gesper1}"
export GESPER_DATA_ROOT="${GESPER_DATA_ROOT:-/var/www/gesper}"
export REMOTE_HOST="${REMOTE_HOST:-$GESPER_DEPLOY_HOST}"
export REMOTE_APP_DIR="${REMOTE_APP_DIR:-$GESPER_REMOTE_PROJECT_DIR}"
export REMOTE_DATA_ROOT="${REMOTE_DATA_ROOT:-$GESPER_DATA_ROOT}"

usage() {
  cat <<'EOF'
GESPER — deploy e sync ambienti (flusso unico)

Uso:
  ./deploy/gesper.sh <comando> [opzioni]

Comandi:
  pull-data [--yes] [--data-only|--db-only|--media-only|--code-only]
      Produzione → locale (DB/media; default anche codice+static da remoto)
  push-code [--skip-tests]
      Locale → produzione: codice (rsync, pip, migrate, collectstatic, restart)
  push-data [--yes] [--db-only|--media-only]
      Locale → produzione: solo dati (eccezionale)
  verify-remote
      Diagnostica MEDIA_ROOT / path documenti sulla VPS
  nginx-apply
      Applica vhost Nginx dal repo e reload
  check-local [--skip-tests]
      manage.py check (+ test opzionali) in locale
  help
      Questo messaggio

Documentazione: deploy/DEPLOY_STANDARD.md
Script deprecati: deploy/DEPRECATED.md

Variabili: GESPER_DEPLOY_HOST, GESPER_DATA_ROOT (/var/www/gesper),
           GESPER_DEPLOY_SKIP_TESTS=1, GESPER_RSYNC_DRY_RUN=1
EOF
}

cmd="${1:-help}"
shift || true

case "$cmd" in
  help|-h|--help)
    usage
    ;;
  pull-data)
    exec bash "$ROOT/scripts/produzione_a_locale.sh" "$@"
    ;;
  push-code)
    for arg in "$@"; do
      case "$arg" in
        --skip-tests) export GESPER_DEPLOY_SKIP_TESTS=1 ;;
        *)
          echo "Opzione non riconosciuta per push-code: $arg (usa --skip-tests)" >&2
          exit 1
          ;;
      esac
    done
    exec bash "$SCRIPT_DIR/deploy-gesper1-completo.sh"
    ;;
  push-data)
    # Codice in produzione solo con push-code
    filtered=()
    for arg in "$@"; do
      if [[ "$arg" == "--code-only" ]]; then
        echo "ERRORE: per il codice usa ./deploy/gesper.sh push-code (non push-data)." >&2
        exit 1
      fi
      filtered+=("$arg")
    done
    if [[ " ${filtered[*]} " != *" --yes "* && " ${filtered[*]} " != *" -y "* ]]; then
      echo "ATTENZIONE: push-data sovrascrive dati in PRODUZIONE. Aggiungi --yes se confermi." >&2
    fi
    exec bash "$ROOT/scripts/locale_a_produzione.sh" "${filtered[@]}"
    ;;
  verify-remote)
    exec bash "$SCRIPT_DIR/tappa2-check-dati-vps.sh"
    ;;
  nginx-apply)
    exec bash "$SCRIPT_DIR/remote-apply-nginx-gesper1.sh"
    ;;
  check-local)
    PY="${ROOT}/.venv/bin/python"
    [[ -x "$PY" ]] || PY="python3"
    "$PY" manage.py check
    skip=false
    for arg in "$@"; do
      [[ "$arg" == "--skip-tests" ]] && skip=true
    done
    if [[ "$skip" != true && -z "${GESPER_DEPLOY_SKIP_TESTS:-}" ]]; then
      "$PY" manage.py test rapporto_di_lavoro.tests -v 1 --keepdb
    fi
    ;;
  *)
    echo "Comando sconosciuto: $cmd" >&2
    usage >&2
    exit 1
    ;;
esac
