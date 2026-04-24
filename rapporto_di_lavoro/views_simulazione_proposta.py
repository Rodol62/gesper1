"""
View: simulazione economica allegata alla proposta di assunzione.

Produce due prospetti affiancati per il dipendente:
  A) Stipendio netto mensile (mese tipo, tutti i giorni lavorativi)
     — include maggiorazioni domenicali, bonus TI/L207 se previsti
     — mostra: netto mensile, paga giornaliera netta, paga oraria netta

  B) Come (A) + ratei netti mensili accantonati
     — TFR mensile netto, rateo 13ª netto, rateo 14ª netto, rateo ferie netto
     — mostra: netto mensile con ratei, paga giornaliera netta con ratei, paga oraria netta con ratei

Sezione di confronto: paragona i valori A/B con la paga giornaliera attesa
indicata dal candidato in fase di registrazione.
"""
from __future__ import annotations

import calendar as _cal_mod
from datetime import date
from decimal import Decimal

from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, render

from accounts.formatting import euro_it_str

from .models import PropostaAssunzione, CCNL
from .services_simulazione import invoca_calcola_busta_paga_mese


def _primo_mese_pieno(data_inizio: date) -> tuple[int, int]:
    """
    Restituisce (anno, mese) del primo mese interamente coperto dalla proposta.
    Se il rapporto inizia il 1° del mese → quel mese stesso.
    Altrimenti → mese successivo.
    """
    if data_inizio.day == 1:
        return data_inizio.year, data_inizio.month
    if data_inizio.month == 12:
        return data_inizio.year + 1, 1
    return data_inizio.year, data_inizio.month + 1


def _fmt(value) -> str:
    """Formattazione importo italiano: punto migliaia, virgola decimali."""
    if value is None or value == '':
        return '—'
    s = euro_it_str(value)
    return s if s else '—'


@login_required
def simulazione_economica_proposta(request, proposta_id: int):
    """
    Mostra la simulazione economica allegata alla proposta `proposta_id`.
    Accessibile da: admin HR, admin azienda, dipendente proprietario della proposta.
    """
    from .views import _get_proposta_con_permesso, _is_admin_like

    proposta = _get_proposta_con_permesso(request, proposta_id)

    cp = proposta.parametro_ccnl_risolto
    if not cp:
        return render(request, 'rapporto_di_lavoro/simulazione_economica_proposta.html', {
            'proposta': proposta,
            'errore': (
                'Impossibile calcolare la simulazione: nessun parametro CCNL collegato alla proposta '
                'e nessuna riga attiva in «Parametri CCNL Turismo» per il livello indicato nella proposta.'
            ),
        })

    # Profilo candidato (per paga attesa e familiari)
    profilo = None
    paga_attesa = None
    num_familiari = 0
    regione = 'Sicilia'  # default operativo
    if proposta.dipendente:
        try:
            profilo = proposta.dipendente.profilocandidato
            paga_attesa = profilo.paga_giornaliera_attesa
            num_familiari = profilo.num_familiari_a_carico or 0
            if profilo.regione_residenza:
                regione = profilo.regione_residenza
        except Exception:
            pass

    # Mese di riferimento per la simulazione
    data_inizio = proposta.data_inizio_rapporto or date.today()
    anno, mese = _primo_mese_pieno(data_inizio)
    nome_mese = [
        '', 'Gennaio', 'Febbraio', 'Marzo', 'Aprile', 'Maggio', 'Giugno',
        'Luglio', 'Agosto', 'Settembre', 'Ottobre', 'Novembre', 'Dicembre',
    ][mese]

    # CCNL object per DB lookups
    ccnl_obj = CCNL.objects.filter(sigla__icontains='FIPE').first()

    # Divisore
    divisore_str = str(round(float(cp.ore_mensili))) if cp.ore_mensili else '173'

    # Chiamata motore — mese pieno con domenicali automatici
    try:
        r = invoca_calcola_busta_paga_mese(
            log_prefix='SIMULAZIONE_PROPOSTA',
            parametro_ccnl=cp,
            tipo_contratto=proposta.tipo_contratto,
            anno=anno,
            mese=mese,
            azienda=proposta.azienda,
            data_inizio_rapporto=date(anno, mese, 1),  # mese intero
            divisore_str=divisore_str,
            auto_ore_domenicali_da_calendario=True,
            ccnl_obj=ccnl_obj,
            num_familiari_a_carico=num_familiari,
            regione_residenza=regione,
        )
    except Exception as exc:
        return render(request, 'rapporto_di_lavoro/simulazione_economica_proposta.html', {
            'proposta': proposta,
            'errore': f'Errore durante il calcolo: {exc}',
        })

    # ── Valori colonna A: netto mensile + ratei 13ª/14ª (pagati mensilmente) ──
    netto = r['netto_totale']
    ore_mensili_r = r.get('ore_mensili', Decimal(divisore_str))
    Q2 = Decimal('0.01')

    # Ratei 13ª/14ª: per FIPE sono pagati mensilmente → inclusi nel netto di colonna A
    includi_rat13 = getattr(proposta, 'tredicesima', True)
    includi_rat14 = getattr(proposta, 'quattordicesima', False)
    rat13_n_v = r.get('rat13_n', Decimal('0')) if includi_rat13 else Decimal('0')
    rat14_n_v = r.get('rat14_n', Decimal('0')) if includi_rat14 else Decimal('0')
    rat13_m_v = r.get('rat13_m', Decimal('0')) if includi_rat13 else Decimal('0')
    rat14_m_v = r.get('rat14_m', Decimal('0')) if includi_rat14 else Decimal('0')
    netto_con_1314 = (netto + rat13_n_v + rat14_n_v).quantize(Q2)
    lordo_base = r.get('lordo_mensile', Decimal('0'))
    lordo_con_1314 = (lordo_base + rat13_m_v + rat14_m_v).quantize(Q2)

    paga_gg_netta     = (netto_con_1314 / Decimal('26')).quantize(Q2)
    paga_ora_netta    = (netto_con_1314 / ore_mensili_r).quantize(Q2) if ore_mensili_r else Decimal('0')

    # ── Valori colonna B: netto A + TFR + ferie accantonati ───────────────────
    tfr_n_v   = r.get('tfr_n',     Decimal('0'))
    fer_n_v   = r.get('rat_fer_n', Decimal('0'))
    tot_tfr_fer = (tfr_n_v + fer_n_v).quantize(Q2)
    netto_ratei = (netto_con_1314 + tot_tfr_fer).quantize(Q2)
    paga_gg_ratei  = (netto_ratei / Decimal('26')).quantize(Q2)
    paga_ora_ratei = (netto_ratei / ore_mensili_r).quantize(Q2) if ore_mensili_r else Decimal('0')

    # ── Confronto con paga attesa ─────────────────────────────────────────────
    delta_gg = None
    delta_gg_ratei = None
    pct_gg = None
    pct_gg_ratei = None
    if paga_attesa and paga_attesa > 0:
        delta_gg        = (paga_gg_netta    - paga_attesa).quantize(Q2)
        delta_gg_ratei  = (paga_gg_ratei    - paga_attesa).quantize(Q2)
        pct_gg          = round(float(paga_gg_netta)    / float(paga_attesa) * 100, 1)
        pct_gg_ratei    = round(float(paga_gg_ratei)    / float(paga_attesa) * 100, 1)

    # ── Dettaglio voci colonna A ──────────────────────────────────────────────
    voci_a = [
        ('Paga base mensile',          r.get('paga_base',    Decimal('0'))),
        ('Contingenza',                r.get('contingenza',  Decimal('0'))),
        ('EDR',                        r.get('edr',          Decimal('0'))),
        ('Superminimo',                r.get('superminimo',  Decimal('0'))),
        ('Indennità turno',            r.get('indennita_turno', Decimal('0'))),
        ('Scatto anzianità',           r.get('scatto_anzianita', Decimal('0'))),
        ('Maggiorazione domenicale',   r.get('comp_domenicale', Decimal('0'))),
        ('Festivi lavorati',           r.get('comp_festivo',  Decimal('0'))),
        ('+ Rateo 13ª lordo (1/12)',   rat13_m_v),
        ('+ Rateo 14ª lordo (1/12)',   rat14_m_v),
        ('Lordo mensile (incl. ratei 13ª/14ª)', lordo_con_1314),
        ('— Contributi INPS (dip.)',   -r.get('inps_dip',    Decimal('0'))),
        ('Imponibile IRPEF',           r.get('imponibile_m', Decimal('0'))),
        ('— IRPEF lorda',              -r.get('irpef_lorda', Decimal('0'))),
        ('+ Detrazioni lavoro dip.',   r.get('detrazioni',   Decimal('0'))),
        ('— Addizionale regionale',    -r.get('add_reg_m',   Decimal('0'))),
        ('— Addizionale comunale',     -r.get('add_com_m',   Decimal('0'))),
        ('+ Trattamento integrativo',  r.get('ti',           Decimal('0'))),
        ('+ Bonus L207/2025',          r.get('l207',         Decimal('0'))),
        ('Netto mensile base',         netto),
    ]
    if rat13_m_v:
        voci_a.append(('+ Rateo 13ª mensile (lordo 1/12, netto stimato)', rat13_n_v))
    if rat14_m_v:
        voci_a.append(('+ Rateo 14ª mensile (lordo 1/12, netto stimato)', rat14_n_v))
    if rat13_m_v or rat14_m_v:
        voci_a.append(('NETTO MENSILE (incl. ratei 13ª/14ª)', netto_con_1314))
    else:
        voci_a[-1] = ('NETTO MENSILE', netto_con_1314)

    # ── Colonna B: TFR + ferie (13ª/14ª già in col. A) ───────────────────────
    ratei_b = [
        ('Rateo TFR mensile (netto)',   tfr_n_v),
        ('Rateo ferie mensile (netto)', fer_n_v),
        ('Tot. ratei netti (TFR + ferie)', tot_tfr_fer),
        ('NETTO + TFR + FERIE',         netto_ratei),
    ]

    context = {
        'proposta':         proposta,
        'profilo':          profilo,
        'anno':             anno,
        'mese':             mese,
        'nome_mese':        nome_mese,
        'regione':          regione,
        'num_familiari':    num_familiari,
        # risultato grezzo motore
        'r':                r,
        'lordo_con_1314':   lordo_con_1314,
        # colonna A
        'netto':            netto_con_1314,
        'paga_gg_netta':    paga_gg_netta,
        'paga_ora_netta':   paga_ora_netta,
        'voci_a':           [(n, v) for n, v in voci_a if v and v != Decimal('0')],
        # colonna B
        'netto_ratei':      netto_ratei,
        'paga_gg_ratei':    paga_gg_ratei,
        'paga_ora_ratei':   paga_ora_ratei,
        'ratei_b':          ratei_b,
        # confronto
        'paga_attesa':      paga_attesa,
        'delta_gg':         delta_gg,
        'delta_gg_ratei':   delta_gg_ratei,
        'pct_gg':           pct_gg,
        'pct_gg_ratei':     pct_gg_ratei,
        # helper formattazione
        'ore_mensili_r':    ore_mensili_r,
        'divisore_str':     divisore_str,
    }

    return render(
        request,
        'rapporto_di_lavoro/simulazione_economica_proposta.html',
        context,
    )
