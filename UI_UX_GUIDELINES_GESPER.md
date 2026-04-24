# UI/UX Guidelines GESPER

## Obiettivo

Definire regole uniche di interfaccia per garantire:
- coerenza visiva tra moduli;
- riduzione errori operativi;
- esperienza uniforme per admin, consulente e dipendente.

Queste linee guida si applicano a tutte le pagine web e alle future schermate app/PWA.

## Principi base

1. Una pagina = un obiettivo principale.
2. Una azione primaria chiara per schermata.
3. Stati e workflow espliciti e sempre visibili.
4. Terminologia coerente in tutto il sistema.
5. Accessibilita` minima garantita (contrasto, focus, messaggi chiari).

## Ruoli e coerenza di esperienza

- `Admin/Supervisore`: vista gestionale completa, azioni ad alto impatto con conferma.
- `Consulente`: focus su documenti paghe/CU, presenze, supporto approvazioni.
- `Dipendente`: focus su consultazione dati personali, documenti, ferie/permessi, richieste.

Regola: componenti e pattern sono gli stessi per tutti i ruoli; cambia solo il perimetro delle azioni.

## Design system minimo (baseline)

### Palette semantica

- Primario: azioni principali (salva, conferma, invia).
- Neutro: contenuto standard.
- Successo: operazione completata, stato positivo.
- Attenzione: warning non bloccante.
- Errore: blocchi di validazione o operazione fallita.
- Info: messaggi informativi.

Usare i colori per semantica, non per estetica casuale.

### Tipografia e spaziatura

- Un solo font family globale.
- Gerarchia titoli fissa (`H1`, `H2`, `H3`).
- Testo body uniforme.
- Spaziatura consistente (griglia 4/8 px).

### Componenti standard

- Bottoni: `Primary`, `Secondary`, `Ghost`, `Danger`.
- Input form: label sopra campo + help text opzionale + errore inline.
- Tabelle: header fisso, ordinamento chiaro, paginazione uniforme.
- Badge stato: set unico per tutti i workflow.
- Alert/toast: posizione e stile costanti.
- Modali: solo per conferme o azioni distruttive.

## Convenzioni di naming UI

- Usare sempre gli stessi termini:
  - "Proposta", "Contratto", "Richiesta", "Presenza", "Riepilogo", "Documento".
- Evitare sinonimi nella UI (es. "Accetta" vs "Conferma" usati in modo casuale).
- Etichette bottoni in forma verbale chiara:
  - "Salva bozza", "Invia al dipendente", "Approva", "Rifiuta", "Converti in contratto".

## Stati workflow (standard visuale)

Ogni stato deve avere:
- badge colore dedicato;
- descrizione breve;
- azioni consentite.

Esempio struttura badge:
- `Bozza`
- `Inviata`
- `In revisione`
- `Approvata`
- `Rifiutata`
- `Convertita`

Regola: mai mostrare azioni non consentite nello stato corrente.

## Regole form e validazione

1. Validazione lato client + lato server.
2. Errori vicino al campo, non solo in cima pagina.
3. Riepilogo errori in alto per form lunghi.
4. Form multi-sezione con progressivo chiaro.
5. Salvataggio bozza disponibile quando il processo e` lungo.

## Regole tabelle e liste

- Filtri in alto, sempre nello stesso ordine.
- Ricerca testuale sempre disponibile dove utile.
- Colonne principali coerenti per modulo.
- Azioni riga in menu contestuale o colonna dedicata.
- Export (CSV/Excel/PDF) in posizione standard.

## Messaggistica e microcopy

- Tono chiaro e operativo.
- Messaggi di successo orientati al risultato.
- Messaggi di errore con causa + azione consigliata.
- Nessun testo tecnico non necessario all'utente finale.

Esempio:
- no: "Errore 500 in conversione payload"
- si`: "Impossibile completare la conversione in contratto. Riprova o contatta l'amministratore."

## Accessibilita` minima

- Contrasto testo/sfondo adeguato.
- Focus visibile su elementi interattivi.
- Etichette sempre associate agli input.
- Navigazione tastiera per form e azioni principali.

## Mobile/PWA (dipendente)

- Priorita`: documenti, profilo, presenze, ferie/permessi, richieste.
- Bottom navigation con massimo 4-5 voci.
- Azioni critiche (check-in/check-out) sempre raggiungibili in 1 tap.
- Stato geolocalizzazione sempre esplicito (permesso attivo/non attivo).

## Regole per refactor UI

Prima di modificare:
1. identificare pattern corrente;
2. verificare se esiste componente equivalente in altre pagine;
3. evitare varianti locali inutili.

Dopo modifica:
1. verificare coerenza con linee guida;
2. testare ruolo per ruolo;
3. aggiornare questa guida se nasce un nuovo pattern stabile.
