"""
Persistenza su DB Django dello schema «cedolini v4» (allineato a ``schema_cedolini_v4.sql``),
con ``dipendente`` = :class:`anagrafiche.models.Dipendente`.
"""

from __future__ import annotations

import logging
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from django.db import transaction

from documenti.cedolino_estrazione_v4_hash import pdf_sha256_per_documento
from documenti.motore_cedolino_v4 import parse_bytes

logger = logging.getLogger(__name__)

from documenti.models import (
    CedolinoMotoreV4,
    Documento,
    ValidazioneCedolinoMotoreV4,
    VoceCedolinoMotoreV4,
)
from documenti.natura_busta_utils import infer_natura_busta_per_busta

if TYPE_CHECKING:
    from anagrafiche.models import Dipendente

    from documenti.motore_cedolino_v4 import Cedolino


def _d(v, *, places: int = 2) -> Decimal:
    if v is None:
        return Decimal("0")
    q = Decimal("1").scaleb(-places)  # 10^-places
    return Decimal(str(v)).quantize(q)


def _d5(v) -> Decimal:
    return _d(v, places=5)


def _optional_d(v) -> Decimal | None:
    if v is None:
        return None
    return _d(v)


def _optional_d5(v) -> Decimal | None:
    if v is None:
        return None
    return _d5(v)


_MESI_NOMI = (
	"",
	"GENNAIO",
	"FEBBRAIO",
	"MARZO",
	"APRILE",
	"MAGGIO",
	"GIUGNO",
	"LUGLIO",
	"AGOSTO",
	"SETTEMBRE",
	"OTTOBRE",
	"NOVEMBRE",
	"DICEMBRE",
)


def _applica_mese_anno_cedolino_da_documento(c: Any, doc: Documento | None) -> None:
	"""
	Se ``parse_bytes`` non valorizza mese/anno (regex intestazione non aderente al PDF),
	usa il periodo ricavato da descrizione/data documento (stessa logica elenco buste).
	"""
	if getattr(c, "mese", None) and getattr(c, "anno", None):
		return
	if not doc:
		return
	from documenti.buste_cedolino_batch import parse_periodo_busta

	m, y = parse_periodo_busta(doc)
	if m and y:
		c.mese = int(m)
		c.anno = int(y)
		if not (getattr(c, "mese_anno", None) or "").strip() and 1 <= c.mese <= 12:
			c.mese_anno = f"{_MESI_NOMI[c.mese]} {c.anno}"


def tenta_persistenza_cedolino_v4_dopo_lettura(
    doc: Documento,
    raw: bytes,
    report: dict[str, Any],
    *,
    password: str = "",
    c_precalcolato: Cedolino | None = None,
    calc_precalcolato: dict | None = None,
    checks_precalcolato: list | None = None,
) -> bool:
    """
    Se il report proviene dal motore posizionale v4, salva/aggiorna ``CedolinoMotoreV4``
    (stesso flusso del pulsante «Memorizza estrazione v4»).

    Con ``c_precalcolato`` (da :class:`documenti.cedolino_bridge_v4.BustaV4Bundle`) non viene
    rieseguito ``parse_bytes`` sul PDF: stesso flusso logico della lettura canonica.

    Ritorna True se il salvataggio è riuscito. In caso di errore (es. mese/anno assenti)
    registra un warning e ritorna False senza sollevare eccezioni.
    """
    if not report or report.get("motore") != "posizionale_v4":
        return False
    if not getattr(doc, "dipendente_id", None):
        logger.info(
            "CedolinoMotoreV4: documento %s senza dipendente, persistenza saltata",
            getattr(doc, "pk", None),
        )
        return False
    try:
        if c_precalcolato is not None:
            c = c_precalcolato
        else:
            c = parse_bytes(
                raw,
                password=password or "",
                file_label=doc.nome_file() or "",
            )
        _applica_mese_anno_cedolino_da_documento(c, doc)
        salva_cedolino_motore_v4(
            dipendente=doc.dipendente,
            c=c,
            documento=doc,
            file_pdf=doc.nome_file() or "",
            pdf_raw_bytes=raw,
            calc=calc_precalcolato,
            checks=checks_precalcolato,
            report=report,
        )
        return True
    except Exception:
        logger.warning(
            "Persistenza CedolinoMotoreV4 non riuscita (documento_id=%s)",
            getattr(doc, "pk", None),
            exc_info=True,
        )
        return False


@transaction.atomic
def salva_cedolino_motore_v4(
    *,
    dipendente: Dipendente,
    c: Cedolino,
    calc: dict | None = None,
    checks: list | None = None,
    documento: Documento | None = None,
    file_pdf: str = "",
    pdf_raw_bytes: bytes | None = None,
    report: dict | None = None,
) -> CedolinoMotoreV4:
    """
    Crea o aggiorna :class:`CedolinoMotoreV4` + voci + validazioni.

    ``c.mese`` / ``c.anno`` devono essere valorizzati dal parser; se mancano e c'è
    ``documento``, si usano descrizione/data caricamento (come in elenco buste).
    """
    from documenti.motore_cedolino_v4 import TOLL, ar, calcola

    if documento and documento.dipendente_id and documento.dipendente_id != dipendente.id:
        raise ValueError("Il documento non appartiene al dipendente indicato.")
    if documento and documento.azienda_id != dipendente.azienda_id:
        raise ValueError("Il documento non appartiene all'azienda del dipendente.")

    _applica_mese_anno_cedolino_da_documento(c, documento)

    if not c.mese or not c.anno:
        raise ValueError("Mese/anno cedolino mancanti: impossibile salvare.")

    if calc is None or checks is None:
        calc, checks = calcola(c)

    natura_busta = infer_natura_busta_per_busta(
        documento=documento,
        report=report,
        tipo_cedolino_motore=getattr(c, "tipo_cedolino", None),
    )

    fp = c.ferie_perm
    inps = c.inps
    prog = c.prog
    detr = c.detr

    defaults = {
        "documento": documento,
        "natura_busta": natura_busta,
        "foglio_n": c.foglio_n or "",
        "file_pdf": file_pdf or (c.file_pdf or "")[:512],
        "tipo_cedolino": (c.tipo_cedolino or "ORDINARIO")[:64],
        "paga_base": _optional_d5(c.paga_base),
        "contingenza": _optional_d5(c.contingenza),
        "el_dis_san": _d(c.el_dis_san),
        "el_dis_bil": _d5(c.el_dis_bil),
        "scatti_anz_imp": _d5(c.scatti_anz_imp),
        "superminimo_imp": _d5(c.superminimo_imp),
        "retr_oraria_att": _optional_d5(c.retr_oraria_att),
        "retr_giornaliera": _optional_d(c.retr_giornaliera),
        "retrib_di_fatto": _optional_d5(c.retrib_di_fatto),
        "gg_contratto": c.gg_contratto or None,
        "ore_contratto": _optional_d(c.ore_contratto),
        # F3 (ordinario: Σ competenze+N/C; cessazione: Σ liquidazioni − preavviso) ≠ sempre la cella riga A
        "totale_lordo": _optional_d(calc.get("totale_lordo", c.totale_lordo)),
        "imponibile_contrib": _optional_d(c.imponibile_contrib),
        "tot_contrib_soc": _optional_d(c.tot_contrib_soc),
        "imp_irpef_mese": _optional_d(c.imp_irpef_mese),
        "irpef_lorda_mese": _optional_d(c.irpef_lorda_mese),
        "tot_detr_mese": _optional_d(c.tot_detr_mese),
        "tot_trat_irpef": _optional_d(c.tot_trat_irpef),
        "tot_trattenute": _optional_d(c.tot_trattenute),
        "netto_busta": _optional_d(calc.get("netto_busta", c.netto_busta)),
        "irpef_erario": _d(c.irpef_erario),
        "addiz_regionale": _d(c.addiz_regionale),
        "addiz_comunale": _d(c.addiz_comunale),
        "arr_prec": _d(c.arr_prec),
        "arr_attuale": _d(c.arr_attuale),
        "conguaglio_irpef": _d(c.conguaglio_irpef),
        "detr_lavoro_dip": _d(detr.lavoro_dip),
        "detr_coniuge": _d(detr.coniuge),
        "detr_figli": _d(detr.figli),
        "detr_altri": _d(detr.altri_carichi),
        "detr_totale": _d(detr.totale),
        "ferie_ap": _d(fp.ferie_ap),
        "ferie_mat": _d(fp.ferie_mat),
        "ferie_god": _d(fp.ferie_god),
        "ferie_res": _d(fp.ferie_res),
        "perm_ap": _d(fp.perm_ap),
        "perm_mat": _d(fp.perm_mat),
        "perm_god": _d(fp.perm_god),
        "perm_res": _d(fp.perm_res),
        "rol_ap": _d(fp.rol_ap),
        "rol_mat": _d(fp.rol_mat),
        "rol_god": _d(fp.rol_god),
        "rol_res": _d(fp.rol_res),
        "fest_ap": _d(fp.fest_ap),
        "fest_mat": _d(fp.fest_mat),
        "fest_god": _d(fp.fest_god),
        "fest_res": _d(fp.fest_res),
        "pos_sett_inps": int(inps.pos_sett or 0),
        "ore_inps": _d(inps.ore_inps),
        "gg_inps": _d(inps.gg_inps),
        "gg_minimi_inps": _d(inps.gg_minim),
        "ore_inail": _d(inps.ore_inail),
        "gg_inail": _d(inps.gg_inail),
        "imponibile_inail": _d(inps.imponibile_inail),
        "tfr_mese": _d(inps.tfr_mese),
        "prog_imp_inail": _d(prog.imp_inail),
        "prog_imp_contrib_soc": _d(prog.imp_contrib_soc),
        "prog_contrib_soc": _d(prog.contrib_soc),
        "prog_oneri_deduc": _d(prog.oneri_deduc),
        "prog_imp_irpef": _d(prog.imp_irpef),
        "prog_irpef_lorda": _d(prog.irpef_lorda),
        "prog_tot_detr": _d(prog.tot_detr),
        "prog_irpef_pagata": _d(prog.irpef_pagata),
        "imp_contrib_voci": _d(calc.get("imponibile_contrib_voci") or 0),
        "retr_oraria_calc": _d5(calc.get("retr_oraria") or 0),
        "pdf_bytes_sha256": pdf_sha256_per_documento(documento, pdf_raw_bytes),
        "estrazione_motore": "posizionale_v4",
        "verifica_stato": CedolinoMotoreV4.VerificaStato.PENDING,
        "verifica_il": None,
        "verifica_n_diff": None,
        "verifica_n_checks_formula_ko": None,
        "verifica_n_checks_formula_ko_bloccanti": None,
    }

    obj, _created = CedolinoMotoreV4.objects.update_or_create(
        dipendente=dipendente,
        mese=int(c.mese),
        anno=int(c.anno),
        natura_busta=natura_busta,
        defaults=defaults,
    )

    obj.voci.all().delete()
    bulk_voci: list[VoceCedolinoMotoreV4] = []
    for v in c.voci:
        ic = None
        dc = None
        if v.ore_gg is not None and v.base:
            ic = ar(v.ore_gg * v.base)
            dc = ar(abs(ic - v.importo))
        es = "OK" if dc is not None and dc <= TOLL else ("KO" if dc is not None else "N/A")
        bulk_voci.append(
            VoceCedolinoMotoreV4(
                cedolino=obj,
                codice=v.codice,
                descrizione=(v.descrizione or "")[:255],
                tipo=(v.tipo or "")[:32],
                ore_gg=_optional_d4(v.ore_gg),
                base_unitaria=_optional_d5(v.base),
                importo=_d(v.importo),
                riferimento=(v.riferimento or "")[:64],
                importo_calcolato=_optional_d(ic) if ic is not None else None,
                delta_calc=_optional_d(dc) if dc is not None else None,
                esito_check=es[:8],
            )
        )
    if bulk_voci:
        VoceCedolinoMotoreV4.objects.bulk_create(bulk_voci)

    obj.validazioni.all().delete()
    bulk_val: list[ValidazioneCedolinoMotoreV4] = []
    for ch in checks:
        es = "OK" if ch.ok else ("WARN" if abs(ch.delta) < 1.0 else "KO")
        bulk_val.append(
            ValidazioneCedolinoMotoreV4(
                cedolino=obj,
                formula=(ch.campo[:16] if ch.campo else "")[:16],
                descrizione=(ch.campo or "")[:255],
                valore_calc=_d5(ch.calcolato),
                valore_letto=_d5(ch.letto),
                delta=_d5(ch.delta),
                esito=es[:8],
                nota=(ch.nota or "")[:2000],
            )
        )
    if bulk_val:
        ValidazioneCedolinoMotoreV4.objects.bulk_create(bulk_val)

    # Dopo il commit: alimenta il partitario «netti da pagare» (DARE) per admin, senza
    # annullare il salvataggio del cedolino se il partitario fallisce.
    _pk = obj.pk
    _uid = documento.caricato_da_id if documento else None

    def _partitario_netto_dopo_commit() -> None:
        from partitario_netti.services_sync import sincronizza_netto_dopo_persistenza_cedolino_v4

        sincronizza_netto_dopo_persistenza_cedolino_v4(_pk, utente_id=_uid)

    transaction.on_commit(_partitario_netto_dopo_commit)

    return obj


def _optional_d4(v) -> Decimal | None:
    if v is None:
        return None
    return _d(v, places=4)
