"""
Anteprima parametri per il motore «Calcolatore ferie e ROL» da normativa CCNL + rapporto vigente.

Non sostituisce il calcolo in ``presenze.maturazione_griglia_utils`` (stessa fonte dati:
``costruisci_dati_contratto``).
"""
from __future__ import annotations

from datetime import date
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from anagrafiche.models import Azienda, Dipendente

_TIPO_CONTRATTO_CALC_LABEL = {
    'full_time': 'Full time',
    'part_time_orizzontale': 'Part-time orizzontale',
    'part_time_verticale': 'Part-time verticale',
}


def preview_parametri_calcolatore_ferie_rol(
    dipendente: 'Dipendente',
    azienda: 'Azienda',
    data_rif: date | None = None,
) -> dict[str, Any]:
    """
    Restituisce ferie annue (gg), ROL annui (ore), tipo contratto calcolato e id motore.
    Utile in UI proposta/contratto o controlli HR.
    """
    from presenze.maturazione_ferie_rol import MOTORE_CALCOLATORE_FERIE_ROL_ID
    from presenze.maturazione_griglia_utils import costruisci_dati_contratto

    if data_rif is None:
        data_rif = date.today()
    dati = costruisci_dati_contratto(dipendente, azienda, data_rif)
    tc = dati.tipo_contratto
    tc_val = tc.value if tc else None
    return {
        'motore_id': MOTORE_CALCOLATORE_FERIE_ROL_ID,
        'motore_nome': 'Calcolatore ferie e ROL',
        'data_rif': data_rif.isoformat(),
        'data_rif_it': data_rif.strftime('%d/%m/%Y'),
        'ferie_annue_giorni': dati.ferie_annue,
        'rol_annui_ore': dati.rol_annui,
        'tipo_contratto_calc': tc_val,
        'tipo_contratto_calc_label': _TIPO_CONTRATTO_CALC_LABEL.get(tc_val or '', '—'),
        'ore_settimanali_pt': dati.ore_settimanali_pt,
        'giorni_lavorabili_ft': dati.giorni_lavorabili_ft,
    }
