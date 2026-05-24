"""
Ponte tra il motore posizionale `motore_cedolino_v4` e il dict «report cedolino» usato da Gesper
(template, JSON, confronto imponibile INPS).

La pipeline basata solo su testo + regex (`report_cedolino_da_testo`) resta fallback
se il v4 non produce segnali sufficienti o solleva eccezioni.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

from documenti.motore_cedolino_v4 import (
    Cedolino,
    EsitoCheck,
    TOLL,
    Voce,
    calcola,
    fmt,
    parse_bytes,
)


def _voce_gesper(v: Voce) -> dict[str, str]:
    cat = (v.tipo or "N/C").strip().upper()
    tipo_tab = "Trattenuta" if cat == "TRATTENUTA" else "Competenza"
    if v.ore_gg is not None and v.ore_gg > 0:
        ore_s = f"{fmt(v.ore_gg, 2)} ore"
    else:
        ore_s = "—"
    base_s = fmt(v.base, 5) if v.base else "—"
    imp_s = fmt(v.importo, 2)
    return {
        "codice": v.codice,
        "descrizione": v.descrizione,
        "ore_giorni": ore_s,
        "base": base_s,
        "importo": imp_s,
        "tipo": tipo_tab,
        "categoria": cat,
        "icona": "",
    }


def _esito_dict(ch: EsitoCheck) -> dict[str, Any]:
    return {
        "campo": ch.campo,
        "formula": ch.formula,
        "calcolato": ch.calcolato,
        "letto": ch.letto,
        "delta": ch.delta,
        "ok": ch.ok,
        "nota": ch.nota or "",
    }


def _calc_serializable(calc: dict) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for k, v in calc.items():
        if isinstance(v, (int, float, str, bool)) or v is None:
            out[k] = v
        else:
            out[k] = str(v)
    return out


def cedolino_v4_a_report_gesper(c: Cedolino, calc: dict, checks: list) -> dict[str, Any]:
    dip: dict[str, str] = {}
    if c.cognome_nome:
        dip["Cognome e Nome"] = c.cognome_nome
    if c.codice_fiscale:
        dip["Codice Fiscale"] = c.codice_fiscale
    if c.data_nascita:
        dip["Data di Nascita"] = c.data_nascita
    if c.comune_residenza:
        dip["Comune di Residenza"] = c.comune_residenza
    if c.data_assunzione:
        dip["Data Assunzione"] = c.data_assunzione
    if c.data_cessazione:
        dip["Data Cessazione"] = c.data_cessazione
    if c.matricola:
        dip["Matricola"] = c.matricola
    if c.matr_inps_az:
        dip["Matricola INPS Az."] = c.matr_inps_az
    if c.pos_inail:
        dip["Posizione INAIL"] = c.pos_inail
    if c.codice_dip:
        dip["Codice Dipendente"] = c.codice_dip
    if c.mese_anno:
        dip["Mese Retribuito"] = c.mese_anno
    if c.qualifica:
        dip["Qualifica"] = c.qualifica
    if c.livello:
        dip["Livello"] = c.livello
    if c.gg_contratto:
        dip["GG. Contributivi"] = str(c.gg_contratto)
    if c.ore_contratto:
        dip["Ore Contrattuali"] = fmt(c.ore_contratto, 2)

    ret: dict[str, str] = {}
    if c.paga_base:
        dec = 5 if c.paga_base < 50 else 2
        ret["Paga Base"] = fmt(c.paga_base, dec)
    if c.contingenza:
        dec = 5 if c.contingenza < 15 else 2
        ret["Contingenza"] = fmt(c.contingenza, dec)
    if c.el_dis_san:
        ret["EL.DIS.SAN"] = fmt(c.el_dis_san, 2)
    if c.el_dis_bil:
        ret["EL.DIS.BIL / scatti"] = fmt(c.el_dis_bil, 5 if c.el_dis_bil < 1 else 2)
    if c.retr_oraria_att:
        ret["Retribuzione Oraria Tot."] = fmt(c.retr_oraria_att, 5)
    if c.retr_giornaliera:
        ret["Retribuzione Giornaliera"] = fmt(c.retr_giornaliera, 2)
    if c.retrib_di_fatto:
        ret["Retribuzione di Fatto"] = fmt(c.retrib_di_fatto, 5 if c.retrib_di_fatto < 100 else 2)

    # F9 (calcola): netto = Lordo + Σ bonus netti (8992/9824/9746) − Tot.Trattenute + Arr.prec
    # (cessazione: coincide col netto letto). Esporre il valore di formula, non solo la colonna PDF.
    f9_netto = float(calc.get("netto_busta", c.netto_busta))
    f3_lordo = float(calc.get("totale_lordo", c.totale_lordo or 0))
    tot: dict[str, str] = {
        "Totale Lordo": fmt(f3_lordo),
        "Imponibile Contr. Soc.": fmt(c.imponibile_contrib),
        "Tot. Contributi Sociali": fmt(c.tot_contrib_soc),
        "Imponibile IRPEF (mese)": fmt(c.imp_irpef_mese),
        "IRPEF Lorda (mese)": fmt(c.irpef_lorda_mese),
        "Tot. Detrazioni (mese)": fmt(c.tot_detr_mese),
        "Tot. Trattenute IRPEF": fmt(c.tot_trat_irpef),
        "Tot. Trattenute": fmt(c.tot_trattenute),
        "Netto in Busta": fmt(f9_netto),
        "Arretrato precedente": fmt(c.arr_prec),
        "Arretrato attuale": fmt(c.arr_attuale),
    }
    if c.totale_lordo and abs(f3_lordo - float(c.totale_lordo)) > TOLL:
        tot["Totale Lordo (cella riga A)"] = fmt(c.totale_lordo)
    letto_netto = float(c.netto_busta or 0)
    if letto_netto > 0 and abs(f9_netto - letto_netto) > TOLL:
        tot["Netto in busta (lettura PDF)"] = fmt(c.netto_busta)

    irp: dict[str, str] = {
        "IRPEF Erario": fmt(c.irpef_erario),
        "Addizionale Regionale": fmt(c.addiz_regionale),
        "Addizionale Comunale": fmt(c.addiz_comunale),
    }

    tipo = "Cessazione" if "CESSAZIONE" in (c.tipo_cedolino or "").upper() else "Ordinario"

    f9_ch = next((ch for ch in checks if "F9" in ch.campo), None)
    formule_ced: dict[str, Any] = {}
    if f9_ch:
        formule_ced = {
            "descrizione_umana": (
                "Il «Netto in busta» nei totali mensili usa il valore F9 del motore v4 "
                f"({f9_ch.formula}). "
                f"Calcolato {fmt(f9_ch.calcolato)} €, confronto con campo PDF {fmt(f9_ch.letto)} €"
                + (" (coerenti)." if f9_ch.ok else " (differenza oltre tolleranza: verificare il PDF).")
            ),
            "segnali_testo_pdf": {"lordo": [], "netto": []},
        }

    periodo_pdf: dict[str, int] = {}
    if getattr(c, "mese", 0) and getattr(c, "anno", 0):
        periodo_pdf = {"periodo_mese_pdf": int(c.mese), "periodo_anno_pdf": int(c.anno)}

    return {
        "dati_aziendali": {},
        "tipo_cedolino": tipo,
        "dati_dipendente": dip,
        **periodo_pdf,
        "retribuzione_base": ret,
        "voci_retributive": [_voce_gesper(v) for v in c.voci],
        "totali_mensili": tot,
        "irpef_addizionali": irp,
        "ferie_permessi_rol": {},
        "dati_previdenziali": {},
        "progressivi_annui": {},
        "motore": "posizionale_v4",
        "motore_validazione": {
            "checks": [_esito_dict(ch) for ch in checks],
            "calcolo": _calc_serializable(calc),
        },
        "formule_cedolino": formule_ced,
        "controllo_aritmetico": [],
        "percorso_fiscale_italia": [],
    }


@dataclass
class BustaV4Bundle:
    """Risultato unico del parse v4: dataclass + calcolo + report Gesper (niente doppio ``parse_bytes``)."""

    c: Cedolino
    calc: dict[str, Any]
    checks: list[Any]
    report: dict[str, Any]


def try_busta_v4_bundle(
    raw: bytes, *, password: str = "", file_label: str = ""
) -> BustaV4Bundle | None:
    """
    Esegue il motore v4 su buffer PDF. Ritorna None se l'estrazione è vuota/inaffidabile
    o in caso di eccezione (stesso criterio di ``try_report_cedolino_v4_bytes``).
    """
    try:
        c = parse_bytes(raw, password=password or "", file_label=file_label or "(buffer)")
        calc, checks = calcola(c)
        usable = (
            c.totale_lordo > 0
            or len(c.voci) > 0
            or bool((c.cognome_nome or "").strip())
            or c.imponibile_contrib > 0
            or c.netto_busta > 0
        )
        if not usable:
            logger.debug(
                "Motore v4 senza segnali utili (file=%s, voci=%s, lordo=%s, netto=%s)",
                file_label or "(buffer)",
                len(c.voci),
                c.totale_lordo,
                c.netto_busta,
            )
            return None
        report = cedolino_v4_a_report_gesper(c, calc, checks)
        return BustaV4Bundle(c=c, calc=calc, checks=checks, report=report)
    except Exception as exc:
        logger.warning(
            "Motore v4 eccezione (file=%s): %s",
            file_label or "(buffer)",
            exc,
            exc_info=True,
        )
        return None


def try_report_cedolino_v4_bytes(
    raw: bytes, *, password: str = "", file_label: str = ""
) -> dict[str, Any] | None:
    """
    Esegue il motore v4 su buffer PDF. Ritorna None se l'estrazione è vuota/inaffidabile
    (nessun segnale strutturale) così il chiamante può usare la pipeline legacy.
    """
    b = try_busta_v4_bundle(raw, password=password, file_label=file_label)
    return b.report if b else None
