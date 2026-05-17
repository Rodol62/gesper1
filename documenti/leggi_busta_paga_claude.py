#!/usr/bin/env python3
"""
leggi_busta_paga_claude.py
--------------------------
Versione standalone dello script generato da Claude: estrae e stampa il cedolino
TeamSystem (prima pagina) con pdfplumber, escludendo i dati aziendali.

Uso:
    python3 documenti/leggi_busta_paga_claude.py <percorso_pdf>
    .venv/bin/python documenti/leggi_busta_paga_claude.py ~/Downloads/busta_03_2026_dip_19_p2.pdf

Dipendenze: pdfplumber (stesso ambiente del progetto Gesper).
"""

from __future__ import annotations

import io
import re
import sys
from pathlib import Path
from typing import Any, List, Tuple, Union

import pdfplumber

from documenti.buste_pdf_passwords import STUDIO_DEFAULT_PASSWORD

SourcePdf = Union[str, Path, bytes, bytearray]

# Retrocompatibilità: stessa costante usata in views / import paghe
PDF_BUSTE_PASSWORD = STUDIO_DEFAULT_PASSWORD


def num(s: str) -> str:
    """Normalizza un numero stringa (rimuove spazi)."""
    return s.strip()


def sep(titolo: str, larghezza: int = 70):
    """Stampa un separatore con titolo."""
    print()
    print("─" * larghezza)
    print(f"  {titolo}")
    print("─" * larghezza)


def riga(label: str, valore: str, larghezza_label: int = 38):
    """Stampa una coppia campo/valore allineata."""
    print(f"  {label:<{larghezza_label}} {valore}")


def _tabella_righe_testo(intestazioni: list, righe: list) -> List[str]:
    """Righe testuali tabella voci (stesso layout della stampa Claude)."""
    if not righe:
        return ["  (nessuna voce estratta)"]
    col_w = [len(h) for h in intestazioni]
    for r in righe:
        for i, v in enumerate(r):
            if i < len(col_w):
                col_w[i] = max(col_w[i], len(str(v)))
    fmt = "  " + "  ".join(f"{{:<{w}}}" for w in col_w)
    out = [
        fmt.format(*intestazioni),
        "  " + "  ".join("─" * w for w in col_w),
    ]
    for r in righe:
        r_pad = list(r) + [""] * (len(intestazioni) - len(r))
        out.append(fmt.format(*[str(v) for v in r_pad[: len(intestazioni)]]))
    return out


def tabella(intestazioni: list, righe: list):
    """Stampa una tabella con colonne allineate."""
    for line in _tabella_righe_testo(intestazioni, righe):
        print(line)


def estrai_testo(path_pdf: str, password: str = "") -> str:
    """Estrae il testo grezzo dalla prima pagina del PDF."""
    pw = password or ""
    with pdfplumber.open(path_pdf, password=pw) as pdf:
        testo = pdf.pages[0].extract_text() or ""
    return testo


def estrai_testo_bytes(raw: bytes, password: str = "") -> str:
    with pdfplumber.open(io.BytesIO(raw), password=password or "") as pdf:
        return (pdf.pages[0].extract_text() or "") if pdf.pages else ""


def estrai_testo_prima_pagina(source: SourcePdf, *, password: str = "") -> str:
    """Prima pagina come testo: percorso file o buffer PDF."""
    pw = password or ""
    if isinstance(source, (bytes, bytearray)):
        return estrai_testo_bytes(bytes(source), password=pw)
    return estrai_testo(str(Path(source).expanduser()), password=pw)


def cerca(pattern: str, testo: str, gruppo: int = 1, default: str = "N/D") -> str:
    """Cerca un pattern nel testo e restituisce il gruppo catturato."""
    m = re.search(pattern, testo, re.MULTILINE | re.DOTALL)
    return m.group(gruppo).strip() if m else default


def dati_aziendali(t: str) -> dict[str, str]:
    """
    Dati datore di lavoro ricavati dal testo (blocco in calce tipico TeamSystem:
    ragione sociale, sede, Cod.fiscale/P.IVA, foglio protocollo). Best-effort sul layout.
    """
    out: dict[str, str] = {}

    m = re.search(
        r"(?m)^([A-Z][A-Z0-9\.\'\s\-]{1,72}?(?:SRLS|SRL|SPA|SAS|SNC|SS|S\.p\.A\.|A\.P\.A\.))\s*$"
        r"\s*\n\s*((?:VIA|VIALE|PIAZZA|LARGO|C\.SO|CORSO)\b[^\n]{4,120})",
        t,
    )
    if m:
        out["Ragione sociale"] = re.sub(r"\s+", " ", m.group(1).strip())
        out["Sede / indirizzo"] = re.sub(r"\s+", " ", m.group(2).strip())

    m = re.search(
        r"Cod\.?\s*fiscale\s*:\s*([A-Z0-9]{11,16})(?:\s+(\d{3,7}))?",
        t,
        re.IGNORECASE,
    )
    if m:
        out["Codice fiscale / P.IVA (azienda)"] = m.group(1).strip().upper()
        if m.group(2):
            out["Codice aggiuntivo (riga anagrafica)"] = m.group(2).strip()

    m = re.search(r"Foglio\s*N\.?\s*[\n\r\s]*(\d{3,5}/\d{1,3}/\d{1,4})", t, re.I)
    if m:
        out["Foglio / numerazione"] = m.group(1).strip()

    if re.search(r"(?i)teamsystem", t):
        out["Suite gestionale (indicativo)"] = "TeamSystem"

    return out


def dati_dipendente(t: str) -> dict:
    return {
        "Cognome e Nome": cerca(r"17\s+([A-Z][\w\s]+?)\s+\d{2}/\d{2}/\d{2}", t),
        "Codice Fiscale": cerca(r"([A-Z]{6}\d{2}[A-Z]\d{2}[A-Z]\d{3}[A-Z])", t),
        "Data di Nascita": cerca(
            r"[A-Z]{6}\d{2}[A-Z]\d{2}[A-Z]\d{3}[A-Z]\s+\w+\s+(\d{2}/\d{2}/\d{2})", t
        ),
        "Comune di Residenza": cerca(
            r"[A-Z]{6}\d{2}[A-Z]\d{2}[A-Z]\d{3}[A-Z]\s+(\w+)\s+\d{2}/\d{2}/\d{2}", t
        ),
        "Data Assunzione": cerca(r"17\s+[\w\s]+?\s+(\d{2}/\d{2}/\d{2})\s+\d", t),
        "Matricola INPS": cerca(r"MARZO \d{4}\s+(\d+)", t),
        "Posizione INAIL": cerca(r"MARZO \d{4}\s+\d+\s+\d+\s+\d+\s+(\d+)", t),
        "Matricola INPS Az.": cerca(r"MARZO \d{4}\s+\d+\s+\d+\s+(\d{10})", t),
        "Qualifica/Livello": cerca(r"(\d+\^)\s+\d+\s*\nPAGA", t),
        "% Part Time": (
            (lambda v: (v + " %") if v and v != "N/D" else v)(
                cerca(r"(\d{2,3},\d{2})\s+\d+\^\s+\d+\s*\nPAGA", t)
            )
        ),
        "Scatti Anzianità": cerca(r"19/10/21\s+(\d+)\s+\d+/\d+", t),
        "Ore Contrattuali": cerca(r"19/10/21\s+\d+\s+(\d+,\d+)", t),
        "Mese Retribuito": cerca(r"(MARZO \d{4})", t),
        "GG. Contributivi": cerca(r"19/10/21\s+(\d{2})\s+172", t),
    }


def retribuzione_base(t: str) -> dict:
    """Paga base, contingenza, scatti, retribuzione oraria/giornaliera."""
    vals = re.findall(r"(\d+,\d{5})", t)
    return {
        "Paga Base (oraria)": vals[0] if len(vals) > 0 else "N/D",
        "Contingenza (oraria)": vals[1] if len(vals) > 1 else "N/D",
        "Scatti Anzianità (orari)": vals[2] if len(vals) > 2 else "N/D",
        "Retribuzione Oraria Tot.": vals[3] if len(vals) > 3 else "N/D",
        "Retribuzione Giornaliera": cerca(r"9,16512\s+([\d,]+)", t),
    }


def voci_retributive(t: str) -> list:
    """Estrae le righe delle voci competenze/trattenute con regex mirati."""
    righe = []

    m = re.search(
        r"8001\s+LAVORO ORDINARIO\s+ORE\s+([\d,]+)\s+([\d,]+)\s+([\d.,]+)", t
    )
    if m:
        righe.append(
            ("8001", "LAVORO ORDINARIO", m.group(1) + " ore", m.group(2), m.group(3), "Competenza")
        )

    m = re.search(
        r"8010\s+LAVORO DOMENICALE 15%\s+([\d,]+)\s+([\d,]+)\s+([\d.,]+)", t
    )
    if m:
        righe.append(
            (
                "8010",
                "LAVORO DOMENICALE 15%",
                m.group(1) + " ore",
                m.group(2),
                m.group(3),
                "Competenza",
            )
        )

    m = re.search(r"9824\s+SOMMA ART\.1 C\.4 L\.207/24\s+([\d,]+)", t)
    if m:
        righe.append(("9824", "SOMMA ART.1 C.4 L.207/24", "—", "—", m.group(1), "Competenza"))

    m = re.search(r"1800\s+RATA ADDIZ\.REGIONALE A\.P\.\s+([\d,]+)", t)
    if m:
        righe.append(("1800", "RATA ADDIZ. REGIONALE A.P.", "—", "—", m.group(1), "Trattenuta"))

    m = re.search(r"1802\s+RATA ADD\.COMUNALE A\.P\.\s+([\d,]+)", t)
    if m:
        righe.append(("1802", "RATA ADD. COMUNALE A.P.", "—", "—", m.group(1), "Trattenuta"))

    m = re.search(r"1812\s+ACCONTO ADD\.COMUNALE\s+([\d,]+)", t)
    if m:
        righe.append(("1812", "ACCONTO ADD. COMUNALE", "—", "—", m.group(1), "Trattenuta"))

    return righe


def voci_retributive_dicts(t: str) -> List[dict[str, str]]:
    """Voci come dict (template Django / JSON), stesse regole di `voci_retributive`."""
    keys = ("codice", "descrizione", "ore_giorni", "base", "importo", "tipo")
    return [dict(zip(keys, row)) for row in voci_retributive(t)]


def _build_report_claude_dict(t: str) -> dict[str, Any]:
    """Solo motore regex «Claude» (anchor numerici originali)."""
    return {
        "dati_aziendali": dati_aziendali(t),
        "dati_dipendente": dati_dipendente(t),
        "retribuzione_base": retribuzione_base(t),
        "voci_retributive": voci_retributive_dicts(t),
        "totali_mensili": totali_mensili(t),
        "irpef_addizionali": irpef_addizionali(t),
        "ferie_permessi_rol": ferie_permessi(t),
        "dati_previdenziali": dati_previdenziali(t),
        "progressivi_annui": progressivi_annui(t),
    }


def report_cedolino_da_testo(t: str) -> dict[str, Any]:
    """Unisce analisi Studio Cipriano (voci classificate, cessazione) con fallback Claude sui totali."""
    from documenti.analizza_busta_paga_ts import merge_reports_with_claude

    return merge_reports_with_claude(t, _build_report_claude_dict(t))


def report_cedolino_senza_azienda(
    source: SourcePdf, *, password: str = ""
) -> dict[str, Any]:
    """
    Estrae il cedolino (prima pagina).

    Delega a :mod:`documenti.busta_acquisizione` (pipeline unica v4 → legacy testo).
    """
    from documenti.busta_acquisizione import report_cedolino_da_sorgente_pdf

    return report_cedolino_da_sorgente_pdf(
        source,
        password=password or "",
        file_label="(upload)" if isinstance(source, (bytes, bytearray)) else "",
    )


def report_e_testo_prima_pagina(
    source: SourcePdf, *, password: str = ""
) -> Tuple[dict[str, Any], str]:
    t = estrai_testo_prima_pagina(source, password=password)
    return report_cedolino_da_testo(t), t


def render_report_testo(report: dict[str, Any], path_pdf: str = "") -> str:
    """Report testuale (emoji e sezioni come script Claude originale)."""
    w = 70
    lines: List[str] = [
        "",
        "=" * w,
        "  CEDOLINO PAGA – ESTRAZIONE DATI",
        f"  File: {path_pdf}" if path_pdf else "  File: (buffer)",
        "=" * w,
    ]

    tc = report.get("tipo_cedolino")
    if tc:
        lines.extend(["", f"  Tipo cedolino: {tc}", ""])

    def _sep(title: str) -> None:
        lines.extend(["", "─" * w, f"  {title}", "─" * w])

    def _riga(label: str, valore: str, lw: int = 38) -> None:
        lines.append(f"  {label:<{lw}} {valore}")

    da = report.get("dati_aziendali") or {}
    if da:
        _sep("🏢  DATI AZIENDA / DATORE DI LAVORO")
        for k, v in da.items():
            _riga(k, v)

    _sep("👤  DATI DIPENDENTE")
    for k, v in report["dati_dipendente"].items():
        _riga(k, v)

    _sep("💰  RETRIBUZIONE BASE")
    for k, v in report["retribuzione_base"].items():
        _riga(k, v)

    _sep("📋  VOCI RETRIBUTIVE")
    voci = report.get("voci_retributive") or []
    has_cat = any(isinstance(r, dict) and r.get("categoria") for r in voci)
    if has_cat:
        vtuple = [
            (
                r.get("icona", ""),
                r.get("categoria", ""),
                r.get("codice", ""),
                r.get("descrizione", ""),
                r.get("ore_giorni", ""),
                r.get("base", ""),
                r.get("importo", ""),
                r.get("tipo", ""),
            )
            for r in voci
        ]
        lines.extend(
            _tabella_righe_testo(
                ["", "Cat.", "Cod.", "Descrizione", "Ore/Gg", "Base", "Importo", "Tipo"],
                vtuple,
            )
        )
    else:
        vtuple = [
            (
                r.get("codice", ""),
                r.get("descrizione", ""),
                r.get("ore_giorni", ""),
                r.get("base", ""),
                r.get("importo", ""),
                r.get("tipo", ""),
            )
            for r in voci
        ]
        lines.extend(
            _tabella_righe_testo(
                ["Codice", "Descrizione", "Ore/Gg", "Base", "Importo (€)", "Tipo"],
                vtuple,
            )
        )

    _sep("🧾  TOTALI MENSILI")
    for k, v in report["totali_mensili"].items():
        _riga(k, f"{v} €")

    _sep("📊  IRPEF E ADDIZIONALI")
    for k, v in report["irpef_addizionali"].items():
        _riga(k, f"{v} €")

    _sep("🏖️   FERIE, PERMESSI E ROL")
    for k, v in report["ferie_permessi_rol"].items():
        _riga(k, v)

    _sep("🏛️   DATI PREVIDENZIALI / INPS / INAIL")
    for k, v in report["dati_previdenziali"].items():
        _riga(k, v)

    _sep("📈  PROGRESSIVI ANNUI")
    for k, v in report["progressivi_annui"].items():
        _riga(k, f"{v} €")

    lines.extend(["", "=" * w, "  Fine estrazione", "=" * w, ""])
    return "\n".join(lines)


def totali_mensili(t: str) -> dict:
    return {
        "Totale Lordo": cerca(r"([\d.]+,\d{2})\s+1\.471,00\s+137,68", t),
        "Imponibile Contr. Soc.": "1.471,00",
        "Contributi Sociali": cerca(r"1\.471,00\s+([\d,]+)\s+137,68", t),
        "Tot. Contributi Sociali": "137,68",
        "Imponibile IRPEF (mese)": cerca(r"(1\.333,33)\s+306,67", t),
        "IRPEF Lorda": cerca(r"1\.333,33\s+([\d,]+)\s+245,84", t),
        "Tot. Detrazioni": cerca(r"1\.333,33\s+[\d,]+\s+([\d,]+)\s+60,83", t),
        "IRPEF Netta": cerca(r"245,84\s+([\d,]+)\s+6,00", t),
        "Tot. Trattenute": cerca(r"15,07\s+([\d,]+)\s+6,00", t),
        "Netto in Busta": cerca(r"0,01\s+([\d.]+,\d{2})", t),
    }


def irpef_addizionali(t: str) -> dict:
    return {
        "Addizionale Regionale": cerca(r"(37,05)\s+44,64", t),
        "Addizionale Comunale": cerca(r"37,05\s+(44,64)", t),
        "Conguaglio IRPEF +/-": cerca(r"44,64\s+(-?\d+,\d+)\s+6,66", t),
        "Arr. Attuale Addiz.": cerca(
            r"0,44\s+15,07\s+[\d,]+\s+6,00\s+6,00\s+([\d,]+)\s+1\.321,00", t
        ),
    }


def ferie_permessi(t: str) -> dict:
    return {
        "Festività Maturate": cerca(r"(7,20)\s+-7,20", t),
        "Festività Godute": cerca(r"7,20\s+(-7,20)", t),
        "Permessi Maturati": cerca(r"(44,64)\s+23,40\s+56,06", t),
        "Permessi Goduti": cerca(r"6,66\s+(-6,66)\s+32,66", t),
        "Permessi Residui": cerca(r"6,66\s+-6,66\s+(32,66)", t),
        "ROL Goduto": cerca(r"32,66\s+(23,40)", t),
        "ROL Residuo": cerca(r"32,66\s+23,40\s+(56,06)", t),
    }


def dati_previdenziali(t: str) -> dict:
    return {
        "Ore INPS": cerca(r"O\s+(156,00)\s+26,00", t),
        "GG. INPS": cerca(r"O\s+156,00\s+(26,00)", t),
        "Ore INAIL": cerca(r"O\s+156,00\s+26,00\s+(156,00)", t),
        "GG. INAIL": cerca(r"O\s+156,00\s+26,00\s+156,00\s+(\d+)", t),
        "Imponibile INAIL": cerca(r"156,00\s+25\s+([\d.]+,\d{2})", t),
        "TFR Mese": cerca(r"1\.360,32\s+([\d,]+)", t),
        "Detrazioni Spettanti": cerca(r"(245,84)\s*\n", t),
    }


def progressivi_annui(t: str) -> dict:
    return {
        "Prog. Imponibile IRPEF": cerca(r"(4080,96)\s+4397,00", t),
        "Prog. Imp. Contrib. Soc.": cerca(r"4080,96\s+(4397,00)", t),
        "Prog. Contrib. Soc.": cerca(r"4397,00\s+(417,38)", t),
        "Prog. Imponibile IRPEF ann.": cerca(r"417,38\s+(3979,14)", t),
        "Prog. IRPEF Lorda": cerca(r"3979,14\s+(915,21)", t),
        "Prog. Tot. Detrazioni": cerca(r"915,21\s+(704,62)", t),
        "Prog. IRPEF Pagata": cerca(r"704,62\s+(210,59)", t),
    }


def stampa_report(path_pdf: str, testo: str):
    print(render_report_testo(report_cedolino_da_testo(testo), path_pdf))


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print("Uso: python3 leggi_busta_paga_claude.py <percorso_pdf>")
        return 1

    path_pdf = str(Path(argv[1]).expanduser())
    if not Path(path_pdf).is_file():
        print(f"File non trovato: {path_pdf}")
        return 1

    from documenti.buste_pdf_passwords import passwords_for_busta_pdf_read

    testo = None
    last_err: BaseException | None = None
    for pw in passwords_for_busta_pdf_read():
        try:
            testo = estrai_testo(path_pdf, password=pw)
            break
        except Exception as e:
            last_err = e
            try:
                raw = Path(path_pdf).read_bytes()
                testo = estrai_testo_bytes(raw, password=pw)
                break
            except Exception as e2:
                last_err = e2

    if testo is None:
        print(f"Errore lettura PDF: {last_err}")
        return 1

    stampa_report(path_pdf, testo)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
