"""
Estrazione best-effort del periodo retributivo (mese, anno) dal testo delle prime
pagine del PDF cedolino (layout italiani / TeamSystem), per ordinamento MM-AAAA.
"""

from __future__ import annotations

import io
import re
from typing import TYPE_CHECKING

from documenti.buste_pdf_passwords import passwords_for_busta_pdf_read

if TYPE_CHECKING:
    from documenti.models import Documento

_MESI_ITA = {
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


def busta_pdf_text_first_pages(doc: "Documento", *, max_pages: int = 3) -> str:
    """Testo estratto con PyPDF dalle prime ``max_pages`` pagine (password buste)."""
    try:
        from pypdf import PdfReader
    except Exception:
        return ""
    if not getattr(doc, "file", None):
        return ""
    try:
        raw = doc.file.read()
    except Exception:
        return ""
    if not raw or len(raw) < 5 or raw[:5] != b"%PDF-":
        return ""
    max_pages = max(1, min(int(max_pages), 20))
    for pwd in passwords_for_busta_pdf_read():
        try:
            reader = PdfReader(io.BytesIO(raw))
            if getattr(reader, "is_encrypted", False):
                try:
                    reader.decrypt(pwd or "")
                except Exception:
                    continue
            n = min(len(reader.pages), max_pages)
            parts: list[str] = []
            for i in range(n):
                try:
                    parts.append(reader.pages[i].extract_text() or "")
                except Exception:
                    parts.append("")
            joined = "\n".join(parts)
            if joined.strip():
                return joined
        except Exception:
            continue
    return ""


def _mese_anno_da_nomi_mesi(s: str) -> tuple[int | None, int | None]:
    t = (s or "").upper()
    year_m = re.search(r"\b(20\d{2})\b", t)
    year = int(year_m.group(1)) if year_m else None
    month = None
    # Preferisci il nome mese più lungo (es. SETTEMBRE prima di MARZO in «MARZO» no - ok)
    for nome, num in sorted(_MESI_ITA.items(), key=lambda x: -len(x[0])):
        if re.search(rf"\b{re.escape(nome)}\b", t):
            month = num
            break
    if month and year:
        return month, year
    return None, None


def _periodo_plausibile(mese: int | None, anno: int | None) -> bool:
    if not mese or not anno:
        return False
    if not (1 <= mese <= 12):
        return False
    return 2015 <= anno <= 2100


def _mm_yyyy_sicuro(u: str) -> tuple[int | None, int | None]:
    """
    MM/AAAA senza catturare sottostringhe di date DD/MM/AAAA (es. 27/01/2009 → 01/2009).
    """
    m = re.search(r"(?<!\d/)(0?[1-9]|1[0-2])\s*[/\-.]\s*(20\d{2})\b", u)
    if m:
        mm, yy = int(m.group(1)), int(m.group(2))
        if _periodo_plausibile(mm, yy):
            return mm, yy
    m = re.search(r"\b(20\d{2})\s*[/\-.]\s*(0?[1-9]|1[0-2])\b", u)
    if m:
        yy, mm = int(m.group(1)), int(m.group(2))
        if _periodo_plausibile(mm, yy):
            return mm, yy
    return None, None


def _mese_anno_da_righe_mese_retribuito(lines: list[str]) -> tuple[int | None, int | None]:
    """
    Layout TeamSystem: intestazione «MESE RETRIBUITO» e sulla riga successiva
    nome mese + anno (es. APRILE 2026), spesso in colonne separate.
    """
    for idx, ln in enumerate(lines):
        u = ln.upper()
        if "MESE" not in u or "RETRIBUIT" not in u:
            continue
        mm, yy = _mm_yyyy_sicuro(u)
        if mm and yy:
            return mm, yy
        ma = _mese_anno_da_nomi_mesi(u)
        if ma[0] and ma[1] and _periodo_plausibile(ma[0], ma[1]):
            return ma
        for follow in lines[idx + 1 : idx + 4]:
            fu = follow.upper()
            ma = _mese_anno_da_nomi_mesi(fu)
            if ma[0] and ma[1] and _periodo_plausibile(ma[0], ma[1]):
                return ma
            mm, yy = _mm_yyyy_sicuro(fu)
            if mm and yy:
                return mm, yy
    return None, None


def estrai_mese_anno_da_testo_cedolino(text: str) -> tuple[int | None, int | None]:
    """
    Cerca MM/AAAA, MM-AAAA, AAAA-MM e intestazioni tipo «Mese retribuito OTTOBRE 2025».
    Restringe i match «generici» alle righe che contengono parole chiave del cedolino.
    """
    if not (text or "").strip():
        return None, None

    head = text[:12000]
    lines = [re.sub(r"\s+", " ", ln).strip() for ln in head.splitlines() if ln.strip()][:140]

    ma = _mese_anno_da_righe_mese_retribuito(lines)
    if ma[0] and ma[1]:
        return ma

    keywords_mm = (
        "MESE RETRIBUIT",
        "MESE DI PAGA",
        "PERIODO",
        "COMPETENZA",
        "RIFERIMENTO",
        "CEDOLINO",
        "PAGA DEL",
        "RETRIBUZIONE",
    )

    for ln in lines:
        u = ln.upper()
        if not any(k in u for k in keywords_mm):
            continue
        mm, yy = _mm_yyyy_sicuro(u)
        if mm and yy:
            return mm, yy
        if "MESE" in u and "RETRIB" in u:
            ma = _mese_anno_da_nomi_mesi(u)
            if ma[0] and ma[1] and _periodo_plausibile(ma[0], ma[1]):
                return ma

    blob = re.sub(r"\s+", " ", head.upper())
    for pat in (
        r"MESE\s+RETRIBUIT[OA][\s\S]{0,120}?([A-ZÀ]{4,12})\s+(20\d{2})",
        r"MESE\s+RETRIBUIT[OA]\s*:?\s*([A-ZÀ]{3,15})\s+(20\d{2})",
        r"MESE\s+RETRIBUIT[OA]\s*:?\s*([A-ZÀ]{3,15})\s*,\s*(20\d{2})",
    ):
        m = re.search(pat, blob)
        if m:
            chunk = f"{m.group(1)} {m.group(2)}"
            ma = _mese_anno_da_nomi_mesi(chunk)
            if ma[0] and ma[1] and _periodo_plausibile(ma[0], ma[1]):
                return ma

    return None, None
