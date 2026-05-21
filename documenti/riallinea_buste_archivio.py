"""
Riallineamento massivo buste in archivio: periodo retributivo e importi netto/lordo da PDF.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from accounts.models import MovimentoImportPaghe
from documenti.busta_acquisizione import acquisisci_busta_da_documento, leggi_pdf_busta_documento
from documenti.buste_cedolino_batch import parse_periodo_busta, periodo_retributivo_effettivo
from documenti.cedolino_estrazione_v4_store import tenta_persistenza_cedolino_v4_dopo_lettura
from documenti.models import Documento


@dataclass
class EsitoRiallineaBusta:
    documento_id: int
    ok: bool = False
    creato_movimento: bool = False
    aggiornato_movimento: bool = False
    aggiornato_v4: bool = False
    errore: str = ""
    mese: int | None = None
    anno: int | None = None
    netto: Decimal | None = None
    lordo: Decimal | None = None
    motore: str = ""


@dataclass
class RiepilogoRiallineaBuste:
    totale: int = 0
    ok: int = 0
    errori: int = 0
    senza_periodo: int = 0
    senza_pdf: int = 0
    movimenti_creati: int = 0
    movimenti_aggiornati: int = 0
    v4_aggiornati: int = 0
    dettaglio_errori: list[str] = field(default_factory=list)


def queryset_buste_archivio(*, azienda_id: int | None = None, anno: int | None = None):
    """Tutte le buste paga con file in archivio."""
    qs = (
        Documento.objects.filter(tipo="busta_paga")
        .exclude(file="")
        .select_related("dipendente", "azienda")
        .order_by("azienda_id", "dipendente__cognome", "id")
    )
    if azienda_id:
        qs = qs.filter(azienda_id=azienda_id)
    if anno:
        doc_ids_mov = MovimentoImportPaghe.objects.filter(
            tipo="BUSTA", anno=anno, documento_id__isnull=False
        ).values_list("documento_id", flat=True)
        from django.db.models import Q

        qs = qs.filter(Q(id__in=doc_ids_mov) | Q(data_caricamento__year=anno))
    return qs


def riallinea_documento_busta(
    doc: Documento,
    *,
    forza: bool = True,
    persisti_v4: bool = True,
    dry_run: bool = False,
) -> EsitoRiallineaBusta:
    """Rilegge il PDF e allinea ``MovimentoImportPaghe`` (e opz. CedolinoMotoreV4)."""
    esito = EsitoRiallineaBusta(documento_id=doc.pk)

    raw = leggi_pdf_busta_documento(doc)
    if not raw or len(raw) < 5 or raw[:5] != b"%PDF-":
        # Fallback: descrizione / nome file (es. busta_01_2026) se storage.exists fallisce
        # ma il file è leggibile con risoluzione path alternativa.
        mese_fb, anno_fb = parse_periodo_busta(doc)
        if not mese_fb or not anno_fb:
            esito.errore = "PDF mancante o non valido"
            return esito
        mese, anno = mese_fb, anno_fb
        esito.mese, esito.anno = mese, anno
        if not doc.dipendente_id:
            esito.errore = "Documento senza dipendente collegato"
            return esito
        if dry_run:
            esito.ok = True
            return esito
        periodo_label = f"{int(mese):02d}/{int(anno)}"
        dip = doc.dipendente
        mov = MovimentoImportPaghe.objects.filter(documento=doc, tipo="BUSTA").first()
        if mov is None:
            mov = (
                MovimentoImportPaghe.objects.filter(
                    azienda=doc.azienda,
                    dipendente=dip,
                    tipo="BUSTA",
                    anno=anno,
                    mese=mese,
                )
                .order_by("-id")
                .first()
            )
        if mov is None:
            esito.errore = "PDF non leggibile e nessun movimento da aggiornare"
            return esito
        changed: list[str] = []
        if forza or mov.mese != int(mese):
            mov.mese = int(mese)
            changed.append("mese")
        if forza or mov.anno != int(anno):
            mov.anno = int(anno)
            changed.append("anno")
        if forza or mov.periodo_label != periodo_label:
            mov.periodo_label = periodo_label
            changed.append("periodo_label")
        if changed:
            mov.save(update_fields=list(dict.fromkeys(changed + ["updated_at"])))
            esito.aggiornato_movimento = True
        esito.ok = True
        return esito

    res = acquisisci_busta_da_documento(doc, raw_pdf=raw)
    if res.errore:
        esito.errore = res.errore
        return esito

    esito.motore = (res.motore or "").strip()
    esito.netto = res.netto
    esito.lordo = res.lordo
    mese, anno = periodo_retributivo_effettivo(doc, res.report if not res.errore else None)
    esito.mese, esito.anno = mese, anno

    if not mese or not anno:
        esito.errore = "Periodo retributivo non ricavato dal PDF"
        return esito

    if not doc.dipendente_id:
        esito.errore = "Documento senza dipendente collegato"
        return esito

    periodo_label = f"{int(mese):02d}/{int(anno)}"
    dip = doc.dipendente
    cf = (getattr(dip, "codice_fiscale", "") or "")[:16]
    nominativo = f"{getattr(dip, 'cognome', '')} {getattr(dip, 'nome', '')}".strip()[:160]

    if dry_run:
        esito.ok = True
        return esito

    mov = MovimentoImportPaghe.objects.filter(documento=doc, tipo="BUSTA").first()
    if mov is None:
        mov = (
            MovimentoImportPaghe.objects.filter(
                azienda=doc.azienda,
                dipendente=dip,
                tipo="BUSTA",
                anno=anno,
                mese=mese,
            )
            .order_by("-id")
            .first()
        )

    campi = {
        "documento": doc,
        "importo": esito.netto,
        "importo_netto": esito.netto,
        "importo_lordo": esito.lordo,
        "cf_estratto": cf,
        "nominativo_estratto": nominativo,
        "periodo_label": periodo_label,
        "source_pdf": (getattr(doc.file, "name", None) or "")[:260],
        "mese": int(mese),
        "anno": int(anno),
    }

    if mov is None:
        MovimentoImportPaghe.objects.create(
            azienda=doc.azienda,
            dipendente=dip,
            tipo="BUSTA",
            natura_busta="ORDINARIA",
            **campi,
        )
        esito.creato_movimento = True
    else:
        changed: list[str] = []
        for attr, val in campi.items():
            if forza or getattr(mov, attr) in (None, ""):
                if getattr(mov, attr) != val:
                    setattr(mov, attr, val)
                    changed.append(attr)
        if forza:
            if mov.mese != int(mese):
                mov.mese = int(mese)
                changed.append("mese")
            if mov.anno != int(anno):
                mov.anno = int(anno)
                changed.append("anno")
        if changed:
            mov.save(update_fields=list(dict.fromkeys(changed)))
            esito.aggiornato_movimento = True

    if persisti_v4 and (res.motore or "").strip() == "posizionale_v4":
        if tenta_persistenza_cedolino_v4_dopo_lettura(
            doc,
            raw,
            res.report,
            password=res.password_usata or "",
            c_precalcolato=res.cedolino_v4,
            calc_precalcolato=res.calc_v4,
            checks_precalcolato=res.checks_v4,
        ):
            esito.aggiornato_v4 = True

    esito.ok = True
    return esito


def esegui_riallineamento_massivo(
    *,
    azienda_id: int | None = None,
    anno: int | None = None,
    forza: bool = True,
    persisti_v4: bool = True,
    dry_run: bool = False,
    limit: int = 0,
    verbosity: int = 1,
) -> RiepilogoRiallineaBuste:
    """Elabora tutte le buste dell'archivio (filtri opzionali)."""
    riep = RiepilogoRiallineaBuste()
    qs = queryset_buste_archivio(azienda_id=azienda_id, anno=anno)
    riep.totale = qs.count()

    n = 0
    for doc in qs.iterator(chunk_size=100):
        if limit and n >= limit:
            break
        n += 1

        raw_check = leggi_pdf_busta_documento(doc)
        if not raw_check or len(raw_check) < 5 or raw_check[:5] != b"%PDF-":
            riep.senza_pdf += 1
            continue

        esito = riallinea_documento_busta(
            doc,
            forza=forza,
            persisti_v4=persisti_v4,
            dry_run=dry_run,
        )
        if esito.errore:
            if "Periodo" in esito.errore:
                riep.senza_periodo += 1
            else:
                riep.errori += 1
                if verbosity >= 2 and len(riep.dettaglio_errori) < 300:
                    riep.dettaglio_errori.append(f"doc {esito.documento_id}: {esito.errore}")
            continue

        riep.ok += 1
        if esito.creato_movimento:
            riep.movimenti_creati += 1
        if esito.aggiornato_movimento:
            riep.movimenti_aggiornati += 1
        if esito.aggiornato_v4:
            riep.v4_aggiornati += 1

        if verbosity >= 2 and esito.mese and esito.anno:
            print(
                f"doc {esito.documento_id}: {esito.mese:02d}/{esito.anno} "
                f"netto={esito.netto} lordo={esito.lordo} [{esito.motore}]"
            )

    return riep
