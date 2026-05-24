# Deploy GESPER — flusso unico (riferimento definitivo)

**Host produzione:** `https://gesper1.plazapretoria.it` (SSH: `root@178.105.161.77`)  
**Radice dati VPS:** `/var/www/gesper` → `db.sqlite3`, `media/`, `archivio/`  
**Codice in esecuzione:** `/home/deploy/gesper1` (Gunicorn `gesper1.service`, socket locale)  
**Env:** `/home/deploy/gesper1/.env` con `GESPER_DATA_ROOT=/var/www/gesper`  
**Deploy automatico GitHub:** `/home/deploy/deploy_gesper1.sh` (copia da `deploy/deploy_gesper1.sh` nel repo: `git fetch` + `reset --hard origin/main` + migrate + restart)

Dettagli Nginx, TLS, migrazioni storiche: `deploy/PROCEDURA_DEPLOY.md` (appendice operativa).  
**Non usare** gli script elencati in `deploy/DEPRECATED.md`.

---

## Principio

| Cosa | Fonte di verità | Dove si lavora |
|------|-----------------|----------------|
| **Dati** (DB, PDF buste, media) | **Produzione** | Copia in locale solo per test |
| **Codice** (Django, template, fix acquisizione) | **Locale + Git** | Deploy verso produzione dopo test |
| **Versionamento** | **GitHub** (`main`) | Ogni modifica utile va in commit/push prima del deploy |

Flusso standard (**locale per prove, produzione per dati reali**):

```text
1. Allinea locale ai dati di produzione     →  gesper pull-data
2. Sviluppa e testa in locale               →  manage.py test / prove manuali
3. Commit + push su GitHub                  →  git add / commit / push
4. Deploy codice su produzione              →  gesper push-code
5. Verifica su produzione (acquisizione PDF) →  UI + eventuali comandi manage.py
```

**Non** fare patch persistenti solo in produzione senza riportarle nel repo.

---

## Comando unico

Dalla root del repository:

```bash
./deploy/gesper.sh <comando> [opzioni]
./deploy/gesper.sh help
```

| Comando | Azione |
|---------|--------|
| `pull-data` | Produzione → locale: DB + media (+ opzioni) |
| `push-code` | Locale → produzione: rsync codice, pip, migrate, collectstatic, restart |
| `push-data` | Locale → produzione: **solo dati** (eccezionale; chiede conferma) |
| `verify-remote` | Diagnostica path MEDIA/DB sulla VPS |
| `nginx-apply` | Applica vhost Nginx dal repo e reload |
| `check-local` | `manage.py check` (+ test opzionali) |

Variabili comuni (override):

```bash
export GESPER_DEPLOY_HOST=root@gesper1.plazapretoria.it
export GESPER_DATA_ROOT=/var/www/gesper          # = GESPER_DATA_ROOT in /etc/gesper.env
export GESPER_DEPLOY_SKIP_TESTS=1                # salta test prima di push-code
```

---

## 1. Allineare il locale (dati da produzione)

Prima di modificare acquisizione documenti / buste paga:

```bash
./deploy/gesper.sh pull-data --yes
# oppure solo DB + media, senza codice/static da remoto:
./deploy/gesper.sh pull-data --data-only --yes
```

Equivalente esplicito:

```bash
REMOTE_DATA_ROOT=/var/www/gesper bash scripts/produzione_a_locale.sh --data-only --yes
```

Poi in locale:

```bash
source .venv/bin/activate
python manage.py check
python manage.py verifica_path_documenti --tipo busta_paga --solo-mancanti --limite 20
# prova acquisizione su un doc noto
python manage.py ricalcola_buste_acquisizione --limit 5
```

`documento/db.sqlite3` e `media/` sotto il progetto vengono aggiornati; su XAMPP può usarsi anche `htdocs/media` se presente (vedi `settings.py`).

---

## 2. Sviluppo e test in locale

- Modifiche in `documenti/busta_acquisizione.py`, `motore_cedolino_v4.py`, ecc.
- Test mirati prima del deploy:

```bash
python manage.py test documenti rapporto_di_lavoro.tests -v 1 --keepdb
```

---

## 3. GitHub

```bash
git status
git add …
git commit -m "…"
git push origin main
```

Il deploy invia il **tree locale** (non fa `git pull` sul server salvo strategia git esplicita). Il push assicura backup e storia allineata.

---

## 4. Deploy codice su produzione

```bash
./deploy/gesper.sh push-code
# oppure, dopo git push, sul server (webhook) o via SSH:
#   bash /home/deploy/deploy_gesper1.sh
```

Esegue: check locale → test `rapporto_di_lavoro.tests` (saltabili) → `remote-rsync-django-gesper1.sh` (rsync **senza** DB/media).

Post-deploy rapido:

```bash
./deploy/gesper.sh verify-remote
```

In browser: login consulente → upload/prova lettura busta; oppure `ricalcola_buste_acquisizione` sulla VPS.

---

## 5. Quando serve portare dati locale → produzione

**Raro.** Solo migrazioni controllate o ripristino da backup locale verificato:

```bash
./deploy/gesper.sh push-data --yes
```

Preferire backup sulla VPS prima. Per il **codice** usare sempre `push-code`, non `scripts/locale_a_produzione.sh --code-only` (rsync diverso, senza migrate/collectstatic standard).

---

## Allineamento produzione (checklist una tantum)

Su VPS, in **`/home/deploy/gesper1/.env`** (caricato da Gunicorn) **oppure** `/etc/gesper.env`:

```bash
GESPER_DATA_ROOT=/var/www/gesper
DJANGO_SETTINGS_MODULE=settings_production
DJANGO_SECRET_KEY=…
GESPER_ALLOWED_HOSTS=gesper1.plazapretoria.it
```

**Non** impostare `GESPER_MEDIA_ROOT` se i PDF sono in `/var/www/gesper/media/` (derivato automaticamente da `GESPER_DATA_ROOT`).

**Gunicorn deve leggere lo stesso env** usato da `deploy_gesper1.sh` per migrate: copiare `deploy/gesper1.service.example` in `/etc/systemd/system/gesper1.service` con `EnvironmentFile=-/home/deploy/gesper1/.env`, poi `daemon-reload` e `restart gesper1`. Senza questo, migrate vede i path giusti ma il sito in esecuzione può usare `MEDIA_ROOT=/var/www/media` e DB sotto il repo.

Nginx `location /media/` → `alias /var/www/gesper/media/;` (file `deploy/nginx-gesper-vps-standalone.conf`).

```bash
./deploy/gesper.sh nginx-apply
./deploy/gesper.sh verify-remote   # SSH sulla VPS: MEDIA_ROOT, path buste, prova acquisizione
```

---

## Riferimento incrociato

- Trasferimenti dati (opzioni `--db-only`, ecc.): `scripts/TRASFERIMENTI_AMBIENTI.md`
- Script **non** più da usare: `deploy/DEPRECATED.md`
