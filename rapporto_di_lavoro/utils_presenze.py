"""
Aggregazione presenze per il motore paga.
Utilizzato da Simulatore Paga e Simulazione annua per importare
automaticamente i dati dal calendario presenze.
"""
from __future__ import annotations
import calendar
from datetime import date
from decimal import Decimal

_Q2 = Decimal('0.01')


def ore_cedolino_festivita_totali(ced_by_codice: dict[str, Decimal] | dict) -> Decimal:
    """
    Ore busta motore v4 nel bucket «festività» per confronto con il calendario.

    Su TeamSystem le ore festive compaiono spesso su **più codici con lo stesso valore**
    (stessa competenza ripartita in contabilità):

    - ``8109`` e ``109`` «Festività godute (ore)» identici → una sola somma.
    - Stesso valore anche su ``8108`` (fest. non goduta) insieme alle precedenti →
      tre righe da *n* ore sul PDF non equivalgono a *3n* ore lavorate.

    Se almeno due tra 8108/8109/109 sono > 0 e **tutti i valori positivi coincidono**,
    si usa quel valore una sola volta. Altrimenti: deduplica 8109+109 se uguali, poi
    si somma ``8108`` al risultato.
    """
    c = ced_by_codice or {}

    def _d(key: str) -> Decimal:
        return Decimal(str(c.get(key) or 0)).quantize(_Q2)

    o8020 = _d('8020')
    o8108 = _d('8108')
    o8109 = _d('8109')
    o109 = _d('109')

    trio = (o8108, o8109, o109)
    positivi = [x for x in trio if x > 0]
    if len(positivi) >= 2 and len(set(positivi)) == 1:
        ore_fest_ts = positivi[0]
    elif o8109 > 0 and o109 > 0 and o8109 == o109:
        ore_fest_ts = (o8108 + o8109).quantize(_Q2)
    else:
        ore_fest_ts = (o8108 + o8109 + o109).quantize(_Q2)

    return (o8020 + ore_fest_ts).quantize(_Q2)


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

    La classificazione delle ore (ordinarie, domenicali, festive e straordinari
    per tipologia) e' delegata al core di ``presenze.utils`` per mantenere una
    sola regola di business in tutta l'applicazione.
    """
    from presenze.models import Presenza
    from presenze.utils import ore_std_giornaliere_contratto, _aggregazione_mensile_core
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

    # Classificazione ore centralizzata e coerente con il modulo Presenze:
    # separa sempre ordinario / domeniche / festivi / straord. per categoria.
    ore_std = ore_std_giornaliere_contratto(dipendente, azienda, anno, mese)
    acc = _aggregazione_mensile_core(presenze, festivi_dates, ore_std)

    return {
        'ore_straord_diurno':     Decimal(str(acc.get('ore_straord_diurno', 0))).quantize(_Q2),
        'ore_straord_notturno':   Decimal(str(acc.get('ore_straord_notturno', 0))).quantize(_Q2),
        'ore_straord_festivo':    Decimal(str(acc.get('ore_straord_festivo', 0))).quantize(_Q2),
        'ore_straord_domenica':   Decimal(str(acc.get('ore_straord_domenica', 0))).quantize(_Q2),
        'ore_straord_nott_fest':  Decimal(str(acc.get('ore_straord_nott_fest', 0))).quantize(_Q2),
        'ore_domenicali':         Decimal(str(acc.get('ore_domenicali', 0))).quantize(_Q2),
        'ore_festivi_lavorati':   Decimal(str(acc.get('ore_festivi', 0))).quantize(_Q2),
        'ore_ordinarie_retribuite': Decimal(str(acc.get('ore_ordinarie', 0))).quantize(_Q2),
        'giorni_assenza_ingiust': Decimal(str(acc.get('giorni_assenza_ingiust', 0))).quantize(_Q2),
        'giorni_ferie_godute':    Decimal(str(acc.get('giorni_ferie_godute', 0))).quantize(_Q2),
        'ore_permessi_goduti':    Decimal(str(acc.get('ore_permessi_goduti', 0))).quantize(_Q2),
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

    **Festività:** molti studi TeamSystem non mettono le ore in ``8020`` ma in
    ``8108``/``8109``/``109`` (competenza festività). Il calendario somma ore su giorni
    festivi in ``ore_festivi_lavorati`` + eccedenza giornaliera in ``ore_straord_festivo``.
    Per il confronto con la busta si usa :func:`ore_cedolino_festivita_totali`, allineata
    a ``presenze.views._build_rows_scostamento_cedolino`` (deduplica righe TS uguali su
    8108/8109/109 e 8109/109).
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

    # Ore su giorno festivo (non domenica): entro soglia + eccedenza (non doppio in stra_altri).
    cal_fest_tot = (
        _d(agg.get('ore_festivi_lavorati')) + _d(agg.get('ore_straord_festivo'))
    ).quantize(_Q2)
    cal_stra_non_nott = (
        _d(agg.get('ore_straord_diurno'))
        + _d(agg.get('ore_straord_domenica'))
        + _d(agg.get('ore_straord_nott_fest'))
    ).quantize(_Q2)
    cal_nott = _d(agg.get('ore_straord_notturno'))

    ced_fest_tutti = ore_cedolino_festivita_totali(ced_by_codice)

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
            'label': 'Festività (cal. giorni fest.; busta voci festive TS)',
            'cal': cal_fest_tot,
            'ced': ced_fest_tutti,
            'codici': '8020+8108+8109+109 (dedup. TS se stesso valore su più codici)',
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
            'label': 'Straord. dom./diurno + nott. fest. (cal.; senza eccedenza su fest.)',
            'cal': cal_stra_non_nott,
            'ced': ced_altre_80xx,
            'codici': 'altri 80xx (esclusi 8001,8010,8011,8020,8030)',
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

    Per ogni mese si usa l'intersezione con il periodo rapporto effettivo
    (inclusa ``data_cessazione`` profilo), come nel riepilogo presenze.
    """
    from presenze.views import _intervallo_mese_per_rapporto, _periodo_rapporto_dipendente_per_mese

    az = azienda if azienda is not None else getattr(dipendente, 'azienda', None)
    out = {}
    for m in range(1, 13):
        di, df = _periodo_rapporto_dipendente_per_mese(dipendente, az, anno, m)
        ps, pe = _intervallo_mese_per_rapporto(anno, m, di, df)
        if ps and pe:
            out[m] = get_presenze_mese_aggregato(dipendente, anno, m, azienda, data_da=ps, data_a=pe)
        else:
            _, ult = calendar.monthrange(anno, m)
            out[m] = get_presenze_mese_aggregato(
                dipendente,
                anno,
                m,
                azienda,
                data_da=date(anno, m, ult),
                data_a=date(anno, m, 1),
            )
    return out
