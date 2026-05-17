"""
Natura busta (ordinaria / 13ª / 14ª) allineata a ``accounts.MovimentoImportPaghe.natura_busta``.

Usata per chiavi logiche ``CedolinoMotoreV4`` e conciliazione quando più PDF condividono lo
stesso mese/anno calendario: tipicamente **dicembre** (ordinaria + cedolino **13ª** separato)
e **luglio** (ordinaria + cedolino **14ª** separato, competenza CCNL FIPE luglio–giugno).
"""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from documenti.models import Documento


def _report_blob_upper(report: dict | None) -> str:
    """
    Testo aggregato (maiuscolo) da report lettura / v4: intestazioni, totali, voci.
    Serve a riconoscere 13ª/14ª quando il PDF non ha le parole chiave nel nome file ma sì
    nelle voci o nei totali (es. liquidazione quattordicesima in busta di luglio).
    """
    if not report or not isinstance(report, dict):
        return ""
    chunks: list[str] = []
    for key in ("tipo_cedolino", "descrizione", "titolo"):
        v = report.get(key)
        if isinstance(v, str) and v.strip():
            chunks.append(v.upper())
    for sec in ("dati_dipendente", "totali_mensili", "retribuzione_base", "irpef_addizionali"):
        block = report.get(sec)
        if isinstance(block, dict):
            for v in block.values():
                if isinstance(v, str) and v.strip():
                    chunks.append(v.upper())
    for voce in report.get("voci_retributive") or []:
        if isinstance(voce, dict):
            for k in ("descrizione", "codice", "tipo", "categoria"):
                v = voce.get(k)
                if isinstance(v, str) and v.strip():
                    chunks.append(v.upper())
    return " ".join(chunks)


def infer_natura_busta_per_busta(
    *,
    documento: Documento | None = None,
    report: dict | None = None,
    tipo_cedolino_motore: str | None = None,
) -> str:
    """
    Ritorna ORDINARIA | TREDICESIMA | QUATTORDICESIMA.

    Precedenza: nome file e descrizione documento (come import massivo PDF), poi tipo dal motore v4,
    poi contenuto testuale del report (voci/totali; **QUATTORDICESIMA** prima di **TREDICESIMA**
    se compaiono entrambe), infine ``tipo_cedolino`` sintetico del report.
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
        blob_r = _report_blob_upper(report)
        if "QUATTORDICESIMA" in blob_r:
            return "QUATTORDICESIMA"
        if "TREDICESIMA" in blob_r:
            return "TREDICESIMA"
        tr = (report.get("tipo_cedolino") or "").upper()
        if "QUATTORDICESIMA" in tr:
            return "QUATTORDICESIMA"
        if "TREDICESIMA" in tr:
            return "TREDICESIMA"

    return "ORDINARIA"
