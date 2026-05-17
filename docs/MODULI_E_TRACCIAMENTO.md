# GESPER — Moduli: nomi canonici e tracciamento implementazione

Documento di riferimento per **ripensamenti**, **priorità** e **allineamento** tra codice, help in linea e roadmap.

**Formati data/euro/anno (tutta la procedura):** vedi [`docs/CONVENZIONI_FORMATO.md`](CONVENZIONI_FORMATO.md).

## Convenzioni

| Risorsa | Dove si definisce |
| --- | --- |
| **Codice modulo** (`slug`, es. `reg-dipendente`) | `guida/registry.py` (`MODULI`) — elenco ufficiale |
| **Classificazione nuovo / ibrido / consolidato** | campo `evoluzione` in ogni voce di `MODULI` |
| **Aree legacy da rimuovere** | `guida/registry.py` (`LEGACY_AREE`) + tabella sotto |
| **Testi help** (modulo / campo) | Django Admin → *Guida e help contestuale* → *Voci guida* (`VoceGuida`) |
| **Pagine help in app** | `/guida/` indice (moduli + tabella legacy) · `/guida/m/<codice>/` dettaglio |
| **Icona contestuale in template** | `{% load guida_tags %}` poi `{% guida_link "codice-modulo" %}` o `{% guida_link "codice-modulo" "codice-campo" %}` |

Aggiungere un **modulo nuovo**: **1)** riga in `MODULI` con `evoluzione` adeguata · **2)** riga nella tabella sotto · **3)** eventuali voci in Admin.

Aggiungere una **voce legacy**: riga in `LEGACY_AREE` con `modulo_sostitutivo` = codice del modulo che la sostituirà; aggiornare `stato` quando si elimina il vecchio flusso (`rimosso`).

### Codici campo (help)

Usare `slug` minuscoli con trattini, es. `email-lavoro`, `matricola`, `consenso-geolocalizzazione`.  
Una sola voce per modulo con `codice_campo` vuoto = testo **generale** del modulo (ancora `#campo-intro`).

---

## Evoluzione: cosa significa

| Valore | Significato | Quando togliere il “vecchio” |
| --- | --- | --- |
| **nuovo** | Target di progetto; UI o logica ancora da completare | Non applicabile finché non esiste il sostituto |
| **ibrido** | Già usato in produzione ma convive con duplicazioni / menu legacy | Quando `LEGACY_AREE` collegate hanno criterio soddisfatto → passare a `da_smantellare` poi rimuovere codice/menu |
| **consolidato** | Questo modulo è il riferimento per l’ambito | Legacy collegato rimovibile appena i criteri sono OK |

---

## Tabella moduli e stato implementazione

*Aggiornare **Stato** e **Note** in parallelo a `evoluzione` nel registry se serve più dettaglio operativo.*

| Codice | Titolo (breve) | App Django | Evoluzione (registry) | Stato / note |
| --- | --- | --- | --- | --- |
| `reg-dipendente` | Registrazione e identità dipendente | accounts, anagrafiche | nuovo | **Lista / crea / modifica dipendente** con box o alert + guida (`modifica_dipendente.html` incluso). Restano self-service e convalida flussi. |
| `portale-dipendente` | Portale dipendente | accounts, documenti, richieste, presenze | ibrido | Menu unificato; **Guida portale** in navbar; alert + link guida su `candidato/dashboard.html`. |
| `modulo-admin` | Operazioni HR / admin | anagrafiche, rapporto_di_lavoro, … | ibrido | Navbar **Contratti e richieste**, **Storico** (HR). **Richieste** (`richieste/lista.html`): alert + `guida_link` (staff vs dipendente); **log attività/errori**; **riferimenti economici** (`paghe-controlli` sul titolo); **profilo admin** → guida **multi-azienda**. Simulazione annua / calendario aziendale già coperti. |
| `modulo-consulente` | Profilo consulente | documenti, accounts, workflow | ibrido | Hub **carica-documento**; **dashboard**; **`consulente/documenti.html`**: `guida_link` + alert verso **documenti-compliance**; breadcrumb buste; Presenze → **Cedolino da riepilogo**. |
| `presenze-turni` | Presenze e calendario | presenze | ibrido | **Ingresso predefinito:** griglia mensile (`riepilogo_presenze_mese`); `lista_dipendenti_presenze` reindirizza lì (`?vista=elenco` = tabella legacy). Nome dipendente in griglia → `calendario_presenze` + **panoramica mese** a righe orarie. **Cedolino da riepilogo** per admin/HR/consulente. Alert anche su **calendario aziendale** e **pianificazione orari**. |
| `documenti-compliance` | Documenti e consensi | documenti, rapporto_di_lavoro, accounts | nuovo | `documenti/lista.html`; **upload massivo** buste/CUD (`upload_buste_massivo`, `upload_cud_massivo`) con link guida. Privacy, geo, firma in roadmap. |
| `paghe-controlli` | Paghe e riconciliazioni | documenti, storico, rapporto_di_lavoro | ibrido | **Libro paga**, **simulatore**, **riferimenti economici** (`gestione_riferimenti_economici.html` con titolo `guida_link`); upload buste massivo con cross-link. |
| `multi-azienda` | Contesto multi-aziendale | anagrafiche, accounts | consolidato | `lista_aziende`: sottotitolo + `guida_link` + alert (solo **admin/superuser** — la vista è ristretta). |

---

## Controllo copertura UI — Amministratore vs Consulente

Usare questa tabella per **verifiche periodiche** (menu `templates/base.html`, dashboard, guide in pagina).  
**HR** condivide quasi tutto con Admin tranne: menu **Admin** (simulazioni, log, impostazioni, Django Admin) e in barra **Storico** è top-level solo per HR puro.

### Amministratore (`superuser` / ruolo `admin`)

| Area | Voce menu / ingresso | Modulo (registry) | Note |
| --- | --- | --- | --- |
| Personale | Personale → Dipendenti, Candidati | `reg-dipendente`, `modulo-admin` | Lista/crea/modifica + guida in template |
| Documenti | Tutti, Buste, CUD, F24, Libro paga | `documenti-compliance`, `paghe-controlli` | Alert su `documenti/lista`; upload massivi con link guida |
| Presenze | Griglia mese, Export Excel, Pianificazione, Calendario aziendale, Cedolino motore | `presenze-turni` | HR senza admin: no pianificazione/calendario aziendale |
| Contratti | Proposte/contratti, Richieste | `modulo-admin` | `lista_proposte` + guida; `richieste/lista` + alert |
| Admin ▼ | Dashboard, Simulatore, Simulazione annua, Rif. economici, Impostazioni, Log, Storico, Django Admin | `modulo-admin`, `paghe-controlli` | Alert su simulazione, calendario aziendale (da menu Admin), riferimenti economici, log |
| Profilo / azienda | Profilo, selezione azienda | `multi-azienda` | Link guida + elenco aziende (blocco admin) |
| Home `/` | Griglia moduli generica + card solo se admin | — | Card Simulazione / Calendario / Rif. economici / Dashboard admin solo per `superuser` o ruolo `admin` (non HR-only) |

### Consulente (ruolo `consulente`)

| Area | Voce menu / ingresso | Modulo (registry) | Note |
| --- | --- | --- | --- |
| Dashboard | Consulente → Dashboard | `modulo-consulente` | Alert + guida; card verso F24, partitario, CUD, import PDF, griglia presenze |
| Anagrafiche | Anagrafiche (candidati/proposte) | `modulo-admin` + `modulo-consulente` | Flussi approvazione e PDF proposta |
| Documenti ▼ | Elenco documenti dipendenti, Buste/CUD/F24 (liste filtrate), Libro paga, Carica documenti paghe | `modulo-consulente`, `documenti-compliance`, `paghe-controlli` | `consulente/documenti.html` con guida; hub `carica-documento` |
| Presenze ▼ | Griglia mese, Export schermata, Excel mese corrente, Cedolino motore | `presenze-turni` | Stessi URL HR dove `_is_admin_hr`; **no** pianificazione orari / calendario aziendale |
| — | (solo dashboard) Import PDF unico, Riepilogo F24 annuale, Partitario paghe | `modulo-consulente`, `paghe-controlli` | Non tutte replicati in navbar; raggiungibili dalla dashboard |
| Profilo | Profilo | `multi-azienda` | Azienda legata al profilo utente (no `lista_aziende` se non admin) |

### Lacune / attenzioni

- **`home.html`** (URL **`/moduli/`**, name `centro_moduli`): griglia **per ruolo** (dipendente / candidato / consulente / HR–admin). Il consulente vede solo i moduli dell’area consulente più profilo; HR non vede la fascia solo-admin (simulazione, calendario aziendale, rif. economici, dashboard admin).
- **Testi guida in app**: dipendono da voci **VoceGuida** in Django Admin; la tabella sopra copre solo **punti di ingresso** e **alert** già presenti nei template.

---

## Mappa legacy → modulo sostitutivo (sincronizza con `LEGACY_AREE`)

Usare questa tabella per **decidere cosa eliminare**: quando il modulo sostitutivo copre il caso e il criterio è soddisfatto, impostare lo stato legacy a `da_smantellare`, rimuovere codice/menu, poi `rimosso`.

| ID legacy (registry) | Titolo | Modulo sostitutivo | Stato |
| --- | --- | --- | --- |
| `legacy-portale-candidato-dipendente-stesso-menu` | Menu candidato/dipendente sovrapposto | `portale-dipendente` | convivenza |
| `legacy-documenti-multi-entry` | Più entry point documenti | `modulo-admin` | convivenza |
| `legacy-simulazioni-menu-admin` | Simulatori sparsi nel menu Admin | `modulo-admin` | convivenza |
| `legacy-django-admin-crud-parallelo` | Doppio CRUD app vs Django Admin | `modulo-admin` | convivenza |
| `legacy-consulente-upload-singoli` | Upload separati buste/CUD/… | `modulo-consulente` | convivenza |
| `legacy-registrazione-solo-candidato` | Solo percorso candidato per self-reg | `reg-dipendente` | convivenza |

Il testo completo del **criterio di rimozione** per ciascuna riga è nel file `guida/registry.py` (campo `criterio_rimozione`).

---

## Storico decisioni (opzionale)

- *2026-04-09:* Creazione app `guida` (`VoceGuida`), registry `MODULI`, URL `/guida/`, link “Guida” in navbar utenti autenticati.
- *2026-04-09:* Introdotti `evoluzione` su ogni modulo e `LEGACY_AREE` con tabella in `/guida/` per pianificare rimozioni.
- *2026-04-10:* Nome prodotto **simulazione annua** (organico e costo); confronto attivi vs testate scenario; export `Simulazione_annua_*.xlsx`; URL **`/rapporti/simulazione-annua/`** con redirect da `simulazione-2026/`.
- *2026-04-10 (seq. moduli):* Hub **modulo-admin** in navbar (Contratti e richieste; link Storico per HR); **Personale → Dipendenti** visibile anche a **HR**.
- *2026-04-10 (seq. end-to-end):* Consulente hub **carica-documento**; portale **Guida**; documenti alert compliance; admin **Richieste** / **Libro paga** / **Cedolino da riepilogo**; note su `lista_dipendenti` e `crea_dipendente`.
- *2026-04-10 (moduli successivi):* Presenze: menu motore per **HR/consulente**; alert su dipendenti/riepilogo mese/motore; **Libro paga** e **simulatore** con alert; **consulente** dashboard + breadcrumb buste; **lista proposte** + guida admin; **lista aziende** + guida multi-azienda.
- *2026-04-10 (presenze grafiche + guide):* Default **griglia mese**; redirect da elenco dipendenti; **panoramica mese** sul calendario singolo; simulazione annua / risultato / calendario aziendale / pianificazione orari con alert; redirect assenza azienda → **profilo** (no loop).
- *2026-04-10 (altri moduli UI):* **Richieste**, **portale** (dashboard), **log attività/errori**, **riferimenti economici**, **documenti consulente**, **upload massivo**, **modifica dipendente**, **profilo admin** (multi-azienda): alert/link guida incrociati ai codici registry.
- *2026-04-10 (controllo Admin/Consulente):* Sezione **Controllo copertura UI** in questo doc; menu Presenze senza doppioni (HR + consulente) + **Export Excel** in dropdown consulente.
- *2026-04-10 (home per ruolo):* `home.html` ramificata per ruolo; rotta **`/moduli/`** (`centro_moduli`) per aprire il centro moduli senza sostituire il redirect `home` → profilo.

---

## Esempio in template

```django
{% load guida_tags %}
<label>Email di lavoro {% guida_link "reg-dipendente" "email-lavoro" %}</label>
```

La seconda stringa deve coincidere con `codice_campo` della voce in Admin (e con l’ancora `#campo-email-lavoro` sulla pagina modulo).
