"""
Motore «analizza_busta_paga» (TeamSystem / Studio Cipriano – Palermo): classificazione voci,
cedolini ordinari e di cessazione. I totali/IRPEF/progressivi dipendono dal layout: si integra
con le regex del modulo `leggi_busta_paga_claude` tramite `merge_reports_with_claude`.
"""

from __future__ import annotations

import re
from decimal import Decimal
from typing import Any

from documenti.cedolini_tolleranze import TOLLERANZA_FORMULE_EURO
from documenti.estrazione_busta_teamsystem import voci_teamsystem_in_ordine
from documenti.cedolino_percorso_fiscale_it import costruisci_percorso_fiscale_normativa_it
from documenti.formule_cedolino_da_busta import (
    applica_totali_secondo_formule_ricavate,
    ricava_formule_da_lettura_busta,
    risolvi_contributi_da_totali,
    risolvi_irpef_netta,
    somma_addizionali_da_blocco_irpef,
)


def _parse_importo_it(s: Any) -> Decimal | None:
    """Stesso algoritmo di ``cedolino_confronto_import.parse_importo_it`` (senza dip. Django)."""
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

# ══════════════════════════════════════════════════════════════════════
CATEGORIE_VOCI: dict[str, tuple[str, str]] = {
    "8001": ("COMPETENZA", "Lavoro Ordinario"),
    "8010": ("COMPETENZA", "Lavoro Domenicale 15%"),
    "8108": ("COMPETENZA", "Festivita' Non Goduta (ore)"),
    "8109": ("COMPETENZA", "Festivita' Godute (ore)"),
    "8020": ("COMPETENZA", "Lavoro Straordinario"),
    "8030": ("COMPETENZA", "Lavoro Notturno"),
    "8830": ("LIQUIDAZIONE", "Ferie Residue"),
    "8832": ("LIQUIDAZIONE", "ROL Residui"),
    "8834": ("LIQUIDAZIONE", "Tredicesima Residua"),
    "8835": ("LIQUIDAZIONE", "Quattordicesima Residua"),
    "8400": ("LIQUIDAZIONE", "Trattamento Fine Rapporto (TFR)"),
    "8992": ("LIQUIDAZIONE", "Trattamento Integrativo DL 3/2020"),
    "405": ("LIQUIDAZIONE", "Indennità Sostitutiva Preavviso"),
    "9824": ("BONUS_LEGGE", "Somma Art.1 c.4 L.207/2024 (Detassazione)"),
    "1800": ("TRATTENUTA", "Rata Addizionale Regionale A.P."),
    "1802": ("TRATTENUTA", "Rata Addizionale Comunale A.P."),
    "1812": ("TRATTENUTA", "Acconto Addizionale Comunale"),
}

COLORI = {
    "COMPETENZA": "💼",
    "ACCESSORIO": "➕",
    "LIQUIDAZIONE": "📦",
    "BONUS_LEGGE": "🎁",
    "TRATTENUTA": "🔻",
    "PREVIDENZA": "🏛️",
    "ALTRO": "📎",
    "N/C": "❓",
}


def classifica_voce_per_codice(cod: str) -> tuple[str, str]:
    """
    Classifica ogni codice voce TeamSystem (anche non presente in CATEGORIE_VOCI).
    Ritorna (chiave_categoria, etichetta_se_manca_descrizione_pdf).
    """
    if cod in CATEGORIE_VOCI:
        c, d = CATEGORIE_VOCI[cod]
        return c, d
    if not cod.isdigit():
        return "ALTRO", "Voce generica"
    n = int(cod)
    if 400 <= n <= 499:
        return "LIQUIDAZIONE", "Voce liquidazione / fine rapporto (4xx)"
    if 8000 <= n <= 8099:
        return "COMPETENZA", "Retribuzione e ore (80xx)"
    if 8100 <= n <= 8699:
        return "ACCESSORIO", "Indennità e accessori (81–86xx)"
    if 8700 <= n <= 8799:
        return "ACCESSORIO", "Altri elementi retributivi (87xx)"
    if 8800 <= n <= 8999:
        return "LIQUIDAZIONE", "Liquidazioni, ratei e arretrati (88–89xx)"
    if 9000 <= n <= 9799:
        return "ALTRO", "Altra voce in conto economico (90–97xx)"
    if 9800 <= n <= 9899:
        return "BONUS_LEGGE", "Trattamento fiscale / bonus normativi (98xx)"
    if 1000 <= n <= 1999:
        return "TRATTENUTA", "Trattenuta fiscale o contributo (10–19xx)"
    if 2000 <= n <= 4999:
        return "TRATTENUTA", "Ritenute e addebiti (20–49xx)"
    if 5000 <= n <= 6999:
        return "ALTRO", "Altra voce codice 50–69xx"
    if 7000 <= n <= 7999:
        return "PREVIDENZA", "Contributi e addebiti previdenziali (70–79xx)"
    return "ALTRO", "Voce (descrizione da PDF)"

VOCI_MANUALI: list[tuple[str, str, str]] = [
    ("8830", r"FERIE RESIDUE", r"8830[^\n]+?([\d,]+)\s+63,94642\s+([\d.,]+)"),
    ("8832", r"ROL RESIDUI", r"8832[^\n]+?([\d,]+)\s+9,66632\s+([\d.,]+)"),
    ("8834", r"TREDICESIMA RESIDUA", r"8834[^\n]+?([\d,]+)\s+138,55083\s+([\d.,]+)"),
    ("8835", r"QUATTORDICESIMA RESIDUA", r"8835[^\n]+?([\d,]+)\s+138,55083\s+([\d.,]+)"),
    ("8400", r"TRATTAMENTO FINE RAPPORTO", r"8400[^\n]+?([\d.,]+)"),
    ("8992", r"TRATTAMENTO INT\. DL 3/20", r"8992\s+TRATTAMENTO INT\. DL 3/20\s+([\d.,]+)"),
    ("9824", r"SOMMA ART\.1 C\.4 L\.207/24", r"9824\s+SOMMA ART\.1 C\.4 L\.207/24\s+([\d.,]+)"),
    ("405", r"IND\.SOST\.PREAVVISO", r"405\s+IND\.SOST\.PREAVVISO\s+([\d,]+)\s+63,94642\s+([\d.,]+)"),
    ("8001", r"LAVORO ORDINARIO", r"8001[^\n]+?([\d,]+)\s+9,16512\s+([\d.,]+)"),
    ("8010", r"LAVORO DOMENICALE", r"8010[^\n]+?([\d,]+)\s+10,53989\s+([\d.,]+)"),
    ("8108", r"FEST\.?\s*NON\s*GODUTA", r"8108[^\n]+?([\d,]+)\s+[\d,]+\s+([\d.,]+)"),
    ("1800", r"RATA ADDIZ\.REGIONALE", r"1800[^\n]+?([\d,]+)"),
    ("1802", r"RATA ADD\.COMUNALE", r"1802[^\n]+?([\d,]+)"),
    ("1812", r"ACCONTO ADD\.COMUNALE", r"1812[^\n]+?([\d,]+)"),
]


def _cerca(pattern: str, testo: str, gruppo: int = 1, default: str = "—") -> str:
    m = re.search(pattern, testo, re.MULTILINE | re.DOTALL)
    return m.group(gruppo).strip() if m else default


def _meaningful(v: Any) -> bool:
    if v is None:
        return False
    s = str(v).strip()
    if not s or s in ("—", "N/D", "-"):
        return False
    if "cedolino ordinario" in s.lower():
        return False
    return True


def parse_dipendente_analizza(t: str) -> dict[str, str]:
    """Riga MESE … MATR … COGNOME NOME DATA_ASS + CF + cessazione + ore."""
    m_riga = re.search(
        r"(\w+ \d{4})\s+(\d+)\s+(\d+)\s+(\d{10})\s+(\d+)\s+\d+\s+\d+\s+([\w\s]+?)\s+(\d{2}/\d{2}/\d{2})",
        t,
    )
    m_cf = re.search(
        r"([A-Z]{6}\d{2}[A-Z]\d{2}[A-Z]\d{3}[A-Z])\s+(\w+)\s+(\d{2}/\d{2}/\d{2})",
        t,
    )
    m_cess = re.search(
        r"[A-Z]{6}\d{2}[A-Z]\d{2}[A-Z]\d{3}[A-Z]\s+\w+\s+\d{2}/\d{2}/\d{2}\s+\d{2}/\d{2}/\d{2}\s+(\d{2}/\d{2}/\d{2})",
        t,
    )
    m_gg = re.search(
        r"\d{2}/\d{2}/\d{2}(?:\s+\d{2}/\d{2}/\d{2})?\s+(\d{2})\s+(\d+,\d+)",
        t,
    )
    m_qual = re.search(r"\n([A-Z ]+)\s+(\d+\^)\s+(\d+)\n", t)
    m_pt = re.search(r"(\d{2,3},\d{2})\s+\d+\^\s+\d+", t)
    m_sc = re.search(r"\d{2}/\d{2}/\d{2}\s+(\d+)\s+\d{2}/\d{2}", t)

    cognome_nome = m_riga.group(6).strip() if m_riga else "—"
    data_ass = m_riga.group(7) if m_riga else "—"
    cess = m_cess.group(1) if m_cess else "—  (cedolino ordinario)"

    qual_liv = "—"
    if m_qual:
        qual_liv = f"{m_qual.group(1).strip()} {m_qual.group(2)} (cart. {m_qual.group(3)})"

    pt = (m_pt.group(1) + " %") if m_pt else "—"

    return {
        "Mese Retribuito": m_riga.group(1) if m_riga else "—",
        "Cognome e Nome": cognome_nome,
        "Data Assunzione": data_ass,
        "Data Cessazione": cess,
        "Codice Fiscale": m_cf.group(1) if m_cf else "—",
        "Comune di Residenza": m_cf.group(2) if m_cf else "—",
        "Data di Nascita": m_cf.group(3) if m_cf else "—",
        "Matricola INPS": m_riga.group(2) if m_riga else "—",
        "Matricola INPS Az.": m_riga.group(4) if m_riga else "—",
        "Posizione INAIL": m_riga.group(5) if m_riga else "—",
        "Qualifica/Livello": qual_liv,
        "% Part Time": pt,
        "Scatti Anzianità": m_sc.group(1) if m_sc else "—",
        "GG. Contributivi": m_gg.group(1) if m_gg else "—",
        "Ore Contrattuali": m_gg.group(2) if m_gg else "—",
    }


def parse_retribuzione_base_analizza(t: str) -> dict[str, str]:
    vals5 = re.findall(r"(\d+,\d{5})", t)
    edb = _cerca(r"EL\.DIS\.BIL\s*\n([\d,]+)", t)
    retr_oraria = vals5[3] if len(vals5) > 3 else (vals5[2] if len(vals5) > 2 else "—")
    retr_giorn = _cerca(r"(\d{2},\d{2})\s*\n", t)
    out: dict[str, str] = {
        "Paga Base (oraria)": vals5[0] if len(vals5) > 0 else "—",
        "Contingenza (oraria)": vals5[1] if len(vals5) > 1 else "—",
    }
    if edb != "—":
        out["Elemento Dist. Bilaterale"] = edb
    if len(vals5) > 2:
        out["Scatti Anzianità (orari)"] = vals5[2]
    out["Retribuzione Oraria Tot."] = retr_oraria
    out["Retribuzione Giornaliera"] = retr_giorn
    return out


def parse_voci_universal(t: str) -> list[dict[str, str]]:
    trovate: list[dict[str, str]] = []
    codici_visti: set[str] = set()

    for codice, _desc_re, pat in VOCI_MANUALI:
        if codice in codici_visti:
            continue
        m = re.search(pat, t)
        if not m:
            continue

        cat, desc_std = CATEGORIE_VOCI.get(codice, ("N/C", "Voce non classificata"))
        icona = COLORI.get(cat, "❓")

        if codice in ("8830", "8832", "8834", "8835", "405", "8001", "8010"):
            ore = m.group(1)
            imp = m.group(2)
            base_map = {
                "8830": "63,94642",
                "8832": "9,66632",
                "8834": "138,55083",
                "8835": "138,55083",
                "8001": "9,16512",
                "8010": "10,53989",
                "405": "63,94642",
            }
            base = base_map.get(codice, "—")
            tipo = "Competenza"
        else:
            ore = "—"
            base = "—"
            imp = m.group(1)
            tipo = "Trattenuta" if cat == "TRATTENUTA" else "Competenza"

        trovate.append(
            {
                "codice": codice,
                "descrizione": desc_std,
                "categoria": cat,
                "icona": icona,
                "ore_gg": ore,
                "base": base,
                "importo": imp,
                "tipo": tipo,
            }
        )
        codici_visti.add(codice)

    return trovate


def parse_totali_analizza(t: str) -> dict[str, str]:
    return {
        "Totale Lordo": _cerca(r"([\d.]+,\d{2})\s+783,00\s+74,07", t),
        "Imponibile Contr. Soc.": _cerca(r"([\d.]+,\d{2})\s+74,07\s+74,07", t),
        "Tot. Contributi Sociali": _cerca(r"\d+,00\s+([\d,]+)\s+74,07", t),
        "Imponibile IRPEF (mese)": _cerca(r"(-?[\d.,]+)\s+23,00", t),
        "Oneri Deducibili": _cerca(r"(-255,70)\s+23,00", t),
        "Tot. Trattenute": _cerca(r"959,20\s+([\d.]+,\d{2})", t),
        "Netto in Busta": _cerca(r"0,26\s+959,20\s+([\d.]+,\d{2})", t),
    }


def parse_irpef_analizza(t: str) -> dict[str, str]:
    return {
        "IRPEF Erario": _cerca(r"(341,38)", t),
        "Addizionale Regionale": _cerca(r"341,38\s+([\d,]+)", t),
        "Addizionale Comunale": _cerca(r"341,38\s+[\d,]+\s+([\d,]+)", t),
        "Arr. Attuale": _cerca(r"341,38\s+[\d,]+\s+[\d,]+\s+([\d,]+)", t),
        "Netto Busta (conferma)": _cerca(r"([\d.]+,\d{2})\s*\nSIGLA", t),
        "Sigla/Nota": _cerca(r"SIGLA DESCRIZIONE\s*\n(\w+)\s+([\w\s]+)", t, 2),
    }


def parse_ferie_analizza(t: str) -> dict[str, str]:
    m_fgod = re.search(r"([\d,]+)\s+([\d,]+)\s+12,21\s+12,21", t)
    return {
        "Ferie Godute (liquidate)": m_fgod.group(1) if m_fgod else "—",
        "Ferie Residue (liquidate)": m_fgod.group(2) if m_fgod else "—",
        "ROL Goduti": _cerca(r"(12,21)\s+12,21", t),
        "ROL Residui": _cerca(r"12,21\s+(12,21)", t),
    }


def parse_previdenziale_analizza(t: str) -> dict[str, str]:
    return {
        "Imponibile INAIL": _cerca(r"1\s+([\d.]+,\d{2})\s+125,13", t),
        "GG. INAIL (pos. sett.)": _cerca(r"1\s+783,00\s+([\d,]+)", t),
        "TFR Mese": _cerca(r"783,00\s+[\d,]+\n([\d,]+)", t),
        "Detrazioni Spettanti": _cerca(r"(771,29)", t),
    }


def parse_progressivi_analizza(t: str) -> dict[str, str]:
    m = re.search(
        r"([\d.]+,\d{2})\s+([\d.]+,\d{2})\s+([\d,]+)\s+([\d.]+,\d{2})\s+([\d.]+,\d{2})\s+([\d,]+)\s+([\d.]+,\d{2})\s*$",
        t,
        re.MULTILINE,
    )
    if m:
        return {
            "Prog. Imponibile Contrib. (INPS)": m.group(1),
            "Prog. Imp. Contrib. Soc.": m.group(2),
            "Prog. Contrib. Soc.": m.group(3),
            "Prog. Imponibile IRPEF": m.group(4),
            "Prog. IRPEF Lorda": m.group(5),
            "Prog. Tot. Detrazioni": m.group(6),
            "Prog. IRPEF Pagata": m.group(7),
        }
    return {}


def _voce_to_row(v: dict[str, str]) -> dict[str, str]:
    og = v.get("ore_gg") or "—"
    if og not in ("—", "") and not str(og).strip().endswith("ore"):
        og = f"{og} ore" if re.match(r"^\d", str(og)) else og
    return {
        "codice": v["codice"],
        "descrizione": v["descrizione"],
        "ore_giorni": og,
        "base": v.get("base") or "—",
        "importo": v.get("importo") or "—",
        "tipo": v.get("tipo") or "—",
        "categoria": v.get("categoria", ""),
        "icona": v.get("icona", ""),
    }


def _voce_teamsystem_a_riga(vo: dict[str, Any]) -> dict[str, str]:
    """Converte una riga parser TeamSystem nel dict usato da template / JSON cedolino."""
    cod = str(vo.get("codice") or "").strip()
    cat, fallback_label = classifica_voce_per_codice(cod)
    pdf_descr = (vo.get("descrizione") or "").strip()
    if cod in CATEGORIE_VOCI:
        descr = CATEGORIE_VOCI[cod][1]
    elif pdf_descr:
        descr = pdf_descr[:200]
    else:
        descr = fallback_label
    icona = COLORI.get(cat, COLORI["ALTRO"])
    if cat in ("TRATTENUTA", "PREVIDENZA"):
        tipo_ui = "Trattenuta"
    else:
        tipo_ui = "Competenza"

    og = vo.get("ore_giorni") or ""
    og = og.strip() if isinstance(og, str) else ""
    if not og:
        og = "—"
    elif not og.endswith("ore") and re.match(r"^\d", og):
        og = f"{og} ore"

    return {
        "codice": cod,
        "descrizione": descr,
        "ore_giorni": og,
        "base": (vo.get("base_unitaria") or "").strip() or "—",
        "importo": (vo.get("importo") or "").strip() or "—",
        "tipo": tipo_ui,
        "categoria": cat,
        "icona": icona,
    }


def normalizza_riga_voce_classificata(v: Any) -> dict[str, str]:
    """
    Garantisce categoria + icona + tipo UI per ogni voce (Claude, analizza manuale o TeamSystem).
    """
    if not isinstance(v, dict):
        return {
            "codice": "",
            "descrizione": "—",
            "ore_giorni": "—",
            "base": "—",
            "importo": "—",
            "tipo": "Competenza",
            "categoria": "ALTRO",
            "icona": COLORI["ALTRO"],
        }
    cod = str(v.get("codice") or "").strip()
    cat_cat, fb = classifica_voce_per_codice(cod)
    prev_cat = (v.get("categoria") or "").strip()
    if prev_cat and prev_cat not in ("N/C", ""):
        cat = prev_cat
    else:
        cat = cat_cat
    pdf_d = (v.get("descrizione") or "").strip()
    if cod in CATEGORIE_VOCI:
        descr = CATEGORIE_VOCI[cod][1]
    elif pdf_d:
        descr = pdf_d[:200]
    else:
        descr = fb
    icona = COLORI.get(cat, COLORI["ALTRO"])
    tipo_ui = "Trattenuta" if cat in ("TRATTENUTA", "PREVIDENZA") else "Competenza"
    og = v.get("ore_giorni") or v.get("ore_gg") or ""
    og = og.strip() if isinstance(og, str) else ""
    if not og:
        og = "—"
    elif not str(og).endswith("ore") and re.match(r"^\d", str(og)):
        og = f"{og} ore"
    base = (v.get("base") or "").strip() or "—"
    imp = (v.get("importo") or "").strip() or "—"
    return {
        "codice": cod,
        "descrizione": descr,
        "ore_giorni": og,
        "base": base,
        "importo": imp,
        "tipo": tipo_ui,
        "categoria": cat,
        "icona": icona,
    }


def _fmt_importo_it_da_decimal(d: Decimal) -> str:
    """Formato italiano tipo 1.234,56 (senza simbolo €)."""
    d = d.quantize(Decimal("0.01"))
    neg = d < 0
    if neg:
        d = -d
    ip = int(d)
    cents = int(((d - Decimal(ip)) * 100).quantize(Decimal("1")))
    s = str(ip)
    parts = []
    for i, c in enumerate(reversed(s)):
        if i and i % 3 == 0:
            parts.append(".")
        parts.append(c)
    body = "".join(reversed(parts)) + f",{cents:02d}"
    return "-" + body if neg else body


# Etichette tipiche TeamSystem / Zucchetti per importi lordo e netto in coda cedolino
_LORDO_ETICHETTE = (
    r"TOTALE\s+LORDO",
    r"TOT\.\s*LORDO",
    r"LORDO\s+TOTALE",
    r"RETRIBUZIONE\s+LORDA",
    r"RET\.\s*LORDA",
)
_NETTO_ETICHETTE = (
    r"NETTO\s+IN\s+BUSTA",
    r"NETTO\s+BUSTA",
    r"NETTO\s+DA\s+PAGARE",
    r"NETTO\s+CORRISPOSTO",
    r"NETTO\s+A\s+DISPOSIZIONE",
    r"IMPORTO\s+NETTO",
    r"TOTALE\s+NETTO",
    r"NETTO\s+EROGATO",
    r"NETTO\s+MENSILE",
)
_RE_IMPORTO_VICINO = re.compile(r"\d{1,3}(?:\.\d{3})*,\d{2}|\d+,\d{2}")


def _importo_dopo_etichetta(testo: str, etichette: tuple[str, ...]) -> str | None:
    """Primo importo italiano trovato dopo un'etichetta (entro ~200 caratteri)."""
    for lab in etichette:
        for m in re.finditer(lab, testo, flags=re.IGNORECASE):
            chunk = testo[m.end() : m.end() + 200]
            mm = _RE_IMPORTO_VICINO.search(chunk)
            if mm:
                return mm.group(0).strip()
    return None


def riconcilia_totali_lordo_netto_da_testo(t: str, tot: dict[str, str]) -> dict[str, str]:
    """
    Allinea «Totale Lordo» e «Netto in Busta» a valori trovati vicino alle etichette nel testo,
    con controllo di coerenza (lordo ≥ netto). Riduce incongruenze tra regex ancorate a numeri
    fissi (783,00 / 1.471,00 / 0,01) e il PDF reale.
    """
    out = dict(tot or {})
    l_raw = _importo_dopo_etichetta(t, _LORDO_ETICHETTE)
    n_raw = _importo_dopo_etichetta(t, _NETTO_ETICHETTE)
    l_d = _parse_importo_it(l_raw) if l_raw else None
    n_d = _parse_importo_it(n_raw) if n_raw else None

    if l_d is not None and n_d is not None:
        if l_d + Decimal("0.005") >= n_d:
            out["Totale Lordo"] = l_raw
            out["Netto in Busta"] = n_raw
        # se lordo < netto: probabile cattura errata, non sovrascrivere
    else:
        if l_raw and l_d is not None:
            out["Totale Lordo"] = l_raw
        if n_raw and n_d is not None:
            out["Netto in Busta"] = n_raw
    return out


def riconcilia_addizionali_irpef_da_voci(
    voci: list[dict[str, Any]], irp: dict[str, str]
) -> dict[str, str]:
    """
    Allinea Addizionale regionale/comunale (e acconto) agli importi delle righe voce
    1800 / 1802 / 1812. Evita incongruenze tra tabella voci e blocco IRPEF quando i regex
    sul totale IRPEF (es. ancore 341,38 o 37,05) non coincidono col layout del cedolino.
    """
    out = dict(irp or {})
    mapping = (
        ("1800", "Addizionale Regionale"),
        ("1802", "Addizionale Comunale"),
        ("1812", "Acconto addizionale comunale"),
    )
    for cod, label in mapping:
        tot: Decimal | None = None
        for v in voci or []:
            if str(v.get("codice") or "").strip() != cod:
                continue
            p = _parse_importo_it(v.get("importo"))
            if p is not None:
                tot = p if tot is None else tot + p
        if tot is not None:
            out[label] = _fmt_importo_it_da_decimal(tot)
    return out


def costruisci_controllo_aritmetico_cedolino(
    voci: list[dict[str, Any]],
    tot: dict[str, str],
    irp: dict[str, str],
) -> dict[str, Any]:
    """
    Somma gli importi delle righe voce (competenze vs trattenute) e confronta con lordo/netto
    estratti dal PDF e con contributi/IRPEF dai blocchi totali, per evidenziare scostamenti
    rispetto all'archivio e capire se mancano righe in lettura.
    """
    somma_comp = Decimal("0")
    somma_tratt = Decimal("0")
    n_comp = n_tratt = 0
    for v in voci or []:
        imp = _parse_importo_it(v.get("importo"))
        if imp is None:
            continue
        tipo_r = (v.get("tipo") or "").strip()
        if tipo_r == "Trattenuta":
            somma_tratt += abs(imp)
            n_tratt += 1
        else:
            somma_comp += imp
            n_comp += 1

    lordo_d = _parse_importo_it(tot.get("Totale Lordo"))
    netto_d = _parse_importo_it(tot.get("Netto in Busta"))

    _, contributi = risolvi_contributi_da_totali(tot)
    _, irpef_netta, _ = risolvi_irpef_netta(tot, irp)
    addizionali = somma_addizionali_da_blocco_irpef(irp)

    delta_lordo: Decimal | None = None
    coer_lordo: bool | None = None
    if lordo_d is not None and n_comp > 0:
        delta_lordo = lordo_d - somma_comp
        coer_lordo = abs(delta_lordo) <= TOLLERANZA_FORMULE_EURO

    # Netto atteso: stessa catena di «percorso fiscale IT» e «Totali mensili» applicati
    # (lordo − contributi − IRPEF netta − Σ addizionali blocco IRPEF).
    netto_calc: Decimal | None = None
    coer_netto: bool | None = None
    delta_netto: Decimal | None = None
    formula_netto_completa = (
        lordo_d is not None
        and netto_d is not None
        and contributi is not None
        and irpef_netta is not None
    )
    if formula_netto_completa:
        netto_calc = lordo_d - contributi - irpef_netta - addizionali  # type: ignore[operator]
        delta_netto = netto_d - netto_calc
        coer_netto = abs(delta_netto) <= TOLLERANZA_FORMULE_EURO

    def _fmt(d: Decimal | None) -> str:
        return _fmt_importo_it_da_decimal(d) if d is not None else "—"

    nota = (
        "Allineato al percorso fiscale busta paga: netto atteso = lordo − contributi dip. − IRPEF netta − addizionali "
        "(blocco IRPEF). La somma «Trattenuta» in tabella voci resta solo controllo incrociato (non nella formula netto)."
    )

    return {
        "somma_importi_voci_competenza": _fmt(somma_comp),
        "n_righe_competenza": n_comp,
        "somma_importi_voci_trattenuta": _fmt(somma_tratt),
        "n_righe_trattenuta": n_tratt,
        "lordo_cedolino": _fmt(lordo_d),
        "delta_lordo_vs_somma_competenze": _fmt(delta_lordo) if delta_lordo is not None else "—",
        "coerenza_lordo_ok": coer_lordo,
        "contributi_da_totali": _fmt(contributi),
        "irpef_netta_componente": _fmt(irpef_netta),
        "addizionali_blocco_irpef": _fmt(addizionali),
        "netto_atteso_da_formula": _fmt(netto_calc),
        "netto_cedolino": _fmt(netto_d),
        "delta_netto_vs_atteso": _fmt(delta_netto) if delta_netto is not None else "—",
        "coerenza_netto_ok": coer_netto,
        "formula_netto_completa": formula_netto_completa,
        "nota_metodo": nota,
    }


def _merge_flat_dict(pref: dict[str, str], fb: dict[str, str]) -> dict[str, str]:
    keys = set(pref) | set(fb)
    out: dict[str, str] = {}
    for k in keys:
        pv, fv = pref.get(k), fb.get(k)
        if _meaningful(pv):
            out[k] = pv
        elif _meaningful(fv):
            out[k] = fv
        else:
            out[k] = (pv if pv not in (None, "") else fv) or "N/D"
    return out


def merge_reports_with_claude(t: str, cl: dict[str, Any]) -> dict[str, Any]:
    """
    `cl` = dict prodotto da `_build_report_claude_dict` (stesse chiavi template).
    Voci: preferisce il parser «analizza» se trova almeno una voce, altrimenti Claude.
    """
    az_dip = parse_dipendente_analizza(t)
    az_ret = parse_retribuzione_base_analizza(t)
    az_voci = parse_voci_universal(t)
    az_tot = parse_totali_analizza(t)
    az_irp = parse_irpef_analizza(t)
    az_fer = parse_ferie_analizza(t)
    az_prv = parse_previdenziale_analizza(t)
    az_prg = parse_progressivi_analizza(t)

    cl_dip = cl.get("dati_dipendente") or {}
    cess_az = az_dip.get("Data Cessazione", "")
    tipo = (
        "CESSAZIONE"
        if _meaningful(cess_az) and "ordinario" not in cess_az.lower()
        else "ORDINARIO"
    )

    dip = _merge_flat_dict(az_dip, {k: str(v) for k, v in cl_dip.items()})
    if not _meaningful(dip.get("Data Cessazione")):
        dip["Data Cessazione"] = "—"

    ret = _merge_flat_dict(az_ret, {k: str(v) for k, v in (cl.get("retribuzione_base") or {}).items()})
    cl_ret = cl.get("retribuzione_base") or {}
    if _meaningful(cl_ret.get("Scatti Anzianità (orari)")) and not _meaningful(
        ret.get("Scatti Anzianità (orari)")
    ):
        ret["Scatti Anzianità (orari)"] = str(cl_ret["Scatti Anzianità (orari)"])

    voci_ts = [_voce_teamsystem_a_riga(v) for v in voci_teamsystem_in_ordine(t)]
    if voci_ts:
        voci = voci_ts
    elif len(az_voci) >= 1:
        voci = [_voce_to_row(v) for v in az_voci]
    else:
        voci = list(cl.get("voci_retributive") or [])
    voci = [normalizza_riga_voce_classificata(x) for x in voci]

    tot = _merge_flat_dict(az_tot, {k: str(v) for k, v in (cl.get("totali_mensili") or {}).items()})
    tot = riconcilia_totali_lordo_netto_da_testo(t, tot)
    irp = _merge_flat_dict(az_irp, {k: str(v) for k, v in (cl.get("irpef_addizionali") or {}).items()})
    irp = riconcilia_addizionali_irpef_da_voci(voci, irp)
    formule = ricava_formule_da_lettura_busta(t, tot, irp, voci)
    tot = applica_totali_secondo_formule_ricavate(tot, voci, irp, formule)
    if _meaningful(tot.get("Netto in Busta")):
        irp = dict(irp)
        irp["Netto Busta (conferma)"] = str(tot["Netto in Busta"]).strip()
    fer = _merge_flat_dict(az_fer, {k: str(v) for k, v in (cl.get("ferie_permessi_rol") or {}).items()})
    prv = _merge_flat_dict(az_prv, {k: str(v) for k, v in (cl.get("dati_previdenziali") or {}).items()})
    prg = _merge_flat_dict(az_prg, {k: str(v) for k, v in (cl.get("progressivi_annui") or {}).items()})
    controllo = costruisci_controllo_aritmetico_cedolino(voci, tot, irp)
    percorso_fiscale = costruisci_percorso_fiscale_normativa_it(voci, tot, irp)

    return {
        "dati_aziendali": cl.get("dati_aziendali") or {},
        "tipo_cedolino": tipo,
        "dati_dipendente": dip,
        "retribuzione_base": ret,
        "voci_retributive": voci,
        "totali_mensili": tot,
        "irpef_addizionali": irp,
        "ferie_permessi_rol": fer,
        "dati_previdenziali": prv,
        "progressivi_annui": prg,
        "controllo_aritmetico": controllo,
        "formule_cedolino": formule,
        "percorso_fiscale_italia": percorso_fiscale,
    }
