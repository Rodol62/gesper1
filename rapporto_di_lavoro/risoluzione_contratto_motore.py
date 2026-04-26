"""
Risolve contratto attivo e ParametroCCNLTurismo coerenti con anagrafica/contratto,
per riallineare il motore busta paga al cedolino (oltre al fallback da RuoloOrganico2026).
"""
import calendar
from datetime import date
from decimal import Decimal
from typing import Optional, Tuple

from django.db.models import Q

from .models import AddendumContrattuale, ParametroCCNLTurismo, RapportoDiLavoro


def rapporto_sottoscritto_attivo_nel_mese(
    *,
    dipendente,
    azienda,
    anno: int,
    mese: int,
) -> Optional[RapportoDiLavoro]:
    """Contratto firmato il cui arco temporale interseca il mese richiesto."""
    ultimo = date(anno, mese, calendar.monthrange(anno, mese)[1])
    primo = date(anno, mese, 1)
    return (
        RapportoDiLavoro.objects.filter(
            azienda=azienda,
            dipendente=dipendente,
            stato='sottoscritto',
            data_inizio_rapporto__lte=ultimo,
        )
        .filter(Q(data_fine_rapporto__isnull=True) | Q(data_fine_rapporto__gte=primo))
        .select_related(
            'tipo_contratto',
            'proposta_origine',
            'proposta_origine__parametro_ccnl',
        )
        .order_by('-data_inizio_rapporto', '-id')
        .first()
    )


def risolvi_parametro_ccnl_per_mese(
    *,
    rapporto: Optional[RapportoDiLavoro],
    data_primo_giorno_mese: date,
    livello_fallback: str,
) -> Tuple[Optional[ParametroCCNLTurismo], str]:
    """
    Restituisce (parametro, fonte) dove fonte ∈
    {'proposta_origine','addendum','tabella_livello','tabella_fallback'}.
    """
    livello = (livello_fallback or '').strip()
    if rapporto:
        proposta = getattr(rapporto, 'proposta_origine', None)
        if proposta is not None and getattr(proposta, 'parametro_ccnl_id', None):
            pc = proposta.parametro_ccnl
            if pc is not None:
                return pc, 'proposta_origine'
        ultimo = date(
            data_primo_giorno_mese.year,
            data_primo_giorno_mese.month,
            calendar.monthrange(data_primo_giorno_mese.year, data_primo_giorno_mese.month)[1],
        )
        add = (
            AddendumContrattuale.objects.filter(
                rapporto=rapporto,
                data_decorrenza__lte=ultimo,
                parametro_ccnl__isnull=False,
            )
            .select_related('parametro_ccnl')
            .order_by('-data_decorrenza', '-id')
            .first()
        )
        if add is not None and add.parametro_ccnl_id:
            return add.parametro_ccnl, 'addendum'
        livello = (rapporto.livello_ccnl or livello or '').strip()

    if not livello:
        return None, 'tabella_fallback'

    pc = (
        ParametroCCNLTurismo.objects.filter(
            attivo=True,
            livello=livello,
            decorrenza_validita_da__lte=data_primo_giorno_mese,
        )
        .order_by('-decorrenza_validita_da')
        .first()
    )
    if pc:
        return pc, 'tabella_livello'
    return None, 'tabella_fallback'


def superminimo_da_rapporto_o_ruolo(*, rapporto: RapportoDiLavoro | None, ruolo_superminimo) -> Decimal:
    if rapporto is not None:
        try:
            return Decimal(str(rapporto.superminimo_mensile or 0)).quantize(Decimal('0.01'))
        except Exception:
            pass
    try:
        return Decimal(str(ruolo_superminimo or 0)).quantize(Decimal('0.01'))
    except Exception:
        return Decimal('0.00')


def divisore_str_da_parametro_get(raw: Optional[str]) -> str:
    """Allineato alla simulazione 2026: 172 | 26 | 173.33 (default)."""
    s = (raw or '173.33').strip()
    if s == '172':
        return '172'
    if s == '26':
        return '26'
    return '173.33'


def kwargs_percorso_fiscale_sim(percorso: Optional[str]) -> dict:
    """
    Allinea il motore al trattamento fiscale tipico cedolino (L.207/2024 come detrazione IRPEF).

    GET ``percorso_fiscale``:
    - ``standard`` (default): calcolo «integrato» netto (TI+L207 come crediti a netto).
    - ``ced_l207_det``: modalità cedolino con L207 in detrazione IRPEF (come ``calcola_busta_paga_mese``).
    """
    p = (percorso or 'standard').strip().lower()
    if p in ('ced_l207_det', 'cedolino', 'ts', 'l207_detrazione'):
        return {
            'fiscale_modalita_cedolino': True,
            'l207_come_detrazione_irpef': True,
        }
    return {}
