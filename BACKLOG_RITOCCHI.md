# Backlog — ritocchi dopo gli aggiustamenti prioritari

Elenco da **processare in blocco** al termine del giro su tutti i moduli (non blocca le priorità correnti).

## Anagrafiche

- [x] `lista_dipendenti`: `select_related('azienda')`; conteggi tab con una sola query (`aggregate` + `Count` filtrato).
- [x] `lista_aziende`: paginazione 25, `select_related` CCNL/tipo contratto, UI allineata alle altre liste.
- [x] `dettaglio_dipendente`: `select_related('azienda','utente')`; un’unica query documenti con `.only(...)` + filtri buste/CUD in Python.
- [ ] Valutare export CSV / permessi dove non ancora allineati.

## Refactor trasversale

- [x] `accounts.pagination.pagination_window` — usato da anagrafiche, richieste, workflow, documenti, presenze (rimossa duplicazione `_pagination_window_presenze`).

## Documenti

- [x] `lista_documenti`: paginazione (25) e `select_related('azienda')` sulla **vista tabellare** (non sulle dashboard buste/F24/CUD).
- [ ] Allineamento formattazione importi dove ancora assente.

## Workflow

- [ ] `lista_da_approvare`: ulteriore UX (filtri per tipo/stato, conferme modali). *(Paginazione base già fatta.)*
- [ ] Verificare coerenza assegnazione `approvatore` per admin vs HR su tutti i flussi.

## Presenze

- [ ] Eventuali `post_save` su `Presenza` se in futuro servono audit automatici (oggi `bulk_create`/`bulk_update` non li invocano).
- [ ] Job schedulato opzionale per generazione teorica massiva (alternativa a export-only).

## Rapporto / simulazioni / candidato

- [ ] Controllo incrociato template €/h e PDF/CSV dopo ulteriori modifiche CCNL.

## API / integrazioni

- [ ] Endpoint presenze/timbrature: profilazione N+1 se segnalato in produzione.

## Impostazioni / economici (admin)

- [x] `impostazioni_sistema` e `geocode_impostazioni`: accesso con `has_ruolo('admin')` o superuser (allineato al resto del portale).
- [x] `gestione_riferimenti_economici`: paginazione 25 su **parametri** (`p_page`) e **regole** (`r_page`) con `pagination_window`; arricchimento costo_lavoro solo sulla pagina corrente.
- [x] `_is_admin_only` in `rapporto_di_lavoro/views.py`: superuser o `has_ruolo('admin')` (non più `user.ruolo` legacy). Simulazione 2026: stesso criterio + `is_authenticated`.

## Moduli admin (portale)

- [x] `dashboard_admin`: conteggi documenti (totale, buste, F24, CUD) in **una** query `aggregate` per ambito.
- [x] `admin_table_detail`: `pagination_window` + navigazione pagine allineata alle altre liste.
- [x] `lista_log_attivita` / `lista_log_errori`: paginazione 25, filtri preservati in query string, `prefetch_related('utente__ruoli')` sui log; accesso con `has_ruolo('admin')` o superuser (sostituito check legacy su `user.ruolo`).
- [x] `test_stato_utente`: nome azienda da `azienda.nome` (campo reale del modello).
- [x] `segna_errore_risolto`: redirect di fallback con `reverse('lista_log_errori')` se manca `Referer`.

---

*Ultimo aggiornamento: impostazioni sistema, riferimenti economici, `_is_admin_only` rapporti.*
