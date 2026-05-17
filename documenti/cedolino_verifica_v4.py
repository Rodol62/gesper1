"""
Persistenza dell’esito della conciliazione (DB + formule vs PDF) su :class:`CedolinoMotoreV4`.
"""

from __future__ import annotations

from typing import Any

from django.utils import timezone

from documenti.models import CedolinoMotoreV4


def persisti_esito_verifica_da_riga_busta(
    v4_row: CedolinoMotoreV4 | None,
    conc: dict[str, Any],
    extraction_err: str | None,
) -> None:
    """
    Aggiorna i campi ``verifica_*`` dopo aver calcolato ``conciliazione_oggi_vs_cedolino_motore_v4``.

    - Se la lettura PDF fallisce ma esiste già un cedolino v4 in DB → ``senza_report``.
    - Se non è stata eseguita la verifica profonda (``verifica_ricalcolo_eseguita`` False) → nessun update.
    """
    if v4_row is None:
        return
    now = timezone.now()
    st = CedolinoMotoreV4.VerificaStato
    if extraction_err:
        CedolinoMotoreV4.objects.filter(pk=v4_row.pk).update(
            verifica_stato=st.SENZA_REPORT,
            verifica_il=now,
            verifica_n_diff=None,
            verifica_n_checks_formula_ko=None,
            verifica_n_checks_formula_ko_bloccanti=None,
        )
        return
    if not conc.get("verifica_ricalcolo_eseguita"):
        return
    if conc.get("ricostruzione_error"):
        vs = st.ERRORE
    else:
        stato = conc.get("stato") or ""
        if stato == "ok":
            vs = st.OK
        elif stato == "differenze":
            vs = st.DIVERGENZE
        elif stato == "senza_report":
            vs = st.SENZA_REPORT
        else:
            vs = st.PENDING
    n_ko_tot = int(conc.get("n_checks_formula_ko") or 0)
    raw_bloc = conc.get("n_checks_formula_ko_bloccanti")
    n_ko_bloc = int(raw_bloc) if raw_bloc is not None else n_ko_tot
    CedolinoMotoreV4.objects.filter(pk=v4_row.pk).update(
        verifica_stato=vs,
        verifica_il=now,
        verifica_n_diff=int(conc.get("n_diff") or 0),
        verifica_n_checks_formula_ko=n_ko_tot,
        verifica_n_checks_formula_ko_bloccanti=n_ko_bloc,
    )
