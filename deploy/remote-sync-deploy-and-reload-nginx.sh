#!/usr/bin/env bash
# Wrapper: rsync di deploy/ sulla VPS e solo reload Nginx (senza rilanciare
# install-nginx-gesper1-vhost-from-repo.sh, quindi niente backup/sovascrittura di gesper1.conf).
# Usare per aggiornare snippet, PROCEDURA_DEPLOY.md, script in deploy/, ecc.
#
#   GESPER_DEPLOY_HOST=root@gesper1.plazapretoria.it ./deploy/remote-sync-deploy-and-reload-nginx.sh
#
# Per cambiare il file vhost principale (nginx-gesper-vps-standalone.conf) usa invece
# remote-apply-nginx-gesper1.sh senza GESPER_DEPLOY_SYNC_ONLY.

export GESPER_DEPLOY_SYNC_ONLY=1
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
exec bash "$SCRIPT_DIR/remote-apply-nginx-gesper1.sh" "$@"
