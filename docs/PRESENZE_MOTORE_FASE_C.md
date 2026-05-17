# Presenze — Fase C: casi di test numerici (monti / riepilogo)

**Prerequisiti:** [PRESENZE_MOTORE_FASE_A.md](./PRESENZE_MOTORE_FASE_A.md) (causali e flussi) e [PRESENZE_MOTORE_FASE_B.md](./PRESENZE_MOTORE_FASE_B.md) (ledger implementato).

**Scopo:** definire scenari **riproducibili** per validare, mese per mese, la catena:

`Presenza` → `aggrega_presenze_per_motore` → `RiepilogoMensilePresenze` → `applica_movimenti_da_riepilogo` → `MovimentoMonte` / saldo.

HR può estendere l’elenco (obiettivo indicativo in Fase A: 10–30 scenari); qui sono documentati **12 scenari base** più una matrice di verifica.

---

## 1. Convenzioni (allineate al codice attuale)

| Elemento | Regola |
| -------- | ------ |
| **Saldo corrente** | `saldo_iniziale` + Σ `MovimentoMonte.quantita` sul relativo `SaldoMonteDipendente` (anno competenza = anno del riepilogo). |
| **Godimento in monte** | `quantita = −(goduto)` con goduto ≥ 0: ferie in **giorni**, ROL in **ore**. |
| **Chiavi idempotenza** | `rie-{AAAA}-{MM}-ferie`, `rie-{AAAA}-{MM}-rol` (es. `rie-2026-04-ferie`). |
| **Data competenza movimento** | Ultimo giorno del mese del riepilogo. |
| **Chiusura monti** | `applica_movimenti_da_riepilogo` con riepilogo in stato `approvata` o `elaborata` (o `solo_se_approvato=False` in test). |
| **Ferie da presenze** | Causale `F`: se `ore_lavorate()==0` → +1 giorno; altrimenti → `+ ore_lavorate / ore_std` (ore_std da contratto/pianificazione). |
| **ROL da presenze** | Causale `PE`: somma `ore_lavorate()` nel mese. |
| **Riposi compensativi** | Nessun movimento automatico da riepilogo (campo dedicato assente su `RiepilogoMensilePresenze`). |

---

## 2. Matrice rapida (cosa controllare)

| Controllo | Come |
| --------- | ---- |
| Riepilogo | Campi `giorni_ferie_godute`, `ore_permessi_goduti` coerenti con le presenze del mese. |
| Movimenti | Per ogni tipo: una riga con `tipo_movimento=GODIMENTO`, `origine=RIEPILOGO_MENSILE`, `idempotency_key` corretta. |
| Idempotenza | Seconda esecuzione di `applica_movimenti_da_riepilogo` **senza** duplicare righe; solo aggiornamento quantità se il riepilogo cambia. |
| Saldo | Dopo movimenti: `saldo_iniziale + somma(quantita)` uguale al valore atteso dello scenario. |

---

## 3. Scenari numerici (12)

*Ipotesi comune dove serve uno standard giornaliero: **ore_std = 8** (verificare sul dipendente/azienda di test; se diverso, ricalcolare le attese di ferie parziali).*

| ID | Descrizione sintetica | Presenze del mese (estratto) | `giorni_ferie_godute` | `ore_permessi_goduti` | Movimenti attesi (dopo applica) | Saldo netto se `saldo_iniziale` ferie = 10 gg, ROL = 40 h |
| -- | --------------------- | ---------------------------- | --------------------- | --------------------- | ------------------------------- | --------------------------------------------------------- |
| **C1** | Nessuna F né PE | — | 0 | 0 | Nessun consumo: righe con chiavi mensili assenti o quantità 0 (sync elimina se prima presenti) | Ferie: 10 + 0 = **10**; ROL: 40 + 0 = **40** |
| **C2** | 2 giornate F senza orari | 2× `F` con ore 0 | 2.00 | 0 | Ferie: `quantita = -2`, `unita=GG` | Ferie: **8**; ROL: **40** |
| **C3** | 1 giorno F mezza giornata | 1× `F` con 4 h lavorate, std 8 h | 0.50 | 0 | Ferie: `quantita = -0.5` | Ferie: **9.5**; ROL: **40** |
| **C4** | Solo permessi ROL | 3× `PE` con 2 h + 3 h + 1 h | 0 | 6.00 | ROL: `quantita = -6`, `unita=ORE` | Ferie: **10**; ROL: **34** |
| **C5** | F + PE combinati | C2 + C4 nello stesso mese | 2.00 | 6.00 | Entrambi i movimenti | Ferie: **8**; ROL: **34** |
| **C6** | Idempotenza | Stesso riepilogo, `applica_movimenti_da_riepilogo` eseguito 2 volte | invariato | invariato | Stesso numero di righe (2 tipi), quantità invariate | Invariato |
| **C7** | Ricalcolo aggrega | Dopo prima applica, si correggono presenze e si rilancia `aggrega_presenze_per_motore` poi di nuovo `applica` | aggiornato | aggiornato | `quantita` movimenti **aggiornate**, non duplicate | Coerente col nuovo riepilogo |
| **C8** | Due mesi consecutivi | Stesso dipendente, marzo e aprile con consumi diversi | per mese | per mese | Chiavi `rie-2026-03-*` e `rie-2026-04-*` distinte; saldi stesso `anno_competenza` se anno uguale | Σ movimenti su entrambi i mesi nel saldo 2026 |
| **C9** | Solo malattia / assenza | `M`, `A`, `CIG` | 0 | 0 | Nessun movimento monte da queste causali | Saldi solo da `saldo_iniziale` + eventuali altri movimenti |
| **C10** | Zero ore PE | `PE` con 0 ore lavorate (se configurazione ammessa) | 0 | 0 | ROL: nessun consumo o 0 | Verificare comportamento `aggrega` su edge case |
| **C11** | Blocco B4 | Riepilogo `approvata` / `elaborata` | — | — | — | Modifica `Presenza` sul mese **rifiutata** dal server; calendario in sola lettura |
| **C12** | Riposi / monte senza flusso riepilogo | Presenze che generano altri conteggi | — | — | **Nessun** `MovimentoMonte` `RIPOSI_COMP` da chiusura mensile finché il riepilogo non espone un aggregato | Eventuale gestione manuale / fase evolutiva |

---

## 4. Procedura di verifica manuale (checklist)

1. Creare o selezionare dipendente e azienda di test; impostare **saldo iniziale** dalla pagina **Monti** (`/presenze/monti/`) se servono verifiche di saldo (C1, C2, …).
2. Inserire le presenze del mese nel calendario.
3. Eseguire **Aggrega** (motore) → controllare `RiepilogoMensilePresenze`.
4. Portare il riepilogo in **approvata** (o chiamare `applica_movimenti_da_riepilogo` con `solo_se_approvato=False` in ambiente di test).
5. In admin o DB: verificare `MovimentoMonte` (chiavi, segni, unità).
6. Calcolare saldo a mano e confrontare con la pagina Monti / export CSV.

---

## 5. Test automatici nel repo

Suite Django: `presenze/tests/test_monte_fase_c.py` (scenari aggregazione C2–C4, movimenti ferie/ROL, idempotenza, aggiornamento quantità senza ricalcolo aggrega).

```bash
python manage.py test presenze.tests.test_monte_fase_c
```

L’azienda di test usa `ore_settimanali_standard=40` → `ore_std_giornaliere_contratto` = 8 h (necessario per **C3**).

## 6. Estensioni

È possibile aggiungere casi su `calcola_saldo_corrente`, `RIPOSI_COMP`, o integrazione con busta importata.

---

## 7. Approvazione scenari (HR)

| Ruolo                   | Nome | Firma / Data |
| ----------------------- | ---- | ------------ |
| HR / Datore             |      |              |
| Referente IT / prodotto |      |              |

**Versione documento:** 1.0 — allineata a implementazione B1–B4 in `presenze`.
