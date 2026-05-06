"""
Simulatore Motore Paga Mensile — vista principale dell'applicazione.
Calcolo completo delegato a utils_motore_paga.calcola_busta_paga_mese().
"""
from __future__ import annotations
from datetime import date
from decimal import Decimal

from django.contrib.auth.decorators import login_required
from django.shortcuts import render
from django.utils import timezone as _tz

Q2 = Decimal('0.01')
Q4 = Decimal('0.0001')

SESSION_KEY = 'simulatore_paga_form'
MESI_NOMI = [
    '', 'Gennaio', 'Febbraio', 'Marzo', 'Aprile', 'Maggio', 'Giugno',
    'Luglio', 'Agosto', 'Settembre', 'Ottobre', 'Novembre', 'Dicembre',
]
DIVISORI = [
    ('26',     'FIPE / Turismo (÷ 26 gg)'),
    ('172',    'Standard 40h/sett (÷ 172 h/mese)'),
    ('173.33', 'Standard 40h/sett (÷ 173,33 h/mese)'),
]


def _divisore_tabellare_da_parametro_ccnl(cp) -> str | None:
    """Divisore orario da ``ore_mensili`` del parametro (≥160), altrimenti None."""
    if not cp or not getattr(cp, 'ore_mensili', None):
        return None
    try:
        om_dec = Decimal(str(cp.ore_mensili))
    except Exception:
        return None
    if om_dec < Decimal('160'):
        return None
    om = float(cp.ore_mensili)
    if abs(om - 173.33) < 0.06:
        return '173.33'
    return str(int(round(om)))


def _utente_puo_vedere_dipendente_sim(request, dip) -> bool:
    u = request.user
    if not u.is_authenticated:
        return False
    if u.is_superuser:
        return True
    try:
        if u.has_ruolo('admin') or u.has_ruolo('hr'):
            from accounts.tenant import get_azienda_operativa
            az = get_azienda_operativa(u, request.session)
            return az is None or dip.azienda_id == az.id
        if u.has_ruolo('consulente') and getattr(u, 'azienda_id', None):
            return dip.azienda_id == u.azienda_id
        if getattr(dip, 'utente_id', None) == u.id:
            return True
        profilo = getattr(u, 'profilo_candidato', None)
        if profilo and getattr(profilo, 'dipendente_id', None) == dip.id:
            return True
    except Exception:
        return False
    return False


def _elenco_proposte_prefill_simulatore(request):
    """Proposte recenti (non convertite) per prefill candidati / bozze."""
    from .models import PropostaAssunzione
    from accounts.tenant import get_azienda_operativa

    u = request.user
    if not u.is_authenticated:
        return []
    finiti = (
        'contratto_attivo', 'convertita_in_contratto', 'rifiutata_candidato',
        'rifiutata_admin', 'rifiutata_dipendente',
    )
    qs = (
        PropostaAssunzione.objects.select_related('dipendente', 'azienda')
        .exclude(stato__in=finiti)
        .order_by('-data_modifica')[:40]
    )
    if u.is_superuser:
        return list(qs)
    az = get_azienda_operativa(u, request.session)
    if az:
        return list(qs.filter(azienda=az))
    if getattr(u, 'has_ruolo', lambda _c: False)('candidato') or u.has_ruolo('dipendente'):
        return list(
            PropostaAssunzione.objects.select_related('dipendente', 'azienda')
            .filter(dipendente__utente=u)
            .exclude(stato__in=finiti)
            .order_by('-data_modifica')[:25]
        )
    return []


def _json_prefill_da_contratto_dipendente(request, dip_id: int, anno: int, mese: int) -> dict:
    from anagrafiche.models import Dipendente
    from .risoluzione_contratto_motore import (
        anni_di_servizio,
        build_scatti_db,
        calcola_scatto_totale_maturato,
        rapporto_sottoscritto_attivo_nel_mese,
        risolvi_parametro_ccnl_per_mese,
        superminimo_da_rapporto_o_ruolo,
    )

    try:
        dip = Dipendente.objects.get(pk=dip_id)
    except Dipendente.DoesNotExist:
        return {'ok': False, 'errore': 'Dipendente non trovato'}
    if not _utente_puo_vedere_dipendente_sim(request, dip):
        return {'ok': False, 'errore': 'Non autorizzato a consultare questo dipendente'}
    rapporto = rapporto_sottoscritto_attivo_nel_mese(
        dipendente=dip, azienda=dip.azienda, anno=anno, mese=mese,
    )
    if not rapporto:
        return {
            'ok': False,
            'errore': (
                'Nessun contratto sottoscritto attivo nel mese selezionato per questo dipendente '
                '(verifica date rapporto o stato contratto).'
            ),
        }
    primo_m = date(anno, mese, 1)
    cp, _f = risolvi_parametro_ccnl_per_mese(
        rapporto=rapporto,
        data_primo_giorno_mese=primo_m,
        livello_fallback=(dip.livello or '').strip(),
    )
    if not cp:
        return {
            'ok': False,
            'errore': f'Parametro CCNL non trovato per livello «{(rapporto.livello_ccnl or "").strip() or "?"}».',
        }
    tc = rapporto.tipo_contratto
    sm_contr = superminimo_da_rapporto_o_ruolo(
        rapporto=rapporto, ruolo_superminimo=Decimal('0'),
    ).quantize(Q2)
    div = _divisore_tabellare_da_parametro_ccnl(cp)
    if not div:
        div = '172'
    from .parametro_ccnl_voci_retributive import risolvi_ccnl_modello_da_parametro

    _ccnl_obj = risolvi_ccnl_modello_da_parametro(cp)
    anni_srv = anni_di_servizio(rapporto.data_inizio_rapporto, primo_m)
    livello_eff = (rapporto.livello_ccnl or dip.livello or '').strip()
    scatti_db = build_scatti_db(_ccnl_obj, anno) if _ccnl_obj else {}
    scatto_m = calcola_scatto_totale_maturato(livello_eff, anni_srv, scatti_db).quantize(Q2)

    payload = {
        'ok': True,
        'fonte': 'contratto',
        'messaggio': f'Contratto {rapporto.numero_contratto} — livello {rapporto.livello_ccnl}',
        'parametro_ccnl': str(cp.pk),
        'tipo_contratto': str(tc.pk),
        'azienda': str(dip.azienda_id),
        'dipendente_id': str(dip.pk),
        'data_inizio_rapporto': rapporto.data_inizio_rapporto.isoformat(),
        'data_fine_rapporto': rapporto.data_fine_rapporto.isoformat() if rapporto.data_fine_rapporto else '',
        'superminimo': str(sm_contr),
        'divisore': div,
        'usa_dati_contratto': '1',
        'rateo_13_mensile_in_imponibile': '1' if rapporto.tredicesima_rateo_mensile_in_imponibile else '0',
        'rateo_14_mensile_in_imponibile': '1' if rapporto.quattordicesima_rateo_mensile_in_imponibile else '0',
        'scatto_maturato_mese_hint': str(scatto_m),
    }
    try:
        from documenti.models import CedolinoMotoreV4

        ced = (
            CedolinoMotoreV4.objects
            .filter(dipendente_id=dip.pk, anno=anno, mese=mese)
            .order_by('-id')
            .prefetch_related('voci')
            .first()
        )
        if ced is not None:
            _sm_da_ced = False
            voci_by_cod = {}
            for vv in ced.voci.all():
                key = (vv.codice or '').strip()
                if key:
                    voci_by_cod.setdefault(key, []).append(vv)

            def _sum_ore(*codes: str) -> Decimal:
                tot = Decimal('0')
                for code in codes:
                    for row in voci_by_cod.get(code, []):
                        tot += Decimal(str(row.ore_gg or 0))
                return tot.quantize(Q2)

            def _sum_imp(*codes: str) -> Decimal:
                tot = Decimal('0')
                for code in codes:
                    for row in voci_by_cod.get(code, []):
                        tot += Decimal(str(row.importo or 0))
                return tot.quantize(Q2)

            def _sum_ore_desc_contains(*needles: str) -> Decimal:
                nrm = tuple((n or '').strip().lower() for n in needles if (n or '').strip())
                if not nrm:
                    return Decimal('0.00')
                tot = Decimal('0')
                for rows in voci_by_cod.values():
                    for row in rows:
                        desc = (row.descrizione or '').strip().lower()
                        if all(tok in desc for tok in nrm):
                            tot += Decimal(str(row.ore_gg or 0))
                return tot.quantize(Q2)

            coeff = Decimal(str(tc.coefficiente_ore or 1))
            div_dec = Decimal(str(div))

            def _dced(x) -> Decimal:
                try:
                    return Decimal(str(x or 0))
                except Exception:
                    return Decimal('0')

            # €/h in cedolino = (Sm_ref × coeff) / divisore → Sm_ref mensile = (h × divisore) / coeff
            super_h = _dced(getattr(ced, 'superminimo_imp', 0))
            if sm_contr <= 0 and super_h > 0 and div_dec > 0 and coeff > 0:
                sm_m_ref = (super_h * div_dec / coeff).quantize(Q2) if coeff < Decimal('1') else (super_h * div_dec).quantize(Q2)
                payload['superminimo'] = str(sm_m_ref)
                _sm_da_ced = True
            elif sm_contr <= 0 and div_dec > Decimal('30') and coeff > 0:
                # Cedolino importato: a volte EL.SAN / scatti / superminimo non sono in colonna (0 in DB)
                # ma «retrib. di fatto» è corretta — ricostruisci il superminimo orario come residuo.
                rof_ced = _dced(getattr(ced, 'retrib_di_fatto', 0) or getattr(ced, 'retr_oraria_att', 0))
                if rof_ced > 0:
                    h_pb = _dced(ced.paga_base)
                    h_ct = _dced(ced.contingenza)
                    h_es = _dced(ced.el_dis_san)
                    if h_es <= 0:
                        h_es = _dced(getattr(cp, 'elemento_distinto_sanita', 0))
                    h_eb = _dced(ced.el_dis_bil)
                    if h_eb <= 0:
                        h_eb = _dced(getattr(cp, 'elemento_distinto_bilateralita', 0))
                    h_sc = _dced(ced.scatti_anz_imp)
                    if h_sc <= 0 and scatto_m > 0:
                        h_sc = (scatto_m / div_dec).quantize(Q4)
                    base_oraria = (h_pb + h_ct + h_es + h_sc + h_eb).quantize(Q4)
                    super_h_res = (rof_ced - base_oraria).quantize(Q4)
                    if super_h_res > Decimal('0.005'):
                        sm_m_ref = (
                            (super_h_res * div_dec / coeff).quantize(Q2)
                            if coeff < Decimal('1')
                            else (super_h_res * div_dec).quantize(Q2)
                        )
                        payload['superminimo'] = str(sm_m_ref)
                        _sm_da_ced = True
            ore_ord = _sum_ore('8001')
            ore_dom = _sum_ore('8010', '8011')
            if ore_ord > 0:
                payload['ore_ordinarie_retribuite'] = str(ore_ord)
            if ore_dom > 0:
                payload['ore_domenicali'] = str(ore_dom)
            ore_fest = _sum_ore('8020')
            if ore_fest > 0:
                payload['ore_festivi_lavorati'] = str(ore_fest)
            ore_st_nott = _sum_ore('8030')
            if ore_st_nott <= 0:
                ore_st_nott = _sum_ore_desc_contains('straord', 'nott')
            if ore_st_nott > 0:
                payload['ore_straord_notturno'] = str(ore_st_nott)

            ore_st_dom = _sum_ore_desc_contains('straord', 'domen')
            ore_st_fest = _sum_ore_desc_contains('straord', 'festiv')
            ore_st_nf = _sum_ore_desc_contains('straord', 'nott', 'fest')
            # Straord. diurno: fallback su altre competenze 80xx non mappate esplicitamente.
            # Se il codice ha descrizione "straord." prova a tenere una distinzione minima.
            ore_st_diur = Decimal('0')
            for code, rows in voci_by_cod.items():
                if not (code.isdigit() and 8000 <= int(code) <= 8099):
                    continue
                if code in {'8001', '8010', '8011', '8020', '8030'}:
                    continue
                for row in rows:
                    desc = (row.descrizione or '').strip().lower()
                    ore = Decimal(str(row.ore_gg or 0))
                    if ore <= 0:
                        continue
                    if 'straord' in desc:
                        if 'nott' in desc and 'fest' in desc:
                            ore_st_nf += ore
                        elif 'domen' in desc:
                            ore_st_dom += ore
                        elif 'fest' in desc:
                            ore_st_fest += ore
                        elif 'nott' in desc:
                            ore_st_nott += ore
                        else:
                            ore_st_diur += ore
            if ore_st_diur > 0:
                payload['ore_straord_diurno'] = str(ore_st_diur.quantize(Q2))
            if ore_st_dom > 0:
                payload['ore_straord_domenica'] = str(ore_st_dom.quantize(Q2))
            if ore_st_fest > 0:
                payload['ore_straord_festivo'] = str(ore_st_fest.quantize(Q2))
            if ore_st_nf > 0:
                payload['ore_straord_nott_fest'] = str(ore_st_nf.quantize(Q2))
            if ore_ord > 0 or ore_dom > 0 or ore_fest > 0:
                payload['ore_ordinarie_escludono_dom_fest'] = '1'
            l207_bonus = _sum_imp('9824')
            trattenute_extra = _sum_imp('1800', '1802', '9250')
            if l207_bonus > 0:
                payload['competenze_extra_non_imponibili'] = str(l207_bonus)
            if trattenute_extra > 0:
                payload['trattenute_extra_mese'] = str(trattenute_extra)
            _msg_ced = (
                f'Contratto {rapporto.numero_contratto} — livello {rapporto.livello_ccnl} · '
                f'dati cedolino importato {mese:02d}/{anno}'
            )
            if _sm_da_ced:
                _msg_ced += (
                    ' · Superminimo stimato da cedolino (valorizzare «Superminimo» sul contratto come fonte ufficiale).'
                )
            payload['messaggio'] = _msg_ced
    except Exception:
        pass
    return payload


def _json_prefill_da_proposta(request, proposta_id: int, anno: int, mese: int) -> dict:
    from rapporto_di_lavoro.views import _get_proposta_con_permesso

    prop = _get_proposta_con_permesso(request, proposta_id)
    if prop is None:
        return {'ok': False, 'errore': 'Proposta non trovata o accesso negato'}
    cp = prop.parametro_ccnl_risolto
    if not cp:
        return {'ok': False, 'errore': 'Nessun parametro CCNL risolvibile per questa proposta'}
    div = _divisore_tabellare_da_parametro_ccnl(cp) or '172'
    sm = Decimal(str(prop.superminimo_mensile or 0)).quantize(Q2)
    return {
        'ok': True,
        'fonte': 'proposta',
        'messaggio': f'Proposta {prop.numero_proposta} ({prop.get_stato_display()})',
        'parametro_ccnl': str(cp.pk),
        'tipo_contratto': str(prop.tipo_contratto_id),
        'azienda': str(prop.azienda_id),
        'dipendente_id': str(prop.dipendente_id) if prop.dipendente_id else '',
        'data_inizio_rapporto': prop.data_inizio_rapporto.isoformat(),
        'data_fine_rapporto': prop.data_fine_rapporto.isoformat() if prop.data_fine_rapporto else '',
        'superminimo': str(sm),
        'divisore': div,
        'usa_dati_contratto': '',
        'rateo_13_mensile_in_imponibile': '1' if prop.tredicesima_rateo_mensile_in_imponibile else '0',
        'rateo_14_mensile_in_imponibile': '1' if prop.quattordicesima_rateo_mensile_in_imponibile else '0',
    }


@login_required
def api_prefill_simulatore_form(request):
    """
    GET ?dipendente_id=&anno=&mese=  → campi da RapportoDiLavoro sottoscritto.
    GET ?proposta_id=&anno=&mese=    → campi da PropostaAssunzione (candidato / bozza).
    """
    from django.http import JsonResponse

    try:
        anno = int(request.GET.get('anno', ''))
        mese = int(request.GET.get('mese', ''))
    except (ValueError, TypeError):
        return JsonResponse({'ok': False, 'errore': 'anno e mese numerici obbligatori'}, status=400)
    if not (1 <= mese <= 12 and 2000 <= anno <= 2100):
        return JsonResponse({'ok': False, 'errore': 'mese o anno non validi'}, status=400)

    prop_raw = (request.GET.get('proposta_id') or '').strip()
    dip_raw = (request.GET.get('dipendente_id') or '').strip()
    if prop_raw:
        try:
            pid = int(prop_raw)
        except ValueError:
            return JsonResponse({'ok': False, 'errore': 'proposta_id non valido'}, status=400)
        return JsonResponse(_json_prefill_da_proposta(request, pid, anno, mese))
    if dip_raw:
        try:
            did = int(dip_raw)
        except ValueError:
            return JsonResponse({'ok': False, 'errore': 'dipendente_id non valido'}, status=400)
        return JsonResponse(_json_prefill_da_contratto_dipendente(request, did, anno, mese))
    return JsonResponse({'ok': False, 'errore': 'Specificare dipendente_id o proposta_id'}, status=400)


@login_required
def simulatore_paga(request):
    from .models import ParametroCCNLTurismo, TipoContratto
    from anagrafiche.models import Azienda

    oggi = _tz.localdate()
    # Il dropdown riceve SEMPRE tutte le opzioni attive — il filtro per data
    # è gestito lato client via JS (data-da / data-a sulle opzioni).
    # Il filtro server-side per data viene applicato solo al momento del calcolo.
    parametri_ccnl = ParametroCCNLTurismo.objects.filter(attivo=True).order_by('ccnl', 'livello_ordinamento')
    tipi_contratto = TipoContratto.objects.filter(attivo=True).order_by('nome')
    aziende = Azienda.objects.all().order_by('nome')

    def _filtra_ccnl_per_data(qs, anno_s, mese_s):
        """Filtra ParametroCCNLTurismo per validità nel mese/anno indicato.
        Usa intersezione intervalli: da <= ultimo_giorno_mese AND a >= primo_giorno_mese.
        """
        import calendar as _cal
        from django.db.models import Q as _Q
        try:
            anno_i, mese_i = int(anno_s), int(mese_s)
            primo   = date(anno_i, mese_i, 1)
            ultimo  = date(anno_i, mese_i, _cal.monthrange(anno_i, mese_i)[1])
        except (ValueError, TypeError):
            return qs
        return qs.filter(
            _Q(decorrenza_validita_da__isnull=True) | _Q(decorrenza_validita_da__lte=ultimo)
        ).filter(
            _Q(decorrenza_validita_a__isnull=True) | _Q(decorrenza_validita_a__gte=primo)
        )

    risultato = None
    errore = None

    # ── Gestione sessione ─────────────────────────────────────────────────────
    if request.method == 'POST':
        action = request.POST.get('action', '')
        if action == 'nuova':
            # Cancella scenario salvato e riporta form vuoto
            request.session.pop(SESSION_KEY, None)
            return _render(request, parametri_ccnl, tipi_contratto, aziende,
                           {}, risultato, errore, oggi)

        form_data = dict(request.POST)  # QueryDict → dict of lists
        # Serializza come dict di valori singoli per la sessione
        form_flat = {k: v[0] if len(v) == 1 else v for k, v in form_data.items()
                     if k != 'csrfmiddlewaretoken'}
        request.session[SESSION_KEY] = form_flat

    else:
        # GET: ripristina ultimo scenario dalla sessione
        form_flat = request.session.get(SESSION_KEY, {})
        form_data = form_flat  # compatibilità template

    if not form_flat:
        return _render(request, parametri_ccnl, tipi_contratto, aziende,
                       {}, None, None, oggi)

    # ── Calcolo ───────────────────────────────────────────────────────────────
    try:
        from .utils_motore_paga import calcola_busta_paga_mese
        from .motore_paga_roel import costruisci_competenze_logica_v1

        def _get(key, default=''):
            v = form_flat.get(key, default)
            return v if isinstance(v, str) else (v[0] if v else default)

        # Mese / Anno (nuovi campi separati o retrocompatibile)
        mese_str = _get('mese_riferimento', '')
        anno_s   = _get('anno', '')
        mese_s   = _get('mese', '')
        if anno_s and mese_s:
            anno, mese = int(anno_s), int(mese_s)
            mese_str = f"{anno}-{mese:02d}"
        elif mese_str:
            anno, mese = int(mese_str[:4]), int(mese_str[5:7])
        else:
            anno, mese = oggi.year, oggi.month
            mese_str = f"{anno}-{mese:02d}"

        # Verifica che il parametro CCNL selezionato sia valido per il mese
        # (non riassegna parametri_ccnl: il dropdown mostra sempre tutte le opzioni)

        # Azienda
        azienda_pk = _get('azienda', '').strip()
        azienda = None
        if azienda_pk:
            try:
                azienda = Azienda.objects.get(pk=azienda_pk)
            except Azienda.DoesNotExist:
                pass

        # Pro-rata date
        def _to_date(s):
            s = (s or '').strip()
            return date.fromisoformat(s) if s else None

        data_inizio = _to_date(_get('data_inizio_rapporto'))
        data_fine   = _to_date(_get('data_fine_rapporto'))

        # Parametri CCNL e contratto (base: form; sovrascritti se «Dati da contratto attivo»)
        from .parametro_ccnl_voci_retributive import risolvi_ccnl_modello_da_parametro

        cp = ParametroCCNLTurismo.objects.get(pk=_get('parametro_ccnl'))
        _ccnl_obj = risolvi_ccnl_modello_da_parametro(cp)
        tc = TipoContratto.objects.get(pk=_get('tipo_contratto'))
        coeff = Decimal(str(tc.coefficiente_ore or 1))
        scatto_anzianita = Decimal('0')
        sim_fonte_dati = 'parametri_ccnl'
        indennita_extra = Decimal('0')
        contratto_esclude_13 = False
        contratto_esclude_14 = False
        dip_ctx_merge = None
        dip_scheda_anagrafica = None
        num_fam_fisc = 0
        regione_fisc = 'Sicilia'
        comune_fisc = None
        prov_fisc = None

        # Divisore
        div_raw = _get('divisore', '26').replace(',', '.')

        # Voci variabili dal form
        superminimo     = Decimal(_get('superminimo', '0') or '0').quantize(Q2)
        indennita_turno = Decimal(_get('indennita_turno', '0') or '0').quantize(Q2)
        comp_extra_non_imp = Decimal(_get('competenze_extra_non_imponibili', '0') or '0').quantize(Q2)
        trattenute_extra = Decimal(_get('trattenute_extra_mese', '0') or '0').quantize(Q2)
        ore_distinte_dom_fest = _get('ore_ordinarie_escludono_dom_fest', '0') == '1'

        def _ore(key): return Decimal(_get(key, '0') or '0').quantize(Q2)
        ore_sd  = _ore('ore_straord_diurno')
        ore_sn  = _ore('ore_straord_notturno')
        ore_sf  = _ore('ore_straord_festivo')
        ore_sdom = _ore('ore_straord_domenica')
        ore_snf = _ore('ore_straord_nott_fest')

        def _gg(key): return Decimal(_get(key, '0') or '0').quantize(Q2)
        ore_ord_ret = _ore('ore_ordinarie_retribuite')
        ore_dom     = _ore('ore_domenicali')
        ore_fest    = _ore('ore_festivi_lavorati')
        gg_assenza  = _gg('giorni_assenza_ingiust')
        gg_ferie    = _gg('giorni_ferie_godute')
        ore_perm    = _gg('ore_permessi_goduti')

        # Ratei 13ª/14ª nella base INPS/IRPEF/INAIL solo se erogati mensilmente in busta (contratto / simulazione).
        r13_imp_m = _get('rateo_13_mensile_in_imponibile', '0') == '1'
        r14_imp_m = _get('rateo_14_mensile_in_imponibile', '0') == '1'

        # ── Fonte «dipendente in carico»: contratto sottoscritto + scatti da anzianità di servizio ──
        usa_contr = _get('usa_dati_contratto', '') == '1'
        dip_id_raw = (_get('dipendente_id', '') or '').strip()
        if usa_contr:
            if not dip_id_raw:
                raise ValueError(
                    'Per applicare il contratto attivo seleziona un dipendente nel campo sottostante.'
                )
            from anagrafiche.models import Dipendente
            from .risoluzione_contratto_motore import (
                anni_di_servizio,
                build_scatti_db,
                calcola_scatto_totale_maturato,
                rapporto_sottoscritto_attivo_nel_mese,
                risolvi_parametro_ccnl_per_mese,
                superminimo_da_rapporto_o_ruolo,
            )
            try:
                dip_ctx = Dipendente.objects.get(pk=int(dip_id_raw))
            except (ValueError, Dipendente.DoesNotExist) as exc:
                raise ValueError('Dipendente non valido per l\'importazione contrattuale.') from exc
            dip_ctx_merge = dip_ctx
            # Il rapporto è sempre legato all'azienda del dipendente: non usare l'azienda del calendario
            # (se diversa, prima non si trovava alcun contratto).
            rapporto = rapporto_sottoscritto_attivo_nel_mese(
                dipendente=dip_ctx, azienda=dip_ctx.azienda, anno=anno, mese=mese,
            )
            # Calendario (chiusure / festività) allineato alla sede contrattuale
            azienda = dip_ctx.azienda
            if not rapporto:
                raise ValueError(
                    'Nessun contratto sottoscritto attivo nel mese selezionato per questo dipendente '
                    '(verifica azienda e arco data inizio / fine rapporto), oppure deseleziona «Dati da contratto».'
                )
            primo_m = date(anno, mese, 1)
            cp_c, _fonte_pc = risolvi_parametro_ccnl_per_mese(
                rapporto=rapporto,
                data_primo_giorno_mese=primo_m,
                livello_fallback=(dip_ctx.livello or '').strip(),
            )
            if not cp_c:
                raise ValueError(
                    f'Impossibile risolvere il parametro CCNL tabellare per il livello contrattuale '
                    f'«{(rapporto.livello_ccnl or "").strip() or "?"}» nel mese {anno}-{mese:02d}.'
                )
            cp = cp_c
            _ccnl_obj = risolvi_ccnl_modello_da_parametro(cp)
            tc = rapporto.tipo_contratto
            coeff = Decimal(str(tc.coefficiente_ore or 1))
            data_inizio = rapporto.data_inizio_rapporto
            data_fine = rapporto.data_fine_rapporto
            superminimo = superminimo_da_rapporto_o_ruolo(
                rapporto=rapporto, ruolo_superminimo=Decimal('0'),
            ).quantize(Q2)
            r13_imp_m = bool(rapporto.tredicesima_rateo_mensile_in_imponibile)
            r14_imp_m = bool(rapporto.quattordicesima_rateo_mensile_in_imponibile)
            anni_srv = anni_di_servizio(rapporto.data_inizio_rapporto, primo_m)
            scatti_db = build_scatti_db(_ccnl_obj, anno)
            livello_eff = (rapporto.livello_ccnl or dip_ctx.livello or '').strip()
            scatto_anzianita = calcola_scatto_totale_maturato(livello_eff, anni_srv, scatti_db).quantize(Q2)
            sim_fonte_dati = 'contratto_attivo'
            indennita_extra = Decimal(str(rapporto.premio_obiettivi or 0)).quantize(Q2)
            contratto_esclude_13 = not bool(rapporto.tredicesima)
            contratto_esclude_14 = not bool(rapporto.quattordicesima)
            try:
                _pf = dip_ctx.profilocandidato
                num_fam_fisc = int(_pf.num_familiari_a_carico or 0)
                if (_pf.regione_residenza or '').strip():
                    regione_fisc = _pf.regione_residenza.strip()
                if (_pf.citta or '').strip():
                    comune_fisc = _pf.citta.strip()
                if (_pf.provincia or '').strip():
                    prov_fisc = (_pf.provincia or '').strip()[:2]
            except Exception:
                pass
            # Divisore orario tabellare (solo se il parametro espone ore «divisore» ≥ 160)
            if cp.ore_mensili and Decimal(str(cp.ore_mensili)) >= Decimal('160'):
                om = float(cp.ore_mensili)
                if abs(om - 173.33) < 0.06:
                    div_raw = '173.33'
                else:
                    div_raw = str(int(round(om)))

        elif dip_id_raw:
            try:
                from anagrafiche.models import Dipendente
                dip_scheda_anagrafica = Dipendente.objects.exclude(stato='cessato').filter(
                    pk=int(dip_id_raw),
                ).first()
            except (ValueError, TypeError):
                dip_scheda_anagrafica = None

        # ── Auto-import presenze reali quando il dipendente e' selezionato ───
        # Se tutte le ore/assenze sono ancora a zero, usa il registro presenze del mese
        # per evitare il fallback al calendario teorico (gg ord. × h/gg).
        _tutti_zero = (
            ore_sd == 0 and ore_sn == 0 and ore_sf == 0 and ore_sdom == 0 and ore_snf == 0
            and ore_ord_ret == 0 and ore_dom == 0 and ore_fest == 0
            and gg_assenza == 0 and gg_ferie == 0 and ore_perm == 0
        )
        if dip_id_raw and _tutti_zero:
            try:
                from anagrafiche.models import Dipendente
                from .utils_presenze import get_presenze_mese_aggregato

                _dip_for_pres = Dipendente.objects.get(pk=int(dip_id_raw))
                _agg_pres = get_presenze_mese_aggregato(_dip_for_pres, anno, mese, azienda or _dip_for_pres.azienda)
                ore_sd = Decimal(str(_agg_pres.get('ore_straord_diurno', 0))).quantize(Q2)
                ore_sn = Decimal(str(_agg_pres.get('ore_straord_notturno', 0))).quantize(Q2)
                ore_sf = Decimal(str(_agg_pres.get('ore_straord_festivo', 0))).quantize(Q2)
                ore_sdom = Decimal(str(_agg_pres.get('ore_straord_domenica', 0))).quantize(Q2)
                ore_snf = Decimal(str(_agg_pres.get('ore_straord_nott_fest', 0))).quantize(Q2)
                ore_ord_ret = Decimal(str(_agg_pres.get('ore_ordinarie_retribuite', 0))).quantize(Q2)
                ore_dom = Decimal(str(_agg_pres.get('ore_domenicali', 0))).quantize(Q2)
                ore_fest = Decimal(str(_agg_pres.get('ore_festivi_lavorati', 0))).quantize(Q2)
                gg_assenza = Decimal(str(_agg_pres.get('giorni_assenza_ingiust', 0))).quantize(Q2)
                gg_ferie = Decimal(str(_agg_pres.get('giorni_ferie_godute', 0))).quantize(Q2)
                ore_perm = Decimal(str(_agg_pres.get('ore_permessi_goduti', 0))).quantize(Q2)
                form_flat['ore_straord_diurno'] = str(ore_sd)
                form_flat['ore_straord_notturno'] = str(ore_sn)
                form_flat['ore_straord_festivo'] = str(ore_sf)
                form_flat['ore_straord_domenica'] = str(ore_sdom)
                form_flat['ore_straord_nott_fest'] = str(ore_snf)
                form_flat['ore_ordinarie_retribuite'] = str(ore_ord_ret)
                form_flat['ore_domenicali'] = str(ore_dom)
                form_flat['ore_festivi_lavorati'] = str(ore_fest)
                form_flat['giorni_assenza_ingiust'] = str(gg_assenza)
                form_flat['giorni_ferie_godute'] = str(gg_ferie)
                form_flat['ore_permessi_goduti'] = str(ore_perm)
                form_flat['ore_ordinarie_escludono_dom_fest'] = '1'
                ore_distinte_dom_fest = True
            except Exception:
                pass

        # ── Auto-calcolo dal calendario se ore non inserite ───────────────────
        # Domenicali: delegate al motore (usa calendario + chiusure aziendali).
        # Festivi: ore/gg = h settimanali tabellari × coeff. part-time ÷ 6 (stesso criterio del motore busta).
        _ost = (Decimal(str(cp.ore_settimanali or 0)) * coeff).quantize(Q2)
        _ore_gg = (_ost / Decimal('6')).quantize(Q2) if _ost > 0 else (
            (Decimal(str(cp.ore_giornaliere or 0)) * coeff).quantize(Q2)
        )
        if not ore_fest and _ore_gg:
            from .utils_calendario import get_festivita_mese as _get_fest
            _n_fest = sum(
                1 for f in _get_fest(anno, mese, azienda)
                if f['data'].weekday() != 6
            )
            ore_fest = (Decimal(str(_n_fest)) * _ore_gg).quantize(Q2)

        # ── Chiamata al motore paga condiviso ─────────────────────────────────
        _modalita_ore_effettive = (ore_ord_ret > 0)
        r = calcola_busta_paga_mese(
            parametro_ccnl=cp,
            tipo_contratto=tc,
            anno=anno,
            mese=mese,
            azienda=azienda,
            data_inizio_rapporto=data_inizio,
            data_fine_rapporto=data_fine,
            divisore_str=div_raw,
            superminimo=superminimo,
            scatto_anzianita=scatto_anzianita,
            indennita_extra=indennita_extra,
            indennita_turno=indennita_turno,
            competenze_extra_non_imponibili=comp_extra_non_imp,
            trattenute_extra_mese=trattenute_extra,
            contratto_esclude_tredicesima=contratto_esclude_13,
            contratto_esclude_quattordicesima=contratto_esclude_14,
            ore_straord_diurno=ore_sd,
            ore_straord_notturno=ore_sn,
            ore_straord_festivo=ore_sf,
            ore_straord_domenica=ore_sdom,
            ore_straord_nott_fest=ore_snf,
            ore_domenicali=ore_dom,
            ore_festivi=ore_fest,
            giorni_assenza_ingiust=gg_assenza,
            giorni_ferie_godute=gg_ferie,
            ore_permessi_goduti=ore_perm,
            ccnl_obj=_ccnl_obj,
            rateo_13_mensile_in_imponibile=r13_imp_m,
            rateo_14_mensile_in_imponibile=r14_imp_m,
            ore_ordinarie_retribuite=ore_ord_ret,
            modalita_ore_effettive=_modalita_ore_effettive,
            # Se le ore ordinarie escludono domeniche/festivi, le relative righe
            # devono contenere il compenso completo (base + maggiorazione).
            domenicale_compenso_completo=(not _modalita_ore_effettive) or ore_distinte_dom_fest,
            festivo_compenso_completo=(not _modalita_ore_effettive) or ore_distinte_dom_fest,
            mensilita_contrattuale_piena=True,
            num_familiari_a_carico=num_fam_fisc,
            regione_residenza=regione_fisc,
            comune_residenza=comune_fisc,
            provincia_residenza=prov_fisc,
        )

        # ── Voci per tabella Box 1 (solo importo > 0) ────────────────────────
        _div_orario = r['divisore'] > Decimal('30')
        from .motore_paga_roel import roel_tabellare_euro_oraria

        _roel_tab = roel_tabellare_euro_oraria(r) if _div_orario else r['retribuzione_oraria_di_fatto']

        # Importo righe tabellari (paga/cont/scatto/...) allineato alla stessa base ore
        # della riga ordinario in rubrica (es. 120 h = 20 gg ord. × 6 h/gg).
        if ore_ord_ret and ore_ord_ret > 0:
            _ore_base_tab = ore_ord_ret.quantize(Q2)
        else:
            _ore_base_tab = (
                Decimal(str(r.get('cal_giorni_ordinari') or 0)) *
                Decimal(str(r.get('ore_giornaliere') or 0))
            ).quantize(Q2)

        def _r_tab(nome, imp_field, note, oraria_field):
            row = {'nome': nome, 'importo': r[imp_field], 'inps': True, 'irpef': True, 'note': note}
            if _div_orario:
                _oraria = Decimal(str(r.get(oraria_field) or 0)).quantize(Q4)
                row['oraria_tab'] = _oraria
                if _ore_base_tab > 0:
                    row['ore'] = _ore_base_tab
                    row['importo'] = (_oraria * _ore_base_tab).quantize(Q2)
            return row

        voci = [
            _r_tab('Paga base CCNL', 'paga_base', 'Art. 74 CCNL FIPE', 'oraria_tabellare_paga_base'),
            _r_tab('Contingenza', 'contingenza', 'Indennità contingenza', 'oraria_tabellare_contingenza'),
        ]
        if r.get('edr') and r['edr'] > 0:
            voci.append(_r_tab('EDR', 'edr', 'Elemento Distorsivo Retrib.', 'oraria_tabellare_edr'))
        # Indennità CCNL solo se > 0
        if r['indennita']:
            voci.append(_r_tab('Indennità CCNL', 'indennita', 'Prevista da CCNL', 'oraria_tabellare_indennita'))
        if r.get('scatto') and r['scatto'] > 0:
            voci.append(_r_tab(
                'Scatto anzianità', 'scatto',
                'Da parametro CCNL (tabella livello) se non diversamente indicato',
                'oraria_tabellare_scatto',
            ))
        # Subtotale ROEL (solo paga+cont+scatto ÷ divisore) — stesso valore straord./domeniche; prima di superminimo/turno
        if _div_orario:
            voci.append({
                'nome': 'ROEL tabellare (paga base + contingenza + scatto €/h)',
                'oraria_tab': _roel_tab,
                'importo': None,
                'inps': False,
                'irpef': False,
                'note': (
                    'Formula: (paga tab. × fraz. ÷ divisore) + (contingenza × fraz. ÷ divisore) + (scatto × fraz. ÷ divisore). '
                    'Esclude EDR, indennità CCNL, superminimo, turno, straord. e maggiorazioni.'
                ),
                'row_kind': 'subtotal_rof',
            })
        if r.get('superminimo') and Decimal(str(r['superminimo'])) > 0:
            _hsm = r.get('oraria_tabellare_superminimo')
            try:
                _hsm_q = Decimal(str(_hsm)).quantize(Q4) if _hsm is not None else None
            except Exception:
                _hsm_q = None
            _note_sm = (
                'Importo mensile fisso in busta; €/h = quota effetto part-time (Sm_ref × % PT ÷ divisore CCNL).'
            )
            voci.append({
                'nome': 'Superminimo',
                'importo': r['superminimo'],
                'oraria_tab': _hsm_q,
                'inps': True,
                'irpef': True,
                'note': _note_sm,
            })
        if r['indennita_turno']:
            voci.append({'nome': 'Indennità turno', 'importo': r['indennita_turno'], 'inps': True, 'irpef': True, 'note': 'Turni notturni/speciali'})
        # Straordinari — solo se ore > 0
        if ore_sd:
            voci.append({'nome': f'Straord. diurno (+{r["magg_diur_pct"]}%)',   'importo': r['imp_sd'],  'ore': ore_sd,  'inps': True, 'irpef': True})
        if ore_sn:
            voci.append({'nome': f'Straord. notturno (+{r["magg_nott_pct"]}%)', 'importo': r['imp_sn'],  'ore': ore_sn,  'inps': True, 'irpef': True})
        if ore_sf:
            voci.append({'nome': f'Straord. festivo (+{r["magg_fest_pct"]}%)',  'importo': r['imp_sf'],  'ore': ore_sf,  'inps': True, 'irpef': True})
        if ore_snf:
            voci.append({'nome': f'Straord. nott-fest (+{r["magg_nf_pct"]}%)',  'importo': r['imp_snf'], 'ore': ore_snf, 'inps': True, 'irpef': True})
        # Maggiorazioni domenicali/festive — solo se ore > 0
        if r['ore_domenicali']:
            _pct_dom = Decimal(str(r['magg_dom_pct'])) / Decimal('100')
            _dom_completo = bool(r.get('domenicale_compenso_completo'))
            if _div_orario:
                if _dom_completo:
                    _or_dom_magg = (r['paga_oraria'] * (Decimal('1') + _pct_dom)).quantize(Q4)
                else:
                    _or_dom_magg = (r['paga_oraria'] * _pct_dom).quantize(Q4)
            else:
                _or_dom_magg = None
            _nome_dom = (
                f'Lavoro domenicale (completo: ROF×(1+{r["magg_dom_pct"]}%))'
                if _dom_completo
                else f'Lavoro domenicale +{r["magg_dom_pct"]}% (solo magg.)'
            )
            _tit_dom = (
                f'€/h: ROF × (1 + {r["magg_dom_pct"]}%)'
                if _dom_completo
                else f'€/h magg.: ROF × {r["magg_dom_pct"]}%'
            )
            _nota_dom = (
                f'Ore × ROEL × (1 + {r["magg_dom_pct"]}%) — compenso domenicale completo.'
                if _dom_completo
                else (
                    f'Importo = ore × retrib. oraria di fatto × {r["magg_dom_pct"]}% '
                    '(base ordinaria già inclusa nel lordo tabellare).'
                )
            )
            voci.append({
                'nome': _nome_dom,
                'importo': r['imp_dom_magg'],
                'ore': r['ore_domenicali'],
                'oraria_tab': _or_dom_magg,
                'oraria_tab_titolo': _tit_dom,
                'cal_hint': r.get('cal_domeniche_lav_n', r['cal_domeniche_n']),
                'cal_hint_kind': 'dom',
                'inps': True, 'irpef': True,
                'note': _nota_dom,
            })
        if r['ore_festivi']:
            _pct_fest = Decimal(str(r['magg_fest_day_pct'])) / Decimal('100')
            _or_fest_magg = (r['paga_oraria'] * _pct_fest).quantize(Q4) if _div_orario else None
            voci.append({
                'nome': f'Lavoro festivo +{r["magg_fest_day_pct"]}%',
                'importo': r['imp_fest_magg'],
                'ore': r['ore_festivi'],
                'oraria_tab': _or_fest_magg,
                'oraria_tab_titolo': f'€/h magg.: ROF × {r["magg_fest_day_pct"]}%',
                'cal_hint': r['cal_festivi_lav_n'],
                'cal_hint_kind': 'fest',
                'inps': True, 'irpef': True,
                'note': (
                    f'Importo = ore nella colonna Ore (da presenze o valore inserito) '
                    f'× retrib. oraria di fatto × {r["magg_fest_day_pct"]}%'
                ),
            })
        if r['decurt_assenze']:
            voci.append({'nome': 'Assenze ingiustificate', 'importo': -r['decurt_assenze'], 'gg': r['giorni_assenza_ingiust'], 'inps': True, 'irpef': True, 'negativo': True})

        if _div_orario:
            for row in voci:
                row.setdefault('oraria_tab', None)

        # Allineamento foglio INPS: con ore ordinarie, importo riga = €/h tab. × ore lav. ord.
        if _div_orario and r.get('modalita_ore_effettive') and ore_ord_ret > 0:
            _imp_o = r['imp_ordinario_ore']
            for row in voci:
                if row.get('row_kind') == 'subtotal_rof':
                    row['importo'] = _imp_o
                    row['ore_lav_ord'] = ore_ord_ret
                    row['note'] = (
                        f'Somma €/h tab. × {ore_ord_ret} h ord. = importo tabellare su ore lavorate '
                        f'(ROEL {_roel_tab} €/h). Esclude superminimo, turno, straord. e maggiorazioni.'
                    )
                    break
            for row in voci:
                ot = row.get('oraria_tab')
                if ot is None or row.get('row_kind') == 'subtotal_rof':
                    continue
                if row.get('oraria_tab_titolo'):
                    continue
                row['importo'] = (ot * ore_ord_ret).quantize(Q2)
                row['ore_lav_ord'] = ore_ord_ret
                _prev = (row.get('note') or '').strip()
                row['note'] = (
                    f'{_prev} — importo = €/h tab. × {ore_ord_ret} h ord.'.strip(' —')
                    if _prev
                    else f'Importo = €/h tab. × {ore_ord_ret} h ord.'
                )

        # Paga netta giornaliera (divisore convenzionale 26 FIPE)
        _div26 = r['divisore'] if r['divisore'] <= Decimal('30') else Decimal('26')
        # Ratei 13ª/14ª pagati mensilmente → inclusi nel netto mensile effettivo
        netto_con_1314  = (r['netto_totale'] + r['rat13_n'] + r['rat14_n']).quantize(Q2)
        tfr_fer_netti   = (r['tfr_n'] + r['rat_fer_n']).quantize(Q2)
        lordo_con_1314  = (r['lordo_mensile'] + r['rat13_m'] + r['rat14_m']).quantize(Q2)
        netto_gg        = (netto_con_1314    / _div26).quantize(Q2)
        netto_gg_ratei  = (r['netto_con_ratei'] / _div26).quantize(Q2)

        # Ore suggerite dal calendario: domeniche × ore/gg e festivi (non-dom) × ore/gg
        _ore_gg = r['ore_giornaliere']
        ore_dom_sug  = (Decimal(str(r.get('cal_domeniche_lav_n', r['cal_domeniche_n']))) * _ore_gg).quantize(Q2)
        ore_fest_sug = (Decimal(str(r['cal_festivi_lav_n'])) * _ore_gg).quantize(Q2)

        risultato = {
            'nome_test': _get('nome_test', ''),
            'sim_fonte_dati': sim_fonte_dati,
            'azienda_nome': azienda.nome if azienda else '(festività nazionali + domenica)',
            'ccnl_nome': cp.ccnl, 'ccnl_livello': cp.livello, 'ccnl_qualifica': cp.qualifica,
            'ccnl_decorrenza_da': cp.decorrenza_validita_da,
            'tipo_contratto': tc.nome, 'coeff_ore': coeff,
            'voci': voci,
            'mese_nome': MESI_NOMI[mese],
            'mese_str': mese_str,
            'netto_mensile_con_1314': netto_con_1314,
            'tfr_fer_netti': tfr_fer_netti,
            'lordo_con_1314': lordo_con_1314,
            'netto_giornaliero': netto_gg,
            'netto_giornaliero_con_ratei': netto_gg_ratei,
            'divisore_gg_conv': _div26,
            'ore_dom_suggerite': ore_dom_sug,
            'ore_fest_suggerite': ore_fest_sug,
            **r,  # spread all fields from calcola_busta_paga_mese result
        }
        risultato['competenze_logica_v1'] = costruisci_competenze_logica_v1(risultato)
        if dip_ctx_merge is not None:
            risultato['dipendente_label'] = f'{dip_ctx_merge.cognome} {dip_ctx_merge.nome}'.strip()
            if data_inizio:
                risultato['data_assunzione_display'] = data_inizio.strftime('%d/%m/%Y')
            _d = dip_ctx_merge
            _cf = (_d.codice_fiscale or '').strip().upper()
            if _cf:
                risultato['cedolino_codice_fiscale'] = _cf
            _cm = (_d.citta or '').strip()
            _pr = (_d.provincia or '').strip()
            if _cm or _pr:
                risultato['cedolino_comune_residenza'] = ', '.join(x for x in (_cm, _pr) if x)
            if _d.data_nascita:
                risultato['cedolino_data_nascita'] = _d.data_nascita.strftime('%d/%m/%Y')
        elif dip_scheda_anagrafica is not None:
            d = dip_scheda_anagrafica
            risultato['dipendente_label'] = f'{d.cognome} {d.nome}'.strip()
            cf = (d.codice_fiscale or '').strip().upper()
            if cf:
                risultato['cedolino_codice_fiscale'] = cf
            cm = (d.citta or '').strip()
            pr = (d.provincia or '').strip()
            if cm or pr:
                risultato['cedolino_comune_residenza'] = ', '.join(x for x in (cm, pr) if x)
            if d.data_nascita:
                risultato['cedolino_data_nascita'] = d.data_nascita.strftime('%d/%m/%Y')
            if d.data_assunzione and not risultato.get('data_assunzione_display'):
                risultato['data_assunzione_display'] = d.data_assunzione.strftime('%d/%m/%Y')

        # Dopo «Dati da contratto»: aggiorna sessione con i valori effettivi (dropdown e campi = contratto)
        if request.method == 'POST' and sim_fonte_dati == 'contratto_attivo' and dip_ctx_merge is not None:
            form_flat['parametro_ccnl'] = str(cp.pk)
            form_flat['tipo_contratto'] = str(tc.pk)
            form_flat['superminimo'] = str(superminimo)
            form_flat['divisore'] = div_raw
            form_flat['data_inizio_rapporto'] = data_inizio.isoformat() if data_inizio else ''
            form_flat['data_fine_rapporto'] = data_fine.isoformat() if data_fine else ''
            form_flat['rateo_13_mensile_in_imponibile'] = '1' if r13_imp_m else '0'
            form_flat['rateo_14_mensile_in_imponibile'] = '1' if r14_imp_m else '0'
            form_flat['azienda'] = str(dip_ctx_merge.azienda_id)
            form_flat['dipendente_id'] = str(dip_ctx_merge.pk)
            form_flat['usa_dati_contratto'] = '1'
            request.session[SESSION_KEY] = form_flat

        # ── Salvataggio persistente scenario ──────────────────────────────────
        if request.method == 'POST':
            _salva_scenario(request, form_flat, risultato, anno, mese, cp, tc)

    except Exception as exc:
        errore = str(exc)

    return _render(request, parametri_ccnl, tipi_contratto, aziende,
                   form_flat, risultato, errore, oggi)


def _salva_scenario(request, form_flat, risultato, anno, mese, cp, tc):
    """Salva o aggiorna lo scenario calcolato in SimulazionePagaSalvata."""
    from .models import SimulazionePagaSalvata
    nome = risultato.get('nome_test') or f'Scenario {MESI_NOMI[mese]} {anno}'
    utente = request.user if request.user.is_authenticated else None

    # Serializza form_flat (tutti i valori sono già stringhe)
    def _clean(d):
        return {k: (str(v) if not isinstance(v, (str, int, float, bool, type(None))) else v)
                for k, v in d.items()}

    SimulazionePagaSalvata.objects.update_or_create(
        nome=nome,
        utente=utente,
        defaults={
            'anno': anno,
            'mese': mese,
            'ccnl_livello': str(cp.livello),
            'ccnl_qualifica': str(cp.qualifica),
            'tipo_contratto_nome': str(tc.nome),
            'lordo_mensile': risultato.get('lordo_mensile'),
            'netto_totale':  risultato.get('netto_totale'),
            'costo_mensile': risultato.get('costo_mensile'),
            'form_data': _clean(form_flat),
        },
    )


def _render(request, parametri_ccnl, tipi_contratto, aziende, form_data, risultato, errore, oggi):
    from django.shortcuts import render as _r
    from accounts.models import ConfigurazioneSistema
    from anagrafiche.models import Dipendente
    from .busta_paga_layout_canonico import costruisci_riepilogo_simulatore_da_risultato
    from .models import SimulazionePagaSalvata
    ha_scenario = bool(request.session.get(SESSION_KEY))
    utente = request.user if request.user.is_authenticated else None
    scenari_salvati = SimulazionePagaSalvata.objects.filter(
        utente=utente
    ).order_by('-data_modifica')[:20]
    cfg = ConfigurazioneSistema.get()
    use_ced = bool(getattr(cfg, 'simulatore_paga_riepilogo_cedolino_canonico', True))
    ced_riepilogo = costruisci_riepilogo_simulatore_da_risultato(risultato) if (risultato and use_ced) else None
    return _r(request, 'rapporto_di_lavoro/simulatore_paga.html', {
        'parametri_ccnl': parametri_ccnl,
        'tipi_contratto': tipi_contratto,
        'aziende': aziende,
        'dipendenti': Dipendente.objects.exclude(stato='cessato').order_by('cognome', 'nome'),
        'proposte_prefill': _elenco_proposte_prefill_simulatore(request),
        'divisori': DIVISORI,
        'mesi_nomi': MESI_NOMI[1:],
        'anni_range': list(range(2024, oggi.year + 3)),
        'risultato': risultato,
        'errore': errore,
        'form_data': form_data,
        'ha_scenario': ha_scenario,
        'scenari_salvati': scenari_salvati,
        'simulatore_riepilogo_cedolino_canonico': use_ced,
        'cedolino_riepilogo': ced_riepilogo,
    })


@login_required
def api_presenze_simulatore(request):
    """
    GET /api/presenze-simulatore/?dipendente_id=X&anno=Y[&mese=Z][&azienda_id=W]

    Se mese non specificato → ritorna tutti i 12 mesi: {"1": {...}, ..., "12": {...}}
    Se mese specificato     → ritorna il singolo mese aggregato.
    """
    from django.http import JsonResponse
    from anagrafiche.models import Dipendente, Azienda
    from .utils_presenze import get_presenze_mese_aggregato, get_presenze_anno_aggregato

    try:
        dip_id = int(request.GET.get('dipendente_id', ''))
        anno   = int(request.GET.get('anno', ''))
    except (ValueError, TypeError):
        return JsonResponse({'errore': 'Parametri dipendente_id e anno obbligatori'}, status=400)

    try:
        dipendente = Dipendente.objects.get(pk=dip_id)
    except Dipendente.DoesNotExist:
        return JsonResponse({'errore': 'Dipendente non trovato'}, status=404)

    azienda = None
    az_raw = request.GET.get('azienda_id', '').strip()
    if az_raw:
        try:
            azienda = Azienda.objects.get(pk=az_raw)
        except Azienda.DoesNotExist:
            pass

    mese_raw = request.GET.get('mese', '').strip()
    if mese_raw:
        try:
            mese = int(mese_raw)
        except ValueError:
            return JsonResponse({'errore': 'Mese non valido'}, status=400)
        result = get_presenze_mese_aggregato(dipendente, anno, mese, azienda)
        return JsonResponse({k: float(v) for k, v in result.items()})
    else:
        yearly = get_presenze_anno_aggregato(dipendente, anno, azienda)
        return JsonResponse({
            str(m): {k: float(v) for k, v in agg.items()}
            for m, agg in yearly.items()
        })


@login_required
def carica_scenario_salvato(request, scenario_id):
    """Carica i parametri di uno scenario salvato nella sessione e reindirizza al simulatore."""
    from django.shortcuts import redirect
    from .models import SimulazionePagaSalvata
    try:
        sc = SimulazionePagaSalvata.objects.get(pk=scenario_id, utente=request.user)
    except SimulazionePagaSalvata.DoesNotExist:
        from django.contrib import messages
        messages.error(request, 'Scenario non trovato.')
        return redirect('simulatore_paga')
    request.session[SESSION_KEY] = sc.form_data
    return redirect('simulatore_paga')


@login_required
def elimina_scenario_salvato(request, scenario_id):
    """Elimina uno scenario salvato (solo POST)."""
    from django.shortcuts import redirect
    from .models import SimulazionePagaSalvata
    if request.method == 'POST':
        SimulazionePagaSalvata.objects.filter(pk=scenario_id, utente=request.user).delete()
    return redirect('simulatore_paga')
