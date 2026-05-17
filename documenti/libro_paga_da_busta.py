"""
Libro Unico del Lavoro: dettaglio riga da busta PDF.

Sovrascrive l’estrazione posizionale legacy (``estrai_busta_dettaglio_libro_paga_da_pdf``)
con :func:`documenti.busta_acquisizione.acquisisci_busta_da_documento`. Con motore v4,
**lordo F3** e **netto F9** provengono da ``calc`` (totalizzatori delle formule del cedolino);
contributi INPS dip., IRPEF, addizionali, ore INPS e TFR mese dai campi letti sul PDF (``Cedolino``),
allineati alla conciliazione — così non restano importi errati (es. 4 €) ereditati dall’euristica legacy.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from documenti.busta_acquisizione import acquisisci_busta_da_documento
from documenti.cedolino_confronto_import import (
    applica_totalizzatori_calcolo_v4_a_libro,
    arricchisci_dettaglio_libro_da_cedolino_v4,
    dettaglio_libro_paga_da_report,
)

if TYPE_CHECKING:
    from documenti.models import Documento


def merge_dettaglio_libro_paga_per_documento(doc: Documento) -> dict[str, Any]:
    # Import lazy: ``documenti.views`` è pesante e non va importato a livello modulo qui.
    from documenti.views import estrai_busta_dettaglio_libro_paga_da_pdf

    leg = estrai_busta_dettaglio_libro_paga_da_pdf(doc) or {}
    res = acquisisci_busta_da_documento(doc)
    merged = dict(leg)
    if res.errore is not None or not res.report:
        return merged
    canon = dettaglio_libro_paga_da_report(res.report)
    if res.cedolino_v4 is not None and res.calc_v4 is not None:
        applica_totalizzatori_calcolo_v4_a_libro(canon, res.calc_v4, res.cedolino_v4)
    elif res.cedolino_v4 is not None:
        arricchisci_dettaglio_libro_da_cedolino_v4(canon, res.cedolino_v4)
    for k, v in canon.items():
        if v is not None:
            merged[k] = v
    return merged
