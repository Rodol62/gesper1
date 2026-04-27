# Checklist Pre-Deploy ISTAT (2 minuti)

Usare questa checklist ogni volta che si aggiornano i dataset territoriali (Italia + estero).

## 1) Aggiornamento dataset

- [ ] Eseguire: `python3 manage.py aggiorna_dataset_istat_territorio`
- [ ] Verificare output senza errori (download comuni + stati esteri)

## 2) Migrazioni e check base

- [ ] Eseguire: `python3 manage.py migrate`
- [ ] Eseguire: `python3 manage.py check`

## 3) Smoke test API geografiche

- [ ] `api_regioni_italia` risponde `200`
- [ ] `api_province_italia?regione=...` risponde `200` e restituisce elementi
- [ ] `api_comuni_italia?regione=...&provincia=...` risponde `200` e restituisce elementi

## 4) Smoke test UI candidato

- [ ] Caso Italia: regione -> provincia -> comune funzionano
- [ ] Caso Estero: campi estero visibili e select italiani disabilitati
- [ ] Validazione Estero: città estera obbligatoria
- [ ] CAP estero alfanumerico accettato (es. `SW1A1AA`)

## 5) Smoke test UI dipendente

- [ ] Caso Italia: nascita/residenza/domicilio a cascata funzionano
- [ ] Caso Estero: campi città estera funzionano su nascita/residenza/domicilio
- [ ] Salvataggio dipendente ok in entrambi i casi

## 6) Verifica pannelli HR/Admin

- [ ] In dettaglio candidato è visibile la fonte dati ISTAT
- [ ] Diagnostica anagrafica coerente con casi Italia/Estero

## Esito finale

- [ ] **GO** (tutti pass)
- [ ] **NO-GO** (almeno un fail; correggere prima del deploy)

