"""
Griglia presenze — motore «Calcolatore ferie e ROL»:
normativa CCNL + rapporto vigente, assenze non maturative da tabella HR.

Saldo mese su mese: fine mese N → apertura mese N+1 (coerente con ``CalcolatoreFerieRol``).
"""
from __future__ import annotations

import calendar
from datetime import date
from typing import TYPE_CHECKING, Any, Optional

from django.db.models import Q

if TYPE_CHECKING:
    from anagrafiche.models import Azienda, Dipendente

from presenze.calcolatore_ferie_rol_formati import (
    giorni_ferie_a_hhmm,
    ore_decimali_a_hhmm,
    ore_per_report_consulente,
)
from presenze.maturazione_ferie_rol import (
    MOTORE_CALCOLATORE_FERIE_ROL_ID,
    CalcolatoreFerieRol,
    DatiContratto,
    SituazioneMensile,
    TipoContrattoCalc,
)


def _qs_causali_non_maturative(azienda: 'Azienda'):
    from presenze.models import CausaleAssenzaNonMaturativa

    return CausaleAssenzaNonMaturativa.objects.filter(
        Q(azienda=azienda) | Q(azienda__isnull=True),
        attiva=True,
    ).order_by('ordine', 'id')


def giorni_malattia_eccedenti_nel_mese(
    dipendente: 'Dipendente',
    azienda: 'Azienda',
    anno: int,
    mese: int,
    comporto_annuo: int,
) -> int:
    """Giorni di malattia nel mese che eccedono l'ordine annuo rispetto al comporto retribuito."""
    from presenze.models import Presenza

    if comporto_annuo <= 0:
        return Presenza.objects.filter(
            dipendente=dipendente,
            azienda=azienda,
            data__year=anno,
            data__month=mese,
            causale='M',
        ).count()

    last = calendar.monthrange(anno, mese)[1]
    end_year = date(anno, mese, last)
    presenze_m = (
        Presenza.objects.filter(
            dipendente=dipendente,
            azienda=azienda,
            data__year=anno,
            data__lte=end_year,
            causale='M',
        )
        .order_by('data')
        .values_list('data', flat=True)
    )
    eccedenti_in_month = 0
    for idx, d in enumerate(presenze_m, start=1):
        if idx > comporto_annuo and d.month == mese and d.year == anno:
            eccedenti_in_month += 1
    return eccedenti_in_month


def giorni_non_maturativi_mese(
    dipendente: 'Dipendente',
    azienda: 'Azienda',
    anno: int,
    mese: int,
    rapporto,
) -> int:
    from presenze.models import Presenza

    tot = 0
    comporto = int(getattr(rapporto, 'giorni_malattia_retribuiti', 0) or 0) if rapporto else 0

    for row in _qs_causali_non_maturative(azienda):
        if row.modalita == 'MALATTIA_ECCEDENTE':
            if row.codice_causale == 'M':
                tot += giorni_malattia_eccedenti_nel_mese(
                    dipendente, azienda, anno, mese, comporto
                )
            continue
        tot += Presenza.objects.filter(
            dipendente=dipendente,
            azienda=azienda,
            data__year=anno,
            data__month=mese,
            causale=row.codice_causale,
        ).count()
    return tot


def coefficiente_giorni_mese_in_rapporto(anno: int, mese: int, rapporto) -> float:
    """
    Frazione [0–1] dei giorni del mese solare compresi nell'intervallo di rapporto
    [data_inizio_rapporto, data_fine_rapporto] (estremi inclusi). Se ``data_fine_rapporto``
    è assente, il rapporto si intende a tempo indeterminato fino a fine mese.
    Mesi totalmente anteriori all'assunzione o successivi alla cessazione → 0.
    """
    if rapporto is None:
        return 0.0
    di = getattr(rapporto, 'data_inizio_rapporto', None)
    if not di:
        return 0.0
    df = getattr(rapporto, 'data_fine_rapporto', None)
    first = date(anno, mese, 1)
    last = date(anno, mese, calendar.monthrange(anno, mese)[1])
    start_eff = max(first, di)
    end_eff = min(last, df) if df else last
    if start_eff > end_eff:
        return 0.0
    giorni_in = (end_eff - start_eff).days + 1
    giorni_mese = (last - first).days + 1
    return giorni_in / giorni_mese if giorni_mese else 0.0


def giorni_lavorati_mese_pt_verticale(
    dipendente: 'Dipendente',
    azienda: 'Azienda',
    anno: int,
    mese: int,
) -> int:
    """Giorni con presenza lavorativa (ore > 0) per PT verticale."""
    from presenze.models import Presenza

    n = 0
    for p in Presenza.objects.filter(
        dipendente=dipendente,
        azienda=azienda,
        data__year=anno,
        data__month=mese,
        causale='P',
    ):
        if p.ore_lavorate() and p.ore_lavorate() > 0:
            n += 1
    return n


def costruisci_dati_contratto(
    dipendente: 'Dipendente',
    azienda: 'Azienda',
    data_rif: date,
) -> DatiContratto:
    from rapporto_di_lavoro.normativa_ccnl import parametri_normativi_contrattuali

    from presenze.utils import _rapporto_vigente_per_ore

    pn = parametri_normativi_contrattuali(dipendente, azienda, data_rif)
    rdl = _rapporto_vigente_per_ore(dipendente, azienda, data_rif)
    coeff = float(pn['coefficiente_ore'])
    ore_sett = float(pn['ore_settimanali'])
    ferie = float(pn['ferie_annue_giorni'])
    rol = float(pn['permessi_annui_ore'])

    tipo_tc = TipoContrattoCalc.FULL_TIME
    ttipo = getattr(getattr(rdl, 'tipo_contratto', None), 'tipo', '') or ''
    if ttipo == 'intermittente':
        tipo_tc = TipoContrattoCalc.PART_TIME_VERTICALE
    elif coeff < 0.99 or ore_sett < 39:
        tipo_tc = TipoContrattoCalc.PART_TIME_ORIZZONTALE

    return DatiContratto(
        tipo_contratto=tipo_tc,
        ferie_annue=ferie,
        rol_annui=rol,
        ore_settimanali_pt=ore_sett if tipo_tc == TipoContrattoCalc.PART_TIME_ORIZZONTALE else None,
        giorni_lavorabili_ft=26,
        annuali_gia_prorati_ccnl=True,
        ore_giornaliere_riferimento=float(pn['ore_giornaliere']),
    )


def build_situazione_mensile(
    dipendente: 'Dipendente',
    azienda: 'Azienda',
    anno: int,
    mese: int,
    ferie_prog_start: float,
    rol_prog_start: float,
    dati: DatiContratto,
    rapporto,
) -> SituazioneMensile:
    from presenze.models import Presenza
    from presenze.utils import ore_std_giornaliere_contratto

    ore_std = float(ore_std_giornaliere_contratto(dipendente, azienda, anno, mese))
    ferie_godute = Presenza.objects.filter(
        dipendente=dipendente,
        azienda=azienda,
        data__year=anno,
        data__month=mese,
        causale='F',
    ).count()
    pe = Presenza.objects.filter(
        dipendente=dipendente,
        azienda=azienda,
        data__year=anno,
        data__month=mese,
        causale='PE',
    ).count()
    rol_goduti_ore = pe * ore_std

    gl: Optional[int] = None
    if dati.tipo_contratto == TipoContrattoCalc.PART_TIME_VERTICALE:
        gl = giorni_lavorati_mese_pt_verticale(dipendente, azienda, anno, mese)

    gn = giorni_non_maturativi_mese(dipendente, azienda, anno, mese, rapporto)
    coeff_pr = coefficiente_giorni_mese_in_rapporto(anno, mese, rapporto)

    return SituazioneMensile(
        giorni_lavorati=gl,
        giorni_non_maturativi=gn,
        ferie_godute_mese=float(ferie_godute),
        rol_goduti_mese=float(rol_goduti_ore),
        ferie_progressive_anno=ferie_prog_start,
        rol_progressivi_anno=rol_prog_start,
        coeff_periodo_rapporto=coeff_pr,
    )


def calcolo_maturazione_griglia_mese(
    dipendente: 'Dipendente',
    azienda: 'Azienda',
    anno: int,
    mese: int,
) -> dict[str, Any]:
    """
    Maturazione teorica mese e residui dopo movimenti del mese (calendario),
    coerente per tutti i dipendenti. I residui da libro monti restano in saldi separati.
    """
    from presenze.utils import _rapporto_vigente_per_ore

    _, ult = calendar.monthrange(anno, mese)
    data_rif = date(anno, mese, min(15, ult))
    data_fine_mese = date(anno, mese, ult)
    rapporto = _rapporto_vigente_per_ore(dipendente, azienda, data_fine_mese)

    try:
        dati = costruisci_dati_contratto(dipendente, azienda, data_rif)
    except Exception as exc:
        return {
            'mat_disponibile': False,
            'mat_motivo': str(exc)[:200],
        }

    from presenze.models import SaldoMonteDipendente

    def _saldo_iniziale_monte(tipo_m: str) -> float:
        saldo = SaldoMonteDipendente.objects.filter(
            dipendente=dipendente,
            azienda=azienda,
            anno_competenza=anno,
            tipo_monte=tipo_m,
        ).first()
        if not saldo:
            return 0.0
        return float(saldo.saldo_iniziale or 0)

    fp = _saldo_iniziale_monte('FERIE_GG')
    rp = _saldo_iniziale_monte('ROL_ORE')
    fp0_riporto = fp
    rp0_riporto = rp
    for m in range(1, mese):
        ult_m = calendar.monthrange(anno, m)[1]
        data_rif_m = date(anno, m, min(15, ult_m))
        data_fine_m = date(anno, m, ult_m)
        rapporto_m = _rapporto_vigente_per_ore(dipendente, azienda, data_fine_m)
        try:
            dati_m = costruisci_dati_contratto(dipendente, azienda, data_rif_m)
        except Exception:
            break
        sit = build_situazione_mensile(
            dipendente, azienda, anno, m, fp, rp, dati_m, rapporto_m
        )
        try:
            r = CalcolatoreFerieRol.calcola(dati_m, sit)
        except Exception:
            break
        fp = r.ferie_residue
        rp = r.rol_residui

    sit_cur = build_situazione_mensile(
        dipendente, azienda, anno, mese, fp, rp, dati, rapporto
    )
    try:
        r_cur = CalcolatoreFerieRol.calcola(dati, sit_cur)
    except Exception as exc:
        return {
            'mat_disponibile': False,
            'mat_motivo': str(exc)[:200],
        }

    out = {
        'mat_disponibile': True,
        'mat_motore_id': MOTORE_CALCOLATORE_FERIE_ROL_ID,
        'mat_motore_nome': 'Calcolatore ferie e ROL',
        'mat_ferie_saldo_apertura': fp,
        'mat_rol_saldo_apertura': rp,
        'mat_ferie_mese': r_cur.ferie_mese,
        'mat_rol_mese': r_cur.rol_mese,
        'mat_ferie_godute_mese': float(sit_cur.ferie_godute_mese),
        'mat_rol_goduti_ore_mese': float(sit_cur.rol_goduti_mese),
        'mat_ferie_progressive': r_cur.ferie_progressive,
        'mat_rol_progressive': r_cur.rol_progressivi,
        'mat_ferie_residue_teoria': r_cur.ferie_residue,
        'mat_rol_residue_teoria': r_cur.rol_residui,
        'mat_giorni_non_maturativi': float(sit_cur.giorni_non_maturativi),
        'mat_ferie_annue_base': dati.ferie_annue,
        'mat_rol_annui_ore_base': dati.rol_annui,
        'mat_quota_mese_in_rapporto': float(sit_cur.coeff_periodo_rapporto),
        'mat_ore_giornaliere_riferimento': float(dati.ore_giornaliere_riferimento or 8.0),
        'mat_ferie_riporto_monte_iniziale': fp0_riporto,
        'mat_rol_riporto_monte_iniziale': rp0_riporto,
    }
    out.update(_formati_maturazione_sidebar(out))
    out.update(_decimali_report_consulente_mese(out))
    return out


def _formati_maturazione_sidebar(d: dict) -> dict:
    """HH:MM per ROL (ore); ferie in gg + equivalente HH:MM (ore giornaliere contratto)."""
    og = float(d.get('mat_ore_giornaliere_riferimento') or 8.0)

    def _equiv_ferie(gg: float) -> str:
        return giorni_ferie_a_hhmm(gg, ore_per_giorno_lavorativo=og)

    return {
        'mat_ferie_mese_gg_fmt': f"{d['mat_ferie_mese']:.4f} gg",
        'mat_ferie_mese_hhmm_equiv': _equiv_ferie(d['mat_ferie_mese']),
        'mat_rol_mese_hhmm': ore_decimali_a_hhmm(d['mat_rol_mese']),
        'mat_ferie_residue_hhmm_equiv': _equiv_ferie(d['mat_ferie_residue_teoria']),
        'mat_rol_residue_hhmm': ore_decimali_a_hhmm(d['mat_rol_residue_teoria']),
        'mat_ferie_saldo_apertura_hhmm_equiv': _equiv_ferie(d['mat_ferie_saldo_apertura']),
        'mat_rol_saldo_apertura_hhmm': ore_decimali_a_hhmm(d['mat_rol_saldo_apertura']),
    }


def _decimali_report_consulente_mese(d: dict) -> dict:
    """Ore ROL / equivalenti in decimale con virgola (export consulente)."""
    return {
        'rep_rol_maturate_ore_dec': ore_per_report_consulente(d['mat_rol_mese']),
        'rep_rol_godute_ore_dec': ore_per_report_consulente(d['mat_rol_goduti_ore_mese']),
        'rep_rol_residue_ore_dec': ore_per_report_consulente(d['mat_rol_residue_teoria']),
        'rep_ferie_maturate_gg_dec': ore_per_report_consulente(d['mat_ferie_mese']),
        'rep_ferie_godute_gg_dec': ore_per_report_consulente(d['mat_ferie_godute_mese']),
        'rep_ferie_residue_gg_dec': ore_per_report_consulente(d['mat_ferie_residue_teoria']),
    }


def cronologia_maturazione_anno(
    dipendente: 'Dipendente',
    azienda: 'Azienda',
    anno: int,
) -> list[dict[str, Any]]:
    """
    Per ogni mese dell'anno: saldi apertura/chiusura, maturato, goduto (motore Calcolatore ferie e ROL).
    Utile per stampa / controllo consulente.
    """
    from presenze.models import SaldoMonteDipendente
    from presenze.utils import _rapporto_vigente_per_ore

    def _saldo_ini(tipo_m: str) -> float:
        saldo = SaldoMonteDipendente.objects.filter(
            dipendente=dipendente,
            azienda=azienda,
            anno_competenza=anno,
            tipo_monte=tipo_m,
        ).first()
        if not saldo:
            return 0.0
        return float(saldo.saldo_iniziale or 0)

    rows: list[dict[str, Any]] = []
    fp = _saldo_ini('FERIE_GG')
    rp = _saldo_ini('ROL_ORE')
    for mese in range(1, 13):
        _, ult = calendar.monthrange(anno, mese)
        data_rif = date(anno, mese, min(15, ult))
        data_fine_mese = date(anno, mese, ult)
        rapporto = _rapporto_vigente_per_ore(dipendente, azienda, data_fine_mese)
        try:
            dati = costruisci_dati_contratto(dipendente, azienda, data_rif)
        except Exception:
            rows.append({'mese': mese, 'mat_disponibile': False})
            continue

        fp_loop = fp
        rp_loop = rp
        for m in range(1, mese):
            ult_m = calendar.monthrange(anno, m)[1]
            data_rif_m = date(anno, m, min(15, ult_m))
            rapporto_m = _rapporto_vigente_per_ore(
                dipendente, azienda, date(anno, m, ult_m)
            )
            try:
                dati_m = costruisci_dati_contratto(dipendente, azienda, data_rif_m)
            except Exception:
                break
            sit = build_situazione_mensile(
                dipendente, azienda, anno, m, fp_loop, rp_loop, dati_m, rapporto_m
            )
            try:
                r = CalcolatoreFerieRol.calcola(dati_m, sit)
            except Exception:
                break
            fp_loop = r.ferie_residue
            rp_loop = r.rol_residui

        sit = build_situazione_mensile(
            dipendente, azienda, anno, mese, fp_loop, rp_loop, dati, rapporto
        )
        try:
            r = CalcolatoreFerieRol.calcola(dati, sit)
        except Exception as exc:
            rows.append({'mese': mese, 'mat_disponibile': False, 'errore': str(exc)[:120]})
            continue

        rows.append(
            {
                'mese': mese,
                'mat_disponibile': True,
                'mat_motore_id': MOTORE_CALCOLATORE_FERIE_ROL_ID,
                'ferie_saldo_apertura': fp_loop,
                'rol_saldo_apertura_ore': rp_loop,
                'ferie_maturate': r.ferie_mese,
                'rol_maturate_ore': r.rol_mese,
                'ferie_godute': sit.ferie_godute_mese,
                'rol_goduti_ore': sit.rol_goduti_mese,
                'ferie_saldo_chiusura': r.ferie_residue,
                'rol_saldo_chiusura_ore': r.rol_residui,
            }
        )
        fp = r.ferie_residue
        rp = r.rol_residui
    return rows


def report_consulente_mese_ferie_rol(
    dipendente: 'Dipendente',
    azienda: 'Azienda',
    anno: int,
    mese: int,
) -> dict[str, Any]:
    """
    Dizionario unico per export mensile verso consulente del lavoro (ore in decimale con virgola).
    """
    base = calcolo_maturazione_griglia_mese(dipendente, azienda, anno, mese)
    if not base.get('mat_disponibile'):
        return base
    return {
        'motore_id': base.get('mat_motore_id'),
        'motore_nome': base.get('mat_motore_nome'),
        'anno': anno,
        'mese': mese,
        'ferie_gg_maturate': base['mat_ferie_mese'],
        'ferie_gg_godute': base['mat_ferie_godute_mese'],
        'ferie_gg_residue': base['mat_ferie_residue_teoria'],
        'rol_ore_maturate': base['mat_rol_mese'],
        'rol_ore_godute': base['mat_rol_goduti_ore_mese'],
        'rol_ore_residue': base['mat_rol_residue_teoria'],
        'rol_ore_maturate_dec_it': base.get('rep_rol_maturate_ore_dec'),
        'rol_ore_godute_dec_it': base.get('rep_rol_godute_ore_dec'),
        'rol_ore_residue_dec_it': base.get('rep_rol_residue_ore_dec'),
        'ferie_gg_maturate_dec_it': base.get('rep_ferie_maturate_gg_dec'),
        'ferie_gg_godute_dec_it': base.get('rep_ferie_godute_gg_dec'),
        'ferie_gg_residue_dec_it': base.get('rep_ferie_residue_gg_dec'),
        'giorni_non_maturativi': base['mat_giorni_non_maturativi'],
    }
