#!/usr/bin/env python3
"""
PoC: analisi PDF paghe/CUD/F24 (file unico) con output JSON.
Uso:
  python scripts/analizza_pdf_paghe.py "/percorso/file.pdf" --out snapshots/report.json
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import tempfile
from collections import Counter
from dataclasses import dataclass, asdict
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Optional

# Quando lo script viene eseguito via subprocess dal management command,
# sys.path punta a scripts/. Inseriamo la root progetto per poter importare
# i moduli Django locali (es. documenti.buste_pdf_passwords).
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import os

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "settings")


def _ensure_django() -> None:
    """Necessario per motore v4 / password PDF quando lo script gira in subprocess."""
    try:
        import django
        from django.apps import apps

        if not apps.ready:
            django.setup()
    except Exception:
        pass

MONTHS_IT = {
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


def _password_candidates() -> list[str]:
    """
    Password candidate per PDF cifrati:
    - usa la stessa logica applicativa (`documenti.buste_pdf_passwords`)
    - include sempre anche stringa vuota (PDF non protetti)
    """
    try:
        from documenti.buste_pdf_passwords import passwords_for_busta_pdf_read

        return passwords_for_busta_pdf_read()
    except Exception:
        return [""]

STOP_NAME_WORDS = {
    "COGNOME", "NOME", "CODICE", "FISCALE", "DATA", "ASSUNZ", "COMUNE",
    "RESIDENZA", "POSIZIONE", "INAIL", "MATR", "INPS", "AZIENDA", "MESE",
    "RETRIBUITO", "NETTO", "BUSTA", "QUALIFICA", "DESCRIZIONE", "TRATTENUTE",
    "STATISTICHE", "ATT", "PREC", "SIGLA", "QUANTITA", "DITTA", "FOGLIO",
    "STAMPATO", "AUTORIZZAZIONE", "VOCE", "TARIFFA", "SCAD", "DOC",
}


@dataclass
class PageResult:
    page: int
    kind: str  # BUSTA | F24 | ALTRO
    cf: Optional[str]
    full_name: Optional[str]
    birth_date: Optional[str]
    lordo_busta: Optional[str]
    netto_busta: Optional[str]
    f24_importo: Optional[str]
    period_month: Optional[int]
    period_year: Optional[int]
    data_assunzione_conv: Optional[str] = None   # DD/MM/YYYY — DATA ASS. CONV.
    data_cessazione: Optional[str] = None         # DD/MM/YYYY — DATA CESSAZIONE


def run_pdftotext_layout(pdf_path: Path) -> str:
    # 1) Tentativi pdftotext: prima senza password, poi password candidate.
    candidates = [p for p in _password_candidates() if p]
    attempts = [None, *candidates]
    for pwd in attempts:
        cmd = ["pdftotext", "-layout"]
        if pwd is not None:
            cmd += ["-upw", pwd]
        cmd += [str(pdf_path), "-"]
        try:
            proc = subprocess.run(cmd, check=True, capture_output=True, text=True)
            return proc.stdout
        except (FileNotFoundError, subprocess.CalledProcessError):
            continue

    # 2) Fallback senza dipendenze di sistema: usa pypdf (+ tentativi password).
    import importlib

    PdfReader = importlib.import_module("pypdf").PdfReader
    reader = PdfReader(str(pdf_path))
    if getattr(reader, "is_encrypted", False):
        unlocked = False
        for pwd in _password_candidates():
            try:
                if reader.decrypt(pwd):
                    unlocked = True
                    break
            except Exception:
                continue
        if not unlocked:
            return ""
    pages = []
    for p in reader.pages:
        try:
            pages.append(p.extract_text() or "")
        except Exception:
            pages.append("")
    return "\f".join(pages)


def run_ocr_fallback(pdf_path: Path) -> str:
    """
    OCR fallback per PDF scansione/immagine.
    Richiede binari di sistema: `pdftoppm` e `tesseract`.
    Restituisce testo separato da form-feed per pagina.
    """
    with tempfile.TemporaryDirectory(prefix="gesper_ocr_") as td:
        tmpdir = Path(td)
        # Genera PNG per pagina (con tentativi password, perché molti PDF paghe sono cifrati).
        # -r 220: buon compromesso qualità/tempo per layout paghe.
        ok = False
        for pwd in [None, *_password_candidates()]:
            ppm_cmd = ["pdftoppm", "-png", "-r", "220"]
            if pwd:
                ppm_cmd += ["-upw", pwd]
            ppm_cmd += [str(pdf_path), str(tmpdir / "page")]
            try:
                subprocess.run(ppm_cmd, check=True, capture_output=True, text=True)
                ok = True
                break
            except subprocess.CalledProcessError:
                continue
        if not ok:
            raise RuntimeError("OCR fallback: impossibile rasterizzare PDF (password/lettura).")

        page_imgs = sorted(tmpdir.glob("page-*.png"))
        pages_txt: list[str] = []
        for img in page_imgs:
            out_base = img.with_suffix("")  # tesseract aggiunge .txt
            tess_cmd = [
                "tesseract",
                str(img),
                str(out_base),
                "-l",
                "ita",
                "--psm",
                "6",
            ]
            subprocess.run(tess_cmd, check=True, capture_output=True, text=True)
            txt_path = Path(f"{out_base}.txt")
            if txt_path.exists():
                pages_txt.append(txt_path.read_text(encoding="utf-8", errors="ignore"))
            else:
                pages_txt.append("")
        return "\f".join(pages_txt)


def normalize_spaces(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip()


def parse_amount_ita(raw: str) -> Optional[str]:
    # Esempi: "1 . 037 , 44" | "103 , 88" | "1.234,56"
    clean = raw.replace(" ", "")
    clean = clean.replace(".", "").replace(",", ".")
    try:
        return str(Decimal(clean).quantize(Decimal("0.01")))
    except Exception:
        return None


def extract_amount_by_labels(text: str, label_patterns: list[str]) -> Optional[str]:
    """
    Estrae importo monetario vicino all'etichetta.
    Regola principale: usa il valore più a destra (ultimo) nella riga/area,
    più affidabile sui layout cedolino multi-colonna.
    """
    amount_re = re.compile(r"([+-]?[0-9]{1,3}(?:[\s\.'\u00A0]?[0-9]{3})*,\s*[0-9]{2}|[+-]?[0-9]+,\s*[0-9]{2})")
    lines = [normalize_spaces(x) for x in (text or '').splitlines()]

    def _last_amount(raw: str, *, min_plausible: Decimal = Decimal("50")) -> Optional[str]:
        matches = amount_re.findall(raw or "")
        if not matches:
            return None
        # Sul cedolino il valore corretto è tipicamente in ultima colonna plausibile.
        for raw_amt in reversed(matches):
            val = parse_amount_ita(raw_amt)
            if val is None:
                continue
            try:
                if Decimal(val) >= min_plausible:
                    return val
            except Exception:
                continue
        return None

    # 1) stessa riga dell'etichetta
    for i, ln in enumerate(lines):
        if not ln:
            continue
        for pat in label_patterns:
            m_lbl = re.search(pat, ln, re.IGNORECASE)
            if not m_lbl:
                continue

            # Prima prova: dopo etichetta sulla stessa riga
            tail = ln[m_lbl.end():]
            val = _last_amount(tail)
            if val is not None:
                return val

            # Seconda prova: righe immediatamente successive (layout spezzato)
            for j in range(i + 1, min(i + 4, len(lines))):
                val_next = _last_amount(lines[j])
                if val_next is not None:
                    return val_next

    # 2) fallback testuale su finestra più ampia vicino all'etichetta
    for pat in label_patterns:
        m = re.search(pat + r"([\s\S]{0,260})", text or '', re.IGNORECASE)
        if not m:
            continue
        val = _last_amount(m.group(1))
        if val is not None:
            return val
    return None


def extract_period(text: str) -> tuple[Optional[int], Optional[int]]:
    """
    Periodo retributivo della singola busta (mese/anno di riferimento cedolino).
    Delega alla logica condivisa con archivio/ordinamento (TeamSystem: MESE RETRIBUITO).
    """
    try:
        from documenti.busta_periodo_da_pdf import estrai_mese_anno_da_testo_cedolino

        mese, anno = estrai_mese_anno_da_testo_cedolino(text or "")
        if mese and anno:
            return mese, anno
    except Exception:
        pass

    # Fallback legacy: MM/AAAA plausibile (esclude sottostringhe DD/MM/AAAA)
    up_text = (text or "").upper()
    pairs = re.findall(r"(?<!\d/)(0[1-9]|1[0-2])\s*/\s*(20\d{2})\b", up_text)
    plausible = [(int(mm), int(yy)) for mm, yy in pairs if int(yy) >= 2015]
    if plausible:
        freq = Counter(plausible)
        month, year = max(freq.items(), key=lambda kv: (kv[1], kv[0][1], kv[0][0]))[0]
        return month, year

    year_match = re.search(r"\b(20\d{2})\b", up_text)
    year = int(year_match.group(1)) if year_match else None
    month = None
    for m_name, m_num in sorted(MONTHS_IT.items(), key=lambda x: -len(x[0])):
        if re.search(rf"\b{m_name}\b", up_text) and year and year >= 2015:
            month = m_num
            break
    return month, year


def extract_cf(text: str) -> Optional[str]:
    m = re.search(r"\b([A-Z]{6}[0-9]{2}[A-Z][0-9]{2}[A-Z][0-9]{3}[A-Z])\b", text.upper())
    return m.group(1) if m else None


def extract_birth_date(text: str) -> Optional[str]:
    m = re.search(r"DATA\s*DI\s*NAS\.?", text, re.IGNORECASE)
    if not m:
        return None
    tail = text[m.end(): m.end() + 500]
    d = re.search(r"([0-9]{1,2}\s*/\s*[0-9]{1,2}\s*/\s*[0-9]{2,4})", tail)
    if not d:
        return None
    return normalize_spaces(d.group(1)).replace(" / ", "/")


def _parse_date_raw(text_fragment: str) -> Optional[str]:
    """Estrae la prima data DD/MM/YYYY (o varianti con spazi) da un frammento di testo."""
    # Accetta sia "01/01/2020" sia "0 1 / 0 1 / 2 0 2 0" (PDF con spaziatura)
    m = re.search(
        r"(\d{1,2})\s*/\s*(\d{1,2})\s*/\s*(\d{2,4})",
        text_fragment,
    )
    if not m:
        return None
    d_s, mo_s, y_s = m.group(1), m.group(2), m.group(3)
    try:
        d_v = int(d_s)
        mo_v = int(mo_s)
        y_v = int(y_s)
        if y_v < 100:
            y_v = 2000 + y_v if y_v <= 30 else 1900 + y_v
        if 1 <= d_v <= 31 and 1 <= mo_v <= 12 and y_v >= 1940:
            return f"{d_v:02d}/{mo_v:02d}/{y_v:04d}"
    except Exception:
        pass
    return None


def _extract_all_dates_with_pos(text: str) -> list[tuple[int, str]]:
    """Restituisce tutte le date trovate come (posizione, 'DD/MM/YYYY')."""
    out: list[tuple[int, str]] = []
    for m in re.finditer(r"\d{1,2}\s*/\s*\d{1,2}\s*/\s*\d{2,4}", text or ""):
        d = _parse_date_raw(m.group(0))
        if d:
            out.append((m.start(), d))
    return out


def _pick_data_assunzione(candidates: list[str], birth: Optional[str], birth_year: Optional[int]) -> Optional[str]:
    if not candidates:
        return None

    current_year = date.today().year

    for d in candidates:
        if birth and d == birth:
            continue
        try:
            y = int(d.split("/")[-1])
        except Exception:
            continue
        if 2000 <= y <= (current_year + 1):
            return d

    for d in candidates:
        if birth and d == birth:
            continue
        try:
            y = int(d.split("/")[-1])
        except Exception:
            y = None
        if y is not None:
            if birth_year is not None and y < (birth_year + 14):
                continue
            if y < 1980 or y > 2100:
                continue
        return d
    for d in candidates:
        if not birth or d != birth:
            return d
    return candidates[0]


def extract_data_assunzione_conv(text: str) -> Optional[str]:
    """Estrae DATA ASS. CONV. (data assunzione convenzionale) dalla busta paga."""
    upper = text.upper()
    birth = extract_birth_date(text)
    birth_year = None
    if birth:
        try:
            birth_year = int(birth.split("/")[-1])
        except Exception:
            birth_year = None
    # Pattern principali trovabili nei cedolini FIPE/Turismo
    patterns = [
        r"DATA\s+ASS\.?\s*CONV\.?",
        r"DATA\s+ASSUNZ\.?",
        r"DATA\s+ASS\.\s+CONTR\.?",
        r"DATA\s+ASSUNZIONE\s+CONV\.?",
        r"DATA\s+ASSUNZIONE",
        r"DATA\s+INIZIO\s+RAPPORTO",
        r"ASSUNTO\s+(?:IL|DAL|DAI?)\s",
    ]
    for pat in patterns:
        m = re.search(pat, upper)
        if not m:
            continue

        # Nei cedolini reali la riga dati può essere molto lontana dall'intestazione.
        tail = text[m.end(): m.end() + 3500]
        selected = _pick_data_assunzione([d for _, d in _extract_all_dates_with_pos(tail)], birth, birth_year)
        if selected:
            return selected

    # Fallback: cerca una data plausibile vicino al codice fiscale (anagrafica cedolino).
    cf = extract_cf(text)
    if cf:
        pos_cf = upper.find(cf)
        if pos_cf >= 0:
            start = max(0, pos_cf - 500)
            end = min(len(text), pos_cf + 900)
            window = text[start:end]
            selected = _pick_data_assunzione([d for _, d in _extract_all_dates_with_pos(window)], birth, birth_year)
            if selected:
                return selected
    return None


def extract_data_cessazione(text: str) -> Optional[str]:
    """Estrae DATA CESSAZIONE dalla busta paga (presente solo nelle mensilità di fine rapporto)."""
    upper = text.upper()
    patterns = [
        r"DATA\s+CESSAZIONE",
        r"DATA\s+FINE\s+RAPPORTO",
        r"CESSAZIONE\s+(?:AL|IL|DAL|DEL)\s",
        r"DATA\s+TERM\.?\s*RAPPORTO",
    ]
    for pat in patterns:
        m = re.search(pat, upper)
        if not m:
            continue
        tail = text[m.end(): m.end() + 120]
        parsed = _parse_date_raw(tail)
        if parsed:
            return parsed

    # Fallback per varianti tipiche di stampa (es. CESS. RAPP. 31/12/2025)
    m2 = re.search(r"CESS\.?\s*RAPP\.?[^\d]{0,20}(\d{1,2}\s*/\s*\d{1,2}\s*/\s*\d{2,4})", upper)
    if m2:
        parsed = _parse_date_raw(m2.group(1))
        if parsed:
            return parsed
    return None


def _normalize_name_tokens(raw_name: str) -> str:
    tokens = [t for t in raw_name.strip().split() if t]
    if not tokens:
        return raw_name

    merged: list[str] = []
    for t in tokens:
        if merged and len(t) <= 2 and t.isalpha():
            merged[-1] = merged[-1] + t
        else:
            merged.append(t)
    return " ".join(merged)


def extract_name(text: str) -> Optional[str]:
    upper = text.upper()

    # 1) Tra "COGNOME E NOME" e "CODICE FISCALE"
    m = re.search(
        r"COGNOME\s+E\s+NOME(?P<body>[\s\S]{0,240}?)CODICE\s+FISCALE",
        upper,
        re.IGNORECASE,
    )
    if m:
        body = m.group("body")
        lines = [normalize_spaces(x) for x in body.splitlines() if normalize_spaces(x)]
        for ln in lines:
            if re.fullmatch(r"[A-Z'\- ]{4,}", ln):
                words = [w for w in ln.split() if w not in STOP_NAME_WORDS and len(w) > 1]
                if len(words) >= 2:
                    return _normalize_name_tokens(" ".join(words[:4])).title()

    # 1b) Riga principale con nome prima della data assunzione
    # Esempio: ... 1 CARDELLA MASS I MO 29 / 05 / 20 ...
    for ln in text.splitlines():
        line = normalize_spaces(ln.upper())
        if "COGNOME E NOME" in line:
            continue
        m2 = re.search(r"\b([A-Z' ]{6,}?)\s+([0-9]{1,2}\s*/\s*[0-9]{1,2}\s*/\s*[0-9]{2,4})\b", line)
        if m2:
            candidate = m2.group(1)
            candidate = re.sub(r"\b\d+\b", " ", candidate)
            words = [w for w in candidate.split() if w not in STOP_NAME_WORDS and len(w) > 1]
            if len(words) >= 2:
                return _normalize_name_tokens(" ".join(words[-5:])).title()

    # 2) Fallback: riga tutta maiuscola stile nome cognome
    for ln in upper.splitlines():
        ln = normalize_spaces(ln)
        if not ln:
            continue
        if re.fullmatch(r"[A-Z'\- ]{6,}", ln):
            words = [w for w in ln.split() if w not in STOP_NAME_WORDS and len(w) > 1]
            if len(words) == 2:
                return _normalize_name_tokens(" ".join(words)).title()

    return None


def extract_netto(text: str) -> Optional[str]:
    return extract_amount_by_labels(text, [
        r"NETTO\s+BUSTA",
        r"NETTO\s+DA\s+PAGARE",
        r"NETTO\s+CORRISPOSTO",
    ])


def extract_lordo(text: str) -> Optional[str]:
    return extract_amount_by_labels(text, [
        r"TOTALE\s+LORDO",
        r"TOT\.?\s*LORDO",
        r"RETRIBUZIONE\s+LORDA",
    ])


def extract_f24_importo(text: str) -> Optional[str]:
    up = text.upper()

    # 1) pattern prioritari sul totale da versare
    keyword_patterns = [
        r"SALDO\s*\(A-B\)",
        r"IMPORTO\s+DA\s+VERSARE",
        r"TOTALE\s+DEBITO",
        r"TOTALE\s+DA\s+VERSARE",
        r"IMPORTI\s+A\s+DEBITO\s+VERSATI",
    ]
    for kp in keyword_patterns:
        for m in re.finditer(kp + r"[\s\S]{0,140}?([0-9][0-9\s\.,]{1,20},\s*[0-9]{2})", up, re.IGNORECASE):
            parsed = parse_amount_ita(m.group(1))
            if parsed is not None:
                return parsed

    # 2) fallback: massimo importo monetario presente nella pagina F24
    all_amounts = re.findall(r"([0-9][0-9\s\.,]{1,20},\s*[0-9]{2})", up)
    parsed_vals = []
    for raw in all_amounts:
        p = parse_amount_ita(raw)
        if p is None:
            continue
        try:
            parsed_vals.append(Decimal(p))
        except Exception:
            continue
    if parsed_vals:
        return str(max(parsed_vals).quantize(Decimal("0.01")))
    return None


def classify_page(text: str) -> str:
    up = text.upper()
    if "MOD. F24" in up or "DELEGA IRREVOCABILE" in up:
        return "F24"
    if "NETTO BUSTA" in up and "COGNOME E NOME" in up:
        return "BUSTA"
    if "COGNOME E NOME" in up and (
        "TOTALE LORDO" in up
        or "RETRIBUZIONE LORDA" in up
        or "NETTO DA PAGARE" in up
        or "NETTO CORRISPOSTO" in up
    ):
        return "BUSTA"
    return "ALTRO"


def analyze_pdf(pdf_path: Path) -> dict:
    def _parse_pages(raw_content: str) -> list[PageResult]:
        pages = (raw_content or "").split("\f")
        out: list[PageResult] = []
        for idx, raw_page in enumerate(pages, start=1):
            text = raw_page or ""
            kind = classify_page(text)
            month, year = extract_period(text)
            result = PageResult(
                page=idx,
                kind=kind,
                cf=extract_cf(text) if kind == "BUSTA" else None,
                full_name=extract_name(text) if kind == "BUSTA" else None,
                birth_date=extract_birth_date(text) if kind == "BUSTA" else None,
                lordo_busta=extract_lordo(text) if kind == "BUSTA" else None,
                netto_busta=extract_netto(text) if kind == "BUSTA" else None,
                f24_importo=extract_f24_importo(text) if kind == "F24" else None,
                period_month=month,
                period_year=year,
                data_assunzione_conv=extract_data_assunzione_conv(text) if kind == "BUSTA" else None,
                data_cessazione=extract_data_cessazione(text) if kind == "BUSTA" else None,
            )
            if result.kind == "BUSTA" and not result.cf and not result.netto_busta:
                result.kind = "ALTRO"
            # evita l'ultima pagina vuota dopo split su form-feed
            if idx == len(pages) and not normalize_spaces(text):
                continue
            out.append(result)
        return out

    content = run_pdftotext_layout(pdf_path)
    page_results = _parse_pages(content)

    # OCR fallback aggressivo:
    # - se testo quasi vuoto, oppure
    # - se non riconosciamo alcuna pagina BUSTA/F24 (caso tipico OCR necessario).
    if (
        len(re.sub(r"\s+", "", content or "")) < 80
        or not any(p.kind in {"BUSTA", "F24"} for p in page_results)
    ):
        try:
            ocr_content = run_ocr_fallback(pdf_path)
            ocr_results = _parse_pages(ocr_content)
            if any(p.kind in {"BUSTA", "F24"} for p in ocr_results):
                page_results = ocr_results
                content = ocr_content
        except Exception:
            pass

    # Fallback da nome file (es: "1 GENNAIO 2024.pdf")
    file_upper = pdf_path.name.upper().replace("_", " ")
    fallback_year = None
    fallback_month = None
    y_m = re.search(r"\b(20\d{2})\b", file_upper)
    if y_m:
        fallback_year = int(y_m.group(1))
    for m_name, m_num in MONTHS_IT.items():
        if m_name in file_upper:
            fallback_month = m_num
            break

    # Consolida periodo sulle buste (evita 2009 da intestazioni autorizzative)
    buste_periods = [
        (r.period_month, r.period_year)
        for r in page_results
        if r.kind == "BUSTA" and r.period_year and r.period_year >= 2015
    ]
    dominant_month = fallback_month
    dominant_year = fallback_year
    if buste_periods:
        # prendi il periodo più frequente
        freq = {}
        for p in buste_periods:
            freq[p] = freq.get(p, 0) + 1
        dominant_month, dominant_year = max(freq.items(), key=lambda kv: kv[1])[0]

    for r in page_results:
        if r.kind == "BUSTA":
            # Anno spurio (es. 01/2009 da «27/01/2009»): azzera anche il mese errato.
            if not r.period_year or r.period_year < 2015:
                r.period_year = dominant_year
                r.period_month = dominant_month
            elif not r.period_month and dominant_month:
                r.period_month = dominant_month
        elif r.kind == "F24":
            if not r.period_year or r.period_year < 2015:
                r.period_year = dominant_year
            if not r.period_month and dominant_month:
                r.period_month = dominant_month

    buste = [r for r in page_results if r.kind == "BUSTA"]
    f24 = [r for r in page_results if r.kind == "F24"]

    # Lordo/netto: motore v4 sulla singola pagina (affidabile su TeamSystem a colonne).
    try:
        _ensure_django()
        from documenti.busta_importi_estrazione import lordo_netto_stringhe_per_pagina

        for r in buste:
            netto_s, lordo_s = lordo_netto_stringhe_per_pagina(pdf_path, r.page)
            if lordo_s:
                r.lordo_busta = lordo_s
            if netto_s:
                r.netto_busta = netto_s
    except Exception:
        pass

    # deduplica preliminare per CF+periodo (utile per doppie copie)
    unique_key = set()
    unique_buste = []
    duplicates = []
    for b in buste:
        key = (b.cf, b.period_month, b.period_year)
        if key in unique_key:
            duplicates.append(b.page)
        else:
            unique_key.add(key)
            unique_buste.append(b)

    return {
        "file": str(pdf_path),
        "pages_total": len(page_results),
        "counts": {
            "buste": len(buste),
            "f24": len(f24),
            "altro": len([r for r in page_results if r.kind == "ALTRO"]),
            "buste_uniche": len(unique_buste),
            "buste_duplicate_pagine": duplicates,
        },
        "records": [asdict(x) for x in page_results],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Analizza PDF paghe/CUD/F24 e produce report JSON")
    parser.add_argument("pdf", help="Percorso file PDF da analizzare")
    parser.add_argument("--out", help="Percorso output JSON", default="")
    args = parser.parse_args()

    pdf_path = Path(args.pdf)
    if not pdf_path.exists():
        raise SystemExit(f"File non trovato: {pdf_path}")

    report = analyze_pdf(pdf_path)

    if args.out:
        out_path = Path(args.out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"Report scritto in: {out_path}")
    else:
        print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
