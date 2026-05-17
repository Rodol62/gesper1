import os
import re
import io
import json
import subprocess
import tempfile
from decimal import Decimal
from pathlib import Path
from collections import defaultdict
from django.shortcuts import render, get_object_or_404, redirect
from django.http import HttpResponseForbidden, FileResponse, Http404
from django.contrib import messages
from django.conf import settings
from django.core.management import call_command
from django.contrib.auth.decorators import login_required
from django.urls import reverse
from .models import Documento
from anagrafiche.models import Dipendente
from accounts.models import MovimentoImportPaghe
from rapporto_di_lavoro.models import RapportoDiLavoro
from anagrafiche.permissions import admin_required, hr_required
from log_attivita.utils import registra_log
from accounts.tenant import get_azienda_operativa

PDF_BUSTE_PASSWORD = 'DOLCEMASCOLO'


def _is_admin_or_hr(user):
    return user.is_superuser or user.has_ruolo('admin') or user.has_ruolo('hr')


def _is_admin_hr_or_consulente(user):
    return _is_admin_or_hr(user) or user.has_ruolo('consulente')


def _is_dipendente(user):
    return user.has_ruolo('dipendente')


def _is_candidato(user):
    return user.has_ruolo('candidato')


MESI_ITA = {
    'GENNAIO': 1,
    'FEBBRAIO': 2,
    'MARZO': 3,
    'APRILE': 4,
    'MAGGIO': 5,
    'GIUGNO': 6,
    'LUGLIO': 7,
    'AGOSTO': 8,
    'SETTEMBRE': 9,
    'OTTOBRE': 10,
    'NOVEMBRE': 11,
    'DICEMBRE': 12,
}

MESI_NUM_TO_NAME = {
    1: 'Gennaio',
    2: 'Febbraio',
    3: 'Marzo',
    4: 'Aprile',
    5: 'Maggio',
    6: 'Giugno',
    7: 'Luglio',
    8: 'Agosto',
    9: 'Settembre',
    10: 'Ottobre',
    11: 'Novembre',
    12: 'Dicembre',
}


def _parse_periodo_busta(doc: Documento):
    """Estrae (mese, anno) da descrizione busta; fallback a data_caricamento."""
    desc = (getattr(doc, 'descrizione', '') or '').upper()

    m = re.search(r'\b(0?[1-9]|1[0-2])\s*/\s*(20\d{2})\b', desc)
    if m:
        return int(m.group(1)), int(m.group(2))

    year_m = re.search(r'\b(20\d{2})\b', desc)
    year = int(year_m.group(1)) if year_m else None
    month = None
    for nome, num in MESI_ITA.items():
        if nome in desc:
            month = num
            break

    if month and year:
        return month, year

    if doc.data_caricamento:
        return doc.data_caricamento.month, doc.data_caricamento.year
    return None, None


def _parse_decimal_text(value):
    if value in (None, ''):
        return None
    txt = str(value).strip().replace(' ', '').replace('.', '').replace(',', '.')
    try:
        return Decimal(txt).quantize(Decimal('0.01'))
    except Exception:
        return None


def _extract_amount_by_labels(text: str, label_patterns):
    """
    Estrae importo monetario vicino a etichette note.
    Sul cedolino i valori utili sono spesso in ultima colonna: si usa il valore
    monetario più a destra (ultimo) nella riga/area target.
    """
    amount_re = re.compile(
        r'([+-]?\s*[0-9]{1,3}(?:\s*[\.\,\u00A0\']\s*[0-9]{3})*\s*[\,\.]\s*[0-9]{2}|[+-]?\s*[0-9]+\s*[\,\.]\s*[0-9]{2})'
    )

    lines = [re.sub(r'\s+', ' ', ln).strip() for ln in (text or '').splitlines()]

    def _first_amount(raw: str):
        matches = amount_re.findall(raw or '')
        if not matches:
            return None
        # Nei cedolini target il valore corretto è quello immediatamente dopo
        # l'etichetta (prima ricorrenza), non l'ultima colonna numerica della riga.
        raw_val = re.sub(r'\s+', '', matches[0])
        return _parse_decimal_text(raw_val)

    # 1) etichetta e importo nella stessa riga
    for i, ln in enumerate(lines):
        if not ln:
            continue
        for pat in label_patterns:
            m_lbl = re.search(pat, ln, re.IGNORECASE)
            if not m_lbl:
                continue

            tail = ln[m_lbl.end():]
            val = _first_amount(tail)
            if val is not None:
                return val

            # 2) fallback su poche righe successive (layout PDF spezzato)
            for j in range(i + 1, min(i + 4, len(lines))):
                val_next = _first_amount(lines[j])
                if val_next is not None:
                    return val_next

    # 3) fallback testuale ampio vicino all'etichetta
    for pat in label_patterns:
        m = re.search(pat + r'([\s\S]{0,260})', text or '', re.IGNORECASE)
        if not m:
            continue
        val = _first_amount(m.group(1))
        if val is not None:
            return val
    return None


def _extract_busta_importi_posizionale_pdfplumber(doc: Documento):
    """Estrazione posizionale (TeamSystem): valore sotto etichetta colonna."""
    try:
        import pdfplumber
    except Exception:
        return None, None

    amount_word_re = re.compile(r'^-?\s*[0-9]{1,3}(?:\s*[\.\,\u00A0\']\s*[0-9]{3})*\s*[\,\.]\s*[0-9]{2}$')

    def _normalize_amount_token(raw: str):
        if raw in (None, ''):
            return None
        txt = str(raw).replace('\u00A0', ' ')
        txt = re.sub(r'\s+', '', txt)
        # Formato IT con separatore migliaia '.' e decimale ','
        if ',' in txt and '.' in txt:
            txt = txt.replace('.', '').replace(',', '.')
        elif ',' in txt:
            txt = txt.replace(',', '.')
        try:
            return Decimal(txt).quantize(Decimal('0.01'))
        except Exception:
            return None

    def _find_value_below_label(words, label_text, y_gap_max=70, x_tolerance=8, y_min_gap=0, strict_y_max=22):
        label_parts = label_text.upper().split()
        for i, w in enumerate(words):
            if w.get('text', '').upper() != label_parts[0]:
                continue

            ok = True
            for j, part in enumerate(label_parts[1:], 1):
                if i + j >= len(words) or words[i + j].get('text', '').upper() != part:
                    ok = False
                    break
            if not ok:
                continue

            first_w = words[i]
            last_w = words[i + len(label_parts) - 1]
            label_x0 = first_w.get('x0', 0)
            label_x1 = last_w.get('x1', 0)
            label_bottom = max(first_w.get('bottom', 0), last_w.get('bottom', 0))
            label_cx = (label_x0 + label_x1) / 2

            strict_candidates = []
            loose_candidates = []
            for cw in words:
                token = cw.get('text', '')
                if not amount_word_re.match(token):
                    continue
                gap = cw.get('top', 0) - label_bottom
                if gap < y_min_gap or gap > y_gap_max:
                    continue
                in_col = (cw.get('x0', 0) < label_x1 + x_tolerance and cw.get('x1', 0) > label_x0 - x_tolerance)
                if in_col:
                    if gap <= strict_y_max:
                        strict_candidates.append(cw)
                    else:
                        loose_candidates.append(cw)

            # 1) Valore valido solo nella fascia stretta subito sotto etichetta
            if strict_candidates:
                best = min(
                    strict_candidates,
                    key=lambda c: (c.get('top', 0), abs(((c.get('x0', 0) + c.get('x1', 0)) / 2) - label_cx)),
                )
                return _normalize_amount_token(best.get('text'))

            # 2) Se troviamo valori solo più in basso, sono tipicamente della riga successiva
            # (es. voci statistiche/festività) e NON il totale etichettato.
            if loose_candidates:
                return Decimal('0.00')

            # 3) Nessun valore numerico riconosciuto sotto etichetta: nei cedolini con totale zero
            # il campo può risultare vuoto/illeggibile. Restituiamo 0.00 per non leggere altre righe.
            return Decimal('0.00')

        return None

    try:
        with doc.file.open('rb') as fh:
            with pdfplumber.open(fh, password=PDF_BUSTE_PASSWORD) as pdf:
                if not pdf.pages:
                    return None, None
                page = pdf.pages[0]
                words = page.extract_words(keep_blank_chars=False) or []
                if not words:
                    return None, None

                lordo = _find_value_below_label(words, 'TOTALE LORDO', y_min_gap=0)
                netto = _find_value_below_label(words, 'NETTO BUSTA', y_min_gap=0)
                return netto, lordo
    except Exception:
        return None, None


def _extract_busta_importi_da_pdf(doc: Documento):
    """Estrae netto/lordo da PDF busta (best-effort)."""
    if not getattr(doc, 'file', None):
        return None, None

    # 1) Fallback robusto per layout TeamSystem via coordinate PDF (se disponibile)
    netto_pos, lordo_pos = _extract_busta_importi_posizionale_pdfplumber(doc)
    if netto_pos is not None or lordo_pos is not None:
        return netto_pos, lordo_pos

    try:
        from pypdf import PdfReader
    except Exception:
        return None, None

    try:
        with doc.file.open('rb') as fh:
            reader = PdfReader(fh)
            if getattr(reader, 'is_encrypted', False):
                try:
                    reader.decrypt(PDF_BUSTE_PASSWORD)
                except Exception:
                    return None, None
            pages_text = []
            for p in reader.pages:
                try:
                    pages_text.append(p.extract_text() or '')
                except Exception:
                    pages_text.append('')
            text = '\n'.join(pages_text)
    except Exception:
        return None, None

    if not text:
        return None, None

    netto = _extract_amount_by_labels(text, [
        r'NETTO\s+BUSTA',
    ])
    lordo = _extract_amount_by_labels(text, [
        r'TOTALE\s+LORDO',
        r'TOT\.?\s*LORDO',
        r'RETRIBUZIONE\s+LORDA',
    ])

    return netto, lordo


def _extract_f24_importo_da_pdf(doc: Documento):
    """Estrae importo F24 (SALDO FINALE) da PDF, best-effort."""
    if not getattr(doc, 'file', None):
        return None

    def _parse_token_amount(raw: str):
        if not raw:
            return None
        txt = str(raw).strip().replace(' ', '').replace("'", '').replace('\u00A0', '')

        # Formato standard IT: 1.234,56
        if ',' in txt:
            txt = txt.replace('.', '').replace(',', '.')
            try:
                return Decimal(txt).quantize(Decimal('0.01'))
            except Exception:
                return None

        # Formato compatto OCR: 2.81155 -> 2811.55
        m_compact = re.fullmatch(r'([0-9]{1,3}(?:\.[0-9]{3})+)([0-9]{2})', txt)
        if m_compact:
            int_part = m_compact.group(1).replace('.', '')
            dec_part = m_compact.group(2)
            try:
                return Decimal(f"{int_part}.{dec_part}").quantize(Decimal('0.01'))
            except Exception:
                return None

        # Solo cifre con ultime 2 decimali implicite (es. 281155 -> 2811.55)
        if re.fullmatch(r'\d{3,}', txt):
            try:
                return (Decimal(txt) / Decimal('100')).quantize(Decimal('0.01'))
            except Exception:
                return None
        return None

    text = ''

    # 1) pdftotext (più robusto sui PDF F24 scannerizzati/malformati)
    try:
        cmd = ['pdftotext', '-layout', str(doc.file.path), '-']
        proc = subprocess.run(cmd, check=True, capture_output=True, text=True)
        text = proc.stdout or ''
    except Exception:
        text = ''

    # 2) fallback pypdf
    if not text:
        try:
            from pypdf import PdfReader
            with doc.file.open('rb') as fh:
                r = PdfReader(fh)
                pages = []
                for p in r.pages:
                    try:
                        pages.append(p.extract_text() or '')
                    except Exception:
                        pages.append('')
                text = '\n'.join(pages)
        except Exception:
            return None

    if not text:
        return None

    # Cerca importo vicino a SALDO FINALE
    candidates = []
    for m in re.finditer(r'SALDO\s+FINALE([\s\S]{0,180})', text, re.IGNORECASE):
        window = m.group(1)
        raw_vals = re.findall(r'([0-9]{1,3}(?:[\.\s][0-9]{3})*,\s*[0-9]{2}|[0-9]{1,3}(?:\.[0-9]{3})+[0-9]{2}|[0-9]{3,})', window)
        for rv in raw_vals:
            v = _parse_token_amount(rv)
            if v is not None:
                candidates.append(v)

    if candidates:
        return max(candidates)

    # fallback globale: importo dopo "EURO"
    m_euro = re.search(r'EURO\s*[+\-]?\s*,?\s*([0-9]{1,3}(?:[\.\s][0-9]{3})*,\s*[0-9]{2}|[0-9]{1,3}(?:\.[0-9]{3})+[0-9]{2}|[0-9]{3,})', text, re.IGNORECASE)
    if m_euro:
        return _parse_token_amount(m_euro.group(1))

    return None


def _extract_f24_dettagli_da_pdf(doc: Documento):
    """Estrae dettaglio righe F24 (sezione/codice/anno/debito/credito) + totali."""
    if not getattr(doc, 'file', None):
        return {
            'rows': [],
            'tot_debito': None,
            'tot_credito': None,
            'saldo_finale': None,
        }

    def _parse_token_amount(raw: str):
        if not raw:
            return None
        txt = str(raw).strip().replace(' ', '').replace("'", '').replace('\u00A0', '')
        if ',' in txt:
            txt = txt.replace('.', '').replace(',', '.')
            try:
                return Decimal(txt).quantize(Decimal('0.01'))
            except Exception:
                return None
        m_compact = re.fullmatch(r'([0-9]{1,3}(?:\.[0-9]{3})+)([0-9]{2})', txt)
        if m_compact:
            try:
                return Decimal(f"{m_compact.group(1).replace('.', '')}.{m_compact.group(2)}").quantize(Decimal('0.01'))
            except Exception:
                return None
        if re.fullmatch(r'\d{3,}', txt):
            try:
                return (Decimal(txt) / Decimal('100')).quantize(Decimal('0.01'))
            except Exception:
                return None
        return None

    text = ''
    try:
        proc = subprocess.run(['pdftotext', '-layout', str(doc.file.path), '-'], check=True, capture_output=True, text=True)
        text = proc.stdout or ''
    except Exception:
        text = ''

    if not text:
        return {
            'rows': [],
            'tot_debito': None,
            'tot_credito': None,
            'saldo_finale': _extract_f24_importo_da_pdf(doc),
        }

    lines = text.splitlines()
    section = 'ALTRO'
    rows = []
    section_subtotals = []

    def _detect_section(line_up: str):
        if 'SEZIONE ERARIO' in line_up:
            return 'ERARIO'
        if 'SEZIONE INPS' in line_up:
            return 'INPS'
        if 'SEZIONE REGIONI' in line_up:
            return 'REGIONI'
        if 'SEZIONE IMU' in line_up:
            return 'IMU'
        if 'SEZIONE ALTRI ENTI PREVIDENZIALI' in line_up:
            return 'ALTRI_ENTI'
        return None

    for i, ln in enumerate(lines):
        up_ln = ln.upper()
        sec = _detect_section(up_ln)
        if sec:
            section = sec

        # Subtotali per sezione (colonne: debito / credito), escludendo il totale finale M/N
        if 'TOTALE' in up_ln and section != 'ALTRO' and 'SALDO' not in up_ln and not re.search(r'\bTOTALE\s+M\b', up_ln):
            w = '\n'.join(lines[i:i + 5])
            raw_vals_signed = re.findall(
                r'([0-9]{1,3}(?:[\.\s][0-9]{3})*,\s*[0-9]{2}[\-\+]?|[0-9]{1,3}(?:\.[0-9]{3})+[0-9]{2}[\-\+]?|[0-9]{3,}[\-\+]?)',
                w,
            )

            signed_vals = []
            for rv in raw_vals_signed:
                rv_clean = re.sub(r'\s+', '', rv)
                sign = '-' if rv_clean.endswith('-') else ('+' if rv_clean.endswith('+') else '')
                rv_num = rv_clean[:-1] if sign else rv_clean
                parsed = _parse_token_amount(rv_num)
                if parsed is not None:
                    signed_vals.append((parsed, sign))

            if signed_vals:
                debito = signed_vals[0][0]
                credito = Decimal('0.00')

                # Se presente valore con '-', quello è il credito compensato
                neg_candidates = [v for v, s in signed_vals[1:] if s == '-']
                if neg_candidates:
                    credito = neg_candidates[0]
                elif len(signed_vals) >= 2:
                    second = signed_vals[1][0]
                    # In molti layout il 2° valore è il saldo se credito=0 (es. INPS), quindi evitalo
                    if second != debito:
                        credito = second

                section_subtotals.append({
                    'sezione': section,
                    'debito': debito,
                    'credito': credito,
                })

        m = re.match(r'^\s*(\d{4})\s+(\d{1,2})\s+(20\d{2})\b', ln)
        if not m:
            continue

        codice = m.group(1)
        periodo = m.group(2).zfill(2)
        anno_rif = int(m.group(3))

        window = '\n'.join(lines[i:i + 6])
        raw_vals = re.findall(r'([0-9]{1,3}(?:[\.\s][0-9]{3})*,\s*[0-9]{2}|[0-9]{1,3}(?:\.[0-9]{3})+[0-9]{2}|[0-9]{3,})', window)
        vals = []
        for rv in raw_vals:
            rv_clean = re.sub(r'\s+', '', rv)
            if rv_clean in {codice, m.group(2), m.group(3)}:
                continue
            parsed = _parse_token_amount(rv)
            if parsed is not None:
                vals.append(parsed)

        debito = vals[0] if vals else None
        credito = vals[1] if len(vals) > 1 else Decimal('0.00')

        rows.append({
            'sezione': section,
            'codice_tributo': codice,
            'anno_riferimento': anno_rif,
            'periodo_riferimento': periodo,
            'importo_debito': debito,
            'importo_credito': credito,
            'ordine': len(rows) + 1,
        })

    # Deduplica righe duplicate dovute alle copie multiple presenti nello stesso PDF F24
    dedup_rows = []
    seen_rows = set()
    for r in rows:
        k = (
            r.get('sezione') or '',
            r.get('codice_tributo') or '',
            r.get('anno_riferimento') or 0,
            r.get('periodo_riferimento') or '',
            str(r.get('importo_debito') or Decimal('0.00')),
            str(r.get('importo_credito') or Decimal('0.00')),
        )
        if k in seen_rows:
            continue
        seen_rows.add(k)
        dedup_rows.append(r)
    rows = dedup_rows

    # Deduplica subtotali per sezione (evita triplicazioni copia contribuente/banca/ente)
    dedup_subtotals = []
    seen_subtotals = set()
    for s in section_subtotals:
        k = (s.get('sezione') or '', str(s.get('debito') or Decimal('0.00')), str(s.get('credito') or Decimal('0.00')))
        if k in seen_subtotals:
            continue
        seen_subtotals.add(k)
        dedup_subtotals.append(s)

    if dedup_subtotals:
        tot_debito = sum((s.get('debito') or Decimal('0.00')) for s in dedup_subtotals)
        tot_credito = sum((s.get('credito') or Decimal('0.00')) for s in dedup_subtotals)
    else:
        tot_debito = sum((r['importo_debito'] or Decimal('0.00')) for r in rows) if rows else None
        tot_credito = sum((r['importo_credito'] or Decimal('0.00')) for r in rows) if rows else None

    # Importo da versare F24: max(debito - credito, 0)
    saldo = None
    if tot_debito is not None and tot_credito is not None:
        diff = (tot_debito - tot_credito).quantize(Decimal('0.01'))
        saldo = max(diff, Decimal('0.00'))

    return {
        'rows': rows,
        'tot_debito': tot_debito,
        'tot_credito': tot_credito,
        'saldo_finale': saldo,
    }


def _extract_f24_totali_da_pdf(doc: Documento):
    """Estrae (tot_debito, tot_credito, saldo_finale) da PDF F24, best-effort."""
    data = _extract_f24_dettagli_da_pdf(doc)
    return data.get('tot_debito'), data.get('tot_credito'), data.get('saldo_finale')


def _assert_documento_accesso(request, documento: Documento):
    """Valida accesso al documento per ruolo utente."""
    ruolo = None
    if _is_dipendente(request.user):
        ruolo = 'dipendente'
    elif _is_candidato(request.user):
        ruolo = 'candidato'

    # Consulente: può accedere ai documenti della propria azienda
    if request.user.has_ruolo('consulente'):
        azienda_consulente = getattr(request.user, 'azienda', None)
        if not azienda_consulente or documento.azienda_id != azienda_consulente.id:
            return HttpResponseForbidden("Accesso negato.")
    # Admin/HR della stessa azienda
    elif _is_admin_or_hr(request.user):
        if documento.caricato_dal_dipendente and not documento.visualizzato_da_azienda:
            documento.visualizzato_da_azienda = True
            documento.save(update_fields=['visualizzato_da_azienda'])
    # Dipendente: solo i propri, visibili
    elif ruolo == 'dipendente':
        if not (documento.dipendente and documento.dipendente.utente == request.user and documento.visibile_al_dipendente):
            return HttpResponseForbidden("Accesso negato.")
    # Candidato: solo i propri, visibili
    elif ruolo == 'candidato':
        profilo = getattr(request.user, 'profilo_candidato', None)
        dip = profilo.dipendente if profilo else None
        if not (dip and documento.dipendente == dip and documento.visibile_al_dipendente):
            return HttpResponseForbidden("Accesso negato.")
    else:
        return HttpResponseForbidden("Accesso negato.")

    return None


@login_required
def lista_documenti(request):
    """Lista documenti per admin/HR."""
    if request.user.is_superuser or request.user.has_ruolo('admin'):
        azienda_operativa = get_azienda_operativa(request.user, request.session)
        documenti = Documento.objects.filter(azienda=azienda_operativa).select_related(
            'dipendente', 'caricato_da'
        ) if azienda_operativa else Documento.objects.none()
    elif request.user.has_ruolo('hr'):
        documenti = Documento.objects.filter(azienda=request.user.azienda).select_related(
            'dipendente', 'caricato_da'
        )
    elif request.user.has_ruolo('consulente'):
        documenti = Documento.objects.filter(azienda=request.user.azienda).select_related(
            'dipendente', 'caricato_da'
        )
    elif _is_dipendente(request.user):
        try:
            dip = Dipendente.objects.get(utente=request.user)
            documenti = Documento.objects.filter(dipendente=dip, visibile_al_dipendente=True)
        except Dipendente.DoesNotExist:
            documenti = Documento.objects.none()
    else:
        return HttpResponseForbidden("Accesso negato")

    categoria = request.GET.get('categoria', '').strip().lower()
    tipo_filter = request.GET.get('tipo', '')
    anno_filter = request.GET.get('anno', '').strip()
    dipendente_filter = request.GET.get('dipendente', '').strip()
    anno_filter_int = None
    dipendente_filter_int = None
    anno_norm = anno_filter.replace('.', '').replace(' ', '').replace(',', '')
    if anno_norm.isdigit():
        anno_filter_int = int(anno_norm)
        anno_filter = str(anno_filter_int)
    if dipendente_filter.isdigit():
        dipendente_filter_int = int(dipendente_filter)

    # Anni disponibili letti dal database (pre-filtro anno)
    # NB: usiamo DISTINCT su data_caricamento__year per avere la lista canonica
    # degli anni realmente presenti nei documenti dell'azienda corrente.
    anni_disponibili = [
        y for y in documenti
        .order_by('data_caricamento__year')
        .values_list('data_caricamento__year', flat=True)
        .distinct()
        if y
    ]
    anni_disponibili.sort(reverse=True)

    # Normalizzazione filtri categoria/tipo:
    # - se l'utente seleziona esplicitamente il tipo, quel valore ha priorità
    # - la categoria viene riallineata al tipo per evitare "sticky" della vista precedente
    # - se il tipo non è valorizzato, usiamo la categoria come scorciatoia
    if tipo_filter:
        if tipo_filter == 'busta_paga':
            categoria = 'buste'
        elif tipo_filter == 'certificato':
            categoria = 'cud'
        elif tipo_filter == 'altro':
            categoria = 'f24'
        else:
            categoria = ''
    else:
        if categoria == 'buste':
            tipo_filter = 'busta_paga'
        elif categoria == 'cud':
            tipo_filter = 'certificato'
        elif categoria == 'f24':
            tipo_filter = 'altro'

    is_f24_context = (_is_admin_hr_or_consulente(request.user) and (categoria == 'f24' or tipo_filter == 'altro'))

    if is_f24_context:
        azienda_rif = None
        if request.user.is_superuser or request.user.has_ruolo('admin'):
            azienda_rif = get_azienda_operativa(request.user, request.session)
        elif request.user.has_ruolo('hr'):
            azienda_rif = request.user.azienda
        elif request.user.has_ruolo('consulente'):
            azienda_rif = request.user.azienda

        if azienda_rif:
            anni_f24_db = sorted(
                MovimentoImportPaghe.objects.filter(azienda=azienda_rif, tipo='F24')
                .order_by('anno').values_list('anno', flat=True).distinct(),
                reverse=True,
            )
            if anni_f24_db:
                anni_disponibili = anni_f24_db

    if tipo_filter:
        documenti = documenti.filter(tipo=tipo_filter)
    if categoria == 'f24':
        documenti = documenti.filter(descrizione__icontains='F24')
    # Per le buste il filtro anno deve essere sul periodo busta (descrizione/import),
    # non sull'anno di caricamento file.
    if anno_filter_int and not (_is_admin_hr_or_consulente(request.user) and tipo_filter in ('busta_paga', 'altro')):
        documenti = documenti.filter(data_caricamento__year=anno_filter_int)

    # Per F24 (tipo=altro) il filtro anno deve usare l'anno di riferimento del movimento,
    # non l'anno di caricamento del file.
    if anno_filter_int and is_f24_context and azienda_rif:
        doc_ids_f24_anno = list(
            MovimentoImportPaghe.objects.filter(
                azienda=azienda_rif,
                tipo='F24',
                anno=anno_filter_int,
            ).exclude(documento__isnull=True).values_list('documento_id', flat=True)
        )
        documenti = documenti.filter(id__in=doc_ids_f24_anno)

    dipendenti_filtri = []
    if _is_admin_hr_or_consulente(request.user):
        doc_dip_ids = list(documenti.exclude(dipendente__isnull=True).values_list('dipendente_id', flat=True).distinct())
        if doc_dip_ids:
            dipendenti_filtri = list(Dipendente.objects.filter(id__in=doc_dip_ids).order_by('cognome', 'nome'))

    if dipendente_filter_int:
        documenti = documenti.filter(dipendente_id=dipendente_filter_int)

    # Default: lista vuota se non è stato applicato alcun filtro esplicito
    if _is_admin_hr_or_consulente(request.user):
        has_explicit_filter = bool(categoria or tipo_filter or anno_filter_int or dipendente_filter_int)
        if not has_explicit_filter:
            documenti = documenti.none()

    show_buste_dashboard = False
    show_f24_dashboard = False
    buste_anni = []
    buste_tot_lordo = Decimal('0.00')
    buste_tot_netto = Decimal('0.00')
    buste_tot_f24 = Decimal('0.00')
    buste_tot_costo_azienda = Decimal('0.00')
    buste_has_lordo = False
    buste_has_netto = False
    buste_has_f24 = False
    buste_num_dipendenti = 0
    buste_num_documenti = 0
    buste_anni_disponibili = anni_disponibili
    buste_show_f24_details = True
    f24_anni = []
    f24_tot_importo = Decimal('0.00')
    f24_tot_debito = Decimal('0.00')
    f24_tot_credito = Decimal('0.00')
    f24_has_importo = False
    f24_has_debito = False
    f24_has_credito = False
    f24_num_documenti = 0
    f24_anni_disponibili = anni_disponibili

    if _is_admin_hr_or_consulente(request.user) and tipo_filter == 'busta_paga':
        show_buste_dashboard = True
        # Uniforma il contenitore/filtri alla resa usata da `tipo=altro`.
        show_f24_dashboard = True
        buste_show_f24_details = not bool(dipendente_filter_int)

        buste_qs = documenti.filter(tipo='busta_paga', dipendente__isnull=False).select_related('dipendente', 'caricato_da')

        buste_docs = list(buste_qs.order_by('-data_caricamento'))

        azienda_rif = None
        if request.user.is_superuser or request.user.has_ruolo('admin'):
            azienda_rif = get_azienda_operativa(request.user, request.session)
        elif request.user.has_ruolo('hr'):
            azienda_rif = request.user.azienda
        elif request.user.has_ruolo('consulente'):
            azienda_rif = request.user.azienda

        movimenti_qs = MovimentoImportPaghe.objects.none()
        movimenti_f24_qs = MovimentoImportPaghe.objects.none()
        if azienda_rif:
            movimenti_qs = MovimentoImportPaghe.objects.filter(
                azienda=azienda_rif,
                tipo='BUSTA',
            ).select_related('dipendente', 'documento')
            if anno_filter_int:
                movimenti_qs = movimenti_qs.filter(anno=anno_filter_int)
            if dipendente_filter_int:
                movimenti_qs = movimenti_qs.filter(dipendente_id=dipendente_filter_int)

            movimenti_f24_qs = MovimentoImportPaghe.objects.filter(
                azienda=azienda_rif,
                tipo='F24',
            ).select_related('documento')
            if anno_filter_int:
                movimenti_f24_qs = movimenti_f24_qs.filter(anno=anno_filter_int)

        mov_by_doc = {m.documento_id: m for m in movimenti_qs if m.documento_id}
        mov_by_key = {(m.dipendente_id, m.mese, m.anno): m for m in movimenti_qs if m.dipendente_id and m.mese and m.anno}

        f24_by_month = defaultdict(lambda: Decimal('0.00'))
        for fm in movimenti_f24_qs:
            if not (fm.anno and fm.mese):
                continue
            da_versare = fm.f24_saldo_finale
            if da_versare is None:
                if fm.importo_netto is not None:
                    da_versare = fm.importo_netto
                elif fm.importo is not None:
                    da_versare = fm.importo
                elif fm.f24_tot_debito is not None and fm.f24_tot_credito is not None:
                    da_versare = max((fm.f24_tot_debito - fm.f24_tot_credito), Decimal('0.00'))
                else:
                    da_versare = Decimal('0.00')
            da_versare = max(da_versare, Decimal('0.00'))
            f24_by_month[(fm.anno, fm.mese)] += da_versare

        # Per la dashboard admin/HR delle buste usare solo il periodo canonico
        # del movimento importato. Evita anni fantasma dovuti alla data di
        # caricamento del file o a documenti orfani/di test senza periodo reale.
        # anni_disp: tutti gli anni presenti nel DB senza filtri (per dropdown anno)
        if azienda_rif:
            anni_disp = sorted(
                MovimentoImportPaghe.objects.filter(azienda=azienda_rif, tipo='BUSTA')
                .order_by('anno').values_list('anno', flat=True).distinct(),
                reverse=True
            )
        else:
            anni_disp = []
        # dip_ids_disp: dipendenti con buste per l'anno selezionato, ignorando il filtro dipendente
        # (così il dropdown mostra sempre tutti i dipendenti disponibili)
        _mov_dip_qs = (
            MovimentoImportPaghe.objects.filter(azienda=azienda_rif, tipo='BUSTA')
            .exclude(dipendente__isnull=True)
            if azienda_rif else MovimentoImportPaghe.objects.none()
        )
        if anno_filter_int:
            _mov_dip_qs = _mov_dip_qs.filter(anno=anno_filter_int)
        dip_ids_disp = sorted(_mov_dip_qs.order_by('dipendente_id').values_list('dipendente_id', flat=True).distinct())
        if dip_ids_disp:
            dipendenti_filtri = list(Dipendente.objects.filter(id__in=dip_ids_disp).order_by('cognome', 'nome'))

        # Lordo mensile teorico da rapporto sottoscritto (fallback)
        lordo_by_dip = {}
        if azienda_rif:
            rapporti = RapportoDiLavoro.objects.filter(
                azienda=azienda_rif,
                stato='sottoscritto',
            ).order_by('dipendente_id', '-data_inizio_rapporto', '-id')
            for r in rapporti:
                if r.dipendente_id not in lordo_by_dip:
                    lordo_by_dip[r.dipendente_id] = r.stipendio_lordo_mensile

        grouped = defaultdict(lambda: {
            'anno': None,
            'tot_lordo': Decimal('0.00'),
            'tot_netto': Decimal('0.00'),
            'has_lordo': False,
            'has_netto': False,
            'month_map': defaultdict(lambda: {
                'mese': None,
                'mese_nome': '',
                'tot_lordo': Decimal('0.00'),
                'tot_netto': Decimal('0.00'),
                'tot_f24': Decimal('0.00'),
                'has_lordo': False,
                'has_netto': False,
                'has_f24': False,
                'dip_map': defaultdict(lambda: {
                    'dipendente': None,
                    'tot_lordo': Decimal('0.00'),
                    'tot_netto': Decimal('0.00'),
                    'has_lordo': False,
                    'has_netto': False,
                    'rows': [],
                }),
            }),
        })

        parsed_cache = {}

        for doc in buste_docs:
            mov = mov_by_doc.get(doc.id)

            # In dashboard admin/HR mostrare solo buste con movimento canonico.
            # I documenti orfani (es. test manuali o upload senza periodo certo)
            # non devono generare anni errati come 2026/2009 nella UI.
            if not (mov and mov.anno):
                continue

            mese, anno = mov.mese, mov.anno

            if anno_filter_int and anno != anno_filter_int:
                continue

            if not mov and doc.dipendente_id and mese and anno:
                mov = mov_by_key.get((doc.dipendente_id, mese, anno))

            netto_pdf = None
            lordo_pdf = None
            netto_da_mov = None
            lordo_da_mov = None
            if mov:
                netto_da_mov = mov.importo_netto if mov.importo_netto is not None else mov.importo
                lordo_da_mov = mov.importo_lordo

            # Fallback PDF quando il movimento manca O quando ha importi mancanti.
            # Evita parsing non necessario ma consente di riallineare i record
            # esistenti che sono stati importati senza lordo/netto.
            needs_pdf_fallback = mov is None or (netto_da_mov is None or lordo_da_mov is None)
            if needs_pdf_fallback:
                if doc.id in parsed_cache:
                    netto_pdf, lordo_pdf = parsed_cache[doc.id]
                else:
                    netto_pdf, lordo_pdf = _extract_busta_importi_da_pdf(doc)
                    parsed_cache[doc.id] = (netto_pdf, lordo_pdf)

            if doc.dipendente_id and mese and anno and (netto_pdf is not None or lordo_pdf is not None or mov is None):
                # Riallinea i movimenti senza sovrascrivere valori già presenti.
                # Aggiorna solo campi mancanti, per evitare azzeramenti indesiderati.
                try:
                    if mov is None:
                        mov, _ = MovimentoImportPaghe.objects.update_or_create(
                            azienda=doc.azienda,
                            dipendente=doc.dipendente,
                            tipo='BUSTA',
                            anno=anno,
                            mese=mese,
                            defaults={
                                'documento': doc,
                                'importo': netto_pdf,
                                'importo_netto': netto_pdf,
                                'importo_lordo': lordo_pdf,
                                'cf_estratto': (getattr(doc.dipendente, 'codice_fiscale', '') or '')[:16],
                                'nominativo_estratto': f"{getattr(doc.dipendente, 'cognome', '')} {getattr(doc.dipendente, 'nome', '')}".strip()[:160],
                                'periodo_label': f'{mese:02d}/{anno}',
                                'source_pdf': getattr(getattr(doc, 'file', None), 'name', '') or '',
                                'page_number': None,
                            },
                        )
                    else:
                        changed_fields = []
                        if mov.documento_id != doc.id:
                            mov.documento = doc
                            changed_fields.append('documento')
                        if mov.importo_netto is None and netto_pdf is not None:
                            mov.importo_netto = netto_pdf
                            changed_fields.append('importo_netto')
                            if mov.importo is None:
                                mov.importo = netto_pdf
                                changed_fields.append('importo')
                        if mov.importo_lordo is None and lordo_pdf is not None:
                            mov.importo_lordo = lordo_pdf
                            changed_fields.append('importo_lordo')
                        if changed_fields:
                            mov.save(update_fields=changed_fields)

                    mov_by_doc[doc.id] = mov
                    mov_by_key[(doc.dipendente_id, mese, anno)] = mov
                except Exception:
                    pass

            netto = netto_da_mov
            lordo = lordo_da_mov

            if netto is None:
                netto = netto_pdf
            if lordo is None:
                lordo = lordo_pdf if lordo_pdf is not None else lordo_by_dip.get(doc.dipendente_id)

            y = grouped[anno]
            y['anno'] = anno
            mese_key = mese if mese else 0
            m = y['month_map'][mese_key]
            m['mese'] = mese_key
            m['mese_nome'] = MESI_NUM_TO_NAME.get(mese_key, 'Mese non indicato')

            d = m['dip_map'][doc.dipendente_id]
            d['dipendente'] = doc.dipendente

            if lordo is not None:
                y['tot_lordo'] += lordo
                y['has_lordo'] = True
                m['tot_lordo'] += lordo
                m['has_lordo'] = True
                d['tot_lordo'] += lordo
                d['has_lordo'] = True
            if netto is not None:
                y['tot_netto'] += netto
                y['has_netto'] = True
                m['tot_netto'] += netto
                m['has_netto'] = True
                d['tot_netto'] += netto
                d['has_netto'] = True

            d['rows'].append({
                'documento': doc,
                'mese': mese,
                'anno': anno,
                'movimento': mov,
                'lordo': lordo,
                'netto': netto,
            })

        for yy in sorted(grouped.keys(), reverse=True):
            item = grouped[yy]
            mesi = []
            y_tot_f24 = Decimal('0.00')
            y_has_f24 = False
            for mk in sorted(item['month_map'].keys()):
                mitem = item['month_map'][mk]
                f24_mese = f24_by_month.get((yy, mk), Decimal('0.00'))
                mitem['tot_f24'] = f24_mese
                mitem['has_f24'] = f24_mese > Decimal('0.00')
                dipendenti = []
                m_tot_lordo = Decimal('0.00')
                m_tot_netto = Decimal('0.00')
                m_has_lordo = False
                m_has_netto = False
                for _, ditem in mitem['dip_map'].items():
                    ditem['rows'].sort(
                        key=lambda r: (
                            r['anno'] or 0,
                            r['mese'] or 0,
                            r['documento'].data_caricamento,
                        ),
                        reverse=True,
                    )
                    if ditem.get('has_lordo'):
                        m_tot_lordo += (ditem.get('tot_lordo') or Decimal('0.00'))
                        m_has_lordo = True
                    if ditem.get('has_netto'):
                        m_tot_netto += (ditem.get('tot_netto') or Decimal('0.00'))
                        m_has_netto = True
                    dipendenti.append(ditem)
                dipendenti.sort(key=lambda x: ((x['dipendente'].cognome if x['dipendente'] else ''), (x['dipendente'].nome if x['dipendente'] else '')))
                # Totali mese coerenti con la somma dei dipendenti
                mitem['tot_lordo'] = m_tot_lordo
                mitem['tot_netto'] = m_tot_netto
                mitem['has_lordo'] = m_has_lordo
                mitem['has_netto'] = m_has_netto
                mitem['dipendenti'] = dipendenti
                if mitem.get('has_f24'):
                    y_tot_f24 += (mitem.get('tot_f24') or Decimal('0.00'))
                    y_has_f24 = True
                mesi.append(mitem)

            # Totali anno coerenti con la somma dei mesi
            y_tot_lordo = Decimal('0.00')
            y_tot_netto = Decimal('0.00')
            y_has_lordo = False
            y_has_netto = False
            for m in mesi:
                if m.get('has_lordo'):
                    y_tot_lordo += (m.get('tot_lordo') or Decimal('0.00'))
                    y_has_lordo = True
                if m.get('has_netto'):
                    y_tot_netto += (m.get('tot_netto') or Decimal('0.00'))
                    y_has_netto = True

            item['tot_lordo'] = y_tot_lordo
            item['tot_netto'] = y_tot_netto
            item['tot_f24'] = y_tot_f24
            item['tot_costo_azienda'] = y_tot_netto + y_tot_f24
            item['has_lordo'] = y_has_lordo
            item['has_netto'] = y_has_netto
            item['has_f24'] = y_has_f24
            item['mesi'] = mesi
            buste_anni.append(item)

        buste_num_documenti = sum(
            len(d.get('rows', []))
            for y in buste_anni
            for m in y.get('mesi', [])
            for d in m.get('dipendenti', [])
        )
        buste_num_dipendenti = len({
            d.get('dipendente').id
            for y in buste_anni
            for m in y.get('mesi', [])
            for d in m.get('dipendenti', [])
            if d.get('dipendente')
        })

        for y in buste_anni:
            if y.get('has_lordo'):
                buste_tot_lordo += (y.get('tot_lordo') or Decimal('0.00'))
                buste_has_lordo = True
            if y.get('has_netto'):
                buste_tot_netto += (y.get('tot_netto') or Decimal('0.00'))
                buste_has_netto = True
            if y.get('has_f24'):
                buste_tot_f24 += (y.get('tot_f24') or Decimal('0.00'))
                buste_has_f24 = True
        buste_tot_costo_azienda = buste_tot_netto + buste_tot_f24

        buste_anni_disponibili = anni_disp
        f24_anni_disponibili = anni_disponibili
    elif _is_admin_hr_or_consulente(request.user) and (categoria == 'f24' or tipo_filter == 'altro'):
        show_f24_dashboard = True

        f24_docs_qs = documenti.filter(tipo='altro').filter(descrizione__icontains='F24').order_by('-data_caricamento')
        f24_docs = list(f24_docs_qs)

        azienda_rif = None
        if request.user.is_superuser or request.user.has_ruolo('admin'):
            azienda_rif = get_azienda_operativa(request.user, request.session)
        elif request.user.has_ruolo('hr'):
            azienda_rif = request.user.azienda
        elif request.user.has_ruolo('consulente'):
            azienda_rif = request.user.azienda

        mov_f24_qs = MovimentoImportPaghe.objects.none()
        if azienda_rif:
            mov_f24_qs = MovimentoImportPaghe.objects.filter(
                azienda=azienda_rif,
                tipo='F24',
            ).select_related('documento')
            if anno_filter_int:
                mov_f24_qs = mov_f24_qs.filter(anno=anno_filter_int)

        mov_by_doc = {m.documento_id: m for m in mov_f24_qs if m.documento_id}
        f24_parsed_cache = {}

        grouped_f24 = defaultdict(lambda: {
            'anno': None,
            'tot_importo': Decimal('0.00'),
            'tot_debito': Decimal('0.00'),
            'tot_credito': Decimal('0.00'),
            'has_importo': False,
            'has_debito': False,
            'has_credito': False,
            'month_map': defaultdict(lambda: {
                'mese': None,
                'mese_nome': '',
                'tot_importo': Decimal('0.00'),
                'tot_debito': Decimal('0.00'),
                'tot_credito': Decimal('0.00'),
                'has_importo': False,
                'has_debito': False,
                'has_credito': False,
                'rows': [],
            }),
        })

        for doc in f24_docs:
            mov = mov_by_doc.get(doc.id)
            if not mov or not mov.anno:
                continue

            mese, anno = mov.mese, mov.anno
            if anno_filter_int and anno != anno_filter_int:
                continue

            y = grouped_f24[anno]
            y['anno'] = anno
            mese_key = mese if mese else 0
            m = y['month_map'][mese_key]
            m['mese'] = mese_key
            m['mese_nome'] = MESI_NUM_TO_NAME.get(mese_key, 'Mese non indicato')

            importo = mov.importo_netto if mov.importo_netto is not None else mov.importo
            debito = mov.f24_tot_debito
            credito = mov.f24_tot_credito
            saldo = mov.f24_saldo_finale
            if importo is None:
                if doc.id in f24_parsed_cache:
                    deb_pdf, cred_pdf, saldo_pdf = f24_parsed_cache[doc.id]
                else:
                    deb_pdf, cred_pdf, saldo_pdf = _extract_f24_totali_da_pdf(doc)
                    f24_parsed_cache[doc.id] = (deb_pdf, cred_pdf, saldo_pdf)

                if saldo_pdf is not None:
                    importo = saldo_pdf
                    try:
                        changed = []
                        if mov.importo is None:
                            mov.importo = saldo_pdf
                            changed.append('importo')
                        if mov.importo_netto is None:
                            mov.importo_netto = saldo_pdf
                            changed.append('importo_netto')
                        if mov.f24_saldo_finale is None:
                            mov.f24_saldo_finale = saldo_pdf
                            changed.append('f24_saldo_finale')
                        if mov.f24_tot_debito is None and deb_pdf is not None:
                            mov.f24_tot_debito = deb_pdf
                            changed.append('f24_tot_debito')
                        if mov.f24_tot_credito is None and cred_pdf is not None:
                            mov.f24_tot_credito = cred_pdf
                            changed.append('f24_tot_credito')
                        if changed:
                            mov.save(update_fields=changed)
                    except Exception:
                        pass
            else:
                if debito is None or credito is None or saldo is None:
                    if doc.id in f24_parsed_cache:
                        deb_pdf, cred_pdf, saldo_pdf = f24_parsed_cache[doc.id]
                    else:
                        deb_pdf, cred_pdf, saldo_pdf = _extract_f24_totali_da_pdf(doc)
                        f24_parsed_cache[doc.id] = (deb_pdf, cred_pdf, saldo_pdf)
                    try:
                        changed = []
                        if mov.f24_saldo_finale is None and saldo_pdf is not None:
                            mov.f24_saldo_finale = saldo_pdf
                            changed.append('f24_saldo_finale')
                        if mov.f24_tot_debito is None and deb_pdf is not None:
                            mov.f24_tot_debito = deb_pdf
                            changed.append('f24_tot_debito')
                        if mov.f24_tot_credito is None and cred_pdf is not None:
                            mov.f24_tot_credito = cred_pdf
                            changed.append('f24_tot_credito')
                        if changed:
                            mov.save(update_fields=changed)
                    except Exception:
                        pass

            debito = mov.f24_tot_debito
            credito = mov.f24_tot_credito
            saldo = mov.f24_saldo_finale if mov.f24_saldo_finale is not None else importo

            if importo is not None:
                y['tot_importo'] += importo
                y['has_importo'] = True
                m['tot_importo'] += importo
                m['has_importo'] = True
            if debito is not None:
                y['tot_debito'] += debito
                y['has_debito'] = True
                m['tot_debito'] += debito
                m['has_debito'] = True
            if credito is not None:
                y['tot_credito'] += credito
                y['has_credito'] = True
                m['tot_credito'] += credito
                m['has_credito'] = True

            m['rows'].append({
                'documento': doc,
                'movimento': mov,
                'importo': importo,
                'debito': debito,
                'credito': credito,
                'saldo_finale': saldo,
                'mese': mese,
                'anno': anno,
            })

        for yy in sorted(grouped_f24.keys(), reverse=True):
            item = grouped_f24[yy]
            mesi = []
            y_tot = Decimal('0.00')
            y_tot_debito = Decimal('0.00')
            y_tot_credito = Decimal('0.00')
            y_has = False
            y_has_debito = False
            y_has_credito = False
            for mk in sorted(item['month_map'].keys()):
                mitem = item['month_map'][mk]
                mitem['rows'].sort(
                    key=lambda r: (
                        r['anno'] or 0,
                        r['mese'] or 0,
                        r['documento'].data_caricamento,
                    ),
                    reverse=True,
                )
                if mitem.get('has_importo'):
                    y_tot += (mitem.get('tot_importo') or Decimal('0.00'))
                    y_has = True
                if mitem.get('has_debito'):
                    y_tot_debito += (mitem.get('tot_debito') or Decimal('0.00'))
                    y_has_debito = True
                if mitem.get('has_credito'):
                    y_tot_credito += (mitem.get('tot_credito') or Decimal('0.00'))
                    y_has_credito = True
                mesi.append(mitem)
            item['tot_importo'] = y_tot
            item['tot_debito'] = y_tot_debito
            item['tot_credito'] = y_tot_credito
            item['has_importo'] = y_has
            item['has_debito'] = y_has_debito
            item['has_credito'] = y_has_credito
            item['mesi'] = mesi
            f24_anni.append(item)

        f24_num_documenti = sum(len(m.get('rows', [])) for y in f24_anni for m in y.get('mesi', []))
        for y in f24_anni:
            if y.get('has_importo'):
                f24_tot_importo += (y.get('tot_importo') or Decimal('0.00'))
                f24_has_importo = True
            if y.get('has_debito'):
                f24_tot_debito += (y.get('tot_debito') or Decimal('0.00'))
                f24_has_debito = True
            if y.get('has_credito'):
                f24_tot_credito += (y.get('tot_credito') or Decimal('0.00'))
                f24_has_credito = True

        f24_anni_disponibili = sorted({m.anno for m in mov_f24_qs if m.anno} - {None}, reverse=True)
    else:
        buste_anni_disponibili = anni_disponibili
        f24_anni_disponibili = anni_disponibili

    if show_buste_dashboard:
        anni_filtri = buste_anni_disponibili
    elif show_f24_dashboard:
        anni_filtri = f24_anni_disponibili
    else:
        anni_filtri = anni_disponibili

    return render(request, 'documenti/lista.html', {
        'documenti': documenti,
        'tipo_filter': tipo_filter,
        'categoria': categoria,
        'anno_filter': anno_filter,
        'dipendente_filter': dipendente_filter,
        'show_buste_dashboard': show_buste_dashboard,
        'show_f24_dashboard': show_f24_dashboard,
        'buste_anni': buste_anni,
        'buste_tot_lordo': buste_tot_lordo,
        'buste_tot_netto': buste_tot_netto,
        'buste_tot_f24': buste_tot_f24,
        'buste_tot_costo_azienda': buste_tot_costo_azienda,
        'buste_show_f24_details': buste_show_f24_details,
        'buste_has_lordo': buste_has_lordo,
        'buste_has_netto': buste_has_netto,
        'buste_has_f24': buste_has_f24,
        'buste_num_dipendenti': buste_num_dipendenti,
        'buste_num_documenti': buste_num_documenti,
        'buste_anni_disponibili': buste_anni_disponibili,
        'f24_anni': f24_anni,
        'f24_tot_importo': f24_tot_importo,
        'f24_tot_debito': f24_tot_debito,
        'f24_tot_credito': f24_tot_credito,
        'f24_has_importo': f24_has_importo,
        'f24_has_debito': f24_has_debito,
        'f24_has_credito': f24_has_credito,
        'f24_num_documenti': f24_num_documenti,
        'f24_anni_disponibili': f24_anni_disponibili,
        'anni_filtri': anni_filtri,
        'anni_disponibili': anni_disponibili,
        'dipendenti_filtri': dipendenti_filtri,
        'is_admin_hr': _is_admin_or_hr(request.user),
        'is_gestore_documenti': _is_admin_hr_or_consulente(request.user),
        'tipo_choices': [
            (c, 'F24' if c == 'altro' else l)
            for c, l in Documento.TIPO_CHOICES
        ],
    })


@login_required
def lista_buste_paga(request):
    return redirect(f"{reverse('lista_documenti')}?categoria=f24&tipo=busta_paga&anno=&dipendente=")


@login_required
def lista_f24(request):
    return redirect(f"{reverse('lista_documenti')}?tipo=altro&anno=&dipendente=")


@login_required
def lista_cud(request):
    return redirect(f"{reverse('lista_documenti')}?tipo=certificato&anno=&dipendente=")


@login_required
def documenti_dipendente_admin(request, dipendente_id):
    """Admin/HR: tutti i documenti di un dipendente specifico."""
    if not _is_admin_or_hr(request.user):
        return HttpResponseForbidden("Accesso riservato.")
    dip = get_object_or_404(Dipendente, id=dipendente_id)
    documenti = Documento.objects.filter(dipendente=dip).order_by('tipo', '-data_caricamento')
    # Marca come visualizzati i documenti caricati dal dipendente
    non_visti = documenti.filter(caricato_dal_dipendente=True, visualizzato_da_azienda=False)
    if non_visti.exists():
        non_visti.update(visualizzato_da_azienda=True)
    # Raggruppa per tipo
    gruppi = {}
    for d in documenti:
        gruppi.setdefault(d.get_tipo_display(), []).append(d)
    return render(request, 'documenti/documenti_dipendente_admin.html', {
        'dipendente': dip,
        'gruppi': gruppi,
        'documenti': documenti,
    })


@login_required
def upload_documento(request):
    """Admin/HR caricano documenti aziendali per un dipendente."""
    if not _is_admin_or_hr(request.user):
        return HttpResponseForbidden("Solo admin o HR possono caricare documenti aziendali.")
    if request.method == 'POST':
        tipo = request.POST.get('tipo')
        descrizione = request.POST.get('descrizione', '')
        dipendente_id = request.POST.get('dipendente_id')
        file_obj = request.FILES.get('file')
        if not tipo or not file_obj:
            messages.error(request, "Tipo e file sono obbligatori.")
        else:
            azienda = get_azienda_operativa(request.user, request.session) if request.user.has_ruolo('admin') else getattr(request.user, 'azienda', None)
            dip = Dipendente.objects.filter(id=dipendente_id).first() if dipendente_id else None
            doc = Documento.objects.create(
                azienda=azienda,
                dipendente=dip,
                tipo=tipo,
                descrizione=descrizione,
                file=file_obj,
                caricato_da=request.user,
                caricato_dal_dipendente=False,
                visibile_al_dipendente=True,
            )

            if tipo == 'busta_paga' and dip is not None:
                mese, anno = _parse_periodo_busta(doc)
                netto_pdf, lordo_pdf = _extract_busta_importi_da_pdf(doc)
                if mese and anno:
                    MovimentoImportPaghe.objects.update_or_create(
                        azienda=azienda,
                        dipendente=dip,
                        tipo='BUSTA',
                        anno=anno,
                        mese=mese,
                        defaults={
                            'documento': doc,
                            'importo': netto_pdf,
                            'importo_netto': netto_pdf,
                            'importo_lordo': lordo_pdf,
                            'cf_estratto': (getattr(dip, 'codice_fiscale', '') or '')[:16],
                            'nominativo_estratto': f"{getattr(dip, 'cognome', '')} {getattr(dip, 'nome', '')}".strip()[:160],
                            'periodo_label': f'{mese:02d}/{anno}',
                            'source_pdf': getattr(getattr(doc, 'file', None), 'name', '') or '',
                            'page_number': None,
                        },
                    )
            messages.success(request, "Documento caricato con successo.")
            if dip:
                return redirect('documenti_dipendente_admin', dipendente_id=dip.id)
            return redirect('lista_documenti')

    azienda = get_azienda_operativa(request.user, request.session) if request.user.has_ruolo('admin') else getattr(request.user, 'azienda', None)
    dipendenti = Dipendente.objects.filter(azienda=azienda).order_by('cognome', 'nome') if azienda else []
    return render(request, 'documenti/upload.html', {
        'dipendenti': dipendenti,
        'tipo_choices': Documento.TIPO_CHOICES,
    })


@login_required
def upload_buste_paga_massivo(request):
    """Admin/HR: import PDF unico mensile con split buste + F24 separato."""
    if not _is_admin_hr_or_consulente(request.user):
        return HttpResponseForbidden("Solo admin, HR o consulente possono caricare buste massivamente.")

    azienda = get_azienda_operativa(request.user, request.session) if request.user.has_ruolo('admin') else getattr(request.user, 'azienda', None)
    if not azienda:
        messages.error(request, "Nessuna azienda operativa selezionata.")
        return redirect('lista_documenti')

    from datetime import datetime

    risultati = []
    import_results = []

    if request.method == 'POST':
        uploaded_files = request.FILES.getlist('pdf_files')
        if not uploaded_files:
            messages.warning(request, 'Seleziona almeno un PDF unico.')
            return redirect(request.path)

        snapshots_dir = Path(settings.BASE_DIR) / 'snapshots'
        snapshots_dir.mkdir(parents=True, exist_ok=True)

        for idx, up in enumerate(uploaded_files, start=1):
            nome = getattr(up, 'name', f'file_{idx}.pdf')
            if not nome.lower().endswith('.pdf'):
                risultati.append({'file': nome, 'ok': False, 'errore': 'Formato non supportato (solo PDF).'})
                continue

            tmp_path = None
            try:
                with tempfile.NamedTemporaryFile(delete=False, suffix='.pdf') as tmp:
                    for chunk in up.chunks():
                        tmp.write(chunk)
                    tmp_path = tmp.name

                stamp = datetime.now().strftime('%Y%m%d_%H%M%S')
                preview_out = snapshots_dir / f'preview_admin_hr_{stamp}_{idx}.json'

                buff_prev = io.StringIO()
                call_command(
                    'preview_import_paghe_pdf',
                    tmp_path,
                    azienda_id=azienda.id,
                    source_name=nome,
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

                for row in preview_data.get('rows', []):
                    periodo = (row.get('periodo') or '').strip()
                    mese_r = anno_r = None
                    m_periodo = re.match(r'^(\d{2})/(\d{4})$', periodo)
                    if m_periodo:
                        mese_r = int(m_periodo.group(1))
                        anno_r = int(m_periodo.group(2))

                    dip = None
                    dip_id = row.get('dipendente_id')
                    if dip_id:
                        dip = Dipendente.objects.filter(id=dip_id, azienda=azienda).first()
                    if dip is None and row.get('cf'):
                        dip = Dipendente.objects.filter(azienda=azienda, codice_fiscale=row.get('cf')).first()

                    mov = None
                    if mese_r and anno_r:
                        qs = MovimentoImportPaghe.objects.filter(azienda=azienda, tipo='BUSTA', anno=anno_r, mese=mese_r).select_related('documento', 'dipendente')
                        if dip is not None:
                            mov = qs.filter(dipendente=dip).first()
                        elif row.get('cf'):
                            mov = qs.filter(cf_estratto=row.get('cf')).first()
                            if mov and mov.dipendente_id:
                                dip = mov.dipendente

                    action = row.get('action')
                    import_results.append({
                        'filename': nome,
                        'periodo': periodo or '-',
                        'esito': 'scartato' if action == 'already_present' else ('ok' if action != 'ambiguous' and mov else ('errore' if action == 'ambiguous' else 'attenzione')),
                        'messaggio': 'Busta già presente per il periodo' if action == 'already_present' else ('Importato' if mov else ('Match ambiguo' if action == 'ambiguous' else 'Da verificare')),
                        'dipendente': dip,
                        'lordo': (getattr(mov, 'importo_lordo', None) if mov else None) or _parse_decimal_text(row.get('lordo_busta')),
                        'netto': (getattr(mov, 'importo_netto', None) if mov else None) or _parse_decimal_text(row.get('netto_busta')),
                        'documento_id': getattr(mov, 'documento_id', None) if mov else None,
                    })
            except Exception as exc:
                risultati.append({'file': nome, 'ok': False, 'errore': str(exc)})
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
            registra_log(
                utente=request.user,
                azienda=azienda,
                operazione='import_pdf_unico_admin_hr',
                descrizione=f'Admin/HR ha importato {ok_n} PDF unici buste/F24',
                request=request,
            )
        if ko_n:
            messages.warning(request, f'⚠️ {ko_n} file non elaborati. Controlla il dettaglio sotto.')

        return render(request, 'documenti/upload_buste_massivo.html', {
            'azienda': azienda,
            'risultati': risultati,
            'import_results': import_results,
        })

    return render(request, 'documenti/upload_buste_massivo.html', {
        'azienda': azienda,
        'risultati': [],
        'import_results': [],
    })


@login_required
def upload_documento_personale(request):
    """Dipendente/candidato carica i propri documenti personali."""
    ruolo = None
    if _is_candidato(request.user):
        ruolo = 'candidato'
        profilo = getattr(request.user, 'profilo_candidato', None)
        dip = profilo.dipendente if profilo else None
    elif _is_dipendente(request.user):
        ruolo = 'dipendente'
        dip = Dipendente.objects.filter(utente=request.user).first()
    else:
        return HttpResponseForbidden("Accesso negato.")

    if not dip:
        messages.error(request, "Nessun profilo dipendente associato.")
        return redirect('candidato_dashboard' if ruolo == 'candidato' else 'lista_documenti')

    TIPI_PERSONALI = [
        ('documento_identita', 'Documento di identità'),
        ('permesso_soggiorno', 'Permesso di soggiorno'),
        ('codice_fiscale_doc', 'Tessera sanitaria / Codice fiscale'),
        ('attestato', 'Attestato professionale'),
        ('certificazione', 'Certificazione / Titolo di studio'),
        ('altro', 'Altro'),
    ]

    if request.method == 'POST':
        tipo = request.POST.get('tipo')
        descrizione = request.POST.get('descrizione', '')
        file_obj = request.FILES.get('file')
        if not tipo or not file_obj:
            messages.error(request, "Tipo e file sono obbligatori.")
        else:
            Documento.objects.create(
                azienda=dip.azienda,
                dipendente=dip,
                tipo=tipo,
                descrizione=descrizione,
                file=file_obj,
                caricato_da=request.user,
                caricato_dal_dipendente=True,
                visibile_al_dipendente=True,
            )
            messages.success(request, "Documento caricato con successo.")
            return redirect('candidato_miei_documenti' if ruolo == 'candidato' else 'lista_documenti')

    return render(request, 'documenti/upload_personale.html', {
        'tipo_choices': TIPI_PERSONALI,
        'dipendente': dip,
    })


@login_required
def download_documento(request, documento_id):
    """Download documento — autorizzazione per ruolo."""
    documento = get_object_or_404(Documento, id=documento_id)
    access_error = _assert_documento_accesso(request, documento)
    if isinstance(access_error, HttpResponseForbidden):
        return access_error

    if not documento.file:
        raise Http404("File non trovato.")

    try:
        return FileResponse(
            documento.file.open('rb'),
            as_attachment=True,
            filename=os.path.basename(documento.file.name),
        )
    except FileNotFoundError:
        raise Http404("File non trovato sul server.")


@login_required
def visualizza_documento(request, documento_id):
    """Visualizzazione inline documento (PDF browser), con stessi permessi del download."""
    documento = get_object_or_404(Documento, id=documento_id)

    access_error = _assert_documento_accesso(request, documento)
    if isinstance(access_error, HttpResponseForbidden):
        return access_error

    if not documento.file:
        raise Http404("File non trovato.")

    try:
        return FileResponse(
            documento.file.open('rb'),
            as_attachment=False,
            filename=os.path.basename(documento.file.name),
        )
    except FileNotFoundError:
        raise Http404("File non trovato sul server.")


@login_required
def legacy_documento_redirect(request, legacy_filename):
    """Compatibilità URL storiche tipo /documenti/f24_...pdf -> visualizza_documento."""
    name = (legacy_filename or '').strip()
    if not name or '/' in name or not name.lower().endswith('.pdf'):
        raise Http404("Documento non trovato.")

    documento = Documento.objects.filter(file__iendswith=f"documenti/{name}").first()
    if not documento:
        documento = Documento.objects.filter(file__iendswith=name).first()
    if not documento:
        raise Http404("Documento non trovato.")

    access_error = _assert_documento_accesso(request, documento)
    if isinstance(access_error, HttpResponseForbidden):
        return access_error

    return redirect('visualizza_documento', documento_id=documento.id)


@login_required
def elimina_documento(request, documento_id):
    """Elimina un documento — solo POST.

    Regole:
    - Admin/HR: possono sempre eliminare.
    - Dipendente/Candidato: possono eliminare solo documenti caricati da loro
      (caricato_dal_dipendente=True) E non ancora visualizzati dall'azienda
      (visualizzato_da_azienda=False). Una volta che l'azienda ha visualizzato
      il documento, il dipendente non può più eliminarlo. L'admin non sblocca.
    """
    if request.method != 'POST':
        return HttpResponseForbidden("Metodo non consentito.")

    documento = get_object_or_404(Documento, id=documento_id)
    ruolo = None
    if _is_dipendente(request.user):
        ruolo = 'dipendente'
    elif _is_candidato(request.user):
        ruolo = 'candidato'

    if _is_admin_or_hr(request.user):
        pass  # autorizzato
    elif ruolo in ('dipendente', 'candidato'):
        # Deve essere un documento caricato dal dipendente stesso
        profilo = getattr(request.user, 'profilo_candidato', None)
        if ruolo == 'dipendente':
            dip = Dipendente.objects.filter(utente=request.user).first()
        else:
            dip = profilo.dipendente if profilo else None

        if not (dip and documento.dipendente == dip and documento.caricato_dal_dipendente):
            return HttpResponseForbidden("Non puoi eliminare questo documento.")

        # Blocco: se già visualizzato dall'azienda non si può più eliminare
        if documento.visualizzato_da_azienda:
            messages.error(
                request,
                "Documento acquisito dall'azienda — non è più possibile eliminarlo."
            )
            return redirect('candidato_miei_documenti')
    else:
        return HttpResponseForbidden("Accesso negato.")

    # Elimina il file fisico e il record
    if documento.file:
        try:
            storage = documento.file.storage
            path = documento.file.name
            documento.delete()
            storage.delete(path)
        except Exception:
            documento.delete()
    else:
        documento.delete()

    messages.success(request, "Documento eliminato.")
    next_url = request.POST.get('next', '')
    if next_url in ('/candidato/documenti/', '/documenti/'):
        return redirect(next_url)
    return redirect('candidato_miei_documenti' if ruolo in ('candidato', 'dipendente') else 'lista_documenti')
