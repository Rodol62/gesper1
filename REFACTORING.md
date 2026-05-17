# Rifattorizzazione motore retributivo GESPER

## Obiettivo
Rifattorizzare il motore di calcolo retributivo per renderlo modulare, multi-CCNL e conforme alle normative 2026.
Il progetto deve adottare un'architettura a tre strati separando i calcoli contrattuali, i calcoli statali e l'orchestrazione.

## Architettura proposta

1. **Strato contrattuale (MotoreCCNL)**
   - Calcoli legati al CCNL specifico.
   - Paga base, scatti, maggiorazioni, ratei, TFR e competenze di fine rapporto.
   - Gestione di eventi contrattuali come promozioni, aspettative, licenziamenti e dimissioni.
   - Parametri letti dal database: tabelle dei livelli, scatti annuali, maggiorazioni, ratei, regole normative.

2. **Strato statale (OpenFiscaAdapter)**
   - Calcoli fiscali e contributivi di competenza statale.
   - INPS, IRPEF, INAIL, addizionali regionali e comunali.
   - Uso di `openfisca-italy` per la logica normativa statale, con fallback manuale sui parametri DB.

3. **Strato orchestratore (MotoreRetributivo)**
   - Coordina MotoreCCNL e OpenFiscaAdapter.
   - Restituisce il cedolino completo: lordo, contributi, IRPEF, netto e costo azienda.
   - Sostituisce le vecchie funzioni duplicate sparse nel progetto.

## Modelli coinvolti

- `CCNL` / `ParametroCCNLTurismo`
- `LivelloCCNL`
- `EventoContrattuale`
- `Transizione`
- `ParametroContributi`
- `ParametroOrario`
- `ParametroMaggiorazione`
- `ParametroScattiAnnuali`
- `ParametroRatei`
- `RegolaNormativaCCNL`

## Normativa 2026 da riflettere nei calcoli

- Scaglioni IRPEF: 23%, 33%, 43%.
- Massimale contributivo INPS: 122.295 €.
- Detrazioni lavoro dipendente aggiornate secondo la normativa vigente.
- I calcoli statali devono riflettere correttamente le aliquote e i massimali 2026, ma la configurazione dei parametri deve rimanere nel database.

## Piano delle fasi

- [x] Fase 0 – Documentazione: `REFACTORING.md`
- [x] Fase 1 – Modelli CCNL
- [x] Fase 2 – Motore CCNL
- [x] Fase 3 – Adapter OpenFisca
- [x] Fase 4 – Motore unico orchestratore
- [x] Fase 5 – Test e pulizia

## Note operative

- Tutti i calcoli monetari devono usare `Decimal` con due cifre decimali.
- Gli aggiornamenti normativi devono essere fatti tramite parametri DB e non tramite logica hardcoded nel codice.
- I test dovranno coprire sia i calcoli mensili sia gli eventi di fine rapporto.
