#!/usr/bin/env bash
# DEPRECATO — vedi deploy/DEPRECATED.md e deploy/DEPLOY_STANDARD.md
set -euo pipefail
echo "deploy_gesper.sh è deprecato. Uso: ./deploy/gesper.sh push-code" >&2
echo "Documentazione: deploy/DEPLOY_STANDARD.md" >&2
ROOT="$(cd "$(dirname "$0")" && pwd)"
exec bash "$ROOT/deploy/gesper.sh" push-code "$@"
