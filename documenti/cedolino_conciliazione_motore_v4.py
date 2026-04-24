"""
Conciliazione tra lettura cedolino corrente (PDF → report) e dati strutturati salvati
in :class:`documenti.models.CedolinoMotoreV4` (estrazione motore posizionale v4).

La lettura odierna del PDF deve essere prodotta da :mod:`documenti.busta_acquisizione`
(stesso flusso di elenco buste, ZIP e memorizzazione v4).
"""

from __future__ import annotations

import logging
from decimal import Decimal
from typing import Any

from documenti.cedolini_tolleranze import (
    TOLLERANZA_CONFRONTO_EURO,
    TOLLERANZA_F5_CONTRIBUTI_INPS,
    TOLLERANZA_FORMULE_EURO,
    TOLLERANZA_IMPONIBILE_VOCI_VS_PDF,
)
from documenti.cedolino_conciliazione_checks import (
    formula_ko_bloccanti_da_checks,
    formula_ko_totali_da_checks,
)
from documenti.cedolino_confronto_import import netto_lordo_da_report, parse_importo_it
from documenti.cedolino_motore_v4_da_db import cedolino_dataclass_da_motore_v4
from documenti.models import CedolinoMotoreV4
from documenti.motore_cedolino_v4 import calcola, fmt as _fmt_euro_motore_check

logger = logging.getLogger(__name__)


def _fmt_eur(d: Decimal | None) -> str:
    if d is None:
        return "—"
    s = f"{d:.2f}"
    intp, dec = s.split(".")
    out = []
    for i, c in enumerate(reversed(intp)):
        if i and i % 3 == 0:
            out.append(".")
        out.append(c)
    return "".join(reversed(out)) + "," + dec


def _fmt_delta_eur_signed(d: Decimal) -> str:
    """Differenza con segno in formato italiano (es. +130,79 o −0,03)."""
    if d == 0:
        return _fmt_eur(Decimal("0"))
    body = _fmt_eur(abs(d))
    return ("+" if d > 0 else "−") + body


def format_euro_conc(v: Decimal | None) -> str:
    """Euro in formato italiano (es. riepiloghi pagina conciliazione)."""
    return _fmt_eur(v)


def _ok_imp_tol(
    a: Decimal | None, b: Decimal | None, lim: Decimal
) -> bool | None:
    if a is None and b is None:
        return None
    if a is None or b is None:
        return False
    return abs(a - b) <= lim


def _ok_imp(a: Decimal | None, b: Decimal | None) -> bool | None:
    return _ok_imp_tol(a, b, TOLLERANZA_CONFRONTO_EURO)


def _tot(report: dict | None, chiave: str) -> Decimal | None:
    if not report:
        return None
    tot = report.get("totali_mensili") or {}
    return parse_importo_it(tot.get(chiave))


def _dcalc(x) -> Decimal | None:
    if x is None:
        return None
    try:
        return Decimal(str(round(float(x), 2)))
    except (TypeError, ValueError):
        return None


def _lordo_f3_da_report_posizionale_v4(report: dict[str, Any] | None) -> Decimal | None:
    """
    Lordo coerente con F3 (Σ voci competenze+N/C come in ``calcola()``), esposto in
    ``motore_validazione.calcolo.totale_lordo``. Preferibile alla sola cella riga A in
    ``totali_mensili`` quando le coordinate X sfasano e la prima cifra nella banda lordo
    non è il totale (es. scarto ~600 € con stesso conteggio righe voce).
    """
    if not report or (report.get("motore") or "").strip() != "posizionale_v4":
        return None
    calc = (report.get("motore_validazione") or {}).get("calcolo") or {}
    return _dcalc(calc.get("totale_lordo"))


def conciliazione_oggi_vs_cedolino_motore_v4(
    report: dict[str, Any] | None,
    v4: CedolinoMotoreV4 | None,
    *,
    periodo_mese: int | None,
    periodo_anno: int | None,
    n_voci_lettura: int,
    verifica_ricalcolo_da_db: bool = True,
) -> dict[str, Any]:
    """
    Confronta importi chiave e metadati tra report odierno e riga ``CedolinoMotoreV4``.

    Con ``verifica_ricalcolo_da_db=True`` (pagina conciliazione): ricostruisce il cedolino
    dal DB, riesegue ``calcola()`` e confronta subtotali/formule con il PDF odierno.
    Con ``False``: solo confronto snapshot salvato vs lettura PDF (più leggero).
    """
    if report is None:
        return {
            "stato": "senza_report",
            "righe": [],
            "n_diff": 0,
            "cedolino_v4": None,
            "ha_salvato": False,
            "motore_lettura": "",
            "controlli_formula": [],
            "n_checks_formula_ko": 0,
            "n_checks_formula_ko_bloccanti": 0,
            "ricostruzione_error": False,
            "verifica_ricalcolo_eseguita": False,
        }

    motore_pdf = (report.get("motore") or "").strip()

    if v4 is None:
        netto_ant, lordo_ant_tm = netto_lordo_da_report(report)
        lordo_ant = _lordo_f3_da_report_posizionale_v4(report) or lordo_ant_tm
        return {
            "stato": "senza_salvato",
            "righe": [
                {
                    "campo": "Estrazione motore v4",
                    "riferimento": "—",
                    "lettura": "Nessuna riga in tabella CedolinoMotoreV4 per questo dipendente e periodo.",
                    "delta": "—",
                    "ok": None,
                    "nota": "Usa «Memorizza estrazione v4» per salvare l’analisi posizionale e abilitare i confronti successivi.",
                }
            ],
            "n_diff": 0,
            "cedolino_v4": None,
            "ha_salvato": False,
            "motore_lettura": motore_pdf,
            "anteprima_lettura_pdf": {
                "netto": _fmt_eur(netto_ant),
                "lordo": _fmt_eur(lordo_ant),
                "n_voci": str(n_voci_lettura),
            },
            "controlli_formula": [],
            "n_checks_formula_ko": 0,
            "n_checks_formula_ko_bloccanti": 0,
            "ricostruzione_error": False,
            "verifica_ricalcolo_eseguita": False,
        }

    netto_let, lordo_cella_totali = netto_lordo_da_report(report)
    lordo_f3_oggi = _lordo_f3_da_report_posizionale_v4(report)
    lordo_let = lordo_f3_oggi if lordo_f3_oggi is not None else lordo_cella_totali
    lordo_nota_cella: str = ""
    if (
        lordo_f3_oggi is not None
        and lordo_cella_totali is not None
        and abs(lordo_f3_oggi - lordo_cella_totali) > TOLLERANZA_FORMULE_EURO
    ):
        lordo_nota_cella = (
            f"La cella «Totale Lordo» nei totali mensili ({_fmt_eur(lordo_cella_totali)} €) "
            f"differisce dalla somma F3 delle voci ({_fmt_eur(lordo_f3_oggi)} €); "
            "per i confronti lordo si usa il valore F3 (stesso criterio delle righe elenco)."
        )
    impon_let = _tot(report, "Imponibile Contr. Soc.") or _tot(report, "Imponibile contributivo")
    contrib_let = _tot(report, "Tot. Contributi Sociali")

    righe: list[dict[str, Any]] = []
    n_diff = 0

    def add_row(
        label: str,
        ref_d: Decimal | None,
        let_d: Decimal | None,
        nota: str = "",
        *,
        tol: Decimal = TOLLERANZA_FORMULE_EURO,
    ):
        """Snapshot DB vs PDF: stesse soglie del motore sulle formule (non ±0,02 import)."""
        nonlocal n_diff
        ok = _ok_imp_tol(ref_d, let_d, tol)
        if ok is True:
            nota_finale = nota
        elif ok is False:
            nota_finale = "Diverso o mancante rispetto ai dati salvati."
            if ref_d is not None and let_d is not None:
                diff = abs(ref_d - let_d)
                nota_finale += (
                    f" Scarto assoluto {_fmt_eur(diff)} € "
                    f"(soglia conciliazione ±{_fmt_eur(tol)} €)."
                )
            if nota:
                nota_finale += " " + nota
        else:
            nota_finale = nota
        d_delta = "—"
        if ref_d is not None and let_d is not None:
            d_delta = _fmt_delta_eur_signed(ref_d - let_d)
        righe.append(
            {
                "campo": label,
                "riferimento": _fmt_eur(ref_d),
                "lettura": _fmt_eur(let_d) if let_d is not None else "—",
                "delta": d_delta,
                "ok": ok,
                "nota": nota_finale,
            }
        )
        if ok is False:
            n_diff += 1

    calc_da_db: dict[str, Any] | None = None
    checks_da_db: list[Any] = []
    ricostruzione_error = False
    if verifica_ricalcolo_da_db:
        try:
            c_pre = cedolino_dataclass_da_motore_v4(v4)
            calc_da_db, checks_da_db = calcola(c_pre)
        except Exception:
            ricostruzione_error = True
            logger.warning(
                "Ricalcolo conciliazione (preflight) da CedolinoMotoreV4 fallito (cedolino_id=%s)",
                getattr(v4, "pk", None),
                exc_info=True,
            )
            calc_da_db = None
            checks_da_db = []

    ref_lordo_db: Decimal | None = (
        Decimal(str(v4.totale_lordo)) if v4.totale_lordo is not None else None
    )
    lordo_nota_snapshot = lordo_nota_cella
    if calc_da_db is not None:
        tl = _dcalc(calc_da_db.get("totale_lordo"))
        if tl is not None:
            ref_lordo_db = tl
        if (
            v4.totale_lordo is not None
            and tl is not None
            and abs(Decimal(str(v4.totale_lordo)) - tl) > TOLLERANZA_FORMULE_EURO
        ):
            extra = (
                f"Campo «totale_lordo» in DB ({_fmt_eur(Decimal(str(v4.totale_lordo)))} €) = cella riga A; "
                f"riferimento conciliazione = F3 ({_fmt_eur(tl)} €). Rimemorizza v4 per allineare il campo."
            )
            lordo_nota_snapshot = f"{lordo_nota_snapshot} {extra}".strip() if lordo_nota_snapshot else extra

    add_row("Netto in busta", v4.netto_busta, netto_let)
    add_row("Totale lordo", ref_lordo_db, lordo_let, nota=lordo_nota_snapshot)
    add_row(
        "Imponibile contributivo",
        v4.imponibile_contrib,
        impon_let,
        tol=TOLLERANZA_IMPONIBILE_VOCI_VS_PDF,
    )
    add_row("Contributi sociali (mese)", v4.tot_contrib_soc, contrib_let)

    n_voci_db = v4.voci.count()
    ok_nv = n_voci_db == n_voci_lettura
    d_v = "—"
    if n_voci_db is not None and n_voci_lettura is not None:
        d_v = str(int(n_voci_lettura) - int(n_voci_db))
    righe.append(
        {
            "campo": "Righe voce",
            "riferimento": str(n_voci_db),
            "lettura": str(n_voci_lettura),
            "delta": d_v,
            "ok": ok_nv,
            "nota": "Conteggio righe in DB vs voci lette oggi dal PDF. Δ = lettura − DB.",
        }
    )
    if not ok_nv:
        n_diff += 1

    per_db = f"{v4.mese:02d}/{v4.anno}" if v4.mese and v4.anno else "—"
    per_let = "—"
    ok_per: bool | None = None
    if periodo_mese and periodo_anno:
        per_let = f"{periodo_mese:02d}/{periodo_anno}"
        ok_per = v4.mese == periodo_mese and v4.anno == periodo_anno
    righe.append(
        {
            "campo": "Periodo (mese/anno)",
            "riferimento": per_db,
            "lettura": per_let,
            "delta": "—",
            "ok": ok_per,
            "nota": (
                "DB: mese/anno dell’estrazione salvata; lettura: mese retribuito dal PDF "
                "(non dalla sola descrizione del documento)."
            ),
        }
    )
    if ok_per is False:
        n_diff += 1

    mot = (report.get("motore") or "").strip()
    if mot:
        righe.append(
            {
                "campo": "Motore lettura oggi",
                "riferimento": "—",
                "lettura": mot,
                "delta": "—",
                "ok": None,
                "nota": "Indica se l’estrazione corrente usa il motore posizionale v4 o il fallback testo.",
            }
        )

    controlli_formula: list[dict[str, Any]] = []
    n_checks_formula_ko = 0
    n_checks_formula_ko_bloccanti = 0

    if not verifica_ricalcolo_da_db:
        stato = "ok" if n_diff == 0 else "differenze"
        return {
            "stato": stato,
            "righe": righe,
            "n_diff": n_diff,
            "cedolino_v4": v4,
            "ha_salvato": True,
            "motore_lettura": motore_pdf,
            "controlli_formula": [],
            "n_checks_formula_ko": 0,
            "n_checks_formula_ko_bloccanti": 0,
            "ricostruzione_error": False,
            "verifica_ricalcolo_eseguita": False,
        }

    if ricostruzione_error or calc_da_db is None:
        n_diff += 1
        righe.append(
            {
                "campo": "Ricalcolo da dati memorizzati (calcola)",
                "riferimento": "—",
                "lettura": "Errore",
                "delta": "—",
                "ok": False,
                "nota": "Impossibile ricostruire il cedolino o eseguire le formule; verificare i dati in admin.",
            }
        )
    else:
        calc = calc_da_db
        checks = checks_da_db

        def add_elab_row(
            label: str,
            elaborato: Decimal | None,
            busta_pdf: Decimal | None,
            *,
            nota: str = "",
            tol: Decimal | None = None,
        ):
            nonlocal n_diff
            lim = tol if tol is not None else TOLLERANZA_FORMULE_EURO
            if elaborato is None and busta_pdf is None:
                ok = None
            elif elaborato is None or busta_pdf is None:
                ok = False
            else:
                ok = abs(elaborato - busta_pdf) <= lim
            d_el = "—"
            if elaborato is not None and busta_pdf is not None:
                d_el = _fmt_delta_eur_signed(elaborato - busta_pdf)
            righe.append(
                {
                    "campo": label,
                    "riferimento": _fmt_eur(elaborato),
                    "lettura": _fmt_eur(busta_pdf) if busta_pdf is not None else "—",
                    "delta": d_el,
                    "ok": ok,
                    "nota": nota
                    if ok
                    else (
                        "Elaborato da voci+totali memorizzati ≠ totale letto oggi dal PDF."
                        if ok is False
                        else nota
                    ),
                }
            )
            if ok is False:
                n_diff += 1

        add_elab_row(
            "Lordo (F3 · Σ voci memorizzate vs PDF)",
            _dcalc(calc.get("totale_lordo")),
            lordo_let,
            nota=(
                "F3 ordinario = Σ COMPETENZA + Σ N/C (escluso 9250 pignoramento). "
                "Cessazione: F3 = Σ liquidazioni − Σ preavviso (non la sola cella «Totale Lordo» riga A). "
                "Rate addizionali (1800/1802/800/802/1812) sono TRATTENUTA e non concorrono al lordo TS. "
                "Colonna «lettura»: F3 della lettura odierna (motore_validazione.calcolo)."
            ),
        )
        add_elab_row(
            "Netto (F9 · ricalcolo vs PDF)",
            _dcalc(calc.get("netto_busta")),
            netto_let,
            nota="Netto da formule F3/F8/F9 sui dati memorizzati.",
        )
        add_elab_row(
            "Imponibile (F4 · Σ voci contrib. vs PDF)",
            _dcalc(calc.get("imponibile_contrib_voci")),
            impon_let,
            nota=(
                "Σ voci contribuibili vs imponibile riga A PDF; entro tolleranza F4 = "
                "arrotondamento riferimento / totali TS."
            ),
            tol=TOLLERANZA_IMPONIBILE_VOCI_VS_PDF,
        )
        add_elab_row(
            "Contributi INPS (F5 · calc. vs PDF)",
            _dcalc(calc.get("contrib_sociali")),
            contrib_let,
            nota="Contributi = imponibile memorizzato × 9,36% (IVS dipendente).",
            tol=TOLLERANZA_F5_CONTRIBUTI_INPS,
        )

        n_checks_formula_ko = formula_ko_totali_da_checks(checks)
        n_checks_formula_ko_bloccanti = formula_ko_bloccanti_da_checks(checks)
        for ch in checks:
            controlli_formula.append(
                {
                    "campo": ch.campo,
                    "formula": ch.formula,
                    "calcolato": ch.calcolato,
                    "letto": ch.letto,
                    "delta": _fmt_euro_motore_check(ch.delta),
                    "ok": ch.ok,
                    "nota": ch.nota or "",
                }
            )
        righe.append(
            {
                "campo": "Controlli aritmetici F1–F9 (su cedolino ricostruito da DB)",
                "riferimento": f"{len(checks) - n_checks_formula_ko}/{len(checks)} OK",
                "lettura": f"{n_checks_formula_ko} non OK" if n_checks_formula_ko else "tutti OK",
                "delta": "—",
                "ok": n_checks_formula_ko_bloccanti == 0,
                "nota": (
                    "Ogni controllo confronta valore calcolato vs valore di riferimento nel cedolino memorizzato. "
                    "Per il badge OK/Δ della pagina conciliazione contano solo gli errori su F3,F4,F5,F8,F9 "
                    "(F1 retribuzione, F2 righe voce, F7 IRPEF annua su progressivi sono diagnostici)."
                ),
            }
        )
        if n_checks_formula_ko:
            righe[-1]["nota"] += (
                f" Dettaglio sotto ({n_checks_formula_ko} formula/e in errore)."
            )
        if n_checks_formula_ko_bloccanti:
            n_diff += 1

    stato = "ok" if n_diff == 0 else "differenze"
    return {
        "stato": stato,
        "righe": righe,
        "n_diff": n_diff,
        "cedolino_v4": v4,
        "ha_salvato": True,
        "motore_lettura": motore_pdf,
        "controlli_formula": controlli_formula,
        "n_checks_formula_ko": n_checks_formula_ko,
        "n_checks_formula_ko_bloccanti": n_checks_formula_ko_bloccanti,
        "ricostruzione_error": ricostruzione_error,
        "verifica_ricalcolo_eseguita": True,
    }


def _riga_per_campo(righe: list[dict[str, Any]], campo: str) -> dict[str, Any] | None:
    for r in righe:
        if r.get("campo") == campo:
            return r
    return None


def compact_conciliazione_per_tabella(conc: dict[str, Any]) -> dict[str, Any]:
    """
    Dati per una riga di tabella riassuntiva (pagina conciliazione compatta).
    """
    stato = conc.get("stato") or "senza_report"
    righe = list(conc.get("righe") or [])

    if stato == "senza_report":
        return {
            "stato": stato,
            "badge": "warning",
            "etichetta": "PDF",
            "etichetta_title": "Lettura PDF non riuscita",
            "netto_v4": "—",
            "netto_pdf": "—",
            "netto_ok": None,
            "lordo_v4": "—",
            "lordo_pdf": "—",
            "lordo_ok": None,
            "voci": "—",
            "voci_ok": None,
            "n_diff": 0,
            "ha_salvato": False,
            "motore": "—",
            "formule_ok": None,
            "n_checks_ko": 0,
            "n_checks_ko_bloccanti": 0,
            "ricostruzione_error": False,
        }

    if stato == "senza_salvato":
        mot = (conc.get("motore_lettura") or "").strip() or "—"
        ant = conc.get("anteprima_lettura_pdf") or {}
        return {
            "stato": stato,
            "badge": "secondary",
            "etichetta": "Senza v4",
            "etichetta_title": "Nessuna estrazione motore v4 salvata per questo dipendente e periodo",
            "netto_v4": "—",
            "netto_pdf": ant.get("netto") or "—",
            "netto_ok": None,
            "lordo_v4": "—",
            "lordo_pdf": ant.get("lordo") or "—",
            "lordo_ok": None,
            "voci": f"— / {ant.get('n_voci', '—')}",
            "voci_ok": None,
            "n_diff": 0,
            "ha_salvato": False,
            "motore": mot,
            "formule_ok": None,
            "n_checks_ko": 0,
            "n_checks_ko_bloccanti": 0,
            "ricostruzione_error": False,
        }

    # Snapshot (prima del ricalcolo) vs righe elaborato: in pagina conciliazione con
    # ``verifica_ricalcolo_eseguita`` la colonna compatta deve riflettere F3/F9 (stessi
    # conteggi che alimentano ``n_diff``), non solo il confronto sul campo memorizzato.
    vre_raw = conc.get("verifica_ricalcolo_eseguita")
    vre = bool(vre_raw)
    rn = _riga_per_campo(righe, "Netto (F9 · ricalcolo vs PDF)")
    if not (vre and rn):
        rn = _riga_per_campo(righe, "Netto in busta")
    rl = _riga_per_campo(righe, "Lordo (F3 · Σ voci memorizzate vs PDF)")
    if not (vre and rl):
        rl = _riga_per_campo(righe, "Totale lordo")
    rv = _riga_per_campo(righe, "Righe voce")
    mot = (conc.get("motore_lettura") or "").strip() or "—"
    rm = _riga_per_campo(righe, "Motore lettura oggi")
    if rm and str(rm.get("lettura") or "").strip():
        mot = str(rm.get("lettura") or "—")

    n_diff = int(conc.get("n_diff") or 0)
    n_checks_ko = int(conc.get("n_checks_formula_ko") or 0)
    raw_bloc = conc.get("n_checks_formula_ko_bloccanti")
    if raw_bloc is None:
        n_checks_ko_bloccanti = n_checks_ko
    else:
        n_checks_ko_bloccanti = int(raw_bloc)
    rerr = bool(conc.get("ricostruzione_error"))
    if vre_raw is False:
        formule_ok_val = None
    elif rerr:
        formule_ok_val = False
    else:
        formule_ok_val = n_checks_ko_bloccanti == 0
    ok = stato == "ok"
    return {
        "stato": stato,
        "badge": "success" if ok else "danger",
        "etichetta": "OK" if ok else f"Δ{n_diff}",
        "etichetta_title": (
            "Complessivo OK: DB vs PDF, subtotali ricalcolati (F3–F5, F9) e formule bloccanti (F3–F5, F8, F9) ok; "
            "F1, F2 e F7 restano in elenco solo come diagnostica."
            if ok
            else (
                f"{n_diff} controllo/i non entro tolleranza (snapshot DB vs PDF, subtotali o formule bloccanti F3–F5, F8, F9)"
            )
        ),
        "netto_v4": (rn.get("riferimento") if rn else None) or "—",
        "netto_pdf": (rn.get("lettura") if rn else None) or "—",
        "netto_ok": rn.get("ok") if rn else None,
        "lordo_v4": (rl.get("riferimento") if rl else None) or "—",
        "lordo_pdf": (rl.get("lettura") if rl else None) or "—",
        "lordo_ok": rl.get("ok") if rl else None,
        "voci": (
            f"{rv.get('riferimento', '—')} / {rv.get('lettura', '—')}"
            if rv
            else "—"
        ),
        "voci_ok": rv.get("ok") if rv else None,
        "n_diff": n_diff,
        "ha_salvato": bool(conc.get("ha_salvato")),
        "motore": mot,
        "formule_ok": formule_ok_val,
        "n_checks_ko": n_checks_ko,
        "n_checks_ko_bloccanti": n_checks_ko_bloccanti,
        "ricostruzione_error": rerr,
    }
