import csv
import json
import re
from collections import defaultdict
from datetime import timedelta
from io import StringIO

from django.contrib.auth import get_user_model
from django.core.paginator import Paginator
from django.core.mail import EmailMessage, get_connection
from django.db.models import Count, Q
from django.http import Http404, HttpResponse
from django.http import JsonResponse
from django.shortcuts import render, get_object_or_404, redirect
from django.contrib.auth.decorators import login_required, user_passes_test
from django.contrib import messages
from django.urls import reverse
from django.utils import timezone
from django.forms import inlineformset_factory, BaseInlineFormSet
from .models import Dipendente, Azienda, ComunicazioneRecessoProva
from .territorio_it import (
    regioni as regioni_it,
    province_per_regione,
    comuni_per_regione_provincia,
    dataset_sources_info,
)
from .forms import (
    AssegnazioneTurnoForm,
    AziendaForm,
    DipendenteForm,
    date_validita_default_per_turno,
)
from accounts.tenant import get_azienda_operativa
from accounts.pagination import pagination_window
from accounts.models import ConfigurazioneSistema
from anagrafiche.nominatim_geocode import geocode_indirizzo_it, user_agent_gesper
from accounts.outbound_uri import outbound_absolute_uri
from django.core.files.base import ContentFile
from rapporto_di_lavoro.models import RapportoDiLavoro

User = get_user_model()


def _puo_modificare_anagrafica_azienda(user, azienda) -> bool:
    """Superuser / admin app; HR e consulente solo sull’azienda collegata al profilo."""
    if not user.is_authenticated or azienda is None:
        return False
    if user.is_superuser or user.has_ruolo('admin'):
        return True
    aid = getattr(user, 'azienda_id', None)
    if not aid or azienda.pk != aid:
        return False
    return user.has_ruolo('hr') or user.has_ruolo('consulente')


def _puo_usare_geocode_anagrafiche(user) -> bool:
    if not user.is_authenticated:
        return False
    if user.is_superuser or user.has_ruolo('admin'):
        return True
    return (user.has_ruolo('hr') or user.has_ruolo('consulente')) and bool(getattr(user, 'azienda_id', None))


def _sync_configurazione_sistema_da_azienda(azienda, request):
    """
    Allinea la riga singleton ConfigurazioneSistema (documenti / default geofence)
    quando l’azienda salvata è l’unica in archivio oppure coincide con l’azienda operativa in sessione.
    """
    only_one = Azienda.objects.count() == 1
    op = get_azienda_operativa(request.user, request.session)
    if not only_one and not (op and op.pk == azienda.pk):
        return
    cfg = ConfigurazioneSistema.get()
    cfg.nome_azienda = azienda.nome or ''
    cfg.partita_iva = azienda.partita_iva or ''
    cfg.indirizzo_sede = (azienda.indirizzo or '')[:255]
    cfg.firmatario_amministratore_nome = (azienda.amministratore_pro_tempore_nome or '')[:150]
    cfg.firmatario_amministratore_ruolo = (azienda.amministratore_pro_tempore_ruolo or '')[:150]
    if azienda.sede_lavorativa_lat is not None and azienda.sede_lavorativa_lon is not None:
        cfg.presenze_geo_center_lat = azienda.sede_lavorativa_lat
        cfg.presenze_geo_center_lon = azienda.sede_lavorativa_lon
    cfg.save(update_fields=[
        'nome_azienda', 'partita_iva', 'indirizzo_sede',
        'firmatario_amministratore_nome', 'firmatario_amministratore_ruolo',
        'presenze_geo_center_lat', 'presenze_geo_center_lon',
    ])


def _puo_eliminare_dipendente(request, dipendente):
    """Solo admin (su azienda operativa) o HR (sulla propria azienda)."""
    u = request.user
    if u.is_superuser or u.has_ruolo('admin'):
        az = get_azienda_operativa(u, request.session)
        return az is not None and dipendente.azienda_id == az.id
    if u.has_ruolo('hr'):
        return getattr(u, 'azienda_id', None) and dipendente.azienda_id == u.azienda_id
    return False


def _dipendente_accessibile(request, dipendente):
    """
    Stessi criteri della lista: admin su azienda operativa, HR/consulente sulla propria azienda,
    dipendente solo sulla propria scheda (utente collegato).
    """
    u = request.user
    if u.is_superuser or u.has_ruolo('admin'):
        az = get_azienda_operativa(u, request.session)
        return az is not None and dipendente.azienda_id == az.id
    if u.has_ruolo('hr') or u.has_ruolo('consulente'):
        return getattr(u, 'azienda_id', None) and dipendente.azienda_id == u.azienda_id
    if u.has_ruolo('dipendente'):
        return dipendente.utente_id == u.id
    return False


def _export_csv_dipendenti(qs, filename_prefix='dipendenti'):
    qs = qs.select_related('azienda').order_by('cognome', 'nome')
    buf = StringIO()
    writer = csv.writer(buf, delimiter=';', quoting=csv.QUOTE_MINIMAL)
    writer.writerow([
        'Matricola', 'Cognome', 'Nome', 'Codice fiscale', 'Data assunzione', 'Data cessazione',
        'Stato', 'Ruolo', 'Mansione', 'Azienda',
    ])
    for d in qs:
        mans = d.get_mansione_display() if d.mansione else ''
        writer.writerow([
            d.matricola if d.matricola is not None else '',
            d.cognome or '',
            d.nome or '',
            d.codice_fiscale or '',
            d.data_assunzione.strftime('%d/%m/%Y') if d.data_assunzione else '',
            d.data_cessazione.strftime('%d/%m/%Y') if d.data_cessazione else '',
            d.get_stato_display(),
            d.ruolo or '',
            mans,
            d.azienda.nome if d.azienda_id else '',
        ])
    resp = HttpResponse(
        '\ufeff' + buf.getvalue(),
        content_type='text/csv; charset=utf-8',
    )
    resp['Content-Disposition'] = f'attachment; filename="{filename_prefix}.csv"'
    return resp


def _fmt_data_it(value):
    return value.strftime('%d/%m/%Y') if value else ''


@login_required
def api_regioni_italia(request):
    return JsonResponse({'items': regioni_it()})


@login_required
def api_province_italia(request):
    regione = (request.GET.get('regione') or '').strip()
    items = province_per_regione(regione)
    return JsonResponse({'items': items})


@login_required
def api_comuni_italia(request):
    regione = (request.GET.get('regione') or '').strip()
    provincia = (request.GET.get('provincia') or '').strip()
    items = comuni_per_regione_provincia(regione, provincia)
    return JsonResponse({'items': items})


@login_required
def api_geocode_indirizzo_anagrafiche(request):
    """Geocoding Nominatim per form anagrafica (stesso motore delle Impostazioni)."""
    if request.method != 'POST':
        return JsonResponse({'ok': False, 'error': 'Metodo non consentito.'}, status=405)
    if not _puo_usare_geocode_anagrafiche(request.user):
        return JsonResponse({'ok': False, 'error': 'Accesso non autorizzato.'}, status=403)
    indirizzo = (request.POST.get('indirizzo') or '').strip()
    contact = (getattr(request.user, 'email', None) or '').strip()
    ua = user_agent_gesper(contact)
    result = geocode_indirizzo_it(indirizzo, user_agent=ua)
    if result.get('ok'):
        return JsonResponse({
            'ok': True,
            'lat': result['lat'],
            'lon': result['lon'],
            'display_name': result.get('display_name', ''),
        })
    err = result.get('error', 'Errore sconosciuto.')
    if err == 'Indirizzo troppo corto.':
        return JsonResponse({'ok': False, 'error': err}, status=400)
    if err.startswith('Nessun risultato'):
        return JsonResponse({'ok': False, 'error': err}, status=404)
    return JsonResponse({'ok': False, 'error': err}, status=502)


@login_required
def api_decodifica_cf(request):
    """Decodifica anagrafica da CF (validazione MEF + Belfiore). Solo GET, JSON."""
    from anagrafiche.codice_fiscale_it import (
        decodifica_codice_fiscale,
        serializza_decodifica,
        valida_cf,
    )

    cf = (request.GET.get('cf') or '').strip().upper()
    if len(cf) != 16:
        return JsonResponse(
            {'ok': False, 'error': 'Il codice fiscale deve essere di 16 caratteri.'},
            status=400,
        )
    if not valida_cf(cf):
        return JsonResponse(
            {
                'ok': False,
                'error': 'Codice fiscale non valido (formato o carattere di controllo MEF / Agenzia Entrate).',
            },
            status=400,
        )
    dec = decodifica_codice_fiscale(cf)
    if not dec:
        return JsonResponse(
            {
                'ok': False,
                'error': 'Impossibile decodificare data o luogo di nascita dal codice fiscale.',
            },
            status=400,
        )
    payload = serializza_decodifica(dec)
    payload['ok'] = True
    return JsonResponse(payload)


def _riga_cap_citta_residenza_dipendente(dipendente) -> str:
    """CAP e località di residenza dal dipendente (Italia o estero)."""
    cap = (getattr(dipendente, 'cap', None) or '').strip()
    citta = (getattr(dipendente, 'citta', None) or '').strip()
    prov = (getattr(dipendente, 'provincia', None) or '').strip()
    reg = (getattr(dipendente, 'regione_residenza', None) or '').strip().upper()
    paese = (getattr(dipendente, 'paese_residenza', None) or '').strip()
    pe = paese.upper() if paese else ''
    if reg == 'ESTERO' or (pe and pe != 'ITALIA'):
        pezzo = ' '.join(x for x in [cap, citta] if x).strip()
        if pe and pe != 'ITALIA':
            pezzo = f"{pezzo} ({paese})".strip() if pezzo else paese
        return pezzo or '[CAP - Città]'
    pezzi = []
    if cap:
        pezzi.append(cap)
    if citta and prov:
        pezzi.append(f"{citta} ({prov})")
    elif citta:
        pezzi.append(citta)
    elif prov:
        pezzi.append(f"({prov})")
    out = ' '.join(pezzi).strip()
    return out or '[CAP - Città]'


def _estrai_luogo_da_indirizzo_sede(indirizzo: str) -> str:
    """Estrae una denominazione di luogo (es. comune) dall’ultimo segmento dell’indirizzo, se riconoscibile."""
    s = (indirizzo or '').strip()
    if not s:
        return ''
    ultimo = s.split(',')[-1].strip()
    m = re.match(r'^(\d{5})\s+(.+)$', ultimo)
    if m:
        return m.group(2).strip()
    if re.match(r'^\d{5}$', ultimo):
        return ''
    return ultimo


def _build_testo_recesso_prova(*, rapporto, dipendente, oggi):
    giorni_prova = int(getattr(rapporto.tipo_contratto, 'prova_giorni', 0) or 0)
    cfg = ConfigurazioneSistema.get()
    azienda = rapporto.azienda
    # Luogo data lettera: prima indirizzo anagrafica azienda del rapporto, poi sede legale in impostazioni globali.
    luogo_firma = (
        _estrai_luogo_da_indirizzo_sede(getattr(azienda, 'indirizzo', '') or '')
        or _estrai_luogo_da_indirizzo_sede(cfg.indirizzo_sede)
    )
    if not luogo_firma:
        luogo_firma = '[Luogo]'
    nome_firmatario = (cfg.firmatario_amministratore_nome or '').strip()
    ruolo_firmatario = (cfg.firmatario_amministratore_ruolo or '').strip()
    if not nome_firmatario:
        nome_firmatario = '[Nome e Cognome del Titolare / Legale rappresentante]'
    if not ruolo_firmatario:
        ruolo_firmatario = '[Ruolo]'
    righe_indirizzo = [f"Spett.le {dipendente.nome} {dipendente.cognome}"]
    righe_indirizzo.append((dipendente.indirizzo or '').strip() or '[Indirizzo]')
    righe_indirizzo.append(_riga_cap_citta_residenza_dipendente(dipendente))
    tipo_contratto_label = rapporto.tipo_contratto.nome if rapporto.tipo_contratto else 'contratto di lavoro'
    if rapporto.data_fine_rapporto:
        riferimento_contratto = (
            f"con {tipo_contratto_label} a tempo determinato fino al {_fmt_data_it(rapporto.data_fine_rapporto)}"
        )
    else:
        riferimento_contratto = f"con {tipo_contratto_label} a tempo indeterminato"
    if giorni_prova > 0:
        clausola_prova = (
            f"con periodo di prova della durata di {giorni_prova} giorni di calendario "
            "ai sensi dell'art. 95 del CCNL FIPE - Imprese Minori e dell'art. 3 del contratto individuale di lavoro"
        )
    else:
        clausola_prova = "con periodo di prova come da contratto individuale di lavoro e CCNL applicato"
    return (
        "Oggetto: Recesso dal rapporto di lavoro durante il periodo di prova\n\n"
        f"{righe_indirizzo[0]}\n{righe_indirizzo[1]}\n{righe_indirizzo[2]}\n\n"
        f"La presente per comunicarLe che {azienda.nome}, presso cui Lei è stato/a assunto/a "
        f"in data {_fmt_data_it(rapporto.data_inizio_rapporto)} {riferimento_contratto}, {clausola_prova},\n\n"
        "intende esercitare il diritto di recesso dal rapporto di lavoro durante il periodo di prova, con effetto immediato.\n\n"
        "Come previsto dalla normativa applicabile, il recesso avviene senza obbligo di preavviso, con corresponsione della "
        "retribuzione maturata sino alla data odierna, comprensiva - ove spettanti - delle frazioni di mensilità aggiuntive e del TFR.\n\n"
        "Si invita a verificare con attenzione le ore lavorative maturate fino alla data di cessazione, "
        "al fine di consentire il corretto conteggio delle spettanze finali.\n\n"
        "La invitiamo a restituire eventuali beni aziendali in Suo possesso e a completare gli adempimenti amministrativi necessari "
        "alla chiusura del rapporto.\n\n"
        "Distinti saluti.\n\n"
        f"{luogo_firma}, {_fmt_data_it(oggi)}\n\n"
        f"{nome_firmatario}\n"
        f"{ruolo_firmatario}\n"
        f"{azienda.nome}\n"
    )


@login_required
def lista_dipendenti(request):
    stato_filter = (request.GET.get('stato') or '').strip().lower()
    q_filter = (request.GET.get('q') or '').strip()

    if request.user.is_superuser or request.user.has_ruolo('admin'):
        azienda_operativa = get_azienda_operativa(request.user, request.session)
        base_qs = Dipendente.objects.filter(azienda=azienda_operativa) if azienda_operativa else Dipendente.objects.none()
    elif request.user.has_ruolo('hr'):
        base_qs = Dipendente.objects.filter(azienda=request.user.azienda)
        azienda_operativa = request.user.azienda
    elif request.user.has_ruolo('consulente'):
        base_qs = Dipendente.objects.filter(azienda=request.user.azienda)
        azienda_operativa = request.user.azienda
    elif request.user.has_ruolo('dipendente'):
        base_qs = Dipendente.objects.filter(utente=request.user)
        azienda_operativa = request.user.azienda
    else:
        base_qs = Dipendente.objects.none()
        azienda_operativa = None

    conteggi_agg = base_qs.aggregate(
        tutti=Count('id'),
        attivo=Count('id', filter=Q(stato='attivo')),
        cessato=Count('id', filter=Q(stato='cessato')),
        candidato=Count('id', filter=Q(stato='candidato')),
    )
    conteggi = {
        'tutti': conteggi_agg['tutti'] or 0,
        'attivo': conteggi_agg['attivo'] or 0,
        'cessato': conteggi_agg['cessato'] or 0,
        'candidato': conteggi_agg['candidato'] or 0,
    }

    dipendenti = base_qs.select_related('azienda')
    if stato_filter in {'attivo', 'cessato', 'candidato'}:
        dipendenti = dipendenti.filter(stato=stato_filter)

    if q_filter:
        qobj = (
            Q(cognome__icontains=q_filter)
            | Q(nome__icontains=q_filter)
            | Q(codice_fiscale__icontains=q_filter)
        )
        if q_filter.isdigit():
            qobj |= Q(matricola=int(q_filter))
        dipendenti = dipendenti.filter(qobj)

    dipendenti = dipendenti.order_by('cognome', 'nome')

    export = (request.GET.get('export') or '').strip().lower()
    if export == 'csv':
        if not (
            request.user.is_superuser
            or request.user.has_ruolo('admin')
            or request.user.has_ruolo('hr')
            or request.user.has_ruolo('consulente')
        ):
            raise Http404()
        prefix = 'dipendenti'
        if azienda_operativa:
            prefix = f"dipendenti_{re.sub(r'[^a-zA-Z0-9_-]+', '_', azienda_operativa.nome)[:40]}"
        return _export_csv_dipendenti(dipendenti, filename_prefix=prefix)

    paginator = Paginator(dipendenti, 25)
    page_obj = paginator.get_page(request.GET.get('page') or 1)

    return render(
        request,
        'anagrafiche/lista_dipendenti.html',
        {
            'page_obj': page_obj,
            'dipendenti': page_obj,
            'azienda_operativa': azienda_operativa,
            'stato_filter': stato_filter,
            'q_filter': q_filter,
            'conteggi': conteggi,
        },
    )


@login_required
@user_passes_test(lambda u: u.is_superuser or (u.has_ruolo('admin') or u.has_ruolo('hr')))
def crea_dipendente(request):
    if request.user.is_superuser or request.user.has_ruolo('admin'):
        azienda_operativa = get_azienda_operativa(request.user, request.session)
        if not azienda_operativa:
            messages.error(request, "Devi prima selezionare un'azienda operativa.")
            return redirect('lista_aziende')
    else:
        azienda_operativa = request.user.azienda

    if request.method == 'POST':
        form = DipendenteForm(request.POST, azienda_operativa=azienda_operativa)
        if form.is_valid():
            dipendente = form.save(commit=False)
            dipendente.azienda = azienda_operativa
            dipendente.save()
            messages.success(request, f'Dipendente {dipendente.nome} {dipendente.cognome} creato con successo.')
            return redirect('lista_dipendenti')
    else:
        form = DipendenteForm(azienda_operativa=azienda_operativa)

    return render(request, 'anagrafiche/crea_dipendente.html', {
        'form': form,
        'azienda_operativa': azienda_operativa,
        'territorio_dataset_info': dataset_sources_info(),
    })


@login_required
def dettaglio_dipendente(request, pk):
    import datetime
    from documenti.models import Documento
    from presenze.models import Presenza
    dipendente = get_object_or_404(
        Dipendente.objects.select_related('azienda', 'utente'),
        pk=pk,
    )
    if not _dipendente_accessibile(request, dipendente):
        raise Http404()

    puo_gestire_contratto_cartaceo_hr = (
        request.user.is_superuser
        or request.user.has_ruolo('admin')
        or request.user.has_ruolo('hr')
        or request.user.has_ruolo('consulente')
    )
    if request.method == 'POST' and request.POST.get('action') == 'upload_contratto_cartaceo_hr':
        if not puo_gestire_contratto_cartaceo_hr:
            messages.error(request, 'Operazione non consentita.')
            return redirect('dettaglio_dipendente', pk=pk)
        f = request.FILES.get('contratto_firmato_pdf_hr')
        rid = (request.POST.get('contratto_id') or '').strip()
        qs_cartaceo = RapportoDiLavoro.objects.filter(
            dipendente=dipendente,
            stato__in=('sottoscritto', 'sospeso'),
        ).order_by('-data_inizio_rapporto', '-id')
        if rid.isdigit():
            contratto = qs_cartaceo.filter(pk=int(rid)).first()
        else:
            contratto = qs_cartaceo.first()
        if not contratto:
            messages.error(
                request,
                'Nessun contratto in stato sottoscritto o sospeso: impossibile allegare il PDF firmato su carta.',
            )
            return redirect('dettaglio_dipendente', pk=pk)
        if not f:
            messages.error(request, 'Seleziona un file PDF da caricare.')
            return redirect('dettaglio_dipendente', pk=pk)
        from rapporto_di_lavoro.views import registra_contratto_firmato_cartaceo_da_hr

        try:
            registra_contratto_firmato_cartaceo_da_hr(contratto, request.user, f)
            messages.success(
                request,
                'PDF del contratto firmato su carta caricato. '
                'È disponibile tra i documenti del dipendente e come PDF del rapporto in portale.',
            )
        except ValueError as exc:
            messages.error(request, str(exc))
        return redirect('dettaglio_dipendente', pk=pk)

    oggi = datetime.date.today()
    presenze_mese = Presenza.objects.filter(
        dipendente=dipendente,
        data__year=oggi.year,
        data__month=oggi.month,
    ).order_by('data')

    docs_all = list(
        Documento.objects.filter(dipendente_id=dipendente.pk).only(
            'id', 'tipo', 'descrizione', 'data_caricamento',
        ).order_by('-data_caricamento', '-id')
    )
    buste_paga = [d for d in docs_all if d.tipo == 'busta_paga']
    mesi_nome = {
        1: 'Gennaio', 2: 'Febbraio', 3: 'Marzo', 4: 'Aprile',
        5: 'Maggio', 6: 'Giugno', 7: 'Luglio', 8: 'Agosto',
        9: 'Settembre', 10: 'Ottobre', 11: 'Novembre', 12: 'Dicembre',
    }
    periodo_re = re.compile(r'(?P<mese>\d{2})/(?P<anno>\d{4})')
    buste_per_anno_map = defaultdict(dict)
    for busta in buste_paga:
        mese = None
        anno = None
        descrizione = busta.descrizione or ''
        m = periodo_re.search(descrizione)
        if m:
            mese = int(m.group('mese'))
            anno = int(m.group('anno'))
        if not anno:
            anno = busta.data_caricamento.year if busta.data_caricamento else oggi.year
        mese_key = mese if mese and 1 <= mese <= 12 else 0
        corrente = buste_per_anno_map[anno].get(mese_key)
        # Deduplica per periodo: mantiene il record più recente.
        if corrente is None:
            buste_per_anno_map[anno][mese_key] = busta
        else:
            corrente_ts = corrente.data_caricamento.timestamp() if corrente.data_caricamento else 0
            nuovo_ts = busta.data_caricamento.timestamp() if busta.data_caricamento else 0
            if (nuovo_ts, busta.id) > (corrente_ts, corrente.id):
                buste_per_anno_map[anno][mese_key] = busta

    buste_per_anno = []
    for anno, mesi_map in sorted(buste_per_anno_map.items(), key=lambda x: x[0], reverse=True):
        mesi = []
        for mese_num, doc in sorted(mesi_map.items(), key=lambda x: x[0], reverse=True):
            if mese_num > 0:
                mese_label = mesi_nome.get(mese_num, f'Mese {mese_num:02d}')
                busta_label = f'Busta Paga {mese_label}/{anno}'
            else:
                mese_label = 'Senza periodo'
                busta_label = f'Busta Paga {anno}'
            mesi.append({
                'mese': mese_num,
                'mese_label': mese_label,
                'doc': doc,
                'busta_label': busta_label,
            })
        buste_per_anno.append({'anno': anno, 'mesi': mesi})

    cud_docs = [
        d for d in docs_all
        if d.tipo == 'certificato' and 'cud' in (d.descrizione or '').lower()
    ]
    anno_cud_re = re.compile(r'(20\d{2})')
    cud_per_anno_map = defaultdict(list)
    for cud in cud_docs:
        anno = None
        m = anno_cud_re.search(cud.descrizione or '')
        if m:
            anno = int(m.group(1))
        if anno is None:
            anno = cud.data_caricamento.year if cud.data_caricamento else oggi.year
        cud_per_anno_map[anno].append(cud)
    cud_per_anno = []
    for anno, docs in sorted(cud_per_anno_map.items(), key=lambda x: x[0], reverse=True):
        docs_sorted = sorted(
            docs,
            key=lambda d: (
                d.data_caricamento.timestamp() if d.data_caricamento else 0,
                d.id,
            ),
            reverse=True,
        )
        cud_per_anno.append({'anno': anno, 'documenti': docs_sorted})

    from rapporto_di_lavoro.services_contratti import posizione_contrattuale_per_dipendente

    posizioni_contrattuali = posizione_contrattuale_per_dipendente(dipendente)
    puo_registrare_addendum = (
        request.user.is_superuser
        or request.user.has_ruolo('admin')
        or request.user.has_ruolo('hr')
    )
    puo_inviare_certificazione_firma = puo_registrare_addendum

    rapporti_upload_cartaceo = (
        RapportoDiLavoro.objects.filter(
            dipendente=dipendente,
            stato__in=('sottoscritto', 'sospeso'),
        )
        .select_related('tipo_contratto')
        .order_by('-data_inizio_rapporto', '-id')
    )

    return render(request, 'anagrafiche/dettaglio_dipendente.html', {
        'dipendente': dipendente,
        'presenze_mese': presenze_mese,
        'anno_corrente': oggi.year,
        'mese_corrente': oggi.month,
        'buste_per_anno': buste_per_anno,
        'cud_per_anno': cud_per_anno,
        'posizioni_contrattuali': posizioni_contrattuali,
        'puo_registrare_addendum': puo_registrare_addendum,
        'puo_inviare_certificazione_firma': puo_inviare_certificazione_firma,
        'puo_gestire_contratto_cartaceo_hr': puo_gestire_contratto_cartaceo_hr,
        'rapporti_upload_cartaceo': rapporti_upload_cartaceo,
    })


@login_required
def modifica_dipendente(request, pk):
    dipendente = get_object_or_404(Dipendente, pk=pk)

    if not _dipendente_accessibile(request, dipendente):
        messages.error(request, 'Accesso non autorizzato o dipendente non valido per il tuo profilo.')
        return redirect('lista_dipendenti')

    is_admin = request.user.is_superuser or request.user.has_ruolo('admin')
    is_hr = request.user.has_ruolo('hr')
    is_consulente = request.user.has_ruolo('consulente')
    is_dipendente = request.user.has_ruolo('dipendente')

    if is_admin:
        azienda_operativa = get_azienda_operativa(request.user, request.session)
    else:
        azienda_operativa = request.user.azienda

    from rapporto_di_lavoro.models import RapportoDiLavoro
    contratto_attivo = RapportoDiLavoro.objects.filter(
        dipendente=dipendente,
        stato='sottoscritto',
    ).order_by('-data_ora_sottoscrizione', '-data_creazione').first()

    # ── Formset agganci turni (max 4 slot) ────────────────────────────────
    from presenze.models import AssegnazioneTurnoDipendente, TurnoLavorativoAziendale

    # Custom BaseInlineFormSet che passa l'azienda a ogni form
    class _TurnoFormSet(BaseInlineFormSet):
        def __init__(self, *args, **kwargs):
            self._azienda = kwargs.pop('azienda', None)
            super().__init__(*args, **kwargs)

        def get_form_kwargs(self, index):
            kw = super().get_form_kwargs(index)
            kw['azienda'] = self._azienda
            inst = getattr(self, 'instance', None)
            kw['dipendente'] = inst if inst and getattr(inst, 'pk', None) else None
            return kw

    TurnoFormSetClass = inlineformset_factory(
        Dipendente,
        AssegnazioneTurnoDipendente,
        form=AssegnazioneTurnoForm,
        formset=_TurnoFormSet,
        max_num=4,
        extra=4,
        can_delete=True,
    )

    turni_disponibili = list(
        TurnoLavorativoAziendale.objects.filter(
            configurazione__azienda=azienda_operativa, attivo=True
        )
        .select_related('configurazione')
        .order_by('ordine', 'ora_inizio')
    ) if azienda_operativa else []

    turni_date_defaults = {}
    for t in turni_disponibili:
        try:
            dal, al = date_validita_default_per_turno(t, dipendente)
            turni_date_defaults[str(t.pk)] = {'dal': dal.isoformat(), 'al': al.isoformat()}
        except Exception:
            continue
    turni_date_defaults_json = json.dumps(turni_date_defaults)

    if request.method == 'POST' and is_dipendente and request.POST.get('richiedi_integrazione'):
        if not contratto_attivo:
            messages.error(request, "Nessun contratto definitivo trovato per avviare un'integrazione.")
            return redirect('modifica_dipendente', pk=dipendente.pk)

        if not request.POST.get('firma_digitale_dipendente'):
            messages.error(request, "Per inviare l'integrazione devi sottoscrivere digitalmente la richiesta.")
            return redirect('modifica_dipendente', pk=dipendente.pk)

        note = (request.POST.get('note_integrazione') or '').strip()
        if not note:
            messages.error(request, 'Inserisci la descrizione delle variazioni anagrafiche richieste.')
            return redirect('modifica_dipendente', pk=dipendente.pk)

        ts = timezone.now().strftime('%d/%m/%Y %H:%M')
        contenuto = (
            "Richiesta integrazione contrattuale\n"
            f"Data richiesta: {ts}\n"
            f"Dipendente: {dipendente.nome} {dipendente.cognome}\n"
            f"Contratto origine: {contratto_attivo.numero_contratto}\n"
            "Stato: In attesa di approvazione del datore di lavoro\n"
            "Sottoscrizione digitale dipendente: SI\n\n"
            f"Dettaglio variazioni richieste:\n{note}\n"
        )
        ts2 = timezone.now().strftime('%Y%m%d%H%M%S')
        nome_file = f"integrazione_{contratto_attivo.numero_contratto}_{ts2}.txt"

        from documenti.models import Documento
        from storico.models import EventoStorico
        doc = Documento.objects.create(
            azienda=dipendente.azienda,
            dipendente=dipendente,
            tipo='altro',
            descrizione=f'Richiesta integrazione contratto {contratto_attivo.numero_contratto}',
            file=ContentFile(contenuto.encode('utf-8'), name=nome_file),
            caricato_da=request.user,
            caricato_dal_dipendente=True,
            visibile_al_dipendente=True,
        )
        EventoStorico.objects.create(
            dipendente=dipendente,
            azienda=dipendente.azienda,
            tipo='variazione',
            data_evento=timezone.now(),
            descrizione=(
                f'Richiesta integrazione anagrafica sul contratto {contratto_attivo.numero_contratto} '
                'inviata dal dipendente e in attesa di approvazione datore di lavoro.'
            ),
            documento=doc,
        )
        messages.success(request, 'Richiesta integrazione inviata e registrata nei Documenti.')
        return redirect('lista_documenti')

    if request.method == 'POST':
        form = DipendenteForm(
            request.POST,
            instance=dipendente,
            azienda_operativa=azienda_operativa,
            for_dipendente=is_dipendente,
        )
        turno_formset = TurnoFormSetClass(
            request.POST,
            instance=dipendente,
            azienda=azienda_operativa,
        )
        form_ok = form.is_valid()
        fs_ok = turno_formset.is_valid() if not is_dipendente else True
        if form_ok and fs_ok:
            form.save()
            if not is_dipendente:
                # Salva solo righe con turno valorizzato (le vuote vengono saltate da Django)
                instances = turno_formset.save(commit=False)
                for inst in instances:
                    if inst.turno_id:
                        inst.dipendente = dipendente
                        inst.save()
                for obj in turno_formset.deleted_objects:
                    obj.delete()
            messages.success(request, f'Dipendente {dipendente.nome} {dipendente.cognome} aggiornato con successo.')
            return redirect('dettaglio_dipendente', pk=dipendente.pk)
    else:
        form = DipendenteForm(
            instance=dipendente,
            azienda_operativa=azienda_operativa,
            for_dipendente=is_dipendente,
        )
        turno_formset = TurnoFormSetClass(
            instance=dipendente,
            azienda=azienda_operativa,
        )

    from rapporto_di_lavoro.services_contratti import posizione_contrattuale_per_dipendente

    posizioni_contrattuali = []
    puo_registrare_addendum = False
    if not is_dipendente and (is_admin or is_hr or is_consulente):
        posizioni_contrattuali = posizione_contrattuale_per_dipendente(dipendente)
        puo_registrare_addendum = is_admin or is_hr

    return render(request, 'anagrafiche/modifica_dipendente.html', {
        'form': form,
        'dipendente': dipendente,
        'azienda_operativa': azienda_operativa,
        'is_dipendente': is_dipendente,
        'contratto_attivo': contratto_attivo,
        'turno_formset': turno_formset,
        'turni_disponibili': turni_disponibili,
        'turni_date_defaults_json': turni_date_defaults_json,
        'posizioni_contrattuali': posizioni_contrattuali,
        'puo_registrare_addendum': puo_registrare_addendum,
        'territorio_dataset_info': dataset_sources_info(),
    })


@login_required
def elimina_dipendente(request, pk):
    dipendente = get_object_or_404(Dipendente, pk=pk)
    if not _puo_eliminare_dipendente(request, dipendente):
        messages.error(request, 'Non hai permesso di eliminare questo dipendente.')
        return redirect('lista_dipendenti')

    if request.method == 'POST':
        etichetta = f'{dipendente.cognome} {dipendente.nome}'.strip()
        dipendente.delete()
        messages.success(request, f'Dipendente {etichetta or "selezionato"} eliminato.')
        return redirect('lista_dipendenti')

    return render(request, 'anagrafiche/elimina_dipendente.html', {'dipendente': dipendente})


@login_required
@user_passes_test(lambda u: u.is_superuser or u.has_ruolo('admin'))
def lista_aziende(request):
    if request.method == 'POST' and (request.user.is_superuser or request.user.has_ruolo('admin')):
        azienda_id = request.POST.get('azienda_id')
        azienda = Azienda.objects.filter(id=azienda_id).first()
        if azienda:
            request.user.azienda = azienda
            request.user.save(update_fields=['azienda'])
            request.session['azienda_id'] = azienda.id
            return redirect('lista_dipendenti')
    aziende_qs = Azienda.objects.select_related(
        'ccnl_predefinito',
        'tipo_contratto_predefinito',
    ).order_by('nome')
    paginator = Paginator(aziende_qs, 25)
    page_obj = paginator.get_page(request.GET.get('page') or 1)
    return render(request, 'anagrafiche/lista_aziende.html', {
        'page_obj': page_obj,
        'aziende': page_obj,
    })


@login_required
@user_passes_test(lambda u: u.is_superuser or getattr(u, 'ruolo', None) == 'admin')
def crea_azienda(request):
    if request.method == 'POST':
        form = AziendaForm(request.POST)
        if form.is_valid():
            azienda = form.save()
            _sync_configurazione_sistema_da_azienda(azienda, request)
            messages.success(request, f'Azienda {azienda.nome} creata con successo.')
            return redirect('lista_aziende')
    else:
        form = AziendaForm()

    return render(request, 'anagrafiche/form_azienda.html', {
        'form': form,
        'titolo': 'Nuova azienda',
        'territorio_dataset_info': dataset_sources_info(),
    })


@login_required
def modifica_azienda(request, pk):
    azienda = get_object_or_404(Azienda, pk=pk)
    if not _puo_modificare_anagrafica_azienda(request.user, azienda):
        messages.error(request, 'Non hai permesso di modificare questa anagrafica aziendale.')
        if request.user.has_ruolo('consulente'):
            return redirect('consulente_dashboard')
        return redirect('lista_dipendenti')

    if request.method == 'POST':
        form = AziendaForm(request.POST, instance=azienda)
        if form.is_valid():
            form.save()
            azienda.refresh_from_db()
            _sync_configurazione_sistema_da_azienda(azienda, request)
            messages.success(request, f'Azienda {azienda.nome} aggiornata con successo.')
            return redirect('lista_aziende')
    else:
        form = AziendaForm(instance=azienda)

    return render(request, 'anagrafiche/form_azienda.html', {
        'form': form,
        'titolo': f'Modifica azienda: {azienda.nome}',
        'azienda': azienda,
        'territorio_dataset_info': dataset_sources_info(),
    })


@login_required
@user_passes_test(
    lambda u: u.is_superuser or u.has_ruolo('admin') or u.has_ruolo('hr')
)
def toggle_convalidato(request, pk):
    """Abilita/disabilita l'accesso all'app per un dipendente (campo convalidato sull'utente)."""
    dipendente = get_object_or_404(Dipendente, pk=pk)
    u = request.user
    if u.is_superuser or u.has_ruolo('admin'):
        az = get_azienda_operativa(u, request.session)
        if az is None or dipendente.azienda_id != az.id:
            messages.error(request, 'Operazione non consentita per questo dipendente.')
            return redirect('dettaglio_dipendente', pk=pk)
    elif u.has_ruolo('hr'):
        if not getattr(u, 'azienda_id', None) or dipendente.azienda_id != u.azienda_id:
            messages.error(request, 'Operazione non consentita per questo dipendente.')
            return redirect('dettaglio_dipendente', pk=pk)
    if request.method != 'POST':
        from django.http import HttpResponseNotAllowed
        return HttpResponseNotAllowed(['POST'])

    utente = dipendente.utente
    if not utente:
        messages.error(request, 'Questo dipendente non ha un account app associato.')
        return redirect('dettaglio_dipendente', pk=pk)

    utente.convalidato = not utente.convalidato
    utente.save(update_fields=['convalidato'])
    stato = 'abilitato' if utente.convalidato else 'disabilitato'
    messages.success(request, f'Accesso app {stato} per {dipendente.nome} {dipendente.cognome}.')
    return redirect('dettaglio_dipendente', pk=pk)


@login_required
@user_passes_test(
    lambda u: u.is_superuser or u.has_ruolo('admin') or u.has_ruolo('hr') or u.has_ruolo('consulente')
)
def genera_comunicazione_recesso_prova(request, pk, rapporto_id):
    dipendente = get_object_or_404(
        Dipendente.objects.select_related('azienda'),
        pk=pk,
    )
    if not _dipendente_accessibile(request, dipendente):
        raise Http404()

    rapporto = get_object_or_404(
        RapportoDiLavoro.objects.select_related('tipo_contratto', 'azienda', 'dipendente'),
        pk=rapporto_id,
        dipendente=dipendente,
    )

    if rapporto.stato not in ('sottoscritto', 'sospeso'):
        messages.error(request, 'La comunicazione puo essere generata solo per contratti attivi o sospesi.')
        return redirect('dettaglio_dipendente', pk=dipendente.pk)

    comunicazione, created = ComunicazioneRecessoProva.objects.get_or_create(
        rapporto=rapporto,
        defaults={
            'azienda': dipendente.azienda,
            'dipendente': dipendente,
            'testo_bozza': _build_testo_recesso_prova(rapporto=rapporto, dipendente=dipendente, oggi=timezone.localdate()),
            'creato_da': request.user,
            'modificato_da': request.user,
        },
    )
    if created:
        messages.success(request, 'Bozza comunicazione recesso creata.')
    return redirect('workflow_recesso_prova', pk=dipendente.pk, rapporto_id=rapporto.id)


def _render_recesso_pdf_bytes(comunicazione):
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas

    from io import BytesIO
    out = BytesIO()
    c = canvas.Canvas(out, pagesize=A4)
    width, height = A4
    y = height - 60
    c.setFont('Helvetica-Bold', 12)
    c.drawString(40, y, f"Comunicazione Recesso - Contratto {comunicazione.rapporto.numero_contratto}")
    y -= 28
    c.setFont('Helvetica', 10)
    for line in (comunicazione.testo_bozza or '').splitlines():
        if y < 60:
            c.showPage()
            c.setFont('Helvetica', 10)
            y = height - 50
        c.drawString(40, y, line[:150])
        y -= 14
    c.showPage()
    c.save()
    return out.getvalue()


def _send_email_recesso_with_pdf(request, comunicazione, pdf_doc):
    dip = comunicazione.dipendente
    destinatario = (dip.email or '').strip() or (getattr(dip.utente, 'email', '') or '').strip()
    if not destinatario:
        return False, 'Nessuna email disponibile sul dipendente.'

    cfg = ConfigurazioneSistema.get()
    link = outbound_absolute_uri(
        request,
        reverse('dettaglio_dipendente', kwargs={'pk': dip.pk}),
    )
    subject = f"[{cfg.nome_sito or 'GESPER'}] Comunicazione recesso periodo prova"
    body = (
        f"Gentile {dip.nome} {dip.cognome},\n\n"
        "in allegato trova la comunicazione di recesso durante il periodo di prova.\n"
        "Ti invitiamo a verificare le ore lavorative maturate per il conteggio delle spettanze finali.\n\n"
        f"Per dettagli puoi accedere al portale: {link}\n\n"
        f"{cfg.nome_azienda or comunicazione.azienda.nome}"
    )
    try:
        if cfg.smtp_user and cfg.smtp_password:
            conn = get_connection(
                backend='accounts.email_backend.ConfigurazioneSistemaEmailBackend',
                host=cfg.smtp_host,
                port=cfg.smtp_port,
                username=cfg.smtp_user,
                password=cfg.smtp_password,
                use_tls=cfg.smtp_use_tls and not cfg.smtp_use_ssl,
                use_ssl=cfg.smtp_use_ssl,
                fail_silently=False,
            )
            msg = EmailMessage(subject=subject, body=body, from_email=cfg.from_email(), to=[destinatario], connection=conn)
        else:
            msg = EmailMessage(subject=subject, body=body, to=[destinatario])
        if pdf_doc.file:
            msg.attach_file(pdf_doc.file.path)
        msg.send()
        return True, f'Email inviata a {destinatario}.'
    except Exception as exc:
        return False, str(exc)


@login_required
@user_passes_test(
    lambda u: u.is_superuser or u.has_ruolo('admin') or u.has_ruolo('hr') or u.has_ruolo('consulente')
)
def workflow_recesso_prova(request, pk, rapporto_id):
    dipendente = get_object_or_404(Dipendente.objects.select_related('azienda', 'utente'), pk=pk)
    if not _dipendente_accessibile(request, dipendente):
        raise Http404()
    rapporto = get_object_or_404(
        RapportoDiLavoro.objects.select_related('tipo_contratto', 'azienda'),
        pk=rapporto_id,
        dipendente=dipendente,
    )
    comunicazione = get_object_or_404(ComunicazioneRecessoProva, rapporto=rapporto, dipendente=dipendente)

    puo_modificare = (
        comunicazione.stato in ('bozza', 'in_verifica_consulente', 'verificata_consulente')
        and (request.user.is_superuser or request.user.has_ruolo('admin') or request.user.has_ruolo('hr') or request.user.has_ruolo('consulente'))
    )

    if request.method == 'POST':
        action = (request.POST.get('action') or '').strip()
        if action == 'salva_bozza' and puo_modificare and comunicazione.stato != 'firmata_admin':
            comunicazione.testo_bozza = (request.POST.get('testo_bozza') or '').strip()
            comunicazione.modificato_da = request.user
            comunicazione.save(update_fields=['testo_bozza', 'modificato_da', 'data_modifica'])
            messages.success(request, 'Bozza aggiornata.')
            return redirect('workflow_recesso_prova', pk=pk, rapporto_id=rapporto_id)

        if action == 'invia_consulente' and (request.user.is_superuser or request.user.has_ruolo('admin') or request.user.has_ruolo('hr')):
            comunicazione.stato = 'in_verifica_consulente'
            comunicazione.modificato_da = request.user
            comunicazione.save(update_fields=['stato', 'modificato_da', 'data_modifica'])
            consulenti = User.objects.filter(azienda_id=dipendente.azienda_id, ruoli__codice='consulente', is_active=True).distinct()
            if consulenti.exists():
                cfg = ConfigurazioneSistema.get()
                link = outbound_absolute_uri(
                    request,
                    reverse('workflow_recesso_prova', kwargs={'pk': pk, 'rapporto_id': rapporto_id}),
                )
                for consulente in consulenti:
                    if not consulente.email:
                        continue
                    try:
                        msg = EmailMessage(
                            subject=f"[{cfg.nome_sito or 'GESPER'}] Verifica comunicazione recesso prova",
                            body=(
                                f"E richiesta la tua verifica consulenziale per il dipendente {dipendente.nome} {dipendente.cognome}.\n"
                                f"Contratto: {rapporto.numero_contratto}\n"
                                f"Apri workflow: {link}"
                            ),
                            to=[consulente.email],
                            from_email=cfg.from_email() if cfg.smtp_user else None,
                        )
                        msg.send(fail_silently=True)
                    except Exception:
                        pass
            messages.info(request, 'Richiesta verifica inviata al consulente.')
            return redirect('workflow_recesso_prova', pk=pk, rapporto_id=rapporto_id)

        if action == 'verifica_consulente_ok' and request.user.has_ruolo('consulente'):
            comunicazione.note_consulente = (request.POST.get('note_consulente') or '').strip()
            comunicazione.stato = 'verificata_consulente'
            comunicazione.consulente_verificatore = request.user
            comunicazione.data_verifica_consulente = timezone.now()
            comunicazione.modificato_da = request.user
            comunicazione.save(update_fields=[
                'note_consulente', 'stato', 'consulente_verificatore',
                'data_verifica_consulente', 'modificato_da', 'data_modifica',
            ])
            messages.success(request, 'Comunicazione verificata dal consulente.')
            return redirect('workflow_recesso_prova', pk=pk, rapporto_id=rapporto_id)

        if action == 'firma_admin' and (request.user.is_superuser or request.user.has_ruolo('admin')):
            if comunicazione.stato != 'verificata_consulente':
                messages.error(request, 'La firma amministratore richiede prima la verifica consulente.')
                return redirect('workflow_recesso_prova', pk=pk, rapporto_id=rapporto_id)
            cfg = ConfigurazioneSistema.get()
            comunicazione.firmatario_nome = (cfg.firmatario_amministratore_nome or request.user.get_full_name() or request.user.username).strip()
            comunicazione.firmatario_ruolo = (cfg.firmatario_amministratore_ruolo or 'Legale rappresentante').strip()
            pdf_bytes = _render_recesso_pdf_bytes(comunicazione)
            from documenti.models import Documento
            nome_file = f"recesso_prova_{rapporto.numero_contratto}_{timezone.now().strftime('%Y%m%d%H%M%S')}.pdf"
            pdf_doc = Documento.objects.create(
                azienda=dipendente.azienda,
                dipendente=dipendente,
                tipo='altro',
                descrizione=f'Comunicazione recesso periodo prova firmata - {rapporto.numero_contratto}',
                file=ContentFile(pdf_bytes, name=nome_file),
                caricato_da=request.user,
                caricato_dal_dipendente=False,
                visibile_al_dipendente=True,
            )
            comunicazione.documento_pdf = pdf_doc
            comunicazione.firmato_da_admin = request.user
            comunicazione.data_firma_admin = timezone.now()
            comunicazione.stato = 'firmata_admin'
            comunicazione.modificato_da = request.user
            comunicazione.save(update_fields=[
                'documento_pdf', 'firmato_da_admin', 'data_firma_admin', 'stato',
                'firmatario_nome', 'firmatario_ruolo', 'modificato_da', 'data_modifica',
            ])
            messages.success(request, 'Comunicazione firmata digitalmente dall amministratore e PDF generato.')
            return redirect('workflow_recesso_prova', pk=pk, rapporto_id=rapporto_id)

        if action == 'invia_dipendente' and (request.user.is_superuser or request.user.has_ruolo('admin') or request.user.has_ruolo('hr')):
            if comunicazione.stato != 'firmata_admin' or not comunicazione.documento_pdf_id:
                messages.error(request, 'Prima completa la firma amministratore e la generazione del PDF.')
                return redirect('workflow_recesso_prova', pk=pk, rapporto_id=rapporto_id)
            ok, info = _send_email_recesso_with_pdf(request, comunicazione, comunicazione.documento_pdf)
            if ok:
                comunicazione.stato = 'inviata_dipendente'
                comunicazione.inviata_email = True
                comunicazione.data_invio_email = timezone.now()
                comunicazione.modificato_da = request.user
                comunicazione.save(update_fields=['stato', 'inviata_email', 'data_invio_email', 'modificato_da', 'data_modifica'])
                messages.success(request, info)
            else:
                messages.error(request, f'Invio email non riuscito: {info}')
            return redirect('workflow_recesso_prova', pk=pk, rapporto_id=rapporto_id)

    return render(
        request,
        'anagrafiche/recesso_prova_workflow.html',
        {
            'dipendente': dipendente,
            'rapporto': rapporto,
            'comunicazione': comunicazione,
            'puo_modificare': puo_modificare,
        },
    )
