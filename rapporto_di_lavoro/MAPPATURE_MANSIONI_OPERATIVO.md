# Procedura Operativa HR/Admin — Mappature Mansioni

Questa guida serve per mantenere allineate le mappature tra `Mansione` e `Livello CCNL` nel flusso di creazione proposta/contratto.

## Quando usarla

- dopo aggiornamenti dei parametri CCNL;
- dopo inserimento di nuove mansioni operative;
- quando il form proposta segnala incoerenze mansione/livello.

## Sequenza operativa (5 comandi)

Eseguire dalla root progetto:

```bash
python3 manage.py migrate --skip-checks
python3 manage.py sync_mansioni_livelli_ccnl --dry-run --fallback-all-mansioni-per-livello
python3 manage.py sync_mansioni_livelli_ccnl --fallback-all-mansioni-per-livello
python3 manage.py normalizza_mappature_mansioni --solo-fallback
python3 manage.py audit_mappature_mansioni
```

## Cosa aspettarsi dal report audit

- `Copertura minima OK`: tutte le mansioni/livelli hanno almeno una mappatura attiva.
- `Fallback attivi residui: 0`: stato consigliato.
- nessun livello senza mansioni mappate.

Se il report mostra anomalie:

- correggere da Admin in `Mappature Mansione-Livello CCNL`;
- poi rieseguire `python3 manage.py audit_mappature_mansioni`.

## Verifiche UI (3 controlli rapidi)

1. Aprire `Proposte > Nuova proposta`.
2. Selezionare `Livello CCNL`: il campo `Mansione` deve mostrare opzioni coerenti.
3. Selezionare `Mansione`: il pannello coerenza non deve mostrare warning.

## Regole di gestione in Admin

- usare `fonte=custom_admin` solo per eccezioni reali;
- usare `priorita` alta per override specifici (es. `90`);
- valorizzare `note` per motivare la personalizzazione;
- usare `valida_da`/`valida_a` per eccezioni temporanee.

## Comando di audit periodico consigliato

```bash
python3 manage.py audit_mappature_mansioni
```

Frequenza suggerita: almeno settimanale o dopo ogni variazione su CCNL/mansioni.
