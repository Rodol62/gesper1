<!-- markdownlint-disable MD022 MD024 MD032 -->

# Matrice Flussi e Schermate - GESPER

## Scopo

Allineare processi business, schermate, permessi ruolo e stati operativi.

Uso pratico:
1. verificare coerenza logica del flusso;
2. verificare coerenza UI tra schermate;
3. identificare gap e duplicazioni legacy.

---

## Flusso 1 - Onboarding candidato/dipendente

### Obiettivo
Portare un candidato da registrazione a profilo completo validato.

### Ruoli coinvolti
- Candidato
- Admin/Supervisore

### Step attesi
1. Registrazione utente
2. Verifica email
3. Compilazione profilo
4. Upload documenti personali
5. Richiesta integrazione (se mancano dati)
6. Convalida finale admin

### Schermate chiave
- Login/registrazione
- Profilo candidato
- Dettaglio candidato admin
- Lista richieste integrazione

### Controlli coerenza
- Stato profilo sempre visibile.
- Azioni disponibili coerenti con stato.
- Errori campi obbligatori chiari.

---

## Flusso 2 - Proposta di lavoro -> Contratto

### Obiettivo
Creare proposta, gestire firme/approvazioni, convertire in contratto.

### Ruoli coinvolti
- Admin/Supervisore
- Consulente (se previsto in approvazione)
- Dipendente/Candidato

### Step attesi
1. Creazione proposta
2. Invio al dipendente
3. Firma/accettazione dipendente
4. Firma definitiva admin
5. Conversione in contratto
6. Generazione allegati/documenti contrattuali

### Schermate chiave
- Lista proposte
- Dettaglio proposta
- Firma proposta dipendente
- Stampa/PDF proposta
- Dettaglio contratto

### Controlli coerenza
- Stati proposta univoci e non duplicati.
- Pulsanti approvazione/rifiuto sempre coerenti.
- PDF coerente con dati runtime del motore.

---

## Flusso 3 - Presenze e riepilogo mensile

### Obiettivo
Gestire presenze giornaliere e produrre riepilogo mensile per motore paga.

### Ruoli coinvolti
- Supervisore/Admin
- Consulente (consultazione/elaborazione)
- Dipendente (consultazione)

### Step attesi
1. Pianificazione orari
2. Inserimento/aggiornamento presenze
3. Classificazione causali/straordinari
4. Aggregazione mensile
5. Revisione/approvazione riepilogo
6. Passaggio al motore paga

### Schermate chiave
- Calendario presenze dipendente
- Pianificazione oraria annuale/mensile
- Riepilogo mese
- Anteprima cedolino da riepilogo

### Controlli coerenza
- Causali e legende uniformi in tutte le viste.
- Stati riepilogo coerenti (`bozza`, `revisione`, `approvata`, `elaborata`).
- Nessun ricalcolo consentito su stati bloccati.

---

## Flusso 4 - Documenti dipendente (busta paga, CU, altri)

### Obiettivo
Consentire upload, consultazione, download e storico documenti.

### Ruoli coinvolti
- Consulente del lavoro
- Admin/Supervisore
- Dipendente

### Step attesi
1. Upload documento (singolo o massivo)
2. Classificazione (busta paga, CU, F24, altro)
3. Associazione a dipendente/periodo
4. Consultazione e download

### Schermate chiave
- Lista documenti
- Lista buste paga
- Lista CU
- Documenti dipendente

### Controlli coerenza
- Filtri data/tipo coerenti.
- Naming file e metadati standard.
- Permessi download coerenti per ruolo.

---

## Flusso 5 - Richieste dipendente e approvazioni

### Obiettivo
Gestire richieste operative (ferie, permessi, chiarimenti) con workflow.

### Ruoli coinvolti
- Dipendente
- Admin/Supervisore
- Consulente (se destinatario)

### Step attesi
1. Invio richiesta
2. Apertura workflow approvazione
3. Azione approvatore (approva/rifiuta)
4. Notifica esito
5. Chiusura richiesta

### Schermate chiave
- Lista richieste
- Dettaglio richiesta
- Lista "da approvare"

### Controlli coerenza
- Stato richiesta sempre allineato al workflow.
- Tracciamento eventi/storico disponibile.
- Notifiche inviate sugli eventi principali.

---

## Flusso 6 - Simulazione costo del lavoro

### Obiettivo
Supportare decisioni su organico attuale/futuro e impatto economico.

### Ruoli coinvolti
- Admin/Supervisore
- Consulente (supporto)

### Step attesi
1. Selezione scenario (organico attuale/nuovo)
2. Configurazione parametri
3. Calcolo simulazione
4. Analisi risultato
5. Export PDF/Excel

### Schermate chiave
- Configurazione simulazione
- Risultato simulazione
- Storico scenari

### Controlli coerenza
- Parametri CCNL e fiscali con decorrenza corretta.
- Indicatori economici presentati con stessa formattazione.
- Export coerente con dati mostrati a video.

---

## Flusso 7 - API/PWA dipendente

### Obiettivo
Fornire accesso mobile alle funzioni principali dipendente.

### Funzioni attese
- login JWT + refresh
- stato check-in/check-out
- presenze e ferie/permessi
- documenti e download
- notifiche
- profilo

### Controlli coerenza
- Parita` funzionale minima web vs API.
- Gestione 2FA coerente con sicurezza globale.
- Messaggi errore API prevedibili e uniformi.

---

## Matrice permessi sintetica (target)

- `Dipendente`: consulta dati personali, documenti propri, invia richieste, usa check-in/out.
- `Consulente`: gestione documentale paghe/CU, supporto approvazioni, consultazione presenze.
- `Admin/Supervisore`: controllo completo flussi HR, contratti, presenze, simulazioni, convalide.

Regola: ogni schermata deve dichiarare chiaramente i ruoli ammessi.

---

## Backlog incoerenze da compilare durante audit

Compilare tabella durante ricognizione:

| Flusso | Schermata/modulo | Problema rilevato | Tipo (logico/UI/permessi) | Priorita` | Azione proposta |
| --- | --- | --- | --- | --- | --- |
| Onboarding | `accounts/views_admin_candidati.py` (`_build_iter`) | Step "Proposta inviata" marcato come completato con semplice `bool(proposta)` anche su bozza/rifiutata | logico+UI | alta | ✅ step vincolato a stati proposta attivi/coerenti (bozza/rifiutata escluse) |
| Proposta->Contratto | `rapporto_di_lavoro/models.py` (`PropostaAssunzione.STATO_CHOICES`) | Coesistenza stati canonici e legacy nella stessa macchina a stati | logico | alta | ✅ policy canonica completata: mapping centralizzato + normalizzazione scritture legacy in stato canonico |
| Proposta->Contratto | `accounts/views_candidato.py` + `accounts/views_admin_candidati.py` | Fallback legacy su stati/contratti storici (`proposta`, `accettata_dipendente`, ecc.) | logico+manutenibilita` | alta | 🟡 parziale: introdotti helper equivalenza stato in modello e adottati nei flussi candidato principali; proseguire dismissione fallback residui |
| Onboarding | `accounts/views.py.updated` | File parallelo non standard potenzialmente obsoleto | manutenibilita` | alta | ✅ verificata non-referenza e rimosso file parallelo obsoleto |
| Documenti | `documenti/views.py` (`_assert_documento_accesso`) | Per admin/HR manca check esplicito azienda del documento | permessi/sicurezza | alta | ✅ applicato ACL tenant-aware anche su accesso diretto per ID |
| Documenti | `documenti/views.py` (`elimina_documento`) | Admin/HR eliminano senza verifica azienda record | permessi/sicurezza | alta | ✅ applicato controllo azienda prima delete |
| Presenze->Cedolino | `presenze/views.py` (`riepilogo_mensile_motore`) | Operazioni includono anche dipendenti in stato `candidato` | logico | media | ✅ policy applicata: aggregazione/lista motore limitata a dipendenti `attivo` |
| Documenti | `documenti/models.py` (`TIPI_PERSONALI`) | Categoria personale include `altro` (F24) | logico+UI | media | ✅ separata tassonomia: F24 non più caricabile come documento personale |
| Richieste | `richieste/views.py` | Gate ruoli usa `user.ruolo` legacy invece di `has_ruolo` | permessi/logico | alta | ✅ migrati gate principali alla semantica ruoli M2M |
| Richieste | `richieste/views.py` (`dettaglio/rispondi/chiudi/elimina`) | Lookup richiesta per `id` senza filtro azienda operativa | permessi/sicurezza | alta | ✅ applicato filtro tenant sulle action sensibili |
| Workflow | `workflow/services.py` (`_resolve_approvatore`) | Ricerca approvatore su campo `ruolo` non coerente con modello utente | logico/permessi | alta | ✅ risoluzione approvatore via `ruoli__codice` |
| Richieste+Workflow | `richieste` + `workflow` | Doppio percorso di stato (diretto vs step workflow) | logico+UI | media | ✅ applicata policy unica lato server: con workflow pending le azioni dirette sono bloccate; gestione demandata a `workflow` |
| API/PWA richieste | `api/views.py` (`notifiche_view`, `ferie_view`) | API mostrava stato semplificato non coerente col workflow | logico+UX | media | ✅ allineato stato operativo `in_approvazione` quando esiste workflow pending |
| Simulazione costo lavoro | `rapporto_di_lavoro/views.py` | Coesistenza percorsi di calcolo (`legacy`, `costo_lavoro`, motore canonico) | logico | alta | 🟡 parziale: introdotto helper unico che usa il motore canonico come default, con fallback legacy esplicito e tracciato a log |
| Simulazione costo lavoro | `costo_lavoro/*` | Doppio stack interno (`engine/*` e `rules/*`) | manutenibilita` | media | ✅ consolidato: `rules/*` mantenuto come compatibility layer verso implementazione unica `engine/*` |
| Simulazione costo lavoro | `costo_lavoro/servizio_integrazione.py` | Integrazione parziale con metodi incompleti | manutenibilita` | bassa | ✅ completato: implementato percorso contratto e hardening campi opzionali |

---

## Criteri di accettazione globali

Un flusso e` considerato "coerente" quando:
1. gli stati sono univoci e completi;
2. le azioni sono permesse solo ai ruoli corretti;
3. i dati mostrati sono consistenti tra lista/dettaglio/export;
4. la UI usa componenti e terminologia standard;
5. gli eventi principali sono tracciati (log/storico/notifiche).
