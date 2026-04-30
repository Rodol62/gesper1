import os
import re
import io
import json
import csv
import subprocess
import tempfile
from collections import Counter
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from urllib.parse import unquote, urlencode
from collections import defaultdict
from django.shortcuts import render, get_object_or_404, redirect
from django.core.paginator import Paginator
from django.http import HttpResponseForbidden, FileResponse, Http404, JsonResponse, HttpResponse
from django.contrib import messages
from django.conf import settings
from django.core.management import call_command
from django.core.files.base import ContentFile
from django.contrib.auth.decorators import login_required, user_passes_test
from django.views.decorators.http import require_POST
from django.urls import get_script_prefix, reverse
from django.utils import timezone
from django.db.models import Q
from .cedolino_motore_v4_risolvi import (
    cedolini_v4_tutti_per_periodi,
    mappa_cedolini_v4_per_documenti,
    mappa_cedolini_v4_per_periodi,
    risolvi_cedolino_motore_v4_per_documento_busta,
)
from .models import Documento
from .buste_cedolino_batch import (
    build_cedolini_zip_bytes,
    documento_ids_busta_per_anno,
    estrai_report_per_documento,
    parse_periodo_busta,
    parse_periodo_busta_con_pdf,
    periodo_retributivo_effettivo,
    queryset_buste_anno,
)
from .cedolino_confronto_import import netto_lordo_da_report
from .cedolino_conciliazione_motore_v4 import (
    compact_conciliazione_per_tabella,
    conciliazione_oggi_vs_cedolino_motore_v4,
    format_euro_conc,
)
from .cedolino_estrazione_v4_store import tenta_persistenza_cedolino_v4_dopo_lettura
from .natura_busta_utils import infer_natura_busta_per_busta
from .cedolini_tolleranze import tolleranze_cedolini_context
from .cedolino_verifica_v4 import persisti_esito_verifica_da_riga_busta
from .busta_acquisizione import acquisisci_busta_da_documento, acquisisci_busta_pdf_bytes
from .imponibile_inps_da_voci import confronto_imponibile_inps_da_lettura_cedolino
from anagrafiche.models import Azienda, Dipendente
from accounts.gestione_database import can_gestione_database
from accounts.models import MovimentoImportPaghe, ProfiloCandidato
from rapporto_di_lavoro.models import RapportoDiLavoro
from anagrafiche.permissions import admin_required, hr_required, is_admin
from log_attivita.utils import registra_log
from log_attivita.anomalie import build_anomalia_import_export, registra_evento_anomalia
from accounts.tenant import get_azienda_operativa
from accounts.dipendente_portale import get_dipendente_collegato
from log_attivita.models import LogAttivita
from gesper_next_url import sanitize_internal_next

from .buste_pdf_passwords import STUDIO_DEFAULT_PASSWORD, passwords_for_busta_pdf_read

PDF_BUSTE_PASSWORD = STUDIO_DEFAULT_PASSWORD
BUSTE_LETTURA_CEDOLINO_PAGE_SIZE = 12
BUSTE_LETTURA_SCHEDE = frozenset({"completo", "estrazione_v4", "conciliazione"})
BUSTE_LETTURA_REDIRECT_NAME = {
    "completo": "buste_paga_lettura_cedolino",
    "estrazione_v4": "buste_paga_estrazione_motore_v4",
    "conciliazione": "buste_paga_conciliazione_cedolino",
}


def _is_admin_or_hr(user):
    return user.is_superuser or user.has_ruolo('admin') or user.has_ruolo('hr')


def _is_admin_hr_or_consulente(user):
    return _is_admin_or_hr(user) or user.has_ruolo('consulente')


def _dipendente_documenti_accessible(request, dip) -> bool:
    """Admin su azienda operativa; HR e consulente sulla propria azienda."""
    u = request.user
    if u.is_superuser or u.has_ruolo('admin'):
        az = get_azienda_operativa(u, request.session)
        return az is not None and dip.azienda_id == az.id
    if u.has_ruolo('hr') or u.has_ruolo('consulente'):
        return getattr(u, 'azienda_id', None) and dip.azienda_id == u.azienda_id
    return False


def _is_dipendente(user):
    return user.has_ruolo('dipendente')


def _is_candidato(user):
    return user.has_ruolo('candidato')


def _azienda_scope_for_user(user, request):
    """Azienda operativa corrente per controlli tenant-aware."""
    if user.is_superuser or user.has_ruolo('admin'):
        return get_azienda_operativa(user, request.session)
    if user.has_ruolo('hr') or user.has_ruolo('consulente'):
        return getattr(user, 'azienda', None)
    return None


def _documento_file_disponibile(doc: Documento) -> bool:
    if not doc or not getattr(doc, "file", None):
        return False
    name = getattr(doc.file, "name", None)
    if not name:
        return False
    from .file_path_resolution import first_existing_relpath_for_stored_name, stored_relpath_equivalent

    resolved = first_existing_relpath_for_stored_name(doc.file.storage, name)
    if not resolved:
        return False
    if stored_relpath_equivalent(resolved, name):
        return True
    try:
        doc.file.name = resolved
        doc.save(update_fields=["file"])
        return True
    except Exception:
        return False


def _find_documento_alternativo_con_file(documento: Documento) -> Documento | None:
    """Cerca un documento equivalente (stesso contesto logico) con file effettivamente disponibile."""
    if not documento:
        return None

    qs = Documento.objects.filter(
        azienda=documento.azienda,
        dipendente=documento.dipendente,
        tipo=documento.tipo,
        descrizione=documento.descrizione,
    ).exclude(id=documento.id).order_by('-data_caricamento', '-id')

    for cand in qs:
        if _documento_file_disponibile(cand):
            return cand
    return None


def _redirect_documenti_fallback(request, documento: Documento | None = None):
    """Ritorna alla dashboard documenti più coerente possibile (evita lista vuota)."""
    referer = (request.META.get('HTTP_REFERER') or '').strip()
    if referer and '/documenti/' in referer:
        return redirect(referer)

    base = reverse('lista_documenti')
    if documento is not None:
        if documento.tipo == 'busta_paga':
            return redirect(f"{base}?categoria=buste&tipo=busta_paga&anno=&dipendente=")
        if documento.tipo == 'altro':
            return redirect(f"{base}?tipo=altro&anno=&dipendente=")
        if documento.tipo == 'certificato':
            return redirect(f"{base}?tipo=certificato&anno=&dipendente=")

    return redirect(base)


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


def _extract_anno_from_descrizione(text: str | None) -> int | None:
    if not text:
        return None
    m = re.search(r'\b(20\d{2})\b', str(text))
    if not m:
        return None
    try:
        return int(m.group(1))
    except (TypeError, ValueError):
        return None


def _extract_cud_anni_documento(doc: Documento) -> dict:
    """Estrae anno CU e anno di riferimento da descrizione/PDF (fallback prudente)."""
    anno_cu = _extract_anno_from_descrizione(getattr(doc, 'descrizione', None))
    if not anno_cu:
        anno_cu = getattr(getattr(doc, 'data_caricamento', None), 'year', None)

    anno_riferimento = None
    anno_riferimento_stimato = False

    descr = (getattr(doc, 'descrizione', '') or '').upper()
    for pattern in (
        r"ANNO\s+D['’]IMPOSTA\s*(20\d{2})",
        r"ANNO\s*DI\s*RIFERIMENTO\s*(20\d{2})",
        r"REDDITI\s*(20\d{2})",
        r"ANNO\s*(20\d{2})",
    ):
        m = re.search(pattern, descr)
        if m:
            try:
                anno_riferimento = int(m.group(1))
                break
            except (TypeError, ValueError):
                pass

    if anno_riferimento is None and _documento_file_disponibile(doc):
        try:
            from pypdf import PdfReader

            with doc.file.open('rb') as fh:
                reader = PdfReader(fh)
                if getattr(reader, 'is_encrypted', False):
                    unlocked = False
                    for pwd in (PDF_BUSTE_PASSWORD, ''):
                        try:
                            if reader.decrypt(pwd):
                                unlocked = True
                                break
                        except Exception:
                            continue
                    if not unlocked:
                        reader = None
                if reader and reader.pages:
                    txt = (reader.pages[0].extract_text() or '').upper()
                    for pattern in (
                        r"ANNO\s+D['’]IMPOSTA\s*(20\d{2})",
                        r"ANNO\s*DI\s*RIFERIMENTO\s*(20\d{2})",
                        r"REDDITI\s*(20\d{2})",
                    ):
                        m = re.search(pattern, txt)
                        if m:
                            try:
                                anno_riferimento = int(m.group(1))
                                break
                            except (TypeError, ValueError):
                                pass
        except Exception:
            pass

    if anno_riferimento is None and anno_cu:
        # Fallback pratico per CU: normalmente l'anno d'imposta è il precedente.
        anno_riferimento = anno_cu - 1
        anno_riferimento_stimato = True

    return {
        'anno_cu': anno_cu,
        'anno_riferimento': anno_riferimento,
        'anno_riferimento_stimato': anno_riferimento_stimato,
    }


def _next_placeholder_punto_zero(azienda) -> int:
    """Ritorna il prossimo progressivo per dipendente placeholder 'Punto Zero n. XX'."""
    max_n = 0
    for nome in Dipendente.objects.filter(azienda=azienda, cognome='Punto Zero').values_list('nome', flat=True):
        m = re.match(r"^n\.\s*(\d{1,3})$", (nome or '').strip(), flags=re.IGNORECASE)
        if not m:
            continue
        try:
            max_n = max(max_n, int(m.group(1)))
        except Exception:
            continue
    return max_n + 1


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


def _extract_amount_by_labels_cedolino(text: str, label_patterns):
    """
    Come _extract_amount_by_labels ma prova l'ultimo importo sulla stessa riga (dopo l'etichetta):
    nei cedolini a più colonne il totale in euro è spesso l'ultimo valore a destra, non il primo.
    """
    amount_re = re.compile(
        r'([+-]?\s*[0-9]{1,3}(?:\s*[\.\,\u00A0\']\s*[0-9]{3})*\s*[\,\.]\s*[0-9]{2}|[+-]?\s*[0-9]+\s*[\,\.]\s*[0-9]{2})'
    )
    lines = [re.sub(r'\s+', ' ', ln).strip() for ln in (text or '').splitlines()]

    def _amounts_in(raw: str):
        out = []
        for m in amount_re.findall(raw or ''):
            v = _parse_decimal_text(re.sub(r'\s+', '', m))
            if v is not None:
                out.append(v)
        return out

    for i, ln in enumerate(lines):
        if not ln:
            continue
        for pat in label_patterns:
            m_lbl = re.search(pat, ln, re.IGNORECASE)
            if not m_lbl:
                continue
            tail = ln[m_lbl.end():]
            amounts = _amounts_in(tail)
            if amounts:
                return amounts[-1]
            for j in range(i + 1, min(i + 5, len(lines))):
                amounts = _amounts_in(lines[j])
                if amounts:
                    return amounts[-1]

    for pat in label_patterns:
        m = re.search(pat + r'([\s\S]{0,400})', text or '', re.IGNORECASE)
        if not m:
            continue
        amounts = _amounts_in(m.group(1))
        if amounts:
            return amounts[-1]
    return None


def _busta_pdf_full_text_pypdf(doc: Documento) -> str:
    """Testo da tutte le pagine (PyPDF), per cedolini multipagina."""
    try:
        from pypdf import PdfReader
    except Exception:
        return ''
    try:
        with doc.file.open('rb') as fh:
            reader = PdfReader(fh)
            if getattr(reader, 'is_encrypted', False):
                try:
                    reader.decrypt(PDF_BUSTE_PASSWORD)
                except Exception:
                    return ''
            parts = []
            for p in reader.pages:
                try:
                    parts.append(p.extract_text() or '')
                except Exception:
                    parts.append('')
            return '\n'.join(parts)
    except Exception:
        return ''


# Importo in coda riga (etichetta a sinistra, valore a destra — layout cedolino)
_BUSTA_TAIL_AMOUNT_RE = re.compile(
    r'([+-]?\s*[0-9]{1,3}(?:\s*[.\u00A0\']\s*[0-9]{3})*\s*[,\.]\s*[0-9]{2})\s*$'
)


def _busta_cluster_words_in_righe(words, y_tol=2.0):
    """Raggruppa parole pdfplumber per banda verticale → righe di testo ricomposte."""
    from collections import defaultdict

    buckets = defaultdict(list)
    for w in words or []:
        try:
            t = float(w.get('top', 0))
        except (TypeError, ValueError):
            continue
        key = round(t / y_tol) * y_tol
        buckets[key].append(w)
    righe = []
    for key in sorted(buckets.keys()):
        ws = sorted(buckets[key], key=lambda x: float(x.get('x0', 0)))
        parts = [(x.get('text') or '').strip() for x in ws if (x.get('text') or '').strip()]
        if not parts:
            continue
        txt = re.sub(r'\s+', ' ', ' '.join(parts)).strip()
        if txt:
            righe.append(txt)
    return righe


def _busta_parole_per_riga(words, y_tol=2.5):
    """Come una scansione per fasce orizzontali: lista di righe, ciascuna è la lista di parole (ordinate per x)."""
    from collections import defaultdict

    buckets = defaultdict(list)
    for w in words or []:
        try:
            t = float(w.get('top', 0))
        except (TypeError, ValueError):
            continue
        key = round(t / y_tol) * y_tol
        buckets[key].append(w)
    out = []
    for key in sorted(buckets.keys()):
        ws = sorted(buckets[key], key=lambda x: float(x.get('x0', 0)))
        if ws:
            out.append(ws)
    return out


def _busta_gap_split_words(ws, gap_min_pt=12.0):
    """Suddivide una riga in segmenti orizzontali (colonne) in base ai vuoti tra le parole."""
    if not ws:
        return []
    sorted_ws = sorted(ws, key=lambda w: float(w.get('x0', 0)))
    segments = [[sorted_ws[0]]]
    for w in sorted_ws[1:]:
        prev = segments[-1][-1]
        try:
            x1_prev = float(prev.get('x1', prev.get('x0', 0)))
            x0_w = float(w.get('x0', 0))
        except (TypeError, ValueError):
            segments[-1].append(w)
            continue
        if x0_w - x1_prev > gap_min_pt:
            segments.append([w])
        else:
            segments[-1].append(w)
    return segments


def _busta_segment_text(seg):
    parts = [(x.get('text') or '').strip() for x in (seg or []) if (x.get('text') or '').strip()]
    return re.sub(r'\s+', ' ', ' '.join(parts)).strip()


def _busta_parse_importo_segmento(testo: str):
    """Ritorna (Decimal, testo_norm) se il segmento è un importo, altrimenti None."""
    t = re.sub(r'\s+', ' ', (testo or '').strip())
    if not t or len(t) > 32:
        return None
    raw = re.sub(r'\s+', '', t.replace("'", ''))
    dec = _parse_decimal_text(raw)
    if dec is None or not re.search(r'\d', t):
        return None
    return dec, t


def _busta_voce_da_riga_layout(ws, gap_min_pt=12.0):
    """
    Una riga = banda y: a sinistra etichetta (campo), a destra uno o più importi (colonne).
    Simula lettura per layout da PDF vettoriale / cedolino a colonne.
    """
    segments = _busta_gap_split_words(ws, gap_min_pt=gap_min_pt)
    seg_texts = [_busta_segment_text(s) for s in segments]
    seg_texts = [t for t in seg_texts if t]
    if not seg_texts:
        return None

    trail = []
    idx = len(seg_texts) - 1
    while idx >= 0:
        parsed = _busta_parse_importo_segmento(seg_texts[idx])
        if parsed is not None:
            dec, raw_t = parsed
            trail.insert(0, {'testo': raw_t, 'valore': dec})
            idx -= 1
        else:
            break
    label_parts = seg_texts[: idx + 1]
    etichetta = re.sub(r'\s+', ' ', ' '.join(label_parts)).strip() if label_parts else ''

    if trail:
        if len(etichetta) < 2:
            if len(trail) == 1:
                t0 = trail[0]['testo']
                return {
                    'etichetta': t0[:520],
                    'descrizione': t0[:520],
                    'valore': trail[0]['valore'],
                    'valore_testo': t0,
                    'tipo': 'layout_importo_isolato',
                    'layout_colonne': 1,
                }
            return None
        voce = {
            'etichetta': etichetta[:520],
            'descrizione': etichetta[:520],
            'valore': trail[-1]['valore'],
            'valore_testo': ' | '.join(x['testo'] for x in trail),
            'tipo': 'layout_riga',
            'layout_colonne': len(trail),
        }
        if len(trail) > 1:
            voce['importi_per_colonna'] = [x['valore'] for x in trail]
        return voce

    full = re.sub(r'\s+', ' ', ' '.join(seg_texts)).strip()
    st = _busta_riga_a_voce_strutturata(full)
    if st:
        lab = (st.get('descrizione') or '')[:520]
        st['etichetta'] = lab
        st['descrizione'] = lab
        st['tipo'] = f"{st.get('tipo', 'testo')}_layout_unita"
        return st
    return None


def _busta_riduci_voce_solo_etichetta(voce: dict):
    """
    Tiene solo il testo etichetta/campo; scarta importi isolati e righe che sono solo numeri.
    Ritorna dict minimal o None se la riga non ha etichetta testuale.
    """
    tipo = voce.get('tipo') or ''
    if tipo == 'layout_importo_isolato':
        return None
    eti = (voce.get('etichetta') or voce.get('descrizione') or '').strip()
    if len(eti) < 2:
        return None
    if _busta_solo_importo_senza_etichetta(eti):
        return None
    return {
        'etichetta': eti[:520],
        'descrizione': eti[:520],
        'tipo': 'etichetta',
    }


def _busta_finalizza_e_append_voce(
    voce: dict, pagina: int, voci: list, ordine: list, solo_etichette: bool = False
) -> None:
    if solo_etichette:
        voce = _busta_riduci_voce_solo_etichetta(voce)
        if voce is None:
            return
    else:
        eti = (voce.get('etichetta') or voce.get('descrizione') or '').strip()
        voce['etichetta'] = eti[:520]
        voce['descrizione'] = (voce.get('descrizione') or eti)[:520]
    voce['pagina'] = pagina
    voce['ordine'] = ordine[0]
    ordine[0] += 1
    voci.append(voce)


def _busta_append_voce_da_stringa(
    s_norm: str, pagina: int, voci: list, ordine: list, solo_etichette: bool = False
) -> None:
    if not s_norm:
        return
    parsed = _busta_riga_a_voce_strutturata(s_norm)
    if parsed:
        lab = (parsed.get('descrizione') or '')[:520]
        parsed['etichetta'] = lab
        parsed['descrizione'] = lab
        _busta_finalizza_e_append_voce(parsed, pagina, voci, ordine, solo_etichette=solo_etichette)
        return
    if _busta_includi_solo_descrizione(s_norm):
        _busta_finalizza_e_append_voce({
            'etichetta': s_norm[:520],
            'descrizione': s_norm[:520],
            'valore': None,
            'valore_testo': None,
            'tipo': 'solo_descrizione',
        }, pagina, voci, ordine, solo_etichette=solo_etichette)


def _busta_gap_min_da_pagina(page) -> float:
    try:
        pw = float(page.width or 0)
        if pw > 0:
            return max(9.0, min(28.0, pw * 0.016))
    except (TypeError, ValueError):
        pass
    return 12.0


def _busta_accumula_voci_da_layout_pagina(
    page, pagina: int, voci: list, ordine: list, solo_etichette: bool = False
) -> None:
    """Estrae voci rispettando colonne orizzontali (layout cedolino)."""
    try:
        words = page.extract_words(keep_blank_chars=False) or []
    except Exception:
        words = []
    gap = _busta_gap_min_da_pagina(page)
    if words:
        for ws in _busta_parole_per_riga(words):
            if not ws:
                continue
            voce = _busta_voce_da_riga_layout(ws, gap_min_pt=gap)
            if voce is not None:
                _busta_finalizza_e_append_voce(voce, pagina, voci, ordine, solo_etichette=solo_etichette)
                continue
            full = _busta_segment_text(ws)
            if full:
                _busta_append_voce_da_stringa(full, pagina, voci, ordine, solo_etichette=solo_etichette)
        return
    try:
        ttxt = page.extract_text() or ''
        for ln in ttxt.splitlines():
            s = re.sub(r'\s+', ' ', ln).strip()
            if s:
                _busta_append_voce_da_stringa(s, pagina, voci, ordine, solo_etichette=solo_etichette)
    except Exception:
        pass


def _coppia_coda_importo_su_riga(s: str):
    s = re.sub(r'\s+', ' ', (s or '').strip())
    if len(s) < 4:
        return None
    m = _BUSTA_TAIL_AMOUNT_RE.search(s)
    if not m:
        return None
    val_raw = m.group(1).strip()
    etichetta = s[: m.start()].strip(' -–:\t')
    if len(etichetta) < 2:
        return None
    val = _parse_decimal_text(re.sub(r'\s+', '', val_raw.replace("'", '')))
    if val is None:
        return None
    return {
        'etichetta': etichetta[:220],
        'valore': val,
        'valore_testo': val_raw,
        'tipo': 'importo',
        'fonte': 'importo_a_fine_riga',
    }


def _coppia_due_punti_su_riga(s: str):
    """Etichetta: valore (testo o importo compatto)."""
    s = re.sub(r'\s+', ' ', (s or '').strip())
    if ':' not in s or len(s) < 4:
        return None
    idx = s.find(':')
    lab = s[:idx].strip()
    rest = s[idx + 1 :].strip()
    if len(lab) < 2 or len(rest) < 1:
        return None
    if len(lab) > 100 or len(rest) > 120:
        return None
    raw_compact = rest.replace(' ', '').replace("'", '')
    dec = _parse_decimal_text(raw_compact) if re.search(r'\d', rest) else None
    if dec is not None and len(rest) <= 24:
        return {
            'etichetta': lab[:200],
            'valore': dec,
            'valore_testo': rest,
            'tipo': 'importo',
            'fonte': 'due_punti',
        }
    if len(rest) <= 100 and not rest.startswith('http'):
        return {
            'etichetta': lab[:200],
            'valore': None,
            'valore_testo': rest[:500],
            'tipo': 'testo',
            'fonte': 'due_punti',
        }
    return None


def _busta_riga_a_voce_strutturata(s: str):
    """
    Da una riga del cedolino: importo in coda oppure 'Etichetta: valore'.
    Ritorna dict con chiavi descrizione, valore, valore_testo, tipo — o None.
    """
    c = _coppia_coda_importo_su_riga(s)
    if c:
        return {
            'descrizione': c['etichetta'],
            'valore': c['valore'],
            'valore_testo': c['valore_testo'],
            'tipo': 'importo_coda',
        }
    c2 = _coppia_due_punti_su_riga(s)
    if not c2:
        return None
    if c2['tipo'] == 'importo':
        return {
            'descrizione': c2['etichetta'],
            'valore': c2['valore'],
            'valore_testo': c2['valore_testo'],
            'tipo': 'due_punti_importo',
        }
    return {
        'descrizione': c2['etichetta'],
        'valore': None,
        'valore_testo': (c2.get('valore_testo') or '')[:500],
        'tipo': 'due_punti_testo',
    }


def _busta_solo_importo_senza_etichetta(s: str) -> bool:
    """Riga che è solo un importo (nessuna parte testuale descrittiva)."""
    t = re.sub(r'\s+', ' ', (s or '').strip())
    if not t or len(t) > 36:
        return False
    if re.search(r'[A-Za-zÀ-ÿ]', t):
        return False
    raw = re.sub(r'\s+', '', t.replace("'", ''))
    return _parse_decimal_text(raw) is not None


def _busta_includi_solo_descrizione(s: str) -> bool:
    """Riga testuale da mostrare come voce senza valore associato (intestazioni, note, ecc.)."""
    t = re.sub(r'\s+', ' ', (s or '').strip())
    if len(t) < 3 or len(t) > 520:
        return False
    if not re.search(r'[A-Za-zÀ-ÿ]', t):
        return False
    if t.lower().startswith('http'):
        return False
    if re.match(r'^Pag\.?\s*\d+\s*(/\s*\d+)?\s*$', t, re.I):
        return False
    if _busta_solo_importo_senza_etichetta(t):
        return False
    return True


def _busta_accumula_voci_da_righe(
    righe: list, pagina: int, voci: list, ordine: list, solo_etichette: bool = False
) -> None:
    """Solo testo riga per riga (fallback pypdf / senza layout colonne)."""
    for s in righe or []:
        s_norm = re.sub(r'\s+', ' ', (s or '').strip())
        if s_norm:
            _busta_append_voce_da_stringa(s_norm, pagina, voci, ordine, solo_etichette=solo_etichette)


def estrai_voci_descrittive_busta_paga_pdf(doc: Documento, *, solo_etichette: bool = False) -> dict:
    """
    Legge l'intero PDF busta paga (tutte le pagine, in ordine), come lettura per layout (scansione logica):
    per ogni fascia orizzontale separa colonne per spaziatura x → etichetta (campo) a sinistra,
    uno o più importi a destra; ogni etichetta è il campo a cui si associa il valore (ultima colonna €).
    Con solo_etichette=True si emettono solo le etichette testuali (nessun importo in output).
    Fallback: riga unica o pypdf senza coordinate.
    Non usa il motore Libro Unico (estrai_busta_dettaglio_libro_paga_da_pdf).
    """
    out = {
        'ok': False,
        'errore': None,
        'num_pagine': 0,
        'metodo': None,
        'voci': [],
        'n_voci': 0,
        'anteprima_testo': '',
        'solo_etichette': solo_etichette,
    }
    if not getattr(doc, 'file', None):
        out['errore'] = 'Documento senza file'
        return out

    try:
        raw = doc.file.read()
    except Exception as exc:
        out['errore'] = f'Lettura file: {exc}'
        return out

    voci = []
    ord_ctr = [0]
    metodo = None
    err_plumber = None

    try:
        import pdfplumber
    except ImportError:
        pdfplumber = None

    if pdfplumber:
        try:
            with pdfplumber.open(io.BytesIO(raw), password=PDF_BUSTE_PASSWORD) as pdf:
                metodo = 'pdfplumber'
                out['num_pagine'] = len(pdf.pages)
                for i, page in enumerate(pdf.pages, start=1):
                    _busta_accumula_voci_da_layout_pagina(
                        page, i, voci, ord_ctr, solo_etichette=solo_etichette
                    )
        except Exception as exc:
            err_plumber = str(exc)
            voci.clear()
            ord_ctr[0] = 0
            metodo = None

    if not voci:
        try:
            from pypdf import PdfReader
            reader = PdfReader(io.BytesIO(raw))
            if getattr(reader, 'is_encrypted', False):
                try:
                    reader.decrypt(PDF_BUSTE_PASSWORD)
                except Exception:
                    pass
            out['num_pagine'] = len(reader.pages)
            metodo = 'pypdf'
            for i, p in enumerate(reader.pages, start=1):
                try:
                    txt = p.extract_text() or ''
                except Exception:
                    txt = ''
                righe = [re.sub(r'\s+', ' ', ln).strip() for ln in txt.splitlines() if ln.strip()]
                _busta_accumula_voci_da_righe(righe, i, voci, ord_ctr, solo_etichette=solo_etichette)
            parts_ant = []
            for p in reader.pages[:3]:
                try:
                    parts_ant.append(p.extract_text() or '')
                except Exception:
                    parts_ant.append('')
            out['anteprima_testo'] = '\n'.join(parts_ant)[:12000]
        except Exception as exc:
            out['errore'] = err_plumber or f'pypdf: {exc}'
            out['metodo'] = None
            out['voci'] = []
            out['n_voci'] = 0
            return out

    out['voci'] = voci
    out['n_voci'] = len(voci)
    out['metodo'] = metodo
    out['ok'] = len(voci) > 0
    if not out['ok'] and not out['errore']:
        out['errore'] = err_plumber or 'Nessuna riga estraibile dal PDF'
    if metodo == 'pdfplumber' and not out['anteprima_testo']:
        try:
            from pypdf import PdfReader
            reader = PdfReader(io.BytesIO(raw))
            if getattr(reader, 'is_encrypted', False):
                try:
                    reader.decrypt(PDF_BUSTE_PASSWORD)
                except Exception:
                    pass
            parts_ant = []
            for p in reader.pages[:2]:
                try:
                    parts_ant.append(p.extract_text() or '')
                except Exception:
                    parts_ant.append('')
            out['anteprima_testo'] = '\n'.join(parts_ant)[:8000]
        except Exception:
            pass
    return out


_BUSTA_AMOUNT_WORD_RE = re.compile(
    r'^-?\s*[0-9]{1,3}(?:\s*[\.\,\u00A0\']\s*[0-9]{3})*\s*[\,\.]\s*[0-9]{2}$'
)


def _busta_normalize_amount_token(raw: str):
    if raw in (None, ''):
        return None
    txt = str(raw).replace('\u00A0', ' ')
    txt = re.sub(r'\s+', '', txt)
    if ',' in txt and '.' in txt:
        txt = txt.replace('.', '').replace(',', '.')
    elif ',' in txt:
        txt = txt.replace(',', '.')
    try:
        return Decimal(txt).quantize(Decimal('0.01'))
    except Exception:
        return None


def _busta_find_value_right_of_label(words, label_text, y_tol=5):
    """
    Importo sulla stessa riga (stessa banda verticale) a destra dell'etichetta.
    Utile per cedolini con etichetta e importo allineati in riga.
    """
    label_parts = label_text.upper().split()
    if not label_parts:
        return None
    n = len(words)
    for i, w in enumerate(words):
        if w.get('text', '').upper() != label_parts[0]:
            continue
        ok = True
        for j, part in enumerate(label_parts[1:], 1):
            if i + j >= n or words[i + j].get('text', '').upper() != part:
                ok = False
                break
        if not ok:
            continue
        last_w = words[i + len(label_parts) - 1]
        label_x1 = float(last_w.get('x1', 0))
        ref_top = float(last_w.get('top', 0))
        candidates = []
        for cw in words:
            token = cw.get('text', '')
            if not _BUSTA_AMOUNT_WORD_RE.match(token):
                continue
            if float(cw.get('x0', 0)) < label_x1 - 2:
                continue
            if abs(float(cw.get('top', 0)) - ref_top) > y_tol:
                continue
            candidates.append(cw)
        if candidates:
            best = min(candidates, key=lambda c: float(c.get('x0', 0)))
            return _busta_normalize_amount_token(best.get('text'))
    return None


def _busta_find_value_below_label(words, label_text, y_gap_max=70, x_tolerance=8, y_min_gap=0, strict_y_max=22):
    """Valore monetario subito sotto un'etichetta multi-parola (layout TeamSystem / simili)."""
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
            if not _BUSTA_AMOUNT_WORD_RE.match(token):
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

        if strict_candidates:
            best = min(
                strict_candidates,
                key=lambda c: (c.get('top', 0), abs(((c.get('x0', 0) + c.get('x1', 0)) / 2) - label_cx)),
            )
            return _busta_normalize_amount_token(best.get('text'))

        if loose_candidates:
            best = min(
                loose_candidates,
                key=lambda c: (c.get('top', 0), abs(((c.get('x0', 0) + c.get('x1', 0)) / 2) - label_cx)),
            )
            return _busta_normalize_amount_token(best.get('text'))

        return None

    return None


def _busta_find_monetary_for_label(words_list, label, y_min_gap=0, **below_kw):
    """Prova importo a destra dell'etichetta (stessa riga), poi sotto (layout colonna)."""
    if not words_list:
        return None
    v = _busta_find_value_right_of_label(words_list, label)
    if v is not None and v != Decimal('0.00'):
        return v
    return _busta_find_value_below_label(words_list, label, y_min_gap=y_min_gap, **below_kw)


def _busta_pdfplumber_first_page_words(doc: Documento):
    """Restituisce lista words prima pagina cedolino, o None."""
    try:
        import pdfplumber
    except Exception:
        return None
    try:
        with doc.file.open('rb') as fh:
            with pdfplumber.open(fh, password=PDF_BUSTE_PASSWORD) as pdf:
                if not pdf.pages:
                    return None
                page = pdf.pages[0]
                return page.extract_words(keep_blank_chars=False) or []
    except Exception:
        return None


def _busta_pdfplumber_per_page_words(doc: Documento):
    """Lista di liste words, una per pagina (per cedolini multipagina)."""
    try:
        import pdfplumber
    except Exception:
        return []
    try:
        with doc.file.open('rb') as fh:
            with pdfplumber.open(fh, password=PDF_BUSTE_PASSWORD) as pdf:
                return [p.extract_words(keep_blank_chars=False) or [] for p in pdf.pages]
    except Exception:
        return []


def _extract_busta_importi_posizionale_pdfplumber(doc: Documento):
    """Estrazione posizionale (TeamSystem): netto e lordo sotto etichetta."""
    words = _busta_pdfplumber_first_page_words(doc)
    if not words:
        return None, None
    lordo = _busta_find_value_below_label(words, 'TOTALE LORDO', y_min_gap=0)
    netto = _busta_find_value_below_label(words, 'NETTO BUSTA', y_min_gap=0)
    return netto, lordo


# Etichette posizionali aggiuntive (stesso layout): (campo_libro_paga, frase_etichetta)
_BUSTA_LIBRO_PAGA_POS_LABELS = [
    ('irpef', 'IRPEF'),
    ('irpef', 'RITENUTA IRPEF'),
    ('irpef', 'RITENUTE IRPEF'),
    ('irpef', 'RITENUTA SU IRPEF'),
    ('irpef', 'RITENUTE DA IRPEF'),
    ('irpef', 'IMPOSTA SUL REDDITO'),
    ('inps_dipendente', 'CONTRIBUTI DIPENDENTE'),
    ('inps_dipendente', 'TOTALE CONTRIBUTI'),
    ('inps_dipendente', 'CONTRIBUTI PREVIDENZIALI'),
    ('inps_dipendente', 'VERSAMENTO INPS'),
    ('inps_dipendente', 'CONTRIBUTI INPS'),
    ('inps_dipendente', 'INPS DIPENDENTE'),
    ('inps_dipendente', 'INPS LAVORATORE'),
    ('inps_dipendente', 'ONERI DIPENDENTE'),
    ('inps_dipendente', 'CUNEO FISCALE'),
    ('inail_azienda', 'INAIL'),
    ('inail_azienda', 'PREMIO INAIL'),
    ('tfr_mensile', 'TFR ACCANTONATO'),
    ('tfr_mensile', 'ACCANTONAMENTO TFR'),
    ('tfr_mensile', 'QUOTA TFR'),
    ('tfr_mensile', 'TFR MENSILE'),
    ('tfr_mensile', 'ACCANTONAMENTO T.F.R'),
    ('costo_azienda', 'TOTALE COSTO'),
    ('costo_azienda', 'COSTO AZIENDA'),
    ('costo_azienda', 'COSTO COMPLESSIVO'),
    ('costo_azienda', 'COSTO DEL LAVORO'),
    ('costo_azienda', 'COSTO TOTALE'),
    ('inps_azienda', 'CONTRIBUTI AZIENDA'),
    ('inps_azienda', 'ONERI SOCIALI AZIENDA'),
    ('inps_azienda', 'INPS AZIENDA'),
    ('inps_azienda', 'INPS DATORE'),
    ('inps_azienda', 'ONERI AZIENDALI'),
    ('trattamento_integrativo', 'TRATTAMENTO INTEGRATIVO'),
    ('trattamento_integrativo', 'BONUS RENZI'),
    ('trattamento_integrativo', 'EX LEGGE 21'),
    ('addizionali', 'ADDIZIONALI REGIONALI'),
    ('addizionali', 'ADDIZIONALI COMUNALI'),
    ('addizionali', 'ADDIZIONALI REG'),
    ('addizionali', 'ADDIZIONALI REG E COM'),
    ('addizionali', 'ADDIZIONALE REGIONALE'),
    ('addizionali', 'ADDIZIONALE COMUNALE'),
    ('altre_trattenute', 'ALTRE TRATTENUTE'),
    ('altre_trattenute', 'ALTRE RITENUTE'),
    ('altre_trattenute', 'ALTRE RITENUTE SU'),
    ('retribuzione_base', 'PAGA BASE'),
    ('retribuzione_base', 'RETRIBUZIONE BASE'),
    ('retribuzione_base', 'MINIMALE'),
    ('retribuzione_base', 'RETRIBUZIONE ORARIA'),
    ('retribuzione_base', 'MINIMO TABELLARE'),
    ('retribuzione_base', 'SCATTO ANZIANITA'),
    ('indennita_accessorie', 'INDENNITA ACCESSORIE'),
    ('indennita_accessorie', 'TOTALE ACCESSORI'),
    ('indennita_accessorie', 'ACCESSORI'),
    ('indennita_accessorie', 'INDENNITA'),
    ('indennita_accessorie', 'TOTALE INDENNITA'),
    ('rateo_13', 'RATEO TREDICESIMA'),
    ('rateo_13', 'RATEO 13'),
    ('rateo_13', 'TREDICESIMA'),
    ('rateo_14', 'RATEO QUATTORDICESIMA'),
    ('rateo_14', 'RATEO 14'),
    ('rateo_14', 'QUATTORDICESIMA'),
    ('ore_ordinarie', 'ORE ORDINARIE'),
    ('ore_ordinarie', 'ORE LAVORATE'),
    ('ore_ordinarie', 'ORE EFFETTIVE'),
    ('ore_straordinario', 'ORE STRAORDINARIO'),
    ('ore_straordinario', 'ORE STRAORDINARI'),
    ('ore_straordinario', 'STRAORDINARI'),
    ('ore_straordinario', 'ORE S.O.'),
    ('ore_assenza', 'ORE ASSENZA'),
    ('ore_assenza', 'ASSENZE RETRIBUITE'),
    ('ore_assenza', 'ORE ASSENZE'),
    ('ore_assenza', 'FERIE'),
]

_BUSTA_LIBRO_PAGA_POS_NETTO_LORDO = [
    ('importo', 'NETTO BUSTA'),
    ('importo', 'NETTO DA PAGARE'),
    ('importo', 'NETTO CORRISPOSTO'),
    ('importo', 'NETTO A DISPOSIZIONE'),
    ('importo', 'NETTO IN BUSTA'),
    ('importo', 'NETTO EROGATO'),
    ('importo', 'IMPORTO NETTO'),
    ('importo', 'TOTALE NETTO'),
    ('importo', 'NETTO MENSILE'),
    ('lordo_mensile', 'TOTALE LORDO'),
    ('lordo_mensile', 'TOT. LORDO'),
    ('lordo_mensile', 'LORDO IMPONIBILE'),
    ('lordo_mensile', 'RETRIBUZIONE LORDA'),
    ('lordo_mensile', 'TOTALE COMPENSI'),
    ('lordo_mensile', 'TOT COMPENSI'),
    ('lordo_mensile', 'COMPENSI LORDI'),
    ('lordo_mensile', 'TOTALE RETRIBUZIONE'),
    ('lordo_mensile', 'RAL'),
]


# Pattern testuali (fallback PyPDF) per campi libro paga
_BUSTA_LIBRO_PAGA_TEXT_PATTERNS = {
    'importo': [
        r'NETTO\s+BUSTA', r'NETTO\s+DA\s+PAGARE', r'NETTO\s+CORRISPOSTO',
        r'NETTO\s+A\s+DISPOSIZIONE', r'IMPORTO\s+NETTO', r'TOTALE\s+NETTO',
        r'NETTO\s+EROGATO', r'NETTO\s+IN\s+BUSTA',
    ],
    'lordo_mensile': [
        r'TOTALE\s+LORDO', r'TOT\.?\s*LORDO', r'RETRIBUZIONE\s+LORDA',
        r'TOTALE\s+COMPENSI', r'COMPENSI\s+LORDI', r'TOTALE\s+RETRIBUZIONE',
        r'\bRAL\b',
    ],
    'inps_dipendente': [
        r'INPS\s+(?:DIPENDENTE|FPL|DAL\s+LAVORO|LAVORATORE|LAV\.?)',
        r'CONTRIBUTI\s+(?:PREVIDENZIALI\s+)?DIPENDENTE',
        r'CONTRIBUTI\s+SOCIALI',
        r'TOTALE\s+CONTRIBUTI\s+DIPENDENTE',
        r'ONERI\s+DIPENDENTE',
        r'VERSAMENTO\s+INPS',
        r'CASSA\s+EDILE',
        r'CONTRIBUTI\s+INPS',
        r'CUNEO\s+FISCALE',
    ],
    'irpef': [
        r'\bIRPEF\b',
        r'RITENUTA\s+(?:IRPEF|D[\']?ACCONTO|SU\s+IRPEF)',
        r'RITENUTE\s+IRPEF',
        r'IMPOSTA\s+SUL\s+REDDITO',
    ],
    'addizionali': [
        r'ADDIZIONALI\s+(?:REGIONALI|COMUNALI|REG\.?\s*E\s*COM\.)',
        r'ADDIZIONALE\s+REGIONALE', r'ADDIZIONALE\s+COMUNALE',
    ],
    'altre_trattenute': [
        r'ALTRE\s+TRATTENUTE', r'ALTRE\s+RITENUTE',
    ],
    'trattamento_integrativo': [
        r'TRATTAMENTO\s+INTEGRATIVO', r'EX\s+LEGGE\s+21', r'EX\s+L\.\s*21',
    ],
    'tfr_mensile': [
        r'TFR\s+ACCANTONATO',
        r'ACCANTONAMENTO\s+TFR',
        r'TFR\s+MENSILE',
    ],
    'inps_azienda': [
        r'INPS\s+AZIENDA', r'CONTRIBUTI\s+AZIENDA', r'ONERI\s+SOCIALI\s+AZIENDA',
    ],
    'inail_azienda': [
        r'\bINAIL\b',
    ],
    'costo_azienda': [
        r'TOTALE\s+COSTO', r'COSTO\s+TOTALE\s+AZIENDA', r'COSTO\s+AZIENDA',
    ],
    'retribuzione_base': [
        r'PAGA\s+BASE', r'RETRIBUZIONE\s+BASE', r'RETRIBUZIONE\s+ORARIA',
    ],
    'indennita_accessorie': [
        r'INDENNIT[AÀ]', r'ACCESSORI',
    ],
    'rateo_13': [
        r'RATEO\s+13', r'TREDICESIMA',
    ],
    'rateo_14': [
        r'RATEO\s+14', r'QUATTORDICESIMA', r'RATEO\s+QUATTORD',
    ],
    'ore_assenza': [
        r'ORE\s+ASSENZ', r'ASSENZE\s+RET', r'FERIE\s+EX\s+ART', r'TOT\.?\s*ASSENZ',
    ],
}

# Chiavi dict restituito da estrai_busta_dettaglio_libro_paga_da_pdf (allineate a LibroPagaStorico)
LIBRO_PAGA_BUSTA_ESTRAZIONE_CAMPI = (
    'importo', 'lordo_mensile', 'inps_dipendente', 'irpef', 'addizionali', 'altre_trattenute',
    'trattamento_integrativo', 'retribuzione_base', 'indennita_accessorie',
    'inps_azienda', 'inail_azienda', 'costo_azienda', 'tfr_mensile', 'rateo_13', 'rateo_14',
    'ore_ordinarie', 'ore_straordinario', 'ore_assenza',
)


def estrai_busta_dettaglio_libro_paga_da_pdf(doc: Documento) -> dict:
    """
    Estrae da PDF busta paga i campi utili al Libro Unico (best-effort, layout italiani / TeamSystem).
    Restituisce dict con chiavi allineate a LibroPagaStorico (solo chiavi valorizzate o tutte None).
    """
    out = {k: None for k in LIBRO_PAGA_BUSTA_ESTRAZIONE_CAMPI}

    if not getattr(doc, 'file', None):
        return out

    def _fill_positional(words_list):
        if not words_list:
            return
        for loosen in (False, True):
            kw = {}
            if loosen:
                kw = dict(strict_y_max=34, y_gap_max=95, x_tolerance=14)
            for field, label in _BUSTA_LIBRO_PAGA_POS_NETTO_LORDO:
                if out.get(field) is not None:
                    continue
                val = _busta_find_monetary_for_label(words_list, label, y_min_gap=0, **kw)
                if val is not None and val != Decimal('0.00'):
                    out[field] = val
            for field, label in _BUSTA_LIBRO_PAGA_POS_LABELS:
                if out.get(field) is not None:
                    continue
                val = _busta_find_monetary_for_label(words_list, label, y_min_gap=0, **kw)
                if val is None or val == Decimal('0.00'):
                    continue
                out[field] = val

    pages_words = _busta_pdfplumber_per_page_words(doc)
    if pages_words:
        for pw in pages_words:
            _fill_positional(pw)
    else:
        _fill_positional(_busta_pdfplumber_first_page_words(doc) or [])

    def _is_usable_amount(v):
        return v is not None and v != Decimal('0.00')

    full_text = _busta_pdf_full_text_pypdf(doc)

    if full_text:
        for field, patterns in _BUSTA_LIBRO_PAGA_TEXT_PATTERNS.items():
            if _is_usable_amount(out.get(field)):
                continue
            for pat in patterns:
                val = _extract_amount_by_labels_cedolino(full_text, [pat])
                if not _is_usable_amount(val):
                    val = _extract_amount_by_labels(full_text, [pat])
                if _is_usable_amount(val):
                    out[field] = val
                    break

        for field in ('ore_ordinarie', 'ore_straordinario', 'ore_assenza'):
            if out[field] is not None:
                continue
            if field == 'ore_ordinarie':
                pats = [r'ORE\s+ORDINARIE', r'ORE\s+LAVORATE', r'TOTALE\s+ORE\s+ORD', r'ORE\s+EFFETTIVE']
            elif field == 'ore_straordinario':
                pats = [
                    r'ORE\s+STRAORDINAR', r'STRAORDINARI', r'ORE\s+STR\.?', r'STR\.?\s*ORD\.?',
                    r'EXTRA\s+ORD', r'SUPPLEMENTARI', r'S\.?\s*O\.?\s*ORD',
                ]
            else:
                pats = [r'ORE\s+ASSENZ', r'ASSENZE\s+RET', r'TOT\.?\s*ORE\s+ASS']
            val = _extract_amount_by_labels_cedolino(full_text, pats)
            if val is None:
                val = _extract_amount_by_labels(full_text, pats)
            if val is not None:
                out[field] = val

    return out


def _extract_busta_importi_da_pdf_legacy(doc: Documento):
    """
    Fallback se la pipeline canonica (:func:`acquisisci_busta_da_documento`) non restituisce netto/lordo.
    Mantiene l’euristica libro paga + etichette + PyPDF usata in passato.
    """
    if not getattr(doc, "file", None):
        return None, None

    det = estrai_busta_dettaglio_libro_paga_da_pdf(doc)
    netto = det.get("importo")
    lordo = det.get("lordo_mensile")

    def _usable(v):
        return v is not None and v != Decimal("0.00")

    nu = netto if _usable(netto) else None
    lu = lordo if _usable(lordo) else None
    if nu is not None or lu is not None:
        return nu, lu

    netto_pos, lordo_pos = _extract_busta_importi_posizionale_pdfplumber(doc)
    nup = netto_pos if _usable(netto_pos) else None
    lup = lordo_pos if _usable(lordo_pos) else None
    if nup is not None or lup is not None:
        return nup, lup

    try:
        from pypdf import PdfReader
    except Exception:
        return None, None

    try:
        with doc.file.open("rb") as fh:
            reader = PdfReader(fh)
            if getattr(reader, "is_encrypted", False):
                try:
                    reader.decrypt(PDF_BUSTE_PASSWORD)
                except Exception:
                    return None, None
            pages_text = []
            for p in reader.pages:
                try:
                    pages_text.append(p.extract_text() or "")
                except Exception:
                    pages_text.append("")
            text = "\n".join(pages_text)
    except Exception:
        return None, None

    if not text:
        return None, None

    netto = _extract_amount_by_labels(
        text,
        [
            r"NETTO\s+BUSTA",
            r"NETTO\s+DA\s+PAGARE",
            r"NETTO\s+CORRISPOSTO",
        ],
    )
    lordo = _extract_amount_by_labels(
        text,
        [
            r"TOTALE\s+LORDO",
            r"TOT\.?\s*LORDO",
            r"RETRIBUZIONE\s+LORDA",
        ],
    )

    return netto, lordo


def _extract_busta_importi_da_pdf(doc: Documento):
    """Netto/lordo allineati alla stessa acquisizione usata per lettura cedolino e memorizzazione v4."""
    if not getattr(doc, "file", None):
        return None, None
    res = acquisisci_busta_da_documento(doc)
    if res.errore is None and (res.netto is not None or res.lordo is not None):
        return res.netto, res.lordo
    return _extract_busta_importi_da_pdf_legacy(doc)


def _netto_lordo_e_periodo_busta_da_documento(
    doc: Documento,
) -> tuple[Decimal | None, Decimal | None, int | None, int | None]:
    """
    Netto/lordo come :func:`_extract_busta_importi_da_pdf`; mese/anno con
    :func:`periodo_retributivo_effettivo` (PDF / «Mese Retribuito» prima della descrizione file).
    """
    if not getattr(doc, "file", None):
        return None, None, None, None
    res = acquisisci_busta_da_documento(doc)
    report_for_period = None if res.errore else res.report
    netto_pdf, lordo_pdf = res.netto, res.lordo
    if res.errore is not None or (netto_pdf is None and lordo_pdf is None):
        netto_pdf, lordo_pdf = _extract_busta_importi_da_pdf_legacy(doc)
    mese, anno = periodo_retributivo_effettivo(doc, report_for_period)
    return netto_pdf, lordo_pdf, mese, anno


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


def _dipendente_portale_puo_vedere_documento(dip, documento: Documento) -> bool:
    """True se il dipendente/candidato collegato può aprire il documento assegnato a lui."""
    if not dip or not documento or documento.dipendente_id != dip.id:
        return False
    if documento.visibile_al_dipendente:
        return True
    # Buste in archivio (caricate da HR / import) restano accessibili se assegnate al dipendente,
    # anche quando il flag visibilità non è stato impostato in admin.
    return documento.tipo == 'busta_paga'


def _assert_documento_accesso(
    request, documento: Documento, *, mark_visualizzato_da_azienda: bool = True
):
    """Valida accesso al documento per ruolo utente.

    Se ``mark_visualizzato_da_azienda`` è True (default), admin/HR che aprono un documento
    caricato dal dipendente aggiornano ``visualizzato_da_azienda`` (es. anteprima singola).
    Impostare False per letture massive (es. elenco voci su molte buste) per non alterare lo stato.
    """
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
        azienda_scope = _azienda_scope_for_user(request.user, request)
        if not azienda_scope or documento.azienda_id != azienda_scope.id:
            return HttpResponseForbidden("Accesso negato.")
        if (
            mark_visualizzato_da_azienda
            and documento.caricato_dal_dipendente
            and not documento.visualizzato_da_azienda
        ):
            documento.visualizzato_da_azienda = True
            documento.save(update_fields=['visualizzato_da_azienda'])
    # Dipendente: propri documenti con visibilità, oppure buste paga assegnate in archivio
    elif ruolo == 'dipendente':
        dip = get_dipendente_collegato(request.user)
        if not _dipendente_portale_puo_vedere_documento(dip, documento):
            return HttpResponseForbidden("Accesso negato.")
    # Candidato: stessa regola del dipendente sul documento collegato
    elif ruolo == 'candidato':
        dip = get_dipendente_collegato(request.user)
        if not _dipendente_portale_puo_vedere_documento(dip, documento):
            return HttpResponseForbidden("Accesso negato.")
    else:
        return HttpResponseForbidden("Accesso negato.")

    return None


def _anni_disponibili_buste_paga(azienda) -> list[int]:
    """Anni con buste effettive: solo documenti in cartelle busta ammesse e file presente su storage."""
    if not azienda:
        return [timezone.now().year]
    from documenti.upload_paths import busta_paga_storage_q

    valid_ids: list[int] = []
    for d in (
        Documento.objects.filter(azienda_id=azienda.id, tipo="busta_paga")
        .filter(busta_paga_storage_q())
        .only("id", "file")
        .iterator(chunk_size=400)
    ):
        if _documento_file_disponibile(d):
            valid_ids.append(d.id)
    if not valid_ids:
        return [timezone.now().year]
    y_mov = set(
        MovimentoImportPaghe.objects.filter(
            azienda_id=azienda.id,
            tipo="BUSTA",
            documento_id__in=valid_ids,
        )
        .values_list("anno", flat=True)
        .distinct()
    )
    y_doc = set(
        Documento.objects.filter(
            id__in=valid_ids,
            data_caricamento__isnull=False,
        )
        .values_list("data_caricamento__year", flat=True)
        .distinct()
    )
    merged: set[int] = set()
    for y in y_mov:
        if y is not None:
            try:
                merged.add(int(y))
            except (TypeError, ValueError):
                pass
    for y in y_doc:
        if y is not None:
            try:
                merged.add(int(y))
            except (TypeError, ValueError):
                pass
    if not merged:
        return [timezone.now().year]
    return sorted(merged, reverse=True)


@login_required
def lista_documenti(request):
    """Lista documenti con ACL: dipendente vede solo se stesso, consulente vede sua azienda, admin vede tutto."""
    if request.method == "POST" and _is_admin_hr_or_consulente(request.user):
        post_action = (request.POST.get("action") or "").strip()
        if post_action == "riallinea_pdf_mancanti_buste":
            azienda_scope = _azienda_scope_for_user(request.user, request)
            if azienda_scope is None and request.user.is_superuser:
                azienda_scope = get_azienda_operativa(request.user, request.session)
            if azienda_scope is None:
                messages.error(request, "Azienda operativa non disponibile per il riallineamento.")
                return redirect("lista_documenti")

            tipo_q = (request.POST.get("tipo") or "busta_paga").strip() or "busta_paga"
            anno_q = (request.POST.get("anno") or "").strip()
            dip_q = (request.POST.get("dipendente") or "").strip()
            categoria_q = (request.POST.get("categoria") or "").strip()
            solo_pdf_q = (request.POST.get("solo_pdf_mancanti") or "").strip()

            docs_qs = Documento.objects.filter(azienda=azienda_scope, tipo="busta_paga")
            mov_qs = MovimentoImportPaghe.objects.filter(azienda=azienda_scope, tipo="BUSTA")
            anno_int = int(anno_q) if anno_q.isdigit() else None
            dip_int = int(dip_q) if dip_q.isdigit() else None
            if dip_int:
                docs_qs = docs_qs.filter(dipendente_id=dip_int)
                mov_qs = mov_qs.filter(dipendente_id=dip_int)
            if anno_int:
                mov_doc_ids = list(mov_qs.filter(anno=anno_int).exclude(documento_id__isnull=True).values_list("documento_id", flat=True))
                docs_qs = docs_qs.filter(Q(id__in=mov_doc_ids) | Q(data_caricamento__year=anno_int))
                mov_qs = mov_qs.filter(anno=anno_int)

            scanned = 0
            healed = 0
            still_missing = 0
            # No .iterator() mentre si fa save() sui Documento: meglio elenco id stabile.
            doc_ids = list(docs_qs.values_list("id", flat=True))
            for doc_id in doc_ids:
                doc = Documento.objects.filter(id=doc_id).only("id", "file").first()
                if not doc:
                    continue
                scanned += 1
                name_before = (doc.file.name or "") if doc.file else ""
                if _documento_file_disponibile(doc):
                    # Prima chiamata: path già valido, oppure path riparato e salvato qui sopra
                    name_after = (doc.file.name or "") if doc.file else ""
                    if name_after != name_before:
                        healed += 1
                    continue
                doc = Documento.objects.filter(id=doc_id).only("id", "file").first() or doc
                if _documento_file_disponibile(doc):
                    healed += 1
                else:
                    still_missing += 1

            mov_orfani = mov_qs.filter(documento__isnull=True).count()
            if still_missing == 0:
                messages.success(
                    request,
                    f"Riallineamento completato: controllati {scanned} documenti busta, "
                    f"riparati {healed}, mancanti residui {still_missing}, movimenti senza documento {mov_orfani}.",
                )
            else:
                messages.warning(
                    request,
                    f"Riallineamento parziale: controllati {scanned} documenti busta, "
                    f"riparati {healed}, mancanti residui {still_missing}, movimenti senza documento {mov_orfani}.",
                )
            _log_archivio_documenti(
                request,
                "archivio_documenti_riallinea_pdf_mancanti_buste",
                f"Riallineamento PDF mancanti buste: scanned={scanned}, healed={healed}, "
                f"still_missing={still_missing}, mov_orfani={mov_orfani}, anno={anno_q or '-'}, dip={dip_q or '-'}",
            )
            q = {
                "tipo": tipo_q,
                "anno": anno_q,
                "dipendente": dip_q,
                "categoria": categoria_q,
                "solo_pdf_mancanti": solo_pdf_q,
            }
            qs = urlencode({k: v for k, v in q.items() if v})
            return redirect(f"{reverse('lista_documenti')}?{qs}" if qs else "lista_documenti")
        if post_action == "aggancia_movimenti_orfani_buste":
            azienda_scope = _azienda_scope_for_user(request.user, request)
            if azienda_scope is None and request.user.is_superuser:
                azienda_scope = get_azienda_operativa(request.user, request.session)
            if azienda_scope is None:
                messages.error(request, "Azienda operativa non disponibile per l'aggancio movimenti orfani.")
                return redirect("lista_documenti")

            tipo_q = (request.POST.get("tipo") or "busta_paga").strip() or "busta_paga"
            anno_q = (request.POST.get("anno") or "").strip()
            dip_q = (request.POST.get("dipendente") or "").strip()
            categoria_q = (request.POST.get("categoria") or "").strip()
            solo_pdf_q = (request.POST.get("solo_pdf_mancanti") or "").strip()
            anno_int = int(anno_q) if anno_q.isdigit() else None
            dip_int = int(dip_q) if dip_q.isdigit() else None

            mov_qs = MovimentoImportPaghe.objects.filter(
                azienda=azienda_scope,
                tipo="BUSTA",
                documento__isnull=True,
            ).select_related("dipendente")
            if anno_int:
                mov_qs = mov_qs.filter(anno=anno_int)
            if dip_int:
                mov_qs = mov_qs.filter(dipendente_id=dip_int)

            linked = 0
            unresolved = 0
            scanned = 0
            for mov in mov_qs.iterator(chunk_size=300):
                scanned += 1
                if not mov.dipendente_id:
                    unresolved += 1
                    continue
                periodo = f"{int(mov.mese):02d}/{int(mov.anno)}"
                candidates = Documento.objects.filter(
                    azienda=azienda_scope,
                    dipendente_id=mov.dipendente_id,
                    tipo="busta_paga",
                )
                # Filtro periodo su descrizione o path file
                candidates = candidates.filter(
                    Q(descrizione__icontains=periodo)
                    | Q(file__icontains=f"_{int(mov.mese):02d}_{int(mov.anno)}_")
                    | Q(file__icontains=f"{int(mov.mese):02d}_{int(mov.anno)}")
                ).order_by("-data_caricamento", "-id")

                natura = (mov.natura_busta or "ORDINARIA").upper()
                chosen = None
                for doc in candidates[:40]:
                    txt = f"{(doc.descrizione or '').lower()} {(doc.file.name or '').lower()}"
                    if natura == "TREDICESIMA" and "tredices" not in txt and "13" not in txt:
                        continue
                    if natura == "QUATTORDICESIMA" and "quattordices" not in txt and "14" not in txt:
                        continue
                    if natura == "ORDINARIA" and ("tredices" in txt or "quattordices" in txt):
                        continue
                    if not _documento_file_disponibile(doc):
                        continue
                    chosen = doc
                    break
                if chosen is None:
                    unresolved += 1
                    continue
                mov.documento = chosen
                mov.periodo_label = mov.periodo_label or periodo
                mov.save(update_fields=["documento", "periodo_label", "updated_at"])
                linked += 1

            lvl = messages.success if unresolved == 0 else messages.warning
            lvl(
                request,
                f"Aggancio movimenti orfani completato: scansionati {scanned}, agganciati {linked}, irrisolti {unresolved}.",
            )
            _log_archivio_documenti(
                request,
                "archivio_documenti_aggancia_movimenti_orfani_buste",
                f"Aggancio movimenti orfani buste: scanned={scanned}, linked={linked}, unresolved={unresolved}, "
                f"anno={anno_q or '-'}, dip={dip_q or '-'}",
            )
            q = {
                "tipo": tipo_q,
                "anno": anno_q,
                "dipendente": dip_q,
                "categoria": categoria_q,
                "solo_pdf_mancanti": solo_pdf_q,
            }
            qs = urlencode({k: v for k, v in q.items() if v})
            return redirect(f"{reverse('lista_documenti')}?{qs}" if qs else "lista_documenti")

    dipendente_obj = None  # Dipendente object per ACL
    
    if request.user.is_superuser or request.user.has_ruolo('admin'):
        azienda_operativa = get_azienda_operativa(request.user, request.session)
        documenti = Documento.objects.filter(azienda=azienda_operativa).select_related(
            'dipendente', 'caricato_da', 'azienda'
        ) if azienda_operativa else Documento.objects.none()
    elif request.user.has_ruolo('hr'):
        documenti = Documento.objects.filter(azienda=request.user.azienda).select_related(
            'dipendente', 'caricato_da', 'azienda'
        )
    elif request.user.has_ruolo('consulente'):
        documenti = Documento.objects.filter(azienda=request.user.azienda).select_related(
            'dipendente', 'caricato_da', 'azienda'
        )
    elif _is_dipendente(request.user):
        dipendente_obj = get_dipendente_collegato(request.user)
        if dipendente_obj:
            documenti = (
                Documento.objects.filter(dipendente=dipendente_obj)
                .filter(Q(visibile_al_dipendente=True) | Q(tipo='busta_paga'))
                .select_related('dipendente', 'caricato_da', 'azienda')
            )
        else:
            documenti = Documento.objects.none()
    else:
        return HttpResponseForbidden("Accesso negato")

    categoria = request.GET.get('categoria', '').strip().lower()
    tipo_vals = [v for v in request.GET.getlist('tipo') if (v or '').strip()]
    tipo_filter = (tipo_vals[-1] if tipo_vals else '').strip()
    anno_filter = request.GET.get('anno', '').strip()
    dipendente_filter = request.GET.get('dipendente', '').strip()
    solo_pdf_mancanti = (request.GET.get('solo_pdf_mancanti', '').strip() in {'1', 'true', 'on'})
    
    # ACL: se il user è un dipendente, forzare il filtro al suo ID
    show_dipendente_filter = True
    dipendente_name_display = None
    if _is_dipendente(request.user) and dipendente_obj:
        dipendente_filter = str(dipendente_obj.id)
        show_dipendente_filter = False  # Nascondere il filtro nel template
        dipendente_name_display = f"{dipendente_obj.cognome} {dipendente_obj.nome}".strip()
    
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
    # - se arrivano più parametri `tipo` nella querystring, preferiamo il tipo coerente
    #   con `categoria` (evita conflitti come ?categoria=f24&tipo=altro&tipo=busta_paga).
    if len(tipo_vals) > 1 and categoria in {'f24', 'buste', 'cud'}:
        tipo_by_categoria = {'f24': 'altro', 'buste': 'busta_paga', 'cud': 'certificato'}
        coerente = tipo_by_categoria.get(categoria)
        if coerente in tipo_vals:
            tipo_filter = coerente

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

    # Per CUD/certificati usare anno semantico dalla descrizione (es. "CUD 2024")
    # con fallback a data_caricamento__year.
    if tipo_filter == 'certificato':
        anni_cert = set()
        cert_vals = documenti.filter(tipo='certificato').values_list('descrizione', 'data_caricamento__year')
        for descrizione, year_loaded in cert_vals:
            year_doc = _extract_anno_from_descrizione(descrizione) or year_loaded
            if year_doc:
                anni_cert.add(year_doc)
        anni_disponibili = sorted(anni_cert, reverse=True)

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
        # Compatibilità su archivi storici:
        # alcuni contratti possono risultare classificati in cartella ``contratti/``
        # con ``tipo`` non perfettamente allineato (es. maiuscole / legacy).
        if tipo_filter == 'contratto':
            documenti = documenti.filter(
                Q(tipo__iexact='contratto')
                | Q(tipo__iexact='contratti')
                | Q(file__startswith='contratti/')
                | Q(file__startswith='documenti/contratti/')
            )
        else:
            documenti = documenti.filter(tipo__iexact=tipo_filter)
    if categoria == 'f24':
        documenti = documenti.filter(descrizione__icontains='F24')
    # Base per filtri UI (es. select dipendente) prima dei vincoli puntuali anno/dipendente.
    documenti_for_filter_options = documenti
    # Per buste/F24/CUD/contratti il filtro anno segue la semantica documento
    # (periodo o anno nel contenuto/descrizione), non il solo anno di upload.
    if anno_filter_int and not (_is_admin_hr_or_consulente(request.user) and tipo_filter in ('busta_paga', 'altro', 'certificato', 'contratto')):
        documenti = documenti.filter(data_caricamento__year=anno_filter_int)

    # Per CUD/certificati filtrare sull'anno del documento (descrizione),
    # non sull'anno di upload.
    if anno_filter_int and tipo_filter == 'certificato':
        cert_ids_anno = []
        for doc_id, descrizione, year_loaded in documenti.filter(tipo='certificato').values_list('id', 'descrizione', 'data_caricamento__year'):
            year_doc = _extract_anno_from_descrizione(descrizione) or year_loaded
            if year_doc == anno_filter_int:
                cert_ids_anno.append(doc_id)
        documenti = documenti.filter(id__in=cert_ids_anno)

    # Per i contratti usare anno da descrizione/percorso file (es. numero contratto con /2026)
    # con fallback prudente all'anno upload.
    contratti_senza_anno_esplicito_inclusi = 0
    if anno_filter_int and tipo_filter == 'contratto':
        contr_ids_anno = []
        for doc_id, descrizione, file_name, year_loaded in documenti.values_list('id', 'descrizione', 'file', 'data_caricamento__year'):
            year_doc = (
                _extract_anno_from_descrizione(descrizione)
                or _extract_anno_from_descrizione(file_name)
            )
            # Se il contratto non espone un anno esplicito in descrizione/path, non escluderlo:
            # molti archivi legacy hanno naming neutro.
            if year_doc is None or year_doc == anno_filter_int:
                contr_ids_anno.append(doc_id)
                if year_doc is None:
                    contratti_senza_anno_esplicito_inclusi += 1
        documenti = documenti.filter(id__in=contr_ids_anno)

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
        doc_dip_ids = list(
            documenti_for_filter_options
            .exclude(dipendente__isnull=True)
            .values_list('dipendente_id', flat=True)
            .distinct()
        )
        if doc_dip_ids:
            dipendenti_filtri = list(Dipendente.objects.filter(id__in=doc_dip_ids).order_by('cognome', 'nome'))
        else:
            # Fallback UX: se i documenti filtrati non hanno dipendenti associati,
            # mantenere comunque la select popolata dall'azienda operativa.
            azienda_scope = _azienda_scope_for_user(request.user, request)
            if azienda_scope is None and request.user.is_superuser:
                azienda_scope = get_azienda_operativa(request.user, request.session)
            if azienda_scope is not None:
                dipendenti_filtri = list(
                    Dipendente.objects.filter(azienda=azienda_scope).order_by('cognome', 'nome')
                )

    if dipendente_filter_int:
        documenti = documenti.filter(dipendente_id=dipendente_filter_int)

    # Default: lista vuota se non è stato applicato alcun filtro esplicito
    if _is_admin_hr_or_consulente(request.user):
        has_explicit_filter = bool(categoria or tipo_filter or anno_filter_int or dipendente_filter_int)
        if not has_explicit_filter:
            documenti = documenti.none()

    show_buste_dashboard = False
    show_f24_dashboard = False
    show_cud_dashboard = False
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
    buste_missing_file_count = 0
    buste_missing_file_unique_count = 0
    buste_movimenti_senza_documento_count = 0
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
    cud_anni = []
    cud_num_documenti = 0
    cud_num_dipendenti = 0

    if _is_admin_hr_or_consulente(request.user) and tipo_filter == 'busta_paga':
        show_buste_dashboard = True
        # Uniforma il contenitore/filtri alla resa usata da `tipo=altro`.
        show_f24_dashboard = True
        buste_show_f24_details = not bool(dipendente_filter_int)

        azienda_rif = None
        if request.user.is_superuser or request.user.has_ruolo('admin'):
            azienda_rif = get_azienda_operativa(request.user, request.session)
        elif request.user.has_ruolo('hr'):
            azienda_rif = request.user.azienda
        elif request.user.has_ruolo('consulente'):
            azienda_rif = request.user.azienda

        # Stesso ambito dell'admin Documento: tutte le buste_paga dell'azienda (filtro ACL già su ``documenti``),
        # senza escludere path storage legacy o record senza dipendente.
        buste_qs = documenti.filter(tipo='busta_paga').select_related('dipendente', 'caricato_da')
        # Mostra anche i record senza file fisico disponibile: devono restare
        # visibili in dashboard per consentire diagnosi/ripristino.
        buste_docs = list(buste_qs)
        buste_doc_ids = {d.id for d in buste_docs}

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
        elif azienda_rif:
            movimenti_f24_qs = MovimentoImportPaghe.objects.filter(
                azienda=azienda_rif,
                tipo='F24',
            ).select_related('documento')
            if anno_filter_int:
                movimenti_f24_qs = movimenti_f24_qs.filter(anno=anno_filter_int)

        mov_by_doc = {m.documento_id: m for m in movimenti_qs if m.documento_id}
        mov_by_key = {(m.dipendente_id, m.mese, m.anno): m for m in movimenti_qs if m.dipendente_id and m.mese and m.anno}

        def _busta_dashboard_doc_sort_key(d):
            mm, yy = parse_periodo_busta_con_pdf(d, mov_by_doc.get(d.id))
            ym = (yy or 0) * 100 + (mm or 0)
            ts = d.data_caricamento.timestamp() if d.data_caricamento else 0.0
            desc = (d.descrizione or '').strip()
            return (ym, ts, desc, d.id or 0)

        buste_docs.sort(key=_busta_dashboard_doc_sort_key, reverse=True)

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

        # Anni/dipendenti da movimenti importati (anche se senza PDF agganciato),
        # con fallback sui documenti quando non ci sono movimenti.
        if azienda_rif:
            anni_disp = sorted(
                MovimentoImportPaghe.objects.filter(
                    azienda=azienda_rif,
                    tipo='BUSTA',
                )
                .order_by('anno')
                .values_list('anno', flat=True)
                .distinct(),
                reverse=True,
            )
        else:
            anni_disp = []
        if not anni_disp:
            anni_set = set()
            for _bd in buste_docs:
                _, _yy = parse_periodo_busta_con_pdf(_bd, mov_by_doc.get(_bd.id))
                if _yy and _yy > 0:
                    anni_set.add(_yy)
            anni_disp = sorted(anni_set, reverse=True)
        _mov_dip_qs = (
            MovimentoImportPaghe.objects.filter(
                azienda=azienda_rif,
                tipo='BUSTA',
            )
            .exclude(dipendente__isnull=True)
            if azienda_rif
            else MovimentoImportPaghe.objects.none()
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

            # Mostra anche i documenti senza movimento canonico: nei casi di
            # import parziale/ambiguo l'utente deve comunque vedere la busta.
            if mov and mov.anno:
                mese, anno = mov.mese, mov.anno
            else:
                mese, anno = parse_periodo_busta_con_pdf(doc, mov)
                if not anno and doc.data_caricamento:
                    anno = doc.data_caricamento.year
                if not mese and doc.data_caricamento:
                    mese = doc.data_caricamento.month
                if not anno:
                    if anno_filter_int:
                        continue
                    # Resta in dashboard (bucket «periodo non determinato») come in archivio / portale
                    anno = doc.data_caricamento.year if doc.data_caricamento else timezone.now().year
                    mese = mese or 0
            if mese is None:
                mese = 0

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
                            natura_busta='ORDINARIA',
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

            dip_key = doc.dipendente_id if doc.dipendente_id is not None else -int(doc.id or 0)
            d = m['dip_map'][dip_key]
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
                'file_available': _documento_file_disponibile(doc),
            })

        # Include anche i movimenti busta senza PDF collegato:
        # in questo modo il periodo importato resta visibile in dashboard.
        for mov in movimenti_qs:
            if not (mov and mov.dipendente_id and mov.anno and mov.mese):
                continue
            if mov.documento_id and mov.documento_id in buste_doc_ids:
                continue

            anno = mov.anno
            mese = mov.mese
            if anno_filter_int and anno != anno_filter_int:
                continue

            netto = mov.importo_netto if mov.importo_netto is not None else mov.importo
            lordo = mov.importo_lordo if mov.importo_lordo is not None else lordo_by_dip.get(mov.dipendente_id)

            y = grouped[anno]
            y['anno'] = anno
            mese_key = mese if mese else 0
            m = y['month_map'][mese_key]
            m['mese'] = mese_key
            m['mese_nome'] = MESI_NUM_TO_NAME.get(mese_key, 'Mese non indicato')

            d = m['dip_map'][mov.dipendente_id]
            d['dipendente'] = mov.dipendente

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
                'documento': None,
                'mese': mese,
                'anno': anno,
                'movimento': mov,
                'lordo': lordo,
                'netto': netto,
                'file_available': False,
            })

        for yy in sorted(grouped.keys(), reverse=True):
            item = grouped[yy]
            mesi = []
            y_tot_f24 = Decimal('0.00')
            y_has_f24 = False
            for mk in sorted(item['month_map'].keys(), reverse=True):
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
                    if solo_pdf_mancanti:
                        selected_rows = [rr for rr in ditem.get('rows', []) if not rr.get('file_available')]
                        if not selected_rows:
                            continue
                        ditem['rows'] = selected_rows
                        d_tot_lordo = Decimal('0.00')
                        d_tot_netto = Decimal('0.00')
                        d_has_lordo = False
                        d_has_netto = False
                        for rr in selected_rows:
                            if rr.get('lordo') is not None:
                                d_tot_lordo += (rr.get('lordo') or Decimal('0.00'))
                                d_has_lordo = True
                            if rr.get('netto') is not None:
                                d_tot_netto += (rr.get('netto') or Decimal('0.00'))
                                d_has_netto = True
                        ditem['tot_lordo'] = d_tot_lordo
                        ditem['tot_netto'] = d_tot_netto
                        ditem['has_lordo'] = d_has_lordo
                        ditem['has_netto'] = d_has_netto

                    def _row_sort_key(r):
                        doc = r.get('documento')
                        mov = r.get('movimento')
                        if doc:
                            mm, yy = parse_periodo_busta_con_pdf(doc, mov)
                            desc = (doc.descrizione or '').strip()
                            dt = getattr(doc, 'data_caricamento', None)
                            tid = doc.id or 0
                        elif mov:
                            mm, yy = int(mov.mese or 0), int(mov.anno or 0)
                            if not (1 <= mm <= 12 and yy >= 1990):
                                mm, yy = 0, 0
                            pl = (mov.periodo_label or '').strip()
                            desc = pl or (
                                f'{mov.mese:02d}/{mov.anno}'
                                if mov.mese and mov.anno
                                else ''
                            )
                            dt = mov.updated_at or mov.created_at
                            tid = mov.id or 0
                        else:
                            mm = yy = 0
                            desc = ''
                            dt = None
                            tid = 0
                        ym = (yy or 0) * 100 + (mm or 0)
                        ts = dt.timestamp() if dt else 0.0
                        return (ym, ts, desc, tid)

                    ditem['rows'].sort(
                        key=_row_sort_key,
                        reverse=True,
                    )
                    # Riga primaria: preferire la busta ordinaria (se presente),
                    # altrimenti usare la più recente.
                    primary_row = None
                    for rr in ditem.get('rows', []):
                        mov = rr.get('movimento')
                        natura = (getattr(mov, 'natura_busta', '') or '').upper() if mov else ''
                        if natura == 'ORDINARIA':
                            primary_row = rr
                            break
                    if primary_row is None and ditem.get('rows'):
                        primary_row = ditem['rows'][0]
                    ditem['primary_row'] = primary_row

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

        # Allineato all'archivio Documento (stesso queryset della dashboard), non al n. di righe
        # accordion (che può duplicare 13ª/14ª/ordinaria o escludere prima del fix periodo).
        buste_num_documenti = len(buste_docs)
        buste_missing_file_count = sum(
            1
            for y in buste_anni
            for m in y.get('mesi', [])
            for d in m.get('dipendenti', [])
            for r in d.get('rows', [])
            if r.get('documento') is not None and not r.get('file_available')
        )
        _missing_doc_ids: set[int] = set()
        for y in buste_anni:
            for m in y.get("mesi", []):
                for d in m.get("dipendenti", []):
                    for r in d.get("rows", []):
                        doc = r.get("documento")
                        if doc is not None and not r.get("file_available") and getattr(doc, "id", None):
                            _missing_doc_ids.add(int(doc.id))
        buste_missing_file_unique_count = len(_missing_doc_ids)
        buste_movimenti_senza_documento_count = sum(
            1
            for y in buste_anni
            for m in y.get('mesi', [])
            for d in m.get('dipendenti', [])
            for r in d.get('rows', [])
            if r.get('documento') is None
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
            if mov and mov.anno:
                mese, anno = mov.mese, mov.anno
            else:
                mese, anno = _parse_periodo_busta(doc)
                if not anno and doc.data_caricamento:
                    anno = doc.data_caricamento.year
                if not mese and doc.data_caricamento:
                    mese = doc.data_caricamento.month
                if not anno:
                    continue
            if anno_filter_int and anno != anno_filter_int:
                continue

            y = grouped_f24[anno]
            y['anno'] = anno
            mese_key = mese if mese else 0
            m = y['month_map'][mese_key]
            m['mese'] = mese_key
            m['mese_nome'] = MESI_NUM_TO_NAME.get(mese_key, 'Mese non indicato')

            importo = None
            debito = None
            credito = None
            saldo = None
            if mov:
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
                    if mov:
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
                if debito is None:
                    debito = deb_pdf
                if credito is None:
                    credito = cred_pdf
                if saldo is None:
                    saldo = saldo_pdf
            elif mov:
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

            if mov:
                debito = mov.f24_tot_debito if mov.f24_tot_debito is not None else debito
                credito = mov.f24_tot_credito if mov.f24_tot_credito is not None else credito
                saldo = mov.f24_saldo_finale if mov.f24_saldo_finale is not None else importo
            else:
                saldo = saldo if saldo is not None else importo

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
                'file_available': _documento_file_disponibile(doc),
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
    elif _is_admin_hr_or_consulente(request.user) and tipo_filter == 'certificato':
        show_cud_dashboard = True
        cud_info_cache = {}

        grouped_cud = defaultdict(lambda: {
            'anno': None,
            'dip_map': defaultdict(lambda: {
                'dipendente': None,
                'rows': [],
            }),
        })

        for doc in documenti.filter(tipo='certificato').select_related('dipendente', 'caricato_da').order_by('-data_caricamento'):
            info_cud = cud_info_cache.get(doc.id)
            if info_cud is None:
                info_cud = _extract_cud_anni_documento(doc)
                cud_info_cache[doc.id] = info_cud

            anno_doc = info_cud.get('anno_cu') or (doc.data_caricamento.year if doc.data_caricamento else None)
            if not anno_doc:
                continue
            y = grouped_cud[anno_doc]
            y['anno'] = anno_doc
            dip_key = doc.dipendente_id or 0
            d = y['dip_map'][dip_key]
            d['dipendente'] = doc.dipendente
            d['rows'].append({
                'documento': doc,
                'file_available': _documento_file_disponibile(doc),
                'anno_cu': info_cud.get('anno_cu'),
                'anno_riferimento': info_cud.get('anno_riferimento'),
                'anno_riferimento_stimato': info_cud.get('anno_riferimento_stimato'),
            })

        for yy in sorted(grouped_cud.keys(), reverse=True):
            item = grouped_cud[yy]
            dipendenti = []
            for _, ditem in item['dip_map'].items():
                ditem['rows'].sort(key=lambda r: r['documento'].data_caricamento, reverse=True)
                primary_row = None
                for rr in ditem['rows']:
                    if rr.get('file_available'):
                        primary_row = rr
                        break
                if primary_row is None and ditem['rows']:
                    primary_row = ditem['rows'][0]
                ditem['primary_row'] = primary_row
                dipendenti.append(ditem)
            dipendenti.sort(key=lambda x: (
                (x['dipendente'].cognome if x['dipendente'] else 'ZZZZ'),
                (x['dipendente'].nome if x['dipendente'] else 'ZZZZ'),
            ))
            item['dipendenti'] = dipendenti
            cud_anni.append(item)

        cud_num_documenti = sum(len(d.get('rows', [])) for y in cud_anni for d in y.get('dipendenti', []))
        cud_num_dipendenti = len({
            d.get('dipendente').id
            for y in cud_anni
            for d in y.get('dipendenti', [])
            if d.get('dipendente')
        })
    else:
        buste_anni_disponibili = anni_disponibili
        f24_anni_disponibili = anni_disponibili

    if show_buste_dashboard:
        anni_filtri = buste_anni_disponibili
    elif show_f24_dashboard:
        anni_filtri = f24_anni_disponibili
    else:
        anni_filtri = anni_disponibili

    if not (show_buste_dashboard or show_f24_dashboard or show_cud_dashboard):
        _doc_order = (
            ('-descrizione', '-data_caricamento', '-id')
            if tipo_filter == 'busta_paga'
            else ('-data_caricamento', '-id')
        )
        _doc_paginator = Paginator(documenti.order_by(*_doc_order), 25)
        documenti = _doc_paginator.get_page(request.GET.get('page') or 1)

    return render(request, 'documenti/lista.html', {
        'documenti': documenti,
        'tipo_filter': tipo_filter,
        'categoria': categoria,
        'anno_filter': anno_filter,
        'dipendente_filter': dipendente_filter,
        'show_dipendente_filter': show_dipendente_filter,
        'dipendente_name_display': dipendente_name_display,
        'show_buste_dashboard': show_buste_dashboard,
        'solo_pdf_mancanti': solo_pdf_mancanti,
        'show_f24_dashboard': show_f24_dashboard,
        'show_cud_dashboard': show_cud_dashboard,
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
        'buste_missing_file_count': buste_missing_file_count,
        'buste_missing_file_unique_count': buste_missing_file_unique_count,
        'buste_movimenti_senza_documento_count': buste_movimenti_senza_documento_count,
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
        'cud_anni': cud_anni,
        'cud_num_documenti': cud_num_documenti,
        'cud_num_dipendenti': cud_num_dipendenti,
        'anni_filtri': anni_filtri,
        'anni_disponibili': anni_disponibili,
        'dipendenti_filtri': dipendenti_filtri,
        'is_admin_hr': _is_admin_or_hr(request.user),
        'contratti_senza_anno_esplicito_inclusi': contratti_senza_anno_esplicito_inclusi,
        'is_gestore_documenti': _is_admin_hr_or_consulente(request.user),
        'tipo_choices': [
            (c, 'F24' if c == 'altro' else l)
            for c, l in Documento.TIPO_CHOICES
        ],
    })


@login_required
def lista_buste_paga(request):
    return redirect(f"{reverse('lista_documenti')}?categoria=buste&tipo=busta_paga&anno=&dipendente=")


@login_required
def lista_f24(request):
    return redirect(f"{reverse('lista_documenti')}?tipo=altro&anno=&dipendente=")


@login_required
def lista_cud(request):
    return redirect(f"{reverse('lista_documenti')}?tipo=certificato&anno=&dipendente=")


@login_required
def documenti_dipendente_admin(request, dipendente_id):
    """Admin/HR/consulente: tutti i documenti di un dipendente specifico."""
    if not _is_admin_hr_or_consulente(request.user):
        return HttpResponseForbidden("Accesso riservato.")
    dip = get_object_or_404(Dipendente, id=dipendente_id)
    if not _dipendente_documenti_accessible(request, dip):
        return HttpResponseForbidden("Accesso non autorizzato per questo dipendente.")
    profilo_candidato = (
        ProfiloCandidato.objects.filter(dipendente=dip)
        .select_related('user')
        .first()
    )
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
        'profilo_candidato': profilo_candidato,
    })


@login_required
def upload_documento(request):
    """Admin/HR/consulente caricano documenti aziendali per un dipendente (tutti i tipi previsti dal modello)."""
    if not _is_admin_hr_or_consulente(request.user):
        return HttpResponseForbidden("Solo admin, HR o consulente possono caricare documenti da questa pagina.")
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
            if dip and not _dipendente_documenti_accessible(request, dip):
                messages.error(request, "Dipendente non valido per il tuo profilo o per l'azienda operativa.")
                return redirect('upload_documento')
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
                netto_pdf, lordo_pdf, mese, anno = _netto_lordo_e_periodo_busta_da_documento(doc)
                m_desc, y_desc = _parse_periodo_busta(doc)
                if mese and anno:
                    MovimentoImportPaghe.objects.update_or_create(
                        azienda=azienda,
                        dipendente=dip,
                        tipo='BUSTA',
                        natura_busta='ORDINARIA',
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
                    if (
                        m_desc
                        and y_desc
                        and (m_desc != mese or y_desc != anno)
                    ):
                        messages.info(
                            request,
                            "Movimento paghe registrato sul periodo letto dal PDF "
                            f"({mese:02d}/{anno}), non su quello dedotto dalla descrizione "
                            f"({m_desc:02d}/{y_desc}).",
                        )
            messages.success(request, "Documento caricato con successo.")
            if dip:
                return redirect('documenti_dipendente_admin', dipendente_id=dip.id)
            return redirect('lista_documenti')

    azienda = get_azienda_operativa(request.user, request.session) if request.user.has_ruolo('admin') else getattr(request.user, 'azienda', None)
    dipendenti = Dipendente.objects.filter(azienda=azienda).order_by('cognome', 'nome') if azienda else []
    preselect_dipendente_id = None
    raw_pre = (request.GET.get('dipendente_id') or '').strip()
    if raw_pre.isdigit():
        cand = Dipendente.objects.filter(id=int(raw_pre)).first()
        if cand and _dipendente_documenti_accessible(request, cand):
            preselect_dipendente_id = cand.id
    return render(request, 'documenti/upload.html', {
        'dipendenti': dipendenti,
        'tipo_choices': Documento.TIPO_CHOICES,
        'preselect_dipendente_id': preselect_dipendente_id,
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

        forza_reimport = str(request.POST.get('forza_reimport', '')).lower() in (
            '1',
            'on',
            'true',
            'yes',
        )

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
                prev_kw = dict(
                    azienda_id=azienda.id,
                    source_name=nome,
                    out=str(preview_out),
                    stdout=buff_prev,
                )
                if forza_reimport:
                    prev_kw['allow_replace'] = True
                call_command('preview_import_paghe_pdf', tmp_path, **prev_kw)

                buff_imp = io.StringIO()
                imp_kw = dict(
                    azienda_id=azienda.id,
                    apply=True,
                    attach_docs=True,
                    stdout=buff_imp,
                )
                if forza_reimport:
                    imp_kw['allow_overwrite'] = True
                call_command('import_paghe_pdf', str(preview_out), **imp_kw)

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
                    natura_busta = (row.get('natura_busta') or preview_data.get('natura_busta_file') or 'ORDINARIA').upper()
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
                        qs = MovimentoImportPaghe.objects.filter(
                            azienda=azienda,
                            tipo='BUSTA',
                            anno=anno_r,
                            mese=mese_r,
                            natura_busta=natura_busta,
                        ).select_related('documento', 'dipendente')
                        if dip is not None:
                            mov = qs.filter(dipendente=dip).first()
                        elif row.get('cf'):
                            mov = qs.filter(cf_estratto=row.get('cf')).first()
                            if mov and mov.dipendente_id:
                                dip = mov.dipendente

                    action = row.get('action')
                    anomalia_obj = build_anomalia_import_export(
                        action=action,
                        periodo=periodo,
                        dipendente_presente=(dip is not None),
                        movimento_presente=(mov is not None),
                        netto_raw=row.get('netto_busta'),
                    )
                    registra_evento_anomalia(
                        utente=request.user,
                        azienda=azienda,
                        contesto='import_buste_massivo',
                        anomalia=anomalia_obj,
                        request=request,
                    )

                    natura_label = {
                        'ORDINARIA': 'Ordinaria',
                        'TREDICESIMA': '13ª',
                        'QUATTORDICESIMA': '14ª',
                    }.get(natura_busta, natura_busta)

                    documento_id_safe = None
                    if mov and getattr(mov, 'documento_id', None):
                        doc_mov = getattr(mov, 'documento', None)
                        if doc_mov and getattr(doc_mov, 'file', None):
                            has_name = bool(getattr(doc_mov.file, 'name', None))
                            has_file = False
                            if has_name:
                                try:
                                    has_file = doc_mov.file.storage.exists(doc_mov.file.name)
                                except Exception:
                                    has_file = False
                            if has_file:
                                documento_id_safe = doc_mov.id

                    import_results.append({
                        'filename': nome,
                        'periodo': periodo or '-',
                        'natura_busta': natura_label,
                        'esito': 'scartato' if action == 'already_present' else ('ok' if action != 'ambiguous' and mov else ('errore' if action == 'ambiguous' else 'attenzione')),
                        'messaggio': 'Busta già presente per il periodo' if action == 'already_present' else ('Importato' if mov else ('Match ambiguo' if action == 'ambiguous' else 'Da verificare')),
                        'anomalia': (anomalia_obj or {}).get('messaggio'),
                        'anomalia_codice': (anomalia_obj or {}).get('codice'),
                        'dipendente': dip,
                        'lordo': (getattr(mov, 'importo_lordo', None) if mov else None) or _parse_decimal_text(row.get('lordo_busta')),
                        'netto': (getattr(mov, 'importo_netto', None) if mov else None) or _parse_decimal_text(row.get('netto_busta')),
                        'documento_id': documento_id_safe,
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
            'forza_reimport_applicata': forza_reimport,
        })

    return render(request, 'documenti/upload_buste_massivo.html', {
        'azienda': azienda,
        'risultati': [],
        'import_results': [],
        'forza_reimport_applicata': False,
    })


@login_required
def upload_cud_massivo(request):
    """Motore unico CUD: import da PDF unico (anche protetto) e ricomposizione CUD multi-pagina per dipendente."""
    if not _is_admin_hr_or_consulente(request.user):
        return HttpResponseForbidden("Solo admin, HR o consulente possono caricare CUD massivamente.")

    azienda = get_azienda_operativa(request.user, request.session) if request.user.has_ruolo('admin') else getattr(request.user, 'azienda', None)
    if not azienda:
        messages.error(request, "Nessuna azienda operativa selezionata.")
        return redirect('lista_documenti')

    from pypdf import PdfReader, PdfWriter

    def _parse_anno(raw_value):
        raw = str(raw_value or timezone.now().year)
        normalized = raw.replace('.', '').replace(' ', '').replace(',', '')
        try:
            return int(normalized)
        except (TypeError, ValueError):
            return timezone.now().year

    def _extract_cf_from_text(text: str) -> str | None:
        if not text:
            return None
        m = re.search(r"\b([A-Z]{6}[0-9]{2}[A-Z][0-9]{2}[A-Z][0-9]{3}[A-Z])\b", text.upper())
        return m.group(1) if m else None

    def _extract_anno_cu_from_text(text: str) -> int | None:
        if not text:
            return None
        txt = re.sub(r"\s+", " ", text.upper()).strip()
        for pat in (
            r"\bCERTIFICAZIONE\s+UNICA\s*(20\d{2})\b",
            r"\bCUD\s*(20\d{2})\b",
            r"\bCU\s*(20\d{2})\b",
        ):
            m = re.search(pat, txt)
            if m:
                try:
                    return int(m.group(1))
                except (TypeError, ValueError):
                    pass
        return None

    def _extract_anno_cu_from_filename(filename: str) -> int | None:
        m = re.search(r"\b(20\d{2})\b", (filename or '').upper())
        if not m:
            return None
        try:
            return int(m.group(1))
        except (TypeError, ValueError):
            return None

    def _detect_anno_cu(reader, filename: str) -> int | None:
        # 1) prova dal contenuto (prime pagine)
        pages_to_scan = min(len(reader.pages), 6)
        for i in range(pages_to_scan):
            try:
                txt = reader.pages[i].extract_text() or ''
            except Exception:
                txt = ''
            y = _extract_anno_cu_from_text(txt)
            if y:
                return y
        # 2) fallback dal nome file
        return _extract_anno_cu_from_filename(filename)

    risultati = []
    anno = _parse_anno(request.POST.get('anno') if request.method == 'POST' else request.GET.get('anno'))
    sovrascrivi_esistenti = str(request.POST.get('sovrascrivi', '')).lower() in ('1', 'true', 'on', 'yes')

    if request.method == 'POST':
        uploaded_files = request.FILES.getlist('pdf_files')
        if not uploaded_files:
            messages.warning(request, 'Seleziona almeno un PDF unico CUD.')
            return redirect(f'{request.path}?anno={anno}')

        for idx, up in enumerate(uploaded_files, start=1):
            nome = getattr(up, 'name', f'cud_{idx}.pdf')
            if not nome.lower().endswith('.pdf'):
                risultati.append({'filename': nome, 'esito': 'errore', 'messaggio': 'Formato non supportato (solo PDF).'})
                continue

            tmp_path = None
            try:
                with tempfile.NamedTemporaryFile(delete=False, suffix='.pdf') as tmp:
                    for chunk in up.chunks():
                        tmp.write(chunk)
                    tmp_path = tmp.name

                reader = PdfReader(tmp_path)
                if getattr(reader, 'is_encrypted', False):
                    unlocked = False
                    for pwd in (PDF_BUSTE_PASSWORD, ''):
                        try:
                            if reader.decrypt(pwd):
                                unlocked = True
                                break
                        except Exception:
                            continue
                    if not unlocked:
                        raise ValueError('Impossibile decriptare PDF CUD (password non valida).')

                anno_cu_rilevato = _detect_anno_cu(reader, nome)
                if anno_cu_rilevato is not None and anno_cu_rilevato != anno:
                    an = {
                        'codice': 'ANNO_CUD_NON_COERENTE',
                        'messaggio': f'File {nome}: anno CU rilevato {anno_cu_rilevato}, atteso {anno}',
                    }
                    registra_evento_anomalia(
                        utente=request.user,
                        azienda=azienda,
                        contesto='import_cud_massivo',
                        anomalia=an,
                        request=request,
                    )
                    risultati.append({
                        'filename': nome,
                        'pagina': '-',
                        'cf': None,
                        'dipendente': None,
                        'esito': 'errore',
                        'messaggio': f'Anno CU non coerente: selezionato {anno}, file {anno_cu_rilevato}',
                        'documento_id': None,
                    })
                    continue

                gruppi_cud = []
                gruppo_corrente = None
                pagine_orfane_senza_cf = 0

                for page_index, page in enumerate(reader.pages, start=1):
                    try:
                        text = page.extract_text() or ''
                    except Exception:
                        text = ''

                    cf = _extract_cf_from_text(text)
                    if not cf:
                        if gruppo_corrente is None:
                            pagine_orfane_senza_cf += 1
                        else:
                            gruppo_corrente['pages'].append(page)
                            gruppo_corrente['end_page'] = page_index
                        continue

                    if gruppo_corrente is None:
                        gruppo_corrente = {
                            'cf': cf,
                            'pages': [page],
                            'start_page': page_index,
                            'end_page': page_index,
                        }
                        continue

                    if cf == gruppo_corrente['cf']:
                        gruppo_corrente['pages'].append(page)
                        gruppo_corrente['end_page'] = page_index
                        continue

                    gruppi_cud.append(gruppo_corrente)
                    gruppo_corrente = {
                        'cf': cf,
                        'pages': [page],
                        'start_page': page_index,
                        'end_page': page_index,
                    }

                if gruppo_corrente is not None:
                    gruppi_cud.append(gruppo_corrente)

                seen_cf = set()
                gruppi_duplicati = 0

                for gruppo in gruppi_cud:
                    cf = gruppo['cf']
                    start_page = gruppo['start_page']
                    end_page = gruppo['end_page']

                    if cf in seen_cf:
                        gruppi_duplicati += 1
                        risultati.append({
                            'filename': nome,
                            'pagina': f"{start_page}-{end_page}" if end_page != start_page else start_page,
                            'cf': cf,
                            'dipendente': None,
                            'esito': 'scartato',
                            'messaggio': 'Copia duplicata nello stesso PDF (blocco CUD già importato)',
                            'documento_id': None,
                        })
                        continue
                    seen_cf.add(cf)

                    dip = Dipendente.objects.filter(azienda=azienda, codice_fiscale__iexact=cf).first()
                    if not dip:
                        # Placeholder operativo per completamento manuale anagrafica.
                        try:
                            n_prog = _next_placeholder_punto_zero(azienda)
                            dip = Dipendente.objects.create(
                                azienda=azienda,
                                nome=f'n. {n_prog:02d}',
                                cognome='Punto Zero',
                                codice_fiscale=cf,
                                ruolo='Da valorizzare',
                                stato='candidato',
                            )
                            an = {
                                'codice': 'DIPENDENTE_PLACEHOLDER_CREAT0',
                                'messaggio': f'Creato placeholder Punto Zero n. {n_prog:02d} per CF {cf}',
                            }
                            registra_evento_anomalia(
                                utente=request.user,
                                azienda=azienda,
                                contesto='import_cud_massivo',
                                anomalia=an,
                                request=request,
                            )
                        except Exception:
                            an = {'codice': 'DIPENDENTE_NON_ASSOCIATO', 'messaggio': f'Nessun dipendente trovato per CF {cf}'}
                            registra_evento_anomalia(
                                utente=request.user,
                                azienda=azienda,
                                contesto='import_cud_massivo',
                                anomalia=an,
                                request=request,
                            )
                            risultati.append({
                                'filename': nome,
                                'pagina': f"{start_page}-{end_page}" if end_page != start_page else start_page,
                                'cf': cf,
                                'dipendente': None,
                                'esito': 'attenzione',
                                'messaggio': 'Dipendente non trovato',
                                'documento_id': None,
                            })
                            continue

                    writer = PdfWriter()
                    for p in gruppo['pages']:
                        writer.add_page(p)
                    buf = io.BytesIO()
                    writer.write(buf)
                    pdf_bytes = buf.getvalue()

                    descr = f'CUD {anno}'
                    doc = Documento.objects.filter(
                        azienda=azienda,
                        dipendente=dip,
                        tipo='certificato',
                        descrizione=descr,
                    ).first()

                    if doc is not None and _documento_file_disponibile(doc) and not sovrascrivi_esistenti:
                        risultati.append({
                            'filename': nome,
                            'pagina': f"{start_page}-{end_page}" if end_page != start_page else start_page,
                            'cf': cf,
                            'dipendente': dip,
                            'esito': 'scartato',
                            'messaggio': 'CUD già presente (nessuna sovrascrittura richiesta)',
                            'documento_id': doc.id,
                        })
                        continue

                    filename = f'cud_{anno}_dip_{dip.id}_p{start_page}-{end_page}.pdf'
                    if doc is None:
                        doc = Documento(
                            azienda=azienda,
                            dipendente=dip,
                            tipo='certificato',
                            descrizione=descr,
                            caricato_da=request.user,
                            caricato_dal_dipendente=False,
                            visibile_al_dipendente=True,
                        )
                    else:
                        doc.caricato_da = request.user
                        # Mantiene il path esistente del file quando possibile
                        if getattr(getattr(doc, 'file', None), 'name', None):
                            filename = os.path.basename(doc.file.name)
                    doc.file.save(filename, ContentFile(pdf_bytes), save=False)
                    doc.save()

                    risultati.append({
                        'filename': nome,
                        'pagina': f"{start_page}-{end_page}" if end_page != start_page else start_page,
                        'cf': cf,
                        'dipendente': dip,
                        'esito': 'ok',
                        'messaggio': (
                            f'Importato su placeholder {dip.cognome} {dip.nome} ({len(gruppo["pages"])} pagine)'
                            if dip.cognome == 'Punto Zero' and str(dip.nome).lower().startswith('n.')
                            else f'Importato ({len(gruppo["pages"])} pagine)'
                        ),
                        'documento_id': doc.id,
                    })

                if pagine_orfane_senza_cf:
                    an = {
                        'codice': 'CF_NON_ESTRATTO',
                        'messaggio': f'{pagine_orfane_senza_cf} pagine senza CF fuori da blocchi CUD',
                    }
                    registra_evento_anomalia(
                        utente=request.user,
                        azienda=azienda,
                        contesto='import_cud_massivo',
                        anomalia=an,
                        request=request,
                    )
                    risultati.append({
                        'filename': nome,
                        'pagina': '-',
                        'cf': None,
                        'dipendente': None,
                        'esito': 'attenzione',
                        'messaggio': f'{pagine_orfane_senza_cf} pagine ignorate: CF non estratto',
                        'documento_id': None,
                    })

                if gruppi_duplicati:
                    risultati.append({
                        'filename': nome,
                        'pagina': '-',
                        'cf': None,
                        'dipendente': None,
                        'esito': 'scartato',
                        'messaggio': f'{gruppi_duplicati} blocchi CUD duplicati scartati automaticamente',
                        'documento_id': None,
                    })

            except Exception as exc:
                risultati.append({'filename': nome, 'esito': 'errore', 'messaggio': str(exc), 'documento_id': None})
            finally:
                if tmp_path:
                    try:
                        os.unlink(tmp_path)
                    except Exception:
                        pass

        ok_n = sum(1 for r in risultati if r.get('esito') == 'ok')
        if ok_n:
            messages.success(request, f'✅ Import CUD completato: {ok_n} pagine importate.')
            registra_log(
                utente=request.user,
                azienda=azienda,
                operazione='import_cud_massivo_unico',
                descrizione=f'Import CUD massivo anno {anno}: {ok_n} pagine importate',
                request=request,
            )
        else:
            messages.warning(request, 'Nessun CUD importato. Verifica il dettaglio anomalie.')

        return render(request, 'documenti/upload_cud_massivo.html', {
            'azienda': azienda,
            'anno': anno,
            'anni': list(range(timezone.now().year - 3, timezone.now().year + 1)),
            'risultati': risultati,
        })

    return render(request, 'documenti/upload_cud_massivo.html', {
        'azienda': azienda,
        'anno': anno,
        'anni': list(range(timezone.now().year - 3, timezone.now().year + 1)),
        'risultati': [],
    })


@login_required
def upload_documento_personale(request):
    """Dipendente/candidato carica i propri documenti personali."""
    ruolo = None
    if _is_candidato(request.user):
        ruolo = 'candidato'
    elif _is_dipendente(request.user):
        ruolo = 'dipendente'
    else:
        return HttpResponseForbidden("Accesso negato.")
    dip = get_dipendente_collegato(request.user)

    if not dip:
        messages.error(request, "Nessun profilo dipendente associato.")
        return redirect('candidato_dashboard' if ruolo == 'candidato' else 'lista_documenti')

    TIPI_PERSONALI = [
        ('documento_identita', 'Documento di identità'),
        ('permesso_soggiorno', 'Permesso di soggiorno'),
        ('codice_fiscale_doc', 'Tessera sanitaria / Codice fiscale'),
        ('curriculum', 'Curriculum vitae'),
        ('attestato', 'Attestato professionale'),
        ('abilitazione', 'Abilitazione tecnica'),
        ('titolo_studio', 'Titolo di studio'),
        ('certificazione', 'Certificazione / Titolo di studio'),
    ]
    tipi_personali_ammessi = {code for code, _ in TIPI_PERSONALI}

    if request.method == 'POST':
        tipo = request.POST.get('tipo')
        descrizione = request.POST.get('descrizione', '')
        file_obj = request.FILES.get('file')
        if not tipo or not file_obj:
            messages.error(request, "Tipo e file sono obbligatori.")
        elif tipo not in tipi_personali_ammessi:
            messages.error(request, "Tipo documento non valido per caricamento personale.")
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
    documento = Documento.objects.filter(id=documento_id).first()
    if not documento:
        messages.warning(request, "Documento non più disponibile.")
        return _redirect_documenti_fallback(request)

    access_error = _assert_documento_accesso(request, documento)
    if isinstance(access_error, HttpResponseForbidden):
        return access_error

    if not documento.file:
        alt = _find_documento_alternativo_con_file(documento)
        if alt:
            messages.warning(request, "Aperta versione alternativa del documento: il file del record selezionato non è disponibile.")
            documento = alt
        else:
            messages.warning(request, "File documento non presente.")
            return _redirect_documenti_fallback(request, documento)

    try:
        return FileResponse(
            documento.file.open('rb'),
            as_attachment=True,
            filename=os.path.basename(documento.file.name),
        )
    except FileNotFoundError:
        alt = _find_documento_alternativo_con_file(documento)
        if alt:
            try:
                messages.warning(request, "File originale mancante: aperta versione alternativa disponibile.")
                return FileResponse(
                    alt.file.open('rb'),
                    as_attachment=True,
                    filename=os.path.basename(alt.file.name),
                )
            except FileNotFoundError:
                pass
        messages.warning(request, "File documento non trovato sul server. Eseguire reimport o ripristino media.")
        return _redirect_documenti_fallback(request, documento)


def _file_response_inline_pdf_maybe_iframe(file_handle, filename, *, embed: bool):
    """
    PDF in-line per il browser. Con ``embed=True`` (query ``?embed=1`` nel viewer a cornice)
    marca la risposta come esente da X-Frame-Options DENY, così Firefox può mostrarla
    nell'iframe della pagina ``file_viewer_frame.html``.
    """
    response = FileResponse(
        file_handle,
        as_attachment=False,
        filename=filename,
    )
    if embed:
        response.xframe_options_exempt = True
    return response


@login_required
def visualizza_documento(request, documento_id):
    """Visualizzazione inline documento (PDF browser), con stessi permessi del download."""
    documento = Documento.objects.filter(id=documento_id).first()
    if not documento:
        messages.warning(request, "Documento non più disponibile.")
        return _redirect_documenti_fallback(request)

    access_error = _assert_documento_accesso(request, documento)
    if isinstance(access_error, HttpResponseForbidden):
        return access_error

    if not documento.file:
        alt = _find_documento_alternativo_con_file(documento)
        if alt:
            messages.warning(request, "Aperta versione alternativa del documento: il file del record selezionato non è disponibile.")
            documento = alt
        else:
            messages.warning(request, "File documento non presente.")
            return _redirect_documenti_fallback(request, documento)

    next_raw = (request.GET.get('next') or '').strip()
    if (request.GET.get('ui') == '1' or next_raw) and request.GET.get('embed') != '1':
        next_safe = sanitize_internal_next(request, next_raw)
        embed_src = request.build_absolute_uri(
            reverse('visualizza_documento', args=[documento_id]) + '?embed=1'
        )
        response = render(
            request,
            'common/file_viewer_frame.html',
            {
                'titolo': f'Documento #{documento_id}',
                'embed_src': embed_src,
                'next_url': next_safe,
                'documento_id': documento.id,
                'documento_storage_path': (
                    (documento.file.name or '').lstrip('/')
                    if getattr(documento, 'file', None) and getattr(documento.file, 'name', '')
                    else ''
                ),
            },
        )
        # La shell HTML del viewer (non solo il PDF ?embed=1) andava in iframe con
        # X-Frame-Options: DENY — Firefox bloccava l’anteprima. Stesso criterio del PDF.
        response.xframe_options_exempt = True
        return response

    _embed = request.GET.get('embed') == '1'
    try:
        return _file_response_inline_pdf_maybe_iframe(
            documento.file.open('rb'),
            os.path.basename(documento.file.name),
            embed=_embed,
        )
    except FileNotFoundError:
        alt = _find_documento_alternativo_con_file(documento)
        if alt:
            try:
                messages.warning(request, "File originale mancante: aperta versione alternativa disponibile.")
                return _file_response_inline_pdf_maybe_iframe(
                    alt.file.open('rb'),
                    os.path.basename(alt.file.name),
                    embed=_embed,
                )
            except FileNotFoundError:
                pass
        messages.warning(request, "File documento non trovato sul server. Eseguire reimport o ripristino media.")
        return _redirect_documenti_fallback(request, documento)


@login_required
def visualizza_cedolino_busta(request, documento_id):
    """Pagina con dati estratti dal cedolino (layout TeamSystem, senza blocco aziendale)."""
    documento = Documento.objects.filter(id=documento_id).first()
    if not documento:
        messages.warning(request, "Documento non più disponibile.")
        return _redirect_documenti_fallback(request)

    access_error = _assert_documento_accesso(request, documento)
    if isinstance(access_error, HttpResponseForbidden):
        return access_error

    if documento.tipo != "busta_paga":
        messages.warning(
            request,
            "L'estrazione testuale del cedolino è disponibile solo per i documenti di tipo busta paga.",
        )
        return _redirect_documenti_fallback(request, documento)

    if not documento.file:
        alt = _find_documento_alternativo_con_file(documento)
        if alt:
            messages.warning(
                request,
                "Aperta versione alternativa del documento: il file del record selezionato non è disponibile.",
            )
            documento = alt
        else:
            messages.warning(request, "File documento non presente.")
            return _redirect_documenti_fallback(request, documento)

    fname = (documento.file.name or "").lower()
    if not fname.endswith(".pdf"):
        messages.warning(
            request,
            "L'estrazione cedolino è supportata solo per file PDF.",
        )
        return _redirect_documenti_fallback(request, documento)

    raw = None
    try:
        with documento.file.open("rb") as fh:
            raw = fh.read()
    except FileNotFoundError:
        alt = _find_documento_alternativo_con_file(documento)
        if alt:
            documento = alt
            try:
                with documento.file.open("rb") as fh:
                    raw = fh.read()
            except FileNotFoundError:
                raw = None
        else:
            raw = None
    if raw is None:
        messages.warning(
            request,
            "File documento non trovato sul server. Eseguire reimport o ripristino media.",
        )
        return _redirect_documenti_fallback(request, documento)

    res = acquisisci_busta_da_documento(documento, raw_pdf=raw)
    report = res.report if not res.errore else None
    extraction_error = res.errore
    if report and _is_admin_hr_or_consulente(request.user):
        tenta_persistenza_cedolino_v4_dopo_lettura(
            documento,
            raw,
            report,
            password=res.password_usata or "",
            c_precalcolato=res.cedolino_v4,
            calc_precalcolato=res.calc_v4,
            checks_precalcolato=res.checks_v4,
        )

    ctx = {
        "documento": documento,
        "report": report,
        "extraction_error": extraction_error,
    }
    if request.GET.get("format") == "json":
        if report is None:
            return JsonResponse({"ok": False, "error": extraction_error}, status=422)
        return JsonResponse({"ok": True, "report": report})

    return render(request, "documenti/cedolino_busta.html", ctx)


PROVA_LETTURA_BUSTA_MAX_BYTES = 12 * 1024 * 1024


def _prova_lettura_busta_page_context(request) -> dict:
    """Contesto comune: form batch anno + scelta azienda (admin)."""
    ctx = {
        "aziende_scelta": None,
        "azienda_default_id": None,
    }
    if request.user.is_superuser or request.user.has_ruolo("admin"):
        ctx["aziende_scelta"] = list(Azienda.objects.order_by("nome"))
        ao = get_azienda_operativa(request.user, request.session)
        ctx["azienda_default_id"] = ao.id if ao else None
    elif request.user.has_ruolo("hr") or request.user.has_ruolo("consulente"):
        az = getattr(request.user, "azienda", None)
        ctx["azienda_default_id"] = az.id if az else None
    return ctx


@login_required
def prova_lettura_busta_paga(request):
    """
    Carica un PDF busta in prova (non salvato): estrazione cedolino + dati aziendali.
    Solo admin, HR o consulente.
    """
    if not _is_admin_hr_or_consulente(request.user):
        return HttpResponseForbidden(
            "Funzione riservata ad amministratori, HR o consulente."
        )

    page_ctx = _prova_lettura_busta_page_context(request)
    ctx = {
        **page_ctx,
        "report": None,
        "extraction_error": None,
        "pdf_name": "",
        "confronto_imponibile_inps": None,
    }

    if request.method == "POST":
        f = request.FILES.get("pdf")
        if not f:
            messages.warning(request, "Seleziona un file PDF.")
            return render(request, "documenti/prova_lettura_busta.html", ctx)

        if f.size > PROVA_LETTURA_BUSTA_MAX_BYTES:
            messages.error(
                request,
                f"File troppo grande (massimo {PROVA_LETTURA_BUSTA_MAX_BYTES // (1024 * 1024)} MB).",
            )
            return render(request, "documenti/prova_lettura_busta.html", ctx)

        name = (getattr(f, "name", "") or "").strip().lower()
        if not name.endswith(".pdf"):
            messages.error(request, "È consentito solo un file con estensione .pdf.")
            return render(request, "documenti/prova_lettura_busta.html", ctx)

        raw = f.read()
        if len(raw) < 5 or raw[:5] != b"%PDF-":
            messages.error(request, "Il file non risulta un PDF valido.")
            return render(request, "documenti/prova_lettura_busta.html", ctx)

        pdf_name = getattr(f, "name", "") or "busta.pdf"
        report = None
        extraction_error = None
        for pw in passwords_for_busta_pdf_read():
            res = acquisisci_busta_pdf_bytes(
                raw, password=pw, file_label=pdf_name
            )
            if res.errore is None:
                report = res.report
                extraction_error = None
                break
            extraction_error = res.errore

        ctx["report"] = report
        ctx["extraction_error"] = extraction_error
        ctx["pdf_name"] = pdf_name
        ctx["confronto_imponibile_inps"] = (
            confronto_imponibile_inps_da_lettura_cedolino(report)
            if report
            else None
        )

        if request.GET.get("format") == "json" and report is not None:
            return JsonResponse(
                {
                    "ok": True,
                    "report": report,
                    "confronto_imponibile_inps": ctx["confronto_imponibile_inps"],
                },
                json_dumps_params={"default": str},
            )
        if request.GET.get("format") == "json" and extraction_error:
            return JsonResponse({"ok": False, "error": extraction_error}, status=422)

    return render(request, "documenti/prova_lettura_busta.html", ctx)


@login_required
def verifica_buste_paga(request):
    """
    Voce di menu «Verifica buste paghe»: elenco dei collegamenti a strumenti di
    lettura cedolino, motore v4, conciliazione, scostamenti presenze/motore e export.
    """
    if not _is_admin_hr_or_consulente(request.user):
        return HttpResponseForbidden(
            "Accesso riservato a amministratori, HR o consulenti del lavoro."
        )
    return render(
        request,
        "documenti/verifica_buste_paga.html",
        {
            "show_upload_massivo": _is_admin_or_hr(request.user),
            "show_estrazione_storico": is_admin(request.user),
            "show_admin_voci_cedolino": is_admin(request.user),
        },
    )


@login_required
def prova_scarica_buste_anno_cedolino_zip(request):
    """
    ZIP con HTML per ogni busta paga dell'anno (motore cedolino Claude), più index.html.
    Stessi criteri anno della dashboard buste (import + periodo da descrizione).
    """
    if not _is_admin_hr_or_consulente(request.user):
        return HttpResponseForbidden(
            "Funzione riservata ad amministratori, HR o consulente."
        )
    if request.method != "POST":
        return redirect("prova_lettura_busta_paga")

    anno_s = (request.POST.get("anno") or "").strip()
    if not (anno_s.isdigit() and len(anno_s) == 4):
        messages.error(request, "Indica un anno a quattro cifre (es. 2025).")
        return redirect("prova_lettura_busta_paga")
    anno = int(anno_s)

    azienda = None
    if request.user.is_superuser or request.user.has_ruolo("admin"):
        aid = (request.POST.get("azienda_id") or "").strip()
        if aid.isdigit():
            azienda = Azienda.objects.filter(pk=int(aid)).first()
        if not azienda:
            azienda = get_azienda_operativa(request.user, request.session)
    else:
        azienda = getattr(request.user, "azienda", None)

    if not azienda:
        messages.error(
            request,
            "Azienda non disponibile: seleziona un'azienda o verifica il profilo HR/consulente.",
        )
        return redirect("prova_lettura_busta_paga")

    if not documento_ids_busta_per_anno(azienda, anno):
        messages.warning(
            request,
            f"Nessuna busta paga in archivio per l'anno {anno} (criteri: movimenti import "
            "tipo BUSTA e/o periodo ricavato dalla descrizione del documento).",
        )
        return redirect("prova_lettura_busta_paga")

    buf, _rows, _n_ok, _n_err = build_cedolini_zip_bytes(azienda, anno)
    fn = f"cedolini_estratti_{anno}_az{azienda.id}.zip"
    resp = FileResponse(buf, as_attachment=True, filename=fn)
    resp["Content-Type"] = "application/zip"
    return resp


@login_required
def buste_paga_lettura_cedolino(request, buste_scheda="completo"):
    """
    Elenco buste paga dell'anno (stessi criteri dello ZIP cedolini): per ogni cedolino,
    tutte le voci retributive lette dal PDF con la pipeline unica ``documenti.busta_acquisizione``
    (motore v4 se usabile, altrimenti testo + merge analizza/Claude).
    Paginato: si estrae solo la pagina corrente (evita di aprire centinaia di PDF in una richiesta).

    ``buste_scheda`` (da URL): ``completo`` | ``estrazione_v4`` | ``conciliazione`` — stessi dati,
    titoli e riepilogo adattati alle due voci di menu Documenti.
    """
    if buste_scheda not in BUSTE_LETTURA_SCHEDE:
        buste_scheda = "completo"
    # Strumento HR/consulente: niente accesso diretto per dipendenti (anche URL manuale).
    if buste_scheda == "conciliazione" and not _is_admin_hr_or_consulente(request.user):
        messages.info(
            request,
            "La conciliazione tecnica tra PDF e dati è riservata a HR e consulenti. "
            "Per consultare le buste paga usa Documenti dal portale.",
        )
        return redirect("candidato_miei_documenti")
    dipendente_portale = None
    if _is_dipendente(request.user):
        dipendente_portale = get_dipendente_collegato(request.user)
        if not dipendente_portale:
            return HttpResponseForbidden("Accesso negato.")
        azienda = dipendente_portale.azienda
        show_staff_filters = False
        aziende_scelta = None
    elif _is_admin_hr_or_consulente(request.user):
        show_staff_filters = True
        if request.user.is_superuser or request.user.has_ruolo("admin"):
            aziende_scelta = list(Azienda.objects.order_by("nome"))
            aid = (request.GET.get("azienda_id") or "").strip()
            azienda = Azienda.objects.filter(pk=int(aid)).first() if aid.isdigit() else None
            if not azienda:
                azienda = get_azienda_operativa(request.user, request.session)
        else:
            aziende_scelta = None
            azienda = getattr(request.user, "azienda", None)
    else:
        return HttpResponseForbidden("Accesso negato.")

    if not azienda:
        messages.error(request, "Nessuna azienda operativa o selezionata.")
        return redirect("lista_documenti")

    if request.method == "POST" and _is_admin_hr_or_consulente(request.user):
        doc_v4 = (request.POST.get("salva_estrazione_v4") or "").strip()
        if doc_v4.isdigit():
            doc_target = Documento.objects.filter(
                id=int(doc_v4),
                tipo="busta_paga",
                azienda=azienda,
            ).first()
            if doc_target:
                ae = _assert_documento_accesso(
                    request, doc_target, mark_visualizzato_da_azienda=False
                )
                if not isinstance(ae, HttpResponseForbidden):
                    if not doc_target.dipendente_id:
                        messages.warning(
                            request,
                            "Il documento non ha dipendente collegato: impossibile salvare l’estrazione v4.",
                        )
                    elif not _documento_file_disponibile(doc_target):
                        messages.warning(
                            request,
                            "File busta non disponibile sullo storage.",
                        )
                    else:
                        last_exc: Exception | None = None
                        saved = False
                        try:
                            doc_target.file.open("rb")
                            raw = doc_target.file.read()
                        except Exception as exc:
                            last_exc = exc
                            raw = b""
                        finally:
                            try:
                                doc_target.file.close()
                            except Exception:
                                pass
                        if raw:
                            for pw in passwords_for_busta_pdf_read():
                                res = acquisisci_busta_pdf_bytes(
                                    raw,
                                    password=pw,
                                    file_label=doc_target.nome_file() or "",
                                )
                                if res.errore or not res.report:
                                    last_exc = Exception(
                                        res.errore or "lettura PDF non riuscita"
                                    )
                                    continue
                                if tenta_persistenza_cedolino_v4_dopo_lettura(
                                    doc_target,
                                    raw,
                                    res.report,
                                    password=pw,
                                    c_precalcolato=res.cedolino_v4,
                                    calc_precalcolato=res.calc_v4,
                                    checks_precalcolato=res.checks_v4,
                                ):
                                    messages.success(
                                        request,
                                        "Estrazione motore v4 salvata (cedolino e voci in database).",
                                    )
                                    saved = True
                                    break
                                last_exc = Exception(
                                    "Il PDF non risulta elaborabile con il motore posizionale v4 "
                                    "(oppure mese/anno cedolino mancanti)."
                                )
                            if not saved:
                                msg = (
                                    str(last_exc)
                                    if last_exc
                                    else "estrazione non riuscita"
                                )
                                messages.warning(
                                    request,
                                    f"Impossibile salvare l’estrazione v4: {msg}",
                                )
                else:
                    messages.warning(request, "Accesso negato al documento.")
            else:
                messages.warning(
                    request,
                    "Documento non trovato o non è una busta di questa azienda.",
                )
            redir_q = {}
            for k in ("anno", "azienda_id", "dipendente", "page"):
                v = (request.POST.get(k) or "").strip()
                if v:
                    redir_q[k] = v
            scheda_post = (request.POST.get("buste_scheda") or "").strip()
            if scheda_post not in BUSTE_LETTURA_SCHEDE:
                scheda_post = buste_scheda
            url_name = BUSTE_LETTURA_REDIRECT_NAME.get(
                scheda_post, "buste_paga_lettura_cedolino"
            )
            return redirect(f"{reverse(url_name)}?{urlencode(redir_q)}")

    anni_disponibili = _anni_disponibili_buste_paga(azienda)
    anno_default = str(timezone.now().year)
    anno_s = (request.GET.get("anno") or anno_default).strip()
    if not (anno_s.isdigit() and len(anno_s) == 4):
        anno_s = anno_default
    anno = int(anno_s)

    dipendente_filter = (request.GET.get("dipendente") or "").strip()

    qs = queryset_buste_anno(azienda, anno)
    if dipendente_portale is not None:
        qs = qs.filter(dipendente_id=dipendente_portale.id)
    elif show_staff_filters and dipendente_filter.isdigit():
        qs = qs.filter(dipendente_id=int(dipendente_filter))

    qs = qs.order_by("dipendente__cognome", "dipendente__nome", "data_caricamento", "id")

    paginator = Paginator(qs, BUSTE_LETTURA_CEDOLINO_PAGE_SIZE)
    page_obj = paginator.get_page(request.GET.get("page") or 1)

    docs_page = list(page_obj.object_list)
    cedolini_v4_by_doc = mappa_cedolini_v4_per_documenti([d.id for d in docs_page])

    prepared = []
    periodo_keys: list[tuple[int, int, int, str]] = []
    for doc in docs_page:
        access_err = _assert_documento_accesso(
            request, doc, mark_visualizzato_da_azienda=False
        )
        if isinstance(access_err, HttpResponseForbidden):
            continue
        report, err = estrai_report_per_documento(
            doc,
            persist_motore_v4=_is_admin_hr_or_consulente(request.user),
        )
        mese_eff, yper_eff = periodo_retributivo_effettivo(
            doc, report if err is None else None
        )
        rpt_ok = report if err is None else None
        natura_eff = infer_natura_busta_per_busta(documento=doc, report=rpt_ok)
        if doc.dipendente_id and mese_eff and yper_eff:
            periodo_keys.append((doc.dipendente_id, mese_eff, yper_eff, natura_eff))
        prepared.append((doc, report, err, mese_eff, yper_eff, natura_eff))

    cedolini_v4 = mappa_cedolini_v4_per_periodi(periodo_keys)
    cedolini_v4_lists = cedolini_v4_tutti_per_periodi(periodo_keys)

    righe = []
    for doc, report, err, mese, yper, natura_eff in prepared:
        dip = doc.dipendente
        dip_label = f"{dip.cognome} {dip.nome}".strip() if dip else "Senza dipendente"
        if mese and yper:
            periodo_label = f"{mese:02d}/{yper}"
        else:
            periodo_label = (doc.descrizione or "—")[:120]
        m_doc, y_doc = parse_periodo_busta(doc)
        periodo_doc_mismatch = ""
        if (
            mese
            and yper
            and m_doc
            and y_doc
            and (m_doc != mese or y_doc != yper)
        ):
            periodo_doc_mismatch = f"{m_doc:02d}/{y_doc}"
        v4_row = risolvi_cedolino_motore_v4_per_documento_busta(
            doc,
            mese,
            yper,
            natura_busta=natura_eff,
            cache_periodo=cedolini_v4,
            cache_documento=cedolini_v4_by_doc,
        )
        v4_conflitto_documento_id = None
        if (
            v4_row is None
            and doc.dipendente_id
            and mese
            and yper
        ):
            for cand in cedolini_v4_lists.get(
                (doc.dipendente_id, mese, yper, natura_eff), []
            ):
                if cand.documento_id and cand.documento_id != doc.id:
                    v4_conflitto_documento_id = cand.documento_id
                    break
        n_voci_oggi = (
            len(report.get("voci_retributive") or [])
            if (err is None and report)
            else 0
        )
        conciliazione_v4 = conciliazione_oggi_vs_cedolino_motore_v4(
            report if err is None else None,
            v4_row,
            periodo_mese=mese,
            periodo_anno=yper,
            n_voci_lettura=n_voci_oggi,
            verifica_ricalcolo_da_db=(buste_scheda == "conciliazione"),
        )
        righe.append(
            {
                "documento": doc,
                "dipendente_label": dip_label,
                "periodo_label": periodo_label,
                "periodo_doc_mismatch": periodo_doc_mismatch,
                "report": report,
                "extraction_error": err,
                "voci": (report.get("voci_retributive") or []) if report else [],
                "tipo_cedolino": report.get("tipo_cedolino") if report else None,
                "conciliazione_v4": conciliazione_v4,
                "v4_conflitto_documento_id": v4_conflitto_documento_id,
            }
        )
        if buste_scheda == "conciliazione":
            persisti_esito_verifica_da_riga_busta(v4_row, conciliazione_v4, err)

    if buste_scheda == "conciliazione":
        for r in righe:
            r["conc_tab"] = compact_conciliazione_per_tabella(r["conciliazione_v4"])

    somma_netto_pdf_coerenti = Decimal(0)
    somma_lordo_pdf_coerenti = Decimal(0)
    n_mesi_conciliazione_ok = 0
    tot_checks_formula_ko_pagina = 0
    for r in righe:
        cv = r["conciliazione_v4"]
        tot_checks_formula_ko_pagina += int(cv.get("n_checks_formula_ko") or 0)
        if cv.get("stato") != "ok":
            continue
        rep = r.get("report")
        if not rep:
            continue
        nn, ll = netto_lordo_da_report(rep)
        if nn is not None:
            somma_netto_pdf_coerenti += nn
        if ll is not None:
            somma_lordo_pdf_coerenti += ll
        n_mesi_conciliazione_ok += 1

    n_senza_v4_legacy = 0
    n_senza_v4_motore_ok_db_no = 0
    for r in righe:
        if r["conciliazione_v4"].get("stato") != "senza_salvato":
            continue
        rep = r.get("report")
        if not rep:
            continue
        if (rep.get("motore") or "").strip() == "posizionale_v4":
            n_senza_v4_motore_ok_db_no += 1
        else:
            n_senza_v4_legacy += 1

    n_buste_ricalcolo_formula = sum(
        1
        for r in righe
        if r["conciliazione_v4"].get("verifica_ricalcolo_eseguita")
    )

    confronto_riepilogo = {
        "pagina": len(righe),
        "con_estrazione_v4": sum(
            1 for r in righe if r["conciliazione_v4"].get("ha_salvato")
        ),
        "senza_estrazione_v4": sum(
            1
            for r in righe
            if r["conciliazione_v4"].get("stato") == "senza_salvato"
        ),
        "senza_v4_lettura_motore_legacy": n_senza_v4_legacy,
        "senza_v4_motore_v4_senza_persist": n_senza_v4_motore_ok_db_no,
        "coerenti": sum(
            1 for r in righe if r["conciliazione_v4"].get("stato") == "ok"
        ),
        "differenze": sum(
            1 for r in righe if r["conciliazione_v4"].get("stato") == "differenze"
        ),
        "senza_report": sum(
            1 for r in righe if r["conciliazione_v4"].get("stato") == "senza_report"
        ),
        "somma_netto_pdf_coerenti_pagina": (
            format_euro_conc(somma_netto_pdf_coerenti)
            if n_mesi_conciliazione_ok
            else "—"
        ),
        "somma_lordo_pdf_coerenti_pagina": (
            format_euro_conc(somma_lordo_pdf_coerenti)
            if n_mesi_conciliazione_ok
            else "—"
        ),
        "n_mesi_conciliati_ok_pagina": n_mesi_conciliazione_ok,
        "tot_checks_formula_ko_pagina": tot_checks_formula_ko_pagina,
        "n_buste_ricalcolo_formula": n_buste_ricalcolo_formula,
    }
    # Verifica coerenza contatori esito (diagnosi / legenda template)
    _cr = confronto_riepilogo
    _somma_esiti = (
        _cr["coerenti"]
        + _cr["differenze"]
        + _cr["senza_report"]
        + _cr["senza_estrazione_v4"]
    )
    confronto_riepilogo = {
        **_cr,
        "esiti_somma": _somma_esiti,
        "esiti_somma_coerente": _somma_esiti == _cr["pagina"],
    }

    dipendenti_filtro = []
    if show_staff_filters:
        dipendenti_filtro = list(
            Dipendente.objects.filter(azienda=azienda).order_by("cognome", "nome")
        )

    pq = {"anno": str(anno)}
    if show_staff_filters and azienda:
        pq["azienda_id"] = str(azienda.id)
    if dipendente_filter:
        pq["dipendente"] = dipendente_filter

    ref_post = {
        "anno": str(anno),
        "page": str(page_obj.number),
        "azienda_id": str(azienda.id) if show_staff_filters and azienda else "",
        "dipendente": dipendente_filter,
        "buste_scheda": buste_scheda,
    }
    ctx = {
        "azienda": azienda,
        "anno": anno,
        "anni_disponibili": anni_disponibili,
        "page_obj": page_obj,
        "righe": righe,
        "show_staff_filters": show_staff_filters,
        "aziende_scelta": aziende_scelta,
        "dipendenti_filtro": dipendenti_filtro,
        "dipendente_filter": dipendente_filter,
        "is_dipendente_portale": dipendente_portale is not None,
        "pagination_qs": urlencode(pq),
        "confronto_riepilogo": confronto_riepilogo,
        "cedolino_ref_post": ref_post,
        "buste_scheda": buste_scheda,
        **tolleranze_cedolini_context(),
    }
    tpl = (
        "documenti/buste_paga_conciliazione_cedolino.html"
        if buste_scheda == "conciliazione"
        else "documenti/buste_paga_lettura_cedolino.html"
    )
    return render(request, tpl, ctx)


ARCHIVIO_DOC_BROWSE_MAX_PDF = 800


def _allowed_documento_media_subdirs_set() -> frozenset[str]:
    """Percorsi relativi a MEDIA_ROOT noti (tipi + legacy + cartelle effettivamente presenti)."""
    from documenti.upload_paths import all_documento_storage_subdirs

    base = set(all_documento_storage_subdirs())
    media_root = Path(settings.MEDIA_ROOT).resolve()
    if not media_root.is_dir():
        return frozenset(base)

    for top in media_root.iterdir():
        if not top.is_dir():
            continue
        try:
            rel = top.relative_to(media_root).as_posix().strip("/")
        except ValueError:
            continue
        if rel:
            base.add(rel)
        for p in top.rglob("*"):
            if not p.is_dir():
                continue
            try:
                srel = p.relative_to(media_root).as_posix().strip("/")
            except ValueError:
                continue
            if srel:
                base.add(srel)

    doc_root = (media_root / "documenti").resolve()
    if doc_root.is_dir():
        base.add("documenti")
        for p in doc_root.rglob("*"):
            if not p.is_dir():
                continue
            try:
                rel = p.relative_to(media_root).as_posix().strip("/")
            except ValueError:
                continue
            if rel:
                base.add(rel)
    return frozenset(base)


def _normalize_admin_subdir_input(raw: str) -> str:
    """Percorso relativo sotto MEDIA_ROOT (layout piatto, senza prefisso obbligatorio documenti/)."""
    sub = (raw or "").replace("\\", "/").strip().strip("/")
    if not sub:
        return ""
    while "//" in sub:
        sub = sub.replace("//", "/")
    if ".." in sub.split("/"):
        return ""
    return sub


def _resolve_media_path_relative_to_root(rel: str) -> Path | None:
    """Percorso assoluto sotto MEDIA_ROOT; None se path non sicuro."""
    rel = (rel or "").replace("\\", "/").strip().lstrip("/")
    if not rel or ".." in rel.split("/"):
        return None
    parts = [p for p in rel.split("/") if p and p != "."]
    if not parts:
        return None
    media = Path(settings.MEDIA_ROOT).resolve()
    try:
        full = (media / Path(*parts)).resolve()
    except (OSError, ValueError):
        return None
    try:
        full.relative_to(media)
    except ValueError:
        return None
    return full


def _format_bytes(n: int) -> str:
    n = float(n)
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024.0 or unit == "GB":
            if unit == "B":
                return f"{int(n)} {unit}"
            return f"{n:.1f} {unit}"
        n /= 1024.0
    return f"{n:.1f} TB"


def _lista_pdf_in_sottocartella(
    media_root: Path, browse_subdir: str, max_files: int
) -> tuple[list[dict[str, object]], bool]:
    """Elenco file ricorsivi sotto media_root/browse_subdir; truncated se superato max_files."""
    allowed = _allowed_documento_media_subdirs_set()
    if browse_subdir not in allowed:
        return [], False
    root = (media_root / browse_subdir).resolve()
    if not str(root).startswith(str(media_root)) or not root.is_dir():
        return [], False
    entries: list[dict[str, object]] = []
    truncated = False
    for p in sorted(root.rglob("*"), key=lambda x: str(x).lower()):
        if len(entries) >= max_files:
            truncated = True
            break
        if not p.is_file():
            continue
        rel = p.relative_to(media_root).as_posix()
        st = p.stat()
        mdt = datetime.fromtimestamp(st.st_mtime)
        if settings.USE_TZ:
            mdt = timezone.make_aware(mdt, timezone.get_current_timezone())
        entries.append(
            {
                "rel": rel,
                "size": st.st_size,
                "size_h": _format_bytes(st.st_size),
                "mtime_dt": mdt,
                "is_pdf": p.suffix.lower() == ".pdf",
            }
        )
    rels = [e["rel"] for e in entries]
    doc_map = {d.file.name: d for d in Documento.objects.filter(file__in=rels)}
    for e in entries:
        doc = doc_map.get(e["rel"])
        e["documento_id"] = doc.id if doc else None
        e["documento_tipo"] = doc.tipo if doc else ""
        e["documento_dipendente"] = str(doc.dipendente) if doc and doc.dipendente_id else ""
    return entries, truncated


def _log_archivio_documenti(request, operazione: str, descrizione: str, oggetto_id=None) -> None:
    """Log robusto delle azioni manuali nell'archivio documenti admin."""
    try:
        registra_log(
            utente=request.user,
            azienda=_azienda_scope_for_user(request.user, request),
            operazione=operazione,
            descrizione=descrizione[:500],
            oggetto_id=oggetto_id,
            request=request,
        )
    except Exception:
        # Il log non deve bloccare le operazioni archivio.
        pass


@login_required
@user_passes_test(can_gestione_database)
def admin_archivio_documenti_pdf(request):
    """Apre un PDF sotto MEDIA in cartelle documenti consentite (solo gestione database)."""
    rel = unquote((request.GET.get("rel") or "").strip())
    path = _resolve_media_path_relative_to_root(rel)
    if not path or not path.is_file() or path.suffix.lower() != ".pdf":
        raise Http404("File non trovato.")
    next_raw = (request.GET.get("next") or "").strip()
    if (request.GET.get("ui") == "1" or next_raw) and request.GET.get("embed") != "1":
        next_safe = sanitize_internal_next(request, next_raw)
        q = request.GET.copy()
        for k in ("ui", "next"):
            q.pop(k, None)
        q["embed"] = "1"
        embed_src = request.build_absolute_uri(
            reverse("admin_archivio_documenti_pdf") + "?" + q.urlencode()
        )
        return render(
            request,
            "common/file_viewer_frame.html",
            {
                "titolo": path.name,
                "embed_src": embed_src,
                "next_url": next_safe,
            },
        )
    return FileResponse(
        path.open("rb"),
        content_type="application/pdf",
        as_attachment=False,
        filename=path.name,
    )


@login_required
@user_passes_test(can_gestione_database)
@require_POST
def admin_archivio_documenti_elimina_file(request):
    """Elimina un PDF su disco; se esiste un Documento con stesso path, elimina anche il record."""
    rel = (request.POST.get("rel") or "").replace("\\", "/").strip().lstrip("/")
    browse_back = (request.POST.get("browse") or "").strip().strip("/")
    path = _resolve_media_path_relative_to_root(rel)
    if not path or not path.is_file():
        messages.error(request, "File non trovato o percorso non consentito.")
        return redirect("admin_archivio_documenti_storage")

    doc = Documento.objects.filter(file=rel).first()
    if not doc:
        doc = Documento.objects.filter(file__iendswith=rel).first()

    if doc:
        if not request.user.is_superuser:
            azienda_scope = _azienda_scope_for_user(request.user, request)
            if azienda_scope and doc.azienda_id != azienda_scope.id:
                messages.error(
                    request,
                    "Non puoi eliminare documenti collegati a un'altra azienda.",
                )
                return redirect("admin_archivio_documenti_storage")

        try:
            storage = doc.file.storage
            name = doc.file.name
            doc.delete()
            try:
                storage.delete(name)
            except Exception:
                pass
        except Exception:
            doc.delete()
        if path.is_file():
            try:
                path.unlink()
            except OSError:
                pass
        messages.success(request, "Documento e file eliminati.")
        _log_archivio_documenti(
            request,
            "archivio_documenti_elimina_file_e_record",
            f"Eliminato file e record documento rel={rel}",
            oggetto_id=doc.id,
        )
    else:
        try:
            path.unlink()
            messages.success(
                request,
                "File rimosso dal disco (nessun record Documento con questo percorso).",
            )
            _log_archivio_documenti(
                request,
                "archivio_documenti_elimina_file",
                f"Eliminato file su disco senza record DB rel={rel}",
            )
        except OSError as exc:
            messages.error(request, f"Impossibile eliminare il file: {exc}")

    allowed = _allowed_documento_media_subdirs_set()
    if browse_back in allowed:
        return redirect(f"{reverse('admin_archivio_documenti_storage')}?browse={browse_back}")
    return redirect("admin_archivio_documenti_storage")


@login_required
@user_passes_test(can_gestione_database)
def admin_archivio_documenti_storage(request):
    """Solo superuser / ruolo admin: percorsi effettivi sotto MEDIA_ROOT per i file Documento."""
    from django.conf import settings as dj_settings

    from documenti.upload_paths import all_documento_storage_subdirs

    media_root = Path(dj_settings.MEDIA_ROOT).resolve()
    browse_back = (request.POST.get("browse") or request.GET.get("browse") or "").strip().strip("/")

    if request.method == "POST":
        action = (request.POST.get("action") or "").strip()

        if action == "create_folder":
            new_subdir = _normalize_admin_subdir_input(request.POST.get("new_subdir") or "")
            if not new_subdir:
                messages.error(request, "Nome cartella non valido (es. varie/archivio o buste_paghe/2024).")
            else:
                target = (media_root / new_subdir).resolve()
                if not str(target).startswith(str(media_root)):
                    messages.error(request, "Percorso cartella non consentito.")
                else:
                    target.mkdir(parents=True, exist_ok=True)
                    messages.success(request, f"Cartella pronta: {new_subdir}")
                    _log_archivio_documenti(
                        request,
                        "archivio_documenti_crea_cartella",
                        f"Creata cartella {new_subdir}",
                    )
                    browse_back = new_subdir

        elif action == "delete_empty_folder":
            target_subdir = _normalize_admin_subdir_input(request.POST.get("target_subdir") or "")
            if target_subdir == "":
                messages.error(request, "Specifica una sottocartella da eliminare.")
            else:
                target = (media_root / target_subdir).resolve()
                if not str(target).startswith(str(media_root)) or not target.is_dir():
                    messages.error(request, "Cartella non trovata.")
                else:
                    try:
                        next(target.iterdir())
                        messages.error(request, "La cartella non è vuota.")
                    except StopIteration:
                        target.rmdir()
                        messages.success(request, f"Cartella vuota eliminata: {target_subdir}")
                        _log_archivio_documenti(
                            request,
                            "archivio_documenti_elimina_cartella_vuota",
                            f"Eliminata cartella vuota {target_subdir}",
                        )
                        if browse_back == target_subdir:
                            browse_back = ""

        elif action == "move_file":
            rel = (request.POST.get("rel") or "").replace("\\", "/").strip().lstrip("/")
            target_subdir = _normalize_admin_subdir_input(request.POST.get("target_subdir") or "")
            src = _resolve_media_path_relative_to_root(rel)
            if not src or not src.is_file():
                messages.error(request, "File sorgente non trovato.")
            elif not target_subdir:
                messages.error(request, "Seleziona una cartella di destinazione.")
            else:
                dst_dir = (media_root / target_subdir).resolve()
                if not str(dst_dir).startswith(str(media_root)):
                    messages.error(request, "Cartella destinazione non consentita.")
                else:
                    dst_dir.mkdir(parents=True, exist_ok=True)
                    dst = dst_dir / src.name
                    if dst.exists():
                        stem = dst.stem
                        suffix = dst.suffix
                        dst = dst_dir / f"{stem}_{timezone.now().strftime('%Y%m%d%H%M%S')}{suffix}"
                    src.rename(dst)
                    new_rel = dst.relative_to(media_root).as_posix()
                    updated = Documento.objects.filter(file=rel).update(file=new_rel)
                    messages.success(
                        request,
                        f"File spostato in {target_subdir}." + (" Record Documento aggiornato." if updated else ""),
                    )
                    _log_archivio_documenti(
                        request,
                        "archivio_documenti_sposta_file",
                        f"Spostato file {rel} -> {new_rel} (record_aggiornati={updated})",
                    )
                    browse_back = target_subdir

        elif action == "associate_documento":
            rel = (request.POST.get("rel") or "").replace("\\", "/").strip().lstrip("/")
            source = _resolve_media_path_relative_to_root(rel)
            if not source or not source.is_file():
                messages.error(request, "File non trovato o percorso non consentito.")
            else:
                azienda_scope = _azienda_scope_for_user(request.user, request)
                if azienda_scope is None and request.user.is_superuser:
                    azienda_scope = Azienda.objects.order_by("id").first()
                if azienda_scope is None:
                    messages.error(request, "Azienda operativa non disponibile per l'associazione.")
                else:
                    dip = None
                    dip_id = (request.POST.get("dipendente_id") or "").strip()
                    if dip_id:
                        dip = Dipendente.objects.filter(id=dip_id, azienda=azienda_scope).first()
                    tipo = (request.POST.get("tipo") or "").strip()
                    valid_tipi = {c for c, _ in Documento.TIPO_CHOICES}
                    if tipo not in valid_tipi:
                        tipo = "altro"
                    descr = (request.POST.get("descrizione") or source.stem or "").strip()[:200]

                    doc = Documento.objects.filter(file=rel).first()
                    if doc:
                        if (not request.user.is_superuser) and doc.azienda_id != azienda_scope.id:
                            messages.error(request, "Documento di un'altra azienda: associazione negata.")
                        else:
                            doc.dipendente = dip
                            doc.tipo = tipo
                            doc.descrizione = descr
                            doc.azienda = azienda_scope
                            doc.visibile_al_dipendente = bool(dip)
                            doc.save(update_fields=["dipendente", "tipo", "descrizione", "azienda", "visibile_al_dipendente"])
                            messages.success(request, "Documento associato/aggiornato con successo.")
                            _log_archivio_documenti(
                                request,
                                "archivio_documenti_associa_documento",
                                f"Aggiornato documento id={doc.id} file={rel} tipo={tipo} dipendente_id={dip.id if dip else ''}",
                                oggetto_id=doc.id,
                            )
                    else:
                        new_doc = Documento.objects.create(
                            azienda=azienda_scope,
                            dipendente=dip,
                            tipo=tipo,
                            descrizione=descr,
                            file=rel,
                            caricato_da=request.user if request.user.is_authenticated else None,
                            caricato_dal_dipendente=False,
                            visibile_al_dipendente=bool(dip),
                        )
                        messages.success(request, "Record Documento creato e associato.")
                        _log_archivio_documenti(
                            request,
                            "archivio_documenti_associa_documento_crea_record",
                            f"Creato documento id={new_doc.id} file={rel} tipo={tipo} dipendente_id={dip.id if dip else ''}",
                            oggetto_id=new_doc.id,
                        )

        elif action == "delete_document_record":
            rel = (request.POST.get("rel") or "").replace("\\", "/").strip().lstrip("/")
            doc = Documento.objects.filter(file=rel).first()
            if not doc:
                messages.error(request, "Nessun record Documento trovato per questo file.")
            else:
                azienda_scope = _azienda_scope_for_user(request.user, request)
                if (not request.user.is_superuser) and azienda_scope and doc.azienda_id != azienda_scope.id:
                    messages.error(request, "Non puoi eliminare record di un'altra azienda.")
                else:
                    doc_id = doc.id
                    doc.delete()
                    messages.success(request, "Record Documento eliminato (file su disco mantenuto).")
                    _log_archivio_documenti(
                        request,
                        "archivio_documenti_elimina_solo_record",
                        f"Eliminato solo record documento id={doc_id} file={rel}",
                        oggetto_id=doc_id,
                    )

        elif action == "run_storage_index":
            azienda_scope = _azienda_scope_for_user(request.user, request)
            if azienda_scope is None and request.user.is_superuser:
                azienda_scope = Azienda.objects.order_by("id").first()
            if azienda_scope is None:
                messages.error(request, "Azienda operativa non disponibile per l'indicizzazione.")
            else:
                apply_mode = (request.POST.get("index_mode") or "dry") == "apply"
                only_tipo = (request.POST.get("index_tipo") or "").strip()
                out = io.StringIO()
                kwargs = {
                    "azienda_id": azienda_scope.id,
                    "stdout": out,
                    "stderr": out,
                }
                if apply_mode:
                    kwargs["applica"] = True
                if only_tipo:
                    kwargs["solo_tipo"] = only_tipo
                try:
                    call_command("indicizza_documenti_storage", **kwargs)
                    result = out.getvalue().strip()
                    last_line = result.splitlines()[-1] if result else "Indicizzazione completata."
                    messages.success(request, last_line)
                    _log_archivio_documenti(
                        request,
                        "archivio_documenti_indicizza_storage",
                        f"Indicizzazione storage eseguita mode={'apply' if apply_mode else 'dry'} "
                        f"azienda_id={azienda_scope.id} solo_tipo={only_tipo or '-'}",
                    )
                except Exception as exc:
                    messages.error(request, f"Errore indicizzazione storage: {exc}")

        elif action == "audit_massive_destinations":
            from documenti.upload_paths import subdir_for_documento_tipo

            azienda_scope = _azienda_scope_for_user(request.user, request)
            if azienda_scope is None and request.user.is_superuser:
                azienda_scope = Azienda.objects.order_by("id").first()
            if azienda_scope is None:
                messages.error(request, "Azienda operativa non disponibile per la verifica destinazioni.")
            else:
                apply_fix = (request.POST.get("massive_mode") or "dry") == "apply"
                tracked_types = ("busta_paga", "altro", "certificato")
                expected = {t: subdir_for_documento_tipo(t).strip("/") + "/" for t in tracked_types}
                legacy_prefixes = (
                    "buste_paghe/",
                    "F24/",
                    "f24/",
                    "CUD/",
                    "cud/",
                    "documenti/buste_paghe/",
                    "documenti/f24/",
                    "documenti/F24/",
                    "documenti/cud/",
                    "documenti/CUD/",
                    "Liquidazioni_mensili/",
                )

                summary_parts = []
                mismatch_total = 0
                legacy_total = 0
                for tipo in tracked_types:
                    qs = Documento.objects.filter(azienda=azienda_scope, tipo=tipo).exclude(file="")
                    tot = qs.count()
                    mism = qs.exclude(file__startswith=expected[tipo]).count()
                    leg_q = Q()
                    for lp in legacy_prefixes:
                        leg_q |= Q(file__startswith=lp)
                    legacy = qs.filter(leg_q).count()
                    mismatch_total += mism
                    legacy_total += legacy
                    summary_parts.append(f"{tipo}: tot={tot}, fuori_cartella={mism}, legacy={legacy}")

                if apply_fix and (mismatch_total > 0 or legacy_total > 0):
                    out = io.StringIO()
                    try:
                        for tipo in tracked_types:
                            call_command(
                                "normalizza_archivio_documenti",
                                applica=True,
                                elimina_sorgente=True,
                                solo_tipo=tipo,
                                stdout=out,
                                stderr=out,
                            )
                        messages.success(
                            request,
                            "Riallineamento destinazioni completato. " + " | ".join(summary_parts),
                        )
                        _log_archivio_documenti(
                            request,
                            "archivio_documenti_riallinea_destinazioni_massive",
                            f"Riallineamento massive upload: {' | '.join(summary_parts)}",
                        )
                    except Exception as exc:
                        messages.error(request, f"Errore durante riallineamento destinazioni: {exc}")
                else:
                    messages.info(
                        request,
                        "Verifica destinazioni completata. " + " | ".join(summary_parts),
                    )
                    _log_archivio_documenti(
                        request,
                        "archivio_documenti_verifica_destinazioni_massive",
                        f"Verifica massive upload: {' | '.join(summary_parts)}",
                    )

        if browse_back and browse_back in _allowed_documento_media_subdirs_set():
            return redirect(f"{reverse('admin_archivio_documenti_storage')}?browse={browse_back}")
        return redirect("admin_archivio_documenti_storage")
    tipo_labels = dict(Documento.TIPO_CHOICES)
    mapping = getattr(dj_settings, "DOCUMENTO_TIPO_MEDIA_SUBDIRS", None) or {}
    righe_tipo: list[dict[str, object]] = []
    for tipo_cod in sorted(mapping.keys()):
        sub = (mapping[tipo_cod] or "").strip().strip("/")
        if not sub:
            continue
        full = media_root / sub
        righe_tipo.append(
            {
                "tipo_cod": tipo_cod,
                "tipo_label": tipo_labels.get(tipo_cod, tipo_cod),
                "subdir": sub,
                "path": str(full),
                "esiste": full.is_dir(),
            }
        )

    default_sub = (
        getattr(dj_settings, "DOCUMENTI_MEDIA_SUBDIR", "varie") or "varie"
    ).strip().strip("/") or "varie"
    default_path = media_root / default_sub

    altri_tipi = [
        c
        for c, _lbl in Documento.TIPO_CHOICES
        if c not in mapping
    ]

    browse_allowed = sorted(_allowed_documento_media_subdirs_set())
    browse_subdir = (request.GET.get("browse") or "").strip().strip("/")
    browse_files: list[dict[str, object]] = []
    browse_truncated = False
    if browse_subdir and browse_subdir in _allowed_documento_media_subdirs_set():
        browse_files, browse_truncated = _lista_pdf_in_sottocartella(
            media_root, browse_subdir, ARCHIVIO_DOC_BROWSE_MAX_PDF
        )

    azienda_scope = _azienda_scope_for_user(request.user, request)
    dipendenti_assoc_qs = Dipendente.objects.none()
    if azienda_scope:
        dipendenti_assoc_qs = Dipendente.objects.filter(azienda=azienda_scope).order_by("cognome", "nome")

    # KPI qualità archivio (scope azienda operativa quando disponibile)
    quality_qs = Documento.objects.all().order_by("-data_caricamento")
    if azienda_scope:
        quality_qs = quality_qs.filter(azienda=azienda_scope)

    tipi_tipicamente_personali = {"busta_paga", "contratto", "unilav", "riepilogo_mensile", "certificato"}
    docs_non_assoc_qs = quality_qs.filter(
        tipo__in=tipi_tipicamente_personali,
        dipendente__isnull=True,
    )
    docs_non_classificati_qs = quality_qs.filter(
        Q(file__startswith="documenti/non_classificati/")
        | Q(file__startswith="varie/non_classificati/")
        | Q(file__startswith="non_classificati/")
        | Q(tipo="altro")
    )

    quality_recent = list(quality_qs.select_related("dipendente")[:350])
    records_without_file = [d for d in quality_recent if not _documento_file_disponibile(d)]

    filename_counter = Counter()
    file_to_ids: dict[str, list[int]] = {}
    for d in quality_recent:
        fn = os.path.basename((d.file.name or "").strip())
        if not fn:
            continue
        filename_counter[fn] += 1
        file_to_ids.setdefault(fn, []).append(d.id)
    duplicate_names = [
        {"filename": fn, "count": cnt, "ids": file_to_ids.get(fn, [])[:6]}
        for fn, cnt in filename_counter.items()
        if cnt > 1
    ]
    duplicate_names.sort(key=lambda x: (-x["count"], x["filename"]))

    return render(
        request,
        "documenti/admin_archivio_documenti_storage.html",
        {
            "media_root": str(media_root),
            "gesper_data_root": str(getattr(dj_settings, "GESPER_DATA_ROOT", "")),
            "gesper_archivio_root": str(getattr(dj_settings, "GESPER_ARCHIVIO_ROOT", "")),
            "media_url": getattr(dj_settings, "MEDIA_URL", "/media/"),
            "default_subdir": default_sub,
            "default_path": str(default_path),
            "default_exists": default_path.is_dir(),
            "righe_tipo": righe_tipo,
            "altri_tipi": altri_tipi,
            "legacy_subdirs": all_documento_storage_subdirs(),
            "browse_allowed": browse_allowed,
            "browse_subdir": browse_subdir,
            "browse_files": browse_files,
            "browse_truncated": browse_truncated,
            "browse_max": ARCHIVIO_DOC_BROWSE_MAX_PDF,
            "documento_tipo_choices": Documento.TIPO_CHOICES,
            "dipendenti_assoc": dipendenti_assoc_qs,
            "quality_non_assoc_count": docs_non_assoc_qs.count(),
            "quality_non_assoc_sample": docs_non_assoc_qs.select_related("dipendente")[:25],
            "quality_non_classificati_count": docs_non_classificati_qs.count(),
            "quality_non_classificati_sample": docs_non_classificati_qs.select_related("dipendente")[:25],
            "quality_missing_file_count": len(records_without_file),
            "quality_missing_file_sample": records_without_file[:25],
            "quality_duplicate_filename_count": len(duplicate_names),
            "quality_duplicate_filename_sample": duplicate_names[:20],
        },
    )


@login_required
@user_passes_test(can_gestione_database)
def admin_archivio_documenti_log(request):
    """Storico operazioni archivio documenti (azioni manuali admin)."""
    q_operazione = (request.GET.get("operazione") or "").strip()
    q_utente = (request.GET.get("utente") or "").strip()
    q_data_da = (request.GET.get("data_da") or "").strip()
    q_data_a = (request.GET.get("data_a") or "").strip()

    qs = LogAttivita.objects.filter(operazione__startswith="archivio_documenti_")
    azienda_scope = _azienda_scope_for_user(request.user, request)
    if azienda_scope and not request.user.is_superuser:
        qs = qs.filter(azienda=azienda_scope)

    if q_operazione:
        qs = qs.filter(operazione=q_operazione)
    if q_utente:
        qs = qs.filter(
            Q(utente__username__icontains=q_utente)
            | Q(utente__first_name__icontains=q_utente)
            | Q(utente__last_name__icontains=q_utente)
        )
    if q_data_da:
        qs = qs.filter(data_ora__date__gte=q_data_da)
    if q_data_a:
        qs = qs.filter(data_ora__date__lte=q_data_a)

    if (request.GET.get("export") or "").strip().lower() == "csv":
        ts = timezone.now().strftime("%Y%m%d_%H%M%S")
        resp = HttpResponse(content_type="text/csv; charset=utf-8")
        resp["Content-Disposition"] = f'attachment; filename="storico_archivio_documenti_{ts}.csv"'
        writer = csv.writer(resp)
        writer.writerow(["data_ora", "utente", "azienda", "operazione", "descrizione", "oggetto_id", "ip"])
        for row in qs.select_related("utente", "azienda").order_by("-data_ora").iterator(chunk_size=500):
            writer.writerow(
                [
                    timezone.localtime(row.data_ora).strftime("%Y-%m-%d %H:%M:%S") if row.data_ora else "",
                    row.utente.username if row.utente else "",
                    row.azienda.nome if row.azienda else "",
                    row.operazione or "",
                    row.descrizione or "",
                    row.oggetto_id or "",
                    row.ip_address or "",
                ]
            )
        return resp

    paginator = Paginator(qs.select_related("utente", "azienda"), 50)
    page_obj = paginator.get_page(request.GET.get("page"))
    operazioni_disponibili = (
        LogAttivita.objects.filter(operazione__startswith="archivio_documenti_")
        .values_list("operazione", flat=True)
        .distinct()
    )

    return render(
        request,
        "documenti/admin_archivio_documenti_log.html",
        {
            "logs": page_obj,
            "operazioni_disponibili": sorted(operazioni_disponibili),
            "q_operazione": q_operazione,
            "q_utente": q_utente,
            "q_data_da": q_data_da,
            "q_data_a": q_data_a,
        },
    )


@login_required
def legacy_documento_redirect(request, legacy_filename):
    """Compatibilità URL storiche tipo /documenti/f24_...pdf -> visualizza_documento."""
    name = (legacy_filename or '').strip()
    if not name or '/' in name or not name.lower().endswith('.pdf'):
        raise Http404("Documento non trovato.")

    from documenti.upload_paths import all_documento_storage_subdirs

    documento = None
    for sub in all_documento_storage_subdirs():
        documento = Documento.objects.filter(file__iendswith=f"{sub}/{name}").first()
        if documento:
            break
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
        azienda_scope = _azienda_scope_for_user(request.user, request)
        if not azienda_scope or documento.azienda_id != azienda_scope.id:
            return HttpResponseForbidden("Accesso negato.")
    elif ruolo in ('dipendente', 'candidato'):
        # Deve essere un documento caricato dal dipendente stesso
        dip = get_dipendente_collegato(request.user)

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
    next_url = (request.POST.get('next') or '').strip()
    candidato_docs = reverse('candidato_miei_documenti')
    if next_url == candidato_docs or next_url.startswith(candidato_docs + '?'):
        return redirect(next_url)
    # Path legacy senza prefisso + varianti con FORCE_SCRIPT_NAME (es. /gesper/ su www)
    _legacy_doc_next = {'/candidato/documenti/', '/documenti/'}
    _sn = (get_script_prefix() or '').strip().rstrip('/')
    if _sn:
        _legacy_doc_next.add(f'{_sn}/candidato/documenti/')
        _legacy_doc_next.add(f'{_sn}/documenti/')
    if next_url in _legacy_doc_next:
        return redirect('candidato_miei_documenti')
    return redirect('candidato_miei_documenti' if ruolo in ('candidato', 'dipendente') else 'lista_documenti')
