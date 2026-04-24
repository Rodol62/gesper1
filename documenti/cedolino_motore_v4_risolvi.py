"""
Risoluzione coerente di :class:`documenti.models.CedolinoMotoreV4` rispetto a un ``Documento`` busta.

Regola: prima il record legato a **questo** file (``documento_id``), poi il fallback per
``(dipendente, mese, anno, natura_busta)`` se il record non è già attribuito a un altro documento.
La natura (ordinaria / 13ª / 14ª) distingue più PDF nello stesso mese calendario.
"""

from __future__ import annotations

from collections import defaultdict
from typing import TYPE_CHECKING

from django.db.models import Q

from documenti.models import CedolinoMotoreV4

if TYPE_CHECKING:
    from documenti.models import Documento

ChiavePeriodoV4 = tuple[int, int, int, str]  # (dipendente_id, mese, anno, natura_busta)


def mappa_cedolini_v4_per_periodi(
    chiavi: list[ChiavePeriodoV4],
) -> dict[ChiavePeriodoV4, CedolinoMotoreV4]:
    """Precarica i ``CedolinoMotoreV4`` per le chiavi (dipendente_id, mese, anno, natura)."""
    if not chiavi:
        return {}
    q = Q()
    for dip_id, mese_k, yper_k, natura_k in chiavi:
        q |= Q(
            dipendente_id=dip_id,
            mese=mese_k,
            anno=yper_k,
            natura_busta=natura_k,
        )
    out: dict[ChiavePeriodoV4, CedolinoMotoreV4] = {}
    qs = (
        CedolinoMotoreV4.objects.filter(q)
        .select_related("dipendente", "documento")
        .prefetch_related("voci")
    )
    for row in qs:
        out[(row.dipendente_id, row.mese, row.anno, row.natura_busta)] = row
    return out


def cedolini_v4_tutti_per_periodi(
    chiavi: list[ChiavePeriodoV4],
) -> dict[ChiavePeriodoV4, list[CedolinoMotoreV4]]:
    """
    Tutte le righe ``CedolinoMotoreV4`` per ciascuna chiave (dipendente, mese, anno, natura),
    ordinate per ``id`` decrescente.
    """
    if not chiavi:
        return {}
    q = Q()
    for dip_id, mese_k, yper_k, natura_k in chiavi:
        q |= Q(
            dipendente_id=dip_id,
            mese=mese_k,
            anno=yper_k,
            natura_busta=natura_k,
        )
    mp: dict[ChiavePeriodoV4, list[CedolinoMotoreV4]] = defaultdict(list)
    qs = (
        CedolinoMotoreV4.objects.filter(q)
        .select_related("dipendente", "documento")
        .order_by("-id")
    )
    for row in qs:
        mp[(row.dipendente_id, row.mese, row.anno, row.natura_busta)].append(row)
    return dict(mp)


def mappa_cedolini_v4_per_documenti(
    documento_ids: list[int],
) -> dict[int, CedolinoMotoreV4]:
    """
    Ultima estrazione v4 collegata a ciascun documento busta (evita mismatch se la
    descrizione file ha mese/anno diversi dal PDF).
    """
    ids = [i for i in documento_ids if i]
    if not ids:
        return {}
    out: dict[int, CedolinoMotoreV4] = {}
    qs = (
        CedolinoMotoreV4.objects.filter(documento_id__in=ids)
        .select_related("dipendente", "documento")
        .prefetch_related("voci")
        .order_by("-id")
    )
    for row in qs:
        did = row.documento_id
        if did and did not in out:
            out[did] = row
    return out


def risolvi_cedolino_motore_v4_per_documento_busta(
    doc: Documento,
    mese: int | None,
    anno: int | None,
    *,
    natura_busta: str = "ORDINARIA",
    cache_periodo: dict[ChiavePeriodoV4, CedolinoMotoreV4] | None = None,
    cache_documento: dict[int, CedolinoMotoreV4] | None = None,
) -> CedolinoMotoreV4 | None:
    """
    Restituisce l'estrazione v4 da usare per la riga elenco / conciliazione di *questa* busta.

    1. ``documento_id == doc.pk``
    2. Altrimenti, record per ``(dipendente, mese, anno, natura_busta)`` se ``documento_id`` è
       ``NULL`` o coincide con ``doc``.
    """
    dip = getattr(doc, "dipendente", None)
    if dip is None:
        return None

    natura = (natura_busta or "ORDINARIA").strip().upper() or "ORDINARIA"
    if natura not in {"ORDINARIA", "TREDICESIMA", "QUATTORDICESIMA"}:
        natura = "ORDINARIA"

    if cache_documento is not None:
        hit = cache_documento.get(doc.id)
        if hit is not None and hit.dipendente_id == dip.id:
            return hit

    row = (
        CedolinoMotoreV4.objects.filter(documento_id=doc.id)
        .select_related("dipendente", "documento")
        .prefetch_related("voci")
        .first()
    )
    if row is not None:
        return row

    def _accetta_candidato(cand: CedolinoMotoreV4 | None) -> CedolinoMotoreV4 | None:
        if cand is None:
            return None
        if cand.documento_id is None or cand.documento_id == doc.id:
            return cand
        return None

    if mese and anno:
        if cache_periodo is not None:
            cand = cache_periodo.get((dip.id, mese, anno, natura))
            got = _accetta_candidato(cand)
            if got is not None:
                return got
        cand = (
            CedolinoMotoreV4.objects.filter(
                dipendente_id=dip.id,
                mese=mese,
                anno=anno,
                natura_busta=natura,
            )
            .select_related("dipendente", "documento")
            .prefetch_related("voci")
            .first()
        )
        return _accetta_candidato(cand)

    return None
