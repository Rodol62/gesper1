"""
Natura busta (ordinaria / 13ª / 14ª) allineata a ``accounts.MovimentoImportPaghe.natura_busta``.
Usata per chiavi logiche ``CedolinoMotoreV4`` e conciliazione quando più PDF condividono mese/anno.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from documenti.models import Documento


def infer_natura_busta_per_busta(
    *,
    documento: Documento | None = None,
    report: dict | None = None,
    tipo_cedolino_motore: str | None = None,
) -> str:
    """
    Ritorna ORDINARIA | TREDICESIMA | QUATTORDICESIMA.

    Precedenza: nome file e descrizione documento (come import massivo PDF), poi tipo dal motore v4,
    poi tipo nel report di lettura.
    """
    blob_doc = ""
    if documento:
        blob_doc = f"{(documento.nome_file() or '').upper()} {(documento.descrizione or '').upper()}"
    if "QUATTORDICESIMA" in blob_doc:
        return "QUATTORDICESIMA"
    if "TREDICESIMA" in blob_doc:
        return "TREDICESIMA"

    tc_m = (tipo_cedolino_motore or "").upper()
    if "QUATTORDICESIMA" in tc_m:
        return "QUATTORDICESIMA"
    if "TREDICESIMA" in tc_m:
        return "TREDICESIMA"

    if report:
        tr = (report.get("tipo_cedolino") or "").upper()
        if "QUATTORDICESIMA" in tr:
            return "QUATTORDICESIMA"
        if "TREDICESIMA" in tr:
            return "TREDICESIMA"

    return "ORDINARIA"
