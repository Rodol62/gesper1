"""
Confronto tra dati letti dal cedolino (PDF → report) e movimenti ``MovimentoImportPaghe``
collegati all'archivio buste (import / caricamento con estrazione netto-lordo).

I valori da PDF per netto/lordo su ``Documento`` dovrebbero provenire dalla stessa pipeline
di :mod:`documenti.busta_acquisizione` (vedi ``_extract_busta_importi_da_pdf`` in ``views``),
così confronto import e lettura cedolino restano coerenti.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from accounts.models import MovimentoImportPaghe
from documenti.buste_cedolino_batch import parse_periodo_busta, periodo_retributivo_effettivo
from documenti.cedolini_tolleranze import TOLLERANZA_CONFRONTO_EURO
from documenti.models import Documento

TOLLERANZA_EURO = TOLLERANZA_CONFRONTO_EURO

# Possibili etichette nei dict ``totali_mensili`` / IRPEF a seconda del layout PDF.
# Ordine: prima i campi «principali» del merge / motore v4 (``Netto in Busta`` = F9,
# ``Totale Lordo`` = totale riga A), poi i duplicati ``… (lettura PDF)`` se il principale
# manca.
#
# Su ``posizionale_v4``, ``Netto in busta (lettura PDF)`` è ``c.netto_busta`` (cella
# posizionale): su alcuni layout coincide col netto in busta, su altri cattura un
# importo diverso (es. trattenuta / colonna sbagliata ~4xx €). La conciliazione con
# ``CedolinoMotoreV4.netto_busta`` (persistenza F9) deve usare quindi per primo
# ``Netto in Busta``. Per vedere scarto F9 vs cella grezza usare i controlli formula nel
# dettaglio conciliazione.
_CHIAVI_NETTO = (
    "Netto in Busta",
    "Netto in busta (lettura PDF)",
    "Netto Busta (conferma)",
)
_CHIAVI_LORDO = (
    "Totale Lordo",
    "Totale Lordo (lettura PDF)",
    "lordo_mensile",  # eventuale chiave da estrazioni layout
)


def parse_importo_it(s: Any) -> Decimal | None:
    """Converte stringa importo stile italiano (1.234,56) in ``Decimal``."""
    if s is None:
        return None
    t = str(s).strip().replace("€", "").replace("\u00a0", " ")
    if not t or t in ("—", "N/D", "-", "N/D €"):
        return None
    t = t.replace(" ", "")
    if "," in t:
        t = t.replace(".", "").replace(",", ".")
    else:
        t = t.replace(".", "")
    try:
        return Decimal(t).quantize(Decimal("0.01"))
    except Exception:
        return None


def _primo_valore_report(d: dict[str, Any] | None, chiavi: tuple[str, ...]) -> str | None:
    if not d:
        return None
    for k in chiavi:
        v = d.get(k)
        if v is not None and str(v).strip() and str(v).strip() not in ("—", "N/D", "-"):
            return str(v).strip()
    return None


def netto_lordo_da_report(report: dict[str, Any] | None) -> tuple[Decimal | None, Decimal | None]:
    """(netto, lordo) estratti dal report cedolino."""
    if not report:
        return None, None
    tot = report.get("totali_mensili") or {}
    irp = report.get("irpef_addizionali") or {}
    netto_s = _primo_valore_report(tot, _CHIAVI_NETTO) or _primo_valore_report(irp, _CHIAVI_NETTO)
    lordo_s = _primo_valore_report(tot, _CHIAVI_LORDO)
    return parse_importo_it(netto_s), parse_importo_it(lordo_s)


def _tot_val(tot: dict[str, Any], *keys: str) -> Decimal | None:
    """Primo importo parsabile tra chiavi alternative in ``totali_mensili``."""
    for k in keys:
        v = tot.get(k)
        if v is not None and str(v).strip() and str(v).strip() not in ("—", "N/D", "-"):
            p = parse_importo_it(v)
            if p is not None:
                return p
    return None


def dettaglio_libro_paga_da_report(report: dict[str, Any] | None) -> dict[str, Any]:
    """
    Mappa ``totali_mensili`` / ``irpef_addizionali`` / ``dati_previdenziali`` del report cedolino
    (stessa fonte della «lettura buste») sulle chiavi usate da ``LibroPagaStorico`` e da
    ``estrai_busta_dettaglio_libro_paga_da_pdf``, così le colonne del Libro Unico coincidono con i valori
    mostrati in conciliazione / lettura cedolino (evita importi spuri da euristica posizionale).
    """
    keys = (
        "importo",
        "lordo_mensile",
        "inps_dipendente",
        "irpef",
        "addizionali",
        "altre_trattenute",
        "trattamento_integrativo",
        "retribuzione_base",
        "indennita_accessorie",
        "inps_azienda",
        "inail_azienda",
        "costo_azienda",
        "tfr_mensile",
        "rateo_13",
        "rateo_14",
        "ore_ordinarie",
        "ore_straordinario",
        "ore_assenza",
    )
    out: dict[str, Any] = {k: None for k in keys}
    if not report:
        return out

    tot = report.get("totali_mensili") or {}
    irp = report.get("irpef_addizionali") or {}
    prev = report.get("dati_previdenziali") or {}

    out["lordo_mensile"] = _tot_val(tot, "Totale Lordo", "Totale Lordo (lettura PDF)")
    out["importo"] = _tot_val(
        tot,
        "Netto in Busta",
        "Netto in busta (lettura PDF)",
        "Netto Busta (conferma)",
    )
    out["inps_dipendente"] = _tot_val(
        tot,
        "Tot. Contributi Sociali",
        "Contributi Sociali",
    )
    # Colonna IRPEF: trattenute fiscali coerenti con il report (non piccoli importi a caso da layout)
    out["irpef"] = _tot_val(
        tot,
        "Tot. Trattenute IRPEF",
        "IRPEF Netta",
        "IRPEF Lorda (mese)",
        "IRPEF Lorda",
    )
    if out["irpef"] is None:
        out["irpef"] = _tot_val(irp, "IRPEF Erario")

    ar = parse_importo_it(irp.get("Addizionale Regionale"))
    ac = parse_importo_it(irp.get("Addizionale Comunale"))
    if ar is not None or ac is not None:
        out["addizionali"] = (ar or Decimal("0")) + (ac or Decimal("0"))

    out["tfr_mensile"] = _tot_val(prev, "TFR Mese")

    ore_inps = parse_importo_it(prev.get("Ore INPS"))
    if ore_inps is not None and ore_inps > 0:
        out["ore_ordinarie"] = ore_inps

    return out


def applica_totalizzatori_calcolo_v4_a_libro(
    out: dict[str, Any], calc: dict[str, Any], c: Any
) -> None:
    """
    Sovrascrive i campi numerici principali del libro con i totalizzatori del motore v4:
    lordo **F3** e netto **F9** da ``calc``, contributi/IRPEF/addizionali e ore INPS dai valori
    letti sul cedolino (stessi totali usati in conciliazione e nelle formule del PDF).

    Va chiamato quando ``calc`` e ``cedolino_v4`` sono disponibili, *dopo* l’estrazione dal
    dict report, così si eliminano importi spuri (es. 4 €) rimasti dall’euristica legacy.
    """
    if calc is None or c is None:
        return

    def d_money(x: Any) -> Decimal | None:
        if x is None:
            return None
        try:
            return Decimal(str(float(x))).quantize(Decimal("0.01"))
        except Exception:
            return None

    f3 = calc.get("totale_lordo")
    if f3 is None:
        f3 = getattr(c, "totale_lordo", None)
    v = d_money(f3)
    if v is not None:
        out["lordo_mensile"] = v

    f9 = calc.get("netto_busta")
    if f9 is None:
        f9 = getattr(c, "netto_busta", None)
    v = d_money(f9)
    if v is not None:
        out["importo"] = v

    v = d_money(getattr(c, "tot_contrib_soc", None))
    if v is not None:
        out["inps_dipendente"] = v

    v = d_money(getattr(c, "tot_trat_irpef", None))
    if v is not None:
        out["irpef"] = v

    ar = d_money(getattr(c, "addiz_regionale", None))
    ac = d_money(getattr(c, "addiz_comunale", None))
    if ar is not None or ac is not None:
        out["addizionali"] = (ar or Decimal("0")) + (ac or Decimal("0"))

    inps = getattr(c, "inps", None)
    if inps is not None:
        try:
            oi_f = float(getattr(inps, "ore_inps", 0) or 0)
        except Exception:
            oi_f = 0.0
        if oi_f > 0:
            out["ore_ordinarie"] = Decimal(str(round(oi_f, 2))).quantize(Decimal("0.01"))
        tm = d_money(getattr(inps, "tfr_mese", None))
        if tm is not None:
            out["tfr_mensile"] = tm

    # Fallback orario: su alcuni layout TS ``Ore INPS`` resta 0 ma la voce ordinario (8001)
    # è presente e affidabile per il LUL.
    if out.get("ore_ordinarie") is None:
        voci = getattr(c, "voci", None) or []
        ore_8001 = None
        for vv in voci:
            if str(getattr(vv, "codice", "") or "").strip() == "8001":
                try:
                    og = float(getattr(vv, "ore_gg", 0) or 0)
                except Exception:
                    og = 0.0
                if og > 0:
                    ore_8001 = og
                    break
        if ore_8001 is not None:
            out["ore_ordinarie"] = Decimal(str(round(ore_8001, 2))).quantize(Decimal("0.01"))

    pb = float(getattr(c, "paga_base", 0) or 0)
    if pb >= 50:
        b = d_money(pb)
        if b is not None:
            out["retribuzione_base"] = b

    # ``contingenza`` / ``el_dis_*`` su paghe orarie sono componenti €/h (non indennità mensili LUL):
    # azzera la colonna per evitare valori fuorvianti tipo 3,09.
    if pb >= 50:
        ind = float(getattr(c, "contingenza", 0) or 0)
        ind += float(getattr(c, "el_dis_san", 0) or 0) + float(getattr(c, "el_dis_bil", 0) or 0)
        if ind > 0:
            di = d_money(ind)
            if di is not None:
                out["indennita_accessorie"] = di
    else:
        out["indennita_accessorie"] = Decimal("0.00")

    # Derivazioni da elenco voci (utile quando alcuni campi tabellari TeamSystem non sono compilati)
    voci = getattr(c, "voci", None) or []
    if voci:
        def _tipo(vv: Any) -> str:
            return str(getattr(vv, "tipo", "") or "").strip().upper()

        def _cod(vv: Any) -> str:
            return str(getattr(vv, "codice", "") or "").strip()

        def _desc(vv: Any) -> str:
            return str(getattr(vv, "descrizione", "") or "").strip().lower()

        # Retribuzione base: nelle paghe orarie usa la competenza ordinaria (cod. 8001)
        if out.get("retribuzione_base") is None:
            base_imp = Decimal("0.00")
            for vv in voci:
                if _cod(vv) == "8001":
                    iv = d_money(getattr(vv, "importo", None))
                    if iv is not None:
                        base_imp += iv
            if base_imp > 0:
                out["retribuzione_base"] = base_imp

        # Indennità/accessori: competenze residue (es. domenicali/festività) non incluse in 8001
        if out.get("indennita_accessorie") in (None, Decimal("0.00")):
            ind = Decimal("0.00")
            for vv in voci:
                if _tipo(vv) == "COMPETENZA" and _cod(vv) not in {"8001"}:
                    iv = d_money(getattr(vv, "importo", None))
                    if iv is not None:
                        ind += iv
            if ind >= 0:
                out["indennita_accessorie"] = ind

        # Ore straordinarie / assenza (euristica per descrizione/codice)
        if out.get("ore_straordinario") is None:
            ore_str = Decimal("0.00")
            for vv in voci:
                dsc = _desc(vv)
                if "straord" in dsc:
                    og = d_money(getattr(vv, "ore_gg", None))
                    if og is not None:
                        ore_str += og
            if ore_str > 0:
                out["ore_straordinario"] = ore_str
        if out.get("ore_assenza") is None:
            ore_ass = Decimal("0.00")
            for vv in voci:
                dsc = _desc(vv)
                if any(k in dsc for k in ("ferie", "perm", "rol", "assenza", "malatt")):
                    og = d_money(getattr(vv, "ore_gg", None))
                    if og is not None:
                        ore_ass += og
            if ore_ass > 0:
                out["ore_assenza"] = ore_ass

        # TI/Bonus: somma voci BONUS
        bonus = Decimal("0.00")
        for vv in voci:
            if _tipo(vv) == "BONUS":
                iv = d_money(getattr(vv, "importo", None))
                if iv is not None:
                    bonus += iv
        out["trattamento_integrativo"] = bonus

        # Addizionali / altre trattenute da codici trattenuta
        add = Decimal("0.00")
        altre = Decimal("0.00")
        cod_add = {"1800", "1802", "1812", "800", "802"}
        for vv in voci:
            if _tipo(vv) != "TRATTENUTA":
                continue
            iv = d_money(getattr(vv, "importo", None))
            if iv is None:
                continue
            if _cod(vv) in cod_add:
                add += iv
            else:
                altre += iv
        if add > 0:
            out["addizionali"] = add
        out["altre_trattenute"] = altre


def arricchisci_dettaglio_libro_da_cedolino_v4(out: dict[str, Any], c: Any) -> None:
    """Completa ``out`` con campi dal dataclass Cedolino v4 dove il report non li espone."""
    if c is None:
        return

    def d_num(x: Any) -> Decimal | None:
        """Decimal da float/int; include lo zero (utile per correggere importi spuri dal PDF legacy)."""
        if x is None:
            return None
        try:
            return Decimal(str(float(x))).quantize(Decimal("0.01"))
        except Exception:
            return None

    if out.get("lordo_mensile") is None:
        v = d_num(getattr(c, "totale_lordo", None))
        if v is not None:
            out["lordo_mensile"] = v
    if out.get("importo") is None:
        v = d_num(getattr(c, "netto_busta", None))
        if v is not None:
            out["importo"] = v
    if out.get("inps_dipendente") is None:
        v = d_num(getattr(c, "tot_contrib_soc", None))
        if v is not None:
            out["inps_dipendente"] = v
    if out.get("irpef") is None:
        v = d_num(getattr(c, "tot_trat_irpef", None))
        if v is not None:
            out["irpef"] = v
    if out.get("addizionali") is None:
        ar = d_num(getattr(c, "addiz_regionale", None))
        ac = d_num(getattr(c, "addiz_comunale", None))
        if ar is not None or ac is not None:
            out["addizionali"] = (ar or Decimal("0")) + (ac or Decimal("0"))

    inps = getattr(c, "inps", None)
    if inps is not None:
        oi_raw = getattr(inps, "ore_inps", None)
        if oi_raw is not None:
            try:
                oi_f = float(oi_raw)
            except Exception:
                oi_f = 0.0
            if oi_f > 0:
                out["ore_ordinarie"] = Decimal(str(round(oi_f, 2))).quantize(Decimal("0.01"))
        if out.get("tfr_mensile") is None:
            v = d_num(getattr(inps, "tfr_mese", None))
            if v is not None:
                out["tfr_mensile"] = v

    # Stessa euristica del bridge v4: paga_base < 50 è tipicamente oraria (€/h), non va in «retribuzione base» mensile.
    if out.get("retribuzione_base") is None:
        pb = float(getattr(c, "paga_base", 0) or 0)
        if pb >= 50:
            v = d_num(pb)
            if v is not None:
                out["retribuzione_base"] = v


def movimento_busta_per_documento(doc: Documento) -> MovimentoImportPaghe | None:
    """
    Movimento tipo BUSTA collegato al documento; se assente, fallback stesso dipendente + periodo da descrizione.
    """
    if not doc or not doc.azienda_id:
        return None
    m = (
        MovimentoImportPaghe.objects.filter(
            azienda_id=doc.azienda_id,
            tipo="BUSTA",
            documento_id=doc.id,
        )
        .order_by("-id")
        .first()
    )
    if m:
        return m
    mese, anno = periodo_retributivo_effettivo(doc, report)
    if doc.dipendente_id and mese and anno:
        return (
            MovimentoImportPaghe.objects.filter(
                azienda_id=doc.azienda_id,
                tipo="BUSTA",
                dipendente_id=doc.dipendente_id,
                mese=mese,
                anno=anno,
                natura_busta="ORDINARIA",
            )
            .order_by("-id")
            .first()
        )
    return None


def _decimal_mov_netto(mov: MovimentoImportPaghe) -> Decimal | None:
    if mov.importo_netto is not None:
        return mov.importo_netto.quantize(Decimal("0.01"))
    if mov.importo is not None:
        return mov.importo.quantize(Decimal("0.01"))
    return None


def _decimal_mov_lordo(mov: MovimentoImportPaghe) -> Decimal | None:
    if mov.importo_lordo is not None:
        return mov.importo_lordo.quantize(Decimal("0.01"))
    return None


def _coerente_importi(a: Decimal | None, b: Decimal | None) -> bool | None:
    """True se uguali entro tolleranza; None se entrambi mancanti (non verificabile)."""
    if a is None and b is None:
        return None
    if a is None or b is None:
        return False
    return abs(a - b) <= TOLLERANZA_CONFRONTO_EURO


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


def confronta_cedolino_con_movimento(
    doc: Documento,
    report: dict[str, Any] | None,
    mov: MovimentoImportPaghe | None,
    *,
    periodo_mese: int | None,
    periodo_anno: int | None,
) -> dict[str, Any]:
    """
    Ritorna dict per template:
    - stato: senza_report | assente_movimento | ok | differenze
    - righe: lista di {campo, archivio, lettura, ok, nota}
    - n_diff: conteggio incongruenze (solo ok False)
    """
    if report is None:
        return {
            "stato": "senza_report",
            "righe": [],
            "n_diff": 0,
            "movimento": None,
        }

    netto_let, lordo_let = netto_lordo_da_report(report)
    dip = report.get("dati_dipendente") or {}
    cf_let = (dip.get("Codice Fiscale") or dip.get("Codice fiscale") or "").strip().upper()
    cf_let = cf_let[:16] if cf_let else ""

    if mov is None:
        return {
            "stato": "assente_movimento",
            "righe": [
                {
                    "campo": "Archivio import",
                    "archivio": "—",
                    "lettura": "Nessun MovimentoImportPaghe (tipo BUSTA) collegato a questo PDF o allo stesso dipendente/periodo.",
                    "ok": None,
                    "nota": "Collegare il documento dall’import paghe o verificare mese/anno.",
                }
            ],
            "n_diff": 0,
            "movimento": None,
        }

    righe: list[dict[str, Any]] = []
    n_diff = 0

    netto_arch = _decimal_mov_netto(mov)
    lordo_arch = _decimal_mov_lordo(mov)
    ok_netto = _coerente_importi(netto_arch, netto_let)
    ok_lordo = _coerente_importi(lordo_arch, lordo_let)

    righe.append(
        {
            "campo": "Netto in busta",
            "archivio": _fmt_eur(netto_arch),
            "lettura": _fmt_eur(netto_let) if netto_let is not None else "—",
            "ok": ok_netto,
            "nota": ""
            if ok_netto
            else (
                "Valori diversi o uno dei due mancante: controllare layout PDF e campi estratti."
                if ok_netto is False
                else "Non confrontabile (entrambi assenti)."
            ),
        }
    )
    if ok_netto is False:
        n_diff += 1

    righe.append(
        {
            "campo": "Lordo / totale lordo",
            "archivio": _fmt_eur(lordo_arch),
            "lettura": _fmt_eur(lordo_let) if lordo_let is not None else "—",
            "ok": ok_lordo,
            "nota": ""
            if ok_lordo
            else (
                "Da archivio manca importo_lordo o da PDF non è stato trovato «Totale Lordo»."
                if ok_lordo is False
                else ""
            ),
        }
    )
    if ok_lordo is False:
        n_diff += 1

    # Periodo
    ok_per: bool | None = None
    per_arch = f"{mov.mese:02d}/{mov.anno}"
    per_let = "—"
    if periodo_mese and periodo_anno:
        per_let = f"{periodo_mese:02d}/{periodo_anno}"
        ok_per = mov.mese == periodo_mese and mov.anno == periodo_anno
    elif mov.periodo_label:
        per_let = (mov.periodo_label or "").strip()
        ok_per = per_arch.replace(" ", "") == per_let.replace(" ", "")

    righe.append(
        {
            "campo": "Periodo retributivo",
            "archivio": per_arch,
            "lettura": per_let,
            "ok": ok_per,
            "nota": "Mese/anno movimento vs periodo da PDF (mese retribuito) o, in fallback, da descrizione documento."
            if ok_per is not None
            else "Periodo documento non determinato.",
        }
    )
    if ok_per is False:
        n_diff += 1

    # Codice fiscale dipendente
    cf_arch = (mov.cf_estratto or "").strip().upper()[:16]
    ok_cf: bool | None = None
    if cf_let and cf_arch:
        ok_cf = cf_let == cf_arch
    elif not cf_let and not cf_arch:
        ok_cf = None
    else:
        ok_cf = False
    righe.append(
        {
            "campo": "Codice fiscale (dip.)",
            "archivio": cf_arch or "—",
            "lettura": cf_let or "—",
            "ok": ok_cf,
            "nota": "Confronto tra CF salvato in import e CF letto dal cedolino."
            if ok_cf is not None
            else "Uno dei due CF mancante — confronto non applicabile.",
        }
    )
    if ok_cf is False:
        n_diff += 1

    # Nominativo (solo informativo, spesso formattato diversamente)
    nom_arch = (mov.nominativo_estratto or "").strip().upper()
    nom_let = (dip.get("Cognome e Nome") or "").strip().upper()
    if nom_arch and nom_let:
        # contiene almeno una parola in comune (cognome)
        tok_a = set(nom_arch.split())
        tok_b = set(nom_let.split())
        ok_nom = bool(tok_a & tok_b)
    else:
        ok_nom = None
    righe.append(
        {
            "campo": "Nominativo",
            "archivio": nom_arch or "—",
            "lettura": nom_let or "—",
            "ok": ok_nom,
            "nota": "Controllo soft (presenza parole in comune); differenze di formato sono frequenti.",
        }
    )
    # Nominativo: non incrementa n_diff (solo avviso visivo)

    # Conteggio voci (certezza lettura righe)
    n_voci = len(report.get("voci_retributive") or [])
    righe.append(
        {
            "campo": "Righe voce lette",
            "archivio": "—",
            "lettura": str(n_voci),
            "ok": True if n_voci > 0 else None,
            "nota": "Se zero, il PDF potrebbe avere testo su una sola riga o layout non standard."
            if n_voci == 0
            else f"{n_voci} righe codice riconosciute nel testo estratto.",
        }
    )

    stato = "ok" if n_diff == 0 else "differenze"

    return {
        "stato": stato,
        "righe": righe,
        "n_diff": n_diff,
        "movimento": mov,
    }
