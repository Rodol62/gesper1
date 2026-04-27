"""
views_consulente.py — Interfaccia consulente del lavoro.

Funzionalità:
  1. Dashboard con statistiche rapide
  2. Anagrafiche candidati (firmata_candidato / contratto_attivo) + export Excel/CSV
  3. Approva proposta di assunzione (firmata_candidato → contratto_attivo)
  4. Documenti dipendenti (identità, CF, attestati) + download
  5. Presenze dipendenti + export Excel (esistente) + export CSV
  6. Upload buste paga (singola + massiva per dipendente)
  7. Upload CUD (massivo per dipendente)

Accesso: solo utenti con ruolo 'consulente'.
Isolamento azienda: il consulente vede solo i dati di user.azienda.
"""
import calendar
import csv
import io
import os
import tempfile
import re
from datetime import date
from io import BytesIO
from pathlib import Path
from decimal import Decimal

from django.contrib import messages
from django.contrib.auth.decorators import login_required, user_passes_test
from django.conf import settings
from django.core.paginator import Paginator
from django.core.management import call_command
from django.http import HttpResponseForbidden, HttpResponse, FileResponse, Http404
from django.shortcuts import render, get_object_or_404, redirect
from django.urls import reverse
from django.utils import timezone
from django.db.models import Count, Q, Sum
from gesper_next_url import sanitize_internal_next
from django.db.models.functions import Coalesce

from anagrafiche.models import ComunicazioneRecessoProva, Dipendente
from documenti.models import Documento
from presenze.models import Presenza
from rapporto_di_lavoro.models import AddendumContrattuale, PropostaAssunzione, RapportoDiLavoro
from log_attivita.utils import registra_log
from log_attivita.anomalie import registra_evento_anomalia
from .models import MovimentoImportPaghe
import json
from urllib.parse import urlencode

PDF_BUSTE_PASSWORD = 'DOLCEMASCOLO'


# ── Helpers permessi ─────────────────────────────────────────────────────────

def _is_consulente(user):
    return user.is_authenticated and user.has_ruolo('consulente')


def _get_azienda_consulente(user):
    """Restituisce l'azienda associata al consulente (FK su User)."""
    return getattr(user, 'azienda', None)


# Nomi mesi in italiano
MESI_ITA = [
    '', 'Gennaio', 'Febbraio', 'Marzo', 'Aprile', 'Maggio', 'Giugno',
    'Luglio', 'Agosto', 'Settembre', 'Ottobre', 'Novembre', 'Dicembre',
]
MESI_CHOICES = [(i, MESI_ITA[i]) for i in range(1, 13)]
GIORNI_ITA = ['Lun', 'Mar', 'Mer', 'Gio', 'Ven', 'Sab', 'Dom']


def _safe_display(obj, method_name, fallback_attr=''):
    """Restituisce il display di una choice Django senza dipendere dai metodi dinamici per il type checker."""
    method = getattr(obj, method_name, None)
    if callable(method):
        return method()
    return getattr(obj, fallback_attr, '')


def _safe_id(obj):
    """Restituisce l'id del model in modo sicuro per il type checker."""
    return getattr(obj, 'id', None)


def _safe_fk_id(obj, attr_name):
    """Restituisce il valore di una FK *_id in modo sicuro per il type checker."""
    return getattr(obj, attr_name, None)


# ── Dashboard ────────────────────────────────────────────────────────────────

@login_required
@user_passes_test(_is_consulente)
def consulente_dashboard(request):
    """Overview consulente: statistiche rapide."""
    azienda = _get_azienda_consulente(request.user)
    if not azienda:
        messages.error(request, "Nessuna azienda associata al tuo account. Contatta l'amministratore.")
        return redirect('home')

    oggi = date.today()
    dip_st = Dipendente.objects.filter(azienda=azienda, stato__in=('attivo', 'candidato')).aggregate(
        dipendenti_attivi=Count('id', filter=Q(stato='attivo')),
        candidati_count=Count('id', filter=Q(stato='candidato')),
    )
    dipendenti_attivi = dip_st['dipendenti_attivi'] or 0
    candidati_count = dip_st['candidati_count'] or 0
    candidati_recenti = Dipendente.objects.filter(
        azienda=azienda,
        stato='candidato',
    ).order_by('-id')[:8]

    # Per ogni candidato: posizione e livello dalla proposta più recente
    candidati_ids = [_safe_id(d) for d in candidati_recenti if _safe_id(d)]
    candidati_posizione = {}
    # Per ogni candidato recupera posizione/livello dalla proposta più recente
    for p in PropostaAssunzione.objects.filter(
        dipendente_id__in=candidati_ids,
    ).order_by('-id').values('dipendente_id', 'posizione', 'livello_ccnl'):
        did = p['dipendente_id']
        if did not in candidati_posizione:
            candidati_posizione[did] = {
                'posizione': p['posizione'] or '',
                'livello': p['livello_ccnl'] or '',
            }
    # Aggiungi ruolo dal dipendente se non disponibile dalla proposta
    # ed è un valore significativo (non numerico, non uguale allo stato)
    _stati_dip = {'candidato', 'attivo', 'cessato'}
    for dip in candidati_recenti:
        did = _safe_id(dip)
        if did is None:
            continue
        if did not in candidati_posizione:
            r = (getattr(dip, 'ruolo', '') or '').strip()
            if r and r.lower() not in _stati_dip and not r.isdigit():
                candidati_posizione[did] = {'posizione': r, 'livello': ''}
            else:
                candidati_posizione[did] = {'posizione': '', 'livello': ''}

    _stati_firmata_equiv = PropostaAssunzione.stati_equivalenti('firmata_candidato')
    proposte_da_approvare = PropostaAssunzione.objects.filter(
        azienda=azienda, stato__in=_stati_firmata_equiv
    ).count()

    # Documenti paghe/fisco: totali per azienda (stessi ambiti delle liste buste / F24 / CUD senza filtri),
    # così i numeri riflettono i PDF in archivio e non un sottoinsieme (es. solo mese solare corrente).
    doc_kpi = Documento.objects.filter(azienda=azienda).aggregate(
        buste_paga_count=Count('id', filter=Q(tipo='busta_paga')),
        f24_documenti_count=Count('id', filter=Q(tipo='altro', descrizione__icontains='F24')),
        cud_certificati_count=Count('id', filter=Q(tipo='certificato')),
    )
    buste_paga_count = doc_kpi['buste_paga_count'] or 0
    f24_documenti_count = doc_kpi['f24_documenti_count'] or 0
    cud_certificati_count = doc_kpi['cud_certificati_count'] or 0

    presenze_mese_count = Presenza.objects.filter(
        azienda=azienda,
        data__year=oggi.year,
        data__month=oggi.month,
    ).count()

    # Ultimi 5 documenti caricati dal consulente
    ultimi_docs = Documento.objects.filter(
        azienda=azienda,
        caricato_da=request.user,
    ).select_related('dipendente').order_by('-data_caricamento')[:5]

    from rapporto_di_lavoro.services_contratti import contratti_td_in_scadenza, contratti_td_scaduti_non_chiusi

    td_in_scadenza = len(contratti_td_in_scadenza(azienda))
    td_scaduti_aperti = len(contratti_td_scaduti_non_chiusi(azienda))

    contratti_sottoscritti_count = RapportoDiLavoro.objects.filter(
        azienda=azienda, stato='sottoscritto'
    ).count()
    contratti_recenti = (
        RapportoDiLavoro.objects.filter(azienda=azienda)
        .exclude(stato='proposta')
        .select_related('dipendente', 'tipo_contratto')
        .order_by('-data_modifica', '-id')[:10]
    )
    addenda_recenti = (
        AddendumContrattuale.objects.filter(rapporto__azienda=azienda)
        .select_related('rapporto', 'rapporto__dipendente', 'creato_da')
        .order_by('-data_creazione', '-id')[:10]
    )
    addendum_anno_count = AddendumContrattuale.objects.filter(
        rapporto__azienda=azienda,
        data_creazione__year=oggi.year,
    ).count()

    recesso_qs = (
        ComunicazioneRecessoProva.per_azienda(azienda)
        .filter(stato='in_verifica_consulente')
        .select_related('dipendente', 'rapporto')
        .order_by('-data_modifica')
    )
    recesso_prova_in_verifica_count = recesso_qs.count()
    recesso_prova_in_verifica = list(recesso_qs[:12])

    return render(request, 'consulente/dashboard.html', {
        'azienda': azienda,
        'dipendenti_attivi': dipendenti_attivi,
        'candidati_count': candidati_count,
        'candidati_recenti': candidati_recenti,
        'candidati_posizione': candidati_posizione,
        'proposte_da_approvare': proposte_da_approvare,
        'recesso_prova_in_verifica_count': recesso_prova_in_verifica_count,
        'recesso_prova_in_verifica': recesso_prova_in_verifica,
        'buste_paga_count': buste_paga_count,
        'f24_documenti_count': f24_documenti_count,
        'cud_certificati_count': cud_certificati_count,
        'presenze_mese_count': presenze_mese_count,
        'ultimi_docs': ultimi_docs,
        'mese_corrente': MESI_ITA[oggi.month],
        'mese_corrente_num': oggi.month,
        'anno_corrente': oggi.year,
        'td_in_scadenza': td_in_scadenza,
        'td_scaduti_aperti': td_scaduti_aperti,
        'contratti_sottoscritti_count': contratti_sottoscritti_count,
        'contratti_recenti': contratti_recenti,
        'addenda_recenti': addenda_recenti,
        'addendum_anno_count': addendum_anno_count,
    })


# ── Dettaglio proposta (read-only per consulente) ───────────────────────────

@login_required
@user_passes_test(_is_consulente)
def consulente_proposta_detail(request, proposta_id):
    """Visualizzazione read-only di una proposta di assunzione per il consulente."""
    azienda = _get_azienda_consulente(request.user)
    if not azienda:
        return HttpResponseForbidden("Nessuna azienda associata.")
    proposta = get_object_or_404(PropostaAssunzione, id=proposta_id, azienda=azienda)
    return render(request, 'consulente/proposta_detail.html', {
        'proposta': proposta,
        'azienda': azienda,
    })


@login_required
@user_passes_test(_is_consulente)
def consulente_proposta_pdf(request, proposta_id):
    """Genera e serve il PDF della proposta per il consulente."""
    azienda = _get_azienda_consulente(request.user)
    if not azienda:
        return HttpResponseForbidden("Nessuna azienda associata.")
    proposta = get_object_or_404(PropostaAssunzione, id=proposta_id, azienda=azienda)
    next_raw = (request.GET.get('next') or '').strip()
    if (request.GET.get('ui') == '1' or next_raw) and request.GET.get('embed') != '1':
        next_safe = sanitize_internal_next(request, next_raw)
        embed_src = request.build_absolute_uri(
            reverse('consulente_proposta_pdf', kwargs={'proposta_id': proposta_id}) + '?embed=1'
        )
        return render(
            request,
            'common/file_viewer_frame.html',
            {
                'titolo': f'Proposta {proposta.numero_proposta}',
                'embed_src': embed_src,
                'next_url': next_safe,
            },
        )
    try:
        from rapporto_di_lavoro.views import _genera_proposta_pdf, _proposta_context_extra
        extra = _proposta_context_extra(proposta)
        buffer = _genera_proposta_pdf(proposta, extra)
        response = HttpResponse(buffer.getvalue(), content_type='application/pdf')
        nome_file = f"proposta_{proposta.numero_proposta}.pdf"
        response['Content-Disposition'] = f'inline; filename="{nome_file}"'
        return response
    except Exception as exc:
        return HttpResponse(f"Errore nella generazione del PDF: {exc}", status=500)


# ── Anagrafiche candidati ─────────────────────────────────────────────────────

@login_required
@user_passes_test(_is_consulente)
def consulente_candidati(request):
    """Lista candidati con proposta inviata, firmata o contratto attivo + export Excel/CSV."""
    azienda = _get_azienda_consulente(request.user)
    if not azienda:
        return HttpResponseForbidden("Nessuna azienda associata.")

    stato_filter = request.GET.get('stato', '')
    formato = request.GET.get('formato', '')

    _inv = PropostaAssunzione.stati_equivalenti('inviata_candidato')
    _firm = PropostaAssunzione.stati_equivalenti('firmata_candidato')
    _att = PropostaAssunzione.stati_equivalenti('contratto_attivo')
    STATI_VISIBILI = tuple(dict.fromkeys((*_inv, *_firm, *_att)))

    proposte_qs = PropostaAssunzione.objects.filter(
        azienda=azienda,
        stato__in=STATI_VISIBILI,
    ).select_related('dipendente', 'tipo_contratto').order_by(
        'dipendente__cognome', 'dipendente__nome'
    )

    if stato_filter == 'inviata_candidato':
        proposte_qs = proposte_qs.filter(stato__in=_inv)
    elif stato_filter == 'firmata_candidato':
        proposte_qs = proposte_qs.filter(stato__in=_firm)
    elif stato_filter == 'contratto_attivo':
        proposte_qs = proposte_qs.filter(stato__in=_att)

    cnt_inviata = PropostaAssunzione.objects.filter(azienda=azienda, stato__in=_inv).count()
    cnt_firmata = PropostaAssunzione.objects.filter(azienda=azienda, stato__in=_firm).count()
    cnt_attivo = PropostaAssunzione.objects.filter(azienda=azienda, stato__in=_att).count()

    if formato == 'csv':
        return _export_candidati_csv(proposte_qs, azienda)
    if formato == 'excel':
        return _export_candidati_excel(proposte_qs, azienda)

    paginator = Paginator(proposte_qs, 25)
    page_obj = paginator.get_page(request.GET.get('page') or 1)

    return render(request, 'consulente/candidati.html', {
        'azienda': azienda,
        'proposte': page_obj,
        'page_obj': page_obj,
        'stato_filter': stato_filter,
        'totale': cnt_inviata + cnt_firmata + cnt_attivo,
        'cnt_inviata': cnt_inviata,
        'cnt_firmata': cnt_firmata,
        'cnt_attivo': cnt_attivo,
    })


def _export_candidati_csv(proposte_qs, azienda):
    """Export CSV anagrafiche candidati con BOM UTF-8 per compatibilità Excel IT."""
    response = HttpResponse(content_type='text/csv; charset=utf-8')
    nome = azienda.nome.replace(' ', '_')
    response['Content-Disposition'] = f'attachment; filename="candidati_{nome}.csv"'
    response.write('\ufeff')  # BOM UTF-8

    writer = csv.writer(response, delimiter=';')
    writer.writerow([
        'Cognome', 'Nome', 'Codice Fiscale', 'Data Nascita',
        'Posizione', 'Livello CCNL', 'Tipo Contratto',
        'Data Inizio', 'Data Fine', 'Stipendio Lordo Mensile',
        'Ore Settimanali', 'Stato', 'Data Firma Candidato',
    ])
    for p in proposte_qs:
        dip = p.dipendente
        writer.writerow([
            dip.cognome, dip.nome,
            dip.codice_fiscale or '',
            dip.data_nascita.strftime('%d/%m/%Y') if dip.data_nascita else '',
            p.posizione, p.livello_ccnl,
            p.tipo_contratto.nome if p.tipo_contratto else '',
            p.data_inizio_rapporto.strftime('%d/%m/%Y') if p.data_inizio_rapporto else '',
            p.data_fine_rapporto.strftime('%d/%m/%Y') if p.data_fine_rapporto else '',
            str(p.stipendio_lordo_mensile),
            str(p.ore_settimanali),
            p.get_stato_display(),
            p.data_firma_candidato.strftime('%d/%m/%Y %H:%M') if p.data_firma_candidato else '',
        ])
    return response


def _export_candidati_excel(proposte_qs, azienda):
    """Export Excel (.xlsx) anagrafiche candidati con stile."""
    try:
        import openpyxl
        from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
        from openpyxl.utils import get_column_letter
    except ImportError:
        return HttpResponse("openpyxl non disponibile.", status=500)

    wb = openpyxl.Workbook()
    ws = wb.active
    if ws is None:
        return HttpResponse("Impossibile creare il file Excel.", status=500)
    ws.title = "Candidati"

    thin = Side(border_style='thin', color='CCCCCC')
    bordo = Border(left=thin, right=thin, top=thin, bottom=thin)
    al_c = Alignment(horizontal='center', vertical='center')
    al_l = Alignment(horizontal='left', vertical='center')

    intestazioni = [
        'Cognome', 'Nome', 'Cod. Fiscale', 'Data Nascita',
        'Posizione', 'Livello CCNL', 'Tipo Contratto',
        'Data Inizio', 'Data Fine', 'Stipendio Lordo (€)',
        'Ore/Sett.', 'Stato', 'Firma Candidato',
    ]
    fill_h = PatternFill('solid', fgColor='1b3a5f')
    for col, h in enumerate(intestazioni, 1):
        c = ws.cell(row=1, column=col, value=h)
        c.font = Font(bold=True, color='FFFFFF', size=10)
        c.fill = fill_h
        c.alignment = al_c
        c.border = bordo
    ws.row_dimensions[1].height = 20

    fill_alt = PatternFill('solid', fgColor='f0f4f9')
    for row_n, p in enumerate(proposte_qs, 2):
        dip = p.dipendente
        riga = [
            dip.cognome, dip.nome,
            dip.codice_fiscale or '',
            dip.data_nascita.strftime('%d/%m/%Y') if dip.data_nascita else '',
            p.posizione, p.livello_ccnl,
            p.tipo_contratto.nome if p.tipo_contratto else '',
            p.data_inizio_rapporto.strftime('%d/%m/%Y') if p.data_inizio_rapporto else '',
            p.data_fine_rapporto.strftime('%d/%m/%Y') if p.data_fine_rapporto else '',
            float(p.stipendio_lordo_mensile),
            float(p.ore_settimanali),
            _safe_display(p, 'get_stato_display', 'stato'),
            p.data_firma_candidato.strftime('%d/%m/%Y %H:%M') if p.data_firma_candidato else '',
        ]
        fill_row = fill_alt if row_n % 2 == 0 else None
        for col, val in enumerate(riga, 1):
            c = ws.cell(row=row_n, column=col, value=val)
            c.border = bordo
            c.alignment = al_l
            if fill_row:
                c.fill = fill_row

    larghezze = [15, 15, 18, 14, 20, 14, 22, 12, 12, 18, 11, 22, 20]
    for i, w in enumerate(larghezze, 1):
        ws.column_dimensions[get_column_letter(i)].width = w

    out = BytesIO()
    wb.save(out)
    out.seek(0)
    nome = azienda.nome.replace(' ', '_')
    response = HttpResponse(
        out.read(),
        content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    )
    response['Content-Disposition'] = f'attachment; filename="candidati_{nome}.xlsx"'
    return response


# ── Approva proposta ─────────────────────────────────────────────────────────

@login_required
@user_passes_test(_is_consulente)
def consulente_approva_proposta(request, proposta_id):
    """Firma definitiva del datore: firmata_candidato → contratto_attivo."""
    azienda = _get_azienda_consulente(request.user)
    proposta = get_object_or_404(PropostaAssunzione, id=proposta_id, azienda=azienda)

    if not proposta.is_firmata_da_candidato():
        messages.error(
            request,
            f'La proposta deve essere in stato "Firmata dal candidato" (o equivalente legacy). '
            f"Stato attuale: {_safe_display(proposta, 'get_stato_display', 'stato')}."
        )
        return redirect('consulente_candidati')

    if request.method == 'POST':
        try:
            contratto = proposta.firma_definitiva_admin(request.user)
            messages.success(
                request,
                f'✅ Contratto {contratto.numero_contratto} emesso. '
                f'{proposta.dipendente} è ora un dipendente attivo.'
            )
            registra_log(
                utente=request.user,
                azienda=azienda,
                operazione='approva_proposta_consulente',
                descrizione=(
                    f'Consulente {request.user.username} ha approvato la proposta '
                    f'{proposta.numero_proposta} — contratto {contratto.numero_contratto}'
                ),
                request=request,
            )
        except Exception as exc:
            messages.error(request, f'Errore durante l\'approvazione: {exc}')
        return redirect('consulente_candidati')

    return render(request, 'consulente/approva_proposta.html', {
        'proposta': proposta,
        'azienda': azienda,
    })


# ── Documenti ────────────────────────────────────────────────────────────────

@login_required
@user_passes_test(_is_consulente)
def consulente_documenti(request):
    """Lista dipendenti con accesso ai loro documenti."""
    azienda = _get_azienda_consulente(request.user)
    if not azienda:
        return HttpResponseForbidden("Nessuna azienda associata.")

    dipendenti = Dipendente.objects.filter(
        azienda=azienda, stato__in=['attivo', 'candidato']
    ).order_by('cognome', 'nome')

    # Conta documenti personali per dipendente
    TIPI_PERSONALI = [
        'documento_identita', 'permesso_soggiorno', 'codice_fiscale_doc',
        'attestato', 'abilitazione', 'titolo_studio', 'certificazione', 'curriculum',
    ]
    docs_count = {}
    for d in Documento.objects.filter(
        azienda=azienda, tipo__in=TIPI_PERSONALI,
        dipendente__in=dipendenti,
    ).values('dipendente_id'):
        docs_count[d['dipendente_id']] = docs_count.get(d['dipendente_id'], 0) + 1

    dipendenti_list = []
    for dip in dipendenti:
        dip_id = _safe_id(dip)
        dipendenti_list.append({
            'dip': dip,
            'num_documenti': docs_count.get(dip_id, 0) if dip_id is not None else 0,
        })

    return render(request, 'consulente/documenti.html', {
        'azienda': azienda,
        'dipendenti_list': dipendenti_list,
    })


@login_required
@user_passes_test(_is_consulente)
def consulente_documenti_dipendente(request, dipendente_id):
    """Documenti personali di un singolo dipendente (identità, CF, attestati)."""
    azienda = _get_azienda_consulente(request.user)
    dip = get_object_or_404(Dipendente, id=dipendente_id, azienda=azienda)

    TIPI_VISIBILI = [
        'documento_identita', 'permesso_soggiorno', 'codice_fiscale_doc',
        'attestato', 'abilitazione', 'titolo_studio', 'certificazione', 'curriculum',
    ]
    documenti = Documento.objects.filter(
        dipendente=dip,
        tipo__in=TIPI_VISIBILI,
    ).order_by('-data_caricamento')

    gruppi = {}
    for d in documenti:
        gruppi.setdefault(_safe_display(d, 'get_tipo_display', 'tipo'), []).append(d)

    from rapporto_di_lavoro.services_contratti import posizione_contrattuale_per_dipendente

    posizioni_contrattuali = posizione_contrattuale_per_dipendente(dip)

    return render(request, 'consulente/documenti_dipendente.html', {
        'dip': dip,
        'gruppi': gruppi,
        'azienda': azienda,
        'posizioni_contrattuali': posizioni_contrattuali,
        'puo_registrare_addendum': False,
    })


# ── Presenze ─────────────────────────────────────────────────────────────────

@login_required
@user_passes_test(_is_consulente)
def consulente_presenze(request):
    """Selezione mese/anno + link export Excel (esistente) e CSV (nuovo)."""
    azienda = _get_azienda_consulente(request.user)
    if not azienda:
        return HttpResponseForbidden("Nessuna azienda associata.")

    oggi = date.today()

    def _parse_int(raw_value, default_value):
        raw = str(raw_value or default_value)
        normalized = raw.replace('.', '').replace(' ', '').replace(',', '')
        try:
            return int(normalized)
        except (TypeError, ValueError):
            return default_value

    anno = _parse_int(request.GET.get('anno', oggi.year), oggi.year)
    mese = _parse_int(request.GET.get('mese', oggi.month), oggi.month)
    if not (1 <= mese <= 12):
        mese = oggi.month

    dipendenti = Dipendente.objects.filter(
        azienda=azienda, stato__in=['attivo', 'candidato']
    ).order_by('cognome', 'nome')

    anni = list(range(oggi.year - 2, oggi.year + 2))

    return render(request, 'consulente/presenze.html', {
        'azienda': azienda,
        'dipendenti': dipendenti,
        'anno': anno,
        'mese': mese,
        'mesi': MESI_CHOICES,
        'anni': anni,
        'mese_nome': MESI_ITA[mese],
    })


@login_required
@user_passes_test(_is_consulente)
def consulente_presenze_export_csv(request):
    """Export CSV presenze mensili con BOM UTF-8 per Excel italiano."""
    azienda = _get_azienda_consulente(request.user)
    if not azienda:
        registra_evento_anomalia(
            utente=request.user,
            azienda=None,
            contesto='export_presenze_csv_consulente',
            anomalia={'codice': 'AZIENDA_MANCANTE', 'messaggio': 'Export CSV bloccato: azienda non trovata'},
            request=request,
        )
        return HttpResponse("Azienda non trovata.", status=400)

    oggi = date.today()

    def _parse_int(raw_value, default_value):
        raw = str(raw_value or default_value)
        normalized = raw.replace('.', '').replace(' ', '').replace(',', '')
        try:
            return int(normalized)
        except (TypeError, ValueError):
            return default_value

    anno = _parse_int(request.GET.get('anno', oggi.year), oggi.year)
    mese = _parse_int(request.GET.get('mese', oggi.month), oggi.month)
    if not (1 <= mese <= 12):
        mese = oggi.month
    dip_id = request.GET.get('dipendente', '')

    presenze_qs = Presenza.objects.filter(
        azienda=azienda, data__year=anno, data__month=mese
    ).select_related('dipendente').order_by('dipendente__cognome', 'dipendente__nome', 'data')

    if dip_id:
        presenze_qs = presenze_qs.filter(dipendente_id=dip_id)

    if not presenze_qs.exists():
        registra_evento_anomalia(
            utente=request.user,
            azienda=azienda,
            contesto='export_presenze_csv_consulente',
            anomalia={'codice': 'EXPORT_VUOTO', 'messaggio': f'Export CSV vuoto {mese:02d}/{anno}'},
            request=request,
        )
    else:
        registra_log(
            utente=request.user,
            azienda=azienda,
            operazione='export_presenze_csv_consulente',
            descrizione=f'Export presenze CSV {mese:02d}/{anno}',
            request=request,
        )

    nome_az = azienda.nome.replace(' ', '_')
    nome_file = f"presenze_{nome_az}_{anno}_{mese:02d}.csv"
    response = HttpResponse(content_type='text/csv; charset=utf-8')
    response['Content-Disposition'] = f'attachment; filename="{nome_file}"'
    response.write('\ufeff')  # BOM UTF-8

    writer = csv.writer(response, delimiter=';')
    writer.writerow([
        'Cognome', 'Nome', 'Data', 'Giorno', 'Causale',
        'Entrata 1', 'Uscita 1', 'Entrata 2', 'Uscita 2', 'Entrata 3', 'Uscita 3',
        'Ore Straordinario', 'Tipo Straordinario',
    ])
    for p in presenze_qs:
        d = p.data
        writer.writerow([
            p.dipendente.cognome, p.dipendente.nome,
            d.strftime('%d/%m/%Y'),
            GIORNI_ITA[d.weekday()],
            _safe_display(p, 'get_causale_display', 'causale') or '',
            p.ora_entrata.strftime('%H:%M') if p.ora_entrata else '',
            p.ora_uscita.strftime('%H:%M') if p.ora_uscita else '',
            p.ora_entrata2.strftime('%H:%M') if p.ora_entrata2 else '',
            p.ora_uscita2.strftime('%H:%M') if p.ora_uscita2 else '',
            p.ora_entrata3.strftime('%H:%M') if p.ora_entrata3 else '',
            p.ora_uscita3.strftime('%H:%M') if p.ora_uscita3 else '',
            str(p.ore_straordinario or ''),
            _safe_display(p, 'get_tipo_straordinario_display', 'tipo_straordinario') if p.tipo_straordinario else '',
        ])
    return response


# ── Hub caricamento documenti paghe (modulo consulente) ───────────────────────

@login_required
@user_passes_test(_is_consulente)
def consulente_carica_documento(request):
    """Unico punto di ingresso verso upload buste paga e CUD."""
    azienda = _get_azienda_consulente(request.user)
    if not azienda:
        return HttpResponseForbidden('Nessuna azienda associata.')
    return render(
        request,
        'consulente/carica_documento.html',
        {'azienda': azienda},
    )


# ── Upload buste paga ─────────────────────────────────────────────────────────

@login_required
@user_passes_test(_is_consulente)
def consulente_upload_buste_paga(request):
    """Upload buste paga mensili: singola o massiva (un file per dipendente)."""
    azienda = _get_azienda_consulente(request.user)
    if not azienda:
        return HttpResponseForbidden("Nessuna azienda associata.")

    dipendenti = Dipendente.objects.filter(
        azienda=azienda, stato='attivo'
    ).order_by('cognome', 'nome')
    dipendenti_tutti = Dipendente.objects.filter(
        azienda=azienda
    ).order_by('cognome', 'nome')

    oggi = date.today()

    def _parse_int(raw_value, default_value):
        raw = str(raw_value or default_value)
        normalized = raw.replace('.', '').replace(' ', '').replace(',', '')
        try:
            return int(normalized)
        except (TypeError, ValueError):
            return default_value

    if request.method == 'POST':
        anno = _parse_int(request.POST.get('anno', oggi.year), oggi.year)
        mese = _parse_int(request.POST.get('mese', oggi.month), oggi.month)
        dipendente_filter = request.POST.get('dipendente', '').strip()
        if mese < 1 or mese > 12:
            mese = oggi.month
        mese_nome = MESI_ITA[mese]

        def _parse_decimal_text(value):
            if value in (None, ''):
                return None
            txt = str(value).strip().replace(' ', '').replace('.', '').replace(',', '.')
            try:
                return Decimal(txt).quantize(Decimal('0.01'))
            except Exception:
                return None

        def _extract_importi_from_pdf(uploaded_file):
            try:
                from pypdf import PdfReader
            except Exception:
                return None, None

            try:
                pos = uploaded_file.tell() if hasattr(uploaded_file, 'tell') else None
                if hasattr(uploaded_file, 'seek'):
                    uploaded_file.seek(0)
                reader = PdfReader(uploaded_file)
                if getattr(reader, 'is_encrypted', False):
                    reader.decrypt(PDF_BUSTE_PASSWORD)
                pages_text = []
                for p in reader.pages:
                    try:
                        pages_text.append(p.extract_text() or '')
                    except Exception:
                        pages_text.append('')
                text = '\n'.join(pages_text)
                if hasattr(uploaded_file, 'seek') and pos is not None:
                    uploaded_file.seek(pos)
            except Exception:
                return None, None

            up = (text or '').upper()
            amount_re = re.compile(r"([0-9]{1,3}(?:[\s\.]?[0-9]{3})*,\s*[0-9]{2}|[0-9]+,\s*[0-9]{2})")

            def _extract_by_labels(label_patterns):
                lines = [re.sub(r'\s+', ' ', ln).strip() for ln in up.splitlines()]
                for ln in lines:
                    if not ln:
                        continue
                    for pat in label_patterns:
                        m_lbl = re.search(pat, ln, re.IGNORECASE)
                        if not m_lbl:
                            continue
                        tail = ln[m_lbl.end():]
                        m_amount = amount_re.search(tail)
                        if m_amount:
                            return _parse_decimal_text(m_amount.group(1))
                for pat in label_patterns:
                    m_lbl = re.search(pat + r"([\s\S]{0,120})", up, re.IGNORECASE)
                    if not m_lbl:
                        continue
                    m_amount = amount_re.search(m_lbl.group(1))
                    if m_amount:
                        return _parse_decimal_text(m_amount.group(1))
                return None

            netto = _extract_by_labels([r"NETTO\s+BUSTA"])
            lordo = _extract_by_labels([r"TOTALE\s+LORDO", r"TOT\.?\s*LORDO", r"RETRIBUZIONE\s+LORDA"])

            return netto, lordo

        caricati = 0
        for dip in dipendenti:
            dip_id = _safe_id(dip)
            if dip_id is None:
                continue
            file_key = f'busta_paga_{dip_id}'
            file_obj = request.FILES.get(file_key)
            if file_obj:
                doc_busta = Documento.objects.create(
                    azienda=azienda,
                    dipendente=dip,
                    tipo='busta_paga',
                    descrizione=f'Busta paga {mese_nome} {anno}',
                    file=file_obj,
                    caricato_da=request.user,
                    caricato_dal_dipendente=False,
                    visibile_al_dipendente=True,
                )

                netto, lordo = _extract_importi_from_pdf(file_obj)
                MovimentoImportPaghe.objects.update_or_create(
                    azienda=azienda,
                    dipendente=dip,
                    tipo='BUSTA',
                    anno=anno,
                    mese=mese,
                    defaults={
                        'documento': doc_busta,
                        'importo': netto,
                        'importo_netto': netto,
                        'importo_lordo': lordo,
                        'cf_estratto': (dip.codice_fiscale or '')[:16],
                        'nominativo_estratto': f'{dip.cognome} {dip.nome}'.strip()[:160],
                        'periodo_label': f'{mese:02d}/{anno}',
                        'source_pdf': getattr(file_obj, 'name', '') or '',
                        'page_number': None,
                    },
                )
                caricati += 1
        if caricati > 0:
            messages.success(
                request,
                f'✅ Caricate {caricati} buste paga per {mese_nome} {anno}.'
            )
            registra_log(
                utente=request.user,
                azienda=azienda,
                operazione='upload_buste_paga_massivo',
                descrizione=f'Consulente ha caricato {caricati} buste paga ({mese_nome} {anno})',
                request=request,
            )
        else:
            messages.warning(request, 'Nessun file selezionato.')
        suffix = f'&dipendente={dipendente_filter}' if dipendente_filter else ''
        return redirect(f'{request.path}?anno={anno}&mese={mese}{suffix}')

    anno = _parse_int(request.GET.get('anno', oggi.year), oggi.year)
    mese = _parse_int(request.GET.get('mese', oggi.month), oggi.month)
    dipendente_filter = request.GET.get('dipendente', '').strip()
    dipendente_filter_int = None
    if dipendente_filter.isdigit():
        dipendente_filter_int = int(dipendente_filter)
    if mese < 1 or mese > 12:
        mese = oggi.month
    mese_nome = MESI_ITA[mese]

    def _match_period_descr(descrizione: str, month: int, year: int) -> bool:
        d = (descrizione or '').upper()
        month_name = MESI_ITA[month].upper()
        return (
            f'{month_name} {year}' in d
            or f'{month:02d}/{year}' in d
            or f'{month}/{year}' in d
        )

    # Buste già caricate per il mese selezionato
    buste_esistenti = {}
    buste_periodo = []
    for d in Documento.objects.filter(
        azienda=azienda,
        tipo='busta_paga',
        dipendente__isnull=False,
    ).select_related('dipendente'):
        if not _match_period_descr(getattr(d, 'descrizione', ''), mese, anno):
            continue
        dip_id = _safe_fk_id(d, 'dipendente_id')
        if dip_id is not None and (dipendente_filter_int is None or dip_id == dipendente_filter_int):
            buste_periodo.append(d)
        if dip_id is not None:
            buste_esistenti[dip_id] = d

    buste_periodo.sort(
        key=lambda x: (
            (x.dipendente.cognome if x.dipendente else ''),
            (x.dipendente.nome if x.dipendente else ''),
            x.data_caricamento,
        )
    )

    return render(request, 'consulente/upload_buste_paga.html', {
        'azienda': azienda,
        'dipendenti': dipendenti,
        'dipendenti_tutti': dipendenti_tutti,
        'anno': anno,
        'mese': mese,
        'mesi': MESI_CHOICES,
        'anni': list(range(oggi.year - 2, oggi.year + 1)),
        'mese_nome': mese_nome,
        'buste_esistenti': buste_esistenti,
        'buste_periodo': buste_periodo,
        'dipendente_filter': dipendente_filter,
    })


# ── Upload CUD ────────────────────────────────────────────────────────────────

@login_required
@user_passes_test(_is_consulente)
def consulente_upload_cud(request):
    """Alias legacy: usa il motore unico CUD di documenti."""
    anno = (request.GET.get('anno') or request.POST.get('anno') or '').strip()
    target = reverse('upload_cud_massivo')
    if anno:
        return redirect(f"{target}?anno={anno}")
    return redirect(target)


@login_required
@user_passes_test(_is_consulente)
def consulente_partitario_paghe(request):
    """Alias legacy: reindirizza il consulente alla stessa dashboard buste di admin/HR."""
    query = {
        'categoria': 'buste',
        'tipo': 'busta_paga',
        'anno': '',
        'dipendente': '',
    }

    anno = (request.GET.get('anno') or '').strip()
    dipendente = (request.GET.get('dipendente') or '').strip()
    if anno:
        query['anno'] = anno
    if dipendente:
        query['dipendente'] = dipendente

    return redirect(f"{reverse('lista_documenti')}?{urlencode(query)}")


@login_required
@user_passes_test(_is_consulente)
def consulente_riepilogo_f24_annuale(request):
    """Alias legacy: reindirizza il consulente alla stessa dashboard F24 di admin/HR."""
    query = {
        'tipo': 'altro',
        'anno': '',
        'dipendente': '',
    }

    anno = (request.GET.get('anno') or '').strip()
    if anno:
        query['anno'] = anno

    return redirect(f"{reverse('lista_documenti')}?{urlencode(query)}")


# ── Import PDF unico (buste + F24) ──────────────────────────────────────────

@login_required
@user_passes_test(_is_consulente)
def consulente_import_pdf_unico(request):
    """Importa uno o più PDF unici mensili: crea dipendenti mancanti e allega buste/F24."""
    azienda = _get_azienda_consulente(request.user)
    if not azienda:
        return HttpResponseForbidden("Nessuna azienda associata.")

    risultati = []

    if request.method == 'POST':
        files = request.FILES.getlist('pdf_files')
        if not files:
            messages.warning(request, 'Seleziona almeno un file PDF.')
            return redirect(request.path)

        snapshots_dir = Path(settings.BASE_DIR) / 'snapshots'
        snapshots_dir.mkdir(parents=True, exist_ok=True)

        for idx, up in enumerate(files, start=1):
            nome = getattr(up, 'name', f'file_{idx}.pdf')
            if not nome.lower().endswith('.pdf'):
                risultati.append({
                    'file': nome,
                    'ok': False,
                    'errore': 'Formato non supportato (solo PDF).',
                })
                continue

            tmp_path = None
            try:
                with tempfile.NamedTemporaryFile(delete=False, suffix='.pdf') as tmp:
                    for chunk in up.chunks():
                        tmp.write(chunk)
                    tmp_path = tmp.name

                preview_out = snapshots_dir / f'preview_ui_{timezone.now().strftime("%Y%m%d_%H%M%S")}_{idx}.json'

                buff_prev = io.StringIO()
                call_command(
                    'preview_import_paghe_pdf',
                    tmp_path,
                    azienda_id=azienda.id,
                    out=str(preview_out),
                    stdout=buff_prev,
                )

                buff_imp = io.StringIO()
                call_command(
                    'import_paghe_pdf',
                    str(preview_out),
                    azienda_id=azienda.id,
                    apply=True,
                    attach_docs=True,
                    stdout=buff_imp,
                )

                preview_data = json.loads(preview_out.read_text(encoding='utf-8'))
                s = preview_data.get('summary', {})
                risultati.append({
                    'file': nome,
                    'ok': True,
                    'buste_uniche': s.get('buste_uniche', 0),
                    'matched': s.get('matched', 0),
                    'to_create': s.get('to_create', 0),
                    'already_present': s.get('already_present', 0),
                    'f24_pages': s.get('f24_pages', 0),
                    'report': str(preview_out),
                })
            except Exception as exc:
                risultati.append({
                    'file': nome,
                    'ok': False,
                    'errore': str(exc),
                })
            finally:
                if tmp_path:
                    try:
                        os.unlink(tmp_path)
                    except Exception:
                        pass

        ok_n = sum(1 for r in risultati if r.get('ok'))
        ko_n = len(risultati) - ok_n
        if ok_n:
            messages.success(request, f'✅ Import completato: {ok_n} file elaborati con successo.')
        if ko_n:
            messages.warning(request, f'⚠️ {ko_n} file non elaborati. Controlla il dettaglio sotto.')

    return render(request, 'consulente/import_pdf_unico.html', {
        'azienda': azienda,
        'risultati': risultati,
    })
