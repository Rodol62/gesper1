# GESPER

Documento principale di progetto:

- [DOCUMENTAZIONE_UNICA_GESPER.md](DOCUMENTAZIONE_UNICA_GESPER.md)

Questa documentazione unica contiene:

- architettura e logica applicativa;
- flussi proposta/contratto/simulazione;
- linee guida motore retributivo;
- procedure di verifica e cleanup;
- storico consolidato interventi recenti.

## Trasferimenti ambienti

Script ufficiali:

- `scripts/locale_a_produzione.sh` (codice + DB + media da locale a produzione)
- `scripts/produzione_a_locale.sh` (codice + DB + media + staticfiles da produzione a locale)
- `scripts/segnala_ambiente.sh` (imposta/mostra ambiente operativo `LOCALE`/`PRODUZIONE`)

Guida rapida:

- `scripts/TRASFERIMENTI_AMBIENTI.md`

## Mappature Mansioni-Livelli CCNL

Procedura operativa HR/Admin:

- `rapporto_di_lavoro/MAPPATURE_MANSIONI_OPERATIVO.md`

## Cache Busting JS Proposta

Per forzare il refresh automatico del file JS della pagina proposta (`crea_proposta`), usa la variabile ambiente:

- `GESPER_PROPOSTA_JS_VERSION`

Esempio (file `.env` nella root progetto):

```env
GESPER_PROPOSTA_JS_VERSION=2026-04-16-1
```

Quando aggiorni `static/js/proposta_form_dinamico.js`, incrementa la versione (es. `...-2`, `...-3`) e riavvia il server.
