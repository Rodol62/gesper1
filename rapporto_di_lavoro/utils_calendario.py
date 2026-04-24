"""
Utility per il calendario lavorativo aziendale.

Calendario canonico usato dal motore paga:
- identificatore: `calendario_lavorativo_aziendale_v1`

Fornisce:
- get_festivita_mese(anno, mese, azienda=None) → list[dict]
- get_chiusure_extra_mese(azienda, anno, mese) → set[int]  (giorni del mese)
- get_chiusura_settimanale(azienda, anno, mese) → list[int]  (0=Lun … 6=Dom)
- get_giorni_lavorativi_mese(azienda, anno, mese) → dict
- build_griglia_mese(anno, mese, azienda=None, chiusura_settimanale=None) → list[list]
- FESTIVITA_NAZIONALI_2026 → dict {date: nome}
"""
from __future__ import annotations

import calendar
from datetime import date, timedelta
from typing import Optional


CALENDARIO_LAVORATIVO_AZIENDALE_V1 = 'calendario_lavorativo_aziendale_v1'


def get_calendario_motore_id() -> str:
    """Identificatore stabile del calendario canonico usato dal motore paga."""
    return CALENDARIO_LAVORATIVO_AZIENDALE_V1

# ── Festività nazionali italiane 2026 ───────────────────────────────────────
# Pasqua 2026: 5 aprile (domenica), Pasquetta: 6 aprile
FESTIVITA_NAZIONALI_2026: dict[date, str] = {
    date(2026,  1,  1): 'Capodanno',
    date(2026,  1,  6): 'Epifania',
    date(2026,  4,  5): 'Pasqua',
    date(2026,  4,  6): 'Lunedì dell\'Angelo',
    date(2026,  4, 25): 'Festa della Liberazione',
    date(2026,  5,  1): 'Festa del Lavoro',
    date(2026,  6,  2): 'Festa della Repubblica',
    date(2026,  8, 15): 'Ferragosto',
    date(2026, 11,  1): 'Ognissanti',
    date(2026, 12,  8): 'Immacolata Concezione',
    date(2026, 12, 25): 'Natale',
    date(2026, 12, 26): 'Santo Stefano',
}


def _festivita_per_anno(anno: int) -> dict[date, str]:
    """
    Restituisce le festività nazionali per l'anno richiesto.
    Per anni diversi dal 2026 usa la stessa struttura ricalcolando Pasqua.
    """
    if anno == 2026:
        return FESTIVITA_NAZIONALI_2026

    # Calcolo Pasqua (algoritmo di Butcher)
    a = anno % 19
    b, c = divmod(anno, 100)
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month, day = divmod(h + l - 7 * m + 114, 31)
    pasqua = date(anno, month, day + 1)
    pasquetta = pasqua + timedelta(days=1)

    return {
        date(anno,  1,  1): 'Capodanno',
        date(anno,  1,  6): 'Epifania',
        pasqua:               'Pasqua',
        pasquetta:            'Lunedì dell\'Angelo',
        date(anno,  4, 25): 'Festa della Liberazione',
        date(anno,  5,  1): 'Festa del Lavoro',
        date(anno,  6,  2): 'Festa della Repubblica',
        date(anno,  8, 15): 'Ferragosto',
        date(anno, 11,  1): 'Ognissanti',
        date(anno, 12,  8): 'Immacolata Concezione',
        date(anno, 12, 25): 'Natale',
        date(anno, 12, 26): 'Santo Stefano',
    }


def get_festivita_mese(anno: int, mese: int, azienda=None) -> list[dict]:
    """
    Restituisce le festività (nazionali + aziendali) per un mese.

    Returns:
        list of dict {data, nome, livello, is_nazionale}
    """
    from .models import FestivitaCalendario  # lazy import

    naz = _festivita_per_anno(anno)
    risultato = []

    # Nazionali hardcoded
    for d, nome in naz.items():
        if d.year == anno and d.month == mese:
            risultato.append({'data': d, 'nome': nome, 'livello': 'nazionale', 'is_nazionale': True})

    # Aziendali da DB (patrono, ecc.) — se azienda fornita
    if azienda:
        qs = FestivitaCalendario.objects.filter(
            azienda=azienda,
            data__year=anno,
            data__month=mese,
            attivo=True,
        )
        for f in qs:
            risultato.append({'data': f.data, 'nome': f.nome, 'livello': f.livello, 'is_nazionale': False})

    return risultato


def get_chiusure_extra_mese(azienda, anno: int, mese: int) -> set[date]:
    """
    Restituisce le date di chiusura extra (ChiusuraAziendale) nel mese.
    """
    from .models import ChiusuraAziendale  # lazy import

    _, ultimo_giorno = calendar.monthrange(anno, mese)
    inizio_mese = date(anno, mese, 1)
    fine_mese   = date(anno, mese, ultimo_giorno)

    chiuse = set()
    for c in ChiusuraAziendale.objects.filter(
        azienda=azienda,
        attivo=True,
        data_inizio__lte=fine_mese,
        data_fine__gte=inizio_mese,
    ):
        cur = max(c.data_inizio, inizio_mese)
        end = min(c.data_fine, fine_mese)
        while cur <= end:
            chiuse.add(cur)
            cur += timedelta(days=1)

    return chiuse


def get_chiusura_settimanale(azienda, anno: int, mese: int) -> list[int]:
    """
    Restituisce la lista dei giorni settimanali di chiusura (0=Lun…6=Dom)
    per il mese specificato. Default: [6] (domenica).
    """
    from .models import CalendarioLavoroMensile  # lazy import

    try:
        clm = CalendarioLavoroMensile.objects.get(azienda=azienda, anno=anno, mese=mese)
        return clm.chiusura_settimanale or []
    except CalendarioLavoroMensile.DoesNotExist:
        return [6]  # default domenica


def get_giorni_lavorativi_mese(azienda, anno: int, mese: int) -> dict:
    """
    Calcola i giorni lavorativi effettivi del mese tenendo conto di:
    - Chiusura settimanale (da CalendarioLavoroMensile)
    - Festività nazionali e aziendali (FestivitaCalendario + hardcoded)
    - Chiusure extra aziendali (ChiusuraAziendale)

    Returns dict:
        giorni_totali        int  — giorni nel mese
        chiusure_settimanali int  — nr. giorni di chiusura settimanale nel mese
        festivi              int  — nr. festività che cadono in giorni aperti
        chiusure_extra       int  — nr. giorni ChiusuraAziendale in giorni aperti
        giorni_lavorativi    int  — giorni effettivamente lavorativi (≤ 26 conv.)
        giorni_conv_26       int  — proporzione su base convenzionale 26
        festivi_lavorabili   int  — festivi che potrebbero essere lavorati (su gg aperti)
        dates_festivita      list[date]
        dates_chiusure_sett  list[date]
        dates_chiusure_extra list[date]
    """
    _, giorni_totali = calendar.monthrange(anno, mese)
    chiusura_sett = get_chiusura_settimanale(azienda, anno, mese) if azienda else [6]
    festivita = {f['data'] for f in get_festivita_mese(anno, mese, azienda)}
    chiusure_extra = get_chiusure_extra_mese(azienda, anno, mese) if azienda else set()

    dates_chiusure_sett = []
    dates_festivi_aperti = []
    dates_chiusure_extra_aperti = []

    giorni_lav = 0
    for giorno in range(1, giorni_totali + 1):
        d = date(anno, mese, giorno)
        wd = d.weekday()  # 0=Lun … 6=Dom

        is_chiusura_sett = wd in chiusura_sett
        is_festivo = d in festivita
        is_chiusura_extra = d in chiusure_extra

        if is_chiusura_sett:
            dates_chiusure_sett.append(d)
        elif is_chiusura_extra:
            dates_chiusure_extra_aperti.append(d)
        else:
            # I festivi sono giorni lavorativi (con maggiorazione), non chiusure
            giorni_lav += 1
            if is_festivo:
                dates_festivi_aperti.append(d)

    # Proporzione su base convenzionale 26
    giorni_conv_26 = min(26, round(giorni_lav * 26 / giorni_totali))

    return {
        'giorni_totali':        giorni_totali,
        'chiusure_settimanali': len(dates_chiusure_sett),
        'festivi':              len(dates_festivi_aperti),
        'chiusure_extra':       len(dates_chiusure_extra_aperti),
        'giorni_lavorativi':    giorni_lav,
        'giorni_conv_26':       giorni_conv_26,
        'festivi_lavorabili':   len(dates_festivi_aperti),
        'dates_festivita':      sorted(festivita),
        'dates_chiusure_sett':  dates_chiusure_sett,
        'dates_chiusure_extra': dates_chiusure_extra_aperti,
    }


def build_griglia_mese(
    anno: int,
    mese: int,
    azienda=None,
    chiusura_settimanale: Optional[list[int]] = None,
) -> list[list[Optional[dict]]]:
    """
    Costruisce la griglia settimanale del mese (per il template).

    Returns: list of weeks, each week = list of 7 dicts or None (padding).
    dict keys: giorno, data, weekday, is_chiusura_sett, is_festivo,
               festivo_nome, is_chiusura_extra, is_lavorativo
    """
    if chiusura_settimanale is None:
        chiusura_settimanale = get_chiusura_settimanale(azienda, anno, mese) if azienda else [6]

    festivita_mese = {f['data']: f['nome'] for f in get_festivita_mese(anno, mese, azienda)}
    chiusure_extra = get_chiusure_extra_mese(azienda, anno, mese) if azienda else set()

    _, giorni_totali = calendar.monthrange(anno, mese)

    # primo_giorno_wd: 0=Lun per calendar.monthrange
    primo_wd = date(anno, mese, 1).weekday()

    days_flat: list[Optional[dict]] = [None] * primo_wd

    for giorno in range(1, giorni_totali + 1):
        d = date(anno, mese, giorno)
        wd = d.weekday()
        is_cs = wd in chiusura_settimanale
        is_fes = d in festivita_mese
        is_ce = d in chiusure_extra
        days_flat.append({
            'giorno':           giorno,
            'data':             d,
            'weekday':          wd,
            'is_chiusura_sett': is_cs,
            'is_festivo':       is_fes,
            'festivo_nome':     festivita_mese.get(d, ''),
            'is_chiusura_extra': is_ce,
            'is_lavorativo':    not (is_cs or is_fes or is_ce),
        })

    # Pad fine settimana
    while len(days_flat) % 7 != 0:
        days_flat.append(None)

    return [days_flat[i:i+7] for i in range(0, len(days_flat), 7)]
