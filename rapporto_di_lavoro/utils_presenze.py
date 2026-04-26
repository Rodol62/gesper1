"""
Aggregazione presenze per il motore paga.
Utilizzato da Simulatore Paga e Simulazione annua per importare
automaticamente i dati dal calendario presenze.
"""
from __future__ import annotations
from datetime import date, time
from decimal import Decimal

_Q2 = Decimal('0.01')
_NOTTE_INIZIO = time(22, 0)
_NOTTE_FINE   = time(6, 0)


def get_presenze_mese_aggregato(
    dipendente,
    anno: int,
    mese: int,
    azienda=None,
    data_da: date | None = None,
    data_a: date | None = None,
) -> dict:
    """
    Legge le presenze di un dipendente per un mese e aggrega le ore
    per categoria payroll.

    Se ``data_da`` / ``data_a`` sono valorizzati, filtra le presenze in quell'intervallo
    (es. intersezione con il periodo contrattuale nel mese), coerente con il riepilogo presenze.

    Classificazione straordinari (ore_straordinario > 0):
      domenica/festivo + notte  → ore_straord_nott_fest
      domenica                  → ore_straord_domenica
      festivo (non domenica)    → ore_straord_festivo
      notte (22:00-06:00)       → ore_straord_notturno
      altrimenti                → ore_straord_diurno

    Ore domenicali: ore effettive lavorate in domenica (magg. % su lordo)
    Ore festive:    ore effettive lavorate in festività (non domenica)
    Ore ordinarie retribuite: base oraria retribuita del mese (ore effettive
      lavorate al netto delle ore registrate come straordinario), usata dal
      motore in modalita' ore effettive.
      Le ore domenicali/festive restano valorizzate separatamente per
      applicare la sola maggiorazione in aggiunta alla base.
    """
    from presenze.models import Presenza
    from .utils_calendario import get_festivita_mese

    # Festivi nazionali + aziendali, escluse le domeniche (già conteggiate separatamente)
    try:
        festivi_dates = {
            f['data'] for f in get_festivita_mese(anno, mese, azienda)
            if f['data'].weekday() != 6
        }
    except Exception:
        festivi_dates = set()

    presenze = Presenza.objects.filter(
        dipendente=dipendente,
        data__year=anno,
        data__month=mese,
    ).order_by('data')
    if data_da is not None:
        presenze = presenze.filter(data__gte=data_da)
    if data_a is not None:
        presenze = presenze.filter(data__lte=data_a)

    ore_straord_diurno    = Decimal('0')
    ore_straord_notturno  = Decimal('0')
    ore_straord_festivo   = Decimal('0')
    ore_straord_domenica  = Decimal('0')
    ore_straord_nott_fest = Decimal('0')
    ore_domenicali        = Decimal('0')
    ore_festivi_lav       = Decimal('0')
    ore_ordinarie_retribuite = Decimal('0')
    giorni_assenza        = Decimal('0')
    giorni_ferie          = Decimal('0')
    ore_permessi          = Decimal('0')

    for p in presenze:
        is_domenica = p.data.weekday() == 6
        is_festivo  = p.data in festivi_dates

        if p.causale == 'A':
            giorni_assenza += 1
            continue
        if p.causale == 'F':
            giorni_ferie += 1
            continue
        if p.causale == 'PE':
            ore_permessi += Decimal(str(p.ore_lavorate() or 0)).quantize(_Q2)
            continue

        # Ore domenicali: ogni ora lavorata in domenica vale la maggiorazione
        if is_domenica and p.causale not in ('R', 'A', 'F', 'PE', 'M', 'INF', 'MAT', 'CIG'):
            ore_domenicali += Decimal(str(p.ore_lavorate() or 0)).quantize(_Q2)

        # Ore festive: ore lavorate in festività nazionale/aziendale (non domenica)
        if (is_festivo or p.causale == 'FE') and not is_domenica:
            ore_festivi_lav += Decimal(str(p.ore_lavorate() or 0)).quantize(_Q2)

        # Straordinari: ore aggiuntive oltre il contratto
        ore_st = Decimal(str(p.ore_straordinario or 0)).quantize(_Q2)
        if ore_st > 0:
            is_notte = (
                (p.ora_entrata is not None and p.ora_entrata >= _NOTTE_INIZIO) or
                (p.ora_uscita  is not None and p.ora_uscita  <= _NOTTE_FINE)
            )
            if (is_festivo or is_domenica) and is_notte:
                ore_straord_nott_fest += ore_st
            elif is_domenica:
                ore_straord_domenica += ore_st
            elif is_festivo:
                ore_straord_festivo += ore_st
            elif is_notte:
                ore_straord_notturno  += ore_st
            else:
                ore_straord_diurno    += ore_st

        # Base ore effettive del mese: tutte le ore lavorate (incluse dom/fest),
        # al netto delle sole ore classificate come straordinario.
        ol = Decimal(str(p.ore_lavorate() or 0)).quantize(_Q2)
        part = ol - ore_st
        if part > 0:
            ore_ordinarie_retribuite += part

    return {
        'ore_straord_diurno':     ore_straord_diurno,
        'ore_straord_notturno':   ore_straord_notturno,
        'ore_straord_festivo':    ore_straord_festivo,
        'ore_straord_domenica':   ore_straord_domenica,
        'ore_straord_nott_fest':  ore_straord_nott_fest,
        'ore_domenicali':         ore_domenicali,
        'ore_festivi_lavorati':   ore_festivi_lav,
        'ore_ordinarie_retribuite': ore_ordinarie_retribuite,
        'giorni_assenza_ingiust': giorni_assenza,
        'giorni_ferie_godute':    giorni_ferie,
        'ore_permessi_goduti':    ore_permessi,
    }


def confronto_tipologie_cal_vs_cedolino_v4(
    agg: dict,
    ced_by_codice: dict[str, Decimal],
) -> list[dict]:
    """
    Righe per tabella confronto: aggregato calendario (``get_presenze_mese_aggregato``)
    vs ore ``ore_gg`` sul cedolino motore v4 (codici TeamSystem).

    I codici 80xx sul PDF possono variare per studio: le voci «altre 80xx» raccolgono
    tutte le competenze 8000–8099 non mappate esplicitamente (spesso straordinari).
    """

    def _d(v) -> Decimal:
        return Decimal(str(v or 0)).quantize(_Q2)

    def _ced_sum(codes: tuple[str, ...]) -> Decimal:
        return sum((_d(ced_by_codice.get(c)) for c in codes), start=Decimal('0')).quantize(_Q2)

    mapped_80 = frozenset({'8001', '8010', '8011', '8020', '8030'})
    ced_altre_80xx = Decimal('0')
    for cod, val in (ced_by_codice or {}).items():
        c = str(cod or '').strip()
        if not c.isdigit():
            continue
        n = int(c)
        if 8000 <= n <= 8099 and c not in mapped_80:
            ced_altre_80xx += _d(val)
    ced_altre_80xx = ced_altre_80xx.quantize(_Q2)

    cal_stra_non_nott = (
        _d(agg.get('ore_straord_diurno'))
        + _d(agg.get('ore_straord_festivo'))
        + _d(agg.get('ore_straord_domenica'))
        + _d(agg.get('ore_straord_nott_fest'))
    ).quantize(_Q2)
    cal_nott = _d(agg.get('ore_straord_notturno'))

    rows: list[dict] = [
        {
            'key': 'ord',
            'label': 'Ordinario (ore base retribuite)',
            'cal': _d(agg.get('ore_ordinarie_retribuite')),
            'ced': _ced_sum(('8001',)),
            'codici': '8001',
        },
        {
            'key': 'dom',
            'label': 'Domenica (ore effettive)',
            'cal': _d(agg.get('ore_domenicali')),
            'ced': _ced_sum(('8010', '8011')),
            'codici': '8010 + 8011',
        },
        {
            'key': 'fest_lav',
            'label': 'Festivo lavorato (ore effettive)',
            'cal': _d(agg.get('ore_festivi_lavorati')),
            'ced': _ced_sum(('8020',)),
            'codici': '8020',
        },
        {
            'key': 'nott',
            'label': 'Straord. / magg. notturno (cal.)',
            'cal': cal_nott,
            'ced': _ced_sum(('8030',)),
            'codici': '8030',
        },
        {
            'key': 'stra_altri',
            'label': 'Straord. fest./dom./diurno fer. + nott. fest. (cal.)',
            'cal': cal_stra_non_nott,
            'ced': ced_altre_80xx,
            'codici': 'altri 80xx (esclusi 8001,8010,8011,8020,8030)',
        },
        {
            'key': 'fest_busta',
            'label': 'Festività ore busta (non god./god.)',
            'cal': None,
            'ced': _ced_sum(('8108', '8109', '109')),
            'codici': '8108 + 8109 + 109',
        },
        {
            'key': 'perm',
            'label': 'Permessi (ore)',
            'cal': _d(agg.get('ore_permessi_goduti')),
            'ced': None,
            'codici': '—',
        },
    ]

    out: list[dict] = []
    for r in rows:
        cal = r['cal']
        ced = r['ced']
        if cal is not None and ced is not None:
            delta = (cal - ced).quantize(_Q2)
        elif cal is not None and ced is None:
            delta = None
        elif cal is None and ced is not None:
            delta = None
        else:
            delta = None
        out.append({
            **r,
            'delta': delta,
        })
    return out


def get_presenze_anno_aggregato(dipendente, anno: int, azienda=None) -> dict:
    """
    Aggrega le presenze per tutti i 12 mesi dell'anno.
    Ritorna un dict {mese_int: aggregato_dict}.
    """
    return {
        m: get_presenze_mese_aggregato(dipendente, anno, m, azienda)
        for m in range(1, 13)
    }
