#!/usr/bin/env bash
# Pubblica la cartella PWA gesper-app sul server (rsync + SSH).
# Eseguire dalla macchina di sviluppo (Mac), non dalla VPS. Vedi deploy/PROCEDURA_DEPLOY.md §0.1
# Contesto deploy completo: deploy/PROCEDURA_DEPLOY.md
#
# Variabili opzionali:
#   GESPER_DEPLOY_HOST        default ubuntu@gesper1.plazapretoria.it
#   GESPER_REMOTE_APP_DIR     default /var/www/gesper-app (come PROCEDURA_DEPLOY.md)
#   GESPER_LOCAL_APP_DIR      percorso locale se gesper-app non è ../gesper-app da htdocs
#   GESPER_RSYNC_DRY_RUN=1    simulazione (--dry-run)
#   GESPER_SSH_IDENTITY       percorso chiave privata (es. ~/.ssh/gesper_ed25519)
#
# Eseguire dalla macchina di sviluppo con chiave SSH già configurata.

set -euo pipefail
HOST="${GESPER_DEPLOY_HOST:-ubuntu@gesper1.plazapretoria.it}"
REMOTE_DIR="${GESPER_REMOTE_APP_DIR:-/var/www/gesper-app}"
DRY="${GESPER_RSYNC_DRY_RUN:-}"

if [[ -n "${GESPER_SSH_IDENTITY:-}" ]]; then
  export RSYNC_RSH="ssh -i ${GESPER_SSH_IDENTITY} -o IdentitiesOnly=yes"
fi

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# Default: gesper/deploy → ../.. = htdocs, gesper-app è sibling di gesper/
# Override: GESPER_LOCAL_APP_DIR=/percorso/completo/gesper-app
if [[ -n "${GESPER_LOCAL_APP_DIR:-}" ]]; then
  LOCAL_APP="${GESPER_LOCAL_APP_DIR}"
else
  LOCAL_APP="$(cd "$SCRIPT_DIR/../.." && pwd)/gesper-app"
fi
if [[ ! -d "$LOCAL_APP" ]]; then
  echo "Cartella locale non trovata: $LOCAL_APP" >&2
  echo "Imposta GESPER_LOCAL_APP_DIR oppure posiziona gesper-app come sibling di htdocs (../gesper-app rispetto a gesper/)." >&2
  exit 1
fi

RSYNC=(rsync -avz --delete --exclude '.DS_Store')
[[ -n "$DRY" ]] && RSYNC+=(--dry-run)

"${RSYNC[@]}" "$LOCAL_APP/" "$HOST:$REMOTE_DIR/"

echo "OK: PWA copiata in $HOST:$REMOTE_DIR — apri la PWA dal tuo URL (es. …/gesper-app/); se non vedi aggiornamenti: ricarica forzato o disattiva cache DevTools, così si aggiorna anche sw.js."
