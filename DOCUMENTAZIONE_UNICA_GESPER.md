# Documentazione Unica GESPER

Ultimo aggiornamento: 10/05/2026

## 1) Scopo del sistema

GESPER è un gestionale HR multi-azienda basato su Django, con focus su:

- anagrafiche dipendenti/candidati;
- workflow proposta/contratto;
- presenze, richieste, documenti e storico;
- simulazione e calcolo retributivo/costo lavoro.

## 2) Architettura applicativa (sintesi)

- Entry point: `manage.py`
- Settings: `settings.py`
- Routing root: `urls.py`
- App principali:
  - `accounts`: autenticazione, ruoli, dashboard
  - `anagrafiche`: aziende/dipendenti
  - `rapporto_di_lavoro`: proposte, contratti, motore paga, simulazioni
  - `presenze`, `richieste`, `documenti`, `storico`, `notifiche`, `report`
- Modulo specialistico: `costo_lavoro/` (regole parametrizzate)

## 3) Logica funzionale core

### 3.1 Proposta e contratto

- Creazione/modifica proposta con precompilazione da parametro CCNL e tipo contratto.
- Conversione proposta in contratto quando stati/approvazioni sono coerenti.
- Tracciamento completo stato proposta e passaggi di approvazione.

### 3.2 Simulazione annua (organico e costo) e coerenza motore

- In interfaccia la funzione è etichettata **Simulazione annua** (scenario per ruoli e quantità su un anno di riferimento, oggi calendario 2026).
- **URL:** canonico `/rapporti/simulazione-annua/`; `/rapporti/simulazione-2026/` reindirizza (compatibilità bookmark).
- Confronto esplicito: **dipendenti in carico** (`Dipendente`, `stato='attivo'`) vs **testate di scenario** (somma **Qtà** per ruolo); **delta** = aumento/riduzione vs organico attuale.
- Il calcolo usa il **motore canonico** (`calcola_busta_paga_mese` via `views_simulazione_2026`).
- Le variabili profilo (superminimo, turno, scatti, extra) vengono propagate nel payload.
- La scelta del parametro CCNL deve rispettare la decorrenza (`decorrenza_validita_da <= data_riferimento`).
- Principi generali su CCNL vs contratto individuale vs ore effettive e variazioni nel tempo: sezione **5.0**.
- **Export Excel** (`simulazione_2026_excel`): file `Simulazione_annua_{anno}_{azienda}.xlsx`; titoli foglio «SIMULAZIONE ANNUA» e riga KPI allineata alla UI.

### 3.3 Dashboard candidato

- I riepiloghi economici devono privilegiare calcolo runtime del motore (`calc_ret`) con fallback ai campi statici persistiti.

### 3.4 Presenze e cedolino reale

- Le presenze giornaliere (modello `Presenza`) vengono aggregate mensilmente da
  `presenze.utils.aggrega_presenze_per_motore()` nel modello `RiepilogoMensilePresenze`.
- Il riepilogo passa al motore con `auto_ore_domenicali_da_calendario=False`
  (si usano le ore reali registrate, non il calendario).
- Il flusso di approvazione: `bozza → revisione → approvata → elaborata`.
- Solo i riepiloghi in stato `bozza`/`revisione` possono essere ricalcolati.
- Per confronto busta **consulente** vs motore su stesso mese (PDF acquisito): sezione **3.5**.

### 3.5 Conciliazione: busta consulente vs calcolo mensile GESPER

Obiettivo: confrontare la **busta paga acquisita** (PDF TeamSystem / consulente del lavoro), già estratta nel modello analitico `CedolinoMotoreV4` + `VoceCedolinoMotoreV4`, con il **risultato del motore busta** `calcola_busta_paga_mese` (via `invoca_calcola_busta_paga_mese`), usando gli **stessi dati di regola** che consulente e azienda dovrebbero condividere.

**Dati comuni (input al confronto)**  
1. **Contratto individuale** — `RapportoDiLavoro` attivo nel mese (`risoluzione_contratto_motore.rapporto_sottoscritto_attivo_nel_mese`): livello CCNL, tipo contratto/part-time, date inizio/fine, superminimo, premi, flag 13ª/14ª, residenza per addizionali.  
2. **Parametri CCNL di categoria** — `ParametroCCNLTurismo` risolto per decorrenza (`risolvi_parametro_ccnl_per_mese`), più `CCNL` FIPE e tabelle contributi/ratei coerenti col motore.  
3. **Ore e classificazioni lavorative** — Per il motore in conciliazione le ore del mese provengono dalla **griglia mensile sul ruolo organico** `RuoloOrganico2026.calendario_mensile` (chiavi ore come in simulazione: straordinari diurno/notturno/festivo, domenica, notturno+festivo, ordinarie retribuite, domenicali/festivi, assenze ingiustificate, trattenute/extra). Devono riflettere le **stesse classificazioni** (domenicale, festivo, straordinario, ecc.) usate dal consulente sul cedolino, entro i limiti del modello dati GESPER.  
4. **Acquisizione PDF** — Pipeline unica descritta in `rapporto_di_lavoro/motori_canonici.py` (posizionale v4, fallback testo). Su `CedolinoMotoreV4` possono esserci **ROF** / retribuzione di fatto usate per avvicinare il motore ai numeri del PDF (`allinea_kwargs_calcolo_a_dati_cedolino_v4`).

**Regole temporali (allineamento presenze ↔ consulente)**  
- Dal **01/03/2026** inclusivo, se esiste ruolo organico con calendario per il mese, la griglia **entra sempre** nei kwargs del motore in conciliazione (`usa_calendario_ruolo_organico_in_conciliazione`).  
- **Prima** di quella data: se il cedolino v4 ha **ROF** valorizzato, la griglia ruolo viene **omessa** (presenze non ancora allineate al consulente) e si usano ore/importi ricavati dal PDF; se manca ROF si **ripiega** sulla griglia ruolo per non azzerare le ore. Costante: `DATA_INIZIO_USO_CALENDARIO_RUOLO_CONCILIAZIONE` in `documenti/cedolino_conciliazione_motore_paga.py`.

**Confronto voci**  
- Aggregazione righe cedolino → chiavi motore tramite `MappaturaVoceMotore` e alias codici TeamSystem (es. 8001 composito, 8010/8020/8030 → maggiorazioni / straordinari, 9824 → bonus L207, addizionali 1800/1802…). Funzione centrale: `confronto_cedolino_motore_paga` nello stesso modulo.  
- Tolleranze euro: `documenti/cedolini_tolleranze.py`.

**Checklist operativa (controllo qualità)**  
1. Verificare che il dipendente abbia **contratto sottoscritto** e **parametro CCNL** risolvibili per il mese della busta.  
2. Allineare **RuoloOrganico2026** (quantità, livello, `calendario_mensile` del mese) ai dati trasmessi al consulente (ore per tipologia).  
3. Dopo import PDF: controllare `CedolinoMotoreV4` (ROF, natura busta ORDINARIA) e righe v4; poi eseguire la **conciliazione** dall’interfaccia admin / documenti (stesso stack richiamato da `presenze` dove previsto).  
   **Verifica automatica su campione (CLI, stesso confronto):** `python manage.py concilia_busta_motore_campione --limite=50` (opzioni `--azienda-id`, `--anno`, `--mese`, `--solo-ko`, `--json`).  
4. In caso di scostamenti: prima allineare **dati comuni** (contratto, CCNL, ore classificate); solo dopo ipotizzare differenze di implementazione motore vs software consulente.

**File di riferimento**  
- `documenti/cedolino_conciliazione_motore_paga.py` — contesto kwargs, allineamento a v4, confronto.  
- `documenti/cedolino_conciliazione_motore_v4.py` — controlli oggi vs v4 / preflight.  
- `presenze/views.py` — scostamento fiscale / elenchi collegati alla riconciliazione dove applicabile.

## 4) Motore retributivo (linee guida)

### 4.1 File chiave

- `rapporto_di_lavoro/utils_motore_paga.py` — **motore canonico unico**
- `rapporto_di_lavoro/utils_calcoli.py` — funzioni fiscali (IRPEF, detrazioni, bonus, addizionali)
- `rapporto_di_lavoro/utils_calendario.py` — calendario lavorativo, festività, chiusure
- `rapporto_di_lavoro/views_simulazione_2026.py` — piano organico annuo (scenario ruoli×quantità) ed export Excel
- `rapporto_di_lavoro/views.py` — proposte e contratti
- `accounts/views_candidato.py` — dashboard candidato (`_busta_per_fonte`)
- `presenze/utils.py` — aggregazione presenze per il motore

### 4.2 Principi

- Mantenere **un solo motore canonico** (`calcola_busta_paga_mese`): tutti i contesti
  (proposte, simulazioni, presenze, cedolino reale) devono chiamare lo stesso motore.
- Evitare duplicazioni di logica fiscale/contributiva.
- Usare parametri DB per scaglioni, addizionali, detrazioni, ratei, contributi.
- Mantenere tracciabilità delle modifiche (registro aggiornamenti).

### 4.3 Divisori FIPE (costanti contrattuali)

| Divisore | Valore | Uso |
| --- | --- | --- |
| Orario | 173 h/mese (`cp.ore_mensili`) | Paga oraria |
| Giornaliero | 26 gg/mese | Paga giornaliera, pro-rata |
| Ore/giorno | 173/26 = 6,6538 h | Classificazione domenicali/festivi |

**Regola critica**: `ore_mensili` si ricava SEMPRE da `cp.ore_mensili × coeff`, mai da
`ore_sett/7 × giorni_lav`. Analogamente `paga_giornaliera = lordo/26` (non `paga_oraria × ore_giorn`).

### 4.4 Pro-rata per assunzioni infra-mese

- `gg_cal >= 15` → `frazione = 1` (mese intero convenzionale).
- `gg_cal < 15` → `frazione = gg_lav / giorni_tot` (proporzionale ai giorni lavorativi dal contratto).
- Il chiamante DEVE passare `azienda` per ottenere il corretto giorno di riposo settimanale
  (es. martedì per ristoranti con chiusura infrasettimanale).

### 4.5 Flag comportamentali del motore

| Parametro | Valore per simulazioni/proposte | Valore per cedolino reale |
| --- | --- | --- |
| `auto_ore_domenicali_da_calendario` | `True` | `False` |
| `modalita_ore_effettive` | `False` | `True` |

### 4.6 Base di calcolo 13ª / 14ª / TFR

- **13ª e 14ª**: base = `lordo_base` (solo elementi fissi CCNL: paga base + contingenza + EDR + scatti).
  NON include straordinari, maggiorazioni variabili.
- **TFR**: base = `lordo_mensile` (tutti gli elementi retributivi ordinari, art. 2120 c.c.).

### 4.7 Controllo preliminare anti-duplicazione (flussi logici)

Prima di introdurre nuovi calcoli retributivi o «shortcut», verificare che non duplichino ciò che è già centralizzato.

**Motore busta (unica implementazione numerica)**  
- Implementazione: `rapporto_di_lavoro/utils_motore_paga.py` → `calcola_busta_paga_mese`.  
- Chiamata con log e gestione errori: `rapporto_di_lavoro/services_simulazione.py` → `invoca_calcola_busta_paga_mese` (preferita in UI, presenze, conciliazione cedolino, simulazione proposta/2026 dove già usata).  
- Riferimento narrativo vincolante: `rapporto_di_lavoro/motori_canonici.py` (motore busta vs motore cedolino PDF, cosa **non** usare come sostituto).

**Punti di ingresso noti al motore (nessun secondo motore parallelo)**  
| Contesto | File / nota |
| --- | --- |
| Simulazione annua 2026 | `views_simulazione_2026.py` → `invoca_calcola_busta_paga_mese` nel ciclo ruoli×mesi; il resto del file orchestra totali/F24 (non ricalcola IRPEF fuori dal motore). |
| Simulatore paga | `views_simulatore.py` → `calcola_busta_paga_mese` |
| Proposte / contratti HR | `rapporto_di_lavoro/views.py` (punti che invocano il motore), `views_simulazione_proposta.py` → `invoca_*` |
| Presenze / confronto cedolino | `presenze/views.py` → `invoca_*` o passaggio kwargs al motore |
| Dashboard candidato | `accounts/views_candidato.py` → `calcola_busta_paga_mese` |
| Conciliazione cedolino v4 | `documenti/cedolino_conciliazione_motore_paga.py` → `invoca_*` (flusso end-to-end §3.5) |
| Admin / test | `rapporto_di_lavoro/admin.py` |

**Risoluzione parametro CCNL per mese (contratto → addendum → tabella)**  
- Implementazione unica: `rapporto_di_lavoro/risoluzione_contratto_motore.py` → `risolvi_parametro_ccnl_per_mese`.  
- Chiamate attuali (da non moltiplicare con copie): `views_simulazione_2026.py`, `views_simulatore.py`, `presenze/views.py`, `cedolino_conciliazione_motore_paga.py`.

**Cosa è duplicazione da evitare**  
- Nuove funzioni che ricalcolano IRPEF, INPS, TFR o lordo «come il motore» senza delegare a `calcola_busta_paga_mese`.  
- Uso di `rapporto_di_lavoro/utils_calcoli.py` (`calcola_completo` e simili) **al posto** della busta mensile completa per simulazioni ufficiali o conciliazione (sono mattoni/stime; vedi `motori_canonici.py`).

**Tenant / azienda operativa**  
- Standard: `accounts.tenant.get_azienda_operativa(user, session)` — priorità `session['azienda_id']`, poi chiave legacy `session['azienda_operativa_id']`, infine `user.azienda`. Simulazione annua 2026 e calendario aziendale delegano a questa funzione (`views_simulazione_2026`, `views_calendario`).

**Audit `calcola_completo` e mattoni `utils_calcoli` (controllo preliminare)**  
- `calcola_completo`: unica invocazione nel codice applicativo → `ParametroCCNLTurismo.calcolo_completo()` (`models.py`), per stime su lordo tabellare; docstring in `utils_calcoli` vieta uso come busta/simulazione/conciliazione ufficiale.  
- `calcola_netto_dipendente` / `calcola_costo_azienda`: oltre alle proprietà di comodo su `ParametroCCNLTurismo`, usati in `views_simulazione_2026` (IRPEF incrementale 13ª/14ª cash accanto al motore) e in `views.py` come fallback in `_calcola_netto_dipendente_con_regole`; non costituiscono un secondo motore busta se restano confinati a questi ruoli.  
- Funzioni fiscali granulari (`calcola_irpef_lorda`, `calcola_detrazioni`, TI, bonus, addizionali): richiamate da `utils_motore_paga`, admin test motore, helper candidato — coerente con «mattoni» usati dal motore, non duplicazione del flusso completo.

**Prossimi passi operativi**  
1. Per ogni feature retributiva: tracciare flusso dati fino a una riga della tabella sopra.  
2. Se manca la riga, estendere il punto d’ingresso esistente invece di creare un nuovo calcolo.  
3. Dopo modifiche al motore: smoke test su `rapporto_di_lavoro/tests.py` e, se tocca presenze/cedolino, percorsi indicati in `motori_canonici.py`.

## 5) Dati e parametri CCNL

### 5.0 Gerarchia vincolante: CCNL, contratto individuale, buste e decorrenze

1. **Contratto collettivo nazionale (CCNL)**  
   Il rapporto di lavoro è ancorato al **CCNL di categoria** applicabile (nel prodotto: principalmente **FIPE / Turismo Confcommercio**), con tabelle e clausole previste dal contratto collettivo. I parametri tabellari in anagrafica di sistema (`ParametroCCNLTurismo`, `ParametroMaggiorazione`, `ParametroRatei`, contributi, ecc.) **implementano quel quadro**; non introdurre logiche parallele che lo contraddicono.

2. **Buste paga e simulazioni**  
   Partono dai **dati del contratto individuale** (`RapportoDiLavoro` e, dove pertinente, `PropostaAssunzione` collegata), confrontano le **ore effettive** (presenze, `RiepilogoMensilePresenze`, aggregati verso il motore) con le **ore e le regole contrattuali/CCNL**, applicano le **maggiorazioni** da parametri CCNL e le **voci previste dalla legislazione** vigente nel periodo (IRPEF, detrazioni, TI, bonus normati, ecc.). Il **motore canonico** resta `calcola_busta_paga_mese` in `rapporto_di_lavoro/utils_motore_paga.py` per tutti i contesti (proposte, simulazioni, presenze, cedolino).

3. **Variazioni nel tempo sul singolo dipendente**  
   Livello, percentuale di part-time, importi e altre condizioni possono **cambiare con decorrenza** (promozioni, addendum, revisioni). Ogni competenza o mese deve usare la **configurazione valida alla data di riferimento**: contratto sottoscritto attivo nel periodo, eventuali `AddendumContrattuale`, versione di parametro CCNL/contributiva coerente con le decorrenze. La risoluzione è centralizzata in `rapporto_di_lavoro/risoluzione_contratto_motore.py` (es. `risolvi_parametro_ccnl_per_mese`); **ottimizzazioni** (cache in RAM, meno query) **non devono alterare** l’ordine di priorità contratto → addendum → tabella CC né omettere le date effettive.

### 5.1 Modelli principali

- `ParametroCCNLTurismo` — tabella livelli FIPE con paga base, contingenza, ore mensili, ecc.
- `ParametroScattiAnnuali` — importi scatti anzianità per livello
- `ParametroRatei` — coefficienti ratei (ferie, 13ª, 14ª, TFR, ecc.)
- `ParametroContributi` — aliquote INPS/INAIL datore e dipendente
- `ParametroMaggiorazione` — percentuali maggiorazioni (domenicale, notturno, festivo, ecc.)
- `BonusFiscale` — importi TI (DL3/2020) e Bonus L207/2025 con `data_validita_da/a`
- `ScaglioneIRPEF`, `DetrazioneLavoroDipendente` — tabelle fiscali 2026
- `AddizionaleRegionale`, `AddizionaleComunale` — addizionali locali

### 5.2 Regola operativa

- Ogni calcolo deve usare parametri coerenti con anno/mese e decorrenza.
- `ccnl_obj` (FK a modello `CCNL`, sigla='FIPE') deve essere passato al motore per
  abilitare lookup DB di `ParametroContributi`, `ParametroRatei`, `ParametroMaggiorazione`.
- `ParametroCCNLTurismo.ccnl` è un CharField (non FK): il lookup `ccnl_obj` va fatto
  separatamente con `CCNL.objects.filter(sigla='FIPE').first()`.

### 5.3 Parametri DB validati (Feb 2026)

| Voce | Valore | Fonte |
| --- | --- | --- |
| INPS dipendente | 9,36% | cedolino reale |
| INPS datore | 29,31% | prospetto costo |
| INAIL | 0,74% | prospetto costo |
| TFR | 6,91% (`ParametroRatei`) | c.c. art. 2120 |
| Indennità ferie (coeff rateo) | 11,54 | corretto da 1,0 errato |
| TI (trattamento integrativo) | €100/mese | DB `BonusFiscale` |
| Bonus L207 soglia min | €8.500 annui | DB `BonusFiscale` |
| IRPEF 2026 scaglioni | 23% / 35% / 43% | L. 207/2024 confermata |

### 5.4 Normativa fiscale 2026

- IRPEF: 3 scaglioni (23% fino 28.000 €; 35% fino 50.000 €; 43% oltre).
- Trattamento Integrativo (DL 3/2020): €100/mese — misura permanente, fallback hardcoded.
- Bonus L207/2025 (€70,82/mese, soglia 8.500-20.000 €): misura **temporanea 2025**,
  restituisce €0 se non esiste record DB valido per l'anno di calcolo.
- Addizionale regionale Sicilia: attualmente flat 1,23% (da rendere progressiva in futuro).

## 6) Modulo presenze

### 6.1 Modello Presenza

- Un record per giorno per dipendente (`unique_together = ['dipendente', 'data']`).
- Supporta fino a 3 turni giornalieri (entrata/uscita × 3).
- Campo `ore_straordinario` + **`tipo_straordinario`** (diurno / notturno / festivo / nott_fest).
- Causali: P, ST, F, PE, M, INF, MAT, CIG, A, FE, R, SMART, ALTRO.

### 6.2 Modello RiepilogoMensilePresenze

Aggregazione mensile pronta per il motore paga. Campi principali:

| Campo | Descrizione |
| --- | --- |
| `ore_ordinarie` | Ore ordinarie lavorate |
| `ore_domenicali` | Ore domenicali ≤ orario contrattuale |
| `ore_festivi` | Ore festività nazionali lavorate ≤ orario contrattuale |
| `ore_straord_diurno/notturno/festivo/nott_fest` | Ore extra per tipo |
| `giorni_ferie_godute` | Giorni (o frazione) ferie godute |
| `ore_permessi_goduti` | Ore permessi/ROL goduti |
| `giorni_malattia/assenza_ingiust/cig` | Contatori assenze |
| `stato` | bozza → revisione → approvata → elaborata |

Metodo `as_motore_kwargs()` restituisce dict pronto per `calcola_busta_paga_mese()`.

### 6.3 Funzione aggrega_presenze_per_motore

```text
presenze.utils.aggrega_presenze_per_motore(dipendente, azienda, anno, mese, utente=None)
→ RiepilogoMensilePresenze
```

Logica di classificazione:

| Situazione | Bucket |
| --- | --- |
| Domenica, ore ≤ std | `ore_domenicali` |
| Domenica, eccedenza | `ore_straord_festivo` |
| Festività (non dom), ore ≤ std | `ore_festivi` |
| Festività, eccedenza | `ore_straord_festivo` |
| ST con `tipo_straordinario` compilato | bucket esplicito |
| Ore extra in giorno normale, dopo 22:00 | `ore_straord_notturno` |
| Ore extra in giorno normale, prima 22:00 | `ore_straord_diurno` |
| Causale F (giorno intero) | +1 `giorni_ferie_godute` |
| Causale F (ore parziali) | +ore/ore_std `giorni_ferie_godute` |
| Causale PE | `ore_permessi_goduti` |
| Causale M / A / CIG | contatori rispettivi |

## 7) Convenzioni UI e formattazione

- Formato importi italiano: migliaia con punto, decimali con virgola in output utente.
- Campi economici precompilati lato proposta:
  paga base, contingenza, EDR, lordo, ore, scatti, maggiorazioni.

## 8) Procedure operative minime

### 8.1 Prima di ogni refactor

1. Ricognizione usi simboli (definizioni + riferimenti).
2. Classificazione codice: attivo / legacy / non usato.
3. Eliminazione solo a rischio basso.

### 8.2 Validazioni obbligatorie

```bash
python3 -m py_compile <file_modificati>
python3 manage.py check
# smoke test delle view principali toccate
```

### 8.3 Smoke test motore (da eseguire dopo modifiche al motore)

```python
# Tutti i 10 livelli FIPE
from rapporto_di_lavoro.utils_motore_paga import calcola_busta_paga_mese
from rapporto_di_lavoro.models import ParametroCCNLTurismo, CCNL
from datetime import date

ccnl = CCNL.objects.get(sigla='FIPE')
data_rif = date(2026, 4, 1)
for cp in ParametroCCNLTurismo.objects.filter(decorrenza_validita_da__lte=data_rif):
    r = calcola_busta_paga_mese(parametro_ccnl=cp, data_riferimento=data_rif,
                                 ccnl_obj=ccnl, divisore_str='173',
                                 auto_ore_domenicali_da_calendario=True)
    print(cp.livello, r['lordo_mensile'], r['netto'])
```

## 9) Regole per cleanup codice e test

- Non eliminare file Python solo perché non referenziati staticamente: verificare
  import dinamici, URL include, template renderizzati per stringa, management commands.
- Non eliminare test che coprono logica fiscale/retributiva senza sostituzione equivalente.
- Ogni rimozione deve essere accompagnata da:
  - motivazione,
  - evidenza di non utilizzo,
  - verifica post-rimozione.

## 10) Configurazione di sistema (accounts)

### 10.1 Modello ConfigurazioneSistema

Singleton (pk=1) in `accounts/models.py`. Gestisce parametri globali:

- **Informazioni sito**: `nome_sito`, `nome_azienda`, `indirizzo_sede`, `partita_iva`
- **SMTP**: `smtp_host`, `smtp_port`, `smtp_user`, `smtp_password`, `smtp_use_tls`
- **Property `smtp_use_ssl`**: auto-calcolata — `True` se `smtp_port == 465` (Aruba, ecc.)
- **Aspetto UI**: colori, font

Registrata nell'admin Django (`accounts/admin.py`) come singleton (no add, no delete).

### 10.2 Backend email

- `accounts/email_backend.py` → `UnverifiedSSLEmailBackend`: bypassa verifica SSL certificati.
  Necessario su macOS (Python da python.org) e su server senza CA aggiornati.
- Tutte le `get_connection()` nel codice usano questo backend.
- `CustomPasswordResetForm.send_mail()` usa `ConfigurazioneSistema` per i parametri SMTP.

### 10.3 Reset password

Flusso completo implementato con template custom in `templates/registration/`:

| URL | Template | Descrizione |
| --- | --- | --- |
| `/accounts/password_reset/` | `password_reset_form.html` | Inserimento email |
| `/accounts/password_reset/done/` | `password_reset_done.html` | Email inviata |
| `/accounts/reset/<uid>/<token>/` | `password_reset_confirm.html` | Nuova password |
| `/accounts/reset/done/` | `password_reset_complete.html` | Completato |

Link "Password dimenticata?" già presente nella pagina di login.

## 11) Iter di assunzione candidato — Workflow ottimizzato (27/03/2026)

### 11.1 Stati della PropostaAssunzione (nuovo flusso)

```text
bozza → inviata_candidato → firmata_candidato → contratto_attivo
                          ↘ rifiutata_candidato
        ↘ rifiutata_admin (in qualsiasi fase prima di contratto_attivo)
```

| Stato | Attore | Descrizione |
| --- | --- | --- |
| `bozza` | Admin | Creata, non ancora inviata |
| `inviata_candidato` | Admin→Candidato | Inviata, il candidato deve firmare digitalmente |
| `firmata_candidato` | Candidato | Firma digitale apposta (checkbox + timestamp + IP) |
| `contratto_attivo` | Admin | Firma definitiva datore: contratto emesso, utente promosso a dipendente |
| `rifiutata_candidato` | Candidato | Il candidato ha rifiutato |
| `rifiutata_admin` | Admin | Annullata/respinta dall'amministrazione |

**Stati legacy** (record precedenti, non più generati): `inviata_al_dipendente`, `accettata_dipendente`, `rifiutata_dipendente`, `in_revisione_admin`, `approvata_admin`, `convertita_in_contratto`.

### 11.2 Campi firma digitale su PropostaAssunzione

| Campo | Tipo | Descrizione |
| --- | --- | --- |
| `data_firma_candidato` | DateTimeField | Timestamp firma digitale candidato |
| `ip_firma_candidato` | CharField(45) | IP del candidato al momento della firma |
| `data_firma_datore` | DateTimeField | Timestamp firma definitiva admin |

**Luogo e data nei documenti stampati**: le sezioni firma di `stampa_proposta.html` e `stampa_contratto.html`
mostrano condizionalmente `Palermo, gg/mm/aaaa HH:MM` se il campo datetime è valorizzato,
altrimenti mostrano la riga tratteggiata (per documenti non ancora firmati).
Nel contratto si accede ai dati via `contratto.proposta_origine` (`related_name` OneToOneField).

### 11.3 Azioni disponibili

#### Lato admin (HR/Admin)

| Azione | View/URL | Stato risultante | Quando |
| --- | --- | --- | --- |
| Crea proposta | `crea_proposta_da_candidato` | `bozza` | Candidato convalidato, senza proposta attiva |
| Invia al candidato | `invia_proposta_al_dipendente` | `inviata_candidato` | Stato `bozza` |
| Firma definitiva | `firma_definitiva_admin` | `contratto_attivo` | Stato `firmata_candidato` |
| Annulla/Respingi | `respingi_proposta_candidato` | `rifiutata_admin` | Qualsiasi stato prima di `contratto_attivo` |
| Riapri | `riapri_proposta_candidato` | `bozza` | Stato `rifiutata_admin` o `rifiutata_candidato` |
| Elimina | `elimina_proposta_candidato` | (eliminata) | Tranne `contratto_attivo` e `firmata_candidato` |

#### Lato candidato

| Azione | View/URL | Stato risultante |
| --- | --- | --- |
| Firma digitale | `firma_proposta_candidato` | `firmata_candidato` |
| Rifiuta | `rifiuta_proposta_dipendente` | `rifiutata_candidato` |

### 11.4 Effetti della firma definitiva admin

Quando l'admin esegue `firma_definitiva_admin`:

1. Crea un `RapportoDiLavoro` con stato `sottoscritto` e `data_sottoscrizione = oggi`
2. Imposta `PropostaAssunzione.stato = 'contratto_attivo'` e `data_firma_datore = now()`
3. Imposta `dipendente.utente.ruolo = 'dipendente'` e `azienda` corretta
4. Imposta `dipendente.stato = 'attivo'` e `data_assunzione` se non già presente
5. Crea `EventoStorico` tipo `'assunzione'` con timestamp, numero contratto e luogo "Palermo"

Analogamente, `firma_proposta_candidato` crea `EventoStorico` tipo `'documento'` con timestamp e IP al momento della firma.

### 11.5 Regola `get_users` nel reset password

`CustomPasswordResetForm.get_users()` esclude superuser e utenti con ruolo `admin` o `hr`.
Solo candidati e dipendenti possono richiedere il reset password dal form pubblico.

## 12) Punto di partenza per futuri sviluppi

Questo è il documento unico di riferimento.

Sequenza consigliata quando si riprende il lavoro:

1. leggere questa documentazione;
2. verificare stato server e check Django (`python3 manage.py check`);
3. eseguire smoke test sui flussi proposta/contratto/simulazione (sezione 8.3);
4. applicare modifiche incrementali con validazione immediata.

### Attività aperte (backlog prioritario)

| # | Attività | Priorità |
| --- | --- | --- |
| 7 | Estensione multi-CCNL (Commercio, Metalmeccanici, apprendistato) | Bassa |

## 11) Storico interventi

### 11.11 Luogo/data firme, EventoStorico e pagina CSRF (28/03/2026)

**Luogo e data nei documenti:**

- `templates/rapporto_di_lavoro/stampa_proposta.html`: i campi "Luogo e data" delle sezioni firma
  (datore e lavoratore) mostrano `Palermo, gg/mm/aaaa HH:MM` se il rispettivo campo datetime
  è valorizzato, altrimenti riga tratteggiata per firma manuale.
- `templates/rapporto_di_lavoro/stampa_contratto.html`: stesso trattamento usando
  `contratto.proposta_origine.data_firma_datore` e `data_firma_candidato`.
  Il luogo "Palermo" è fisso per tutte le firme.

**Alimentazione storico (EventoStorico):**

- `rapporto_di_lavoro/views.py`:
  - Aggiunto `from storico.models import EventoStorico` negli import.
  - `firma_proposta_candidato`: dopo il salvataggio crea `EventoStorico(tipo='documento')` con
    timestamp e IP candidato.
  - `firma_definitiva_admin`: dopo `proposta.firma_definitiva_admin()` crea
    `EventoStorico(tipo='assunzione')` con timestamp, numero contratto e operatore.

**Pagina errore CSRF personalizzata:**

- `views.py` (root): aggiunta funzione `csrf_failure(request, reason)` che renderizza
  `templates/403_csrf.html` con status 403.
- `templates/403_csrf.html`: nuova pagina con icona warning, spiegazione in italiano
  (token scaduto per login in altra scheda), pulsanti "Torna indietro" e "Home".
- `settings.py`: aggiunta riga `CSRF_FAILURE_VIEW = 'views.csrf_failure'`.

### 11.10 Ratei 13ª/14ª inclusi nel lordo mensile — 4 pagine (27/03/2026 sessione pomeriggio)

**Obiettivo:** mostrare i ratei lordi di 13ª e 14ª come voci esplicite e includerli nel totale
lordo mensile in tutte le pagine di simulazione/firma.

**File modificati:**

- `templates/candidato/firma_proposta.html`: "Stipendio lordo mensile" → valore `lordo_mensile_totale`
  (incl. ratei); card "Retribuzione lorda mensile" aggiunge righe rat13/rat14 esplicite.
- `rapporto_di_lavoro/views_simulazione_proposta.py`: aggiunge `lordo_con_1314` al context;
  `voci_a` ora include "+ Rateo 13ª lordo", "+ Rateo 14ª lordo" e "Lordo mensile (incl. ratei)".
- `templates/rapporto_di_lavoro/simulazione_economica_proposta.html`: riepilogo costo azienda
  mostra `lordo_con_1314`.
- `rapporto_di_lavoro/views_simulatore.py`: aggiunge `lordo_con_1314` al dict `risultato`.
- `templates/rapporto_di_lavoro/simulatore_paga.html`: Box 1 tbody aggiunge righe rateo 13ª/14ª;
  tfoot usa `lordo_con_1314`.
- `rapporto_di_lavoro/views_simulazione_2026.py`: aggiunge `lordo_con_1314_unit/tot`;
  `totali['lordo_mensile']` accumula il valore inclusivo (propagazione automatica a `totali_annui`).
- `templates/rapporto_di_lavoro/simulazione_2026_risultato.html`: colonne 13ª e 14ª rimosse da
  tutte le tabelle; "Lordo unit." usa `lordo_con_1314_unit`; KPI e tooltip aggiornati.

### 11.9 Redesign workflow candidato→contratto con firma digitale (27/03/2026)

**Obiettivo:** semplificare l'iter da 8 stati/4 azioni a 4 stati/2 azioni per ciascun attore.

**File modificati:**

- `rapporto_di_lavoro/models.py`: nuovi `STATO_CHOICES`, campi `data_firma_candidato`, `ip_firma_candidato`, `data_firma_datore`; metodi `puo_firma_definitiva_admin()`, `firma_definitiva_admin()`, `_crea_rapporto_di_lavoro()`
- `rapporto_di_lavoro/migrations/0026_proposta_firma_digitale.py`: aggiunta campi + data migration stati legacy→nuovi
- `rapporto_di_lavoro/views.py`: nuove view `firma_proposta_candidato`, `firma_definitiva_admin`; aggiornate `invia_proposta_al_dipendente`, `rifiuta_proposta_dipendente`, `converti_proposta_in_contratto` (legacy alias)
- `rapporto_di_lavoro/urls.py`: aggiunti path `firma/` e `firma-admin/`; legacy URLs mantenuti
- `accounts/views_admin_candidati.py`: aggiornati mapping stati `_step_corrente`, `_build_iter`, filtri proposta attiva, stati bloccati
- `accounts/views_candidato.py`: `candidato_dashboard` espone `proposta_da_firmare` (nuovo) oltre a `contratto_da_firmare` (legacy)
- `templates/candidato/firma_proposta.html`: nuova pagina firma digitale candidato
- `templates/candidato/dashboard.html`: banner e timeline aggiornati per nuovo flusso
- `templates/rapporto_di_lavoro/dettaglio_proposta.html`: pulsanti aggiornati per nuovo workflow
- `templates/accounts/candidato_admin_dettaglio.html`: badge e pulsanti azioni aggiornati

### 11.8 Profilo candidato — uppercase, validazioni, flusso contratto (26/03/2026)

**Uppercase e validazioni — `accounts/forms.py`:**

- `CandidatoRegistrazioneForm.save()`: nome e cognome salvati in uppercase.
- `ProfiloCandidatoForm`:
  - Widget `text-uppercase` aggiunto su: luogo_nascita, nazionalita, indirizzo, citta, numero_documento.
  - `clean_luogo_nascita/nazionalita/indirizzo/citta/numero_documento`: uppercase + strip.
  - `clean_codice_fiscale`: uppercase + checksum algoritmo MEF (formato regex + mod-26).
  - `clean_provincia`: uppercase + validazione lunghezza 2 caratteri.
  - `clean_regione_residenza`: validazione contro lista regioni italiane; salva in Title Case.
  - `clean_telefono`: verifica 6-15 cifre reali.
  - `clean_iban`: uppercase + checksum MOD-97 standard.
  - `clean_data_nascita`: data passata, età 16-100 anni.
  - `clean_scadenza_documento`: deve essere futura.
  - `clean()`: emissione < scadenza; data_disponibilita >= data_nascita.

### 11.7 Backlog 1–6 — completamento (26/03/2026)

**Punto 1 — View `riepilogo_mensile_motore`** (nuovi file):

- `presenze/views.py`: aggiunta `riepilogo_mensile_motore()` (lista dipendenti + stati riepilogo,
  azioni aggrega/revisione/approva/elabora) e `anteprima_cedolino_riepilogo()` (calcola cedolino
  dal motore e permette di confermare elaborazione).
- `presenze/urls.py`: aggiunti URL `/presenze/motore/` e `/presenze/motore/anteprima/<dip>/<anno>/<mese>/`.
- Template: `templates/presenze/riepilogo_mensile_motore.html` e `anteprima_cedolino_riepilogo.html`.
- Helper `_contratto_attivo()` e `_calcola_cedolino_da_riepilogo()` in `presenze/views.py`.

**Punto 2 — `azienda` in `views_simulazione_2026`**: già implementato (verificato, nessuna modifica).

**Punto 3 — `num_familiari_a_carico` al motore**:

- `utils_calcoli.py`: `calcola_detrazioni()` accetta `num_familiari: int = 0`.
  Aggiunta detrazioni art. 12 TUIR (stima simulazione): €950/anno per familiare,
  riduzione proporzionale `× (95.000 − reddito) / 95.000`; €0 se reddito ≥ €95.000.
- `utils_motore_paga.py`: `calcola_busta_paga_mese()` accetta `num_familiari_a_carico: int = 0`,
  lo passa a `calcola_detrazioni`.
  `ricava_parametri_proposta_contrattuale()` accetta e passa `num_familiari_a_carico`.
- `views_simulazione_proposta.py`: passa `num_familiari` letto da `profilo.num_familiari_a_carico`.
- `accounts/views_candidato.py`: `_busta_per_fonte()` accetta `num_familiari_a_carico`;
  tutte le chiamate nel dashboard e in `mio_contratto` passano il valore dal profilo.

**Punto 4 — `divisore_str='173'` in `ricava_parametri_proposta_contrattuale`**:

- Corretto in `utils_motore_paga.py`: ora usa `str(round(float(parametro_ccnl.ore_mensili)))`,
  fallback `'173'`. Era hardcoded a `'26'`.

**Punto 5 — Addizionale regionale Sicilia progressiva**:

- Il codice in `utils_calcoli.py` aveva già il fallback progressivo (1,23% / 1,73% / 2,23%)
  ma il DB conteneva due record flat al 1,23% (2025 e 2026) che lo sovrascrivevano.
- Eliminati i record flat dal DB: ora la funzione usa il fallback progressivo corretto.

**Punto 6 — `regione_residenza` nel form profilo candidato**:

- Aggiunto campo `{{ form.regione_residenza }}` nella sezione Residenza di
  `templates/candidato/completa_profilo.html` (dopo provincia, con nota sul calcolo addizionale).

### 11.1 Cleanup documentazione (25/03/2026)

- Documentazione consolidata in questo file unico.
- Rimossi markdown storici/ridondanti; mantenuti `README.md` e `.github/copilot-instructions.md`.

### 11.2 Cleanup codice (25/03/2026)

- Rimosso `notifiche_email/tests.py` (placeholder vuoto).
- Rimosso `costo_lavoro/esempio_utilizzo.py` (script dimostrativo non usato).

### 11.3 Motore paga — bug fix e allineamento (25/03/2026)

Bug corretti in `utils_motore_paga.py`:

- **`ore_mensili` errate** (148 h invece di 173 h): il motore usava `ore_sett/7 × giorni_lav`.
  Corretto a `cp.ore_mensili × coeff`.
- **`paga_giornaliera` errata con divisore=173**: usava `paga_oraria × ore_giorn` = €49,55.
  Corretto a `lordo/26` = €57,69.
- **`giorni_eff_settimana` restituiva 7**: corretto a `round(ore_sett/ore_giorn) = 6` per FIPE.
- **`indennita_ferie` coeff DB = 1,0** (dava c_fer=0,01 = 1%): corretto a 11,54 nel DB.
  Effetto: `rat_fer_m` passato da €18,87 a €217,80.

Funzionalità aggiunte:

- `ricava_parametri_proposta_contrattuale()` — chiama il motore e restituisce payload
  per proposta/contratto con flag 13ª/14ª e `giorni_ferie_annuali` da `RegolaNormativaCCNL`.
- Parametri aggiuntivi: `auto_ore_domenicali_da_calendario`, `modalita_ore_effettive`,
  `competenze_extra_non_imponibili`, `trattenute_extra_mese`.

### 11.4 Calcoli fiscali 2026 — allineamento normativo (25/03/2026)

Modifiche in `utils_calcoli.py`:

- Tutte le funzioni accettano `anno` come parametro.
- `calcola_trattamento_integrativo()`: lookup DB con `data_validita_a`; fallback hardcoded
  mantenuto (misura permanente).
- `calcola_bonus_l207_2024()`: lookup DB con `data_validita_da/a`; **nessun fallback**
  (misura temporanea 2025 — restituisce €0 se nessun record DB valido per l'anno).
- `calcola_irpef_lorda()` e `calcola_detrazioni()`: lookup DB su `ScaglioneIRPEF` e
  `DetrazioneLavoroDipendente`.
- Addizionali regionali/comunali: lookup DB su `AddizionaleRegionale`/`AddizionaleComunale`.

### 11.5 _busta_per_fonte — fix ccnl_obj (25/03/2026)

In `accounts/views_candidato.py`:

- `_busta_per_fonte()` ora risolve `ccnl_obj` via `CCNL.objects.filter(sigla='FIPE').first()`
  (perché `ParametroCCNLTurismo.ccnl` è CharField, non FK).
- `divisore_str` derivato da `cp.ore_mensili` → `'173'`.
- Aggiunta risoluzione profilo SIM2026 (superminimo, indennita_turno, scatti da `RuoloOrganico2026`).

### 11.6 Modulo presenze — implementazione (25/03/2026)

- **`presenze/models.py`**: aggiunto campo `tipo_straordinario` su `Presenza`;
  aggiunto modello `RiepilogoMensilePresenze` con workflow approvazione.
- **`presenze/utils.py`** (nuovo): funzione `aggrega_presenze_per_motore()`.
- **`presenze/migrations/0008_...`**: migration applicata con successo.

Caso monitorato:

- Proposta `SIM2026-1-1-8`
- Contratto `CTR-SIM2026-1-1-8-20260318200710`

### 11.7 Backlog 1–6 — completato (26/03/2026)

Tutti i punti 1–6 del backlog sono stati verificati e completati. Il punto 7 (multi-CCNL) è
rinviato.

### 11.8 Flusso registrazione candidato — miglioramenti (26/03/2026)

#### Uppercase e validazione campi (`accounts/forms.py`)

- `CandidatoRegistrazioneForm.save()`: `first_name`/`last_name` salvati in maiuscolo.
- Widget con classe CSS `text-uppercase` su: luogo_nascita, nazionalita, indirizzo, citta,
  numero_documento.
- `clean_*` methods che normalizzano in uppercase: luogo_nascita, nazionalita, indirizzo,
  citta, numero_documento, codice_fiscale, provincia.
- Validazioni aggiunte:
  - **Codice Fiscale**: regex + checksum MEF (MOD-26) via `_valida_cf()`.
  - **Provincia**: esattamente 2 caratteri alfabetici.
  - **CAP**: esattamente 5 cifre.
  - **Regione residenza**: validata contro `_REGIONI_ITALIANE`; salvata Title Case.
  - **Telefono**: 6–15 cifre reali.
  - **IBAN**: uppercase + checksum MOD-97.
  - **Data di nascita**: data passata, età 16–100 anni.
  - **Scadenza documento**: data futura.
  - `clean()` trasversale: emissione < scadenza; disponibilità ≥ data nascita.

#### Completezza profilo (`accounts/utils.py`)

Nuova funzione `controlla_completezza_profilo(profilo)` che restituisce:

```python

  'consigliati': [(campo, label), ...],
  'percentuale': int,     # % su obbligatori + consigliati
  'doc_scaduto': bool,

Campi obbligatori (bloccano creazione proposta): codice_fiscale, data_nascita,
luogo_nascita, sesso, nazionalita, indirizzo, cap, citta, provincia, telefono,
tipo_documento, numero_documento, scadenza_documento, data_disponibilita.

Campi consigliati (warning): iban, regione_residenza, dichiarazione_no_condanne.

#### Vista admin candidato (`accounts/views_admin_candidati.py`)

- `candidato_admin_dettaglio()`: chiama `controlla_completezza_profilo` e passa
  `completezza` al template.
- `crea_proposta_da_candidato()`: blocca se `not candidato.convalidato` (nuovo, 2026-03-28);
  blocca se `not completezza['completo']` con elenco dei campi mancanti;
  avviso se `completezza['doc_scaduto']`.

#### Template completezza — pannello HR (`templates/accounts/candidato_admin_dettaglio.html`)

Pannello aggiunto dopo i pulsanti azione: barra di avanzamento, badge campi mancanti
(danger), badge campi consigliati (warning), alert scadenza documento, alert successo.

#### Template completezza — dashboard candidato (`templates/candidato/dashboard.html`)

Stessa barra + alert inserita nella sezione MODALITÀ CANDIDATO (dopo header, prima del
pannello "I tuoi dati").

#### Template `completa_profilo.html`

- Aggiunto campo `regione_residenza` nella sezione Residenza (nota addizionale IRPEF).
- Aggiunto pannello completezza (visibile solo se `profilo_completato`).

### 11.9 Bozza contratto modificabile — HR (26/03/2026)

#### Form (`rapporto_di_lavoro/forms.py`)

Aggiunto `RapportoDiLavoroForm` (ModelForm su `RapportoDiLavoro`): tutti i campi
editabili del contratto (tipo, date, posizione, livello, retribuzione, ferie,
straordinari, scatti, TFR). Validazione: data_fine > data_inizio.

#### Vista (`rapporto_di_lavoro/views.py` — `modifica_contratto`)

- Richiede ruolo HR/admin e contratto con `stato='proposta'`.
- GET: carica form pre-popolato, proposta origine, parametri CCNL di riferimento
  e simulazione netto dal motore (`calcola_busta_paga_mese`).
- POST: salva modifiche con `modificato_da=request.user`.
- URL: `contratti/<int:contratto_id>/modifica/` → name `modifica_contratto`.

#### Template (`templates/rapporto_di_lavoro/modifica_contratto.html`)

Layout due colonne:

- **Sinistra**: form con sezioni (Dati fondamentali / Retribuzione / Ferie e permessi /
  Straordinari e scatti).
- **Destra**: dati candidato, parametri CCNL di riferimento (minimo tabellare,
  contingenza, EDR), simulazione netto (lordo → INPS → IRPEF → detrazioni → netto),
  stato iter contrattuale.

#### Link aggiunti

- `candidato_admin_dettaglio.html`: bottone "Modifica bozza contratto" quando
  `contratto.stato == 'proposta'`.
- `dettaglio_proposta.html`: bottone "Modifica bozza" quando
  `proposta.contratto_generato.stato == 'proposta'`.

### 11.10 Template accettazione contratto — dettaglio economico (26/03/2026)

#### Vista (`accounts/views_candidato.py` — `accetta_contratto_dipendente`)

Aggiunta chiamata a `_busta_per_fonte(contratto, ...)` prima del render, passando
`num_familiari_a_carico` dal profilo. Il dict `calc_ret` è ora nel contesto
(anche nel render di errore POST).

#### Template (`templates/candidato/accetta_contratto.html`) — riscrittura completa

Layout due colonne:

- **Sinistra** (col-md-7): condizioni contrattuali complete (tipo, date, posizione,
  livello, turno, ore, ferie, permessi, mensilità aggiuntive, riposi) + sezione
  maggiorazioni straordinario e scatti + form accettazione con timestamp.
- **Destra** (col-md-5): retribuzione lorda (lordo, paga base, contingenza, EDR,
  premio obiettivi) + simulazione cedolino (imponibile → INPS → IRPEF → detrazioni →
  TI/bonus L207 → addizionali → **netto mensile stimato**) + ratei e valori orari
  (paga oraria, rateo 13ª, TFR, netto con ratei/gg).
- Se `calc_ret` è None (parametri CCNL mancanti): mostra solo i dati base del contratto.

### 11.11 Flusso contratto — fix e completamento catena dati (26/03/2026)

#### `PropostaAssunzione.converti_in_contratto()` — fix

1. **`data_sottoscrizione` rimossa** dalla creazione del `RapportoDiLavoro`: era impostata
   prematuramente a `timezone.localdate()` prima che il candidato firmasse. Ora è `NULL`
   e viene impostata correttamente in `accetta_contratto_dipendente` alla firma.
2. **`aliquota_tfr`** ora derivata da `ParametroRatei` (FIPE, tipo='tfr', anno più recente);
   fallback a 6,91% (valore FIPE validato su cedolini reali).

#### `accetta_contratto_dipendente()` — fix

- Aggiunto aggiornamento `dip.data_assunzione = contratto.data_inizio_rapporto` alla firma
  (solo se non già impostato). Il Dipendente ora riceve sia `stato='attivo'` che la data
  di assunzione corretta.

#### Motore — `regione_residenza` (nuovo parametro)

- `calcola_busta_paga_mese()`: aggiunto parametro `regione_residenza: str = 'Sicilia'`.
  Passato a `calcola_addizionale_regionale_sicilia(..., regione=regione_residenza)`.
- `_busta_per_fonte()`: aggiunto parametro `regione_residenza='Sicilia'`; lo legge da
  `ProfiloCandidato.regione_residenza` e lo passa al motore.
- Aggiornate tutte le chiamate a `_busta_per_fonte()` in `views_candidato.py`
  (dashboard, `accetta_contratto_dipendente`, `mio_contratto`) per passare la regione.
- Vista `modifica_contratto` in `views.py`: corretta chiamata al motore (era errata:
  passava `contratto=` invece di `parametro_ccnl=`); aggiunta `regione_residenza`.

### 11.12 Flusso end-to-end — verifica e fix (26/03/2026)

#### Flusso completo verificato

```text
crea_proposta → [bozza] → invia_proposta_al_dipendente → [inviata_al_dipendente]
→ approva_proposta_admin → [approvata_admin]
→ accetta_proposta_dipendente → [accettata_dipendente]
→ converti_proposta_in_contratto → RapportoDiLavoro[proposta]
→ (modifica_contratto — opzionale HR)
→ accetta_contratto_dipendente → [sottoscritto] + ruolo=dipendente
```

#### Problemi trovati e risolti

##### 1. Vista `invia_proposta_al_dipendente` mancante

Aggiunta in `rapporto_di_lavoro/views.py` e URL `/proposte/<id>/invia/`
(name `invia_proposta_al_dipendente`). Transizione `bozza → inviata_al_dipendente`.
Le proposte in `bozza` sono create da `simulazione_2026_crea_proposte`.

##### 2. Pulsanti Approva/Rifiuta mostrati per tutte le proposte

In `dettaglio_proposta.html`: ora i pulsanti Approva/Rifiuta sono nascosti per stato
`bozza`, `approvata_admin`, `rifiutata_admin`, `convertita_in_contratto`. Il bottone
"Invia al dipendente" appare solo per stato `bozza`.

##### 3. Dashboard candidato — messaggi proposta differenziati

- `approvata_admin`: banner arancio "La proposta è pronta per la tua firma!" + link "Leggi e accetta".
- `inviata_al_dipendente`: banner grigio "in attesa di approvazione aziendale" + link "Leggi" (senza accettazione — by design).

#### Tutti gli step verificati operativi

| Step | Vista | URL name | Template |
| ---- | ----- | -------- | -------- |
| Crea proposta | `crea_proposta` | `crea_proposta_assunzione` | `crea_proposta.html` |
| Invia al dipendente | `invia_proposta_al_dipendente` | `invia_proposta_al_dipendente` | `dettaglio_proposta.html` |
| Approva admin | `approva_proposta_admin` | `approva_proposta_admin` | `dettaglio_proposta.html` |
| Accetta proposta | `accetta_proposta_dipendente` | `accetta_proposta_dipendente` | `dettaglio_proposta.html` + dashboard |
| Converti in contratto | `converti_proposta_in_contratto` | `converti_proposta_in_contratto` | `dettaglio_proposta.html` |
| Modifica bozza contratto | `modifica_contratto` | `modifica_contratto` | `modifica_contratto.html` |
| Firma contratto | `accetta_contratto_dipendente` | `accetta_contratto_dipendente` | `accetta_contratto.html` + dashboard |

### 11.13 Flusso assunzione — fix URL, form dinamico e lista proposte (26/03/2026)

#### Fix redirect `proposte/nuova/` → `proposte/crea/`

La vista `crea_proposta_da_candidato` (`accounts/views_admin_candidati.py`) reindirizzava
a `/rapporti/proposte/nuova/` (URL inesistente). Corretto in `/rapporti/proposte/crea/`
(nome registrato: `crea_proposta_assunzione`).

#### Banner "candidati pronti" in lista proposte (`lista_proposte`)

Aggiunto context `candidati_pronti`: lista di utenti con ruolo `candidato`, `convalidato=True`,
`profilo_completato=True`, `azienda_interesse=azienda_operativa` (filtro azienda aggiunto
2026-03-28) e senza proposta attiva. Il template `lista_proposte.html` mostra un banner
verde in cima con un pulsante diretto "Crea proposta → [Nome Cognome]" per ognuno.
Questo elimina la necessità di navigare Candidati → dettaglio → Crea proposta.

#### `crea_proposta` — gestione `dipendente_id` da querystring

Quando l'admin arriva da `crea_proposta_da_candidato`, la vista legge `?dipendente_id=X` e:

- imposta `initial['dipendente']` per pre-selezionare il candidato;
- passa `dipendente_prefill_id` al form per allargare il queryset del campo `dipendente`
  (include il Dipendente anche se appartiene a un'azienda diversa dall'`azienda_operativa`).

La proposta viene creata in stato `bozza` (non più `inviata_al_dipendente`): l'HR
deve inviare esplicitamente tramite "Invia al dipendente".

#### `PropostaAssunzioneForm` — `dipendente_prefill_id`

Nuovo kwarg `dipendente_prefill_id`: se presente, il queryset del campo `dipendente`
viene costruito con `Q(id=dipendente_prefill_id) | Q(azienda=azienda_operativa, stato__in=['attivo','candidato'])`.

### 11.14 Form nuova proposta — select CCNL e moduli (26/03/2026)

#### Problema: select `parametro_ccnl` con 40 voci duplicate

Il form mostrava tutte le versioni storiche dei parametri CCNL (4 versioni × 10 livelli = 40
voci). L'utente vedeva "Livello 5" quattro volte con versioni diverse.

#### Fix: `_parametri_ccnl_correnti()` e `ParametroCCNLChoiceField`

Aggiunti in `rapporto_di_lavoro/forms.py`:

```python
def _parametri_ccnl_correnti():
    max_dec = ParametroCCNLTurismo.objects.filter(attivo=True).aggregate(
        m=Max('decorrenza_validita_da'))['m']
    return ParametroCCNLTurismo.objects.filter(attivo=True, decorrenza_validita_da=max_dec).order_by('livello')

class ParametroCCNLChoiceField(forms.ModelChoiceField):
    def label_from_instance(self, obj):
        lordo = float(obj.importo_lordo_mensile or obj.paga_base_mensile)
        return f"Livello {obj.livello} — {float(obj.paga_base_mensile):,.2f} € base · {lordo:,.2f} € lordo"
```

Il form ora mostra solo 10 voci (versione più recente) con etichette leggibili.

#### Fix: `ModuloContrattuale` duplicato

ID=1 "Proposta assunzione standard" era duplicato di ID=5 "Proposta Assunzione Standard".
ID=1 disattivato (`attivo=False`).

### 11.15 Form nuova proposta — auto-fill CCNL senza tipo contratto (26/03/2026)

#### Problema

Il JS `proposta_form_dinamico.js` richiedeva che **entrambi** `parametro_ccnl` e
`tipo_contratto` fossero selezionati prima di chiamare l'API. Se l'utente selezionava
solo il livello, nessun campo si aggiornava.

#### Fix API (`api_ccnl_parametri`)

`tipo_contratto_id` reso opzionale. Se assente, la vista usa il primo `TipoContratto`
con `coefficiente_ore=1` (full-time) come base di calcolo.

#### Fix JS (`proposta_form_dinamico.js`)

- Rimossa la guard `if (!parametroId || !tipoContrattoId) return`.
- Ora basta selezionare il livello CCNL per scatenare il pre-fill di tutti i campi.
- Quando si seleziona anche il tipo contratto, le ore vengono ricalcolate con il
  coefficiente corretto (es. part-time 50% → ore dimezzate).
- `sincronizzaPosizione()`: se cambia `posizione_scelta`, cerca nell'elenco
  `parametroSelect` l'opzione corrispondente e la seleziona, poi chiama `verificaEcompila()`.

### 11.16 `django.contrib.humanize` aggiunto a `INSTALLED_APPS` (26/03/2026)

Il template `simulazione_economica_proposta.html` usava `{% load humanize %}` ma
`django.contrib.humanize` non era in `INSTALLED_APPS`. Aggiunto in `settings.py`.

### 11.17 Simulazione economica proposta — colori e PDF (26/03/2026)

#### Problema contrasto

Headers card usavano `bg-*-opacity-10` (quasi trasparente) con testi chiari sullo stesso
sfondo. Righe "NETTO" usavano `table-success`/`table-primary` (colori Bootstrap pallidi).

#### Nuovo schema colori

| Elemento | Prima | Dopo |
| -------- | ----- | ---- |
| Header col. A | `bg-success bg-opacity-10` | `#1a7a42` (verde solido, testo bianco) |
| Header col. B | `bg-primary bg-opacity-10` | `#1756c8` (blu solido, testo bianco) |
| Riga NETTO A | `table-success` | verde scuro `#1a7a42`, testo bianco |
| Riga NETTO B | `table-primary` | blu scuro `#1756c8`, testo bianco |
| Footer A/B | sfondo chiarissimo, text-success/primary | sfondo bianco, valori colorati scuri |
| Costo azienda | header senza sfondo | header antracite `#343a40`, testo bianco |

Classi CSS aggiunte: `header-a`, `header-b`, `header-warn`, `footer-a`, `footer-b`,
`totale-a`, `totale-b`, `costo-val`, `costo-val-danger`.

#### Banner confronto paga attesa (colonna B)

Aggiunto box evidenziato in grassetto nel footer della colonna B, visibile solo se
`paga_attesa` è disponibile. Mostra:

- Paga giornaliera attesa dal candidato (`paga_attesa`)
- Paga giornaliera netta + ratei (`paga_gg_ratei`)
- Differenza (`delta_gg_ratei`) con percentuale, colorato verde/rosso

#### Stampa PDF A4 landscape

Aggiunto `@page { size: A4 landscape; margin: 8mm 10mm; }` con:

- `print-color-adjust: exact` per preservare sfondi colorati
- `html, body { font-size: 9pt; }` per adattare alla pagina
- Colonne al 50% con `float: left` per layout affiancato in stampa

### 11.18 Lista proposte lato candidato — fix visibilità e label (26/03/2026)

#### Bug: `Dipendente.utente = None`

Il metodo `_sincronizza_dipendente()` in `views_candidato.py` non impostava `utente`
durante l'update di un Dipendente esistente (era presente solo nella creazione).
Questo causava `PropostaAssunzione.objects.filter(dipendente__utente=request.user)`
a non trovare nulla per il candidato.

**Fix:** aggiunto nel blocco update:

```python
if not profilo.dipendente.utente:
    profilo.dipendente.utente = user
```

Il dato corrotto esistente è stato corretto direttamente via shell.

#### Proposte `bozza` escluse dalla vista candidato

Le proposte in stato `bozza` (non ancora inviate dall'HR) vengono ora escluse con
`.exclude(stato='bozza')` dalla query per `ruolo in ['dipendente','candidato']`.
Quando esiste una bozza in lavorazione, il template mostra un banner giallo
"Proposta in preparazione — l'ufficio HR sta predisponendo la tua proposta".

#### Label rinominato

- Menu candidato (`base.html`): "Le mie proposte" → **"Proposta di assunzione"**
- Titolo pagina `lista_proposte.html`: adattato per ruolo candidato con
  `{% if user.ruolo == 'candidato' %}Proposta di assunzione{% else %}Proposte di Assunzione{% endif %}`

### 11.19 Admin candidati — pagina dettaglio: potenziamento iter e richieste (sessione precedente, deploy 31/03)

#### Problema: potenziamento iter e richieste

#### Nuove view in `accounts/views_admin_candidati.py`

| View | URL name | Funzione |
| --- | --- | --- |
| `forza_tutto_candidato` | `forza_tutto_candidato` | Sblocca in un colpo email + profilo + convalida + chiude richieste aperte |
| `reset_email_verifica_candidato` | `reset_email_verifica_candidato` | Ripristina `email_verificata=False` |
| `forza_verifica_email_candidato` | `forza_verifica_email_candidato` | Forza `email_verificata=True` |
| `forza_profilo_completato_candidato` | `forza_profilo_completato_candidato` | Forza `profilo_completato=True` + `data_completamento=now()` |
| `elimina_richiesta_integrazione_candidato` | `elimina_richiesta_integrazione_candidato` | Elimina una `RichiestaIntegrazioneCandidato` |
| `modifica_richiesta_integrazione_candidato` | `modifica_richiesta_integrazione_candidato` | Modifica `titolo` e `messaggio` di una richiesta |
| `chiudi_richiesta_integrazione_candidato` | `chiudi_richiesta_integrazione_candidato` | Imposta `stato='approvata_hr'` + `data_approvazione_hr=now()` |

Aggiornato `candidato_admin_dettaglio()`: aggiunge `richieste_aperte` al context.

Aggiornato `_build_iter()`: aggiunge `step_key` a ogni dict di step.

#### Redesign `templates/accounts/candidato_admin_dettaglio.html`

- Icone iter 40px con animazione `pulse` sull'icona attiva.
- Classi `.iter-icon-btn` colorate per stato (success/warning/secondary).
- Classi `.status-chip` per la riga badge sottostante.
- Classe `.kpi-highlight` (box giallo `#fff3cd` bordo `#ffc107`) per evidenziare KPI.
- Pulsante **⚡ Sblocca tutto** nella timeline (POST a `forza_tutto_candidato`).
- Pannello **Richieste integrazione in attesa** con azioni inline Approva/Chiudi/Modifica/Elimina.
- Form richiesta integrazione rimosso dalla pagina (non più inline).

---

### 11.20 Simulazione economica proposta — fix anno e paga giornaliera (31/03/2026)

#### Bug 1: anno "2.026" → "2026"

`USE_THOUSAND_SEPARATOR=True` nel locale italiano formattava l'intero `2026` come "2.026".

**Fix:** in `templates/rapporto_di_lavoro/simulazione_economica_proposta.html`,
tutte le occorrenze `{{ anno }}` sostituite con `{{ anno|stringformat:"d" }}` (4 punti).

#### Bug 2: `paga_giornaliera` calcolata sul solo lordo base

La quota giornaliera lorda era `lordo_mensile / 26` senza includere i ratei di 13ª e 14ª.

**Fix:** in `rapporto_di_lavoro/views_simulazione_proposta.py`:

```python
paga_giornaliera = (lordo_mensile_totale / Decimal('26')).quantize(Q2)
```

dove `lordo_mensile_totale = lordo_mensile + rateo_13 + rateo_14`.

#### Evidenziazione KPI paga

Aggiunto stile `.kpi-highlight` (`background:#fff3cd; border:2px solid #ffc107; border-radius:6px; padding:6px 10px`)
e applicato alle righe "Paga giornaliera" e "Paga oraria" nei footer A e B.

---

### 11.21 Fix firma proposta — logout imprevisto (31/03/2026)

**Causa:** il decorator `@user_passes_test` nelle view `firma_proposta_candidato` e
`rifiuta_proposta_dipendente` usava `getattr(u, 'ruolo', None)` che restituisce sempre
`None` perché i ruoli sono ManyToMany (non un campo diretto).

**Fix:** in `rapporto_di_lavoro/views.py`, decorator corretto:

```python
lambda u: u.is_authenticated and (u.has_ruolo('candidato') or u.has_ruolo('dipendente'))
```

---

### 11.22 Simulazione 2026 — fix 414 Request-URI Too Large (31/03/2026)

#### Causa

Il form di configurazione usava `method="get"` con 7+ dipendenti × ~50 parametri ciascuno.
nginx rifiutava la richiesta con **414 Request-URI Too Large**.

#### Soluzione: conversione GET → POST con session fallback

**`rapporto_di_lavoro/views_simulazione_2026.py`:**

1. Aggiunto helper `_get_sim_params(request)`:
   - Legge i parametri in ordine: `request.POST` → `request.GET` → `request.session['sim2026_querystring']`
   - Salva nella session ogni volta che trova params in POST o GET
   - Restituisce un `QueryDict` compatibile con tutte le view downstream

2. `_build_ruoli_config(request)`: usa `params = _get_sim_params(request)` invece di
   `request.GET` direttamente. Fix anche della closure interna `_dec`
   (argomenti default `_p`, `_m`, `_rid` per evitare il problema di cattura di variabili del loop).

3. `_calcola_simulazione_2026(request)`: usa `_params = _get_sim_params(request)` per
   la lettura del divisore.

4. `simulazione_2026_risultato`: controlla `_get_sim_params` invece di `request.GET`.
   `context['querystring']` viene da `_sim_params.urlencode()`.

5. `simulazione_2026_excel`, `simulazione_2026_crea_proposte`: usano `_get_sim_params`
   → caricano dalla session senza bisogno di querystring nell'URL.

**`templates/rapporto_di_lavoro/simulazione_2026_config.html`:**

- Form cambiato da `method="get"` a `method="post"` con `{% csrf_token %}`.

**`templates/rapporto_di_lavoro/simulazione_2026_risultato.html`:**

- Link Excel e Crea Proposte senza `?{{ querystring }}` (usano session).
- Rimosso link "Modifica" ridondante; rimane solo "← Config" (ricarica da DB).

**`templates/rapporto_di_lavoro/simulazione_2026_crea_proposte.html`:**

- Link "← Risultato" e "Annulla" senza querystring.

#### Flusso post-fix

```text
Config (POST form) → simulazione_2026_risultato
  └→ salva params in session['sim2026_querystring']
  └→ link "⬇ Excel"    → simulazione_2026_excel    (legge da session)
  └→ link "Proposte"   → simulazione_2026_crea_proposte (legge da session)
  └→ link "← Config"   → simulazione_2026_config    (ricarica da DB RuoloOrganico2026)
```

---

### 11.23 Simulazione annua — etichette, URL ed Excel (10/04/2026)

**Obiettivo:** naming utente **Simulazione annua**, URL `/rapporti/simulazione-annua/`, export coerente.

#### UI / navigazione

- Menu, dashboard e pagine: **Simulazione annua**; sottotesti con periodo CCNL (es. 2026) dove serve.
- Riepilogo **attivi** vs **testate scenario** e **delta** su config e risultato.
- **Simulatore paga**: calcolo singolo mese (`simulatore-paga/`).

**Excel** (`simulazione_2026_excel`)

- File: `Simulazione_annua_{anno}_{slug}.xlsx`; titoli foglio «SIMULAZIONE ANNUA — …»; riga KPI come in web.

#### URL (agg. nome path)

- Pattern canonici: `simulazione-annua/`, `…/risultato/`, `…/excel/`, `…/crea-proposte/`. I vecchi `simulazione-2026/…` effettuano redirect HTTP al nuovo path (stessi `name=` Django per `{% url %}`).

**Codice:** `views_simulazione_2026.py` — `_somma_testate_ruoli`, KPI in contesto ed export.

---

#### Problema: badge Parziale non esplicativo

La pagina `/accounts/candidati/` mostrava solo il badge "Parziale" senza indicare
quali campi mancassero, rendendo necessario entrare nel dettaglio di ogni candidato
per capire cosa completare.

#### Modifiche

**`accounts/views_admin_candidati.py` — `lista_candidati`:**

Ogni elemento del loop arricchito con `completamento = controlla_completezza_profilo(profilo)`:

```python
candidati.append({
    ...
    'completamento': completamento,   # dict con mancanti, consigliati, percentuale
})
```

```text

- URL: `candidati/<int:user_id>/aggiorna-campi/` (name `aggiorna_campo_profilo_candidato`)
- POST-only; itera `CAMPI_OBBLIGATORI_PROPOSTA + CAMPI_CONSIGLIATI_PROPOSTA`
- Coerce automatica dei tipi: date (`date.fromisoformat`), booleano (`on/1/true`)
- Se dopo il salvataggio tutti i campi obbligatori sono presenti:
  imposta automaticamente `profilo_completato=True` e `data_completamento=now()`
- Redirige a `lista_candidati`; se `POST['next']=='dettaglio'` → `candidato_admin_dettaglio`

**`accounts/urls.py`:** aggiunto `path('candidati/<int:user_id>/aggiorna-campi/', ...)`.

**`templates/accounts/lista_candidati.html` — ristrutturazione completa:**

- Badge **"Parziale"** → pulsante Bootstrap collapse che mostra la percentuale
  (es. *Parziale 60%*) e un'icona chevron.
- Per ogni candidato con profilo parziale: riga `<tr>` nascosta con `collapse`
  identificata da `#profilo-panel-{user_id}`.
- Contenuto del pannello:
  - **Barra progresso** colorata (verde ≥80%, giallo ≥50%, rosso <50%)
  - **Chip rossi** per campi obbligatori mancanti
  - **Chip gialli** per campi consigliati mancanti
  - **Mini-form inline** con input tipizzati (text/date/select/checkbox per sesso,
    tipo_documento, CF, CAP, provincia, dichiarazione_no_condanne, IBAN, date)
  - **Pulsante "Salva campi"** → POST a `aggiorna_campo_profilo_candidato`
  - **Link "Modifica profilo completo"** → `modifica_profilo_candidato`
  - **Pulsante "Forza completamento"** → `forza_profilo_completato_candidato` (con confirm JS)
- Caso speciale: se tutti i campi sono presenti ma `profilo_completato=False`,
  il pannello mostra solo "✓ Tutti i campi presenti" + pulsante "Segna come completato".

#### Caso d'uso verificato

Candidato GIACOMO INGRASSIA (user_id=11): aveva `profilo_completato=False` nonostante
tutti i dati fossero presenti. Fix applicato direttamente sulla produzione via shell
(`profilo.profilo_completato=True; profilo.data_completamento=timezone.now()`).
Con la nuova funzionalità, il caso si risolve dal pannello inline senza entrare nel dettaglio.
