#!/usr/bin/env bash
# Lanciare dal Mac: pubblica deploy/ sulla VPS e (di default) installa il vhost Nginx dal file versionato.
# Con GESPER_DEPLOY_SYNC_ONLY=1 (o remote-sync-deploy-and-reload-nginx.sh): solo reload, niente sovrascrittura gesper1.conf.
# Vedi deploy/PROCEDURA_DEPLOY.md §3.
#
# Di default NON usa git sulla VPS (molte installazioni sono rsync/copia senza .git).
# Strategia:
#   GESPER_DEPLOY_STRATEGY=rsync (default) → rsync della cartella deploy/ dal repo locale → poi install sul server
#   GESPER_DEPLOY_STRATEGY=git             → git pull sul server (serve repository clonato) → poi install
#
# Uso:
#   GESPER_DEPLOY_HOST=root@gesper1.plazapretoria.it ./deploy/remote-apply-nginx-gesper1.sh
# Opzionali:
#   GESPER_REMOTE_PROJECT_DIR=/var/www/gesper
#   GESPER_SSH_IDENTITY=~/.ssh/id_ed25519
#   GESPER_DEPLOY_SYNC_ONLY=1  # dopo rsync/git: solo nginx -t + reload (no copia vhost, no backup)
#   GESPER_SKIP_RSYNC=1        # con strategia rsync: non copiare, solo comandi sul server
#   GESPER_RSYNC_DRY_RUN=1     # simula rsync
#   GESPER_SSH_NO_TTY=1        # forza mai -t (sudo potrebbe richiedere password su stderr in casi rari)
#
# Per aggiornare solo i file in deploy/ (snippet, doc) senza toccare gesper1.conf: vedi
#   ./deploy/remote-sync-deploy-and-reload-nginx.sh
#
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
LOCAL_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
HOST="${GESPER_DEPLOY_HOST:-root@gesper1.plazapretoria.it}"
REMOTE="${GESPER_REMOTE_PROJECT_DIR:-/var/www/gesper}"
STRAT="${GESPER_DEPLOY_STRATEGY:-rsync}"

SSH_BASE=(ssh -o ConnectTimeout=25)
[[ -n "${GESPER_SSH_IDENTITY:-}" ]] && SSH_BASE+=(-i "${GESPER_SSH_IDENTITY}" -o IdentitiesOnly=yes)
# -t solo in terminale interattivo (evita "Pseudo-terminal will not be allocated" da CI / pipe)
if [[ -z "${GESPER_SSH_NO_TTY:-}" ]] && [[ -t 0 ]]; then
  SSH_BASE+=(-t)
fi
SSH_BASE+=("$HOST")

if [[ -n "${GESPER_SSH_IDENTITY:-}" ]]; then
  export RSYNC_RSH="ssh -i ${GESPER_SSH_IDENTITY} -o IdentitiesOnly=yes"
fi

REMOTE_INSTALL="cd '${REMOTE}' && sudo bash deploy/install-nginx-gesper1-vhost-from-repo.sh"
REMOTE_RELOAD="cd '${REMOTE}' && sudo nginx -t && sudo systemctl reload nginx"

if [[ "$STRAT" == "git" ]]; then
  if [[ -n "${GESPER_DEPLOY_SYNC_ONLY:-}" ]]; then
    if [[ -n "${GESPER_SKIP_GIT_PULL:-}" ]]; then
      echo "== $HOST: strategia git (pull saltato) + reload Nginx =="
      REMOTE_SH="$REMOTE_RELOAD"
    else
      echo "== $HOST: git pull + reload Nginx (sync-only) =="
      REMOTE_SH="cd '${REMOTE}' && git pull && sudo nginx -t && sudo systemctl reload nginx"
    fi
  elif [[ -n "${GESPER_SKIP_GIT_PULL:-}" ]]; then
    echo "== $HOST: strategia git (pull saltato) + install vhost =="
    REMOTE_SH="${REMOTE_INSTALL}"
  else
    echo "== $HOST: git pull + install vhost =="
    REMOTE_SH="cd '${REMOTE}' && git pull && sudo bash deploy/install-nginx-gesper1-vhost-from-repo.sh"
  fi
  "${SSH_BASE[@]}" "bash -lc $(printf %q "$REMOTE_SH")"
else
  # rsync (default): cartella deploy/ locale → server
  if [[ -z "${GESPER_SKIP_RSYNC:-}" ]]; then
    echo "== rsync deploy/ → $HOST:$REMOTE/deploy/ =="
    RSYNC=(rsync -avz)
    [[ -n "${GESPER_RSYNC_DRY_RUN:-}" ]] && RSYNC+=(--dry-run)
    RSYNC+=(--exclude '.DS_Store')
    RSYNC+=("$LOCAL_ROOT/deploy/" "$HOST:$REMOTE/deploy/")
    "${RSYNC[@]}"
  else
    echo "== rsync saltato (GESPER_SKIP_RSYNC=1) =="
  fi
  if [[ -n "${GESPER_DEPLOY_SYNC_ONLY:-}" ]]; then
    echo "== $HOST: nginx -t + reload (nessuna sovrascrittura vhost) =="
    "${SSH_BASE[@]}" "bash -lc $(printf %q "$REMOTE_RELOAD")"
  else
    echo "== $HOST: install vhost Nginx da file in $REMOTE =="
    "${SSH_BASE[@]}" "bash -lc $(printf %q "$REMOTE_INSTALL")"
  fi
fi

echo "OK. Da Mac: bash deploy/verify-public-endpoints.sh"
