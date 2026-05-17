"""
Formule lordo/netto ricavate dalla lettura della busta (testo PDF + dizionari merged),
poi applicate ai «Totali mensili» del report cedolino.

Evita di hardcodare solo coppie fisse di chiavi: si sceglie contributi e componente IRPEF
tra le chiavi effettivamente valorizzate nel merge, con priorità e fallback su pattern nel nome.
"""

from __future__ import annotations

import re
from decimal import Decimal
from typing import Any

from documenti.cedolini_tolleranze import TOLLERANZA_FORMULE_EURO

TOLLERANZA = TOLLERANZA_FORMULE_EURO

# Ordine di preferenza (layout Claude / TeamSystem / varianti)
_CONTRIBUTI_CHIAVI_PRIORITA: tuple[str, ...] = (
    "Contributi Sociali",
    "Tot. Contributi Sociali",
    "Contrib. Dipendente",
    "Quota dipendente INPS",
    "Contributo dipendente",
)

_IRPEF_TOT_PRIORITA: tuple[str, ...] = (
    "IRPEF Netta",
    "IRPEF netta",
    "Irpef netta",
)

_IRPEF_IRP_PRIORITA: tuple[str, ...] = (
    "IRPEF Erario",
    "IRPEF erario",
)

_RE_CONTRIB_NOME = re.compile(
    r"CONTRIB|INPS|I\.N\.P\.S|QUOTA\s+DIP|DIPENDENTE.*INPS|TOT\.\s*CONTR",
    re.I,
)
_RE_IRPEF_NOME = re.compile(r"IRPEF|RITENUTA\s+IRPEF", re.I)
_RE_ESCLUDI_IRPEF = re.compile(
    r"LORDA|ADDIZIONALE|ACCONTO\s+ADD|REGIONALE|COMUNALE|ARR\.",
    re.I,
)


def _parse_importo_it(s: Any) -> Decimal | None:
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


def _fmt_importo_it_da_decimal(d: Decimal) -> str:
    d = d.quantize(Decimal("0.01"))
    neg = d < 0
    if neg:
        d = -d
    ip = int(d)
    cents = int(((d - Decimal(ip)) * 100).quantize(Decimal("1")))
    s = str(ip)
    parts: list[str] = []
    for i, c in enumerate(reversed(s)):
        if i and i % 3 == 0:
            parts.append(".")
        parts.append(c)
    body = "".join(reversed(parts)) + f",{cents:02d}"
    return "-" + body if neg else body


def _meaningful(v: Any) -> bool:
    if v is None:
        return False
    s = str(v).strip()
    return bool(s) and s not in ("—", "N/D", "-", "N/D €")


# Etichette tipiche nel testo (stesso spirito di analizza_busta_paga_ts / libro paga)
_ET_LORDO: tuple[tuple[str, str], ...] = (
    ("TOTALE LORDO", r"TOTALE\s+LORDO"),
    ("TOT. LORDO", r"TOT\.\s*LORDO"),
    ("RETRIBUZIONE LORDA", r"RETRIBUZIONE\s+LORDA"),
    ("TOTALE COMPENSI", r"TOTALE\s+COMPENSI"),
)
_ET_NETTO: tuple[tuple[str, str], ...] = (
    ("NETTO IN BUSTA", r"NETTO\s+IN\s+BUSTA"),
    ("NETTO BUSTA", r"NETTO\s+BUSTA"),
    ("NETTO DA PAGARE", r"NETTO\s+DA\s+PAGARE"),
    ("IMPORTO NETTO", r"IMPORTO\s+NETTO"),
    ("TOTALE NETTO", r"TOTALE\s+NETTO"),
)


def etichette_lordo_netto_nel_testo(testo: str) -> dict[str, list[str]]:
    """Segnali testuali usati per documentare da quale busta sono state dedotte le formule."""
    if not testo:
        return {"lordo": [], "netto": []}
    lordo: list[str] = []
    netto: list[str] = []
    for etichetta, pat in _ET_LORDO:
        if re.search(pat, testo, re.I):
            lordo.append(etichetta)
    for etichetta, pat in _ET_NETTO:
        if re.search(pat, testo, re.I):
            netto.append(etichetta)
    return {"lordo": lordo, "netto": netto}


def _contributi_da_totali(tot: dict[str, str]) -> tuple[str | None, Decimal | None]:
    for k in _CONTRIBUTI_CHIAVI_PRIORITA:
        v = _parse_importo_it(tot.get(k))
        if v is not None:
            return k, v
    for k in sorted(tot.keys()):
        if _RE_CONTRIB_NOME.search(k):
            v = _parse_importo_it(tot.get(k))
            if v is not None:
                return k, v
    return None, None


def _irpef_componente_da_totali_e_irp(
    tot: dict[str, str], irp: dict[str, str]
) -> tuple[str | None, Decimal | None, str]:
    for k in _IRPEF_TOT_PRIORITA:
        v = _parse_importo_it(tot.get(k))
        if v is not None:
            return k, v, "totali_mensili"
    for k in _IRPEF_IRP_PRIORITA:
        v = _parse_importo_it(irp.get(k))
        if v is not None:
            return k, v, "irpef_addizionali"
    for k in sorted(tot.keys()):
        if not _RE_IRPEF_NOME.search(k) or _RE_ESCLUDI_IRPEF.search(k):
            continue
        v = _parse_importo_it(tot.get(k))
        if v is not None:
            return k, v, "totali_mensili"
    for k in sorted(irp.keys()):
        if not _RE_IRPEF_NOME.search(k) or _RE_ESCLUDI_IRPEF.search(k):
            continue
        v = _parse_importo_it(irp.get(k))
        if v is not None:
            return k, v, "irpef_addizionali"
    return None, None, "totali_mensili"


def somma_addizionali_da_blocco_irpef(irp: dict[str, str]) -> Decimal:
    """Somma addizionali / acconti / conguagli dal blocco IRPEF (valori assoluti)."""
    keys = (
        "Addizionale Regionale",
        "Addizionale Comunale",
        "Acconto addizionale comunale",
        "Arr. Attuale",
        "Arr. Attuale Addiz.",
        "Conguaglio IRPEF +/-",
    )
    s = Decimal("0")
    for k in keys:
        v = _parse_importo_it((irp or {}).get(k))
        if v is not None:
            s += abs(v)
    return s


def ricava_formule_da_lettura_busta(
    testo_pdf: str,
    totali_mensili: dict[str, str],
    irpef_addizionali: dict[str, str],
    voci: list[dict[str, Any]],
) -> dict[str, Any]:
    """
    Ricava quali chiavi e quali regole usare per lordo/netto in base a ciò che la busta
    ha effettivamente fornito (merge parser + testo).
    """
    tot = totali_mensili or {}
    irp = irpef_addizionali or {}
    ck, _cv = _contributi_da_totali(tot)
    ik, _iv, idict = _irpef_componente_da_totali_e_irp(tot, irp)
    eti = etichette_lordo_netto_nel_testo(testo_pdf)

    n_comp = 0
    for v in voci or []:
        imp = _parse_importo_it(v.get("importo"))
        if imp is None:
            continue
        if (v.get("tipo") or "").strip() != "Trattenuta":
            n_comp += 1

    add_sum = somma_addizionali_da_blocco_irpef(irp)
    add_txt = f", meno Σ addizionali blocco IRPEF ({_fmt_importo_it_da_decimal(add_sum)} €)" if add_sum > 0 else ""
    if ck and ik:
        det_netto = f"meno «{ck}», meno «{ik}» ({idict}){add_txt}"
    elif ck:
        det_netto = f"meno «{ck}»; IRPEF netta/erario non individuata nei dati"
    elif ik:
        det_netto = f"meno «{ik}» ({idict}); contributi dipendente non individuati nei totali"
    else:
        det_netto = "contributi e IRPEF netta non individuati: il netto calcolato non viene applicato"

    descr = (
        f"Formule ricavate da questa busta: lordo = somma righe «Competenza» ({n_comp} righe con importo) "
        f"se ce n’è almeno una, altrimenti valore «Totale Lordo» estratto; "
        f"netto (catena fiscale busta paga IT) = lordo base − contributi dip. − IRPEF netta − Σ addizionali "
        f"(righe tabella «Trattenuta» non sommate qui per evitare doppi conteggi con IRPEF). Dettaglio: {det_netto}."
    )

    return {
        "versione": 1,
        "fonte": "ricavato_da_lettura_busta",
        "lordo": {
            "regola": "somma_competenze_se_righe_altrimenti_totale_lordo_pdf",
            "chiave_lordo_pdf": "Totale Lordo",
            "righe_competenza_con_importo": n_comp,
        },
        "contributi": {
            "trovato": ck is not None,
            "origine": "totali_mensili",
            "chiave": ck,
        },
        "irpef_componente_netto": {
            "trovato": ik is not None,
            "origine": idict,
            "chiave": ik,
        },
        "netto": {
            "regola": "lordo_base_meno_contributi_meno_irpef_netta_meno_somma_addizionali_irpef",
        },
        "segnali_testo_pdf": eti,
        "descrizione_umana": descr,
    }


def applica_totali_secondo_formule_ricavate(
    tot: dict[str, str],
    voci: list[dict[str, Any]],
    irp: dict[str, str],
    formule: dict[str, Any],
) -> dict[str, str]:
    """
    Ribalta sul dizionario «Totali mensili» i valori calcolati secondo `formule`
    (stesso effetto della precedente applicazione hardcoded, ma contributi/IRPEF dalle chiavi ricavate).
    """
    somma_comp = Decimal("0")
    somma_tratt = Decimal("0")
    n_comp = 0
    for v in voci or []:
        imp = _parse_importo_it(v.get("importo"))
        if imp is None:
            continue
        tipo_r = (v.get("tipo") or "").strip()
        if tipo_r == "Trattenuta":
            somma_tratt += abs(imp)
        else:
            somma_comp += imp
            n_comp += 1

    lordo_pdf = _parse_importo_it(tot.get("Totale Lordo"))
    netto_pdf = _parse_importo_it(tot.get("Netto in Busta"))
    lordo_pdf_str = (tot.get("Totale Lordo") or "").strip()
    netto_pdf_str = (tot.get("Netto in Busta") or "").strip()

    ck, contributi = _contributi_da_totali(tot)
    if ck is None:
        contributi = None
    ik, irpef_netta, _idict = _irpef_componente_da_totali_e_irp(tot, irp)
    if ik is None:
        irpef_netta = None

    # Consistenza: se formule ha chiavi esplicite (stesso merge), riallinea
    f_ck = (formule.get("contributi") or {}).get("chiave")
    if f_ck and _parse_importo_it(tot.get(f_ck)) is not None:
        ck, contributi = f_ck, _parse_importo_it(tot.get(f_ck))
    f_ik = (formule.get("irpef_componente_netto") or {}).get("chiave")
    f_io = (formule.get("irpef_componente_netto") or {}).get("origine")
    if f_ik:
        if f_io == "irpef_addizionali":
            pv = _parse_importo_it(irp.get(f_ik))
            if pv is not None:
                ik, irpef_netta = f_ik, pv
        else:
            pv = _parse_importo_it(tot.get(f_ik))
            if pv is not None:
                ik, irpef_netta = f_ik, pv

    lordo_base = somma_comp if n_comp > 0 else lordo_pdf
    addizionali = somma_addizionali_da_blocco_irpef(irp)
    netto_applicabile = (
        lordo_base is not None
        and contributi is not None
        and irpef_netta is not None
    )
    netto_f: Decimal | None = None
    if netto_applicabile:
        # Catena fiscale coerente con «percorso_fiscale_italia»: niente Σ trattenute voci (spesso overlap IRPEF)
        netto_f = lordo_base - contributi - irpef_netta - addizionali  # type: ignore[operator]

    pairs: list[tuple[str, str]] = []

    if n_comp > 0:
        pairs.append(("Totale Lordo", _fmt_importo_it_da_decimal(somma_comp)))
        if lordo_pdf is not None and abs(lordo_pdf - somma_comp) > TOLLERANZA:
            ref = (
                lordo_pdf_str
                if _meaningful(lordo_pdf_str)
                else _fmt_importo_it_da_decimal(lordo_pdf)
            )
            pairs.append(("Totale Lordo (lettura PDF)", ref))
    elif "Totale Lordo" in tot:
        pairs.append(("Totale Lordo", tot["Totale Lordo"]))

    if netto_applicabile and netto_f is not None:
        pairs.append(("Netto in Busta", _fmt_importo_it_da_decimal(netto_f)))
        if netto_pdf is not None and abs(netto_pdf - netto_f) > TOLLERANZA:
            refn = (
                netto_pdf_str
                if _meaningful(netto_pdf_str)
                else _fmt_importo_it_da_decimal(netto_pdf)
            )
            pairs.append(("Netto in busta (lettura PDF)", refn))
    elif "Netto in Busta" in tot:
        pairs.append(("Netto in Busta", tot["Netto in Busta"]))

    for k, v in tot.items():
        if k in ("Totale Lordo", "Netto in Busta"):
            continue
        pairs.append((k, v))

    return dict(pairs)


def risolvi_contributi_da_totali(tot: dict[str, str]) -> tuple[str | None, Decimal | None]:
    """Usato dal controllo aritmetico per allineare le stesse chiavi delle formule."""
    return _contributi_da_totali(tot)


def risolvi_irpef_netta(
    tot: dict[str, str], irp: dict[str, str]
) -> tuple[str | None, Decimal | None, str]:
    k, v, src = _irpef_componente_da_totali_e_irp(tot, irp)
    return k, v, src
