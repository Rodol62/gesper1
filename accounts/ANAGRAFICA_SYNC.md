# Source of Truth anagrafica candidato

Obiettivo: evitare ambiguita tra `User`, `ProfiloCandidato`, `Dipendente`.

## Entita e responsabilita

- `User` (`accounts.User`)
  - identita di accesso/autenticazione
  - campi principali: `first_name`, `last_name`, `email`, ruoli/permessi

- `ProfiloCandidato` (`accounts.ProfiloCandidato`)
  - dossier candidatura e dati onboarding
  - campi principali: CF, nascita, indirizzo, telefono, disponibilita, documenti, consensi
  - eventuale collegamento 1:1 a `Dipendente`

- `Dipendente` (`anagrafiche.Dipendente`)
  - anagrafica operativa HR per proposte/contratti/presenze/documenti
  - per i candidati usa `stato='candidato'`

## Regola di sincronizzazione

Sorgente primaria per candidato:

- `User` per nome/cognome/email
- `ProfiloCandidato` per dati anagrafici e candidatura

Record derivato:

- `Dipendente` viene aggiornato da User+Profilo tramite servizio unico:
  - `accounts.sync_anagrafica.sincronizza_dipendente_da_profilo(...)`

## Note operative

- Flussi espliciti (creazione/assegnazione) chiamano il servizio con `create_if_missing=True`.
- Signal automatici usano `create_if_missing=False` per evitare creazioni indesiderate.
- Evitato aggancio a `Dipendente` gia associato ad altro profilo candidato.
