# Copilot / GitHub — GESPER (EN supplement)

Authoritative project rules (Italian, Cursor + default AI context): **`.cursorrules`** at repo root. Functional deep-dive: **`DOCUMENTAZIONE_UNICA_GESPER.md`**.

## Quick EN hooks
- Monolith Django multi-tenant HR; tenant: `accounts.tenant.get_azienda_operativa(user, session)`.
- Pay engine entry: `rapporto_di_lavoro.utils_motore_paga.calcola_busta_paga_mese` only; attendance bridge `presenze.utils.aggrega_presenze_per_motore` + `as_motore_kwargs()`.
- Never edit applied migrations; avoid changing `settings_production*.py` without explicit approval.
- Pre-commit smoke: `python3 -m py_compile <files>` and `python3 manage.py check`. Tests (uneven): `python3 manage.py test anagrafiche rapporto_di_lavoro`.

## Deploy (pick one)
- `bash deploy_gesper.sh` — scp-style.
- `bash scripts/deploy_prod.sh` — rsync + remote DB backup + migrate + collectstatic + restart.

## Pay engine smoke (after engine edits)
```python
from datetime import date
from rapporto_di_lavoro.models import CCNL, ParametroCCNLTurismo
from rapporto_di_lavoro.utils_motore_paga import calcola_busta_paga_mese
ccnl = CCNL.objects.get(sigla='FIPE')
data_rif = date(2026, 4, 1)
for cp in ParametroCCNLTurismo.objects.filter(decorrenza_validita_da__lte=data_rif):
    r = calcola_busta_paga_mese(
        parametro_ccnl=cp, data_riferimento=data_rif, ccnl_obj=ccnl,
        divisore_str='173', auto_ore_domenicali_da_calendario=True,
    )
    # inspect r['lordo_mensile'], r['netto'] — avoid logging sensitive values in prod
```
