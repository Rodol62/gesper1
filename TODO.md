# TODO GESPER

Questo file deve essere sempre aggiornato da tutti gli strumenti AI (Copilot, Claude, ecc.).
Usalo come unico punto di riferimento per tracciare attività, avanzamento e regole operative.

---

## Principio fondamentale

- L'ambiente principale di riferimento è la **produzione** (dati reali e immutabili).
- L'ambiente locale deve essere sempre aggiornato da produzione (codice + dati).
- Ogni implementazione parte da produzione e poi viene riportata in sviluppo.
- Nessuna modifica ai dati di produzione durante test o sviluppo.

---

## Procedura operativa persistente

Sequenza da seguire a ogni riavvio di VS Code:

**Regola prioritaria (sempre):** non toccare i dati di produzione reali senza processo; validare in **locale** (`manage.py check`, test manuali) e rilasciare su **VPS** secondo `deploy/PROCEDURA_DEPLOY.md` (Gunicorn + Nginx, tipicamente `https://gesper1.plazapretoria.it/` in root). L’ex stack `/gesper-test` (Gunicorn 8001) non è più in repo: sul server rimuovere `location /gesper-test/`, `gesper-test.service` e sotto `/var/www/gesper_test` se non servono.

1. `source .venv/bin/activate`
2. (opz.) allineare il locale da backup/clone autorizzato, non con sync diretti non controllati da produzione
3. `python3 manage.py check`
4. deploy su gesper1 solo dopo checklist; verifiche: `bash deploy/verify-public-endpoints.sh`

---

## Disciplina anti-confusione (obbligatoria)

Per ogni modifica prima del rilascio in produzione:

1. applicare solo patch incrementali, senza lasciare codice duplicato o rami morti.

1. rimuovere subito il codice sostituito nella stessa sessione.

1. aggiornare sempre questo TODO con: cosa è stato fatto, cosa è stato rimosso, cosa resta aperto.

1. validare sempre con `python3 -m py_compile <file_modificati>`, `python3 manage.py check` e, dove previsto, deploy controllato su `gesper1` (o ambiente di staging interno, non lo stack `gesper-test` rimosso).

1. non dichiarare chiusa una task senza evidenza su UI o log.

Regola di qualità: **niente workaround nascosti**, preferire un unico flusso/motore e convergere i percorsi legacy su alias puliti.

---

## Avanzamento operativo corrente (2026-04-04)

- [x] Unificata UX admin/consulente su dashboard documenti/buste.
- [x] Introdotta `natura_busta` su movimenti import (`ORDINARIA`, `TREDICESIMA`, `QUATTORDICESIMA`) con migrazione `accounts.0016`.
- [x] Import/preview aggiornati per evitare sovrascritture luglio/dicembre.
- [x] Introdotto motore unico anomalie import/export in `log_attivita/anomalie.py`.
- [x] Aggiunto dettaglio anomalia in UI import massivo.
- [x] Eliminati link ai PDF mancanti nelle dashboard (prevenzione 404 su file orfani).
- [x] Implementato motore unico CUD da PDF unico protetto (split pagine, deduplica copie, match CF) per admin/HR/consulente.
- [x] `consulente_upload_cud` convertita in alias legacy verso motore unico `documenti/upload-cud-massivo/`.
- [x] Pulizia codice: rimosso template legacy non usato `templates/consulente/upload_cud.html`.
- [ ] Completare ripristino file storici mancanti su storage (reimport o restore media; verificare su `gesper1` o da backup coerente).

---

## Avanzamento moduli

| Modulo                     | Stato       | Criticità principali                         | Dipendenze                           |
| :------------------------- | :---------- | :------------------------------------------- | :----------------------------------- |
| Autenticazione             | completato  | Sicurezza password, gestione sessioni        | Anagrafiche, Log                     |
| Anagrafiche                | in corso    | Coerenza dati, aggiornamenti massivi         | Autenticazione, Presenze, Documenti  |
| Workflow                   | in corso    | Blocchi di stato, gestione errori            | Autenticazione, Notifiche            |
| Calcoli                    | in corso    | Correttezza formule, aggiornamento parametri | Presenze, Rapporto di lavoro         |
| Presenze                   | in corso    | Sincronizzazione dati, gestione anomalie     | Anagrafiche, Calcoli                 |
| Documenti                  | da iniziare | Sicurezza file, versioning, storage          | Anagrafiche, Notifiche, Report       |
| Report                     | da iniziare | Performance, accuratezza dati                | Tutti i moduli                       |
| Log                        | in corso    | Volume dati, privacy, performance            | Tutti i moduli                       |
| Sincronizzazione/Notifiche | da iniziare | Affidabilità recapito, duplicazione notifiche| Tutti i moduli                       |

---

## Macro-attività aperte

- [ ] Configurare ambiente locale identico a produzione (versioni Python, Django, librerie, settings)
- [ ] Script per setup rapido di ambiente locale (requirements, settings, variabili ambiente)
- [ ] Documentare la procedura di avvio e test locale
- [ ] Stabilire workflow Git: branch di sviluppo, test, produzione
- [ ] Definire regole per merge e deploy (mai direttamente su produzione senza test)
- [ ] Automatizzare test e validazione prima del deploy

---

## Ricostruzione logica GESPER

Per ogni macro-componente, traccia lo stato di revisione e annota criticità o dipendenze.

- [ ] Autenticazione e gestione utenti
- [ ] Gestione anagrafiche
- [ ] Workflow richieste e approvazioni
- [ ] Calcolo costo lavoro e simulazioni
- [ ] Gestione presenze e rapporti di lavoro
- [ ] Gestione documenti e notifiche
- [ ] Reportistica e statistiche
- [ ] Log attività e sicurezza
- [ ] Sincronizzazione dati produzione/locale
- [ ] Altri moduli specifici (da dettagliare)

---

## Mappatura moduli principali

- **Autenticazione**
  - File: `accounts/models.py`, `accounts/views.py`, `accounts/forms.py`, `accounts/urls.py`
  - Classi/Funzioni: User, autenticazione custom, views login/logout/registrazione
  - Criticità: sicurezza password, gestione sessioni, escalation permessi

- **Anagrafiche**
  - File: `anagrafiche/models.py`, `anagrafiche/views.py`, `anagrafiche/forms.py`
  - Classi/Funzioni: Anagrafica, gestione dati anagrafici, form inserimento/modifica
  - Criticità: coerenza dati, aggiornamenti massivi

- **Workflow**
  - File: `accounts/views_richieste_integrazione.py`, `accounts/views_admin_candidati.py`
  - Classi/Funzioni: gestione richieste, avanzamento stato, validazione flussi
  - Criticità: blocchi di stato, gestione errori, tracciamento avanzamento

- **Calcoli**
  - File: `rapporto_di_lavoro/utils_motore_paga.py`, `utils_calcoli.py`
  - Classi/Funzioni: calcolo costo lavoro, simulazioni, gestione parametri
  - Criticità: correttezza formule, aggiornamento parametri normativi

- **Presenze**
  - File: `presenze/models.py`, `presenze/views.py`, `presenze/utils.py`
  - Classi/Funzioni: inserimento timbrature, gestione assenze, report presenze
  - Criticità: sincronizzazione dati, gestione anomalie

- **Documenti**
  - File: `documenti/models.py`, `documenti/views.py`
  - Classi/Funzioni: upload/download, generazione documenti, firma digitale
  - Criticità: sicurezza file, versioning, storage

- **Report**
  - File: `report/`
  - Classi/Funzioni: generazione report, esportazione dati, dashboard
  - Criticità: performance, accuratezza dati

- **Log**
  - File: `storico/models.py`, `storico/views.py`
  - Classi/Funzioni: tracciamento attività, audit, gestione errori
  - Criticità: volume dati, privacy, performance

- **Sincronizzazione/Notifiche**
  - File: `notifiche/`
  - Classi/Funzioni: invio email, notifiche push, sincronizzazione dati tra ambienti
  - Criticità: affidabilità recapito, duplicazione notifiche, gestione errori

---

## Gestione TODO nel codice

Usa l'estensione **Todo Tree** di VS Code per tracciare i commenti `TODO`/`FIXME`/`BUG` nel codice.
Esempio:

```python
# TODO: aggiungere controllo su input utente
# FIXME: gestire caso di errore connessione
```

Aggiorna questa lista man mano che procedi!

---

## Aggiornamenti operativi (2026-04-03)

- [x] Memorizzato il flusso operativo persistente: sync iniziale da produzione-test → lavoro riferito a produzione-test → nuova sync finale verso locale.
- [x] Memorizzato l'URL corretto dell'ambiente test: `https://gesper.plazapretoria.it/gesper-test/`.
- [x] Verificato che `https://plazapretoria.it/gesper-test/` mostra warning Firefox per mismatch certificato/hostname.
- [x] Corretto parser buste (admin): estrazione `NETTO BUSTA` e `TOTALE LORDO` ora prende il primo importo dopo etichetta (non l'ultimo valore della riga), riducendo errori sistematici.
- [x] Corretto bug in lettura PDF buste (`documenti/views.py`): ripristinato flusso `PdfReader` reale in `_extract_busta_importi_da_pdf` (prima bloccato da blocco `except` errato).
- [x] Corretto raggruppamento anni buste: in dashboard admin il periodo usa come fonte canonica `MovimentoImportPaghe` (`mese/anno`) quando presente, evitando attribuzioni all'anno di caricamento file.
- [x] Corretto parser periodo in `scripts/analizza_pdf_paghe.py`: priorità a pattern contestuali (mese retribuito/competenza) e fallback su ricorrenza più frequente, evitando anni anomali in pagina.
- [x] Produzione-test ripulita da record anomali legacy: rimosso movimento `BUSTA` con anno 2009 duplicato; distribuzione anni verificata su test -> solo `2024`.
- [x] Estrazione dati buste a video eseguita: `35` movimenti `BUSTA` (solo anno `2024`).
- [x] Verifica coerenza file documenti su produzione-test: `DOC_MANCANTI=35/35` (tutti i file PDF delle buste associati ai movimenti risultano non presenti su storage), quindi backfill `lordo/netto` da PDF non eseguibile finché non vengono ripristinati i file.

- [x] Documenti/Buste: struttura collapse aggiornata a **Anno → Mese → Dipendente**.
- [x] Documenti/Buste: filtro `Anno buste` allineato agli anni reali presenti nelle buste paga.
- [x] Documenti/Buste: normalizzazione visualizzazione anno (es. `2026`, non `2.026`).
- [x] Documenti/Buste: totali `Lordo/Netto` esposti a livello anno, mese e dipendente.
- [x] Documenti/Buste: azioni `Visualizza`/`Scarica` collegate agli endpoint applicativi sicuri.
- [x] Deploy su ambiente **test** (`/gesper-test`) con riavvio servizio `gesper-test`.
- [x] Documenti/Buste: campo filtro `Anno` allargato in UI per leggibilità.
- [x] Documenti/Buste: ricalcolo totali su gerarchia (Dipendente → Mese → Anno) per garantire che il totale anno sia la somma dei mesi.
- [x] Documenti/Buste: fallback lettura importi da PDF (netto/lordo best-effort) quando manca il movimento importato.
- [x] Documenti/Buste: persistenza lazy su `MovimentoImportPaghe` dei valori letti dal PDF per evitare ricalcoli successivi.
- [x] `MovimentoImportPaghe`: introdotti campi separati `importo_lordo` e `importo_netto` con migrazione dedicata.
- [x] Upload massivo buste consulente: upsert automatico movimenti `BUSTA` con popolamento `lordo/netto` (best-effort da PDF).
- [x] Upload buste admin/HR: upsert automatico movimento `BUSTA` al caricamento documento.
- [x] Pipeline import PDF unico (`analizza`/`preview`/`import`): supporto `lordo_busta` e salvataggio su `importo_lordo/importo_netto`.
- [x] Correzione parser importi busta: `lordo` letto da etichetta **Totale Lordo** e `netto` da **Netto Busta** (riduzione match ambigui).
- [x] Riallineamento automatico in vista Documenti/Buste: i movimenti vengono aggiornati con i valori estratti dal PDF quando presenti.
- [x] Parser ulteriormente irrigidito: estrazione del **primo importo dopo etichetta** (evita importi di colonne adiacenti sulla stessa riga).
- [x] Nuova funzione Admin/HR: **importazione massiva buste paga** (UI dedicata + creazione movimento `BUSTA` con `importo_lordo/importo_netto`).
- [x] UX importazione massiva Admin/HR aggiornata: nessuna lista dipendenti, upload da **cartella** o **file multipli** PDF.
- [x] Riepilogo post-import: per ogni file mostra dipendente associato, importo lordo/netto, link visualizzazione documento ed esito importazione.
- [x] Supporto PDF buste protetti da password: lettura automatica con password standard **DOLCEMASCOLO**.
- [x] Correzione input upload massivo: campi separati per **cartella** e **file multipli** per evitare blocchi di selezione PDF nel browser.
- [x] Revisione flusso Admin/HR upload massivo: ora usa **PDF unico mensile** come il consulente, con split delle singole buste, deduplica copie doppie e memorizzazione separata dell'F24.
- [x] Riepilogo Admin/HR esteso: esito per file importato + dettaglio delle singole buste generate dal PDF unico.
- [x] Controllo anti-duplicato in importazione PDF unico: le buste già presenti per **dipendente + mese + anno** vengono scartate e non reimportate.
- [x] Riepiloghi aggiornati con conteggio/visibilità delle buste **già presenti** scartate in fase di import.
- [x] Robustezza import PDF: `pdftotext` ora prova l'apertura con password **DOLCEMASCOLO**; fallback `pypdf` reso tollerante a pagine malformate (`extract_text` non blocca l'intero import).
- [x] Correzione 500 su `?categoria=buste`: in lista documenti il fallback di parsing PDF ora parte solo se manca il movimento importato (evita timeout da parsing massivo di PDF problematici).
- [x] Controllo completo mesi/file su produzione-test: verificati tutti i mesi `01-07/2024` con distinzione `file_ok`/`file_missing` e link ai movimenti `BUSTA`.
- [x] Elaborazione di tutti i file fisicamente presenti: estratti `lordo/netto` da tutti i PDF disponibili e aggiornati i movimenti collegati quando mancavano dati.
- [x] Riallineamento parziale mese `04/2024`: aggiornati 3 movimenti (`dip 23,24,25`) con importi da file presenti (`doc 68,69,70`) e ricollegato il documento al file realmente esistente.
- [x] Confermata situazione residua: mesi `02,03,05,06,07/2024` ancora bloccati da file mancanti su storage test.
- [x] Corretto `preview_import_paghe_pdf`: fallback periodo da nome file PDF (`MESE ANNO`) quando il parser pagina non riconosce `period_month/year`.
- [x] Corretto `import_paghe_pdf` con `--attach-docs`: se il `Documento` esiste ma il file manca su storage, ora rigenera e risalva il PDF della pagina (ripristino file mancanti).
- [x] Deploy patch su `gesper-test` e riavvio `gesper-test.service` completati.
- [x] Fix upload massivo web: `preview_import_paghe_pdf` ora riceve `--source-name` (nome file originale) da [documenti/views.py](documenti/views.py) per evitare fallback periodo su nome temp `/tmp/tmp*.pdf`.
- [x] Fix `import_paghe_pdf` per PDF cifrati: decriptazione esplicita del `PdfReader` sorgente (password standard + fallback vuoto) prima dello split pagine con `--attach-docs`.
- [x] Fix `import_paghe_pdf` su `already_present`: con `--attach-docs` ora ripristina comunque il file documento mancante e riallinea il link sul movimento senza creare duplicati.
- [x] Verifica finale post-reimport mensili 2024 su test: `file_ok` ora completo per tutti i mesi `01-12/2024` (copertura documenti ripristinata).
- [x] Nuovi movimenti importati `08-12/2024`: `importo_lordo` valorizzato, `importo_netto` ancora `NULL` (da migliorare parser netto su questi layout).
- [x] Fix dashboard buste: il fallback PDF in [documenti/views.py](documenti/views.py) ora aggiorna solo campi mancanti e non sovrascrive più `lordo/netto` già presenti (evita totali a zero per azzeramenti involontari).
- [x] Backfill importi buste 2024 su test completato: aggiornati `63` movimenti con `importo_netto` nullo (estrazione da PDF presenti) e allineati campi mancanti `importo`/`importo_lordo`.
- [x] Copertura importi 2024 ora completa per tutti i mesi `01-12` (`lordo` e `netto` valorizzati su tutte le buste).
- [x] Correzione dati test su `importo_lordo` 2024: riallineati `27` movimenti con lordo anomalo (valori base 400/720/800) usando il `TOTALE LORDO` estratto da PDF.
- [x] Totali post-correzione su test: `SUM_LORDO_2024=93844.12`, `SUM_NETTO_2024=79579.05` (lordo > netto come atteso).
- [x] UI F24 allineata allo stile Buste: nuova dashboard su `?categoria=f24` con struttura a fisarmonica **Anno → Mese**, KPI e azioni `Visualizza/Stampa` coerenti.
- [x] Fix 500 su `?categoria=f24`: inizializzazione difensiva di `buste_anni_disponibili`/`f24_anni_disponibili` in [documenti/views.py](documenti/views.py) (prima `UnboundLocalError` nel render context).
- [x] Estensione lato consulente: dashboard documenti `?categoria=buste` e `?categoria=f24` abilitate con lo stesso stile/struttura e filtro anno.
- [x] Estensione lato consulente: accesso abilitato a `upload_buste_paga_massivo` per import PDF unico (stesso flusso admin/HR, limitato alla propria azienda).
- [x] UX F24 migliorata: sezione filtri spostata sopra la dashboard `?categoria=f24` con supporto a filtro **anno singolo**, **intervallo da/a** o **tutti gli anni**.
- [x] Esteso filtro **intervallo anni (Da/A)** anche alla dashboard buste (`?categoria=buste`), mantenendo la stessa disponibilità lato consulente.
- [x] Fix somme F24 in dashboard (`admin` + `consulente`): se `MovimentoImportPaghe.F24` ha importo nullo, estrazione lazy da PDF (`SALDO FINALE`) e persistenza su `importo/importo_netto` in [documenti/views.py](documenti/views.py).
- [x] Fix import F24 con `--attach-docs`: se il `Documento` F24 esiste ma il file è mancante, [import_paghe_pdf.py](accounts/management/commands/import_paghe_pdf.py) ora rigenera e riallega il PDF mensile.
- [x] Pulizia completa ambiente test prima di nuova reimportazione 2024: rimossi movimenti `BUSTA/F24` anno 2024 e documenti/file importati correlati (reset dataset pulito).
- [x] Estensione schema DB per F24: aggiunti campi su `MovimentoImportPaghe` (`f24_tot_debito`, `f24_tot_credito`, `f24_saldo_finale`) con migrazione `accounts.0013`.
- [x] Dashboard F24 (admin + consulente) aggiornata con dettaglio debiti/crediti/saldo e persistenza lazy in DB dai PDF quando i valori sono mancanti.
- [x] Fix finale F24 su test: parser debiti/crediti reso robusto (layout `pdftotext` con grandi spazi/righe spezzate), backfill eseguito su 11 mesi 2024 e link file `Visualizza/Scarica` ripristinati su ogni riga mensile.
- [x] Correzione dati buste `08-12/2024` su test: riallineati `29` movimenti (lordo/netto) direttamente dai PDF per eliminare importi errati nelle singole buste.
- [x] Prevenzione futura: in [import_paghe_pdf.py](accounts/management/commands/import_paghe_pdf.py) l'upsert `BUSTA` usa priorità ai valori estratti dalla pagina PDF allegata (non solo dai campi preview).- [x] Correzione formula saldo F24: saldo ora calcolato come `debito - credito` (può essere negativo quando credito > debito, indicando compensazione dovuta). Backfill 11 mesi 2024 con valori corretti.
- [x] Aggiunta FK documento a `MovimentoImportPagheF24Dettaglio`: ogni riga dettaglio F24 ora collegata al file PDF originale per visualizzazione/download.
- [x] Fix template F24 dashboard: link `Visualizza/Stampa` corretti per accedere al documento tramite `r.movimento.documento` instead di `r.documento`. Tutti i 11 mesi 2024 ora mostrano file collegati.
