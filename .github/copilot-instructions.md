# Copilot Instructions for AI Agents

## Big picture (GESPER)

- Monolite Django (Django 6) con entrypoint `manage.py`, settings root `settings.py`, routing root `urls.py`.
- Dominio principale: HR multi-azienda (utenti/ruoli, candidati, proposte/contratti, presenze, documenti, storico, notifiche).
- App core: `accounts`, `anagrafiche`, `rapporto_di_lavoro`, `presenze`, `documenti`, `richieste`, `storico`, `log_attivita`.
- App di supporto: `workflow` (approvazioni), `notifiche_email` (notifiche DB+email), `costo_lavoro` (pacchetto interno calcolo costo), `ruoli`, `report`.
- Fonte funzionale completa: `DOCUMENTAZIONE_UNICA_GESPER.md`.

## Architettura e confini applicativi

- **Autenticazione/ruoli**: `accounts.models.User` estende `AbstractUser`; i ruoli sono in M2M (`Ruolo`) verificati con `user.has_ruolo('admin'|'hr'|'dipendente'|'candidato'|'consulente')`.
- **Tenant operativo**: azienda corrente da `accounts.tenant.get_azienda_operativa(user, session)` — priorità `session['azienda_id']`, fallback `user.azienda`.
- **Portale candidato**: URL prefisso `/candidato/` inclusi da `accounts.urls_candidato`; portale admin HR in `accounts.urls`.
- **Workflow candidato→contratto**: stati `PropostaAssunzione` (`bozza → inviata_candidato → firmata_candidato → approvata_admin → contratto_attivo`, più stati di rifiuto). Step chiave: `firma_proposta_candidato` e `firma_definitiva_admin`. URL legacy (`accetta_proposta_dipendente`) mantenuti come alias.
- **Motore paga canonico**: usare sempre `rapporto_di_lavoro.utils_motore_paga.calcola_busta_paga_mese` — mai duplicare logica in view/template.
- **Presenze→cedolino**: `presenze.utils.aggrega_presenze_per_motore` popola `RiepilogoMensilePresenze`; poi passare `as_motore_kwargs()` al motore.
- **`costo_lavoro`**: pacchetto interno opzionale (`CostoLavoroAzienda`, `RuleEngine`, `DatiContrattuali`). Va importato con guard: `try: from costo_lavoro import ...; COSTO_LAVORO_ENABLED = True except: ...` come in `views_simulazione_2026.py`.
- **`workflow` app**: inizializzazione automatica via `workflow.signals` → `workflow.services.inizializza_workflow_richiesta` su `post_save(Richiesta, created=True, stato='inviata')`.
- **`notifiche_email`**: MVP DB-only — `services.crea_notifica_evento(richiesta, trigger)` salva `Notifica(stato='pending')`; invio email asincrono non attivo (nessun Celery).

## Regole critiche dominio payroll

- **Simulazione annua** — in UI: etichetta «Simulazione annua»; URL canonico `/rapporti/simulazione-annua/` (redirect da `/rapporti/simulazione-2026/`); logica: dipendenti `stato='attivo'` vs somma **Qtà** ruoli; export Excel `Simulazione_annua_{anno}_*.xlsx`.
- Divisori FIPE: orario `173`/`cp.ore_mensili`, giornaliero `26`; non introdurre formule alternative locali.
- Per cedolino reale (da presenze aggregate) usare sempre questi flag nel motore:

  - `auto_ore_domenicali_da_calendario=False`
  - `modalita_ore_effettive=True`

- Lookup parametri (contributi/ratei/maggiorazioni) via DB richiede `ccnl_obj` valorizzato (sigla FIPE). Filtrare sempre per `data_riferimento` (coerenza decorrenza).
- CCNL di riferimento: Turismo Confcommercio (modello `ParametroCCNLTurismo`), versione `2024-2026`.

## Workflow sviluppatore

- Setup locale: Python 3.12 + venv `.venv`, install da `requirements.txt`.
- Validazioni minime prima di chiudere una modifica:

  1. `python3 -m py_compile <file_modificati>`
  2. `python3 manage.py check`

- Test: `python3 manage.py test anagrafiche rapporto_di_lavoro` (coverage non uniforme).
- **Deploy locale→prod (scp diretto)**: `bash deploy_gesper.sh` — esegue `manage.py check`, `scp` dei moduli, riavvia gunicorn.
- **Deploy rsync (SSH persistente)**: `bash scripts/deploy_prod.sh` — apre sessione SSH multiplexed, backup DB remoto, rsync con esclusioni, migrations, collectstatic, restart.
- Settings produzione: `settings_production.py` (importa `settings.py` e sovrascrive); `FORCE_SCRIPT_NAME='/gesper'` attivo in prod.

## Convenzioni implementative

- **Split views per area**: `accounts/views_admin_candidati.py`, `views_candidato.py`, `views_consulente.py`, `views_impostazioni.py`, `views_registration.py`, `views_richieste_integrazione.py`, `views_supervisore.py`. Mantenere questo split, evitare mega-file.
- **Logging errori**: `log_attivita.middleware.GesperErrorMiddleware` cattura eccezioni non gestite → `LogErrore.registra(...)`. Per log espliciti nelle view: `from log_attivita.utils import registra_log`.
- **Config globale**: `ConfigurazioneSistema` (singleton in `accounts.models`) esposta ovunque via context processor `accounts.context_processors.config_sistema`.
- **Email backend**: `accounts.email_backend.UnverifiedSSLEmailBackend` (workaround SSL macOS dev).
- Templates: directory `templates/` a radice progetto + `APP_DIRS=True`; naming italiano già presente nel dominio.

## Quando modifichi codice sensibile

- **Payroll/presenze**: verifica impatti su `views_simulazione_2026.py`, `views_simulatore.py`, `accounts/views_candidato.py` (dashboard) e flussi proposta/contratto.
- **Stati/campi legacy**: non rimuovere senza verificare retrocompatibilità su dati SQLite esistenti; mantenere URL alias legacy in `rapporto_di_lavoro/urls.py`.
- Privilegiare modifiche incrementali: questa codebase è in produzione attiva su SQLite.
