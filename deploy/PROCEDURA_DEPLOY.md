# Procedura deploy: Hosting Linux Aruba + Cloud VPS + GESPER

> **Flusso unico:** [`DEPLOY_STANDARD.md`](DEPLOY_STANDARD.md) · `./deploy/gesper.sh help` · script obsoleti: [`DEPRECATED.md`](DEPRECATED.md)

Documento di riferimento per DNS, Nginx, Certbot e pubblicazione codice/PWA.

<details>
<summary><strong>Indice</strong> (clic per aprire)</summary>

**Uso frequente**

- [Allineamento dopo modifiche (codice, GitHub, produzione, dati, PWA)](#sec-allineamento-completo)
- [Checklist Git → deploy (~1 min)](#sec-git-deploy)
- [Django: static, migrate, deploy da Mac](#sec-django)
- [Verifica endpoint pubblici](#sec-verify)

**Infrastruttura e migrazione**

- [VPS gesper1 e cutover da gesper](#sec-migrazione)
- [Dove eseguire i comandi (Mac vs VPS)](#sec-mac-vps)
- [Checklist chiusura migrazione dati/DNS](#sec-cutover)
- [Deprecato: `/gesper-test`](#sec-gesper-test)
- [Ruoli (hosting vs VPS)](#sec-ruoli)
- [DNS Aruba](#sec-dns)
- [Nginx](#sec-nginx)
- [Certbot / TLS](#sec-certbot)
- [PWA gesper-app](#sec-pwa)
- [Firewall VPS](#sec-firewall)
- [Upstream WordPress (solo split)](#sec-upstream)
- [Anti-conflitto DNS / Nginx](#sec-anti-conflitto)

</details>

<a id="sec-migrazione"></a>
## 0. Server VPS **gesper1** e migrazione da **gesper**

**Produzione di riferimento:** nuova VM **gesper1** con host pubblico **`gesper1.plazapretoria.it`** (file Nginx e script di deploy nel repo usano questo nome).

**Migrazione sintetica** (vecchia VM `gesper` → `gesper1`):

1. **DNS:** crea **A** `gesper1` → IPv4 della nuova VPS; verifica con `dig +short gesper1.plazapretoria.it A`. Se vuoi **mantenere l’URL storico**, punta anche **A** `gesper` → stesso IP e aggiungi `-d gesper.plazapretoria.it` a Certbot (stesso certificato SAN).
2. **Dati:** `rsync` (o backup/restore) di `/var/www/gesper`, `/var/www/gesper-app`, e **`/var/www/media`** o **`/var/www/documento`** (se usi `GESPER_DATA_ROOT`); copia unit **systemd** (`gesper.service`, eventuali `gesper-www`) e **EnvironmentFile** (`.env` / `/etc/gesper.env`).
3. **TLS:** sul nuovo server, dopo Nginx:  
   `sudo certbot --nginx -d gesper1.plazapretoria.it`  
   (e altri `-d` se servono `gesper`, `www`, …).
4. **Django:** in repo, `ALLOWED_HOSTS` e `CSRF_TRUSTED_ORIGINS` includono **`gesper1.plazapretoria.it`** e, per transizione, **`gesper.plazapretoria.it`**. In Admin → **Configurazione di sistema**, aggiorna **URL pubblica base** se i link nelle e-mail devono usare il nuovo host.
5. **Deploy da sviluppo:** negli script in `deploy/` compare ancora il default storico `GESPER_DEPLOY_HOST=ubuntu@gesper1.plazapretoria.it`; in produzione si usa di solito **`root@gesper1.plazapretoria.it`** se l’utente `ubuntu` non ha chiave o è disabilitato (vedi **§0.1**). Configurare **accesso SSH a chiave** dal Mac (`ssh-copy-id`) così `sync-pwa-and-collectstatic.sh` non fallisce tra il primo `rsync` e il secondo `ssh` (due login separati).
6. **WordPress / comunicazioni:** link a GESPER → `https://gesper1.plazapretoria.it/` (o il nome che tieni in DNS).

<a id="sec-mac-vps"></a>
## 0.1 Dove eseguire i comandi (Mac vs VPS)

| Dove | Cosa fare |
|------|-----------|
| **Mac** (directory del repo, es. `gesper/`) | Lanciare `deploy/sync-pwa-and-collectstatic.sh`, `deploy/sync-gesper-app.example.sh`, `rsync`/`scp` del codice **verso** la VPS. **`ssh-copy-id`** si esegue **dal Mac** (installa la *public key* del Mac in `authorized_keys` sul server). I percorsi tipo `/Applications/XAMPP/...` esistono solo sul Mac: **non** usarli dopo `ssh` sulla VPS. |
| **VPS** (`ssh root@gesper1.plazapretoria.it`, ecc.) | Progetto in **`/var/www/gesper`**: `manage.py`, `.venv`, `pip install -r requirements.txt`, `systemctl` per **gesper**, file **`/etc/gesper.env`**, **Nginx** in `/etc/nginx/`. |

**Esempio sync PWA + collectstatic dal Mac** (utente `root` sulla VPS):

```bash
cd /percorso/al/repo/gesper
GESPER_DEPLOY_HOST=root@gesper1.plazapretoria.it ./deploy/sync-pwa-and-collectstatic.sh
```

**Chiave SSH (una tantum, dal Mac):**

```bash
ssh-copy-id -i ~/.ssh/id_ed25519.pub root@gesper1.plazapretoria.it
```

Non eseguire `ssh-copy-id` *sulla* VPS: sul server non c’è la chiave privata del Mac (`/root/.ssh/id_ed25519.pub` non esiste lì).

**Test Gunicorn sulla VPS:** una `curl` a `http://127.0.0.1:8000/` senza header può dare **400 DisallowedHost**; usare  
`-H 'Host: gesper1.plazapretoria.it'` oppure verificare **`https://gesper1.plazapretoria.it/`** (Nginx inoltra l’host corretto).

**Routine dopo aggiornamento codice o dipendenze (sulla VPS):**

```bash
cd /var/www/gesper
./.venv/bin/pip install -r requirements.txt   # se è cambiato requirements.txt
set -a; [ -f /etc/gesper.env ] && . /etc/gesper.env; set +a
export DJANGO_SETTINGS_MODULE=settings_production
./.venv/bin/python manage.py migrate --noinput
./.venv/bin/python manage.py collectstatic --noinput
sudo systemctl restart gesper
```

(Equivalente: aggiornare il tree con `rsync`/`git pull` dal Mac, poi gli stessi comandi in `/var/www/gesper`.)

<a id="sec-cutover"></a>
## 0.2 Checklist — chiusura migrazione **gesper** → **gesper1**

Esegui quando il **codice**, **Nginx/HTTPS** e **Gunicorn** su gesper1 sono già ok; mancano soprattutto **dati**, **DB** e **cutover**.

1. **Ferma il traffico sulla vecchia VM** (opzionale ma consigliato per l’ultimo sync): metti in manutenzione o blocca l’accesso, poi **ferma Gunicorn** sulla vecchia macchina così il file SQLite non è in scrittura.
2. **Allinea layout dati** con quello che userai su gesper1:
   - **Con `GESPER_DATA_ROOT=/var/www/documento`:** copia sul nuovo server il contenuto di `documento` (almeno `db.sqlite3` e la cartella `media/` con la stessa struttura). Esempio **dalla vecchia VPS** (adatta host e percorsi):
     ```bash
     # sulla vecchia gesper, da root — esempio verso gesper1
     rsync -avz /var/www/documento/ root@gesper1.plazapretoria.it:/var/www/documento/
     ```
   - **Senza `GESPER_DATA_ROOT` (legacy):** copia `/var/www/media` e, se il DB era sotto il progetto, `/var/www/gesper/db.sqlite3` (o il path reale sul vecchio server). Stessi permessi/proprietario coerenti con l’utente che esegue `gesper` (es. `root` o `www-data`).
3. **`/etc/gesper.env` su gesper1:** imposta almeno `DJANGO_SECRET_KEY`. Per **non invalidare** sessioni e dati firmati già in circolazione, usa la **stessa** `SECRET_KEY` della vecchia produzione (se la recuperi dal vecchio env). Aggiungi `GESPER_DATA_ROOT` se usi il layout unificato.
4. **Nginx `alias` per `/media/`** deve puntare a `MEDIA_ROOT` effettivo (`…/documento/media/` o `/var/www/media`). Dopo modifiche: `sudo nginx -t && sudo systemctl reload nginx`.
5. **Sulla nuova VPS** (`/var/www/gesper`):
   ```bash
   set -a; source /etc/gesper.env; set +a
   export DJANGO_SETTINGS_MODULE=settings_production
   ./.venv/bin/pip install -r requirements.txt
   ./.venv/bin/python manage.py migrate --noinput
   ./.venv/bin/python manage.py collectstatic --noinput
   sudo systemctl restart gesper
   ```
6. **DNS:** quando i dati sono allineati, punta **`gesper`** (e se serve altri nomi) verso l’IP di **gesper1**; estendi Certbot con eventuali `-d` aggiuntivi. Verifica con `dig` e `curl -I https://gesper1.plazapretoria.it/`.
7. **Admin GESPER** → **Configurazione di sistema:** aggiorna **URL pubblica base** (e SMTP se serve) per il nuovo host.
8. **Smoke test:** login, apertura documenti/media, PWA `/gesper-app/`, eventuali notifiche push.
9. **Dopo il go-live:** spegni o decommissiona la vecchia VM e i backup restano solo su gesper1 / archivio.

<a id="sec-gesper-test"></a>
## 0.3 Ex ambiente `/gesper-test` (deprecato)

Il secondo Gunicorn su **8001** e le `location` Nginx per `/gesper-test/` **non sono più** nel repository. Su una VPS ancora configurata così: rimuovi da `sites-available` le `location` `/gesper-test/`, `sudo systemctl disable --now gesper-test` (se esiste), `sudo nginx -t && sudo systemctl reload nginx`. I path in repo (`deploy/nginx-gesper-vps-standalone.conf`, `nginx-gesper-production-split.conf`) espongono solo **8000** (root su `gesper1`) e opz. **8003** per `/gesper/` su `www` (split).

<a id="sec-git-deploy"></a>
## 0.4 Checklist rapida — sviluppo → Git → deploy (~1 min)

Flusso consigliato (**`DEPLOY_STANDARD.md`**): allinea dati da produzione (`gesper pull-data`), sviluppa in locale, **commit/push** GitHub, **`gesper push-code`** verso la VPS. Evitare patch solo in produzione senza repo.

### Pre-commit (Mac, root del repo)

1. `git status` — solo le modifiche che si intendono committare.
2. `.venv/bin/python manage.py check`
3. Se si è toccata logica critica (es. registro studio / import):  
   `.venv/bin/python manage.py test accounts.tests_consulente_registro_studio -v 1` (o il sottoinsieme di test abituale).
4. `git diff` rapido su file sensibili (`settings_production.py`, `urls.py`, viste).
5. `git commit` con messaggio chiaro.

### Pre-push

6. `git push origin main` (o il branch usato dal team).

### Pre-deploy (Mac)

7. **Deploy completo (definito nel repo):** dalla root del repo eseguire  
   `./deploy/gesper.sh push-code`
   (oppure `GESPER_DEPLOY_HOST=root@gesper1.plazapretoria.it ./deploy/deploy-gesper1-completo.sh`)  
   Lo script esegue in sequenza: `manage.py check` locale, **`manage.py test rapporto_di_lavoro.tests`** (salta con `GESPER_DEPLOY_SKIP_TESTS=1`), poi delega a `remote-rsync-django-gesper1.sh` (rsync, pip, migrate, collectstatic, restart `gesper`). Variabili opzionali: commenti in cima a `deploy/deploy-gesper1-completo.sh` e `deploy/remote-rsync-django-gesper1.sh`.

8. **Solo sync verso server** (senza suite test locale, ma con `check` dentro `remote-rsync`):  
   `GESPER_DEPLOY_HOST=root@gesper1.plazapretoria.it ./deploy/remote-rsync-django-gesper1.sh`

9. Controllare l’output del deploy: `migrate` senza errori, `collectstatic` ok, `systemctl is-active gesper` → **active**.

### Post-deploy (browser, ~2 min)

10. Login area consulente + una pagina “pesante” (libro / pagamenti / proforma).
11. Un flusso legato all’ultima modifica (es. allegato PDF bonifico, upload multipart documenti).

<a id="sec-allineamento-completo"></a>
## 0.5 Allineamento dopo ogni modifica — regola operativa

**Principio (ordine temporale):**

1. **Ambiente di lavoro:** gli aggiustamenti al codice si fanno **sempre in locale** (repo sul Mac / XAMPP), non direttamente in produzione.  
2. **Verifiche:** `check`, test mirati, prova manuale in locale.  
3. **Versionamento:** `git commit` e `git push` su GitHub.  
4. **Deploy di allineamento:** dalla Mac, script del repo verso la VPS (e, se serve, sync PWA) così produzione esegue lo **stesso** codice del repo.

**Riferimenti URL su VPS e PWA:** link, redirect, `fetch`, service worker e asset devono usare **percorsi relativi** alla radice del sito (`/…`) oppure la **base URL da configurazione** (es. Admin → Configurazione sistema → URL pubblica base, variabili d’ambiente), non host fissi tipo `http://127.0.0.1:8000` o un dominio di sviluppo. Stesso discorso per la PWA: evitare assoluti legati solo al locale.

---

Dopo una **sistemazione o modifica del codice**, l’obiettivo è che **quattro assi** restino coerenti tra loro, salvo eccezioni documentate:

| Asse | Cosa allineare | Come |
|------|----------------|------|
| **Locale** | Tree di lavoro (Mac/XAMPP) uguale a ciò che intendi pubblicare | Salvare i file; `manage.py check` (e test mirati se serve). |
| **Repository GitHub** | Storico ufficiale del codice | `git commit` → `git push` sul branch condiviso (es. `main`). **Non** lasciare fix solo in produzione senza commit. |
| **Produzione (VPS)** | Codice in esecuzione + static + schema DB | `./deploy/deploy-gesper1-completo.sh` (o `remote-rsync-django-gesper1.sh`): rsync, `migrate`, `collectstatic`, `systemctl restart gesper`. |
| **Dati** | DB e file media coerenti con l’ambiente di riferimento | Vedi sotto: non sono coperti dallo stesso `rsync` del codice. |
| **PWA** (se coinvolta) | Asset `/gesper-app/` serviti da Nginx | Solo se hai modificato la PWA: `deploy/sync-pwa-and-collectstatic.sh` e/o `deploy/sync-gesper-app.example.sh` (vedi § PWA). |

**Ordine consigliato (una tantum per ogni feature/fix):**

1. Sviluppo e verifica **in locale**.  
2. **Commit + push** su GitHub (il deploy dalla Mac prende il tree locale: idealmente è già ciò che è su `origin`).  
3. **Deploy** verso `gesper1` con lo script del repo.  
4. Se la release tocca la **PWA**, eseguire anche lo sync PWA + eventuale `collectstatic` come da procedura.  
5. **Dati:** se la modifica richiede migrazioni, sono già nel passo deploy (`migrate` sul server). Se servono **dati** (dump DB, cartelle `media/`, `GESPER_DATA_ROOT`), usare backup/rsync dedicati — **non** si assume che `rsync` del progetto copi `db.sqlite3` o `media/` (sono esclusi dallo script).

**Deploy “completo” vs “solo codice”:** nel repo, *completo* significa: test (opzionale) + trasferimento codice + dipendenze + migrazioni + static + riavvio servizio. *Allineamento dati* (produzione ↔ locale) è un **processo separato** (export/import DB, sync `media`, cutover come in §0.2) e va pianificato quando serve, non ad ogni commit.

<a id="sec-ruoli"></a>
## 1. Ruoli

| Componente | Funzione |
|------------|----------|
| **Hosting Linux Aruba** | Gestione **DNS** del dominio (record A, MX, TXT…), **WordPress** sul piano hosting. **Non** esegue Django. |
| **Cloud VPS Aruba (gesper1)** | **Ubuntu**, **Nginx**, **Gunicorn** (`gesper` su porta 8000), progetto in `/var/www/gesper`, PWA in `/var/www/gesper-app`, **Certbot** per TLS su `gesper1.*` (e opz. `gesper.*`), firewall **80/443**. |

<a id="sec-dns"></a>
## 2. DNS (pannello Hosting / domini Aruba)

### Modalità consigliata: GESPER separato (sottodominio sulla VPS)

WordPress resta sull’**hosting**; GESPER è raggiungibile come **`https://gesper1.plazapretoria.it/`** (Nginx sulla VPS **gesper1**).

| Nome | Tipo | Valore |
|------|------|--------|
| **`gesper1`** | **A** | **IPv4 pubblico della Cloud VPS gesper1** (pannello Aruba Cloud, oppure `curl -4 -s https://api.ipify.org` dopo SSH). |
| **`gesper`** | **A** | *Opzionale:* stesso IP se mantieni URL storico `gesper.…` (stesso backend). |
| **`www`** | **A** | IP del **piano hosting** WordPress, **non** la VPS se non usi lo split (vedi sotto). |
| **`@` (apex)** | **A** | Come da scelta commerciale (spesso stesso IP hosting per il sito vetrina). |
| MX / TXT posta | — | Invariati salvo migrazione mail |

**Errori da evitare**

- Non usare l’IP del **modem/router** (es. risposte TP-Link al posto del sito).
- `dig +short gesper1.plazapretoria.it A` deve coincidere con l’**IP della VPS** usata per GESPER.

**Nginx in repo:** `deploy/nginx-gesper-vps-standalone.conf` (`server_name` **`gesper1.plazapretoria.it`**).  
**Systemd:** serve `gesper` (8000); **`gesper-www` (8003) non serve** in questa modalità: `sudo systemctl disable --now gesper-www`.

### Opzionale (legacy): split Nginx — tutto `www` sulla VPS

Se **`A` `www` → IP VPS**, Nginx sulla VPS riceve anche `https://www…` e può fare proxy verso WordPress Aruba e **`/gesper/` → Gunicorn 8003** (`settings_production_www`). Dettagli DNS e conflitti: §9 e file `deploy/nginx-gesper-production-split.conf`.

<a id="sec-nginx"></a>
## 3. Nginx sul server

- **Default consigliato:** `deploy/nginx-gesper-vps-standalone.conf` (copia in `/etc/nginx/sites-available/` → `sites-enabled`, dopo backup).
- **Install automatico dal repo (consigliato su gesper1):** dopo `git pull` in `/var/www/gesper`, creare (se manca) `/etc/nginx/.htpasswd-gesper-deploy` come in §3 «Documentazione deploy-docs», poi:
  ```bash
  cd /var/www/gesper && sudo bash deploy/install-nginx-gesper1-vhost-from-repo.sh
  ```
  Lo script copia il file versionato in `sites-available/gesper1.conf`, crea il symlink in `sites-enabled` se assente, esegue `nginx -t` e, solo se ok, `reload`. In caso di errore su `nginx -t`, ripristina il backup `gesper1.conf.bak.<timestamp>`.
- **Dal Mac (stesso giro senza copiare a mano):** con SSH verso `gesper1` e percorso progetto su server = `/var/www/gesper`:
  ```bash
  GESPER_DEPLOY_HOST=root@gesper1.plazapretoria.it ./deploy/remote-apply-nginx-gesper1.sh
  ```
  Di default **rsync** della cartella `deploy/` del repo **locale** verso `…/var/www/gesper/deploy/`, poi `install-nginx-gesper1-vhost-from-repo.sh` in sudo (non richiede `.git` sul server). Se la VPS è un **clone git**, usa `GESPER_DEPLOY_STRATEGY=git` per fare `git pull` al posto dell’rsync. `GESPER_SSH_IDENTITY=~/.ssh/…` se serve la chiave.  
  **Solo aggiornamento file in `deploy/`** (snippet, markdown, script) **senza** riscrivere `gesper1.conf`:  
  `GESPER_DEPLOY_HOST=root@gesper1.plazapretoria.it ./deploy/remote-sync-deploy-and-reload-nginx.sh`  
  (oppure `GESPER_DEPLOY_SYNC_ONLY=1` con `remote-apply-nginx-gesper1.sh`) — esegue rsync + `nginx -t` + `reload`, senza backup/riscrittura del vhost.
- **Split www sulla VPS (opzionale):** `deploy/nginx-gesper-production-split.conf`.
- **Porta 80**: mantenere sempre `location ^~ /.well-known/acme-challenge/` con `root /var/www/certbot` (rinnovo Let’s Encrypt).
- **Porta 443 / TLS**: sul server **live**, dopo il primo `certbot --nginx`, **non** riscrivere a mano `ssl_certificate`, `ssl_certificate_key`, include SSL né duplicare blocchi `server` HTTPS. Per modifiche successive aggiornare solo **location**, `proxy_pass`, `upstream`, static/media.
- Dopo ogni modifica manuale alle sole parti applicative:  
  `sudo nginx -t && sudo systemctl reload nginx`

### Sintomo: risposta solo testo `GESPER nginx OK (HTTPS)` (o pochi byte) invece della login

Significa che **`location /` nel blocco HTTPS (443)** non inoltra a Gunicorn (es. è rimasto un `return 200 '…'`, un `alias` a un file di prova, o un catch-all sbagliato). **Correzione:** nel `server { … server_name gesper1…; listen 443 … }` sostituire l’intero blocco `location / { … }` applicativo con quello in **`deploy/snippets/gesper1-https-location-root-proxy.conf`** (stesso contenuto del `location /` in `deploy/nginx-gesper-vps-standalone.conf`: `proxy_pass http://127.0.0.1:8000;` e header elencati nello snippet). Poi `sudo nginx -t && sudo systemctl reload nginx`.

**Verifica rapida sulla VPS** (dopo `cd /var/www/gesper` e pull del repo): `bash deploy/check-gesper-vps-app.sh` — controlla Gunicorn su `127.0.0.1:8000`, confronta la risposta HTTPS locale e segnala il placeholder.

### Documentazione `deploy/` via HTTPS (Basic Auth)

Il repo contiene lo snippet **`deploy/nginx-snippet-deploy-docs.conf`** (URL **`/deploy-docs/`** → **`/var/www/gesper/deploy/`**, password Nginx). Su VPS già live, inserire l’`include` nel `server { 443 … }` è automatizzabile.

**Una tantum sulla VPS:**

```bash
sudo apt-get update && sudo apt-get install -y apache2-utils   # se manca htpasswd
sudo htpasswd -c /etc/nginx/.htpasswd-gesper-deploy TUO_UTENTE
sudo chown root:www-data /etc/nginx/.htpasswd-gesper-deploy
sudo chmod 640 /etc/nginx/.htpasswd-gesper-deploy
```

Sincronizza il codice in `/var/www/gesper` (così esistono snippet + script), poi:

```bash
sudo bash /var/www/gesper/deploy/server-install-deploy-docs-nginx.sh
```

Lo script modifica **`/etc/nginx/sites-available/gesper1.conf`** (backup `.bak.TIMESTAMP`), esegue `nginx -t` e `reload`. Vhost con nome diverso: `NGINX_GESPER_SITE=/etc/nginx/sites-available/altro.conf sudo bash …`.

Alternativa manuale: nel `server` HTTPS aggiungere **`include /var/www/gesper/deploy/nginx-snippet-deploy-docs.conf;`** subito prima di `location / {`, poi `sudo nginx -t && sudo systemctl reload nginx`.

**URL (dopo login browser):**  
`https://gesper1.plazapretoria.it/deploy-docs/PROCEDURA_DEPLOY.md`  
Indice directory: `https://gesper1.plazapretoria.it/deploy-docs/`

Altri utenti: `sudo htpasswd /etc/nginx/.htpasswd-gesper-deploy ALTRO_UTENTE` (senza `-c`).

<a id="sec-certbot"></a>
## 4. Certbot (Let’s Encrypt)

- Emissione / aggancio certificato al vhost: es.  
  `sudo certbot --nginx -d gesper1.plazapretoria.it`  
  (e altri `-d`, es. `gesper.plazapretoria.it`, `www`…, in base alla zona DNS).
- Rinnovo: `sudo certbot renew --dry-run` per test; rinnovo automatico gestito da systemd/cron di Certbot.
- **Non** reintrodurre vecchi blocchi HTTPS manuali obsoleti: rischio conflitti e certificati non validi.

<a id="sec-django"></a>
## 5. Applicazione Django e static

Sul server, dalla directory del progetto (es. `/var/www/gesper`):

```bash
set -a; [ -f /etc/gesper.env ] && . /etc/gesper.env; set +a
export DJANGO_SETTINGS_MODULE=settings_production   # root su gesper1.* / gesper.* (modalità separata)
./.venv/bin/pip install -r requirements.txt   # quando requirements.txt è aggiornato
./.venv/bin/python manage.py migrate --noinput
./.venv/bin/python manage.py collectstatic --noinput
sudo systemctl restart gesper
```

**Dal Mac (stesso ordine via SSH + rsync):** con repo locale allineato e venv sul server già presente:

```bash
GESPER_DEPLOY_HOST=root@gesper1.plazapretoria.it ./deploy/remote-rsync-django-gesper1.sh
```

Per **check + test `rapporto_di_lavoro.tests` + stesso flusso remoto**, usare `./deploy/deploy-gesper1-completo.sh` (stesso host e variabili).

Sincronizza il codice verso `/var/www/gesper` **senza** `--delete` e **esclude** tra l’altro `.venv`, `.git`, `db.sqlite3`, `media/`, `.env` (il server usa `/etc/gesper.env`). Poi `pip install`, `migrate`, `collectstatic`, `systemctl restart gesper`. Con **`GESPER_RSYNC_DRY_RUN=1`** viene solo simulato l’rsync e **non** si esegue nulla sul server (niente restart). Altre opzioni: `GESPER_SKIP_MIGRATE=1`, ecc. (vedi commenti nello script). PWA statica: resta `deploy/sync-pwa-and-collectstatic.sh` se usi `gesper-app` separato.

Se usi ancora lo **split** con path `/gesper/` su `www`: anche `gesper-www` e `settings_production_www`:

```bash
sudo systemctl restart gesper gesper-www
```

Riferimento rapido: `deploy/prod-setup.sh` (messaggi e comandi echo).

### Pulizia import Excel — bonifici partitario consulente–azienda

**Non è una voce di menu nell’app:** è un **management command** Django da eseguire **sulla VPS** (o in locale con lo stesso database che vuoi ripulire). Elimina, **per una sola azienda** (`--azienda-id` = pk in Admin → Azienda):

- i movimenti `bonifico` marcati come provenienti da **Excel** (riepilogo / estratto conto);
- i record **`ImportEstrattoContoStudio`** di quell’azienda (storico import “estratto”).

Senza flag aggiuntivi mostra solo **conteggi** (anteprima). Con **`--execute`** applica le cancellazioni e **ricalcola i saldi progressivi**. I bonifici caricati in altro modo (es. solo PDF) restano intatti se non hanno quella tracciatura Excel.

Per eliminare **solo** i bonifici «finti» da riepilogo PROFORMA (riferimento sintetico tipo `PARCELLA 182|data|…`) lasciando i bonifici bancari reali e **senza** toccare `ImportEstrattoContoStudio`, usare **`--solo-parcella-proforma-sintetici`** insieme a `--execute` (sempre dopo un’anteprima senza `--execute`).

```bash
cd /var/www/gesper
set -a; [ -f /etc/gesper.env ] && . /etc/gesper.env; set +a
export DJANGO_SETTINGS_MODULE=settings_production
./.venv/bin/python manage.py rimuovi_bonifici_import_excel_studio --azienda-id ID
./.venv/bin/python manage.py rimuovi_bonifici_import_excel_studio --azienda-id ID --execute
./.venv/bin/python manage.py rimuovi_bonifici_import_excel_studio --azienda-id ID --solo-parcella-proforma-sintetici --execute
```

Codice: `accounts/management/commands/rimuovi_bonifici_import_excel_studio.py`.

### Radice dati (`GESPER_DATA_ROOT`)

- **Produzione (Hetzner / gesper1):** in `/etc/gesper.env` imposta `GESPER_DATA_ROOT=/var/www/gesper` → **DB** `…/db.sqlite3`, **media** `…/media/`, **archivio** `…/archivio/`. **Nginx** (`nginx-gesper-vps-standalone.conf`): `alias /var/www/gesper/media/;`. Layout alternativo `…/gesper/documento/` solo se già in uso — vedi `DEPRECATED.md` (script unificazione one-shot).
- **Migrazione da layout legacy** (`/var/www/media` + `db.sqlite3` sotto `/var/www/gesper/`) **senza fare tutto a mano:** da Mac, dopo backup mentale,  
  `GESPER_UNIFIED_CONFIRM=1 GESPER_SSH_NO_TTY=1 ./deploy/remote-apply-unified-gesper-data-root.sh`  
  (unisce i file, copia il DB, aggiorna `gesper.env`, adegua l’`alias` Nginx in `sites-enabled` e riavvia). Poi allinea il vhost con il file in repo e `remote-rsync` se serve.
- **Alternativa** (radice fuori dal repo): `GESPER_DATA_ROOT=/var/www/documento` con gli stessi file `nginx-*.conf` adattati e Nginx allineato.
- **PDF in lista “mancanti” ma file presenti sul server:** i path in database sono relativi a `MEDIA_ROOT`; se i file sono stati copiati sotto `…/media/documenti/buste_paghe/` ma `GESPER_DATA_ROOT` o l’`alias` Nginx per `/media/` non coincidono, `storage.exists` fallisce. Allineare cartelle e env, poi in portale (dove previsto) usare le azioni di riallineamento buste; a calcolo, l’app prova anche path alternativi sotto le sottocartelle configurate (`documenti/buste_paghe`, …).
- **Sviluppo:** senza `htdocs/media` o `gesper/media` preesistenti, media e DB nuovi finiscono sotto `gesper/documento/` (vedi `settings.py`).

<a id="sec-pwa"></a>
## 6. PWA `gesper-app`

**Solo dal Mac** (o PC di sviluppo con cartella `gesper-app` locale e SSH verso la VPS), **non** dalla shell SSH sul server.

Con utente `root` sulla VPS e chiave SSH già configurata:

```bash
cd /percorso/al/repo/gesper
GESPER_DEPLOY_HOST=root@gesper1.plazapretoria.it ./deploy/sync-pwa-and-collectstatic.sh
```

Default senza override: `./deploy/sync-pwa-and-collectstatic.sh` (usa `ubuntu@gesper1.plazapretoria.it`).

Oppure solo PWA: `deploy/sync-gesper-app.example.sh`.  
Variabili: `GESPER_DEPLOY_HOST`, `GESPER_REMOTE_APP_DIR` (es. `/var/www/gesper-app`), `GESPER_SSH_IDENTITY` (es. `~/.ssh/id_ed25519`).

<a id="sec-firewall"></a>
## 7. Firewall (VPS)

Su Ubuntu: `ufw allow OpenSSH`, `ufw allow 80/tcp`, `ufw allow 443/tcp`, `ufw enable`.  
Eventuale firewall aggiuntivo nel pannello Aruba Cloud va coerente (stesse porte in ingresso).

<a id="sec-upstream"></a>
## 8. Upstream WordPress in Nginx (solo split)

L’`upstream plazapretoria_aruba_origin` in `nginx-gesper-production-split.conf` usa l’**IPv6** (o host) del piano hosting WordPress. Se Aruba cambia indirizzo, aggiornare l’upstream e verificare con `curl` come indicato nei commenti del file. **Non** applicabile alla sola modalità `gesper1.*` standalone.

<a id="sec-anti-conflitto"></a>
## 9. Anti-conflitto: regole operative

### Modalità separata (consigliata)

- **`gesper1`** (e opz. **`gesper`**) → **A** verso la **VPS**; **`www` / `@`** → **hosting** WordPress. Link dal sito vetrina a `https://gesper1.plazapretoria.it/` (o `gesper.…` se mantieni quel nome).
- Non aspettarsi `https://www…/gesper/` servito dalla VPS: se `www` punta all’hosting, risposte **403 / aruba-proxy** su quel path sono **coerenti** (GESPER non è lì).

### Split Nginx (opzionale)

- **Un solo ingresso per `www` (split attivo)**: record **`A` `www` → IP Cloud VPS**. Non lasciare `www` sull’IP hosting condiviso se vuoi `/gesper/` sulla stessa VPS che fa da reverse proxy.
- **IPv6**: o **`AAAA` `www` → stessa VPS** (IPv6 della VM), oppure **nessun AAAA** per `www` (evita che metà client vada su un backend e metà su un altro).
- **TTL DNS**: in fase di cambio, TTL basso (es. 300s); a regime riesumare valore normale.
- **TLS**: sul server live **solo Certbot** modifica i blocchi `443` e `ssl_*` (vedi §3–4). Nessun duplicato manuale.
- **Prima di edit Nginx sul server**: `sudo bash deploy/backup-nginx-site.sh` (backup timestampato).
- **Django `www` + `/gesper/`**: `settings_production_www` con `FORCE_SCRIPT_NAME`, `STATIC_URL` / `MEDIA_URL` sotto `/gesper/`, cookie path coerenti (già nel progetto).

### Esempio minimale zona DNS (split attivo)

| Nome | Tipo | Valore |
|------|------|--------|
| `www` | A | **IPv4 VPS** |
| `gesper1` | A | **IPv4 VPS** (sottodominio GESPER) |
| `gesper` | A | *Opz.* stesso IP (alias storico) |
| `@` | A | **IPv4 VPS** (solo se anche apex passa dal Nginx sulla VPS) |
| `www` | AAAA | IPv6 VPS **o** assente |
| MX / TXT posta | — | Invariati salvo migrazione mail |

<a id="sec-verify"></a>
## 10. Verifica rapida (dopo aggiornamento DNS)

Da qualsiasi PC connesso a Internet:

```bash
./deploy/verify-public-endpoints.sh
```

Il default verifica **`https://gesper1.plazapretoria.it`**. Per l’host storico:  
`GESPER_HOST=https://gesper.plazapretoria.it ./deploy/verify-public-endpoints.sh`

Backup config Nginx **sul server** prima delle modifiche:

```bash
sudo bash deploy/backup-nginx-site.sh
```

Opzionale, se conosci gli IPv4:

```bash
GESPER_EXPECT_GESPER_A='IP_VPS' ./deploy/verify-public-endpoints.sh
GESPER_EXPECT_WWW_A='IP_hosting_o_VPS' ./deploy/verify-public-endpoints.sh
```

**Interpretazione**

- **`https://gesper1.plazapretoria.it/…`** (o `gesper.…` se in DNS) con **`Server: nginx`** e **200** / **302**: sottodominio GESPER coerente con la VPS.
- **`https://www…/gesper/`** con **`Server: aruba-proxy`** e **403**: **normale in modalità separata** (`www` su hosting). Indica un problema solo se usi lo **split** e ti aspetti **nginx** su quel path.
- Con **split attivo**, `dig +short www.plazapretoria.it A` deve restituire l’**IP della Cloud VPS**.

---

*Aggiornamenti alla sola parte applicativa Nginx: versionare in `deploy/nginx-gesper-vps-standalone.conf` (o `nginx-gesper-production-split.conf` se usi lo split) e applicare sul server preservando i blocchi gestiti da Certbot.*
