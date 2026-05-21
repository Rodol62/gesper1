"""
Estrazione TOTALE LORDO / NETTO BUSTA da una singola pagina di cedolino PDF.

Ordine:
1. pdfplumber sotto etichetta (affidabile su TeamSystem Punto Zero)
2. motore posizionale v4 (fallback)
"""

from __future__ import annotations

import io
from decimal import Decimal
from pathlib import Path
from typing import BinaryIO, Union

from documenti.busta_acquisizione import acquisisci_busta_pdf_bytes
from documenti.busta_importi_pdfplumber import estrai_lordo_netto_pdfplumber_pagina
from documenti.buste_pdf_passwords import passwords_for_busta_pdf_read

PdfSource = Union[str, Path, bytes, BinaryIO]


def _leggi_bytes_sorgente(source: PdfSource) -> bytes | None:
    if isinstance(source, (str, Path)):
        p = Path(source)
        if not p.is_file():
            return None
        return p.read_bytes()
    if isinstance(source, bytes):
        return source
    if hasattr(source, "read"):
        return source.read()
    return None


def _bytes_pagina_singola(raw: bytes, page_num: int, *, password: str = "") -> bytes | None:
    """Isola una pagina del PDF in un buffer monopagina."""
    if not raw or page_num < 1:
        return None
    try:
        from pypdf import PdfReader, PdfWriter
    except ImportError:
        return None
    try:
        reader = PdfReader(io.BytesIO(raw))
        if getattr(reader, "is_encrypted", False):
            try:
                reader.decrypt(password or "")
            except Exception:
                return None
        idx = page_num - 1
        if idx >= len(reader.pages):
            return None
        writer = PdfWriter()
        writer.add_page(reader.pages[idx])
        out = io.BytesIO()
        writer.write(out)
        return out.getvalue()
    except Exception:
        return None


def estrai_lordo_netto_da_pdf_pagina(
    source: PdfSource,
    page_num: int,
) -> tuple[Decimal | None, Decimal | None]:
    """
    Restituisce (netto, lordo) dalla pagina ``page_num`` (1-based) del PDF sorgente.
    """
    raw = _leggi_bytes_sorgente(source)
    if not raw:
        return None, None

    # 1) pdfplumber sulla pagina del file intero (coordinate originali)
    netto, lordo = estrai_lordo_netto_pdfplumber_pagina(raw, page_num)
    if netto is not None and lordo is not None:
        return netto, lordo

    # 2) v4 sulla pagina isolata
    for pwd in passwords_for_busta_pdf_read():
        page_raw = _bytes_pagina_singola(raw, page_num, password=pwd)
        if not page_raw:
            continue
        res = acquisisci_busta_pdf_bytes(
            page_raw,
            password=pwd,
            file_label=f"pagina_{page_num}",
        )
        if res.errore is None:
            n_v4, l_v4 = res.netto, res.lordo
            netto = netto if netto is not None else n_v4
            lordo = lordo if lordo is not None else l_v4
            if netto is not None or lordo is not None:
                return netto, lordo

    return netto, lordo


def lordo_netto_stringhe_per_pagina(
    source: PdfSource,
    page_num: int,
) -> tuple[str | None, str | None]:
    """Come :func:`estrai_lordo_netto_da_pdf_pagina` ma stringhe ``0.00`` per JSON import."""
    netto, lordo = estrai_lordo_netto_da_pdf_pagina(source, page_num)
    ns = str(netto.quantize(Decimal("0.01"))) if netto is not None else None
    ls = str(lordo.quantize(Decimal("0.01"))) if lordo is not None else None
    return ns, ls
