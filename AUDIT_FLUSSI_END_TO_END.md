<!-- markdownlint-disable MD022 MD024 MD032 MD038 -->

# Audit Flussi End-to-End

Data avvio: 2026-04-07  
Stato: passate flussi completate (1-7), pronto piano esecutivo

## Ambito prima passata

- Flusso 1: onboarding candidato/dipendente
- Flusso 2: proposta di lavoro -> contratto

## Esito sintetico

- Architettura funzionale presente e gia` molto avanzata.
- Flusso canonico moderno individuabile.
- Permangono elementi legacy mescolati al flusso nuovo che generano confusione logica e visiva.

## Flusso 1 - Onboarding (stato audit)

### Flusso atteso (canonico)
1. Registrazione
2. Verifica email
3. Completamento profilo
4. Eventuale integrazione richiesta da HR
5. Convalida HR
6. Creazione/invio proposta

### Flusso implementato (evidenze)
- Iter costruito in `accounts/views_admin_candidati.py` (`_build_iter`, `_step_corrente`).
- Gestione integrazione con `RichiestaIntegrazioneCandidato` e stati:
  - `inviata`,
  - `completata_candidato`,
  - `approvata_hr`.
- Convalida vincolata a email verificata + profilo completo + eventuale integrazione approvata.

### Gap rilevati
- Step "Proposta inviata" marcato done con sola esistenza proposta, senza filtro stato.
- Mappature step distribuite in piu` funzioni, rischio divergenza badge/etichette.

## Flusso 2 - Proposta -> Contratto (stato audit)

### Flusso atteso (canonico)
1. `bozza`
2. `inviata_candidato`
3. `firmata_candidato`
4. `contratto_attivo` (con creazione `RapportoDiLavoro` sottoscritto)

### Flusso implementato (evidenze)
- Transizioni principali in `rapporto_di_lavoro/views.py`:
  - `invia_proposta_al_dipendente`,
  - `firma_proposta_candidato`,
  - `firma_definitiva_admin`.
- Creazione contratto e promozione candidato in `PropostaAssunzione.firma_definitiva_admin()`.

### Gap rilevati
- `PropostaAssunzione.STATO_CHOICES` include stati legacy insieme ai nuovi.
- Presenza fallback legacy in piu` viste candidato/admin.
- Alias legacy URL ancora attivi (compatibilita`), da governare come deprecazioni controllate.

## Artefatti generati

- `BACKLOG_BONIFICA_PRIORITARIO.md` (priorita` alta/media/bassa)
- aggiornata tabella backlog in `MATRICE_FLUSSI_E_SCHERMATE.md`

## Flusso 3 - Presenze -> riepilogo -> cedolino (stato audit)

### Flusso implementato (evidenze)
- Modello `RiepilogoMensilePresenze` con stati: `bozza -> revisione -> approvata -> elaborata`.
- Aggregazione mensile in `presenze.utils.aggrega_presenze_per_motore()`, con blocco ricalcolo su `approvata/elaborata`.
- Gestione operativa in `presenze/views.py` (`riepilogo_mensile_motore`, `anteprima_cedolino_riepilogo`).
- Calcolo cedolino da riepilogo via motore canonico (`calcola_busta_paga_mese`) con `modalita_ore_effettive=True`.

### Gap rilevati
- In piu` query operative (`aggrega_tutti`, liste) rientrano anche dipendenti in stato `candidato` oltre agli `attivo`; da chiarire se voluto nel flusso cedolino reale.
- Variabile `STATO_SUCCESSIVO` dichiarata ma non usata (rumore/manutenibilita`).
- Coesistenza di route legacy in fondo al file presenze (ridondanza di navigazione, ma rischio basso).

## Flusso 4 - Documenti (upload/classificazione/download) (stato audit)

### Flusso implementato (evidenze)
- ACL principali presenti in `_assert_documento_accesso()` con differenziazione ruolo.
- Download/visualizzazione passano da controllo ACL.
- Ampia logica di filtro/categoria/tipo/anno in `lista_documenti` (inclusi dashboard buste/F24/CUD).
- Upload personale dipendente/candidato e lock eliminazione post presa visione aziendale.

### Gap rilevati (critici)
- Per admin/HR, `_assert_documento_accesso()` non verifica esplicitamente l'appartenenza del documento all'azienda operativa/corrente (solo consulente ha controllo azienda esplicito).
- In `elimina_documento`, admin/HR risultano autorizzati senza check azienda del record: rischio cancellazione cross-tenant tramite ID diretto.
- In `Documento.TIPI_PERSONALI` compare `altro` (etichettato come F24): possibile incoerenza semantica tra documenti personali e documenti fiscali aziendali.

## Flusso 5 - Richieste + workflow approvazioni + notifiche (stato audit)

### Flusso implementato (evidenze)
- `richieste` gestisce ciclo base (`inviata`, `approvata`, `rifiutata`, `chiusa`) con push notification al dipendente.
- `workflow` introduce approvazione multi-step (`RichiestaApprovazione` con `in_attesa/approvato/rifiutato`) avviata da signal su nuove richieste.
- `notifiche_email` persiste notifiche applicative da `evento_trigger`.

### Gap rilevati (critici)
- In `richieste/views.py` sono ancora usati controlli su `getattr(user, 'ruolo', None)` mentre il modello utente usa ruoli M2M (`has_ruolo`): rischio blocco permessi/liste vuote o comportamenti incoerenti.
- In diverse action richieste (`dettaglio_richiesta`, `rispondi_richiesta`, `chiudi_richiesta`, `elimina_richiesta`) il recupero per `id` non filtra per azienda operativa: rischio accesso/modifica cross-tenant.
- In `workflow/services.py` `_resolve_approvatore` usa `User.objects.filter(..., ruolo=...)` (campo non coerente con il modello attuale): possibile assegnazione approvatore nulla/non deterministica.
- Coesistono due binari di gestione richieste (azioni dirette in `richieste` + workflow a step) senza una regola univoca di precedenza.

## Flusso 7 - API/PWA dipendente (stato audit)

### Flusso implementato (evidenze)
- API JWT con refresh + 2FA TOTP (`login`, `otp_verify`, setup/enable/disable/status).
- Endpoint dipendente: check-in/out, presenze, documenti + download, ferie/permessi, profilo, notifiche, push subscription.
- Buona segregazione documenti in API (`documento_download` limita a documento del dipendente autenticato).

### Gap rilevati
- API e web non sono pienamente allineati su alcune semantiche ruoli/stati (es. richieste con workflow avanzato lato web, notifiche API costruite da stato richiesta semplificato).
- Da verificare policy di accesso API per utenti non-dipendente autenticati (molti endpoint dipendono da `_get_dipendente` e rispondono 404: comportamento funzionale ma poco esplicito lato permessi).

## Flusso 6 - Simulazione costo del lavoro (stato audit)

### Flusso implementato (evidenze)
- Simulazioni distribuite tra:
  - `rapporto_di_lavoro/views.py` (simulazione organico/costo lavoro),
  - `rapporto_di_lavoro/views_simulatore.py` (simulatore busta mensile),
  - `rapporto_di_lavoro/views_simulazione_2026.py` (simulazione 2026).
- Coesistenza di piu` sorgenti di calcolo:
  - motore canonico paga (`utils_motore_paga.calcola_busta_paga_mese`),
  - calcolo legacy (`utils_calcoli.calcola_completo`),
  - modulo specialistico `costo_lavoro` (rule engine + calcolatore).
- Export PDF/Excel e salvataggio scenari presenti.

### Gap rilevati
- Architettura di calcolo non univoca: lo stesso scenario puo` transitare tra percorsi `legacy` e `costo_lavoro`, con rischio divergenza numerica.
- Modulo `costo_lavoro` contiene componenti duplicate (`engine/*` e `rules/*`) e file d'integrazione parziale (`servizio_integrazione.py` con parti non completate): alta complessita` manutentiva.
- `views.py` centralizza logica molto estesa (funzioni numerose e dense): costo alto di evoluzione e test.
- Necessaria policy esplicita su "motore ufficiale per simulazioni" e regole di fallback.

### Avanzamento recente
- In `rapporto_di_lavoro/views.py` introdotto helper `_calcola_base_simulazione_motore_unico(...)`.
- Le chiamate principali di simulazione usano ora il motore canonico `calcola_busta_paga_mese` come default.
- Fallback legacy (`calcola_completo`) mantenuto come rete di sicurezza, con tracciamento esplicito a log in caso di attivazione fallback.
- Primo passo M8 completato: estrazione del calcolo base in service dedicato `rapporto_di_lavoro/services_simulazione.py`, con `views.py` mantenuta come orchestrazione.
- Passi M8 successivi completati: estrazione utility di supporto (`calcola_giorni_attivi_mese`, `periodo_mese_da_riferimento`, `parse_iso_date`) nel medesimo service, mantenendo invariata la semantica funzionale.
- Ulteriori passi M8 completati: estrazione utility ore/chiusure (`calcola_ore_retribuite_contrattuali`, `calcola_paga_oraria_contrattuale`, `parse_giorni_chiusura_mese`) e rimozione delle chiamate dirette a `calcola_completo` da `rapporto_di_lavoro/views.py`.
- M9 completato: consolidato stack `costo_lavoro` su implementazione unica `engine/*`; moduli `rules/*` mantenuti come compatibility layer per import legacy.

## Consolidamento finale audit

Flussi coperti:
1. Onboarding candidato/dipendente  
2. Proposta -> contratto  
3. Presenze -> riepilogo -> cedolino  
4. Documenti  
5. Richieste + workflow + notifiche  
6. Simulazione costo lavoro  
7. API/PWA dipendente

Output operativi aggiornati:
- `MATRICE_FLUSSI_E_SCHERMATE.md`
- `BACKLOG_BONIFICA_PRIORITARIO.md`
- `COMPLIANCE_CHECKLIST_RILASCIO.md` (percorso operativo deploy/smoke/rollback: **sezione 10**)

## Aggiornamento avanzamento (implementazioni eseguite)

- **Documenti (H5/H6):** applicati controlli tenant-aware anche per admin/HR su accesso e cancellazione documento.
- **Governance codice (H4):** verificata non-referenza di `accounts/views.py.updated` e rimosso file parallelo obsoleto.
- **Onboarding (H2):** corretto `_build_iter` in `accounts/views_admin_candidati.py`: step "Proposta inviata" segnato come completato solo su stati proposta attivi/coerenti, non più su mera esistenza proposta.
- **Proposta/Contratto (H1 completato, H3 parziale):** introdotta in `PropostaAssunzione` una policy centralizzata canonico/legacy (`LEGACY_TO_CANONICO`, `stato_canonico`, `stati_equivalenti`) con normalizzazione scritture stato in `save()`; applicata ai controlli principali lato candidato/admin (`firma_proposta_candidato`, `rifiuta_proposta_dipendente`, selezione fonte busta dashboard, blocchi stato in admin candidati).
- **Presenze (L3):** rimossa variabile inutilizzata `STATO_SUCCESSIVO` da `presenze/views.py` (cleanup codice morto).
- **Costo lavoro (L4):** completato `costo_lavoro/servizio_integrazione.py` rimuovendo placeholder (`pass`) e aggiungendo percorso `calcola_per_contratto()` con adapter compatibile.
- **Legacy URL (M3):** `accetta/` collegata a `accetta_proposta_dipendente` (log `[DEPRECATION]`); `converti/` con log deprecazione; percorsi ufficiali `firma/` e `firma-admin/`.
- **Simulazioni/Costo lavoro (M8/M9):** completata estrazione principale service-layer simulazioni e consolidato stack `costo_lavoro`.
- **Richieste (H7/H8):** migrazione gate ruoli principali a `has_ruolo` + filtro azienda nelle action sensibili (`dettaglio`, `rispondi`, `chiudi`, `elimina`).
- **Workflow (H9):** risoluzione approvatore aggiornata a ruoli M2M (`ruoli__codice`) in `workflow/services.py`.
- **Richieste/Workflow (M6 completato):**
  - introdotto punto unico di update stato richiesta lato `richieste/views.py`;
  - policy server-side: se esistono step workflow `in_attesa`, tutte le mutazioni dirette stato sono bloccate;
  - UI resa workflow-aware su dettaglio/risposta/lista richieste con link rapido alla coda approvazioni.
- **API richieste/notifiche (M7 completato):**
  - `api/notifiche` e `api/ferie` ora distinguono richieste in coda workflow con stato operativo `in_approvazione`;
  - allineata semantica stati tra canale web e canale API/PWA.

## Piano interventi consigliato (ordine esecutivo)

1. **Sicurezza e tenant isolation**
   - chiudere tutti i gap cross-tenant (documenti e richieste);
   - uniformare controlli ruolo su `has_ruolo`.
2. **Coerenza logica flussi**
   - unificare state machine proposta/contratto;
   - definire policy unica richieste/workflow;
   - chiarire perimetro presenze per cedolino.
3. **Unificazione motori di simulazione**
   - definire motore canonico e fallback ammessi;
   - ridurre duplicazioni `legacy`/`costo_lavoro`.
4. **Refactor strutturale**
   - estrazione servizi da `views.py` monolitico;
   - cleanup file/route legacy e codice non usato.
5. **UX/UI coherence pass**
   - allineamento finale interfacce secondo `UI_UX_GUIDELINES_GESPER.md`.
