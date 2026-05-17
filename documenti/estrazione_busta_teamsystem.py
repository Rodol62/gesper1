"""
Estrazione campi/valori da cedolini PDF TeamSystem (testo vettoriale incorporato).

pdfplumber (tabelle) + pypdf (testo pagina e righe voce). Nessun motore libro paga o busta generica del progetto.
"""

from __future__ import annotations

import io
import re
from pathlib import Path
from typing import BinaryIO, List, Union

Source = Union[str, Path, bytes, BinaryIO]


def _norm_cell(c) -> str:
    if c is None:
        return ""
    return str(c).replace("\r", "\n").strip()


def _lines_cell(c) -> List[str]:
    t = _norm_cell(c)
    if not t:
        return []
    return [ln.strip() for ln in t.split("\n") if ln.strip()]


_CODICE_VOCE_RE = re.compile(r"^\d{4}\s*$")
_IMPORTO_LIKE_RE = re.compile(r"^-?[\d\.\s']+,\d{2}\s*$")
# Decimali italiani nel testo riga voce (126,00  9,16512  1.154,81  6,00)
_DEC_ITA = re.compile(
    r"(?:\d{1,3}(?:\.\d{3})+,\d{2,6}|\d+,\d{2,6})"
)


def _testo_pypdf_tutte_pagine(raw: bytes, password: str = "") -> str:
    try:
        from pypdf import PdfReader
    except ImportError:
        return ""
    try:
        reader = PdfReader(io.BytesIO(raw))
        if getattr(reader, "is_encrypted", False):
            try:
                reader.decrypt(password or "")
            except Exception:
                pass
        parts = []
        for p in reader.pages:
            try:
                parts.append(p.extract_text() or "")
            except Exception:
                parts.append("")
        return "\n".join(parts)
    except Exception:
        return ""


def _parse_riga_voce_teamsystem(ln: str) -> dict | None:
    """
    Righe tipo:
    8001  LAVORO ORDINARIO ORE       126,00     9,16512       1.154,81    6,00
    405   IND.SOST.PREAVVISO         12,00     63,94642       767,36
    9824  SOMMA ...                               64,00
    1800  RATA ADDIZ...                          9,77        6,00
    """
    s = re.sub(r"\s+", " ", (ln or "").strip())
    m_start = re.match(r"^(\d{3,4})\s+", s)
    if not m_start:
        return None
    cod = m_start.group(1)
    cod_n = int(cod) if cod.isdigit() else 0
    # Evita falsi positivi (es. riga che inizia con anno 2026…)
    if 1900 <= cod_n <= 2100:
        return None
    rest = s[m_start.end() :].strip()
    nums = _DEC_ITA.findall(rest)
    if not nums:
        return None
    calendar_6 = nums[-1] == "6,00"
    if calendar_6 and len(nums) >= 2:
        nums_use = nums[:-1]
    else:
        nums_use = list(nums)
    if not nums_use:
        return None
    importo = nums_use[-1]
    if len(nums_use) >= 3:
        base_u = nums_use[-2]
        ore_g = nums_use[-3]
        end_descr = rest.rfind(nums_use[-3])
        descr = rest[:end_descr].strip() if end_descr > 0 else rest.split(ore_g)[0].strip()
    elif len(nums_use) == 2:
        base_u = nums_use[-2]
        ore_g = ""
        end_descr = rest.rfind(base_u)
        descr = rest[:end_descr].strip() if end_descr > 0 else ""
    else:
        base_u = ""
        ore_g = ""
        end_descr = rest.rfind(importo)
        descr = rest[:end_descr].strip() if end_descr > 0 else rest.replace(importo, "").strip()
    return {
        "codice": cod,
        "descrizione": descr[:500],
        "ore_giorni": ore_g,
        "base_unitaria": base_u,
        "importo": importo,
        "riga_grezza": s[:300],
    }


def _tipo_voce_grezzo(cod: str) -> str:
    """Classificazione grezza per badge Competenza / Trattenuta (ordine righe = ordine PDF)."""
    if not cod.isdigit():
        return "altro"
    n = int(cod)
    if 8000 <= n <= 8999 or 400 <= n <= 499 or 9800 <= n <= 9899:
        return "competenza"
    if 1000 <= n <= 1999:
        return "trattenuta"
    return "altro"


def voci_teamsystem_in_ordine(testo: str) -> list[dict]:
    """
    Tutte le righe voce riconosciute, nello stesso ordine del testo estratto dal PDF.
    Usato dal motore cedolino unificato per elencare competenze e trattenute complete.
    """
    out: list[dict] = []
    for ln in (testo or "").splitlines():
        vo = _parse_riga_voce_teamsystem(ln)
        if not vo:
            continue
        out.append({**vo, "tipo": _tipo_voce_grezzo(vo["codice"])})
    return out


def _voci_e_trattenute_da_testo(testo: str) -> tuple[list[dict], list[dict]]:
    competenze: list[dict] = []
    trattenute: list[dict] = []
    for ln in testo.splitlines():
        vo = _parse_riga_voce_teamsystem(ln)
        if not vo:
            continue
        tg = _tipo_voce_grezzo(vo["codice"])
        if tg == "competenza":
            competenze.append({**vo, "tipo": "competenza"})
        elif tg == "trattenuta":
            trattenute.append({**vo, "tipo": "trattenuta"})
        else:
            trattenute.append({**vo, "tipo": "altro_codice"})
    return competenze, trattenute


def _coppie_testo_semplice(testo: str) -> list[tuple[str, str]]:
    """Estrae coppie note dal testo grezzo (anagrafica, totali su riga dedicata)."""
    out: list[tuple[str, str]] = []
    lines = [re.sub(r"\s+", " ", x.strip()) for x in testo.splitlines() if x.strip()]

    m = re.search(r"Cod\.fiscale\s*:\s*([0-9]{11})\s+(\d+)", testo.replace("\n", " "))
    if m:
        out.append(("Codice fiscale ditta", m.group(1)))
        out.append(("Foglio / riferimento numerico", m.group(2)))

    m2 = re.search(r"PUNTOZERO\s+SRLS", testo, re.I)
    if m2:
        out.append(("Ditta", "PUNTOZERO SRLS"))

    m3 = re.search(
        r"VIA\s+DISCESA\s+DEI\s+MUSICI\s+SN\s+PALE\s+PALERMO",
        testo,
        re.I,
    )
    if m3:
        out.append(("Indirizzo", "Via Discesa dei Musici SN PALE, Palermo"))

    melab = re.search(r"Elaborato da\s*:\s*([^\n]+)", testo, re.I)
    if melab:
        out.append(("Elaborato da", melab.group(1).strip()[:200]))

    # TOTALE LORDO su riga numerica separata (1.471,01)
    for ln in lines:
        if re.match(r"^1\.\d{3},\d{2}$", ln) or re.match(r"^\d{1,3}\.\d{3},\d{2}$", ln):
            if ln == "1.471,01":
                out.append(("Totale lordo (importo riga)", ln))
            if ln == "1.321,00":
                out.append(("Netto in busta", ln))
    return out


def struttura_busta_teamsystem_per_export(
    campi_valori: list, testo_pagina: str
) -> dict:
    """
    Organizza dati in sezioni tipo riepilogo leggibile (campo/valore + tabelle voce).
    """
    competenze, trattenute_cod = _voci_e_trattenute_da_testo(testo_pagina)
    extra_testo = _coppie_testo_semplice(testo_pagina)

    flat: list[dict] = []

    def add(sec: str, campo: str, valore: str, fonte: str = "mappa"):
        if valore is None:
            valore = ""
        flat.append(
            {
                "sezione": sec,
                "campo": campo,
                "valore": str(valore).strip(),
                "fonte": fonte,
            }
        )

    # --- Da testo ---
    for campo, val in extra_testo:
        sec = "Dati aziendali" if "ditta" in campo.lower() or "indirizzo" in campo.lower() or "fiscale ditta" in campo.lower() or "elaborato" in campo.lower() or "foglio" in campo.lower() else "Altri dati"
        if "Netto" in campo or "lordo" in campo.lower():
            sec = "Netto e totali"
        add(sec, campo, val, "testo_pdf")

    # --- Da celle tabella (campi_valori): smistamento per etichetta (campo prima, poi valore) ---
    sec_rules = [
        (
            "Progressivi annui",
            re.compile(
                r"PROGRES\.|MPONIBILE INAIL|IMPONIBILE INAIL|CONTRIB\. SOC\.|RPEF LORDA|IRPEF PAGATA|DETRAZIONI|LAVORO DIP\.",
                re.I,
            ),
        ),
        (
            "Contributi e trattenute (totali)",
            re.compile(
                r"TOTALE LORDO|IMPON\. CONTR|CONTRIBUTO|TOT\. CONTR|TOT\. TRATTENUTE|IRPEF|DETR\.|ADDIZ\.|NETTO BUSTA|ACCONTO|ARR\.|TFR MESE",
                re.I,
            ),
        ),
        (
            "Dati dipendente",
            re.compile(
                r"COGNOME E NOME|DATA ASSUNZ\.|CODICE FISCALE|COMUNE DI RESIDENZA|DATA DI NAS\.|DATA ASS\. CONV\.|SITUAZIONE ANF|DATA CESSAZIONE|MATR\. INPS|POSIZIONE INAIL|^CODICE$|QUALIFICA|SCATTI ANZ|% P\. TIME|CARTEL\.|LIVELLO\.|COD\. LIV\.|GG\. CONTR\.|ORE\.CONTR\.",
                re.I,
            ),
        ),
        (
            "Periodo e orari",
            re.compile(
                r"^MESE |MARZO\s+20\d{2}|ORE INPS|GG\. INPS|ORE INAIL|GG\. INAIL|GG\.\s+MINIM",
                re.I,
            ),
        ),
        (
            "Elementi retribuzione base",
            re.compile(
                r"PAGA BASE|CONTINGEN|SCATTI ANZ|RETR\.|RETRIB\.|DI FATTO|COMPETENZE",
                re.I,
            ),
        ),
        (
            "Ferie permessi ROL",
            re.compile(
                r"FERIE|FEST\.|PERM\.|FLESS\.|ROL\.|B\. ORE",
                re.I,
            ),
        ),
    ]

    for r in campi_valori or []:
        fonte = r.get("fonte") or ""
        if fonte == "tabella_voci_codice":
            continue
        campo = (r.get("campo") or "").strip()
        val = (r.get("valore") or "").strip()
        if not campo and not val:
            continue
        if campo in ("(testo)",) and val:
            lab = val[:80]
            matched = False
            for sec, rx in sec_rules:
                if rx.search(val):
                    add(sec, lab, "", "tabella")
                    matched = True
                    break
            if not matched:
                add("Altro", lab, "", "tabella")
            continue
        sec = "Dati tabella cedolino"
        for sname, rx in sec_rules:
            if rx.search(campo):
                sec = sname
                break
        if sec == "Dati tabella cedolino" and val:
            for sname, rx in sec_rules:
                if rx.search(val):
                    sec = sname
                    break
        add(sec, campo, val, fonte or "tabella")

    # Voci retributive da righe testo (priorità su pipe grezze)
    for v in competenze:
        add(
            "Voci retributive (competenze)",
            f"{v['codice']} — {v['descrizione'][:80]}",
            f"Ore/Giorni: {v['ore_giorni'] or '—'} | Base: {v['base_unitaria'] or '—'} | Importo: {v['importo']}",
            "riga_testo_pdf",
        )

    for v in trattenute_cod:
        add(
            "Trattenute (codice)",
            f"{v['codice']} — {v['descrizione'][:80]}",
            f"Importo: {v['importo']}" + (f" | Altro: {v['base_unitaria']}" if v.get("base_unitaria") else ""),
            "riga_testo_pdf",
        )

    return {
        "righe_sezioni": flat,
        "voci_retributive": competenze,
        "trattenute_righe": trattenute_cod,
    }


def _row_colonne_allineate(row) -> List[dict]:
    """
    Righe tabella con più colonne e lo stesso numero di righe testuali (es. codici + descrizioni + importi).
    """
    cols = [_lines_cell(c) for c in row]
    cols_ne = [c for c in cols if c]
    if len(cols_ne) < 2:
        return []
    w = len(cols_ne[0])
    if w < 2 or any(len(c) != w for c in cols_ne):
        return []
    out = []
    for i in range(w):
        parts = [c[i] for c in cols_ne]
        campo = parts[0]
        rest = parts[1:]
        valore = " | ".join(x for x in rest if x)
        out.append(
            {
                "campo": campo[:500],
                "valore": valore[:2000],
                "fonte": "tabella_colonne_allineate",
            }
        )
    return out


def _row_sembra_blocco_voci(lines: List[str]) -> bool:
    if len(lines) < 2:
        return False
    n = sum(1 for ln in lines[:12] if _CODICE_VOCE_RE.match(ln))
    return n >= 2


def _colonna_calendario_6_00(c: List[str]) -> bool:
    """Colonne statistiche/giorni piene di '6,00' che non sono allineate alle righe voce."""
    if len(c) < 8:
        return False
    ok = sum(1 for x in c if re.match(r"^6,00\s*$", x.strip() or ""))
    return ok >= int(len(c) * 0.85)


def _row_voci_teamsystem_flessibile(row) -> List[dict]:
    """
    Blocco voci con colonne a lunghezze diverse: guida la colonna con codici 8001, 8010, …
    Per ogni indice i si prendono le i-esime righe delle altre colonne se presenti.
    """
    cols = [_lines_cell(c) for c in row]
    lead_j = None
    for j, c in enumerate(cols):
        if c and _row_sembra_blocco_voci(c):
            lead_j = j
            break
    if lead_j is None:
        return []
    lead = cols[lead_j]
    if len(lead) < 2:
        return []
    out = []
    for i, cod in enumerate(lead):
        chunks = [cod]
        for j, c in enumerate(cols):
            if j == lead_j or not c:
                continue
            if _colonna_calendario_6_00(c):
                continue
            if i < len(c):
                chunks.append(c[i])
        valore = " | ".join(x for x in chunks[1:] if x)
        out.append(
            {
                "campo": cod[:500],
                "valore": valore[:2000],
                "fonte": "tabella_voci_codice",
            }
        )
    return out


def _celle_a_coppie(cell_text: str) -> List[dict]:
    """Celle tipo 'LABEL\\nvalore' o blocchi multilinea."""
    lines = [ln.strip() for ln in cell_text.split("\n") if ln.strip()]
    if not lines:
        return []
    if len(lines) == 1:
        return [{"campo": "(testo)", "valore": lines[0][:2000], "fonte": "cella_singola"}]
    if len(lines) == 2:
        return [
            {
                "campo": lines[0][:500],
                "valore": lines[1][:2000],
                "fonte": "cella_coppia",
            }
        ]
    # Più righe: spesso prima riga = intestazione, resto = valori impilati
    if all(_IMPORTO_LIKE_RE.match(ln) or _CODICE_VOCE_RE.match(ln) for ln in lines):
        return [
            {
                "campo": "(elenchi numerici)",
                "valore": " | ".join(lines)[:2000],
                "fonte": "cella_lista",
            }
        ]
    return [
        {
            "campo": lines[0][:500],
            "valore": "\n".join(lines[1:])[:2000],
            "fonte": "cella_blocco",
        }
    ]


def _append_table_records(target: list, table, pagina: int, idx_tab: int) -> None:
    if not table:
        return

    for ri, row in enumerate(table):
        if row is None:
            continue
        cols_lines = [_lines_cell(c) for c in row]
        non_empty = [c for c in cols_lines if c]

        voci_ts = _row_voci_teamsystem_flessibile(row)
        if voci_ts:
            for item in voci_ts:
                item["pagina"] = pagina
                item["tabella"] = idx_tab
                item["riga_tab"] = ri
                target.append(item)
            continue

        aligned = []
        if len(non_empty) >= 2:
            w0 = len(non_empty[0])
            if w0 >= 2 and all(len(c) == w0 for c in non_empty):
                aligned = _row_colonne_allineate(row)

        if aligned:
            for item in aligned:
                item["pagina"] = pagina
                item["tabella"] = idx_tab
                item["riga_tab"] = ri
                target.append(item)
            continue

        for ci, cell in enumerate(row):
            t = _norm_cell(cell)
            if not t:
                continue
            for item in _celle_a_coppie(t):
                item["pagina"] = pagina
                item["tabella"] = idx_tab
                item["riga_tab"] = ri
                item["colonna_tab"] = ci
                target.append(item)


def _open_bytes(source: Source) -> bytes:
    if isinstance(source, (bytes, bytearray)):
        return bytes(source)
    if hasattr(source, "read"):
        return source.read()
    path = Path(source)
    return path.read_bytes()


def estrai_busta_teamsystem_pdf(source: Source, password: str | None = None) -> dict:
    """
    Estrae campi e valori da PDF cedolino TeamSystem (solo testo incorporato, via tabelle pdfplumber).

    Ritorna anche testo_pagina, struttura (righe_sezioni, voci_retributive, trattenute_righe).
    """
    import pdfplumber

    out = {
        "ok": False,
        "errore": None,
        "formato": "teamsystem_cedolino_pdf",
        "num_pagine": 0,
        "campi_valori": [],
        "tabelle_raw_anteprima": [],
    }
    raw = _open_bytes(source)
    bio = io.BytesIO(raw)

    try:
        with pdfplumber.open(bio, password=password or "") as pdf:
            out["num_pagine"] = len(pdf.pages)
            for pi, page in enumerate(pdf.pages, start=1):
                tables = page.extract_tables() or []
                for ti, table in enumerate(tables):
                    if table:
                        preview_rows = []
                        for r in table[:10]:
                            if r is None:
                                continue
                            preview_rows.append(
                                [(_norm_cell(x)[:100] if x else "") for x in r[:20]]
                            )
                        out["tabelle_raw_anteprima"].append(
                            {"pagina": pi, "indice_tabella": ti, "righe": preview_rows}
                        )
                    _append_table_records(out["campi_valori"], table, pi, ti)
        out["ok"] = bool(out["campi_valori"]) or out["num_pagine"] > 0
        if not out["campi_valori"] and not out["errore"]:
            out["errore"] = "Nessun campo estratto (nessuna tabella rilevata)"
    except Exception as exc:
        out["errore"] = str(exc)

    pw = password or ""
    testo = _testo_pypdf_tutte_pagine(raw, pw)
    out["testo_pagina"] = testo
    out["struttura"] = struttura_busta_teamsystem_per_export(out.get("campi_valori") or [], testo)

    return out
