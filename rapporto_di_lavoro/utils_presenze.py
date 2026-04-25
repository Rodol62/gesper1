"""
Aggregazione presenze per il motore paga.
Utilizzato da Simulatore Paga e Simulazione annua per importare
automaticamente i dati dal calendario presenze.
"""
from __future__ import annotations
from datetime import time
from decimal import Decimal

_Q2 = Decimal('0.01')
_NOTTE_INIZIO = time(22, 0)
_NOTTE_FINE   = time(6, 0)


def get_presenze_mese_aggregato(dipendente, anno: int, mese: int, azienda=None) -> dict:
    """
    Legge le presenze di un dipendente per un mese e aggrega le ore
    per categoria payroll.

    Classificazione straordinari (ore_straordinario > 0):
      domenica/festivo + notte  → ore_straord_nott_fest
      domenica                  → ore_straord_domenica
      festivo (non domenica)    → ore_straord_festivo
      notte (22:00-06:00)       → ore_straord_notturno
      altrimenti                → ore_straord_diurno

    Ore domenicali: ore effettive lavorate in domenica (magg. % su lordo)
    Ore festive:    ore effettive lavorate in festività (non domenica)
    Ore ordinarie retribuite: ore lavorate in giorni feriali non festivi,
      al netto delle ore registrate come straordinario (no doppio conteggio
      con domenicali/festivi).
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

        # Ore ordinarie retribuite (feriali non festivi): ore lavorate meno straord.
        # Domenica e festività lavorata sono interamente a maggiorazione (non qui).
        if not is_domenica and not (is_festivo or p.causale == 'FE'):
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


def get_presenze_anno_aggregato(dipendente, anno: int, azienda=None) -> dict:
    """
    Aggrega le presenze per tutti i 12 mesi dell'anno.
    Ritorna un dict {mese_int: aggregato_dict}.
    """
    return {
        m: get_presenze_mese_aggregato(dipendente, anno, m, azienda)
        for m in range(1, 13)
    }
