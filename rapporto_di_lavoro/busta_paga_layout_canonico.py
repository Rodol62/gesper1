"""
Layout canonico della busta paga mensile (cedolino) — Gesper.

Scopo
-----
Definisce **sezioni, ordine e campi** del cedolino cartaceo/logico su cui
allineare:

- output del motore (``utils_motore_paga.calcola_busta_paga_mese`` e derivati),
- simulazioni e libro paga,
- estrazione / conciliazione cedolino (documenti),
- arrotondamenti, progressivi, dettaglio trattenute, dettaglio presenze.

I **modi di calcolo** delle singole voci vanno rifiniti negli step successivi;
questo file **non** implementa calcoli: è il contratto di presentazione e di
mappatura dati (riferimento unico per revisione dei calcoli).

Riferimenti
-----------
- Trattamento normativo voci: ``motore_paga_schema.DEFAULT_TRATTAMENTI``
- Divisori / catena 172–173,33: ``motore_paga_schema.calcola_schema_divisori``
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import Enum
from typing import Any, Dict, Final, Tuple


class SezioneCedolino(str, Enum):
    """Ordine logico delle macro-aree del cedolino (dall'alto al basso)."""

    INTESTAZIONE = "intestazione"
    ORE_LAVORATE = "ore_lavorate"
    RIGHE_VOCE = "righe_voce"
    INPS = "inps"
    IRPEF = "irpef"
    INAIL = "inail"
    NETTO_BUSTA = "netto_busta"


ORDINE_SEZIONI: Final[Tuple[SezioneCedolino, ...]] = (
    SezioneCedolino.INTESTAZIONE,
    SezioneCedolino.ORE_LAVORATE,
    SezioneCedolino.RIGHE_VOCE,
    SezioneCedolino.INPS,
    SezioneCedolino.IRPEF,
    SezioneCedolino.INAIL,
    SezioneCedolino.NETTO_BUSTA,
)


@dataclass(frozen=True)
class CampoLayout:
    """Campo previsto in una sezione (id stabile + etichetta UI / cedolino)."""
    id: str
    etichetta: str


# ── 1) Intestazione ─────────────────────────────────────────────────────────
# Righe concettuali (come da schema cedolino cartaceo).

INTESTAZIONE_RIGA_1: Final[Tuple[CampoLayout, ...]] = (
    CampoLayout("mese_riferimento", "Mese di riferimento"),
    CampoLayout("cognome_nome", "Cognome e Nome"),
    CampoLayout("data_assunzione", "Data assunzione"),
    CampoLayout("numero_scatti", "N. scatti"),
    CampoLayout("decorrenza_anzianita", "Dec. anzianità"),
)

INTESTAZIONE_RIGA_2: Final[Tuple[CampoLayout, ...]] = (
    CampoLayout("codice_fiscale", "Codice fiscale"),
    CampoLayout("comune_residenza", "Comune di residenza"),
    CampoLayout("data_nascita", "Data di nascita"),
    CampoLayout("giorni_contrattuali", "Gg. contrattuali"),
    CampoLayout("ore_contrattuali", "Ore contrattuali (172 / 173,33)"),
)

INTESTAZIONE_RIGA_3: Final[Tuple[CampoLayout, ...]] = (
    CampoLayout("qualifica", "Qualifica"),
    CampoLayout("percentuale_part_time", "% Part time"),
    CampoLayout("livello", "Livello"),
)

INTESTAZIONE_RIGA_RETRIBUZIONE: Final[Tuple[CampoLayout, ...]] = (
    CampoLayout("paga_base", "Paga base"),
    CampoLayout("contingenza", "Contingenza"),
    CampoLayout("scatti_anzianita", "Scatti anz."),
    CampoLayout("retribuzione_oraria_contrattuale", "Retr. oraria contr."),
    CampoLayout("retribuzione_giornaliera_contrattuale", "Retrib. giorn. contr."),
)


# ── 2) Ore lavorate (griglia + totali) ─────────────────────────────────────

GIORNI_MESE_MAX: Final[int] = 31

ORE_LAVORATE_GRIGLIA_GIORNI: Final[Tuple[str, ...]] = tuple(
    str(g) for g in range(1, GIORNI_MESE_MAX + 1)
)

ORE_LAVORATE_GIORNI_CAMPI: Final[Tuple[CampoLayout, ...]] = tuple(
    CampoLayout(f"giorno_{d}", str(d)) for d in range(1, GIORNI_MESE_MAX + 1)
)

ORE_LAVORATE_TOTALI: Final[Tuple[CampoLayout, ...]] = (
    CampoLayout("ore_ordinarie", "Totale ore lavorative ordinarie"),
    CampoLayout("straordinari", "Straordinari"),
    CampoLayout("domenicali_festivi", "Domenicali / Festivi"),
    CampoLayout("ferie", "Ferie"),
    CampoLayout("assenze", "Assenze"),
    CampoLayout("permessi", "Permessi"),
)


# ── 3) Righe voce (tabella centrale) ───────────────────────────────────────

COLONNE_RIGA_VOCE: Final[Tuple[CampoLayout, ...]] = (
    CampoLayout("codice", "Cod."),
    CampoLayout("descrizione", "Descrizione"),
    CampoLayout("ore", "Ore"),
    CampoLayout("giorni", "Giorni"),
    CampoLayout("importi_base", "Importi base"),
    CampoLayout("competenze", "Competenze"),
    CampoLayout("trattenute", "Trattenute"),
)


# ── 4) INPS ─────────────────────────────────────────────────────────────────

INPS_RIGA: Final[Tuple[CampoLayout, ...]] = (
    CampoLayout("totale_lordo", "Totale lordo"),
    CampoLayout("imponibile_contrattuale", "Impon. contrattuale"),
    CampoLayout("inps_carico_dipendente", "INPS carico dipendente"),
    CampoLayout("totale_inps_dipendente", "Totale INPS dip."),
)


# ── 5) IRPEF ─────────────────────────────────────────────────────────────

IRPEF_RIGA: Final[Tuple[CampoLayout, ...]] = (
    CampoLayout("imponibile_irpef", "Imponibile IRPEF"),
    CampoLayout("irpef_lorda", "IRPEF lorda"),
    CampoLayout("detrazioni", "Detrazioni"),
    CampoLayout("totali_trattenute_irpef", "Totali trattenute IRPEF"),
    CampoLayout("totali_trattenute", "Totali trattenute"),
)


# ── 6) INAIL ─────────────────────────────────────────────────────────────

INAIL_RIGA: Final[Tuple[CampoLayout, ...]] = (
    CampoLayout("ore_inps", "Ore INPS"),
    CampoLayout("giorni_inps", "Giorni INPS"),
    CampoLayout("ore_inail", "Ore INAIL"),
    CampoLayout("giorni_inail", "Giorni INAIL"),
    CampoLayout("imponibile_inail", "Imponibile INAIL"),
)


# ── 7) Netto in busta ─────────────────────────────────────────────────────--

NETTO_BUSTA: Final[Tuple[CampoLayout, ...]] = (
    CampoLayout("importo_netto_busta", "Importo netto in busta"),
)


def elenco_sezioni_con_campi() -> Tuple[Tuple[SezioneCedolino, Tuple[CampoLayout, ...]], ...]:
    """
    Ritorna (sezione, campi) in ordine di stampa.
    Utile per test, template engine e checklist di copertura motore → cedolino.
    """
    return (
        (SezioneCedolino.INTESTAZIONE, INTESTAZIONE_RIGA_1 + INTESTAZIONE_RIGA_2 + INTESTAZIONE_RIGA_3 + INTESTAZIONE_RIGA_RETRIBUZIONE),
        (SezioneCedolino.ORE_LAVORATE, ORE_LAVORATE_GIORNI_CAMPI + ORE_LAVORATE_TOTALI),
        (SezioneCedolino.RIGHE_VOCE, COLONNE_RIGA_VOCE),
        (SezioneCedolino.INPS, INPS_RIGA),
        (SezioneCedolino.IRPEF, IRPEF_RIGA),
        (SezioneCedolino.INAIL, INAIL_RIGA),
        (SezioneCedolino.NETTO_BUSTA, NETTO_BUSTA),
    )


def ids_campi_intestazione() -> Tuple[str, ...]:
    """Flat list di id campo intestazione (per mapping da anagrafica/rapporto)."""
    return tuple(
        c.id
        for c in (INTESTAZIONE_RIGA_1 + INTESTAZIONE_RIGA_2 + INTESTAZIONE_RIGA_3 + INTESTAZIONE_RIGA_RETRIBUZIONE)
    )


def _dget(r: dict[str, Any], key: str, default: Any = None) -> Any:
    return r.get(key, default)


def _fmt_euro(v: Any) -> str:
    if v is None:
        return '—'
    try:
        return str(Decimal(str(v)).quantize(Decimal('0.01')))
    except Exception:
        return '—'


def _fmt_num(v: Any, places: int = 2) -> str:
    if v is None:
        return '—'
    try:
        q = Decimal('1').scaleb(-places)  # 10^-places
        return str(Decimal(str(v)).quantize(q))
    except Exception:
        return '—'


def _mappa_cal_griglia_per_giorno(r: dict[str, Any]) -> Dict[int, dict[str, Any]]:
    """giorno (1..31) → cella da ``cal_griglia`` (settimane × 7)."""
    out: Dict[int, dict[str, Any]] = {}
    for sett in r.get('cal_griglia') or []:
        for cell in sett:
            if not cell:
                continue
            try:
                g = int(cell['giorno'])
            except (TypeError, ValueError, KeyError):
                continue
            out[g] = cell
    return out


def _etichetta_tipo_giorno(cell: dict[str, Any]) -> Tuple[str, str]:
    """(simbolo cedolino, titolo tooltip)."""
    if cell.get('is_chiusura_extra'):
        return 'X', 'Chiusura aziendale'
    if cell.get('is_festivo'):
        tit = (cell.get('festivo_nome') or 'Festivo').strip()
        return 'F', tit
    if cell.get('is_chiusura_sett'):
        return 'C', 'Chiusura settimanale'
    if cell.get('is_lavorativo'):
        return 'L', 'Giorno lavorativo'
    return '·', ''


def _pct_part_time(r: dict[str, Any]) -> str:
    c = _dget(r, 'coeff_ore')
    if c is None:
        return '—'
    try:
        cf = Decimal(str(c))
    except Exception:
        return '—'
    if cf >= Decimal('0.999'):
        return '100 % (tempo pieno)'
    return f'{ (cf * Decimal("100")).quantize(Decimal("0.1")) } %'


def costruisci_riepilogo_simulatore_da_risultato(r: dict[str, Any]) -> dict[str, Any]:
    """
    Mappa il dict ``risultato`` del simulatore (output motore + chiavi aggiunte in vista)
    alla struttura del layout cedolino canonico (solo presentazione; nessun ricalcolo).
    """
    anno = _dget(r, 'anno')
    mese_nome = (_dget(r, 'mese_nome') or '').strip()
    mese_rif = f'{mese_nome} {anno}'.strip() if mese_nome or anno else '—'

    cognome_nome = (
        (_dget(r, 'dipendente_label') or '').strip()
        or (_dget(r, 'nome_test') or '').strip()
        or '—'
    )
    data_ass = (_dget(r, 'data_assunzione_display') or '').strip() or '—'

    om = _dget(r, 'ore_mensili')
    ore_contr = _fmt_num(om, 2) if om is not None else '—'
    gg_contr = _dget(r, 'cal_giorni_lavorativi')
    gg_contr_s = str(gg_contr) if gg_contr is not None else '—'

    rof = _dget(r, 'retribuzione_oraria_di_fatto')
    pg = _dget(r, 'paga_giornaliera')
    div = _dget(r, 'divisore')
    try:
        div_dec = Decimal(str(div))
    except Exception:
        div_dec = Decimal('0')
    usa_div_orario = div_dec > Decimal('30')
    if usa_div_orario:
        # Campo «Ore contrattuali (172 / 173,33)»: mostra sempre il divisore scelto.
        ore_contr = _fmt_num(div_dec, 2)

    paga_base_int = _dget(r, 'paga_base')
    cont_int = _dget(r, 'contingenza')
    scatto_int = _dget(r, 'scatto')
    if usa_div_orario:
        # Con divisore 172/173,33 il part-time impatta le ore lavorate, non la €/h tabellare.
        _hb = _dget(r, 'oraria_tabellare_paga_base')
        _hc = _dget(r, 'oraria_tabellare_contingenza')
        _hs = _dget(r, 'oraria_tabellare_scatto')
        paga_base_int = f'{_fmt_num(_hb, 4)} €/h' if _hb is not None else '—'
        cont_int = f'{_fmt_num(_hc, 4)} €/h' if _hc is not None else '—'
        scatto_int = f'{_fmt_num(_hs, 4)} €/h' if _hs is not None else '—'
    else:
        paga_base_int = f'€ {_fmt_euro(paga_base_int)}'
        cont_int = f'€ {_fmt_euro(cont_int)}'
        scatto_int = f'€ {_fmt_euro(scatto_int)}'

    val_intestazione = {
        'mese_riferimento': mese_rif,
        'cognome_nome': cognome_nome,
        'data_assunzione': data_ass,
        'numero_scatti': '—',
        'decorrenza_anzianita': '—',
        'codice_fiscale': ((_dget(r, 'cedolino_codice_fiscale') or '').strip() or '—'),
        'comune_residenza': ((_dget(r, 'cedolino_comune_residenza') or '').strip() or '—'),
        'data_nascita': ((_dget(r, 'cedolino_data_nascita') or '').strip() or '—'),
        'giorni_contrattuali': gg_contr_s,
        'ore_contrattuali': ore_contr,
        'qualifica': (_dget(r, 'ccnl_qualifica') or '—') or '—',
        'percentuale_part_time': _pct_part_time(r),
        'livello': (_dget(r, 'ccnl_livello') or '—') or '—',
        'paga_base': paga_base_int,
        'contingenza': cont_int,
        'scatti_anzianita': scatto_int,
        'retribuzione_oraria_contrattuale': f'{_fmt_num(rof, 4)} €/h' if rof is not None else '—',
        'retribuzione_giornaliera_contrattuale': f'€ {_fmt_euro(pg)}' if pg is not None else '—',
    }

    try:
        giorni_mese = int(_dget(r, 'giorni_nel_mese') or 31)
    except (TypeError, ValueError):
        giorni_mese = 31
    mappa_cal = _mappa_cal_griglia_per_giorno(r)
    ore_per_giorno: list[dict[str, Any]] = []
    for d in range(1, GIORNI_MESE_MAX + 1):
        if d > giorni_mese:
            ore_per_giorno.append({
                'giorno': d, 'etichetta': str(d), 'valore': '·', 'titolo': '',
            })
            continue
        cell = mappa_cal.get(d)
        if not cell:
            ore_per_giorno.append({
                'giorno': d, 'etichetta': str(d), 'valore': '—', 'titolo': '',
            })
            continue
        abbrev, titolo = _etichetta_tipo_giorno(cell)
        ore_per_giorno.append({
            'giorno': d, 'etichetta': str(d), 'valore': abbrev, 'titolo': titolo,
        })

    def _ore_h(key: str) -> str:
        v = _dget(r, key)
        if v is None:
            return '0,00'
        return _fmt_num(v, 2).replace('.', ',')

    ore_ord = _dget(r, 'ore_ordinarie_retribuite')
    if ore_ord is not None and Decimal(str(ore_ord)) > 0:
        ore_ord_s = _ore_h('ore_ordinarie_retribuite')
    else:
        try:
            gg_o = Decimal(str(_dget(r, 'cal_giorni_ordinari') or 0))
            h_gg = Decimal(str(_dget(r, 'ore_giornaliere') or 0))
            ore_ord_s = _fmt_num((gg_o * h_gg).quantize(Decimal('0.01')), 2).replace('.', ',')
        except Exception:
            ore_ord_s = '—'

    try:
        h_straord = (
            Decimal(str(_dget(r, 'ore_straord_diurno') or 0))
            + Decimal(str(_dget(r, 'ore_straord_notturno') or 0))
            + Decimal(str(_dget(r, 'ore_straord_festivo') or 0))
            + Decimal(str(_dget(r, 'ore_straord_domenica') or 0))
            + Decimal(str(_dget(r, 'ore_straord_nott_fest') or 0))
        )
        straord_s = _fmt_num(h_straord, 2).replace('.', ',')
    except Exception:
        straord_s = '—'

    try:
        dom_fest = (
            Decimal(str(_dget(r, 'ore_domenicali') or 0))
            + Decimal(str(_dget(r, 'ore_festivi') or 0))
        )
        dom_fest_s = _fmt_num(dom_fest, 2).replace('.', ',')
    except Exception:
        dom_fest_s = '—'

    ferie_gg = _dget(r, 'gg_ferie_godute')
    ass_gg = _dget(r, 'giorni_assenza_ingiust')
    perm_h = _dget(r, 'ore_perm_goduti')

    val_ore_totali = {
        'ore_ordinarie': f'{ore_ord_s} h',
        'straordinari': f'{straord_s} h',
        'domenicali_festivi': f'{dom_fest_s} h',
        'ferie': f'{_fmt_num(ferie_gg, 2).replace(".", ",")} gg' if ferie_gg is not None else '—',
        'assenze': f'{_fmt_num(ass_gg, 2).replace(".", ",")} gg' if ass_gg is not None else '—',
        'permessi': f'{_fmt_num(perm_h, 2).replace(".", ",")} h' if perm_h is not None else '—',
    }

    righe_voce: list[dict[str, Any]] = []
    comp = _dget(r, 'competenze_logica_v1') or []
    if comp:
        for row in comp:
            righe_voce.append({
                'codice': row.get('cod') or '',
                'descrizione': row.get('descrizione') or '',
                'ore': row.get('ore_o_gg') or '—',
                'giorni': '—',
                'importi_base': row.get('base'),
                'competenze': row.get('competenze'),
                'trattenute': row.get('trattenute'),
            })
    else:
        for v in _dget(r, 'voci') or []:
            nome = v.get('nome') or ''
            imp = v.get('importo')
            ore_v = v.get('ore') or v.get('ore_lav_ord')
            gg_v = v.get('gg')
            ore_cell = '—'
            if ore_v is not None:
                ore_cell = f'{_fmt_num(ore_v, 2)} h'
            elif gg_v is not None:
                ore_cell = f'{_fmt_num(gg_v, 2)} gg'
            righe_voce.append({
                'codice': '',
                'descrizione': nome,
                'ore': ore_cell,
                'giorni': '—',
                'importi_base': v.get('oraria_tab'),
                'competenze': imp,
                'trattenute': None,
            })

    lordo_tot = _dget(r, 'lordo_con_1314')
    if lordo_tot is None:
        try:
            lordo_tot = Decimal(str(_dget(r, 'lordo_mensile') or 0))
            lordo_tot += Decimal(str(_dget(r, 'rat13_m') or 0))
            lordo_tot += Decimal(str(_dget(r, 'rat14_m') or 0))
        except Exception:
            lordo_tot = None

    lordo_imp = _dget(r, 'lordo_imponibile_inps_m')
    inps_dip = _dget(r, 'inps_dip')
    tot_inps_dip = _dget(r, 'tot_contrib_dip')

    try:
        trat_irpef_tot = abs(Decimal(str(_dget(r, 'inps_dip') or 0)))
        trat_irpef_tot += abs(Decimal(str(_dget(r, 'irpef_netta') or 0)))
        trat_irpef_tot += abs(Decimal(str(_dget(r, 'add_reg_m') or 0)))
        trat_irpef_tot += abs(Decimal(str(_dget(r, 'add_com_m') or 0)))
        trat_irpef_tot = trat_irpef_tot.quantize(Decimal('0.01'))
    except Exception:
        trat_irpef_tot = None

    val_inps = {
        'totale_lordo': lordo_tot,
        'imponibile_contrattuale': lordo_imp,
        'inps_carico_dipendente': inps_dip,
        'totale_inps_dipendente': tot_inps_dip,
    }

    val_irpef = {
        'imponibile_irpef': _dget(r, 'imponibile_m'),
        'irpef_lorda': _dget(r, 'irpef_lorda'),
        'detrazioni': _dget(r, 'detrazioni'),
        'totali_trattenute_irpef': _dget(r, 'irpef_netta'),
        'totali_trattenute': trat_irpef_tot,
    }

    try:
        _gl = _dget(r, 'giorni_lavorati')
        if _gl is None:
            _gl = _dget(r, 'cal_giorni_lavorativi')
        gg_lav = int(_gl) if _gl is not None else '—'
    except (TypeError, ValueError):
        gg_lav = '—'

    val_inail = {
        'ore_inps': _fmt_num(_dget(r, 'ore_mensili'), 2),
        'giorni_inps': str(gg_lav) if gg_lav != '—' else '—',
        'ore_inail': _fmt_num(_dget(r, 'ore_mensili'), 2),
        'giorni_inail': str(gg_lav) if gg_lav != '—' else '—',
        'imponibile_inail': lordo_imp,
    }

    netto_disp = _dget(r, 'netto_mensile_con_1314')
    if netto_disp is None:
        try:
            netto_disp = (
                Decimal(str(_dget(r, 'netto_totale') or 0))
                + Decimal(str(_dget(r, 'rat13_n') or 0))
                + Decimal(str(_dget(r, 'rat14_n') or 0))
            ).quantize(Decimal('0.01'))
        except Exception:
            netto_disp = _dget(r, 'netto_totale')

    val_netto = {'importo_netto_busta': netto_disp}

    return {
        'intestazione_righe': (
            INTESTAZIONE_RIGA_1,
            INTESTAZIONE_RIGA_2,
            INTESTAZIONE_RIGA_3,
            INTESTAZIONE_RIGA_RETRIBUZIONE,
        ),
        'intestazione_valori': val_intestazione,
        'ore_griglia_giorni': ore_per_giorno,
        'ore_totali_campi': ORE_LAVORATE_TOTALI,
        'ore_totali_valori': val_ore_totali,
        'righe_voce_colonne': COLONNE_RIGA_VOCE,
        'righe_voce': righe_voce,
        'inps_campi': INPS_RIGA,
        'inps_valori': val_inps,
        'irpef_campi': IRPEF_RIGA,
        'irpef_valori': val_irpef,
        'inail_campi': INAIL_RIGA,
        'inail_valori': val_inail,
        'netto_campi': NETTO_BUSTA,
        'netto_valori': val_netto,
    }
