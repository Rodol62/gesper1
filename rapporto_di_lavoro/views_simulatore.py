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


@login_required
def simulatore_paga(request):
    from .models import ParametroCCNLTurismo, TipoContratto, CCNL
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

        # CCNL obj
        _ccnl_obj = CCNL.objects.filter(sigla__icontains='FIPE').first()

        # Parametri CCNL e contratto
        cp = ParametroCCNLTurismo.objects.get(pk=_get('parametro_ccnl'))
        tc = TipoContratto.objects.get(pk=_get('tipo_contratto'))
        coeff = Decimal(str(tc.coefficiente_ore or 1))

        # Divisore
        div_raw = _get('divisore', '26').replace(',', '.')

        # Voci variabili dal form
        superminimo     = Decimal(_get('superminimo', '0') or '0').quantize(Q2)
        indennita_turno = Decimal(_get('indennita_turno', '0') or '0').quantize(Q2)

        def _ore(key): return Decimal(_get(key, '0') or '0').quantize(Q2)
        ore_sd  = _ore('ore_straord_diurno')
        ore_sn  = _ore('ore_straord_notturno')
        ore_sf  = _ore('ore_straord_festivo')
        ore_sdom = _ore('ore_straord_domenica')
        ore_snf = _ore('ore_straord_nott_fest')

        def _gg(key): return Decimal(_get(key, '0') or '0').quantize(Q2)
        ore_dom     = _ore('ore_domenicali')
        ore_fest    = _ore('ore_festivi_lavorati')
        gg_assenza  = _gg('giorni_assenza_ingiust')
        gg_ferie    = _gg('giorni_ferie_godute')
        ore_perm    = _gg('ore_permessi_goduti')

        # Ratei 13ª/14ª nella base INPS/IRPEF/INAIL solo se erogati mensilmente in busta (contratto / simulazione).
        r13_imp_m = _get('rateo_13_mensile_in_imponibile', '0') == '1'
        r14_imp_m = _get('rateo_14_mensile_in_imponibile', '0') == '1'

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
            indennita_turno=indennita_turno,
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
        )

        # ── Voci per tabella Box 1 (solo importo > 0) ────────────────────────
        voci = [
            {'nome': 'Paga base CCNL',    'importo': r['paga_base'],   'inps': True, 'irpef': True, 'note': 'Art. 74 CCNL FIPE'},
            {'nome': 'Contingenza',        'importo': r['contingenza'], 'inps': True, 'irpef': True, 'note': 'Indennità contingenza'},
            {'nome': 'EDR',                'importo': r['edr'],         'inps': True, 'irpef': True, 'note': 'Elemento Distorsivo Retrib.'},
        ]
        # Indennità CCNL solo se > 0
        if r['indennita']:
            voci.append({'nome': 'Indennità CCNL', 'importo': r['indennita'], 'inps': True, 'irpef': True, 'note': 'Prevista da CCNL'})
        if r['superminimo']:
            voci.append({'nome': 'Superminimo', 'importo': r['superminimo'], 'inps': True, 'irpef': True, 'note': 'Individuale/aziendale'})
        if r['indennita_turno']:
            voci.append({'nome': 'Indennità turno', 'importo': r['indennita_turno'], 'inps': True, 'irpef': True, 'note': 'Turni notturni/speciali'})
        if r.get('scatto') and r['scatto'] > 0:
            voci.append({
                'nome': 'Scatto anzianità',
                'importo': r['scatto'],
                'inps': True,
                'irpef': True,
                'note': 'Da parametro CCNL (tabella livello) se non diversamente indicato',
            })
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
            voci.append({
                'nome': f'Lavoro domenicale +{r["magg_dom_pct"]}%',
                'importo': r['imp_dom_magg'],
                'ore': r['ore_domenicali'],
                'cal_hint': r.get('cal_domeniche_lav_n', r['cal_domeniche_n']),
                'inps': True, 'irpef': True,
                'note': f'ore × paga/h × {r["magg_dom_pct"]}%',
            })
        if r['ore_festivi']:
            voci.append({
                'nome': f'Lavoro festivo +{r["magg_fest_day_pct"]}%',
                'importo': r['imp_fest_magg'],
                'ore': r['ore_festivi'],
                'cal_hint': r['cal_festivi_lav_n'],
                'inps': True, 'irpef': True,
                'note': f'ore × paga/h × {r["magg_fest_day_pct"]}%',
            })
        if r['decurt_assenze']:
            voci.append({'nome': 'Assenze ingiustificate', 'importo': -r['decurt_assenze'], 'gg': r['giorni_assenza_ingiust'], 'inps': True, 'irpef': True, 'negativo': True})

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
    from anagrafiche.models import Dipendente
    from .models import SimulazionePagaSalvata
    ha_scenario = bool(request.session.get(SESSION_KEY))
    utente = request.user if request.user.is_authenticated else None
    scenari_salvati = SimulazionePagaSalvata.objects.filter(
        utente=utente
    ).order_by('-data_modifica')[:20]
    return _r(request, 'rapporto_di_lavoro/simulatore_paga.html', {
        'parametri_ccnl': parametri_ccnl,
        'tipi_contratto': tipi_contratto,
        'aziende': aziende,
        'dipendenti': Dipendente.objects.exclude(stato='cessato').order_by('cognome', 'nome'),
        'divisori': DIVISORI,
        'mesi_nomi': MESI_NOMI[1:],
        'anni_range': list(range(2024, oggi.year + 3)),
        'risultato': risultato,
        'errore': errore,
        'form_data': form_data,
        'ha_scenario': ha_scenario,
        'scenari_salvati': scenari_salvati,
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
