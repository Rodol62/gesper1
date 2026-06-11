#!/usr/bin/env bash
# Installa hook systemd: restart nginx Docker dopo ogni restart gesper1.service.
# Eseguire sulla VPS come root:
#   bash /home/deploy/gesper1/deploy/install-gesper1-docker-nginx-hook.sh
# oppure da locale:
#   ssh root@178.105.161.77 'bash -s' < deploy/install-gesper1-docker-nginx-hook.sh
set -euo pipefail

if [[ "$(id -u)" -ne 0 ]]; then
    echo "Eseguire come root (sudo)." >&2
    exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
SRC_SCRIPT="${SCRIPT_DIR}/gesper1-reload-docker-nginx.sh"
DROP_IN="${SCRIPT_DIR}/gesper1.service.d/docker-nginx-reload.conf"
DEST_SCRIPT="/usr/local/sbin/gesper1-reload-docker-nginx.sh"
DEST_DROP_IN_DIR="/etc/systemd/system/gesper1.service.d"
UNIT="gesper1.service"

if [[ ! -f "$SRC_SCRIPT" ]] || [[ ! -f "$DROP_IN" ]]; then
    echo "File mancanti in ${SCRIPT_DIR}" >&2
    exit 1
fi

install -m755 "$SRC_SCRIPT" "$DEST_SCRIPT"
mkdir -p "$DEST_DROP_IN_DIR"
install -m644 "$DROP_IN" "${DEST_DROP_IN_DIR}/docker-nginx-reload.conf"

systemctl daemon-reload

if systemctl is-enabled "$UNIT" >/dev/null 2>&1 || systemctl is-active "$UNIT" >/dev/null 2>&1; then
    echo "Riavvio ${UNIT} per verificare l'hook..."
    systemctl restart gesper1.service
    sleep 2
    if curl -sfI -m 10 -H "Host: gesper1.plazapretoria.it" https://127.0.0.1/accounts/login/ >/dev/null 2>&1; then
        echo "OK: HTTPS locale risponde dopo restart ${UNIT}"
    else
        echo "ATTENZIONE: verificare manualmente: curl -sI https://gesper1.plazapretoria.it/"
    fi
else
    echo "Unit ${UNIT} non attiva — hook installato; al prossimo restart nginx Docker si aggiornerà."
fi

echo "Installato:"
echo "  ${DEST_SCRIPT}"
echo "  ${DEST_DROP_IN_DIR}/docker-nginx-reload.conf"
echo "Log: journalctl -t gesper1-docker-nginx"
