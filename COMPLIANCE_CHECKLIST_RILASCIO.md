<!-- markdownlint-disable MD032 -->

# Compliance Checklist Pre-Rilascio (Italia)

Checklist operativa per rilasci su produzione (Aruba), con focus:
- diritto del lavoro;
- adempimenti fiscali/contributivi;
- privacy/GDPR;
- tracciabilita` e sicurezza.

> Nota: questa checklist supporta la conformita` tecnica/organizzativa.  
> La validazione normativa finale resta in capo a consulente del lavoro / commercialista / legale.

## 1) Blocco sicurezza dati (obbligatorio)

- [ ] Backup DB completo e verificato (ripristino testato).
- [ ] Backup media/documenti completo e verificato.
- [ ] Nessuna migrazione distruttiva su tabelle critiche senza piano rollback.
- [ ] Controlli tenant attivi su documenti/richieste/azioni staff.
- [ ] Permessi ruolo verificati (`admin`, `hr`, `consulente`, `dipendente`, `candidato`).

## 2) Integrita` flussi lavoro

- [ ] Workflow candidato -> proposta -> contratto segue stato canonico unico.
- [ ] Richieste: nessun conflitto tra percorso diretto e workflow approvativo.
- [ ] Presenze -> riepilogo -> cedolino: transizioni stato rispettate.
- [ ] Nessuna azione sensibile disponibile in stato non consentito.

## 3) Contrattualistica e documenti

- [ ] Contratto, privacy, mansionario e allegati correttamente associati al dipendente.
- [ ] Tracciamento firma (timestamp, utente, IP, luogo) presente dove previsto.
- [ ] Versioni documentali coerenti (evitare duplicati ambigui).
- [ ] ACL documenti verificata per ogni ruolo.

## 4) Parametri fiscali/contributivi

- [ ] Parametri CCNL attivi con decorrenza corretta nel periodo elaborato.
- [ ] Scaglioni IRPEF / detrazioni / addizionali caricati e coerenti con anno.
- [ ] Aliquote INPS/INAIL/TFR verificate su tabelle parametrizzate.
- [ ] Casi campione confrontati con output atteso (consulente).

## 5) Presenze e assenze

- [ ] Classificazione causali e straordinari coerente con policy aziendale/CCNL.
- [ ] Regole domenicali/festivi/chiusure aziendali validate.
- [ ] Blocco ricalcolo su riepiloghi approvati/elaborati funzionante.
- [ ] Anteprima cedolino disponibile solo su stati ammessi.

## 6) API/PWA dipendente

- [ ] Accesso JWT + refresh + 2FA operativo.
- [ ] Endpoint dipendente non espongono dati di altri utenti.
- [ ] Download documenti API limitato al proprietario.
- [ ] Notifiche push e sottoscrizioni funzionanti (subscribe/unsubscribe).

## 7) Audit e tracciabilita`

- [ ] Log attivita` registrato su cambi stato principali.
- [ ] Eventi storici su firma/contratto/documenti rilevanti presenti.
- [ ] Error logging attivo e consultabile.
- [ ] Nessuna eccezione silenziata in punti critici senza fallback tracciato.

## 8) UX professionale (pre go-live)

- [ ] Badge stato e terminologia uniformi in tutte le pagine.
- [ ] Messaggi errore comprensibili (no tecnicismi non necessari).
- [ ] Azioni distruttive con conferma esplicita.
- [ ] Flussi principali verificati end-to-end per ogni ruolo.

## 9) Gate di rilascio

Rilascio consentito solo se:
- [ ] tutti i controlli sezioni 1-4 sono verdi;
- [ ] nessun blocco **critico** aperto in `BACKLOG_BONIFICA_PRIORITARIO.md` (valutare esplicitamente eventuali voci ancora 🟡 parziali, es. H10 simulazioni, H3 fallback legacy residui, M3 URL legacy);
- [ ] test smoke superati su produzione/staging:
  - login + profilo;
  - richiesta + approvazione;
  - proposta -> contratto;
  - presenze -> riepilogo -> anteprima cedolino;
  - upload/download documenti.

## 10) Procedura operativa rilascio (ordine pratico)

Eseguire **prima su staging/test**, poi su produzione. Adattare percorsi e comandi al proprio hosting (Aruba: SSH, pannello, virtualenv, ecc.).

### A) Prima del deploy

- [ ] Opzionale in sviluppo: eseguire `scripts/run_checks.sh` (`django check` + test automatici).
- [ ] Tag/commit di riferimento annotato (hash o tag versione).
- [ ] Backup DB (dump) e verifica dimensione/file non vuoto.
- [ ] Backup cartella `media/` (documenti caricati).
- [ ] Ambiente target: `DEBUG=False`, `SECRET_KEY` e credenziali DB da variabili sicure, `ALLOWED_HOSTS` corretto.
- [ ] Dipendenze allineate: `pip install -r requirements.txt` (o equivalente) su ambiente target.
- [ ] Migrazioni: `python3 manage.py showmigrations` confrontato con locale; nessuna migrazione “a sorpresa” non revisionata.

### B) Deploy applicazione (tipico Django)

- [ ] Mettere in manutenzione breve (opzionale) o pianificare finestra a basso traffico.
- [ ] Pubblicare codice (git pull / rsync / pipeline CI) nella directory applicativa corretta.
- [ ] `python3 manage.py migrate` (solo dopo backup).
- [ ] `python3 manage.py collectstatic --noinput` (se si usano static compressi).
- [ ] Riavvio processo WSGI/ASGI (gunicorn/uwsgi/mod_wsgi) o servizio systemd secondo setup Aruba.

### C) Subito dopo il deploy (smoke)

- [ ] Home/login risponde 200, nessun traceback in log.
- [ ] Login admin + HR + dipendente (almeno un utente per ruolo).
- [ ] Una richiesta in workflow: verifica badge/coda approvazioni.
- [ ] Un documento: download con ACL corretta.
- [ ] Log: cercare `[DEPRECATION]` e `[SIMULAZIONE_MOTORE_UNICO]` per monitorare uso percorsi legacy/fallback.

### D) Rollback (se smoke fallisce)

- [ ] Ripristinare versione codice precedente (stesso commit/tag del backup).
- [ ] Ripristinare DB solo se migrate hanno introdotto schema incompatibile (piano documentato **prima** del go-live).
- [ ] Verificare di nuovo smoke minimo dopo rollback.

### E) Post go-live (prime 24-48 ore)

- [ ] Monitoraggio errori server e log applicativi.
- [ ] Conferma con consulente eventuali controlli numerici su buste/simulazioni (H10).
- [ ] Aggiornare `BACKLOG_BONIFICA_PRIORITARIO.md` e `AUDIT_FLUSSI_END_TO_END.md` con data rilascio e note osservate.
