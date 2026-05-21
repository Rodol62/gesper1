"""
Estrazione TOTALE LORDO / NETTO BUSTA da cedolini TeamSystem via pdfplumber.

Layout tipico: etichetta in testa alla colonna, importo sulla riga immediatamente sotto
(es. «TOTALE LORDO» → ``1.745,16``; «NETTO BUSTA» → ``1.339,00``).
"""

from __future__ import annotations

import io
import re
from decimal import Decimal
from pathlib import Path
from typing import BinaryIO, Union

from documenti.buste_pdf_passwords import passwords_for_busta_pdf_read

PdfSource = Union[str, Path, bytes, BinaryIO]

_AMOUNT_RE = re.compile(r"^-?\d{1,3}(?:[.,\u00A0]\d{3})*[.,]\d{2}$")
# Colonne X (centro parola) sul layout Punto Zero / TeamSystem busta singola
_X_LORDO = (30.0, 95.0)
_X_NETTO = (348.0, 425.0)


def _parse_importo_ita(token: str) -> Decimal | None:
    if not token:
        return None
    t = token.replace("\u00a0", "").replace(" ", "")
    if "," in t and "." in t:
        t = t.replace(".", "").replace(",", ".")
    elif "," in t:
        t = t.replace(",", ".")
    try:
        return Decimal(t).quantize(Decimal("0.01"))
    except Exception:
        return None


def _find_below_label(
    words: list[dict],
    label: str,
    x_band: tuple[float, float],
    *,
    gap_max: float = 25.0,
    min_val: Decimal = Decimal("50"),
) -> Decimal | None:
    """Importo sotto l'etichetta, nella banda orizzontale ``x_band``."""
    parts = label.upper().split()
    if not parts:
        return None

    for i, w in enumerate(words):
        if w.get("text", "").upper() != parts[0]:
            continue
        if not all(
            i + j < len(words) and words[i + j].get("text", "").upper() == parts[j]
            for j in range(len(parts))
        ):
            continue

        first_w = words[i]
        last_w = words[i + len(parts) - 1]
        label_bottom = max(float(first_w.get("bottom", 0)), float(last_w.get("bottom", 0)))

        candidates: list[tuple[float, Decimal]] = []
        for cw in words:
            token = cw.get("text", "")
            if not _AMOUNT_RE.match(token):
                continue
            gap = float(cw.get("top", 0)) - label_bottom
            if gap < 0 or gap > gap_max:
                continue
            cx = (float(cw.get("x0", 0)) + float(cw.get("x1", 0))) / 2
            if not (x_band[0] <= cx <= x_band[1]):
                continue
            val = _parse_importo_ita(token)
            if val is None or val < min_val:
                continue
            candidates.append((gap, val))

        if candidates:
            # Prima riga sotto l'etichetta; a parità di gap, importo più alto (evita 0,50).
            candidates.sort(key=lambda t: (t[0], -t[1]))
            return candidates[0][1]

    return None


def _estrai_da_page_pdfplumber(page) -> tuple[Decimal | None, Decimal | None]:
    words = page.extract_words(keep_blank_chars=False) or []
    if not words:
        return None, None
    lordo = _find_below_label(words, "TOTALE LORDO", _X_LORDO, gap_max=18, min_val=Decimal("100"))
    netto = _find_below_label(words, "NETTO BUSTA", _X_NETTO, gap_max=25, min_val=Decimal("100"))
    if netto is None:
        for alt in ("NETTO DA PAGARE", "NETTO CORRISPOSTO"):
            netto = _find_below_label(words, alt, _X_NETTO, gap_max=25, min_val=Decimal("100"))
            if netto is not None:
                break
    return netto, lordo


def _leggi_bytes(source: PdfSource) -> bytes | None:
    if isinstance(source, (str, Path)):
        p = Path(source)
        return p.read_bytes() if p.is_file() else None
    if isinstance(source, bytes):
        return source
    if hasattr(source, "read"):
        return source.read()
    return None


def estrai_lordo_netto_pdfplumber_pagina(
    source: PdfSource,
    page_num: int = 1,
) -> tuple[Decimal | None, Decimal | None]:
    """
    (netto, lordo) dalla pagina ``page_num`` (1-based) con pdfplumber.
    """
    raw = _leggi_bytes(source)
    if not raw or page_num < 1:
        return None, None

    try:
        import pdfplumber
    except ImportError:
        return None, None

    for pwd in passwords_for_busta_pdf_read():
        try:
            with pdfplumber.open(io.BytesIO(raw), password=pwd) as pdf:
                if page_num > len(pdf.pages):
                    return None, None
                return _estrai_da_page_pdfplumber(pdf.pages[page_num - 1])
        except Exception:
            continue
    return None, None


def estrai_lordo_netto_pdfplumber_monopagina(source: PdfSource) -> tuple[Decimal | None, Decimal | None]:
    """Alias per PDF busta già isolata (una sola pagina)."""
    return estrai_lordo_netto_pdfplumber_pagina(source, 1)
