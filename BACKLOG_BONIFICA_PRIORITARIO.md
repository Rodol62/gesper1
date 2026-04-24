<!-- markdownlint-disable MD038 -->

# Backlog Bonifica Prioritario

Data: 2026-04-07  
Ambito attuale: Flusso 1 (onboarding) + Flusso 2 (proposta -> contratto)

## Priorita` alta (bloccanti logici/coerenza)

| ID | Area | Evidenza | Rischio | Azione proposta |
| --- | --- | --- | --- | --- |
| H1 | Stati proposta | Coesistono stati nuovi e legacy in `PropostaAssunzione.STATO_CHOICES` (`inviata_candidato`, `firmata_candidato`, `contratto_attivo` + `inviata_al_dipendente`, `accettata_dipendente`, `convertita_in_contratto`) | Incoerenza di flusso e UI, branch logici duplicati | ✅ completato: policy centralizzata canonico/legacy + blocco scritture legacy via normalizzazione stato in `save()` |
| H2 | Flusso onboarding | In `accounts/views_admin_candidati.py` il criterio per proposta "done" usa `bool(proposta)` senza filtro stato | Step onboarding pu`o risultare completato anche con proposta `bozza` o rifiutata | ✅ aggiornato `_build_iter`: step "Proposta inviata" true solo su stati proposta attivi/coerenti (inclusa compat legacy) |
| H3 | Contratto/proposta | In piu` punti restano fallback legacy su `RapportoDiLavoro.stato='proposta'` e proposte legacy | Ambiguita` su quale sia il percorso ufficiale | 🟡 Parziale: template principali e `_build_iter` usano `stato_canonico` / equivalenze; restano eventuali riferimenti puntuali a `RapportoDiLavoro.stato='proposta'` dove voluto |
| H4 | Governance codice | Presente file `accounts/views.py.updated` accanto a `accounts/views.py` | Rischio drift e confusione manutentiva | ✅ verificata non-referenza e rimosso file parallelo obsoleto |
| H5 | Sicurezza documenti | In `documenti/views.py`, `_assert_documento_accesso()` per admin/HR non valida esplicitamente azienda documento | Rischio accesso cross-tenant via URL diretto | ✅ Applicato controllo tenant-aware anche per admin/HR |
| H6 | Sicurezza eliminazione | In `documenti/views.py`, `elimina_documento()` consente ad admin/HR eliminazione senza check azienda record | Rischio cancellazione cross-tenant | ✅ Applicato check azienda su delete |
| H7 | Permessi richieste | In `richieste/views.py` controlli basati su `user.ruolo` anziche` ruoli M2M (`has_ruolo`) | Access control incoerente/fallace su liste e azioni | ✅ Migrato gate principali su `has_ruolo` |
| H8 | Sicurezza richieste | In piu` endpoint richieste (`dettaglio`, `rispondi`, `chiudi`, `elimina`) lookup per `id` senza filtro azienda | Rischio accesso/modifica cross-tenant | ✅ Applicato filtro tenant nelle action sensibili |
| H9 | Workflow assegnazione | In `workflow/services.py` `_resolve_approvatore` usa `User.objects.filter(..., ruolo=...)` non allineato al modello utente | Approvazioni non assegnate o assegnate male | ✅ Rifattorizzato su ruoli M2M (`ruoli__codice`) |
| H10 | Simulazioni motore | In `rapporto_di_lavoro/views.py` convivono calcolo `legacy`, `costo_lavoro` e motore canonico in percorsi diversi | Risultati incoerenti e difficile validazione numerica | ✅ Allineati `views_simulazione_2026`, `views_simulazione_proposta` a `invoca_calcola_busta_paga_mese` in `services_simulazione.py` (log unico); rimosso import morto `calcola_completo` da simulazione 2026 |

## Priorita` media (coerenza UX/UI e manutenzione)

| ID | Area | Evidenza | Rischio | Azione proposta |
| --- | --- | --- | --- | --- |
| M1 | Nomenclatura step | Etichette step e mapping stato distribuiti in piu` punti (`_build_iter`, `_step_corrente`, dashboard candidato) | Divergenze future e badge incoerenti | Centralizzare dizionario stati/etichette/classi in un modulo unico |
| M2 | Permessi e visibilita` | Accesso candidato a proposta basato su `dipendente__utente` con fallback via `ProfiloCandidato` | Edge case non uniformi nelle varie view | Standardizzare un helper unico di risoluzione identita` candidato->dipendente |
| M3 | Legacy URL | Alias legacy mantenuti (es. `accetta`/`approva-admin`/`converti`) | Rischio uso involontario percorso legacy | ✅ `accetta/` punta a `accetta_proposta_dipendente` (log `[DEPRECATION]`); `converti/` log deprecazione; URL canonici `firma/` e `firma-admin/` restano ufficiali |
| M4 | Presenze perimetro cedolino | In `presenze/views.py` alcune operazioni motore includono `dipendente.stato='candidato'` | Possibile elaborazione non voluta di non-assunti | ✅ Applicata policy: flusso motore cedolino limitato a dipendenti `attivo` |
| M5 | Tipologie documento | `Documento.TIPI_PERSONALI` include `altro` (etichetta F24) | Confusione UI e classificazione documentale | ✅ Separata tassonomia: `altro/F24` escluso dai caricamenti personali + validazione server-side tipo |
| M6 | Doppio binario richieste | Coesistono gestione diretta richieste e workflow step-based senza regola unica | Stati e notifiche divergenti | ✅ Applicata policy server-side: con workflow `in_attesa` bloccate tutte le mutazioni dirette stato (approva/rifiuta/chiudi/rispondi) + UI workflow-aware |
| M7 | API/web parita` richieste | API notifiche dipendono da stato richiesta semplificato mentre web usa anche workflow | Esperienza incoerente tra canali | ✅ Allineato mapping API: introdotto stato operativo `in_approvazione` (workflow pending) su notifiche e ferie/permessi |
| M8 | Complessita` simulazione | `rapporto_di_lavoro/views.py` concentra logica simulazione molto estesa | Alto rischio regressioni in manutenzione | ✅ completato perimetro principale: estratto service simulazione con helper core e utility principali; `views.py` riallineata a ruolo orchestratore |
| M9 | Duplicazioni costo_lavoro | Presenza componenti duplicati in `costo_lavoro/engine/*` e `costo_lavoro/rules/*` | Confusione su stack realmente usato | ✅ consolidato stack: moduli `rules/*` convertiti a compatibility layer verso `engine/*` (single implementation source) |

## Priorita` bassa (pulizia progressiva)

| ID | Area | Evidenza | Rischio | Azione proposta |
| --- | --- | --- | --- | --- |
| L1 | Terminologia UI | Testi misti tra "dipendente/candidato" in alcune rotte legacy | Rumore lessicale e onboarding lento | Uniformare microcopy secondo `UI_UX_GUIDELINES_GESPER.md` |
| L2 | Documentazione interna | Parte documentazione menziona stati non pienamente allineati al codice | Disallineamento team | Aggiornare documentazione canonica al completamento refactor stati |
| L3 | Presenze codice morto | `STATO_SUCCESSIVO` definito in `presenze/views.py` ma non utilizzato | Rumore manutentivo | ✅ variabile inutilizzata rimossa |
| L4 | Integrazione incompleta | `costo_lavoro/servizio_integrazione.py` contiene metodi placeholder (`pass`) | Debito tecnico/documentazione fuorviante | ✅ completato: rimosso placeholder, implementato adapter contratto e hardening campi opzionali |

## Sequenza di esecuzione suggerita

1. Congelare state machine canonica (H1, H3).  
2. Correggere checklist/iter onboarding per stati attivi (H2).  
3. Rimuovere/revisionare file ambigui e residui (`*.updated`, alias non necessari) (H4, M3).  
4. Consolidare mapping stati/UI in modulo unico (M1).  
5. Pulizia terminologia e documentazione (L1, L2).

## Note avanzamento

- Completati: H1, H2, H4, H5, H6, H7, H8, H9, H10 (perimetro simulazione 2026 + proposta + service), M3 (strumentazione log su alias).
- Controlli locali: `scripts/run_checks.sh` (`django check` + `python3 manage.py test`).
- Migrazione `anagrafiche.0005_delete_user`: dipendenze aggiornate per DB di test/fresh install (ordine vs `presenze`/`documenti`); DB gia` migrati in produzione non richiedono azione.
- Censimento 2026-04-08: `invoca_calcola_busta_paga_mese` centralizza log errori motore busta; simulazione 2026 e simulazione economica proposta usano il wrapper; `calcola_completo` resta fallback solo in `calcola_base_simulazione_motore_unico`.
