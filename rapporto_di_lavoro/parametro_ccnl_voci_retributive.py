"""
Parametri voci retributive (ParametroVoceRetributiva) + ParametroCCNLTurismo.

I valori economici tabellari possono essere mantenuti nella tabella dedicata; il motore
``calcola_busta_paga_mese`` applica quelle voci al parametro CCNL prima del calcolo.

Non si importa l’indennità di funzione da PVR: il minimo tabellare (e contingenza/scatto/EL.DIS.)
restano l’uso previsto; ``indennita_mensile`` sul record CCNL Turismo resta l’unica fonte per quella voce.
"""
from __future__ import annotations

from decimal import Decimal
from typing import Any, Dict, Optional

from django.utils import timezone


def _dec(value) -> Decimal:
    try:
        return Decimal(str(value or 0))
    except Exception:
        return Decimal('0')


def carica_voci_retributive_da_tabella(parametro, anno=None) -> Dict[str, Decimal]:
    """Importi voci retributive da ``ParametroVoceRetributiva`` per livello/ccnl/versione/sezione."""
    if not parametro:
        return {}

    from .models import ParametroVoceRetributiva

    livello = str(getattr(parametro, 'livello', '') or '').strip()
    if not livello:
        return {}

    try:
        anno_rif = int(
            anno or (
                parametro.decorrenza_validita_da.year
                if getattr(parametro, 'decorrenza_validita_da', None)
                else timezone.localdate().year
            )
        )
    except Exception:
        anno_rif = timezone.localdate().year

    qs = ParametroVoceRetributiva.objects.filter(
        ccnl=getattr(parametro, 'ccnl', ''),
        versione=getattr(parametro, 'versione', ''),
        sezione=getattr(parametro, 'sezione', ''),
        livello=livello,
        attivo=True,
        anno=anno_rif,
    ).select_related('voce')
    if not qs.exists():
        qs = (
            ParametroVoceRetributiva.objects.filter(
                ccnl=getattr(parametro, 'ccnl', ''),
                sezione=getattr(parametro, 'sezione', ''),
                livello=livello,
                attivo=True,
            )
            .select_related('voce')
            .order_by('-anno')
        )

    voci: Dict[str, Decimal] = {
        'minimo_tabellare': Decimal('0.00'),
        'contingenza': Decimal('0.00'),
        'scatto_anzianita': Decimal('0.00'),
        'superminimo': Decimal('0.00'),
        'el_dis_san': Decimal('0.00000'),
        'el_dis_bil': Decimal('0.00000'),
    }

    # Nessun IND_FUNZIONE / indennità CCNL da PVR: solo minimo tabellare e voci accessorie tabellari.
    map_codice = {
        'PAGA_BASE': 'minimo_tabellare',
        'MINIMO_TABELLARE': 'minimo_tabellare',
        'CONTINGENZA': 'contingenza',
        'SCATTO_ANZ': 'scatto_anzianita',
        'SCATTO_ANZIANITA': 'scatto_anzianita',
        'SUPERMINIMO': 'superminimo',
        'EL_DIS_SAN': 'el_dis_san',
        'EL_DIS_BIL': 'el_dis_bil',
    }

    for item in qs:
        codice = str(getattr(getattr(item, 'voce', None), 'codice', '') or '').upper()
        chiave = map_codice.get(codice)
        if not chiave:
            continue
        if chiave in ('el_dis_san', 'el_dis_bil'):
            voci[chiave] = _dec(item.importo_orario).quantize(Decimal('0.00001'))
        else:
            voci[chiave] = _dec(item.importo_mensile).quantize(Decimal('0.01'))

    return voci


class ParametroMotoreVociProxy:
    """Delega a ``ParametroCCNLTurismo`` sovrascrivendo solo i campi presenti in ``overrides``."""

    __slots__ = ('_base', '_over')

    def __init__(self, base: Any, overrides: dict):
        self._base = base
        self._over = overrides

    def __getattr__(self, name: str):
        if name in self._over:
            return self._over[name]
        return getattr(self._base, name)


def parametro_ccnl_motore_con_voci_retributive(parametro, anno: int):
    """
    Se esistono righe ``ParametroVoceRetributiva`` con importi > 0 per il livello del parametro,
    ritorna un proxy che il motore usa al posto del record CCNL grezzo.

    Sovrascrive da PVR: minimo tabellare, contingenza, scatto tabellare, EL.DIS. — non l’indennità di funzione
    (``indennita_mensile`` resta sul ``ParametroCCNLTurismo``).
    """
    from .models import ParametroCCNLTurismo

    if not isinstance(parametro, ParametroCCNLTurismo):
        return parametro

    voci = carica_voci_retributive_da_tabella(parametro, anno)
    over: dict[str, Any] = {}

    if voci['minimo_tabellare'] > 0:
        over['minimo_tabellare'] = voci['minimo_tabellare']
    if voci['contingenza'] > 0:
        over['contingenza_mensile'] = voci['contingenza']
    if voci['scatto_anzianita'] > 0:
        over['scatto_importo'] = voci['scatto_anzianita']
    if voci['el_dis_san'] > 0:
        over['elemento_distinto_sanita'] = voci['el_dis_san']
    if voci['el_dis_bil'] > 0:
        over['elemento_distinto_bilateralita'] = voci['el_dis_bil']

    if not over:
        return parametro
    return ParametroMotoreVociProxy(parametro, over)


def risolvi_ccnl_modello_da_parametro(parametro_ccnl) -> Optional[Any]:
    """Riga ``CCNL`` coerente col testo ``ParametroCCNLTurismo.ccnl`` (per scatti / flag FIPE)."""
    if not parametro_ccnl:
        return None
    from .models import CCNL

    s = (getattr(parametro_ccnl, 'ccnl', '') or '').upper()
    if 'FIPE' in s or 'PUBBLICI ESERCIZI' in s:
        return CCNL.objects.filter(sigla='FIPE').first()
    if 'CONFCOMMERCIO' in s:
        return CCNL.objects.filter(sigla='COMMERCIO').first()
    if 'INDUSTRIA' in s or 'METALMECC' in s:
        return CCNL.objects.filter(sigla='INDUSTRIA').first()
    if 'EDILIZ' in s:
        return CCNL.objects.filter(sigla='EDILIZIA').first()
    if 'TRASPORT' in s:
        return CCNL.objects.filter(sigla='TRASPORTI').first()
    if 'AGR' in s and 'COLT' in s:
        return CCNL.objects.filter(sigla='AGRICOLTURA').first()
    return None
