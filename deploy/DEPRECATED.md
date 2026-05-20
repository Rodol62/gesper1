# Script deprecati — non usare nel flusso normale

Il flusso ufficiale è **`deploy/DEPLOY_STANDARD.md`** e il comando **`./deploy/gesper.sh`**.

Gli script sotto restano nel repo solo per storico o operazioni eccezionali documentate. Eseguirli senza motivo crea disallineamento tra locale, GitHub e produzione.

| Script | Motivo deprecazione | Usare invece |
|--------|---------------------|--------------|
| `deploy_gesper.sh` (root) | `scp` parziale + `pkill gunicorn` ad hoc, path XAMPP hardcoded | `./deploy/gesper.sh push-code` |
| `deploy/remote-full-sync-to-gesper1.sh` | Sovrascrive DB+media+code in un colpo; rischio alto | `gesper push-code` + `gesper pull-data` separati |
| `deploy/remote-apply-unified-gesper-data-root.sh` | Migrazione one-shot verso `…/documento/`; layout attuale è `/var/www/gesper` | Env `GESPER_DATA_ROOT=/var/www/gesper` + nginx dal repo |
| `scripts/locale_a_produzione.sh --code-only` | Rsync codice senza pip/migrate/collectstatic standard | `./deploy/gesper.sh push-code` |
| `scripts/produzione_a_locale.sh --code-only` | Duplica deploy inverso non necessario | `git pull` + `gesper pull-data` per dati |
| `deploy/prod-setup.sh` | Solo messaggi echo iniziali | `DEPLOY_STANDARD.md` + `setup_server.sh` se nuova VM |
| `deploy/migrate_media_layout_flat.sh` | Migrazione layout media storica | Già applicata; non ripetere |
| `deploy/remote-sync-deploy-and-reload-nginx.sh` | Solo snippet deploy senza vhost completo | `gesper nginx-apply` |

### Ancora validi (richiamati da `gesper.sh`)

| Script | Ruolo |
|--------|--------|
| `deploy/deploy-gesper1-completo.sh` | Implementazione di `push-code` |
| `deploy/remote-rsync-django-gesper1.sh` | Rsync + migrate + restart |
| `scripts/produzione_a_locale.sh` | Implementazione di `pull-data` |
| `scripts/locale_a_produzione.sh` | Implementazione di `push-data` (solo dati) |
| `deploy/remote-apply-nginx-gesper1.sh` | Implementazione di `nginx-apply` |
| `deploy/tappa2-check-dati-vps.sh` | Implementazione di `verify-remote` |
| `deploy/check-gesper-vps-app.sh` | Preflight manuale sulla VPS |
| `deploy/sync-pwa-and-collectstatic.sh` | Solo se modifichi PWA `gesper-app` |

### Produzione come riferimento per acquisizione documenti

1. Verificare/fix su **gesper1** (dati e PDF reali).
2. `gesper pull-data` per riprodurre in locale.
3. Fix codice in locale + test.
4. `git push` + `gesper push-code`.
5. Eventuale `ricalcola_buste_acquisizione` in produzione dopo deploy.

Non partire da un DB locale obsoleto per debuggare buste paga in produzione.
