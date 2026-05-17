# Presenze — Fase A: specifica di dominio (template)

**Scopo:** definire, prima di ulteriore codice, glossario, causali e output mensili attesi così che HR, consulente e sviluppo condividano le stesse regole.

**Stato documento:** bozza da compilare  
**Ultima revisione:** _(data)_  
**Azienda / CCNL di riferimento:** _(es. FIPE / altro)_  
**Partecipanti al workshop:** _(ruoli / nomi)_

---

## 1. Glossario operativo

_Compilare ogni termine con una definizione che risponda a “come lo calcoliamo nel sistema” e “come lo vede il consulente”._

| Termine | Definizione operativa | Note / riferimento CCNL |
| ------- | ---------------------- | ------------------------- |
| Giornata lavorativa “normale” | | |
| Straordinario | | |
| Notturno | _(es. fascia dopo le 22:00)_ | |
| Festivo / domenica (ai fini maggiorazione) | | |
| Ore ordinarie (mensili) | | |
| Ferie (giorni / ore) | | |
| Permesso / ROL | | |
| Riposo compensativo | | |
| Malattia / assenza non retribuita | | |
| Assenza ingiustificata | | |
| Monte ferie / maturazione / godimento / residuo | | |
| _(altri)_ | | |

---

## 2. Tabella causali → comportamento

_Per ogni **causale** usata in anagrafica presenze (o da introdurre), indicare l’effetto sul flusso paghe e sui monti._

**Legenda colonne (suggerite):**

- **Codice**: sigla nel gestionale (allineare a `Presenza.CAUSALE_CHOICES` dove possibile).
- **Conta ore lavorate**: Sì/No — le ore timbrate entrano nel “lavorato” del giorno/mese?
- **Computo mensile ore contratto**: Sì/No — conta per il totale ore lavorate del mese ai fini confronto con teorico?
- **Scala ferie**: Sì/No/Parziale — se sì, in giorni o ore?
- **Scala ROL / permessi**: idem.
- **Straordinario**: come classificato (diurno / notturno / festivo / ecc.) se applicabile.
- **Retribuzione**: lordo / trattenuta / neutro (solo assenza protetta).
- **Note**: eccezioni (es. mezza giornata ferie).

| Codice | Nome | Conta ore lav. | Computo mensile | Scala ferie | Scala ROL/perm. | Straord. | Retribuzione | Note |
| ------ | ---- | -------------- | --------------- | ----------- | ---------------- | -------- | ------------ | ---- |
| P | Presenza | | | | | | | |
| ST | Straordinario | | | | | | | |
| F | Ferie | | | | | | | |
| PE | Permesso | | | | | | | |
| M | Malattia | | | | | | | |
| A | Assenza ingiustificata | | | | | | | |
| FE | Festivo lavorato | | | | | | | |
| R | Riposo | | | | | | | |
| _(altre)_ | | | | | | | | |

_Righe aggiuntive se servono causali nuove (es. “Ritardo”, “Congedo parentale”, …)._

---

## 3. Output mensili attesi dal motore

_Elenco delle **voci numeriche** che il modulo deve produrre **per dipendente e mese** (per export, cedolino, controllo). Spuntare e integrare._

- [ ] Ore ordinarie (o equivalente da contratto)
- [ ] Straordinario diurno (ore)
- [ ] Straordinario notturno (ore)
- [ ] Straordinario festivo / domenicale (distinte se richiesto da CCNL)
- [ ] Ore lavorate in domenica (entro soglia / maggiorazione)
- [ ] Ore lavorate in festivi (non domenica)
- [ ] Ferie godute (giorni o ore)
- [ ] Permessi / ROL goduti (ore)
- [ ] Giorni malattia (se rilevanti al netto)
- [ ] Giorni assenza ingiustificata
- [ ] _(altro: _____________)_

**Formato verso consulente:** _(file CSV, copia-incolla, integrazione con …)_

---

## 4. Regole trasversali (checklist)

_Compilare Sì/No e dove necessario una riga di chiarimento._

| Regola | Sì / No / N.A. | Chiarimento |
| ------ | -------------- | ----------- |
| Soglia giornaliera ore da contratto individuale (part-time, CCNL, piano orari) | | |
| Cambio orario / contratto a metà mese: gestione per periodo | | |
| Festività: solo nazionali + lista aziendale | | |
| Domenica che coincide con festivo: regola prevalente | | |
| Mezza giornata ferie / permesso: come si scala il monte | | |
| Ritardi: modello (non conteggiati / trattenuti / causale dedicata) | | |

---

## 5. Saldi e migrazione

| Voce | Serve saldo iniziale da busta paga? | Chi inserisce | Frequenza controllo con consulente |
| ---- | ----------------------------------- | ------------- | ---------------------------------- |
| Ferie residue | | | |
| ROL / permessi (ore) | | | |
| Riposi compensativi | | | |

**Data taglio migrazione (dipendenti già in ruolo):** _(es. 31/12/anno N)_

---

## 6. Chiusura mese e responsabilità

| Stato mese | Cosa è bloccato | Chi autorizza il passaggio di stato |
| ---------- | --------------- | ----------------------------------- |
| Bozza | | |
| Confermato / chiuso | | |

---

## 7. Prossimi passi (dopo approvazione Fase A)

1. **Fase B** — schema dati: vedi [PRESENZE_MOTORE_FASE_B.md](./PRESENZE_MOTORE_FASE_B.md) (ledger monti, movimenti, riconciliazione, checklist migrations).
2. **Fase C** — casi di test numerici (10–30 scenari) firmati da HR: vedi [PRESENZE_MOTORE_FASE_C.md](./PRESENZE_MOTORE_FASE_C.md).
3. Allineamento codice esistente (`Presenza`, `RiepilogoMensilePresenze`, `aggrega_presenze_per_motore`) alla specifica.

---

## 8. Approvazione

| Ruolo | Nome | Firma / Data |
| ----- | ---- | ------------ |
| HR / Datore | | |
| Consulente paghe | | |
| Referente IT / prodotto | | |

---

## 9. Mappa 1:1 — codice attuale (GESPER) vs voce di dominio

_Allineamento tra modelli Django in `presenze` e le voci della sez. 3. Serve per vedere **cosa è già coperto** e **cosa manca** senza leggere il codice._

### 9.1 Input giornaliero — `Presenza` (`presenze/models.py`)

| Campo / concetto | Uso attuale | Voce sez. 3 / dominio | Gap o attenzione |
| ---------------- | ----------- | --------------------- | ---------------- |
| `dipendente`, `data`, `azienda` | Chi, quando | Tutte | — |
| `causale` | Tipo giornata (P, ST, F, PE, M, …) | Distribuzione su ore/assenze | Tabella sez. 2 deve decidere ogni effetto |
| `ora_entrata` … `ora_uscita` (×3 turni) | Ore lavorate effettive | Ore ordinarie / straord (derivate) | Un solo record/giorno: mezze giornate miste sono limitate |
| `ore_straordinario`, `tipo_straordinario` | Solo se causale ST | Bucket straordinario | Se ST senza tipo, il motore deduce da orario |
| `note` | Libero | — | Non strutturato per audit |

### 9.2 Output mensile aggregato — `RiepilogoMensilePresenze`

Popolato da `aggrega_presenze_per_motore()` in `presenze/utils.py`.

| Campo modello | Significato nel codice | Voce checklist §3 | Stato |
| ------------- | ---------------------- | ----------------- | ----- |
| `ore_ordinarie` | Ore “in fascia” su giorni feriali (non ST tipizzato) | Ore ordinarie | Coperto |
| `ore_domenicali` | Ore fino a soglia in domenica | Ore in domenica (magg.) | Coperto |
| `ore_festivi` | Ore fino a soglia in festivo (non dom.) | Ore in festivi | Coperto |
| `ore_straord_diurno` | Eccedenza / ST diurno feriale | Straord. diurno | Coperto |
| `ore_straord_notturno` | Fascia notturna / ST notturno | Straord. notturno | Coperto |
| `ore_straord_festivo` | Eccedenza festivo non domenica | Straord. festivo | Coperto |
| `ore_straord_domenica` | Eccedenza in domenica | Straord. domenicale | Coperto |
| `ore_straord_nott_fest` | Notturno in contesto festivo | Straord. nott. fest. | Coperto |
| `giorni_ferie_godute` | Da causale F (gg o frazione) | Ferie godute | Coperto (come decimali) |
| `ore_permessi_goduti` | Da causale PE | ROL / permessi | Coperto (ore) |
| `giorni_malattia` | Conteggio giorni causale M | Malattia | Coperto (intero) |
| `giorni_assenza_ingiust` | Causale A | Ass. ingiustificata | Coperto |
| `giorni_cig` | Causale CIG | CIG | Coperto |
| `stato` | bozza → elaborata | Chiusura mese §6 | Parziale (workflow HR) |

### 9.3 Cosa **non** è ancora un modello di primo livello

| Esigenza di dominio | Nel codice oggi | Nota |
| ------------------- | --------------- | ---- |
| Monte ferie: maturato / residuo / anno competenza | Non presente come ledger | Solo goduto nel riepilogo |
| ROL: monte ore residuo | Non presente | Solo `ore_permessi_goduti` nel mese |
| Riposi compensativi (banca ore) | Non presente | — |
| Saldo iniziale da ultima busta paga | Non presente | Serve tabella import / riconciliazione |
| Contratto con periodi di validità multipli nello stesso mese | `RapportoDiLavoro` + logica in `utils` | Da esplicitare in Fase B |
| Ritardi / causali HR aggiuntive | Non in `CAUSALE_CHOICES` | Estendere enum + tabella sez. 2 |

_Questa tabella va aggiornata quando aggiungete modelli (es. `SaldoFerie`, `MovimentoMonte`)._

---

## 10. Checklist “pronti per Fase B”

Spuntare quando la Fase A è sufficientemente compilata:

- [ ] Sez. 1 glossario: nessun termine critico lasciato vuoto
- [ ] Sez. 2: ogni causale usata ha riga completa (effetto su monti e paghe)
- [ ] Sez. 3: output mensili concordati con il consulente (anche solo elenco colonne export)
- [ ] Sez. 4–6: regole e chiusura mese definite
- [ ] Sez. 9: letta da IT — gap accettati o da pianificare

**Data avvio Fase B (schema dati):** _______________
