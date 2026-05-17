"""
Compatibilità import: la logica Cedolino (regex Claude / TeamSystem) vive in
`leggi_busta_paga_claude.py`. Usare quel modulo per nuovo codice.
"""

from __future__ import annotations

from .leggi_busta_paga_claude import (
    SourcePdf,
    estrai_testo_prima_pagina,
    report_cedolino_senza_azienda,
    report_e_testo_prima_pagina,
    render_report_testo,
)

__all__ = [
    "SourcePdf",
    "estrai_testo_prima_pagina",
    "report_cedolino_senza_azienda",
    "report_e_testo_prima_pagina",
    "render_report_testo",
]
