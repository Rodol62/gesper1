# HANDOFF GESPER - 2026-04-07

## Obiettivo prodotto (confermato)

Realizzare una procedura HR web (server Aruba) con accessi per:
- amministratore/supervisore;
- consulente del lavoro;
- dipendenti.

Flusso base da preservare:
1. registrazione/completamento profilo dipendente/candidato;
2. formulazione proposta di lavoro;
3. accettazione/firma proposta;
4. conversione proposta in contratto;
5. gestione documentale (buste paga, CU, allegati normativi, privacy, geolocalizzazione, mansioni);
6. operativita` dipendente (ferie/permessi, richieste, consultazione documenti);
7. lato azienda: gestione turni/presenze e simulazioni costo del lavoro.

Estensione prevista:
- app/PWA dipendente per documenti + check-in/check-out con geolocalizzazione.

## Contesto tecnico emerso

- Workspace locale: `xampp/htdocs/gesper` (non necessariamente servito da XAMPP).
- Produzione: server Aruba con dati fondamentali gia` in esercizio.
- Dati critici da salvaguardare: anagrafiche, contratti/parametri FIPE, buste paga, CU, documenti gia` caricati.
- L’ex ambiente “produzione-test” su `/gesper-test` è stato ritirato; riferimento produzione: `gesper1.plazapretoria.it` (o layout split `www/…/gesper/` ove attivo).

## Ricognizione architettura gia` eseguita

E` stata fatta una mappatura ad alto livello della codebase:
- progetto Django monolitico modulare;
- configurazione centrale in `settings.py`, routing in `urls.py`;
- app principali identificate: `accounts`, `anagrafiche`, `rapporto_di_lavoro`, `presenze`, `richieste`, `workflow`, `documenti`, `api`, `storico`, `log_attivita`, `notifiche_email`, `costo_lavoro`.

Nota importante:
- non e` ancora stata completata una lettura riga-per-riga end-to-end di tutta la logica.

## Decisione operativa condivisa

Prima di fare cleanup pesante:
1. proteggere dati produzione Aruba;
2. congelare il flusso canonico;
3. confrontare "flusso atteso vs flusso implementato";
4. solo dopo rimuovere codice superfluo/legacy.

## Piano di lavoro da riprendere

### Fase 1 - Audit completo (senza modifiche invasive)
- mappare procedure end-to-end:
  - candidato -> proposta -> firme -> contratto;
  - presenze -> riepilogo -> cedolino;
  - documenti -> consultazione/download;
  - richieste -> workflow approvazione -> notifiche -> storico.
- produrre matrice:
  - atteso (business);
  - implementato (codice reale);
  - gap/incoerenze/rischi.

### Fase 2 - Sicurezza dati e ambienti
- definire elenco tabelle "intoccabili" in produzione;
- separare chiaramente configurazioni locale/test/prod;
- stabilire backup e rollback pre-deploy.

### Fase 3 - Cleanup controllato
- classificare codice in:
  - attivo,
  - legacy compatibile,
  - non usato (candidato rimozione).
- rimuovere solo elementi a rischio basso in prima iterazione.

### Fase 4 - Consolidamento mobile/PWA
- allineare API e permessi ruolo;
- verificare check-in/out e geolocalizzazione con policy privacy.

## Vincoli da rispettare

- Non alterare dati reali Aruba senza backup verificato.
- Non rimuovere codice senza prova di inutilizzo.
- Mantenere compatibilita` con il flusso contrattuale FIPE piccola ristorazione.

## Prossimo task consigliato (quando si riprende)

Avviare immediatamente la "Fase 1 - Audit completo" con output scritto:
- `AUDIT_FLUSSI_END_TO_END.md`
- `MATRICE_GAP_LOGICI.md`
- `BACKLOG_BONIFICA_PRIORITARIO.md`

## Nota continuita` lavoro

Questo file e` il punto di ripartenza ufficiale per riprendere il lavoro dopo interruzioni di sessione o limiti piano.
