"""
Selezione documenti busta paga per anno (movimenti import + fallback descrizione)
e generazione ZIP di HTML estratti con il motore `leggi_busta_paga_claude`.
"""

from __future__ import annotations

import io
import re
import zipfile
from typing import Any, List

from django.template.loader import render_to_string

from accounts.models import MovimentoImportPaghe
from django.db.models import Q

from documenti.busta_acquisizione import acquisisci_busta_da_documento
from documenti.cedolino_estrazione_v4_store import tenta_persistenza_cedolino_v4_dopo_lettura
from documenti.models import Documento
from documenti.upload_paths import busta_paga_storage_q

MESI_ITA = {
    "GENNAIO": 1,
    "FEBBRAIO": 2,
    "MARZO": 3,
    "APRILE": 4,
    "MAGGIO": 5,
    "GIUGNO": 6,
    "LUGLIO": 7,
    "AGOSTO": 8,
    "SETTEMBRE": 9,
    "OTTOBRE": 10,
    "NOVEMBRE": 11,
    "DICEMBRE": 12,
}


def parse_periodo_busta(doc: Documento) -> tuple[int | None, int | None]:
    """Stessa logica di `documenti.views._parse_periodo_busta` (senza import circolare)."""
    desc = (getattr(doc, "descrizione", "") or "").upper()

    m = re.search(r"\b(0?[1-9]|1[0-2])\s*/\s*(20\d{2})\b", desc)
    if m:
        return int(m.group(1)), int(m.group(2))

    year_m = re.search(r"\b(20\d{2})\b", desc)
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


def parse_periodo_busta_con_pdf(
    doc: Documento,
    mov: MovimentoImportPaghe | None = None,
    *,
    max_pdf_pages: int = 3,
) -> tuple[int | None, int | None]:
    """
    Periodo per ordinamento/dashboard: movimento import (se presente e valido),
    poi prime pagine PDF (MM/AAAA dal testo), infine descrizione/data caricamento.
    """
    if mov and getattr(mov, "mese", None) and getattr(mov, "anno", None):
        try:
            mi, yi = int(mov.mese), int(mov.anno)
        except (TypeError, ValueError):
            mi, yi = 0, 0
        if 1 <= mi <= 12 and yi >= 1990:
            return mi, yi

    from documenti.busta_periodo_da_pdf import (
        busta_pdf_text_first_pages,
        estrai_mese_anno_da_testo_cedolino,
    )

    if getattr(doc, "file", None):
        try:
            txt = busta_pdf_text_first_pages(doc, max_pages=max_pdf_pages)
        except Exception:
            txt = ""
        m, y = estrai_mese_anno_da_testo_cedolino(txt)
        if m and y and 1 <= m <= 12 and y >= 1990:
            return m, y
    return parse_periodo_busta(doc)


def periodo_da_mese_retribuito_testo(label: str | None) -> tuple[int | None, int | None]:
    """Da stringa tipo «OTTOBRE 2025» (intestazione PDF / estrazioni legacy)."""
    if not (label or "").strip():
        return None, None
    t = label.strip().upper()
    year_m = re.search(r"\b(20\d{2})\b", t)
    year = int(year_m.group(1)) if year_m else None
    month = None
    for nome, num in MESI_ITA.items():
        if nome in t:
            month = num
            break
    if month and year:
        return month, year
    return None, None


def periodo_retributivo_effettivo(
    doc: Documento, report: dict[str, Any] | None
) -> tuple[int | None, int | None]:
    """
    Mese/anno per conciliazione e match su ``CedolinoMotoreV4``: prima dal PDF
    (motore v4 o «Mese Retribuito»), poi da descrizione / data caricamento documento.
    """
    if report:
        mp, yp = report.get("periodo_mese_pdf"), report.get("periodo_anno_pdf")
        if mp is not None and yp is not None:
            try:
                mi, yi = int(mp), int(yp)
            except (TypeError, ValueError):
                mi, yi = 0, 0
            if 1 <= mi <= 12 and yi >= 1990:
                return mi, yi
        dip = report.get("dati_dipendente") or {}
        m1, y1 = periodo_da_mese_retribuito_testo(dip.get("Mese Retribuito"))
        if m1 and y1:
            return m1, y1
    return parse_periodo_busta(doc)


def _documento_busta_file_ok(doc: Documento) -> bool:
    if not doc.file:
        return False
    try:
        return bool(doc.file.storage.exists(doc.file.name))
    except Exception:
        return False


def documento_ids_busta_per_anno(azienda, anno: int) -> set[int]:
    """Movimenti BUSTA per anno + buste con periodo da descrizione, solo cartelle busta ammesse e file presente su storage."""
    q_busta = Q(azienda_id=azienda.id, tipo="busta_paga") & busta_paga_storage_q()

    raw_mov_ids = set(
        MovimentoImportPaghe.objects.filter(
            azienda_id=azienda.id,
            tipo="BUSTA",
            anno=anno,
            documento_id__isnull=False,
        ).values_list("documento_id", flat=True)
    )
    ids: set[int] = set()
    if raw_mov_ids:
        for d in (
            Documento.objects.filter(id__in=raw_mov_ids)
            .filter(q_busta)
            .only("id", "file")
            .iterator(chunk_size=500)
        ):
            if _documento_busta_file_ok(d):
                ids.add(d.id)

    for d in (
        Documento.objects.filter(azienda_id=azienda.id, tipo="busta_paga")
        .filter(busta_paga_storage_q())
        .only("id", "descrizione", "data_caricamento", "file")
        .iterator(chunk_size=500)
    ):
        if not _documento_busta_file_ok(d):
            continue
        _, y = parse_periodo_busta(d)
        if y == anno:
            ids.add(d.id)
    return ids


def queryset_buste_anno(azienda, anno: int):
    ids = documento_ids_busta_per_anno(azienda, anno)
    return (
        Documento.objects.filter(id__in=ids, tipo="busta_paga")
        .select_related("dipendente", "azienda")
        .order_by("dipendente__cognome", "dipendente__nome", "data_caricamento", "id")
    )


def _safe_filename_part(s: str, max_len: int = 40) -> str:
    t = re.sub(r"[^\w\-.]+", "_", (s or "").strip(), flags=re.UNICODE)
    return (t[:max_len] or "doc").strip("_") or "doc"


def _documento_file_bytes(doc: Documento) -> bytes | None:
    if not doc.file:
        return None
    try:
        with doc.file.open("rb") as fh:
            return fh.read()
    except Exception:
        return None


def estrai_report_per_documento(
    doc: Documento, *, persist_motore_v4: bool = False
) -> tuple[dict[str, Any] | None, str | None]:
    name = (doc.file.name or "").lower() if doc.file else ""
    if not name.endswith(".pdf"):
        return None, "Non è un PDF"
    raw = _documento_file_bytes(doc)
    if raw is None or len(raw) < 5 or raw[:5] != b"%PDF-":
        return None, "File PDF mancante o non valido"
    res = acquisisci_busta_da_documento(doc, raw_pdf=raw)
    if res.errore:
        return None, res.errore
    if persist_motore_v4:
        tenta_persistenza_cedolino_v4_dopo_lettura(
            doc,
            raw,
            res.report,
            password=res.password_usata or "",
            c_precalcolato=res.cedolino_v4,
            calc_precalcolato=res.calc_v4,
            checks_precalcolato=res.checks_v4,
        )
    return res.report, None


def build_cedolini_zip_bytes(azienda, anno: int) -> tuple[io.BytesIO, List[dict[str, Any]], int, int]:
    """
    Restituisce (buffer zip, righe riepilogo per index.html, n_ok, n_err).
    """
    rows: List[dict[str, Any]] = []
    n_ok = 0
    n_err = 0
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for doc in queryset_buste_anno(azienda, anno).iterator():
            dip = doc.dipendente
            dip_label = (
                f"{dip.cognome} {dip.nome}".strip()
                if dip
                else "Senza dipendente"
            )
            base = f"cedolino_{doc.id}_{_safe_filename_part(dip_label)}"
            html_name = f"{base}.html"
            report, err = estrai_report_per_documento(doc)
            if report is not None:
                n_ok += 1
                body = render_to_string(
                    "documenti/cedolino_busta_standalone.html",
                    {
                        "report": report,
                        "extraction_error": None,
                        "pdf_label": doc.nome_file() or f"doc_{doc.id}.pdf",
                        "pdf_path": f"{dip_label} — {(doc.descrizione or '')[:80]}",
                    },
                )
                zf.writestr(html_name, body.encode("utf-8"))
                rows.append(
                    {
                        "doc_id": doc.id,
                        "filename": html_name,
                        "dipendente_label": dip_label,
                        "descrizione": doc.descrizione or "",
                        "ok": True,
                        "error": None,
                    }
                )
            else:
                n_err += 1
                rows.append(
                    {
                        "doc_id": doc.id,
                        "filename": html_name,
                        "dipendente_label": dip_label,
                        "descrizione": doc.descrizione or "",
                        "ok": False,
                        "error": err or "Errore",
                    }
                )

        index_html = render_to_string(
            "documenti/cedolino_batch_zip_index.html",
            {"anno": anno, "azienda": azienda, "rows": rows, "n_ok": n_ok, "n_err": n_err},
        )
        zf.writestr("index.html", index_html.encode("utf-8"))

    buf.seek(0)
    return buf, rows, n_ok, n_err
