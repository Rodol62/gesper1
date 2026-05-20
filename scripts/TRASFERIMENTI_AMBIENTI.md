# Trasferimenti ambienti (LOCALE ↔ PRODUZIONE)

**Flusso ufficiale:** `deploy/DEPLOY_STANDARD.md` e comando unico:

```bash
./deploy/gesper.sh pull-data    # produzione → locale (dati)
./deploy/gesper.sh push-code    # locale → produzione (codice)
./deploy/gesper.sh push-data    # locale → produzione (solo dati, eccezionale)
```

Script sottostanti (chiamati da `gesper.sh` o direttamente):

- `scripts/produzione_a_locale.sh` — pull dati/codice da VPS
- `scripts/locale_a_produzione.sh` — push dati verso VPS (**non** usare `--code-only`; vedi `deploy/DEPRECATED.md`)

Stato ambiente: `.ambiente_operativo` (`scripts/segnala_ambiente.sh`).

**Radice dati produzione (default):** `REMOTE_DATA_ROOT=/var/www/gesper` (= `GESPER_DATA_ROOT` in `/etc/gesper.env`).

## 1) Da LOCALE a PRODUZIONE

```bash
bash scripts/locale_a_produzione.sh
```

Opzioni utili:

```bash
bash scripts/locale_a_produzione.sh --yes
bash scripts/locale_a_produzione.sh --code-only
bash scripts/locale_a_produzione.sh --db-only
bash scripts/locale_a_produzione.sh --media-only
```

Se in produzione è impostato **`GESPER_DATA_ROOT`** (es. su gesper1: `grep GESPER_DATA_ROOT /etc/gesper.env`), passa la stessa radice così DB e media finiscono dove usa Django:

```bash
./deploy/gesper.sh push-data --yes
```

## 2) Da PRODUZIONE a LOCALE

```bash
bash scripts/produzione_a_locale.sh
```

Se sulla VPS il database **non** è in `/var/www/gesper/db.sqlite3` ma sotto **`GESPER_DATA_ROOT`** (es. `/var/www/gesper/documento` come in `deploy/PROCEDURA_DEPLOY.md`), allinea **DB + media** così:

```bash
./deploy/gesper.sh pull-data --data-only --yes
```

(`--db-only` e `--media-only` da soli si escludono a vicenda se passati insieme; usa **`--data-only`** per DB + media in un colpo.)

Lo script copia il DB in `gesper/db.sqlite3` **e** in `gesper/documento/db.sqlite3` (stesso contenuto), e i media in `gesper/media/`, `gesper/documento/media/` **e** `htdocs/media/` (su XAMPP `settings.py` usa spesso `htdocs/media` se la cartella esiste, prima della cartella `media` sotto il progetto).

Opzioni utili:

```bash
bash scripts/produzione_a_locale.sh --yes
bash scripts/produzione_a_locale.sh --code-only
bash scripts/produzione_a_locale.sh --db-only
bash scripts/produzione_a_locale.sh --media-only
bash scripts/produzione_a_locale.sh --data-only
```

## 3) Segnalazione ambiente operativo

```bash
bash scripts/segnala_ambiente.sh locale
bash scripts/segnala_ambiente.sh produzione
bash scripts/segnala_ambiente.sh show
```

Lo stato corrente viene scritto in `.ambiente_operativo`.

## Note sicurezza operative

- Gli script chiedono conferma esplicita (`Digita SI`), salvo `--yes`.
- I backup locali/remoti del DB vengono creati prima della sovrascrittura.
- La sincronizzazione media usa `--delete`: allinea davvero i file tra sorgente e destinazione.
- Default directory applicazione su produzione: `/var/www/gesper` (stesso `WorkingDirectory` del servizio `gesper`; override: `REMOTE_APP_DIR`).
- Default media remoto: `/var/www/media` (override possibile con variabile `REMOTE_MEDIA_DIR`).
- Dopo deploy su produzione viene eseguito:
  - `migrate`
  - `collectstatic`
  - `check`
  - `systemctl restart gesper`
