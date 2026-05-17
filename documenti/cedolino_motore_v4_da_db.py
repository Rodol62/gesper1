"""
Ricostruisce :class:`documenti.motore_cedolino_v4.Cedolino` da un'istanza
:class:`documenti.models.CedolinoMotoreV4` (voci incluse) per rieseguire ``calcola()``
senza rileggere il PDF.
"""

from __future__ import annotations

from documenti.models import CedolinoMotoreV4
from documenti.motore_cedolino_v4 import (
    CODICI,
    Cedolino,
    Detrazioni,
    DatiInps,
    FeriePermessi,
    Progressivi,
    Voce,
    ar,
)


def _tipo_voce_canonico(codice: str, tipo_salvato: str) -> str:
    """Allinea al motore v4: righe salvate con tipo errato (es. 8992 come N/C) non devono entrare in F4."""
    cod = (codice or "").strip()
    if cod in CODICI:
        return CODICI[cod][0]
    t = (tipo_salvato or "").strip()
    return t if t else "N/C"


def _f(x) -> float:
    if x is None:
        return 0.0
    return float(x)


def cedolino_dataclass_da_motore_v4(v4: CedolinoMotoreV4) -> Cedolino:
    """Usare con ``v4`` che abbia già ``voci`` in cache (``prefetch_related``)."""
    voci: list[Voce] = []
    for vv in v4.voci.all().order_by("id"):
        og = vv.ore_gg
        bu = vv.base_unitaria
        voci.append(
            Voce(
                codice=(vv.codice or "").strip(),
                descrizione=(vv.descrizione or "").strip(),
                ore_gg=float(og) if og is not None else None,
                base=float(bu) if bu is not None else None,
                importo=float(vv.importo),
                tipo=_tipo_voce_canonico(vv.codice, vv.tipo or ""),
                riferimento=(vv.riferimento or "").strip(),
            )
        )

    fp = FeriePermessi(
        ferie_ap=_f(v4.ferie_ap),
        ferie_mat=_f(v4.ferie_mat),
        ferie_god=_f(v4.ferie_god),
        ferie_res=_f(v4.ferie_res),
        perm_ap=_f(v4.perm_ap),
        perm_mat=_f(v4.perm_mat),
        perm_god=_f(v4.perm_god),
        perm_res=_f(v4.perm_res),
        rol_ap=_f(v4.rol_ap),
        rol_mat=_f(v4.rol_mat),
        rol_god=_f(v4.rol_god),
        rol_res=_f(v4.rol_res),
        fest_ap=_f(v4.fest_ap),
        fest_mat=_f(v4.fest_mat),
        fest_god=_f(v4.fest_god),
        fest_res=_f(v4.fest_res),
    )
    inps = DatiInps(
        pos_sett=int(v4.pos_sett_inps or 0),
        ore_inps=_f(v4.ore_inps),
        gg_inps=_f(v4.gg_inps),
        gg_minim=_f(v4.gg_minimi_inps),
        ore_inail=_f(v4.ore_inail),
        gg_inail=_f(v4.gg_inail),
        imponibile_inail=_f(v4.imponibile_inail),
        tfr_mese=_f(v4.tfr_mese),
    )
    detr = Detrazioni(
        lavoro_dip=_f(v4.detr_lavoro_dip),
        coniuge=_f(v4.detr_coniuge),
        figli=_f(v4.detr_figli),
        altri_carichi=_f(v4.detr_altri),
        totale=_f(v4.detr_totale),
    )
    prog = Progressivi(
        imp_inail=_f(v4.prog_imp_inail),
        imp_contrib_soc=_f(v4.prog_imp_contrib_soc),
        contrib_soc=_f(v4.prog_contrib_soc),
        oneri_deduc=_f(v4.prog_oneri_deduc),
        imp_irpef=_f(v4.prog_imp_irpef),
        irpef_lorda=_f(v4.prog_irpef_lorda),
        tot_detr=_f(v4.prog_tot_detr),
        irpef_pagata=_f(v4.prog_irpef_pagata),
    )

    tc = _f(v4.tot_contrib_soc)
    tipo_c = (v4.tipo_cedolino or "ORDINARIO").strip() or "ORDINARIO"
    totale_lordo_v = _f(v4.totale_lordo)
    if "CESSAZIONE" in tipo_c.upper():
        tot_liq = sum(v.importo for v in voci if v.tipo == "LIQUIDAZIONE")
        tot_prev = sum(v.importo for v in voci if v.tipo == "PREAVVISO")
        totale_lordo_v = ar(tot_liq - tot_prev)

    return Cedolino(
        file_pdf=(v4.file_pdf or "")[:512],
        foglio_n=(v4.foglio_n or "").strip(),
        mese_anno="",
        mese=int(v4.mese or 0),
        anno=int(v4.anno or 0),
        tipo_cedolino=tipo_c,
        paga_base=_f(v4.paga_base),
        contingenza=_f(v4.contingenza),
        el_dis_san=_f(v4.el_dis_san),
        el_dis_bil=_f(v4.el_dis_bil),
        scatti_anz_imp=_f(v4.scatti_anz_imp),
        superminimo_imp=_f(v4.superminimo_imp),
        retr_oraria_att=_f(v4.retr_oraria_att),
        retr_giornaliera=_f(v4.retr_giornaliera),
        retrib_di_fatto=_f(v4.retrib_di_fatto),
        gg_contratto=int(v4.gg_contratto or 0),
        ore_contratto=_f(v4.ore_contratto),
        voci=voci,
        totale_lordo=totale_lordo_v,
        imponibile_contrib=_f(v4.imponibile_contrib),
        contrib1=tc,
        tot_contrib_soc=tc,
        imp_irpef_mese=_f(v4.imp_irpef_mese),
        irpef_lorda_mese=_f(v4.irpef_lorda_mese),
        tot_detr_mese=_f(v4.tot_detr_mese),
        tot_trat_irpef=_f(v4.tot_trat_irpef),
        arr_prec=_f(v4.arr_prec),
        tot_trattenute=_f(v4.tot_trattenute),
        arr_attuale=_f(v4.arr_attuale),
        netto_busta=_f(v4.netto_busta),
        irpef_erario=_f(v4.irpef_erario),
        addiz_regionale=_f(v4.addiz_regionale),
        addiz_comunale=_f(v4.addiz_comunale),
        conguaglio_irpef=_f(v4.conguaglio_irpef),
        ferie_perm=fp,
        inps=inps,
        detr=detr,
        prog=prog,
    )
