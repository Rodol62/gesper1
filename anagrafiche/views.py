import csv
import json
import re
from collections import defaultdict
from io import StringIO

from django.core.paginator import Paginator
from django.db.models import Count, Q
from django.http import Http404, HttpResponse
from django.shortcuts import render, get_object_or_404, redirect
from django.contrib.auth.decorators import login_required, user_passes_test
from django.contrib import messages
from django.utils import timezone
from django.forms import inlineformset_factory, BaseInlineFormSet
from .models import Dipendente, Azienda
from .forms import (
    AssegnazioneTurnoForm,
    AziendaForm,
    DipendenteForm,
    date_validita_default_per_turno,
)
from accounts.tenant import get_azienda_operativa
from accounts.pagination import pagination_window
from django.core.files.base import ContentFile


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
            messages.success(request, f'Azienda {azienda.nome} creata con successo.')
            return redirect('lista_aziende')
    else:
        form = AziendaForm()

    return render(request, 'anagrafiche/form_azienda.html', {
        'form': form,
        'titolo': 'Nuova azienda',
    })


@login_required
@user_passes_test(lambda u: u.is_superuser or getattr(u, 'ruolo', None) == 'admin')
def modifica_azienda(request, pk):
    azienda = get_object_or_404(Azienda, pk=pk)

    if request.method == 'POST':
        form = AziendaForm(request.POST, instance=azienda)
        if form.is_valid():
            form.save()
            messages.success(request, f'Azienda {azienda.nome} aggiornata con successo.')
            return redirect('lista_aziende')
    else:
        form = AziendaForm(instance=azienda)

    return render(request, 'anagrafiche/form_azienda.html', {
        'form': form,
        'titolo': f'Modifica azienda: {azienda.nome}',
        'azienda': azienda,
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
