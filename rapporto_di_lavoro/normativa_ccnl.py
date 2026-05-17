"""
Risoluzione coerente delle regole normative CCNL e parametri orari/ferie/permessi,
sempre moltiplicati per il coefficiente ore del tipo di contratto del rapporto
(come simulazioni economiche e motore paga).
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

from django.db.models import Q


def coefficiente_ore_rapporto(rdl) -> Decimal:
    """Coefficiente part-time / tipo contratto (1,00 = full-time)."""
    if not rdl:
        return Decimal('1')
    tc = getattr(rdl, 'tipo_contratto', None)
    if not tc:
        return Decimal('1')
    c = getattr(tc, 'coefficiente_ore', None)
    if c is None:
        return Decimal('1')
    try:
        return Decimal(str(c)).quantize(Decimal('0.01'))
    except Exception:
        return Decimal('1')


def trova_regola_normativa_ccnl(parametro_ccnl, data_rif: date | None = None):
    """
    Regola normativa per livello/versione/sezione CCNL, vigente alla data se indicata.
    Allineato alla logica del simulatore economico (``rapporto_di_lavoro.views``).
    """
    if not parametro_ccnl:
        return None
    from .models import RegolaNormativaCCNL

    qs = RegolaNormativaCCNL.objects.filter(
        ccnl=parametro_ccnl.ccnl,
        versione=parametro_ccnl.versione,
        sezione=parametro_ccnl.sezione,
        livello=parametro_ccnl.livello,
        attivo=True,
    )
    if data_rif:
        qs = qs.filter(
            Q(decorrenza_validita_da__isnull=True) | Q(decorrenza_validita_da__lte=data_rif)
        ).filter(
            Q(decorrenza_validita_a__isnull=True) | Q(decorrenza_validita_a__gte=data_rif)
        )
    return qs.order_by('-decorrenza_validita_da').first()


def parametri_normativi_contrattuali(dipendente, azienda, data_rif: date) -> dict:
    """
    Restituisce ore settimanali/mensili/giornaliere, ferie e permessi annui **già prorati**
    al tipo di contratto del rapporto vigente (coefficiente ore).

    Priorità valori «base» (prima del coeff.): ``RegolaNormativaCCNL`` se presente,
    altrimenti ``ParametroCCNLTurismo``, altrimenti dati su ``RapportoDiLavoro`` / default.

    Chiavi: ``coefficiente_ore``, ``ore_settimanali``, ``ore_mensili``, ``ore_giornaliere``,
    ``ferie_annue_giorni``, ``permessi_annui_ore``, ``ha_regola_normativa``, ``ha_parametro_ccnl``.
    """
    from presenze.utils import _rapporto_vigente_per_ore, _trova_parametro_ccnl_per_livello

    rdl = _rapporto_vigente_per_ore(dipendente, azienda, data_rif)
    coeff = coefficiente_ore_rapporto(rdl)

    parametro = None
    if rdl and getattr(rdl, 'proposta_origine', None):
        po = rdl.proposta_origine
        try:
            parametro = po.parametro_ccnl_risolto
        except Exception:
            parametro = getattr(po, 'parametro_ccnl', None)

    if parametro is None:
        livello = None
        if rdl and getattr(rdl, 'livello_ccnl', None):
            livello = (rdl.livello_ccnl or '').strip() or None
        if not livello and getattr(dipendente, 'livello', None):
            livello = (dipendente.livello or '').strip() or None
        parametro = _trova_parametro_ccnl_per_livello(livello or '', data_rif, azienda)

    regola = trova_regola_normativa_ccnl(parametro, data_rif) if parametro else None

    ore_sett_base = Decimal('40')
    ore_mens_base = Decimal('173.33')
    ore_giorn_base = Decimal('8')
    ferie_base = Decimal('26')
    permessi_base = Decimal('72')

    if regola:
        ore_sett_base = Decimal(str(regola.ore_settimanali or 40))
        ore_mens_base = Decimal(str(regola.ore_mensili or 173.33))
        ore_giorn_base = Decimal(str(regola.ore_giornaliere or 8))
        ferie_base = Decimal(str(regola.ferie_annue_giorni or 26))
        permessi_base = Decimal(str(regola.permessi_annui_ore or 72))
    elif parametro:
        ore_sett_base = Decimal(str(parametro.ore_settimanali or 40))
        om = getattr(parametro, 'ore_mensili', None)
        if om is not None and om > 0:
            ore_mens_base = Decimal(str(om))
        else:
            ore_mens_base = (ore_sett_base * Decimal('52') / Decimal('12')).quantize(Decimal('0.01'))
        og = getattr(parametro, 'ore_giornaliere', None)
        if og is not None and og > 0:
            ore_giorn_base = Decimal(str(og))
        else:
            ore_giorn_base = (ore_sett_base / Decimal('5')).quantize(Decimal('0.0001'))
    elif rdl and getattr(rdl, 'ore_settimanali', None) and rdl.ore_settimanali and rdl.ore_settimanali > 0:
        ore_sett_base = Decimal(str(rdl.ore_settimanali))
        ore_mens_base = (ore_sett_base * Decimal('52') / Decimal('12')).quantize(Decimal('0.01'))
        ore_giorn_base = (ore_sett_base / Decimal('5')).quantize(Decimal('0.0001'))

    def qc(d: Decimal) -> Decimal:
        return d.quantize(Decimal('0.01'))

    return {
        'coefficiente_ore': coeff,
        'ore_settimanali': qc(ore_sett_base * coeff),
        'ore_mensili': qc(ore_mens_base * coeff),
        'ore_giornaliere': qc(ore_giorn_base * coeff),
        'ferie_annue_giorni': qc(ferie_base * coeff),
        'permessi_annui_ore': qc(permessi_base * coeff),
        'ha_regola_normativa': regola is not None,
        'ha_parametro_ccnl': parametro is not None,
    }

