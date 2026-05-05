# Trasferimenti ambienti (LOCALE ↔ PRODUZIONE)

Questo progetto usa due script principali:

- `scripts/locale_a_produzione.sh`
- `scripts/produzione_a_locale.sh`

e un file stato ambiente:

- `.ambiente_operativo` (gestito da `scripts/segnala_ambiente.sh`)

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

## 2) Da PRODUZIONE a LOCALE

```bash
bash scripts/produzione_a_locale.sh
```

Se sulla VPS il database **non** è in `/var/www/gesper/db.sqlite3` ma sotto **`GESPER_DATA_ROOT`** (es. `/var/www/gesper/documento` come in `deploy/PROCEDURA_DEPLOY.md`), allinea **DB + media** così:

```bash
# Verifica sulla VPS: grep GESPER_DATA_ROOT /etc/gesper.env
REMOTE_DATA_ROOT=/var/www/gesper/documento bash scripts/produzione_a_locale.sh --data-only --yes
```

(`--db-only` e `--media-only` da soli si escludono a vicenda se passati insieme; usa **`--data-only`** per DB + media in un colpo.)

Lo script copia il DB in `gesper/db.sqlite3` **e** in `gesper/documento/db.sqlite3` (stesso contenuto), e i media sia in `media/` sia in `documento/media/`, così `settings.py` trova i file qualunque sia l’ordine di risoluzione dei path in locale.

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
