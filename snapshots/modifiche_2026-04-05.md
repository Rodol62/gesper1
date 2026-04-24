# GESPER — Registro modifiche del 05/04/2026

## Presenze / Pianificazione orari

- Refactor della pianificazione da logica annuale a logica **mensile persistita**.
- Aggiunti nuovi modelli in `presenze.models`:
  - `ConfigurazioneOrarioMensile`
  - `FasciaAperturaMensile`
- Aggiunte form dedicate in `presenze.forms`:
  - `ConfigurazioneOrarioMensileForm`
  - `FasciaAperturaMensileForm`
- Aggiornata la view `pianificazione_orari_annuale` in `presenze.views`:
  - selezione mese in modifica,
  - salvataggio su tabella mensile,
  - opzione "applica a tutti i mesi dell’anno" (propagazione configurazione e fasce).
- Aggiornata generazione teorica in `presenze.views`:
  - `_genera_presenze_teoriche_mese_azienda` usa tabella mensile come fonte primaria,
  - fallback annuale dove necessario.
- Aggiornate chiusure e ore standard in `presenze.views`:
  - `_get_chiusura_settimanale_presenze` priorità mensile,
  - `_ore_std_giornaliere` prima cerca fasce mensili, poi annuali.
- Aggiornato template `templates/presenze/pianificazione_orari_annuale.html`:
  - selettore mese,
  - testi coerenti con configurazione mensile,
  - checkbox "applica a tutti i mesi".

## Migrazioni

- Creata e applicata migrazione:
  - `presenze/migrations/0012_configurazioneorariomensile_fasciaaperturamensile.py`

## Allineamenti funzionali eseguiti oggi (sessione)

- Popolamento presenze teoriche esteso anche a dipendenti con stato `candidato` (coerenza con riepilogo mensile).
- Rigenerazione teoriche 2026 effettuata sui mesi 1..12 in ambiente test.
- Correzioni UI/UX riepilogo e pianificazione già integrate durante la giornata (fasce mattina/pomeriggio, coerenza domeniche/riposi).

## Geolocalizzazione e timbratura (sessione odierna)

- Consolidata sezione impostazioni geolocalizzazione in `Impostazioni > Sito`.
- Aggiunto supporto acquisizione coordinate da indirizzo/maps/posizione corrente.
- Migliorata gestione errori GPS con messaggi e retry.
- Aggiornata logica test user (`geo.test.presenze`) per flussi di prova.
- Gestito bypass limitazioni turni in contesto test.
- Corretto timezone applicativo a `Europe/Rome`.

## Verifiche

- `py_compile` su file modificati: OK
- `manage.py check`: OK
- `manage.py migrate presenze`: OK

## Deploy

- Deploy test completato con `scripts/deploy_test.sh`
- Migrazione `presenze.0012` applicata su test: OK
- Servizio `gesper-test` riavviato e attivo: OK
- URL test: [https://plazapretoria.it/gesper-test/](https://plazapretoria.it/gesper-test/)

## Aggiornamento serale (05/04/2026)

- Risolto problema "pianificazione non memorizzata" su pagina pianificazione orari:
  - introdotto fallback al **mese mensile più recente** salvato nell'anno,
  - memorizzazione mese selezionato in sessione (`presenze_pianificazione_mese_sel`) per riapertura coerente.
- Risolto problema domenica sempre a riposo in riepilogo mensile quando mancano parametri del mese:
  - `_get_chiusura_settimanale_presenze` ora usa: mese richiesto → ultimo mese configurato nell'anno → annuale → legacy.
- Allineato default mese anche in `riepilogo_mese`:
  - fallback a ultimo mese configurato,
  - mese selezionato salvato in sessione (`presenze_riepilogo_mese_sel`).
- Deploy test rieseguito dopo i fix: OK (nessuna nuova migration, service running).

## Aggiornamento notturno (05/04/2026)

- Aggiunta conferma esplicita del salvataggio in pagina pianificazione orari:
  - messaggio di successo con `ID` configurazione e timestamp ultimo aggiornamento,
  - pannello "Conferma memorizzazione DB (lettura live)" con valori riletti dal database.
- Aggiunta funzione di interrogazione dati DB da UI:
  - endpoint JSON: `/presenze/pianificazione-orari/db-parametri/?anno=YYYY&mese=MM`.
- Migliorata leggibilità errori in pagina pianificazione:
  - visualizzazione `non_field_errors` di form e formset.
- Deploy test completato dopo aggiornamento: OK.

## Allineamento riepilogo mensile (05/04/2026)

- Corretto il calcolo giorni di chiusura usato in `/presenze/riepilogo/` e nelle altre viste presenze:
  - priorità lettura dalle **fasce mensili** (`FasciaAperturaMensile.chiuso`),
  - fallback a `giorni_riposo_settimanale` solo se le fasce non sono disponibili,
  - stesso approccio per fallback annuale (`FasciaAperturaSettimanale.chiuso`).
- Effetto: il riepilogo mensile ora segue i parametri reali della pianificazione orari e non forza più la domenica come riposo se nel planning è lavorativa.
- Deploy test rieseguito: OK (service running).

## Validità contrattuale nelle presenze (05/04/2026)

- Corretto `/presenze/` (lista dipendenti):
  - conteggi `registrate/giorni_lavorativi/mancanti` calcolati solo nell'intersezione tra mese selezionato e periodo contratto del dipendente.
  - stato "N/A (fuori contratto)" quando il dipendente non è in forza nel mese.
- Corretto `/presenze/riepilogo/` (griglia mensile):
  - ore e presenze conteggiate solo nei giorni dentro la validità contrattuale,
  - giorni fuori validità mostrati come celle neutre (`-`) e non più come mancanti/riposi.
- Deploy test completato dopo fix: OK.

## Export presenze (05/04/2026)

- Aggiornata funzione export presenze in [presenze/views.py](presenze/views.py):
  - supporto formato `xlsx` e `csv` (`?formato=xlsx|csv`),
  - export limitato al **solo mese selezionato**,
  - export limitato ai **soli dipendenti attivi**,
  - applicata validità contrattuale: inclusi solo i giorni nel periodo valido del rapporto.
- Ore nel file normalizzate in decimale a due cifre:
  - XLSX con formato numerico `0.00`,
  - CSV con formato italiano `x,xx` (separatore `;`).
- Aggiunti pulsanti UI "Esporta CSV" in:
  - [templates/presenze/riepilogo_mese.html](templates/presenze/riepilogo_mese.html)
  - [templates/presenze/dipendenti.html](templates/presenze/dipendenti.html)
- Deploy test completato: OK.
