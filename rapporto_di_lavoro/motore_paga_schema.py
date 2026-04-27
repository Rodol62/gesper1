"""
Schema divisori contrattuali e classificazione voci per il motore paga mensile unico.

Documenta la catena richiesta in simulatore (÷172 vs ÷173,33 con ore settimanali)
e fornisce i trattamenti predefiniti INPS / INAIL / IRPEF / 13ª / 14ª / TFR
per riconciliazione con buste e cedolini (override da `MappaturaVoceMotore` e imponibili da `VoceRetributiva`).
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Dict, Optional

Q4 = Decimal('0.0001')
Q2 = Decimal('0.01')


@dataclass(frozen=True)
class TrattamentoVoceMotore:
    """Classificazione «normativa» di una voce in output motore (pre-DB)."""
    ordine_schema: int
    imponibile_inps: bool
    imponibile_inail: bool
    imponibile_irpef: bool
    matura_tredicesima: bool
    matura_quattordicesima: bool
    concorre_tfr: bool
    nota: str = ''


# Codici allineati a utils_motore_paga.voci_input (MAGG_DOM_FEST = magg. domenica/festivo non straord.)
DEFAULT_TRATTAMENTI: Dict[str, TrattamentoVoceMotore] = {
    'MINIMO_TABELLARE': TrattamentoVoceMotore(
        1, True, True, True, True, True, True, 'Paga base / minimo tabellare',
    ),
    'CONTINGENZA': TrattamentoVoceMotore(
        2, True, True, True, True, True, True, 'Contingenza + EDR (aggregato in busta)',
    ),
    'IND_FUNZIONE': TrattamentoVoceMotore(
        3, True, True, True, True, True, True, 'Indennità contrattuali / funzione',
    ),
    'SUPERMINIMO': TrattamentoVoceMotore(
        4, True, True, True, True, True, True, 'Superminimo',
    ),
    'SCATTO_ANZIANITA': TrattamentoVoceMotore(
        5, True, True, True, True, True, True, 'Scatti anzianità',
    ),
    'IND_TURNO': TrattamentoVoceMotore(
        6, True, True, True, True, True, True, 'Indennità di turno',
    ),
    'STRAORD_DIURNO': TrattamentoVoceMotore(
        10, True, True, True, False, False, False, 'Straordinario — non matura 13ª/14ª/TFR',
    ),
    'STRAORD_NOTTURNO': TrattamentoVoceMotore(
        11, True, True, True, False, False, False, 'Straordinario notturno',
    ),
    'STRAORD_FESTIVO': TrattamentoVoceMotore(
        12, True, True, True, False, False, False, 'Straord. festivo / nott. fest.',
    ),
    'STRAORD_DOMENICA': TrattamentoVoceMotore(
        13, True, True, True, False, False, False, 'Straord. domenicale',
    ),
    'MAGG_DOM_FEST': TrattamentoVoceMotore(
        14, True, True, True, True, True, True, 'Maggiorazioni lav. domenica/festivo (non straord.)',
    ),
    'TI_DL3_2020': TrattamentoVoceMotore(
        50, False, False, False, False, False, False, 'Trattamento integrativo — credito imposta',
    ),
    'BONUS_L207_2024': TrattamentoVoceMotore(
        51, False, False, False, False, False, False, 'L.207/2024 — fuori INPS; credito/detrazione IRPEF',
    ),
    'TREDICESIMA': TrattamentoVoceMotore(
        80, False, False, False, False, False, True,
        'Rateo 13ª lordo: imponibile INPS/IRPEF/INAIL solo se quota mensile in busta (flag contratto)',
    ),
    'QUATTORDICESIMA': TrattamentoVoceMotore(
        81, False, False, False, False, False, True,
        'Rateo 14ª: come 13ª — default accantonamento',
    ),
}


def calcola_schema_divisori(
    *,
    divisore_orario: Decimal,
    ore_settimanali: Decimal,
    giorni_lavorativi_mese: Decimal = Decimal('26'),
    giorni_lavorativi_settimanali: Decimal = Decimal('6'),
) -> Dict[str, Any]:
    """
    Catena convenzionale (simulatore):

    - **divisore orario** (172 o 173,33): ore mensili convenzionali a cui si divide il lordo tabellare.
    - **divisore fisso convenzionale** = divisore_orario / giorni_lavorativi_mese (default 26 gg conv. FIPE),
      es. 172÷26 ≈ 6,6154 (non va diviso per le ore settimanali part-time).
    - **giorni lavorativi mese** e **settimanali** (default 26 e 6, configurabili).
    - **ore lavorative giornaliere** (qui): media ore_settimanali / giorni_lavorativi_settimanali (riferimento
      contrattuale). In ``calcola_busta_paga_mese`` la stessa logica (h/sett ÷ 6) alimenta ``ore_giornaliere``
      per maggiorazioni; con divisore orario (172/173,33) la **retribuzione oraria di fatto** deriva dalla somma
      delle voci tabellari FT del mese (inclusi gli scatti) ÷ divisore; straordinari e maggiorazioni si calcolano
      a parte sulle ore indicate × quell’orario × la percentuale. **Senza** applicare prima il coefficiente
      part-time su quegli importi tabellari (allineamento foglio Excel INPS / FIPE). Il part-time resta sulle ore
      mensili e sulle voci in busta.
    """
    div = divisore_orario if divisore_orario > 0 else Decimal('173.33')
    ore_sett = ore_settimanali if ore_settimanali > 0 else Decimal('40')
    g_mese = giorni_lavorativi_mese if giorni_lavorativi_mese > 0 else Decimal('26')
    g_sett = giorni_lavorativi_settimanali if giorni_lavorativi_settimanali > 0 else Decimal('6')
    divisore_fisso_conv = (div / g_mese).quantize(Q4)
    ore_gg = (ore_sett / g_sett).quantize(Q4)
    return {
        'divisore_orario': div,
        'ore_settimanali': ore_sett,
        'divisore_fisso_convenzionale': divisore_fisso_conv,
        'giorni_lavorativi_mese': g_mese,
        'giorni_lavorativi_settimanali': g_sett,
        'ore_lavorative_giornaliere': ore_gg,
    }


def trattamento_voce(codice: str) -> TrattamentoVoceMotore:
    return DEFAULT_TRATTAMENTI.get(
        codice,
        TrattamentoVoceMotore(99, True, True, True, True, True, True, 'Voce generica'),
    )


def trattamento_da_mappatura(mappatura: Any) -> TrattamentoVoceMotore:
    """Converte una riga `MappaturaVoceMotore` (modello Django) in `TrattamentoVoceMotore`."""
    eti = (getattr(mappatura, 'etichetta_riconciliazione', None) or '').strip()
    note = (getattr(mappatura, 'note_riconciliazione', None) or '').strip()
    if eti and note:
        nota = f'{eti} — {note}'
    else:
        nota = eti or note or 'Mappatura motore'
    ordine = int(getattr(mappatura, 'ordine_calcolo', 99) or 99)
    return TrattamentoVoceMotore(
        ordine,
        bool(mappatura.imponibile_inps),
        bool(mappatura.imponibile_inail),
        bool(mappatura.imponibile_irpef),
        bool(mappatura.matura_tredicesima),
        bool(mappatura.matura_quattordicesima),
        bool(mappatura.concorre_tfr),
        nota,
    )


def applica_trattamento_a_riga_voce(
    riga: Dict[str, Any],
    voce_db: Optional[Any] = None,
    mappatura_db: Optional[Any] = None,
    *,
    rateo_13_mensile_in_imponibile: bool = False,
    rateo_14_mensile_in_imponibile: bool = False,
) -> Dict[str, Any]:
    """Arricchisce una riga voci_classificate con ordine e flag 13ª/14ª/TFR.

    Priorità: trattamento strutturale da `MappaturaVoceMotore` se attiva, altrimenti default codice.
    Imponibili INPS/INAIL/IRPEF: prima `VoceRetributiva` anagrafica, poi mappatura, poi default.

    Per **TREDICESIMA** / **QUATTORDICESIMA** i tre flag imponibili sono sempre ricalcolati in base ai
    flag ``rateo_*_mensile_in_imponibile`` (coerente con Box 1 «solo accantonamento» vs imponibile mese).
    """
    cod = str(riga.get('codice') or '')
    t_def = trattamento_voce(cod)
    mappa_ok = mappatura_db is not None and bool(getattr(mappatura_db, 'attivo', True))
    t_struct = trattamento_da_mappatura(mappatura_db) if mappa_ok else t_def
    out = {**riga}
    out['ordine_schema'] = t_struct.ordine_schema
    out['matura_tredicesima'] = t_struct.matura_tredicesima
    out['matura_quattordicesima'] = t_struct.matura_quattordicesima
    out['concorre_tfr'] = t_struct.concorre_tfr
    out['nota_trattamento'] = t_struct.nota
    out['fonte_trattamento'] = 'mappatura' if mappa_ok else 'default'
    if voce_db is not None:
        out['imponibile_inps'] = bool(voce_db.imponibile_inps)
        out['imponibile_inail'] = bool(voce_db.imponibile_inail)
        out['imponibile_irpef'] = bool(voce_db.imponibile_irpef)
        out['fonte_imponibili'] = 'voce_retributiva'
    elif mappa_ok:
        out['imponibile_inps'] = bool(mappatura_db.imponibile_inps)
        out['imponibile_inail'] = bool(mappatura_db.imponibile_inail)
        out['imponibile_irpef'] = bool(mappatura_db.imponibile_irpef)
        out['fonte_imponibili'] = 'mappatura'
    else:
        out['imponibile_inps'] = t_def.imponibile_inps
        out['imponibile_inail'] = t_def.imponibile_inail
        out['imponibile_irpef'] = t_def.imponibile_irpef
        out['fonte_imponibili'] = 'default'

    if cod == 'TREDICESIMA':
        if rateo_13_mensile_in_imponibile:
            out['imponibile_inps'] = out['imponibile_inail'] = out['imponibile_irpef'] = True
            out['fonte_imponibili'] = 'rateo_13_mensile_in_busta'
        else:
            out['imponibile_inps'] = out['imponibile_inail'] = out['imponibile_irpef'] = False
            out['fonte_imponibili'] = 'rateo_13_accantonamento'
    elif cod == 'QUATTORDICESIMA':
        if rateo_14_mensile_in_imponibile:
            out['imponibile_inps'] = out['imponibile_inail'] = out['imponibile_irpef'] = True
            out['fonte_imponibili'] = 'rateo_14_mensile_in_busta'
        else:
            out['imponibile_inps'] = out['imponibile_inail'] = out['imponibile_irpef'] = False
            out['fonte_imponibili'] = 'rateo_14_accantonamento'
    return out
