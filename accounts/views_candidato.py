import logging
import math
from decimal import Decimal, ROUND_HALF_UP
from datetime import date as dt_date
from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Q
from django.http import JsonResponse
from django.shortcuts import render, redirect, get_object_or_404
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from .models import ProfiloCandidato, RichiestaIntegrazioneCandidato
from .forms import ProfiloCandidatoForm
from .utils import (
    checklist_richiesta_integrazione,
    get_richiesta_integrazione_attiva,
    get_ultima_richiesta_integrazione,
)
from .dipendente_portale import get_dipendente_collegato

logger = logging.getLogger('django')


def _tipi_richiesta_portale(user):
    tipi = [
        ('generica', 'Richiesta informazioni / chiarimento'),
        ('altro', 'Altro'),
    ]
    if getattr(user, 'ruolo', None) == 'dipendente':
        tipi = [
            ('ferie', 'Ferie'),
            ('permesso', 'Permesso'),
            ('malattia', 'Segnalazione malattia'),
        ] + tipi
    return tipi


def _ensure_dipendente_richieste(user):
    dip = _get_dipendente(user)
    if dip:
        return dip

    profilo = getattr(user, 'profilo_candidato', None)
    if profilo:
        _sincronizza_dipendente(user, profilo)
        if profilo.dipendente_id:
            profilo.save(update_fields=['dipendente'])
            return profilo.dipendente
    return None


def _crea_richiesta_portale(request, user, prefisso_messaggi='Richiesta'):
    from richieste.models import Richiesta

    dip = _ensure_dipendente_richieste(user)
    if not dip:
        messages.error(request, 'Impossibile associare un profilo anagrafico alla richiesta.')
        return None

    tipi_ammessi = {key for key, _ in _tipi_richiesta_portale(user)}
    tipi_con_date = {'ferie', 'permesso', 'malattia'}

    tipo = (request.POST.get('tipo') or '').strip()
    motivo = (request.POST.get('motivo') or '').strip()
    testo = (request.POST.get('testo_richiesta') or '').strip()
    data_inizio = request.POST.get('data_inizio') or None
    data_fine = request.POST.get('data_fine') or None

    if tipo not in tipi_ammessi:
        messages.error(request, 'Tipo di richiesta non consentito per il tuo profilo.')
        return None

    if tipo in tipi_con_date:
        if getattr(user, 'ruolo', None) != 'dipendente':
            messages.error(request, 'Ferie, permessi e malattia sono disponibili solo per i dipendenti.')
            return None
        if not (data_inizio and data_fine):
            messages.error(request, 'Inserisci il periodo della richiesta.')
            return None
        if data_fine < data_inizio:
            messages.error(request, 'La data finale non può essere precedente alla data iniziale.')
            return None
    else:
        if not testo:
            messages.error(request, 'Inserisci il testo della richiesta di informazioni.')
            return None

    richiesta = Richiesta.objects.create(
        dipendente=dip,
        azienda=dip.azienda,
        tipo=tipo,
        data_inizio=data_inizio,
        data_fine=data_fine,
        motivo=motivo,
        testo_richiesta=testo,
        richiesta_da=user,
    )
    messages.success(request, f'{prefisso_messaggi} inviata con successo.')
    return richiesta


def _eventi_documento_contratto(contratto, limite=20):
    from storico.models import EventoStorico

    filtri = Q(descrizione__icontains=contratto.numero_contratto)
    proposta = getattr(contratto, 'proposta_origine', None)
    if proposta and proposta.numero_proposta:
        filtri |= Q(descrizione__icontains=proposta.numero_proposta)

    return (
        EventoStorico.objects.filter(
            dipendente=contratto.dipendente,
            azienda=contratto.azienda,
        )
        .filter(filtri)
        .order_by('-data_evento')[:limite]
    )


def _carica_parametri_ccnl_db():
    """
    Carica dal DB gli stessi coefficienti usati dalla simulazione 2026.
    Restituisce un dict con coeff_tfr, coeff_13, coeff_14, inps_dip_perc.
    """
    from rapporto_di_lavoro.models import CCNL, ParametroRatei, ParametroContributi
    from django.utils import timezone as tz

    anno = tz.now().year

    # Valori di fallback identici alla simulazione 2026
    params = {
        'coeff_tfr':      Decimal('0.0691'),
        'coeff_13':       Decimal('1') / Decimal('12'),
        'coeff_14':       Decimal('1') / Decimal('12'),
        'inps_dip_perc':  Decimal('0.0936'),
    }

    try:
        ccnl = CCNL.objects.get(sigla='FIPE')
    except Exception:
        return params

    r_tfr = ParametroRatei.objects.filter(
        ccnl=ccnl, anno=anno, tipo_rateo='tfr', attivo=True).first()
    if r_tfr:
        params['coeff_tfr'] = r_tfr.coefficiente / Decimal('100')

    r_13 = ParametroRatei.objects.filter(
        ccnl=ccnl, anno=anno, tipo_rateo='tredicesima', attivo=True).first()
    if r_13:
        params['coeff_13'] = r_13.coefficiente / Decimal('12')

    r_14 = ParametroRatei.objects.filter(
        ccnl=ccnl, anno=anno, tipo_rateo='quattordicesima', attivo=True).first()
    if r_14:
        params['coeff_14'] = r_14.coefficiente / Decimal('12')

    pc = ParametroContributi.objects.filter(
        ccnl=ccnl, anno=anno, tipo_contributo='inps', attivo=True).first()
    if pc:
        params['inps_dip_perc'] = pc.aliquota_dipendente / Decimal('100')

    return params


def _calcola_retributivi(lordo_mensile, ore_mensili, ore_giornaliere,
                         ore_settimanali=None,
                         tredicesima=True, quattordicesima=False):
    """
    Calcola valori economici giornalieri/orari usando gli stessi parametri
    DB della simulazione 2026 (ParametroRatei / ParametroContributi FIPE).
    Include Trattamento Integrativo DL3/2020 e Bonus L207/2024 come la simulazione.
    Restituisce il dettaglio fiscale completo (INPS, IRPEF, detrazioni, bonus, addizionali).
    """
    from rapporto_di_lavoro.utils_calcoli import (
        calcola_irpef_lorda, calcola_detrazioni,
        calcola_trattamento_integrativo, calcola_bonus_l207_2024,
        calcola_addizionale_regionale_sicilia, calcola_addizionale_comunale_stima,
    )
    from django.utils import timezone as tz
    Q2 = Decimal('0.01')
    Q4 = Decimal('0.0001')

    p = _carica_parametri_ccnl_db()
    anno = tz.now().year

    lordo = Decimal(str(lordo_mensile))
    ore_m = Decimal(str(ore_mensili)) if ore_mensili else Decimal('173.33')
    ore_g = Decimal(str(ore_giornaliere)) if ore_giornaliere else Decimal('8')
    ore_s = Decimal(str(ore_settimanali)) if ore_settimanali else (ore_m * 12 / 52).quantize(Q2)
    giorni_m = (ore_m / ore_g).quantize(Q2)

    # ── Calcolo fiscale ─────────────────────────────────────────────────────
    # Stesso metodo della simulazione 2026 (aliquota INPS da DB CCNL FIPE)
    inps_dip = (lordo * p['inps_dip_perc']).quantize(Q2)
    imponibile = (lordo - inps_dip).quantize(Q2)
    imponibile_annuo = float(imponibile) * 12

    irpef_lorda_val = Decimal(str(calcola_irpef_lorda(float(imponibile), anno=anno)))
    detrazioni_val  = Decimal(str(calcola_detrazioni(float(imponibile), anno=anno)))
    irpef_netta     = max(irpef_lorda_val - detrazioni_val, Decimal('0'))
    netto_base      = (lordo - inps_dip - irpef_netta).quantize(Q2)

    # ── Bonus fiscali (DL 3/2020 e L. 207/2024) ─────────────────────────────
    # Aggiunti al netto: non concorrono a INPS, IRPEF, TFR, 13ª, 14ª
    # Sono crediti d'imposta anticipati dal datore e recuperati in F24
    tratt_integrativo = calcola_trattamento_integrativo(imponibile_annuo, anno)
    bonus_l207        = calcola_bonus_l207_2024(imponibile_annuo, anno)
    netto_mensile = (netto_base + tratt_integrativo + bonus_l207).quantize(Q2)

    # ── Addizionali regionali e comunali (stima — versate anno successivo) ───
    addiz_reg_annuo = calcola_addizionale_regionale_sicilia(imponibile_annuo, anno=anno)
    addiz_com_annuo = calcola_addizionale_comunale_stima(imponibile_annuo, anno=anno)
    addiz_reg_m = (addiz_reg_annuo / Decimal('12')).quantize(Q2)
    addiz_com_m = (addiz_com_annuo / Decimal('12')).quantize(Q2)

    # ── Paga oraria / giornaliera ────────────────────────────────────────────
    paga_oraria_lorda      = (lordo / ore_m).quantize(Q4)
    paga_giornaliera_lorda = (paga_oraria_lorda * ore_g).quantize(Q2)
    # netto_mensile include TI e Bonus L207 → paga giornaliera li include correttamente
    paga_oraria_netta      = (netto_mensile / ore_m).quantize(Q4)
    paga_giornaliera_netta = (paga_oraria_netta * ore_g).quantize(Q2)

    # Ratio per ratei netti — usa SOLO netto_base (senza TI/L207):
    # TI e Bonus L207 sono crediti mensili ordinari; NON si applicano a 13ª, 14ª, TFR
    # (13ª/14ª: tassazione marginale; TFR: tassazione separata art. 2120 c.c.)
    ratio_ratei = (netto_base / lordo).quantize(Decimal('0.000001')) if lordo else Decimal('0')

    # ── Ratei lordi mensili ──────────────────────────────────────────────────
    rateo_13_lordo_m  = (lordo * p['coeff_13']).quantize(Q2) if tredicesima else Decimal('0')
    rateo_14_lordo_m  = (lordo * p['coeff_14']).quantize(Q2) if quattordicesima else Decimal('0')
    rateo_tfr_lordo_m = (lordo * p['coeff_tfr']).quantize(Q2)

    rateo_13_lordo_g  = (rateo_13_lordo_m  / giorni_m).quantize(Q2) if giorni_m else Decimal('0')
    rateo_14_lordo_g  = (rateo_14_lordo_m  / giorni_m).quantize(Q2) if giorni_m else Decimal('0')
    rateo_tfr_lordo_g = (rateo_tfr_lordo_m / giorni_m).quantize(Q2) if giorni_m else Decimal('0')

    # Ratei netti: tassazione proporzionale senza bonus (ratio_ratei)
    rateo_13_netto_g  = (rateo_13_lordo_g  * ratio_ratei).quantize(Q2)
    rateo_14_netto_g  = (rateo_14_lordo_g  * ratio_ratei).quantize(Q2)
    rateo_tfr_netto_g = (rateo_tfr_lordo_g * ratio_ratei).quantize(Q2)

    # totale_netto_g: paga giornaliera (con TI+L207) + ratei netti (senza TI+L207)
    totale_netto_g = (
        paga_giornaliera_netta + rateo_13_netto_g + rateo_14_netto_g + rateo_tfr_netto_g
    ).quantize(Q2)

    ore_g_int = int(ore_g)
    ore_g_min = int(((ore_g - ore_g_int) * 60).quantize(Decimal('1'), rounding='ROUND_HALF_UP'))
    ore_giornaliere_hhmm = f"{ore_g_int}:{ore_g_min:02d}"

    # Soglie di eleggibilità bonus (per spiegazione in template)
    _ti_eligible  = imponibile_annuo <= 28000
    _l207_eligible = Decimal('8500') < Decimal(str(imponibile_annuo)) <= Decimal('20000')

    return {
        # ── Retribuzione base ─────────────────────────────────────────────
        'lordo_mensile':          lordo,
        'netto_mensile':          netto_mensile,
        # ── Dettaglio fiscale mensile (stesso motore simulazione 2026) ───
        'inps_dip_perc':          p['inps_dip_perc'],
        'inps_dipendente':        inps_dip,
        'imponibile_fiscale':     imponibile,
        'imponibile_annuo':       Decimal(str(round(imponibile_annuo, 2))),
        'irpef_lorda':            irpef_lorda_val,
        'detrazioni':             detrazioni_val,
        'irpef_netta':            irpef_netta,
        'netto_base':             netto_base,
        # ── Bonus fiscali ────────────────────────────────────────────────
        'trattamento_integrativo': tratt_integrativo,
        'bonus_l207':             bonus_l207,
        'ti_eligible':            _ti_eligible,
        'l207_eligible':          _l207_eligible,
        # ── Addizionali (stima — versate anno successivo) ────────────────
        'addiz_reg_mensile':      addiz_reg_m,
        'addiz_com_mensile':      addiz_com_m,
        'addiz_totale_annuo':     (addiz_reg_annuo + addiz_com_annuo).quantize(Q2),
        # ── Tariffe orarie / giornaliere ─────────────────────────────────
        'ore_settimanali':        ore_s,
        'ore_mensili':            ore_m,
        'ore_giornaliere':        ore_g,
        'ore_giornaliere_hhmm':   ore_giornaliere_hhmm,
        'paga_oraria_lorda':      paga_oraria_lorda,
        'paga_giornaliera_lorda': paga_giornaliera_lorda,
        'paga_oraria_netta':      paga_oraria_netta,
        'paga_giornaliera_netta': paga_giornaliera_netta,
        # ── Ratei ────────────────────────────────────────────────────────
        'rateo_13_lordo_g':       rateo_13_lordo_g,
        'rateo_13_netto_g':       rateo_13_netto_g,
        'rateo_14_lordo_g':       rateo_14_lordo_g,
        'rateo_14_netto_g':       rateo_14_netto_g,
        'rateo_tfr_lordo_g':      rateo_tfr_lordo_g,
        'rateo_tfr_netto_g':      rateo_tfr_netto_g,
        'rateo_13_lordo_m':       rateo_13_lordo_m,
        'rateo_14_lordo_m':       rateo_14_lordo_m,
        'rateo_tfr_lordo_m':      rateo_tfr_lordo_m,
        'totale_netto_g':         totale_netto_g,
        'tredicesima':            tredicesima,
        'quattordicesima':        quattordicesima,
        # ── Coefficienti (trasparenza) ───────────────────────────────────
        'coeff_tfr':              p['coeff_tfr'],
        'coeff_13':               p['coeff_13'],
        'coeff_14':               p['coeff_14'],
    }


def _calc_ret_da_busta(r, tredicesima=True, quattordicesima=False):
    """
    Mappa l'output di calcola_busta_paga_mese() nel formato atteso dai template
    candidato (stesso set di chiavi di _calcola_retributivi).
    """
    Q2 = Decimal('0.01')
    Q4 = Decimal('0.0001')

    ore_m = r['ore_mensili']
    ore_g = r['ore_giornaliere']
    giorni_m = (ore_m / ore_g).quantize(Q2) if ore_g else Decimal('26')
    ratio = r['ratio']  # netto_base / lordo

    rat13_lordo_g     = r['rat13_gg']
    rat14_lordo_g     = (r['rat14_m'] / giorni_m).quantize(Q4) if giorni_m else Decimal('0')
    rateo_tfr_lordo_g = r['tfr_gg']

    rat13_netto_g     = (rat13_lordo_g     * ratio).quantize(Q4)
    rat14_netto_g     = (rat14_lordo_g     * ratio).quantize(Q4)
    rateo_tfr_netto_g = (rateo_tfr_lordo_g * ratio).quantize(Q4)

    paga_oraria_netta      = (r['netto_totale'] / ore_m).quantize(Q4) if ore_m else Decimal('0')
    paga_giornaliera_netta = (paga_oraria_netta * ore_g).quantize(Q2)

    totale_netto_g = (
        paga_giornaliera_netta + rat13_netto_g + rat14_netto_g + rateo_tfr_netto_g
    ).quantize(Q2)

    addiz_totale_annuo = ((r['add_reg_m'] + r['add_com_m']) * 12).quantize(Q2)

    ore_g_int = int(ore_g)
    ore_g_min = int(((ore_g - ore_g_int) * 60).quantize(Decimal('1'), rounding=ROUND_HALF_UP))
    ore_g_hhmm = f"{ore_g_int}:{ore_g_min:02d}"

    imponibile_annuo = float(r['imponibile_ann'])

    return {
        'lordo_mensile':           r['lordo_mensile'],
        'paga_base':               r['paga_base'],
        'contingenza':             r['contingenza'],
        'edr':                     r['edr'],
        'netto_mensile':           r['netto_totale'],
        'netto_base':              r['netto_base'],
        'inps_dip_perc':           r['inps_dip_perc'],
        'inps_dipendente':         r['inps_dip'],
        'imponibile_fiscale':      r['imponibile_m'],
        'imponibile_annuo':        r['imponibile_ann'],
        'irpef_lorda':             r['irpef_lorda'],
        'detrazioni':              r['detrazioni'],
        'irpef_netta':             r['irpef_netta'],
        'trattamento_integrativo': r['ti'],
        'bonus_l207':              r['l207'],
        'ti_eligible':             imponibile_annuo <= 28000,
        'l207_eligible':           8500 < imponibile_annuo <= 20000,
        'addiz_reg_mensile':       r['add_reg_m'],
        'addiz_com_mensile':       r['add_com_m'],
        'addiz_totale_annuo':      addiz_totale_annuo,
        'ore_settimanali':         r['ore_settimanali_contr'],
        'ore_mensili':             ore_m,
        'ore_giornaliere':         ore_g,
        'ore_giornaliere_hhmm':    ore_g_hhmm,
        'paga_oraria_lorda':       r['paga_oraria'],
        'paga_giornaliera_lorda':  r['paga_giornaliera'],
        'paga_oraria_netta':       paga_oraria_netta,
        'paga_giornaliera_netta':  paga_giornaliera_netta,
        'rateo_13_lordo_g':        rat13_lordo_g,
        'rateo_13_netto_g':        rat13_netto_g,
        'rateo_14_lordo_g':        rat14_lordo_g,
        'rateo_14_netto_g':        rat14_netto_g,
        'rateo_tfr_lordo_g':       rateo_tfr_lordo_g,
        'rateo_tfr_netto_g':       rateo_tfr_netto_g,
        'rateo_13_lordo_m':        r['rat13_m'],
        'rateo_14_lordo_m':        r['rat14_m'],
        'rateo_tfr_lordo_m':       r['tfr_m'],
        'totale_netto_g':          totale_netto_g,
        'tredicesima':             tredicesima,
        'quattordicesima':         quattordicesima,
        'coeff_tfr':               r['c_tfr'],
        'coeff_13':                r['c_13'],
        'coeff_14':                r['c_14'],
        'netto_con_ratei':         r['netto_con_ratei'],
        # lordo e netto totali comprensivi di ratei 13ª e 14ª
        'lordo_con_1314':          (r['lordo_mensile'] + r['rat13_m'] + r['rat14_m']).quantize(Q2),
        'netto_con_1314':          (r['netto_totale']  + r['rat13_n'] + r['rat14_n']).quantize(Q2),
    }


def _busta_per_fonte(fonte, tredicesima=True, quattordicesima=False, num_familiari_a_carico=0, regione_residenza='Sicilia'):
    """
    Chiama calcola_busta_paga_mese() usando i dati del contratto o della proposta.
    Restituisce un dict in formato calc_ret, o None in caso di errore.

    Risolve ccnl_obj (istanza CCNL) dal ParametroCCNLTurismo per abilitare
    i lookup DB di ParametroContributi, ParametroRatei, ParametroMaggiorazione.
    """
    from rapporto_di_lavoro.models import ParametroCCNLTurismo, CCNL, RuoloOrganico2026, ParametroScattiAnnuali
    from rapporto_di_lavoro.utils_motore_paga import calcola_busta_paga_mese as _calcola_busta
    from django.utils import timezone as tz
    oggi = tz.now().date()

    data_inizio_rapporto = getattr(fonte, 'data_inizio_rapporto', None)
    data_fine_rapporto = getattr(fonte, 'data_fine_rapporto', None)

    # Usa parametro_ccnl diretto (PropostaAssunzione) oppure lookup per livello
    cp = getattr(fonte, 'parametro_ccnl', None)
    if not cp:
        livello = getattr(fonte, 'livello_ccnl', '') or ''
        if livello:
            _qs_cp = ParametroCCNLTurismo.objects.filter(livello=livello, attivo=True)
            if data_inizio_rapporto:
                _qs_cp = _qs_cp.filter(decorrenza_validita_da__lte=data_inizio_rapporto)
            cp = _qs_cp.order_by('-decorrenza_validita_da').first()
    if not cp:
        return None

    # Risolve ccnl_obj: necessario per ParametroContributi, ParametroRatei,
    # ParametroMaggiorazione. ParametroCCNLTurismo.ccnl è un CharField;
    # cerchiamo la corrispondente istanza CCNL per sigla.
    ccnl_obj = getattr(fonte, 'ccnl', None)  # se fonte ha già il FK
    if ccnl_obj is None or not hasattr(ccnl_obj, 'sigla'):
        # Mappa il campo stringa cp.ccnl → oggetto CCNL
        # Per ora tutti i ParametroCCNLTurismo sono FIPE; fallback su sigla='FIPE'
        try:
            ccnl_sigla = 'FIPE'
            ccnl_obj = CCNL.objects.filter(sigla=ccnl_sigla).first()
        except Exception:
            ccnl_obj = None

    # Divisore: usa ore_mensili dal parametro CCNL (es. 173 → divisore orario)
    divisore_str = str(round(float(cp.ore_mensili))) if cp.ore_mensili else '26'

    # Extra variabili da profilo SIM2026 (se il contratto/proposta deriva da SIM2026)
    superminimo = Decimal('0')
    indennita_turno = Decimal('0')
    indennita_extra = Decimal('0')
    scatto_anzianita = Decimal('0')

    proposta_origine = getattr(fonte, 'proposta_origine', None)
    numero_rif = (getattr(proposta_origine, 'numero_proposta', None) or getattr(fonte, 'numero_proposta', None) or '').strip()
    if not numero_rif:
        _nc = (getattr(fonte, 'numero_contratto', None) or '').strip()
        if _nc.startswith('CTR-SIM2026-'):
            # CTR-SIM2026-1-1-8-YYYYmmddHHMMSS -> SIM2026-1-1-8
            _parts = _nc.split('-')
            if len(_parts) >= 5:
                numero_rif = '-'.join(_parts[1:5])

    if numero_rif.startswith('SIM2026-'):
        try:
            _parts = numero_rif.split('-')
            _rid = int(_parts[1])
            _ruolo = RuoloOrganico2026.objects.filter(
                azienda=getattr(fonte, 'azienda', None),
                ordinamento=_rid - 1,
            ).first()
            if _ruolo:
                superminimo = Decimal(str(_ruolo.superminimo or 0))
                indennita_turno = Decimal(str(_ruolo.indennita_turno or 0))
                if hasattr(_ruolo, 'indennita_extra'):
                    indennita_extra = Decimal(str(getattr(_ruolo, 'indennita_extra') or 0))

                # Scatti: somma delle soglie <= anni anzianità
                _anno_ref = (data_inizio_rapporto.year if data_inizio_rapporto else oggi.year)
                _scatti = ParametroScattiAnnuali.objects.filter(
                    ccnl=ccnl_obj,
                    anno=_anno_ref,
                    attivo=True,
                    livello=_ruolo.livello,
                ).order_by('anni_anzianita')
                _anni = int(_ruolo.anni_anzianita or 0)
                scatto_anzianita = sum(
                    (Decimal(str(s.importo_scatto or 0)) for s in _scatti if int(s.anni_anzianita or 0) <= _anni),
                    Decimal('0')
                )
        except Exception:
            logger.exception('Errore risoluzione profilo SIM2026 in _busta_per_fonte')

    try:
        r13_imp = bool(getattr(fonte, 'tredicesima_rateo_mensile_in_imponibile', False))
        r14_imp = bool(getattr(fonte, 'quattordicesima_rateo_mensile_in_imponibile', False))
        r = _calcola_busta(
            parametro_ccnl=cp,
            tipo_contratto=getattr(fonte, 'tipo_contratto', None),
            anno=oggi.year,
            mese=oggi.month,
            azienda=getattr(fonte, 'azienda', None),
            data_inizio_rapporto=data_inizio_rapporto,
            data_fine_rapporto=data_fine_rapporto,
            divisore_str=divisore_str,
            superminimo=superminimo,
            indennita_turno=indennita_turno,
            scatto_anzianita=scatto_anzianita,
            indennita_extra=indennita_extra,
            ccnl_obj=ccnl_obj,
            num_familiari_a_carico=num_familiari_a_carico,
            regione_residenza=regione_residenza or 'Sicilia',
            rateo_13_mensile_in_imponibile=r13_imp,
            rateo_14_mensile_in_imponibile=r14_imp,
        )
        return _calc_ret_da_busta(r, tredicesima, quattordicesima)
    except Exception:
        logger.exception('Errore motore_paga in _busta_per_fonte')
        return None


def _richiede_candidato(view_func):
    """Decoratore: accesso per utenti con ruolo 'candidato' o 'dipendente'."""
    from functools import wraps
    @wraps(view_func)
    @login_required
    def wrapper(request, *args, **kwargs):
        user = request.user
        test_usernames = _geo_test_usernames()
        if user.username in test_usernames:
            return view_func(request, *args, **kwargs)
        if user.is_superuser or (hasattr(user, 'has_ruolo') and (
            user.has_ruolo('admin') or user.has_ruolo('hr') or user.has_ruolo('consulente')
        )):
            return view_func(request, *args, **kwargs)
        # Controlla tramite metodo has_ruolo (ManyToMany)
        if not (hasattr(user, 'has_ruolo') and (user.has_ruolo('candidato') or user.has_ruolo('dipendente'))):
            from django.http import HttpResponseForbidden
            return HttpResponseForbidden("Area riservata ai dipendenti e candidati.")
        return view_func(request, *args, **kwargs)
    return wrapper


def _get_dipendente(user):
    """Restituisce il Dipendente collegato all'utente (portale candidato/dipendente)."""
    return get_dipendente_collegato(user)


def _geo_test_usernames():
    """Usernames abilitati alla modalità test locale geotimbratura."""
    raw = getattr(settings, 'PRESENZE_GEO_TEST_USERNAMES', ['test.geo.presenze', 'geo.test.presenze'])
    if isinstance(raw, str):
        return {x.strip() for x in raw.split(',') if x.strip()}
    if isinstance(raw, (list, tuple, set)):
        return {str(x).strip() for x in raw if str(x).strip()}
    return {'test.geo.presenze', 'geo.test.presenze'}


def _is_geo_test_user(user):
    return bool(user and user.is_authenticated and user.username in _geo_test_usernames())


def _get_geo_config_value(field_name, settings_name, default=None):
    """Legge la configurazione geotimbratura da DB con fallback ai settings."""
    try:
        from accounts.models import ConfigurazioneSistema
        config = ConfigurazioneSistema.get()
        value = getattr(config, field_name, None)
        if value is not None:
            return value
    except Exception:
        pass
    return getattr(settings, settings_name, default)


def _to_float_or_none(val):
    try:
        if val is None or str(val).strip() == '':
            return None
        return float(str(val).strip())
    except (TypeError, ValueError):
        return None


def _haversine_m(lat1, lon1, lat2, lon2):
    """Distanza in metri tra due coordinate GPS."""
    r = 6371000.0
    p1 = math.radians(lat1)
    p2 = math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def _append_note_safe(base_note, extra):
    """Accoda info compatte nel campo note senza superare 255 caratteri."""
    base = (base_note or '').strip()
    ex = (extra or '').strip()
    if not ex:
        return base
    out = f"{base} | {ex}" if base else ex
    return out[:255]


def _log_tentativo_timbratura(*, request, dipendente, azione, esito, motivo='', lat=None, lon=None, distanza=None, raggio=None):
    """Registra tentativi timbratura su LogAttivita (utile per contestazioni)."""
    from log_attivita.utils import registra_log

    descrizione = (
        f"[TIMBRATURA_GEO] dip_id={getattr(dipendente, 'pk', '-')}; azione={azione}; esito={esito}; "
        f"motivo={motivo or '-'}; lat={lat if lat is not None else '-'}; lon={lon if lon is not None else '-'}; "
        f"distanza_m={distanza if distanza is not None else '-'}; raggio_m={raggio if raggio is not None else '-'}"
    )
    registra_log(
        request.user,
        getattr(dipendente, 'azienda', None),
        'presenza',
        descrizione,
        getattr(dipendente, 'pk', None),
        request=request,
    )


def _ha_timbrature_reali(presenza) -> bool:
    """True se la presenza contiene già tracce di timbratura reale (src=...)."""
    return 'src=' in (presenza.note or '')


def _reset_presenza_fittizia(presenza):
    """Azzera eventuali orari precompilati/fittizi per sostituirli con timbrature reali."""
    presenza.ora_entrata = None
    presenza.ora_uscita = None
    presenza.ora_entrata2 = None
    presenza.ora_uscita2 = None
    presenza.ora_entrata3 = None
    presenza.ora_uscita3 = None


def _get_contratto_attivo(user):
    """Restituisce il RapportoDiLavoro sottoscritto dell'utente, o None.
    Cerca via dipendente.utente, poi via ProfiloCandidato.dipendente, poi via _get_dipendente."""
    from rapporto_di_lavoro.models import RapportoDiLavoro
    qs = RapportoDiLavoro.objects.select_related('tipo_contratto', 'azienda', 'dipendente')
    c = qs.filter(dipendente__utente=user, stato='sottoscritto').first()
    if c:
        return c
    dip = _get_dipendente(user)
    if dip:
        return qs.filter(dipendente=dip, stato='sottoscritto').first()
    return None


@_richiede_candidato
def candidato_dashboard(request):
    """Dashboard personale: modalità candidato o dipendente."""
    user = request.user
    profilo = getattr(user, 'profilo_candidato', None)

    from rapporto_di_lavoro.models import PropostaAssunzione, RapportoDiLavoro

    # Recupera proposte: cerca prima via dipendente.utente, poi via _get_dipendente
    proposte = PropostaAssunzione.objects.filter(
        dipendente__utente=user
    ).order_by('-data_creazione')
    if not proposte.exists():
        _dip_prop = _get_dipendente(user)
        if _dip_prop:
            proposte = PropostaAssunzione.objects.filter(
                dipendente=_dip_prop
            ).order_by('-data_creazione')

    # Contratto già firmato (dipendente attivo)
    contratto = _get_contratto_attivo(user)
    e_dipendente = (contratto is not None) or (getattr(user, 'ruolo', None) == 'dipendente')

    # Proposta in attesa di firma digitale da parte del candidato
    proposta_da_firmare = None
    contratto_da_firmare = None  # mantenuto per compatibilità template
    if not e_dipendente:
        proposta_da_firmare = proposte.filter(stato='inviata_candidato').first()
        # Fallback: contratto legacy in stato 'proposta' (vecchio flusso)
        if not proposta_da_firmare:
            _qs_cf = RapportoDiLavoro.objects.select_related('tipo_contratto', 'azienda', 'dipendente')
            contratto_da_firmare = _qs_cf.filter(dipendente__utente=user, stato='proposta').first()
            if not contratto_da_firmare:
                _dip_cf = _get_dipendente(user)
                if _dip_cf:
                    contratto_da_firmare = _qs_cf.filter(dipendente=_dip_cf, stato='proposta').first()

    # Contatori rapidi per la dashboard
    richieste_in_attesa = 0
    buste_paga_count = 0
    cud_count = 0
    documenti_personali_count = 0
    documenti_count = 0
    presenze_mese = 0

    # Cerca il Dipendente collegato (via profilo o direttamente via utente)
    dip_per_contatori = _get_dipendente(user)

    if dip_per_contatori:
        dip = dip_per_contatori
        from richieste.models import Richiesta
        from documenti.models import Documento
        from presenze.models import Presenza
        from django.utils import timezone as tz
        oggi = tz.now().date()

        buste_paga_count = Documento.objects.filter(dipendente=dip, tipo='busta_paga').count()
        cud_count = Documento.objects.filter(
            dipendente=dip, tipo='certificato', visibile_al_dipendente=True
        ).count()
        documenti_personali_count = Documento.objects.filter(
            dipendente=dip, caricato_dal_dipendente=True, visibile_al_dipendente=True
        ).count()
        documenti_count = (
            Documento.objects.filter(dipendente=dip)
            .filter(Q(visibile_al_dipendente=True) | Q(tipo='busta_paga'))
            .count()
        )

        if e_dipendente:
            richieste_in_attesa = Richiesta.objects.filter(
                dipendente=dip, stato='inviata'
            ).count()
            presenze_mese = Presenza.objects.filter(
                dipendente=dip,
                data__year=oggi.year,
                data__month=oggi.month,
            ).count()

    # Calcolo retributivo con motore paga condiviso (stesso engine di Simulatore Paga)
    calc_ret = None

    _num_fam = int(profilo.num_familiari_a_carico or 0) if profilo else 0
    _regione = (profilo.regione_residenza or 'Sicilia') if profilo else 'Sicilia'
    if contratto:
        calc_ret = _busta_per_fonte(
            contratto,
            tredicesima=contratto.tredicesima,
            quattordicesima=contratto.quattordicesima,
            num_familiari_a_carico=_num_fam,
            regione_residenza=_regione,
        )
    elif contratto_da_firmare:
        calc_ret = _busta_per_fonte(
            contratto_da_firmare,
            tredicesima=contratto_da_firmare.tredicesima,
            quattordicesima=contratto_da_firmare.quattordicesima,
            num_familiari_a_carico=_num_fam,
            regione_residenza=_regione,
        )
    else:
        stati_firmata_equivalenti = PropostaAssunzione.stati_equivalenti('firmata_candidato')
        stati_contratto_equivalenti = PropostaAssunzione.stati_equivalenti('contratto_attivo')
        p_fonte = proposte.filter(
            stato__in=[
                *stati_firmata_equivalenti,
                *stati_contratto_equivalenti,
            ]
        ).first()
        if p_fonte:
            calc_ret = _busta_per_fonte(p_fonte, num_familiari_a_carico=_num_fam, regione_residenza=_regione)

    # Data per il calcolo del primo mese nel riepilogo:
    # priorità alla data_disponibilita del profilo (campo modificabile dal dipendente),
    # fallback su data_inizio_rapporto del contratto se il profilo non ha data.
    data_inizio_validita = None
    origine_data = None
    if profilo and profilo.data_disponibilita:
        data_inizio_validita = profilo.data_disponibilita
        origine_data = 'disponibilita'
    elif contratto and contratto.data_inizio_rapporto:
        data_inizio_validita = contratto.data_inizio_rapporto
        origine_data = 'contratto'

    # Calcolo proporzionale primo mese (se non inizia dal 1°)
    import calendar as _cal
    import datetime as _dt
    calc_ret_primo_mese = None
    if calc_ret and data_inizio_validita and data_inizio_validita.day != 1:
        _anno_d = data_inizio_validita.year
        _mese_d = data_inizio_validita.month
        _giorni_nel_mese = _cal.monthrange(_anno_d, _mese_d)[1]
        _giorni_lavorati = _giorni_nel_mese - data_inizio_validita.day + 1
        _fraz = Decimal(str(_giorni_lavorati)) / Decimal(str(_giorni_nel_mese))
        # In ristorazione si lavora lun-dom festivi inclusi; i giorni lavorativi
        # del periodo coincidono con i giorni di calendario (chiusure aziendali escluse)
        _data_fine_mese = _dt.date(_anno_d, _mese_d, _giorni_nel_mese)
        calc_ret_primo_mese = {
            'giorni_lavorati':    _giorni_lavorati,   # giorni calendario = lavorativi
            'giorni_nel_mese':    _giorni_nel_mese,
            'data_inizio':        data_inizio_validita,
            'data_fine':          _data_fine_mese,
            'lordo':  (calc_ret['lordo_mensile']  * _fraz).quantize(Decimal('0.01')),
            'netto':  (calc_ret['netto_mensile']  * _fraz).quantize(Decimal('0.01')),
        }

    preview_parametri_ferie_rol = None
    portale_maturazione_ferie_rol = None
    ratei_mensili_riepilogo = None

    if dip_per_contatori and contratto:
        try:
            from rapporto_di_lavoro.calcolatore_ferie_rol_bridge import (
                preview_parametri_calcolatore_ferie_rol,
            )

            preview_parametri_ferie_rol = preview_parametri_calcolatore_ferie_rol(
                dip_per_contatori,
                contratto.azienda,
                timezone.now().date(),
            )
        except Exception:
            preview_parametri_ferie_rol = None
        if e_dipendente:
            try:
                from presenze.maturazione_griglia_utils import calcolo_maturazione_griglia_mese

                oggi = timezone.now().date()
                portale_maturazione_ferie_rol = calcolo_maturazione_griglia_mese(
                    dip_per_contatori,
                    dip_per_contatori.azienda,
                    oggi.year,
                    oggi.month,
                )
            except Exception:
                portale_maturazione_ferie_rol = None

    if calc_ret and contratto:
        try:
            lordo = calc_ret.get('lordo_mensile')
            nb = calc_ret.get('netto_base')
            lordo_d = lordo if isinstance(lordo, Decimal) else Decimal(str(lordo or 0))
            nb_d = nb if isinstance(nb, Decimal) else Decimal(str(nb or 0))
            ratio = (nb_d / lordo_d) if lordo_d and lordo_d != 0 else Decimal('0')
            r13 = calc_ret.get('rateo_13_lordo_m')
            r14 = calc_ret.get('rateo_14_lordo_m')
            rtfr = calc_ret.get('rateo_tfr_lordo_m')
            r13 = r13 if isinstance(r13, Decimal) else Decimal(str(r13 or 0))
            r14 = r14 if isinstance(r14, Decimal) else Decimal(str(r14 or 0))
            rtfr = rtfr if isinstance(rtfr, Decimal) else Decimal(str(rtfr or 0))
            ratei_mensili_riepilogo = {
                'r13_lordo_m': r13,
                'r14_lordo_m': r14,
                'rtfr_lordo_m': rtfr,
                'r13_netto_m': (r13 * ratio).quantize(Decimal('0.01')),
                'r14_netto_m': (r14 * ratio).quantize(Decimal('0.01')),
                'rtfr_netto_m': (rtfr * ratio).quantize(Decimal('0.01')),
            }
        except Exception:
            ratei_mensili_riepilogo = None

    from accounts.utils import controlla_completezza_profilo
    completezza = controlla_completezza_profilo(profilo)
    richiesta_integrazione_attiva = get_richiesta_integrazione_attiva(user)
    ultima_richiesta_integrazione = get_ultima_richiesta_integrazione(user)
    checklist_integrazione = checklist_richiesta_integrazione(
        richiesta_integrazione_attiva or ultima_richiesta_integrazione,
        profilo,
    )

    # Avvisi richieste inviate/ricevute
    from richieste.models import Richiesta

    if dip_per_contatori:
        richieste_inviate_qs = Richiesta.objects.filter(dipendente=dip_per_contatori)
    else:
        richieste_inviate_qs = Richiesta.objects.filter(richiesta_da=user)

    richieste_con_risposta_qs = richieste_inviate_qs.exclude(stato='inviata').order_by('-data_risposta', '-data_richiesta')
    richieste_con_risposta_count = richieste_con_risposta_qs.count()
    richieste_con_risposta_recenti = list(richieste_con_risposta_qs[:3])

    richieste_ricevute_datore_qs = RichiestaIntegrazioneCandidato.objects.filter(candidato=user).order_by('-data_invio')
    richieste_ricevute_datore_count = richieste_ricevute_datore_qs.count()
    richiesta_ricevuta_attiva = richieste_ricevute_datore_qs.filter(stato__in=['inviata', 'completata_candidato']).first()

    ctx = {
        'profilo': profilo,
        'proposte': proposte,
        'email_verificata': user.email_verificata,
        'profilo_completato': profilo.profilo_completato if profilo else False,
        'convalidato': user.convalidato,
        'e_dipendente': e_dipendente,
        'contratto': contratto,
        'proposta_da_firmare': proposta_da_firmare,
        'contratto_da_firmare': contratto_da_firmare,
        'richieste_in_attesa': richieste_in_attesa,
        # Stesso dipendente usato per i contatori: link calendario presenze coerenti con buste/CUD
        'dipendente_portale': dip_per_contatori,
        'buste_paga_count': buste_paga_count,
        'cud_count': cud_count,
        'documenti_personali_count': documenti_personali_count,
        'documenti_count': documenti_count,
        'presenze_mese': presenze_mese,
        'calc_ret': calc_ret,
        'calc_ret_primo_mese': calc_ret_primo_mese,
        'data_inizio_validita': data_inizio_validita,
        'origine_data': origine_data,
        'completezza': completezza,
        'richiesta_integrazione_attiva': richiesta_integrazione_attiva,
        'ultima_richiesta_integrazione': ultima_richiesta_integrazione,
        'checklist_integrazione': checklist_integrazione,
        'richieste_con_risposta_count': richieste_con_risposta_count,
        'richieste_con_risposta_recenti': richieste_con_risposta_recenti,
        'richieste_ricevute_datore_count': richieste_ricevute_datore_count,
        'richiesta_ricevuta_attiva': richiesta_ricevuta_attiva,
        'preview_parametri_ferie_rol': preview_parametri_ferie_rol,
        'portale_maturazione_ferie_rol': portale_maturazione_ferie_rol,
        'ratei_mensili_riepilogo': ratei_mensili_riepilogo,
    }
    return render(request, 'candidato/dashboard.html', ctx)


@_richiede_candidato
def accetta_contratto_dipendente(request, contratto_id):
    """
    Il candidato firma e accetta il contratto di assunzione.
    Cambia stato contratto → 'sottoscritto' e ruolo utente → 'dipendente'.
    """
    from rapporto_di_lavoro.models import RapportoDiLavoro
    contratto = get_object_or_404(
        RapportoDiLavoro,
        id=contratto_id,
        dipendente__utente=request.user,
        stato='proposta',
    )

    # Simulazione economica per mostrare il dettaglio retributivo al candidato
    profilo = getattr(request.user, 'profilo_candidato', None)
    _num_fam = int(profilo.num_familiari_a_carico or 0) if profilo else 0
    _regione = (profilo.regione_residenza or 'Sicilia') if profilo else 'Sicilia'
    calc_ret = _busta_per_fonte(
        contratto,
        tredicesima=contratto.tredicesima,
        quattordicesima=contratto.quattordicesima,
        num_familiari_a_carico=_num_fam,
        regione_residenza=_regione,
    )

    if request.method == 'POST':
        if not request.POST.get('accetto'):
            messages.error(request, "Devi spuntare la casella di accettazione per procedere.")
            return render(request, 'candidato/accetta_contratto.html', {
                'contratto': contratto,
                'calc_ret': calc_ret,
            })

        # Sottoscrivi il contratto
        firma_ts = timezone.now()
        contratto.stato = 'sottoscritto'
        contratto.data_sottoscrizione = firma_ts.date()
        contratto.data_ora_sottoscrizione = firma_ts
        contratto.luogo_sottoscrizione = 'Palermo'
        contratto.save(update_fields=['stato', 'data_sottoscrizione', 'data_ora_sottoscrizione', 'luogo_sottoscrizione'])

        # Transizione candidato → dipendente
        user = request.user
        # ruolo assegnato via M2M sotto
        user.azienda = contratto.azienda
        user.save(update_fields=['azienda'])
        from accounts.models import Ruolo as _Ruolo
        _r, _ = _Ruolo.objects.get_or_create(codice='dipendente', defaults={'nome': 'Dipendente'})
        user.ruoli.add(_r)

        # Aggiorna stato e data_assunzione del Dipendente in anagrafica
        dip = contratto.dipendente
        update_fields_dip = []
        if dip.stato == 'candidato':
            dip.stato = 'attivo'
            update_fields_dip.append('stato')
        if not dip.data_assunzione:
            dip.data_assunzione = contratto.data_inizio_rapporto
            update_fields_dip.append('data_assunzione')
        if update_fields_dip:
            dip.save(update_fields=update_fields_dip)

        from storico.models import EventoStorico
        proposta = getattr(contratto, 'proposta_origine', None)
        EventoStorico.objects.create(
            dipendente=contratto.dipendente,
            azienda=contratto.azienda,
            tipo='assunzione',
            data_evento=firma_ts,
            descrizione=(
                f'Contratto {contratto.numero_contratto} sottoscritto dal lavoratore '
                f'{contratto.dipendente} — {contratto.luogo_sottoscrizione}, '
                f'{firma_ts.strftime("%d/%m/%Y %H:%M")}'
                f'{f". Proposta origine: {proposta.numero_proposta}." if proposta else "."}'
            ),
        )

        logger.info(
            "Contratto %s accettato da %s — ruolo promosso a dipendente",
            contratto.numero_contratto, user.username,
        )
        messages.success(
            request,
            f"Contratto accettato con successo! Benvenuto/a in {contratto.azienda.nome}. "
            "Il tuo account è ora attivo come dipendente."
        )
        from accounts.contratto_utente_definitivo import (
            ribalta_utente_candidato_su_dipendente_se_contratto_definitivo,
        )

        ribalta_utente_candidato_su_dipendente_se_contratto_definitivo(
            dip, contratto, motivo="accetta_contratto_dipendente"
        )

        dip_id = getattr(dip, 'pk', None)
        if dip_id is None:
            return redirect('candidato_dashboard')
        return redirect('calendario_presenze', dipendente_id=dip_id)

    return render(request, 'candidato/accetta_contratto.html', {
        'contratto': contratto,
        'calc_ret': calc_ret,
    })


@_richiede_candidato
def candidato_mio_contratto(request):
    """Dettaglio del contratto del dipendente."""
    from rapporto_di_lavoro.models import RapportoDiLavoro
    dip = _get_dipendente(request.user)
    if not dip:
        messages.warning(request, "Nessun profilo dipendente collegato.")
        return redirect('candidato_dashboard')
    contratto = (
        RapportoDiLavoro.objects.filter(dipendente=dip, stato='sottoscritto')
        .select_related('tipo_contratto', 'azienda', 'proposta_origine')
        .order_by('-data_inizio_rapporto', '-id')
        .first()
    )
    if not contratto:
        messages.info(request, "Nessun contratto sottoscritto disponibile in portale.")
        return redirect('candidato_miei_documenti')

    profilo = getattr(request.user, 'profilo_candidato', None)
    _num_fam = int(profilo.num_familiari_a_carico or 0) if profilo else 0
    _regione = (profilo.regione_residenza or 'Sicilia') if profilo else 'Sicilia'
    calc_ret = _busta_per_fonte(
        contratto,
        tredicesima=contratto.tredicesima,
        quattordicesima=contratto.quattordicesima,
        num_familiari_a_carico=_num_fam,
        regione_residenza=_regione,
    )
    eventi_documento = _eventi_documento_contratto(contratto)
    return render(request, 'candidato/mio_contratto.html', {
        'contratto': contratto,
        'calc_ret': calc_ret,
        'profilo': profilo,
        'eventi_documento': eventi_documento,
    })


@_richiede_candidato
def candidato_mie_buste_paga(request):
    """Redirect unico verso l'area documenti (tab buste / CUD / altri)."""
    tab = (request.GET.get('tab') or 'buste').strip().lower()
    if tab not in ('buste', 'cud', 'altri', 'contratti'):
        tab = 'buste'
    return redirect(f"{reverse('candidato_miei_documenti')}?tab={tab}")


@_richiede_candidato
def candidato_mie_presenze(request):
    """Presenze del mese del dipendente."""
    from presenze.models import Presenza
    from django.utils import timezone as tz
    from rapporto_di_lavoro.models import RapportoDiLavoro
    dip = _get_dipendente(request.user)
    if not dip:
        messages.warning(request, "Nessun profilo dipendente collegato.")
        return redirect('candidato_dashboard')

    # Data di inizio validità: dal contratto sottoscritto, altrimenti dalla disponibilità del profilo
    contratto_dip = RapportoDiLavoro.objects.filter(
        dipendente=dip, stato='sottoscritto'
    ).order_by('data_inizio_rapporto').first()
    profilo_utente = getattr(request.user, 'profilo_candidato', None)
    if contratto_dip and contratto_dip.data_inizio_rapporto:
        data_inizio_validita = contratto_dip.data_inizio_rapporto
        origine_data = 'contratto'
    elif profilo_utente and profilo_utente.data_disponibilita:
        data_inizio_validita = profilo_utente.data_disponibilita
        origine_data = 'disponibilita'
    else:
        data_inizio_validita = None
        origine_data = None

    oggi = tz.now().date()
    # Mese di default: mese corrente, ma non prima della data di inizio
    default_anno = oggi.year
    default_mese = oggi.month
    if data_inizio_validita:
        if (default_anno, default_mese) < (data_inizio_validita.year, data_inizio_validita.month):
            default_anno = data_inizio_validita.year
            default_mese = data_inizio_validita.month
    anno = int(request.GET.get('anno', default_anno))
    mese = int(request.GET.get('mese', default_mese))

    # Blocca navigazione prima della data di inizio
    if data_inizio_validita and (anno, mese) < (data_inizio_validita.year, data_inizio_validita.month):
        anno = data_inizio_validita.year
        mese = data_inizio_validita.month

    presenze = Presenza.objects.filter(
        dipendente=dip, data__year=anno, data__month=mese
    ).order_by('data')
    # Non mostrare presenze antecedenti alla data di inizio contratto
    if data_inizio_validita:
        presenze = presenze.filter(data__gte=data_inizio_validita)

    from collections import defaultdict

    straord_tot = Decimal('0')
    straord_by_tipo = defaultdict(Decimal)
    for p in presenze:
        o = p.ore_straordinario
        if o is None:
            continue
        odec = o if isinstance(o, Decimal) else Decimal(str(o))
        if odec <= 0:
            continue
        straord_tot += odec
        if p.tipo_straordinario:
            straord_by_tipo[p.tipo_straordinario] += odec
    label_map = dict(Presenza.TIPO_STRAORD_CHOICES)
    straord_per_tipo = [
        {'label': label_map.get(k, k), 'ore': v}
        for k, v in sorted(straord_by_tipo.items())
    ]
    magg_straord_ccnl = None
    if contratto_dip:
        magg_straord_ccnl = {
            'diurno': contratto_dip.ore_straordinario_diurno_maggiorazione,
            'notturno': contratto_dip.ore_straordinario_notturno_maggiorazione,
            'festivo': contratto_dip.ore_straordinario_festivo_maggiorazione,
        }

    # Totale ore mese (somma turni T1/T2/T3) + righe espanse per giornata
    from datetime import datetime

    def _mins(ent, usc):
        if not ent or not usc:
            return 0
        return max(0, int((datetime.combine(oggi, usc) - datetime.combine(oggi, ent)).total_seconds() / 60))

    def _hhmm(mins):
        return f"{mins // 60:02d}:{mins % 60:02d}"

    totale_minuti = 0
    for p in presenze:
        totale_minuti += _mins(p.ora_entrata, p.ora_uscita)
        totale_minuti += _mins(p.ora_entrata2, p.ora_uscita2)
        totale_minuti += _mins(p.ora_entrata3, p.ora_uscita3)

    righe_presenze = []
    for p in presenze:
        eventi = [c.strip() for c in (p.note or '').split('|') if c.strip()]
        in_events = [e for e in eventi if e.startswith('IN@')]
        out_events = [e for e in eventi if e.startswith('OUT@')]
        progressivo_giorno = 0
        turni = [
            (1, p.ora_entrata, p.ora_uscita),
            (2, p.ora_entrata2, p.ora_uscita2),
            (3, p.ora_entrata3, p.ora_uscita3),
        ]
        ha_turni = False
        for idx, ent, usc in turni:
            if not ent and not usc:
                continue
            ha_turni = True
            parziale_min = _mins(ent, usc)
            progressivo_giorno += parziale_min
            in_evt = in_events[idx - 1] if len(in_events) >= idx else ''
            out_evt = out_events[idx - 1] if len(out_events) >= idx else ''
            rilevazione = ' | '.join([x for x in [in_evt, out_evt] if x])
            righe_presenze.append({
                'data': p.data,
                'turno': idx,
                'entrata': ent,
                'uscita': usc,
                'parziale_hhmm': _hhmm(parziale_min),
                'progressivo_hhmm': _hhmm(progressivo_giorno),
                'in_corso': bool(ent and not usc),
                'note': p.note,
                'rilevazione': rilevazione or '—',
                'mostra_note': idx == 1,
            })
        if not ha_turni:
            righe_presenze.append({
                'data': p.data,
                'turno': 1,
                'entrata': None,
                'uscita': None,
                'parziale_hhmm': '00:00',
                'progressivo_hhmm': '00:00',
                'in_corso': False,
                'note': p.note,
                'rilevazione': '—',
                'mostra_note': True,
            })
    totale_ore = totale_minuti // 60
    totale_min_rest = totale_minuti % 60
    import calendar
    mesi_nomi = ['', 'Gennaio', 'Febbraio', 'Marzo', 'Aprile', 'Maggio', 'Giugno',
                 'Luglio', 'Agosto', 'Settembre', 'Ottobre', 'Novembre', 'Dicembre']
    mese_prev = mese - 1 if mese > 1 else 12
    anno_prev = anno if mese > 1 else anno - 1
    mese_next = mese + 1 if mese < 12 else 1
    anno_next = anno if mese < 12 else anno + 1
    oggi_presenza = Presenza.objects.filter(dipendente=dip, data=tz.localdate()).first()
    geo_enabled = bool(_get_geo_config_value('presenze_geo_enabled', 'PRESENZE_GEO_ENABLED', True))
    geo_test_mode = _is_geo_test_user(request.user)
    geo_test_allow_nogps = geo_test_mode and not bool(
        _get_geo_config_value('presenze_geo_enforce_for_test', 'PRESENZE_GEO_ENFORCE_FOR_TEST', False)
    )

    # Griglia calendario mensile (stesse regole colore chiusure/festivi del modulo presenze)
    from presenze import views as presenze_views

    presenze_dict = {p.data: p for p in presenze}
    chiusura_sett = presenze_views._get_chiusura_settimanale_presenze(dip.azienda, anno, mese)
    festivi_mese = presenze_views._get_festivi_mese(anno, mese)
    griglia_calendario = presenze_views._costruisci_griglia_mese(
        anno, mese, presenze_dict, chiusura_sett, festivi_mese
    )
    for week in griglia_calendario:
        for cell in week:
            if not cell:
                continue
            ot = (cell['ore_t1'] or 0) + (cell['ore_t2'] or 0) + (cell['ore_t3'] or 0)
            cell['ore_tot_hhmm'] = presenze_views.ore_dec_to_hhmm(ot) if ot else ''
            if data_inizio_validita and cell['data'] < data_inizio_validita:
                cell['prima_validita'] = True

    return render(request, 'candidato/mie_presenze.html', {
        'presenze': presenze,
        'righe_presenze': righe_presenze,
        'griglia_calendario': griglia_calendario,
        'dipendente': dip,
        'anno': anno,
        'mese': mese,
        'mese_nome': mesi_nomi[mese],
        'totale_ore': totale_ore,
        'totale_min_rest': totale_min_rest,
        'mese_prev': mese_prev,
        'anno_prev': anno_prev,
        'mese_next': mese_next,
        'anno_next': anno_next,
        'data_inizio_validita': data_inizio_validita,
        'origine_data': origine_data,
        # True se il mese precedente è prima del mese minimo consentito
        'prev_disabilitato': bool(
            data_inizio_validita and
            (anno_prev, mese_prev) < (data_inizio_validita.year, data_inizio_validita.month)
        ),
        'oggi_presenza': oggi_presenza,
        'geo_enabled': geo_enabled,
        'geo_test_mode': geo_test_mode,
        'geo_test_allow_nogps': geo_test_allow_nogps,
        'oggi': oggi,
        'straord_tot': straord_tot,
        'straord_per_tipo': straord_per_tipo,
        'magg_straord_ccnl': magg_straord_ccnl,
    })


@_richiede_candidato
@require_POST
def candidato_timbratura_geo(request):
    """
    Timbratura da mobile con geolocalizzazione.
    - Utente test (locale): può timbrare anche senza GPS.
    - Altri utenti: GPS obbligatorio se abilitato da setting.
    """
    if not _get_geo_config_value('presenze_geo_enabled', 'PRESENZE_GEO_ENABLED', True):
        dip_tmp = _get_dipendente(request.user)
        if dip_tmp:
            _log_tentativo_timbratura(
                request=request,
                dipendente=dip_tmp,
                azione=(request.POST.get('azione') or '').strip().lower() or '-',
                esito='negato',
                motivo='geo_disabilitato',
            )
        return JsonResponse({'ok': False, 'error': 'Timbratura geolocalizzata non abilitata.'}, status=403)

    dip = _get_dipendente(request.user)
    if not dip:
        return JsonResponse({'ok': False, 'error': 'Dipendente non associato.'}, status=400)

    azione = (request.POST.get('azione') or '').strip().lower()
    if azione not in {'checkin', 'checkout'}:
        _log_tentativo_timbratura(
            request=request,
            dipendente=dip,
            azione=azione or '-',
            esito='negato',
            motivo='azione_non_valida',
        )
        return JsonResponse({'ok': False, 'error': 'Azione non valida.'}, status=400)

    lat = _to_float_or_none(request.POST.get('lat'))
    lon = _to_float_or_none(request.POST.get('lon'))
    acc = _to_float_or_none(request.POST.get('acc'))
    gps_ok = lat is not None and lon is not None and -90 <= lat <= 90 and -180 <= lon <= 180

    is_test = _is_geo_test_user(request.user)
    require_gps = bool(_get_geo_config_value('presenze_geo_require_gps', 'PRESENZE_GEO_REQUIRE_GPS', True))
    enforce_for_test = bool(
        _get_geo_config_value('presenze_geo_enforce_for_test', 'PRESENZE_GEO_ENFORCE_FOR_TEST', False)
    )
    test_exempt = is_test and not enforce_for_test

    if require_gps and not gps_ok and not test_exempt:
        _log_tentativo_timbratura(
            request=request,
            dipendente=dip,
            azione=azione,
            esito='negato',
            motivo='gps_assente',
            lat=lat,
            lon=lon,
            raggio=None,
        )
        return JsonResponse({'ok': False, 'error': 'Geolocalizzazione richiesta. Attiva il GPS e riprova.'}, status=400)

    azienda_geo = getattr(dip, 'azienda', None)
    center_lat = _to_float_or_none(getattr(azienda_geo, 'sede_lavorativa_lat', None))
    center_lon = _to_float_or_none(getattr(azienda_geo, 'sede_lavorativa_lon', None))
    radius_m = _to_float_or_none(getattr(azienda_geo, 'sede_lavorativa_raggio_m', None))

    # Fallback globale (settings) se l'azienda non ha ancora coordinate configurate
    if center_lat is None:
        center_lat = _to_float_or_none(_get_geo_config_value('presenze_geo_center_lat', 'PRESENZE_GEO_CENTER_LAT', None))
    if center_lon is None:
        center_lon = _to_float_or_none(_get_geo_config_value('presenze_geo_center_lon', 'PRESENZE_GEO_CENTER_LON', None))
    if radius_m is None:
        radius_m = _to_float_or_none(_get_geo_config_value('presenze_geo_radius_m', 'PRESENZE_GEO_RADIUS_M', 300))
    radius_m = radius_m or 300.0
    enforce_geofence = bool(
        _get_geo_config_value('presenze_geo_enforce_geofence', 'PRESENZE_GEO_ENFORCE_GEOFENCE', False)
    )

    distanza = None
    if gps_ok and center_lat is not None and center_lon is not None:
        distanza = round(_haversine_m(lat, lon, center_lat, center_lon), 1)

    if enforce_geofence and gps_ok and distanza is not None and distanza > radius_m and not test_exempt:
        _log_tentativo_timbratura(
            request=request,
            dipendente=dip,
            azione=azione,
            esito='negato',
            motivo='fuori_perimetro',
            lat=lat,
            lon=lon,
            distanza=distanza,
            raggio=radius_m,
        )
        return JsonResponse({
            'ok': False,
            'error': f'Fuori area timbratura ({int(distanza)}m > {int(radius_m)}m).',
        }, status=403)

    from presenze.models import Presenza

    today = timezone.localdate()
    now_t = timezone.localtime().time().replace(microsecond=0)
    p, _ = Presenza.objects.get_or_create(
        dipendente=dip,
        data=today,
        defaults={
            'azienda': dip.azienda,
            'causale': 'P',
            'registrata_da': request.user,
        },
    )

    if p.causale != 'P':
        causale_label = dict(Presenza.CAUSALE_CHOICES).get(p.causale, p.causale)
        _log_tentativo_timbratura(
            request=request,
            dipendente=dip,
            azione=azione,
            esito='negato',
            motivo=f'causale_non_timbrabile:{p.causale}',
            lat=lat,
            lon=lon,
            distanza=distanza,
            raggio=radius_m,
        )
        return JsonResponse({'ok': False, 'error': f'Presenza non timbrabile: causale {causale_label}.'}, status=400)

    # Se il record è precompilato/fittizio (nessuna traccia src=) lo sostituiamo
    # con dati reali al primo check-in del giorno.
    if azione == 'checkin' and not _ha_timbrature_reali(p):
        had_precompiled = any([
            p.ora_entrata, p.ora_uscita,
            p.ora_entrata2, p.ora_uscita2,
            p.ora_entrata3, p.ora_uscita3,
        ])
        if had_precompiled:
            _reset_presenza_fittizia(p)
            p.note = _append_note_safe(p.note, 'RESET_FITTIZIA')

    src = 'test-local' if is_test and not gps_ok else 'geo'
    geo_meta = [f'{"IN" if azione == "checkin" else "OUT"}@{now_t.strftime("%H:%M:%S")}', f'src={src}']
    if gps_ok:
        geo_meta.append(f'lat={lat:.6f}')
        geo_meta.append(f'lon={lon:.6f}')
    if acc is not None:
        geo_meta.append(f'acc={acc:.0f}m')
    if distanza is not None:
        geo_meta.append(f'd={distanza:.0f}m')
    p.note = _append_note_safe(p.note, ' '.join(geo_meta))
    p.registrata_da = request.user

    if azione == 'checkin':
        # Permette più ingressi/uscite nella giornata (T1, T2, T3)
        # 1) Se c'è già un turno aperto, non aprire un nuovo check-in
        if p.ora_entrata and not p.ora_uscita:
            ora_t1 = p.ora_entrata
            return JsonResponse({'ok': False, 'error': f'Turno 1 già aperto dalle {ora_t1.strftime("%H:%M")}. Registra prima il check-out.', 'stato': 'turno1_aperto'}, status=400)
        if p.ora_entrata2 and not p.ora_uscita2:
            ora_t2 = p.ora_entrata2
            return JsonResponse({'ok': False, 'error': f'Turno 2 già aperto dalle {ora_t2.strftime("%H:%M")}. Registra prima il check-out.', 'stato': 'turno2_aperto'}, status=400)
        if p.ora_entrata3 and not p.ora_uscita3:
            ora_t3 = p.ora_entrata3
            return JsonResponse({'ok': False, 'error': f'Turno 3 già aperto dalle {ora_t3.strftime("%H:%M")}. Registra prima il check-out.', 'stato': 'turno3_aperto'}, status=400)

        # 2) Apri il primo turno libero
        turno = None
        if not p.ora_entrata:
            p.ora_entrata = now_t
            turno = 1
        elif not p.ora_entrata2:
            p.ora_entrata2 = now_t
            turno = 2
        elif not p.ora_entrata3:
            p.ora_entrata3 = now_t
            turno = 3
        else:
            if is_test:
                _reset_presenza_fittizia(p)
                p.note = _append_note_safe(p.note, 'TEST_RESET_TURNI_GIORNO')
                p.ora_entrata = now_t
                turno = 1
            else:
                return JsonResponse({'ok': False, 'error': 'Limite raggiunto: massimo 3 ingressi al giorno.', 'stato': 'limite_turni'}, status=400)

        p.save()
        _log_tentativo_timbratura(
            request=request,
            dipendente=dip,
            azione='checkin',
            esito='ok',
            motivo=f'turno_{turno}',
            lat=lat,
            lon=lon,
            distanza=distanza,
            raggio=radius_m,
        )
        return JsonResponse({'ok': True, 'azione': 'checkin', 'ora': now_t.strftime('%H:%M'), 'turno': turno, 'test_mode': is_test})

    # checkout
    # Chiude il primo turno aperto (T1 -> T2 -> T3)
    turno = None
    if p.ora_entrata and not p.ora_uscita:
        p.ora_uscita = now_t
        turno = 1
    elif p.ora_entrata2 and not p.ora_uscita2:
        p.ora_uscita2 = now_t
        turno = 2
    elif p.ora_entrata3 and not p.ora_uscita3:
        p.ora_uscita3 = now_t
        turno = 3
    else:
        return JsonResponse({'ok': False, 'error': 'Nessun turno aperto da chiudere. Registra prima un check-in.', 'stato': 'nessun_turno_aperto'}, status=400)

    p.save()
    _log_tentativo_timbratura(
        request=request,
        dipendente=dip,
        azione='checkout',
        esito='ok',
        motivo=f'turno_{turno}',
        lat=lat,
        lon=lon,
        distanza=distanza,
        raggio=radius_m,
    )
    return JsonResponse({'ok': True, 'azione': 'checkout', 'ora': now_t.strftime('%H:%M'), 'turno': turno, 'test_mode': is_test})


@_richiede_candidato
def candidato_mie_richieste(request):
    """Lista richieste del dipendente."""
    from richieste.models import Richiesta
    dip = _get_dipendente(request.user)
    if dip:
        richieste = Richiesta.objects.filter(dipendente=dip).order_by('-data_richiesta')
    else:
        richieste = Richiesta.objects.filter(richiesta_da=request.user).order_by('-data_richiesta')
    # Contatori per ferie e permessi approvati
    ferie_approvate = richieste.filter(tipo='ferie', stato='approvata')
    permessi_approvati = richieste.filter(tipo='permesso', stato='approvata')
    richieste_ricevute = RichiestaIntegrazioneCandidato.objects.filter(candidato=request.user).order_by('-data_invio')
    return render(request, 'candidato/mie_richieste.html', {
        'richieste': richieste,
        'dipendente': dip,
        'ferie_approvate': ferie_approvate,
        'permessi_approvati': permessi_approvati,
        'richieste_ricevute': richieste_ricevute,
        'tipi_richiesta_disponibili': _tipi_richiesta_portale(request.user),
        'puo_richiedere_assenze': getattr(request.user, 'ruolo', None) == 'dipendente',
    })


@_richiede_candidato
def candidato_dettaglio_richiesta_ricevuta(request, richiesta_id):
    richiesta = get_object_or_404(
        RichiestaIntegrazioneCandidato,
        id=richiesta_id,
        candidato=request.user,
    )
    profilo = getattr(request.user, 'profilo_candidato', None)
    checklist = checklist_richiesta_integrazione(richiesta, profilo)

    return render(request, 'candidato/dettaglio_richiesta_ricevuta.html', {
        'richiesta': richiesta,
        'checklist': checklist,
    })


@_richiede_candidato
def candidato_nuova_richiesta(request):
    """Form per nuova richiesta dal portale candidato/dipendente."""
    dip = _ensure_dipendente_richieste(request.user)
    tipi_richiesta = _tipi_richiesta_portale(request.user)
    puo_richiedere_assenze = getattr(request.user, 'ruolo', None) == 'dipendente'

    if request.method == 'POST':
        richiesta = _crea_richiesta_portale(request, request.user)
        if richiesta:
            return redirect('candidato_mie_richieste')

    return render(request, 'candidato/nuova_richiesta.html', {
        'dipendente': dip,
        'tipi_richiesta_disponibili': tipi_richiesta,
        'puo_richiedere_assenze': puo_richiedere_assenze,
    })


def _portale_periodo_busta(doc, mov):
    """Anno/mese/etichetta periodo per una busta (movimento import o nome file / descrizione).

    Priorità: movimento import → testo con MM/YYYY → pattern file produzione ``busta_MM_YYYY`` (underscore)
    → altre forme ``busta`` + mese + anno. Solo in assenza di tutto ciò si usa la data di caricamento
    (solo come ultimo fallback, non per ordinamento cronologico del periodo retributivo).
    """
    import re

    if mov:
        pl = (mov.periodo_label or '').strip()
        if pl and re.match(r'^\d{1,2}/\d{4}$', pl):
            mm, yy = pl.split('/')
            return int(yy), int(mm), pl
        return int(mov.anno), int(mov.mese), f'{int(mov.mese):02d}/{int(mov.anno)}'
    text = f'{doc.descrizione or ""} {doc.nome_file() or ""}'
    m = re.search(r'\b(\d{1,2})\s*/\s*(\d{4})\b', text)
    if m:
        mm, yy = int(m.group(1)), int(m.group(2))
        if 1 <= mm <= 12:
            return yy, mm, f'{mm:02d}/{yy}'
    # File tipo busta_01_2024_dip_18_p1.pdf (mese/anno nel nome; dopo l'anno può esserci _dip…)
    m = re.search(r'(?i)busta[_\s-](\d{1,2})[_\s-](\d{4})(?:_|\.|$|\s)', text)
    if m:
        mm, yy = int(m.group(1)), int(m.group(2))
        if 1 <= mm <= 12 and 1990 <= yy <= 2100:
            return yy, mm, f'{mm:02d}/{yy}'
    # Variante compatta busta012024…
    m = re.search(r'(?i)busta(\d{2})(\d{4})(?:_|\.|$)', text.replace(' ', ''))
    if m:
        mm, yy = int(m.group(1)), int(m.group(2))
        if 1 <= mm <= 12:
            return yy, mm, f'{mm:02d}/{yy}'
    dc = doc.data_caricamento
    d = dc.date() if hasattr(dc, 'date') and callable(getattr(dc, 'date', None)) else dc
    return int(d.year), int(d.month), d.strftime('%m/%Y')


def _portale_group_by_year(rows, month_desc: bool, key_year='anno'):
    """Raggruppa righe per anno (anno intero); ordina i mesi dentro l'anno (recente prima se month_desc)."""
    from collections import defaultdict
    from decimal import Decimal

    def _period_sort_key(r):
        """Ordine solo per periodo retributivo (anno/mese), poi id documento — non data di caricamento."""
        yy = int(r.get(key_year) or 0)
        mm = int(r.get('mese') or 1)
        mm = max(1, min(12, mm))
        # Chiave numerica monotona: 202604 > 202603; stesso periodo → id documento maggiore prima (più recente)
        period_score = yy * 100 + mm
        return (period_score, int(r['doc'].pk))

    by_year = defaultdict(list)
    for row in rows:
        raw_y = row[key_year]
        yk = int(raw_y) if raw_y is not None else 0
        by_year[yk].append(row)
    for y in by_year:
        by_year[y].sort(key=_period_sort_key, reverse=month_desc)
    years = sorted(by_year.keys(), reverse=True)
    out = []
    for y in years:
        rlist = by_year[y]
        lordi = [r['lordo'] for r in rlist if r.get('lordo') is not None]
        netti = [r['netto'] for r in rlist if r.get('netto') is not None]
        sum_lordo = sum(lordi, Decimal('0')) if lordi else None
        sum_netto = sum(netti, Decimal('0')) if netti else None
        out.append({
            'anno': int(y),
            'collapse_suffix': int(y),
            'rows': rlist,
            'n_docs': len(rlist),
            'sum_lordo': sum_lordo,
            'sum_netto': sum_netto,
            'has_amounts': bool(lordi or netti),
        })
    return out


def _portale_group_contratti_mixed(rows, month_desc: bool):
    """Raggruppa righe contratto (rapporto + documento) per anno; ordine interno dal più recente se month_desc."""
    from collections import defaultdict

    by_year = defaultdict(list)
    for row in rows:
        yk = int(row['anno'])
        by_year[yk].append(row)
    for y in by_year:
        by_year[y].sort(
            key=lambda r: (r['sort_dt'], r.get('rapporto') and r['rapporto'].pk or 0, r.get('doc') and r['doc'].pk or 0),
            reverse=month_desc,
        )
    years = sorted(by_year.keys(), reverse=True)
    out = []
    for y in years:
        rlist = by_year[y]
        out.append({
            'anno': int(y),
            'collapse_suffix': int(y),
            'rows': rlist,
            'n_docs': len(rlist),
        })
    return out


def _portale_paginate_year_groups(year_groups, page_num: int, per_page: int):
    """Paginazione su lista di blocchi anno (ogni elemento è un dict con 'anno', 'rows', …)."""
    from django.core.paginator import EmptyPage, PageNotAnInteger, Paginator

    if not year_groups:
        class _EmptyPage:
            object_list = []
            has_other_pages = False
            number = 1
            has_previous = False
            has_next = False
            previous_page_number = 1
            next_page_number = 1
            paginator = Paginator([], per_page)

        return _EmptyPage()

    paginator = Paginator(year_groups, per_page)
    try:
        return paginator.page(page_num)
    except PageNotAnInteger:
        return paginator.page(1)
    except EmptyPage:
        return paginator.page(paginator.num_pages)


@_richiede_candidato
def candidato_miei_documenti(request):
    """Documenti del dipendente: Buste / CUD / Contratti / Altri, per anno con collapse e paginazione."""
    from accounts.models import MovimentoImportPaghe
    from documenti.models import Documento
    from rapporto_di_lavoro.models import RapportoDiLavoro

    dip = _get_dipendente(request.user)
    if not dip:
        messages.warning(request, "Nessun profilo dipendente collegato.")
        return redirect('candidato_dashboard')

    month_desc = request.GET.get('mese_ord', 'recenti') != 'vecchi'
    active_tab = (request.GET.get('tab') or 'buste').strip().lower()
    if active_tab not in ('buste', 'cud', 'altri', 'contratti'):
        active_tab = 'buste'

    try:
        page_num = max(1, int(request.GET.get('page') or 1))
    except (TypeError, ValueError):
        page_num = 1
    years_per_page = 4

    documenti = list(
        Documento.objects.filter(dipendente=dip)
        .filter(
            Q(visibile_al_dipendente=True)
            | Q(tipo='busta_paga')
            | Q(tipo='contratto'),
        )
        .order_by('-data_caricamento')
    )
    buste_ids = [d.id for d in documenti if d.tipo == 'busta_paga']
    mov_by_doc = {}
    if buste_ids:
        # Non filtrare su dipendente: può essere null su import legacy; il documento è già scoped al dipendente.
        for m in MovimentoImportPaghe.objects.filter(
            tipo='BUSTA', documento_id__in=buste_ids
        ).order_by('-updated_at', '-id'):
            if m.documento_id not in mov_by_doc:
                mov_by_doc[m.documento_id] = m

    buste_rows = []
    for doc in documenti:
        if doc.tipo != 'busta_paga':
            continue
        mov = mov_by_doc.get(doc.id)
        anno, mese, periodo_label = _portale_periodo_busta(doc, mov)
        lordo = None
        netto = None
        if mov:
            lordo = mov.importo_lordo
            netto = mov.importo_netto
            if netto is None and mov.importo is not None:
                netto = mov.importo
        # Escludi PDF «busta» senza movimento import e senza importi (duplicati / righe fantasma)
        # Mostra sempre la busta se c’è un movimento import o un file PDF (senza import restano importi «—»)
        if mov is None and lordo is None and netto is None:
            if not (doc.file and doc.file.name):
                continue
        buste_rows.append({
            'doc': doc,
            'mov': mov,
            'anno': anno,
            'mese': mese,
            'periodo_label': periodo_label,
            'lordo': lordo,
            'netto': netto,
            'natura_sort': (mov.natura_busta if mov else '') or '',
            'natura_display': mov.get_natura_busta_display() if mov else '—',
        })

    cud_rows = []
    altri_rows = []
    contratti_doc_rows = []
    for doc in documenti:
        if doc.tipo == 'certificato':
            dc = doc.data_caricamento
            d = dc.date() if hasattr(dc, 'date') else dc
            titolo = (doc.descrizione or '').strip() or doc.nome_file()
            cud_rows.append({
                'doc': doc,
                'anno': d.year,
                'mese': d.month,
                'titolo': titolo,
                'data_caricamento': dc,
            })
        elif doc.tipo == 'contratto':
            dc = doc.data_caricamento
            d = dc.date() if hasattr(dc, 'date') else dc
            titolo = (doc.descrizione or '').strip() or doc.nome_file()
            contratti_doc_rows.append({
                'kind': 'documento',
                'doc': doc,
                'anno': d.year,
                'mese': d.month,
                'titolo': titolo,
                'data_caricamento': dc,
                'sort_dt': d,
            })
        elif doc.tipo != 'busta_paga':
            dc = doc.data_caricamento
            d = dc.date() if hasattr(dc, 'date') else dc
            titolo = (doc.descrizione or '').strip() or doc.nome_file()
            altri_rows.append({
                'doc': doc,
                'anno': d.year,
                'mese': d.month,
                'titolo': titolo,
                'tipo_display': doc.get_tipo_display(),
                'data_caricamento': dc,
            })

    cud_by_year = []
    from collections import defaultdict

    by_y = defaultdict(list)
    for r in cud_rows:
        by_y[r['anno']].append(r)
    for y in sorted(by_y.keys(), reverse=True):
        lst = by_y[y]
        lst.sort(key=lambda r: (r['mese'], r['data_caricamento']), reverse=month_desc)
        cud_by_year.append({'anno': int(y), 'collapse_suffix': int(y), 'rows': lst, 'n_docs': len(lst)})

    by_y2 = defaultdict(list)
    for r in altri_rows:
        by_y2[r['anno']].append(r)
    altri_by_year = []
    for y in sorted(by_y2.keys(), reverse=True):
        lst = by_y2[y]
        lst.sort(key=lambda r: (r['mese'], r['data_caricamento']), reverse=month_desc)
        altri_by_year.append({'anno': int(y), 'collapse_suffix': int(y), 'rows': lst, 'n_docs': len(lst)})

    buste_by_year = _portale_group_by_year(buste_rows, month_desc)

    contratti_mixed = []
    for rap in RapportoDiLavoro.objects.filter(dipendente=dip).select_related(
        'tipo_contratto', 'azienda', 'proposta_origine'
    ).order_by('-data_inizio_rapporto', '-id'):
        d0 = rap.data_inizio_rapporto
        dc_created = rap.data_creazione
        sort_dt = d0 or (dc_created.date() if hasattr(dc_created, 'date') else dc_created)
        contratti_mixed.append({
            'kind': 'rapporto',
            'rapporto': rap,
            'anno': (d0.year if d0 else dc_created.year),
            'mese': (d0.month if d0 else dc_created.month),
            'sort_dt': sort_dt,
        })
    contratti_mixed.extend(contratti_doc_rows)
    contratti_by_year = _portale_group_contratti_mixed(contratti_mixed, month_desc)

    contratto_portale_pk = RapportoDiLavoro.objects.filter(
        dipendente=dip, stato='sottoscritto'
    ).order_by('-data_inizio_rapporto', '-id').values_list('pk', flat=True).first()

    buste_page = _portale_paginate_year_groups(buste_by_year, page_num, years_per_page)
    cud_page = _portale_paginate_year_groups(cud_by_year, page_num, years_per_page)
    altri_page = _portale_paginate_year_groups(altri_by_year, page_num, years_per_page)
    contratti_page = _portale_paginate_year_groups(contratti_by_year, page_num, years_per_page)

    has_rapporti = RapportoDiLavoro.objects.filter(dipendente=dip).exists()

    miei_documenti_url = reverse('candidato_miei_documenti')
    miei_documenti_next_contratti_pdf = f'{miei_documenti_url}?tab=contratti&page={page_num}'

    return render(
        request,
        'candidato/miei_documenti.html',
        {
            'dipendente': dip,
            'buste_by_year': buste_page.object_list,
            'buste_page': buste_page,
            'cud_by_year': cud_page.object_list,
            'cud_page': cud_page,
            'altri_by_year': altri_page.object_list,
            'altri_page': altri_page,
            'contratti_by_year': contratti_page.object_list,
            'contratti_page': contratti_page,
            'contratto_portale_pk': contratto_portale_pk,
            'page_num': page_num,
            'years_per_page': years_per_page,
            'month_order_desc': month_desc,
            'active_tab': active_tab,
            'has_any_documento': bool(documenti) or has_rapporti,
            'miei_documenti_url': miei_documenti_url,
            'miei_documenti_next_contratti_pdf': miei_documenti_next_contratti_pdf,
        },
    )


@_richiede_candidato
def candidato_completa_profilo(request):
    """Step 2 — Completamento dati anagrafici del candidato."""
    user = request.user
    profilo, _ = ProfiloCandidato.objects.get_or_create(user=user)

    e_dipendente = getattr(user, 'ruolo', None) == 'dipendente'
    richiesta_integrazione_attiva = get_richiesta_integrazione_attiva(user)
    ultima_richiesta_integrazione = get_ultima_richiesta_integrazione(user)
    tipi_richiesta_disponibili = _tipi_richiesta_portale(user)
    puo_richiedere_assenze = getattr(user, 'ruolo', None) == 'dipendente'
    richieste_recenti = []

    dip_richieste = _get_dipendente(user)
    from richieste.models import Richiesta

    if dip_richieste:
        richieste_recenti = list(
            Richiesta.objects.filter(dipendente=dip_richieste).order_by('-data_richiesta')[:5]
        )
    else:
        richieste_recenti = list(
            Richiesta.objects.filter(richiesta_da=user).order_by('-data_richiesta')[:5]
        )

    if request.method == 'POST' and request.POST.get('invia_richiesta_profilo'):
        richiesta = _crea_richiesta_portale(request, user, prefisso_messaggi='Richiesta dal profilo')
        if richiesta:
            return redirect('candidato_completa_profilo')
        form = ProfiloCandidatoForm(instance=profilo)
    elif request.method == 'POST':
        form = ProfiloCandidatoForm(request.POST, request.FILES, instance=profilo)
        if form.is_valid():
            p = form.save(commit=False)

            # Prima volta: imposta data completamento
            if not p.profilo_completato:
                p.data_completamento = timezone.now()
            p.profilo_completato = True

            # Crea/aggiorna il Dipendente PRIMA di p.save()
            # così dipendente_id viene incluso nello stesso save
            _sincronizza_dipendente(user, p)

            p.save()
            form.save_m2m()

            # ── Salva eventuali documenti allegati ───────────────────────
            dip = p.dipendente
            if dip:
                from documenti.models import Documento
                _TIPI_UPLOAD = [
                    ('doc_curriculum',  'curriculum',  'Curriculum vitae'),
                    ('doc_attestato',   'attestato',   'Attestato professionale'),
                    ('doc_abilitazione','abilitazione','Abilitazione tecnica'),
                    ('doc_titolo',      'titolo_studio','Titolo di studio'),
                ]
                for field_name, tipo, label_default in _TIPI_UPLOAD:
                    file_obj = request.FILES.get(field_name)
                    if file_obj:
                        descrizione = request.POST.get(f'{field_name}_desc', '').strip() or label_default
                        Documento.objects.create(
                            azienda=dip.azienda,
                            dipendente=dip,
                            tipo=tipo,
                            descrizione=descrizione,
                            file=file_obj,
                            caricato_da=user,
                            caricato_dal_dipendente=True,
                            visibile_al_dipendente=True,
                        )

            if richiesta_integrazione_attiva and request.POST.get('conferma_integrazione'):
                checklist = checklist_richiesta_integrazione(richiesta_integrazione_attiva, p)
                if checklist['completa']:
                    richiesta_integrazione_attiva.stato = 'completata_candidato'
                    richiesta_integrazione_attiva.conferma_candidato = True
                    richiesta_integrazione_attiva.note_candidato = (request.POST.get('note_integrazione_candidato') or '').strip()
                    richiesta_integrazione_attiva.data_completamento_candidato = timezone.now()
                    richiesta_integrazione_attiva.save(update_fields=[
                        'stato', 'conferma_candidato', 'note_candidato', 'data_completamento_candidato'
                    ])
                    from .views_admin_candidati import _notifica_hr_integrazione_completata

                    _notifica_hr_integrazione_completata(request, richiesta_integrazione_attiva)
                    messages.success(
                        request,
                        'Profilo aggiornato e integrazione confermata. L’ufficio HR è stato avvisato per la revisione finale.'
                    )
                    return redirect('candidato_dashboard')

                messages.warning(
                    request,
                    'Profilo salvato, ma l’integrazione non è ancora completa. Mancano: ' + ', '.join(checklist['mancanti']) + '.'
                )
                return redirect('candidato_completa_profilo')

            messages.success(
                request,
                "Profilo salvato con successo! "
                "Il tuo dossier è ora visibile all'ufficio HR per la valutazione."
            )
            return redirect('candidato_dashboard')
    else:
        form = ProfiloCandidatoForm(instance=profilo)

    # Carica proposta e contratto collegati per il riepilogo in fondo alla pagina
    from rapporto_di_lavoro.models import PropostaAssunzione, RapportoDiLavoro
    proposta = None
    contratto = None
    if profilo.dipendente:
        proposta = (
            PropostaAssunzione.objects
            .filter(dipendente=profilo.dipendente)
            .exclude(stato='rifiutata_dipendente')
            .order_by('-data_creazione')
            .first()
        )
        contratto = (
            RapportoDiLavoro.objects
            .filter(dipendente=profilo.dipendente)
            .order_by('-data_creazione')
            .first()
        )

    from accounts.utils import controlla_completezza_profilo
    completezza = controlla_completezza_profilo(profilo)
    checklist_integrazione = checklist_richiesta_integrazione(
        richiesta_integrazione_attiva or ultima_richiesta_integrazione,
        profilo,
    )

    return render(request, 'candidato/completa_profilo.html', {
        'form': form,
        'profilo': profilo,
        'proposta': proposta,
        'contratto': contratto,
        'e_dipendente': e_dipendente,
        'completezza': completezza,
        'richiesta_integrazione_attiva': richiesta_integrazione_attiva,
        'ultima_richiesta_integrazione': ultima_richiesta_integrazione,
        'checklist_integrazione': checklist_integrazione,
        'tipi_richiesta_disponibili': tipi_richiesta_disponibili,
        'puo_richiedere_assenze': puo_richiedere_assenze,
        'richieste_recenti': richieste_recenti,
    })


def _sincronizza_dipendente(user, profilo):
    """
    Crea o aggiorna il record Dipendente (stato='candidato')
    collegato al ProfiloCandidato.
    """
    from accounts.sync_anagrafica import sincronizza_dipendente_da_profilo

    sincronizza_dipendente_da_profilo(user, profilo)


@_richiede_candidato
def candidato_ruoli_disponibili(request):
    """Posizioni aperte — riservata solo ad admin/HR. Candidati reindirizzati alla dashboard."""
    from django.http import HttpResponseForbidden
    return HttpResponseForbidden("Questa funzione è riservata all'ufficio HR.")
    user = request.user
    profilo = getattr(user, 'profilo_candidato', None)

    from rapporto_di_lavoro.models import PropostaAssunzione, SimulazioneOrganico

    # Proposte bozza disponibili (dipendente.stato='candidato' e senza utente reale)
    from anagrafiche.models import Dipendente
    proposte_aperte = PropostaAssunzione.objects.filter(
        stato='bozza',
        dipendente__stato='candidato',
        dipendente__utente__isnull=True,
    ).select_related('azienda', 'dipendente', 'tipo_contratto')

    # Filtra per azienda di interesse se impostata
    azienda_filtro = profilo.azienda_interesse if profilo else None
    if azienda_filtro:
        proposte_aperte = proposte_aperte.filter(azienda=azienda_filtro)

    # Ultima simulazione disponibile
    simulazione = None
    if azienda_filtro:
        simulazione = SimulazioneOrganico.objects.filter(
            azienda=azienda_filtro
        ).order_by('-data_creazione').first()
    else:
        simulazione = SimulazioneOrganico.objects.order_by('-data_creazione').first()

    ctx = {
        'profilo': profilo,
        'proposte_aperte': proposte_aperte,
        'simulazione': simulazione,
        'azienda_filtro': azienda_filtro,
    }
    return render(request, 'candidato/ruoli_disponibili.html', ctx)


@_richiede_candidato
def candidato_esprimi_interesse(request, proposta_id):
    """Il candidato esprime interesse per una proposta aperta."""
    from rapporto_di_lavoro.models import PropostaAssunzione
    proposta = get_object_or_404(
        PropostaAssunzione,
        id=proposta_id,
        stato='bozza',
        dipendente__stato='candidato',
        dipendente__utente__isnull=True,
    )
    profilo = getattr(request.user, 'profilo_candidato', None)
    if not profilo or not profilo.profilo_completato:
        messages.warning(request, "Completa prima il tuo profilo per esprimere interesse.")
        return redirect('candidato_completa_profilo')

    if request.method == 'POST':
        # Collega il dipendente del candidato alla proposta
        if profilo.dipendente:
            proposta.dipendente = profilo.dipendente
            proposta.save(update_fields=['dipendente'])
            messages.success(
                request,
                f"Hai espresso interesse per la posizione: "
                f"Livello {proposta.livello_ccnl} — {proposta.azienda.nome}. "
                f"L'ufficio HR ti contatterà a breve."
            )
            logger.info(
                f"[INTERESSE] {request.user.email} → Proposta {proposta.numero_proposta}"
            )
        return redirect('candidato_dashboard')

    return render(request, 'candidato/esprimi_interesse.html', {
        'proposta': proposta,
        'profilo': profilo,
    })
