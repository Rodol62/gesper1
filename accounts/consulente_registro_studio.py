"""
Import proforma / parcelle studio per area consulente: estrazione testo da PDF,
parsing campi (PROFORMA, numero, data, totale da pagare), righe dare/avere e saldo progressivo.

Estrazione: prima ``pdfplumber``; se il testo è quasi vuoto, tentativo OCR via
``pdftoppm`` + ``tesseract`` (stesso schema di ``scripts/analizza_pdf_paghe.py``).
"""
from __future__ import annotations

import io
import re
import subprocess
import tempfile
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path

# ── Estrazione testo ─────────────────────────────────────────────────────────


def _pdf_text_pdfplumber(pdf_path: Path) -> str:
    import pdfplumber

    chunks: list[str] = []
    with pdfplumber.open(str(pdf_path)) as pdf:
        for page in pdf.pages:
            try:
                t = page.extract_text() or ""
            except Exception:
                t = ""
            chunks.append(t)
    return "\n".join(chunks)


def _pdf_text_tesseract(pdf_path: Path) -> str:
    """OCR con binari di sistema (pdftoppm + tesseract, lingua italiana)."""
    with tempfile.TemporaryDirectory(prefix="gesper_regstudio_ocr_") as td:
        tmpdir = Path(td)
        ppm_cmd = ["pdftoppm", "-png", "-r", "200", str(pdf_path), str(tmpdir / "page")]
        subprocess.run(ppm_cmd, check=True, capture_output=True, text=True)
        page_imgs = sorted(tmpdir.glob("page-*.png"))
        pages_txt: list[str] = []
        for img in page_imgs:
            out_base = img.with_suffix("")
            tess_cmd = [
                "tesseract",
                str(img),
                str(out_base),
                "-l",
                "ita",
                "--psm",
                "6",
            ]
            subprocess.run(tess_cmd, check=True, capture_output=True, text=True)
            txt_path = Path(f"{out_base}.txt")
            if txt_path.exists():
                pages_txt.append(txt_path.read_text(encoding="utf-8", errors="ignore"))
            else:
                pages_txt.append("")
        return "\n".join(pages_txt)


def estrai_testo_da_pdf(pdf_path: Path) -> tuple[str, str]:
    """
    Restituisce (testo, metodo) con metodo in {'pdfplumber', 'tesseract', 'vuoto'}.
    """
    try:
        txt = _pdf_text_pdfplumber(pdf_path)
    except Exception:
        txt = ""
    compact = re.sub(r"\s+", "", txt)
    if len(compact) >= 40:
        return txt, "pdfplumber"
    try:
        txt2 = _pdf_text_tesseract(pdf_path)
        if re.sub(r"\s+", "", txt2).strip():
            return txt2, "tesseract"
    except (FileNotFoundError, subprocess.CalledProcessError, OSError):
        pass
    return txt or "", "vuoto" if not txt.strip() else "pdfplumber"


# ── Parsing importi / date ───────────────────────────────────────────────────


def _normalizza_testo_pdf(testo: str) -> str:
    """Spazi Unicode e a capo rumorosi: migliora il match delle etichette importo."""
    if not testo:
        return ""
    t = testo.replace("\u00a0", " ").replace("\u202f", " ").replace("\u2009", " ")
    t = re.sub(r"[ \t]+\n", "\n", t)
    t = re.sub(r"\n{3,}", "\n\n", t)
    return t


def _normalizza_etichette_numero_ocr(testo: str) -> str:
    """
    Ripara etichette spezzate da PDF/OCR (es. «n.» + «umero» → «numero»),
    così non finisce in DB il frammento «umero» al posto del vero protocollo.
    """
    if not testo:
        return ""
    t = testo
    t = re.sub(r"(?i)n\.\s*\n\s*umero\b", "numero", t)
    t = re.sub(r"(?i)n\.\s+umero\b", "numero", t)
    t = re.sub(r"(?i)\bnum\s*\.\s*\n\s*ero\b", "numero", t)
    return t


def parse_importo_form(raw: str) -> Decimal | None:
    """Parse importo da campo form (formato italiano o semplice)."""
    return _parse_it_decimal(raw or "")


def _parse_it_decimal(raw: str) -> Decimal | None:
    if not raw:
        return None
    s = raw.strip()
    s = re.sub(r"[\s€EUR]", "", s, flags=re.I)
    if not s or s in "-—":
        return None
    neg = False
    if s.startswith("(") and s.endswith(")"):
        neg = True
        s = s[1:-1]
    if s.startswith("-"):
        neg = True
        s = s[1:]
    # Italiano: migliaia con . e decimali con ,
    if "," in s and "." in s:
        if s.rfind(",") > s.rfind("."):
            s = s.replace(".", "").replace(",", ".")
        else:
            s = s.replace(",", "")
    elif "," in s:
        parts = s.split(",")
        if len(parts) == 2 and len(parts[1]) <= 2 and parts[1].isdigit():
            s = parts[0].replace(".", "") + "." + parts[1]
        else:
            s = s.replace(",", ".")
    else:
        s = s.replace(",", "")
    try:
        d = Decimal(s)
        return -d if neg else d
    except InvalidOperation:
        return None


def _parse_date(s: str) -> date | None:
    s = s.strip()
    m = re.match(r"^(\d{1,2})[/.-](\d{1,2})[/.-](\d{2,4})$", s)
    if not m:
        return None
    d, mo, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
    if y < 100:
        y += 2000 if y < 70 else 1900
    try:
        return date(y, mo, d)
    except ValueError:
        return None


def _first_date_in_text(text: str) -> date | None:
    for m in re.finditer(r"\b(\d{1,2})[/.-](\d{1,2})[/.-](\d{2,4})\b", text):
        d = _parse_date(m.group(0))
        if d:
            return d
    return None


# ── Risultato parsing ─────────────────────────────────────────────────────────


@dataclass
class EsitoParsingProforma:
    tipo_documento: str  # proforma | parcella | sconosciuto
    numero_documento: str
    data_documento: date | None
    totale_da_pagare: Decimal | None
    avvisi: list[str] = field(default_factory=list)


# Frammenti errati da match parziale su «numero» / OCR (non sono numeri documento)
_NUMERO_DOCUMENTO_RIFIUTA = frozenset(
    {
        "umero",
        "mero",
        "ero",
        "ro",
        "num",
        "numo",
        "umo",
        "bero",
    }
)


def _is_plausible_numero_documento(s: str) -> bool:
    """Evita di confondere date, importi o frammenti di «numero» con il numero documento."""
    s = (s or "").strip()
    if len(s) < 2 or len(s) > 80:
        return False
    sl = s.lower()
    if sl in _NUMERO_DOCUMENTO_RIFIUTA:
        return False
    if re.match(r"^\d{1,2}[/.-]\d{1,2}[/.-]\d{2,4}$", s):
        return False
    compact = re.sub(r"[\s€]", "", s, flags=re.I)
    if re.match(r"^-?[\d.]+,\d{2}$", compact) or re.match(r"^-?[\d,]+\.\d{2}$", compact):
        return False
    return True


def _estrai_numero_documento_proforma_parcella(testo: str) -> str:
    """
    Numero proforma/parcella: etichette tipiche (anche su riga diversa dal titolo).
    Ordine dal più specifico al fallback legacy.
    """
    patterns = (
        # Protocollo / Prot. / Rif. (mai «PROT» generico: matcherebbe dentro …/PROT-…)
        r"(?i)(?:protocollo|prot\.)\s*[:#.]?\s*([A-Za-z0-9/_\-\.]{2,40})",
        r"(?i)\brif\.?\s*(?:doc\.?|documento|fatt\.?)?\s*[:#.]?\s*([A-Za-z0-9/_\-\.]{2,40})",
        # «Numero proforma …» / «N. proforma …» / «Numero documento» (NUMERO intero: evita num\. su «numero»)
        r"(?:NUMERO|N\.)[\s:]+(?:DEL\s+|DELLA\s+)?(?:PRO[-\s]?FORMA|PROFORMA|PARCHELLA|PARCELLA|DOCUMENTO)\s*[:.]?\s*"
        r"([A-Za-z0-9/_\-\.]{2,40})",
        r"(?:NUMERO|N\.)[\s:]+(?:DOCUMENTO|DOC\.)\s*[:.]?\s*([A-Za-z0-9/_\-\.]{2,40})",
        # «Proforma n. …» sulla stessa riga (NUM\. solo con punto fermo, non prefisso di «numero»)
        r"(?:PRO[-\s]?FORMA|PROFORMA|PARCHELLA|PARCELLA)\s*(?:N\.?\s*°?\s*|NR\.?\s*|NUM\.\s*|NUMERO\s+)[:.]?\s*"
        r"([A-Za-z0-9/_\-\.]{2,40})",
        # Legacy: dopo PROFORMA/PARCELLA — usa «numero» intero o n./nr., mai num\.? che matcha «numero»
        r"(?:PROFORMA|PARCHELLA|PARCELLA)[^\n]{0,120}?(?:n\.?\s*°?\s*|nr\.?\s*|numero\s*[:.]?\s*)"
        r"([A-Za-z0-9/_\-\.]{1,40})",
    )
    for pat in patterns:
        m = re.search(pat, testo, re.I | re.DOTALL)
        if m:
            cand = m.group(1).strip()
            if _is_plausible_numero_documento(cand):
                return cand[:80]
    # Titolo su una riga, numero sulla successiva (PDF a blocchi)
    lines = testo.splitlines()
    for i, line in enumerate(lines):
        if not re.search(r"\b(?:PRO[-\s]?FORMA|PROFORMA|PARCHELLA|PARCELLA)\b", line, re.I):
            continue
        for j in range(i + 1, min(i + 6, len(lines))):
            seg = lines[j]
            m = re.search(
                r"^\s*(?:NUMERO|NR\.?|N\.?\s*°?|NUM\.(?!\w)|RIF\.?)\s*[:#.]?\s*([A-Za-z0-9/_\-\.]{2,40})\s*$",
                seg,
                re.I,
            )
            if m:
                cand = m.group(1).strip()
                if _is_plausible_numero_documento(cand):
                    return cand[:80]
            m2 = re.search(
                r"^\s*(?:PRO[-\s]?FORMA|PROFORMA)\s*(?:N\.?\s*°?\s*|NR\.?\s*)[:.]?\s*([A-Za-z0-9/_\-\.]{2,40})\s*$",
                seg,
                re.I,
            )
            if m2:
                cand = m2.group(1).strip()
                if _is_plausible_numero_documento(cand):
                    return cand[:80]
    return ""


def classifica_tipo(testo: str, nome_file: str) -> str:
    u = testo.upper()
    n = nome_file.upper()
    if "PROFORMA" in u or "PRO-FORMA" in u:
        return "proforma"
    if "PARCHELLA" in u or "PARCELLA" in u or "PARCELL" in u:
        return "parcella"
    if "PROFORMA" in n:
        return "proforma"
    if "PARCHELLA" in n or "PARCELLA" in n:
        return "parcella"
    return "sconosciuto"


def _totale_fallback_proforma(testo: str) -> Decimal | None:
    """Pattern aggiuntivi per totali su proforma/parcelle (layout vari)."""
    if not (testo or "").strip():
        return None
    extra_patterns = (
        r"(?:NETTO\s+A\s+PAGARE|TOTALE\s+A\s+PAGARE|IMPORTO\s+A\s+PAGARE)\s*[:\s€]*([\d\.\s]+(?:,\d{1,2})?)",
        r"(?:TOTALE\s+COMPLESSIVO|TOTALE\s+FATTURA|TOTALE\s+DOCUMENTO)\s*[:\s€]*([\d\.\s]+(?:,\d{1,2})?)",
        r"(?:IMPORTO\s+TOTALE\s+DOCUMENTO|QUOTA\s+COMPLESSIVA)\s*[:\s€]*([\d\.\s]+(?:,\d{1,2})?)",
        r"(?:Saldo|Importo)\s+documento\s*[:\s€]*([\d\.\s]+(?:,\d{1,2})?)",
        # Etichette tipiche modelli commercialisti (parcella / proforma)
        r"(?:TOTALE|T\.)\s+(?:DELLA\s+|DEL\s+)?(?:PARCHELLA|PARCELLA)\s*(?:PROFESSIONALE\s*)?[:\s€]*([\d\.\s]+(?:,\d{1,2})?)",
        r"(?:TOTALE|T\.)\s+(?:DELLA\s+|DEL\s+)?(?:PRO[-\s]?FORMA|PROFORMA)\s*[:\s€]*([\d\.\s]+(?:,\d{1,2})?)",
        r"(?:IMPORTO\s+)?TOTALE\s+(?:IVA\s+)?(?:COMPRESA\s+)?(?:PARCHELLA|PARCELLA|PRO[-\s]?FORMA)\s*[:\s€]*([\d\.\s]+(?:,\d{1,2})?)",
    )
    for pat in extra_patterns:
        m = re.search(pat, testo, re.I)
        if m:
            d = _parse_it_decimal(m.group(1))
            if d is not None and d > 0:
                return d
    # Righe verso la fine del documento: spesso il totale generale è su una riga "TOTALE …"
    for line in reversed(testo.splitlines()[-150:]):
        if not re.search(r"\bTOTALE\b", line, re.I):
            continue
        m = re.search(r"([\d]{1,3}(?:\.\d{3})*,\d{2}|\d+,\d{2})\s*(?:EUR|€)?\s*$", line.strip(), re.I)
        if not m:
            m = re.search(r"([\d\.\s]+(?:,\d{1,2}))\s*(?:EUR|€)?\s*$", line.strip(), re.I)
        if m:
            d = _parse_it_decimal(m.group(1))
            if d is not None and d > 0:
                return d
    return None


def parse_testo_proforma_parcella(testo: str, nome_file: str) -> EsitoParsingProforma:
    testo = _normalizza_etichette_numero_ocr(_normalizza_testo_pdf(testo))
    tipo = classifica_tipo(testo, nome_file)
    avvisi: list[str] = []

    numero = _estrai_numero_documento_proforma_parcella(testo)
    if not numero:
        m_alt = re.search(
            r"(?:n\.?\s*°?\s*documento|documento\s+n\.?)\s*[:.]?\s*([A-Za-z0-9/_\-\.]{1,32})",
            testo,
            re.I,
        )
        if m_alt:
            cand = m_alt.group(1).strip()
            if _is_plausible_numero_documento(cand):
                numero = cand

    data_doc: date | None = None
    m_data = re.search(
        r"(?:DATA\s*(?:DOCUMENTO|FATTURA|PROFORMA)?|DATA\s*EMISSIONE)\s*[:.]?\s*(\d{1,2}[/.-]\d{1,2}[/.-]\d{2,4})",
        testo,
        re.I,
    )
    if m_data:
        data_doc = _parse_date(m_data.group(1))
    if data_doc is None:
        data_doc = _first_date_in_text(testo[:2500])

    totale: Decimal | None = None
    m_tot = re.search(
        r"TOTALE\s+DA\s+PAGAR[EI]\s*[:.]?\s*([\d\.\s,]+(?:,\d{1,2})?)",
        testo,
        re.I,
    )
    if m_tot:
        totale = _parse_it_decimal(m_tot.group(1))
    if totale is None:
        m_tot2 = re.search(
            r"(?:IMPORTO\s+TOTALE|TOTALE\s+DOCUMENTO)\s*[:.]?\s*([\d\.\s,]+(?:,\d{1,2})?)",
            testo,
            re.I,
        )
        if m_tot2:
            totale = _parse_it_decimal(m_tot2.group(1))

    # Etichette esplicite «totale parcella» / «totale proforma» (spesso su riga unica col valore)
    if totale is None:
        m_tp = re.search(
            r"(?:TOTALE|T\.)\s+(?:DELLA\s+|DEL\s+)?(?:PARCHELLA|PARCELLA)\s*(?:PROFESSIONALE\s*)?[:\s€]*\s*"
            r"([\d]{1,3}(?:\.\d{3})*,\d{2}|\d+,\d{2}|[\d\.\s]+,\d{2})",
            testo,
            re.I,
        )
        if m_tp:
            totale = _parse_it_decimal(m_tp.group(1))
    if totale is None:
        m_pf = re.search(
            r"(?:TOTALE|T\.)\s+(?:DELLA\s+|DEL\s+)?(?:PRO[-\s]?FORMA|PROFORMA)\s*[:\s€]*\s*"
            r"([\d]{1,3}(?:\.\d{3})*,\d{2}|\d+,\d{2}|[\d\.\s]+,\d{2})",
            testo,
            re.I,
        )
        if m_pf:
            totale = _parse_it_decimal(m_pf.group(1))

    if totale is None:
        tot_fb = _totale_fallback_proforma(testo)
        if tot_fb is not None:
            totale = tot_fb
            avvisi.append("Totale ricavato da pattern alternativo (verificare sul PDF).")
    if totale is None:
        avvisi.append("Totale da pagare non rilevato automaticamente.")
    if data_doc is None:
        avvisi.append("Data documento non rilevata; usare ordinamento per data import.")
    if not numero:
        avvisi.append("Numero documento non rilevato.")

    return EsitoParsingProforma(
        tipo_documento=tipo,
        numero_documento=numero[:80] if numero else "",
        data_documento=data_doc,
        totale_da_pagare=totale,
        avvisi=avvisi,
    )


def elenco_pdf_cartella(
    root: Path,
    *,
    escludi_prefisso_nome: str = "riepilogo",
) -> list[Path]:
    """PDF ricorsivi, esclusi file il cui nome (senza path) inizia con il prefisso (case-insensitive)."""
    pref = escludi_prefisso_nome.lower()
    out: list[Path] = []
    for p in sorted(root.rglob("*")):
        if not p.is_file():
            continue
        if p.suffix.lower() != ".pdf":
            continue
        if p.name.lower().startswith(pref):
            continue
        out.append(p)
    # Ordine cronologico preferito: data modifica file (proxy finché non si parsa)
    out.sort(key=lambda x: (x.stat().st_mtime, x.name.lower()))
    return out


def applica_upload_proforma_parcelle_pdf(azienda, user, uploads) -> list[str]:
    """Importa PDF proforma/parcella; restituisce messaggi per ``messages``."""
    import os
    import tempfile
    from pathlib import Path

    from django.core.files import File

    from .models import MovimentoRegistroStudioConsulente

    msgs: list[str] = []
    n_ok = n_skip = n_err = 0
    for up in uploads:
        nome = (up.name or "documento.pdf")[:280]
        if nome.lower().startswith("riepilogo"):
            msgs.append(f"Ignorato (prefisso riepilogo): {nome}")
            n_skip += 1
            continue
        if MovimentoRegistroStudioConsulente.objects.filter(
            azienda=azienda, nome_file=nome, tipo_riga="documento"
        ).exists():
            msgs.append(f"Già presente: {nome}")
            n_skip += 1
            continue
        tmp_path = None
        try:
            with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
                for chunk in up.chunks():
                    tmp.write(chunk)
                tmp_path = tmp.name
            path = Path(tmp_path)
            testo, metodo = estrai_testo_da_pdf(path)
            parsed = parse_testo_proforma_parcella(testo, nome)
            tot = parsed.totale_da_pagare or Decimal("0")
            dare = tot if tot > 0 else Decimal("0")
            note = "; ".join(parsed.avvisi) if parsed.avvisi else ""

            obj = MovimentoRegistroStudioConsulente(
                azienda=azienda,
                tipo_riga="documento",
                tipo_documento=parsed.tipo_documento,
                numero_documento=parsed.numero_documento[:80],
                data_documento=parsed.data_documento,
                totale_da_pagare=parsed.totale_da_pagare,
                dare=dare,
                avere=Decimal("0"),
                nome_file=nome,
                testo_estratto=testo[:50000],
                metodo_estrazione=metodo,
                note=note[:500],
                importato_da=user,
            )
            obj.save()
            if hasattr(up, "seek"):
                up.seek(0)
            obj.file.save(nome, File(up), save=True)
            n_ok += 1
        except Exception as exc:
            msgs.append(f"{nome}: {exc}")
            n_err += 1
        finally:
            if tmp_path:
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass

    if n_ok or n_err:
        ricalcola_saldi_progressivi(azienda.id)
    if n_ok:
        msgs.append(f"Importati {n_ok} documenti.")
    if n_err:
        msgs.append(f"{n_err} file con errori.")
    return msgs


def applica_upload_bonifici_pdf(azienda, user, uploads) -> list[str]:
    """Importa distinte bonifico PDF in avere (con allegato); parsing euristico."""
    import os
    import tempfile
    from pathlib import Path

    from django.core.files import File

    from .models import MovimentoRegistroStudioConsulente

    msgs: list[str] = []
    n_ok = n_skip = n_err = 0
    for up in uploads:
        nome = (up.name or "bonifico.pdf")[:280]
        if MovimentoRegistroStudioConsulente.objects.filter(
            azienda=azienda, nome_file=nome, tipo_riga="bonifico"
        ).exists():
            msgs.append(f"Bonifico già presente (stesso nome file): {nome}")
            n_skip += 1
            continue
        tmp_path = None
        try:
            with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
                for chunk in up.chunks():
                    tmp.write(chunk)
                tmp_path = tmp.name
            path = Path(tmp_path)
            testo, metodo = estrai_testo_da_pdf(path)
            parsed = parse_testo_bonifico_pdf(testo, nome)
            if not parsed.importo or parsed.importo <= 0:
                msgs.append(f"{nome}: importo non rilevato; usare la sezione Pagamenti con inserimento manuale.")
                n_err += 1
                continue
            if not parsed.riferimento or len(parsed.riferimento.strip()) < 3:
                msgs.append(f"{nome}: riferimento assente; usare inserimento manuale.")
                n_err += 1
                continue
            nome_sint = nome[:200]
            note = "; ".join(parsed.avvisi) if parsed.avvisi else ""
            obj = MovimentoRegistroStudioConsulente(
                azienda=azienda,
                tipo_riga="bonifico",
                tipo_documento="sconosciuto",
                numero_documento=parsed.riferimento[:80],
                data_documento=parsed.data_documento,
                totale_da_pagare=None,
                dare=Decimal("0"),
                avere=parsed.importo,
                nome_file=nome_sint,
                testo_estratto=testo[:50000],
                metodo_estrazione=metodo,
                note=note[:500],
                riferimento_pagamento=parsed.riferimento[:160],
                causale_pagamento=parsed.causale[:220],
                importato_da=user,
            )
            obj.save()
            if hasattr(up, "seek"):
                up.seek(0)
            obj.file.save(nome, File(up), save=True)
            n_ok += 1
        except Exception as exc:
            msgs.append(f"{nome}: {exc}")
            n_err += 1
        finally:
            if tmp_path:
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass

    if n_ok or n_err:
        ricalcola_saldi_progressivi(azienda.id)
    if n_ok:
        msgs.append(f"Registrati {n_ok} bonifici da PDF.")
    if n_err:
        msgs.append(f"{n_err} PDF bonifici non importati automaticamente.")
    return msgs


def ricalcola_totali_documenti_da_testo_estratto(azienda_id: int) -> dict[str, int | str]:
    """
    Rilegge proforma/parcelle già importate: ``parse_testo_proforma_parcella`` su ``testo_estratto``,
    aggiorna totale/dare e campi anagrafici documento, poi ricalcola i saldi progressivi.

    Non modifica righe ``bonifico`` / ``rettifica``. Righe senza testo salvato vengono saltate.
    """
    from django.db import transaction
    from django.db.models import F

    from .models import MovimentoRegistroStudioConsulente

    n_senza_testo = n_invariati = n_aggiornati = 0
    bulk: list[MovimentoRegistroStudioConsulente] = []

    qs = (
        MovimentoRegistroStudioConsulente.objects.filter(azienda_id=azienda_id, tipo_riga="documento")
        .order_by(F("data_documento").asc(nulls_last=True), "importato_il", "id")
    )
    for row in qs.iterator(chunk_size=100):
        testo = (row.testo_estratto or "").strip()
        if not testo:
            n_senza_testo += 1
            continue
        parsed = parse_testo_proforma_parcella(testo, row.nome_file or "documento.pdf")
        tot = parsed.totale_da_pagare or Decimal("0")
        nuovo_dare = tot if tot > 0 else Decimal("0")
        note = ("; ".join(parsed.avvisi))[:500] if parsed.avvisi else ""

        cambia = (
            row.totale_da_pagare != parsed.totale_da_pagare
            or row.dare != nuovo_dare
            or row.tipo_documento != parsed.tipo_documento
            or (row.numero_documento or "") != (parsed.numero_documento or "")[:80]
            or row.data_documento != parsed.data_documento
            or (row.note or "") != note
        )
        if not cambia:
            n_invariati += 1
            continue

        row.totale_da_pagare = parsed.totale_da_pagare
        row.dare = nuovo_dare
        row.tipo_documento = parsed.tipo_documento
        row.numero_documento = (parsed.numero_documento or "")[:80]
        row.data_documento = parsed.data_documento
        row.note = note
        bulk.append(row)
        n_aggiornati += 1

    if bulk:
        with transaction.atomic():
            MovimentoRegistroStudioConsulente.objects.bulk_update(
                bulk,
                [
                    "totale_da_pagare",
                    "dare",
                    "tipo_documento",
                    "numero_documento",
                    "data_documento",
                    "note",
                ],
                batch_size=100,
            )
        ricalcola_saldi_progressivi(azienda_id)

    msg = (
        f"Aggiornate {n_aggiornati} righe documento; {n_invariati} già allineate; "
        f"{n_senza_testo} senza testo estratto (reimportare il PDF)."
    )
    return {
        "n_aggiornati": n_aggiornati,
        "n_invariati": n_invariati,
        "n_senza_testo": n_senza_testo,
        "message": msg,
    }


def ricalcola_saldi_progressivi(azienda_id: int) -> None:
    """Ricalcola ``saldo_progressivo`` per tutte le righe dell'azienda (ordine: data doc, import, id)."""
    from django.db.models import F

    from .models import MovimentoRegistroStudioConsulente

    qs = (
        MovimentoRegistroStudioConsulente.objects.filter(azienda_id=azienda_id)
        .order_by(F("data_documento").asc(nulls_last=True), "importato_il", "id")
        .only("id", "dare", "avere", "saldo_progressivo")
    )
    saldo = Decimal("0")
    bulk: list[MovimentoRegistroStudioConsulente] = []
    for row in qs:
        saldo = saldo + row.dare - row.avere
        if row.saldo_progressivo != saldo:
            row.saldo_progressivo = saldo
            bulk.append(row)
    if bulk:
        MovimentoRegistroStudioConsulente.objects.bulk_update(bulk, ["saldo_progressivo"], batch_size=200)


# ── Parsing bonifico da PDF (euristica) ───────────────────────────────────────


@dataclass
class EsitoParsingBonifico:
    riferimento: str
    data_documento: date | None
    importo: Decimal | None
    causale: str
    avvisi: list[str] = field(default_factory=list)


def parse_testo_bonifico_pdf(testo: str, nome_file: str) -> EsitoParsingBonifico:
    """Estrae riferimento, data e importo da testo estratto da distinta bonifico (best-effort)."""
    avvisi: list[str] = []
    t = testo or ""
    rif = ""
    for pat in (
        r"(?:CRO|Riferimento\s+CRO|Codice\s+CRO)\s*[:\s]+([A-Za-z0-9/\-\s]{6,45})",
        r"(?:TRN|End\s+To\s+End\s+Id|Riferimento\s+operazione)\s*[:\s]+([A-Za-z0-9/\-\s]{6,45})",
        r"(?:Ordinativo|Numero\s+disposizione)\s*[:\s]+([A-Za-z0-9/\-\s]{6,40})",
    ):
        m = re.search(pat, t, re.I)
        if m:
            rif = re.sub(r"\s+", " ", m.group(1).strip())[:160]
            break
    if not rif:
        digits = re.findall(r"\b\d{10,30}\b", t)
        if digits:
            rif = max(digits, key=len)[:160]
    if not rif and nome_file:
        stem = Path(nome_file).stem
        if len(stem) >= 6:
            rif = stem[:160]
            avvisi.append("Riferimento non trovato nel PDF: usato nome file.")

    data_doc = _first_date_in_text(t[:8000])
    imp: Decimal | None = None
    for pat in (
        r"(?:importo\s+ordinato|importo\s+bonifico|importo\s+accreditato)\s*[:\s€]*([\d\.\s,]+(?:,\d{1,2})?)",
        r"(?:EUR|€)\s*([\d\.\s,]+(?:,\d{1,2})?)\s*(?:accredit|bonific)",
        r"(?:accreditato|versato)\s+([\d\.\s,]+(?:,\d{1,2})?)\s*€?",
    ):
        m = re.search(pat, t, re.I)
        if m:
            imp = _parse_it_decimal(m.group(1))
            if imp and imp > 0:
                break
    if imp is None or imp <= 0:
        avvisi.append("Importo non rilevato automaticamente dal PDF.")
        imp = None

    caus = ""
    m_c = re.search(r"(?:causale|oggetto|descrizione)\s*[:\s]+(.{0,200}?)(?:\n|$)", t, re.I | re.MULTILINE)
    if m_c:
        caus = m_c.group(1).strip()[:220]

    if not rif:
        avvisi.append("Riferimento non rilevato: compilare a mano o rinominare il file.")

    return EsitoParsingBonifico(
        riferimento=rif,
        data_documento=data_doc,
        importo=imp,
        causale=caus,
        avvisi=avvisi,
    )


def _norm_match(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").lower().strip())


def trova_movimento_documento_per_descrizione(azienda_id: int, descrizione: str):
    """Aggancia una riga estratto a un movimento proforma/parcella già importato (PDF)."""
    from .models import MovimentoRegistroStudioConsulente

    desc_n = _norm_match(descrizione)
    if len(desc_n) < 4:
        return None
    qs = MovimentoRegistroStudioConsulente.objects.filter(azienda_id=azienda_id, tipo_riga="documento").order_by(
        "-importato_il"
    )
    for m in qs[:400]:
        candidates = [
            _norm_match(m.numero_documento),
            _norm_match(Path(m.nome_file).stem if m.nome_file else ""),
            _norm_match((m.note or "")[:240]),
        ]
        if m.testo_estratto:
            candidates.append(_norm_match(m.testo_estratto[:400]))
        for c in candidates:
            if len(c) < 3:
                continue
            if c in desc_n or desc_n in c:
                return m
            if m.numero_documento and m.numero_documento.strip() in descrizione:
                return m
    return None


def trova_movimento_bonifico_per_riferimento(azienda_id: int, riferimento: str):
    """Aggancia per CRO / TRN / sottostringa su movimenti bonifico già registrati."""
    from .models import MovimentoRegistroStudioConsulente

    rif_clean = re.sub(r"\s+", "", (riferimento or "")).upper()
    if len(rif_clean) < 6:
        return None
    for m in MovimentoRegistroStudioConsulente.objects.filter(azienda_id=azienda_id, tipo_riga="bonifico").order_by(
        "-importato_il"
    )[:300]:
        mr = re.sub(r"\s+", "", (m.riferimento_pagamento or "")).upper()
        if not mr:
            continue
        if rif_clean in mr or mr in rif_clean or rif_clean[-12:] in mr:
            return m
    return None


def _pick_excel_col(headers: list[str], patterns: tuple[str, ...]) -> int | None:
    for i, raw in enumerate(headers):
        s = (raw or "").strip().lower()
        for p in patterns:
            if p in s:
                return i
    return None


def _cell_to_date(val) -> date | None:
    if val is None:
        return None
    if hasattr(val, "year") and hasattr(val, "month"):
        try:
            return date(int(val.year), int(val.month), int(val.day))
        except (ValueError, TypeError):
            pass
    if isinstance(val, str):
        for part in re.split(r"[\s;]+", val.strip()):
            d = _parse_date(part)
            if d:
                return d
    return None


def import_estratto_excel(
    fileobj,
    nome_file: str,
    azienda,
    user,
) -> tuple["ImportEstrattoContoStudio", list[str]]:
    """
    Legge il primo foglio Excel, crea Import + righe con aggancio a movimenti esistenti.
    Colonne rilevate per nome (prima riga intestazioni): descrizione, importo, data, riferimento.
    Righe con riferimento bancario forte: solo match su bonifici; altrimenti match su documenti.
    """
    from django.core.files.base import ContentFile
    from django.db import transaction

    from openpyxl import load_workbook

    from .models import ImportEstrattoContoStudio, RigaEstrattoContoStudio

    raw_bytes = fileobj.read()
    try:
        fileobj.seek(0)
    except (OSError, AttributeError, io.UnsupportedOperation):
        pass

    msgs: list[str] = []
    wb = load_workbook(io.BytesIO(raw_bytes), read_only=True, data_only=True)
    try:
        sheet = wb.worksheets[0]
        rows_iter = sheet.iter_rows(values_only=True)
        header = next(rows_iter, None)
        if not header:
            raise ValueError("Il foglio Excel è vuoto.")
        headers = [str(h).strip() if h is not None else "" for h in header]
        ci_desc = _pick_excel_col(headers, ("descri", "caus", "oggetto", "detail", "operaz", "movimento"))
        ci_imp = _pick_excel_col(headers, ("importo", "amount", "eur", "€", "valore", "addebit", "uscit"))
        ci_data = _pick_excel_col(headers, ("data", "date"))
        ci_rif = _pick_excel_col(headers, ("cro", "trn", "rif", "rifer", "ordinativo", "sct", "end to end"))

        if ci_desc is None and ci_rif is None:
            raise ValueError(
                "Intestazioni non riconosciute: servono colonne con nomi tipo "
                "«descrizione», «causale», «importo», «data», «CRO» / «riferimento»."
            )

        def row_vals(r) -> dict[str, object]:
            out: dict[str, object] = {}
            for j, h in enumerate(headers):
                if not h:
                    continue
                if j < len(r):
                    out[h[:60]] = r[j]
            return out

        with transaction.atomic():
            imp_obj = ImportEstrattoContoStudio(
                azienda=azienda,
                nome_file=(nome_file or "estratto.xlsx")[:280],
                importato_da=user,
            )
            imp_obj.save()
            righe_create: list[RigaEstrattoContoStudio] = []
            n_row = 1
            agg = 0
            letti = 0
            for row in rows_iter:
                n_row += 1
                if not row or all(v is None or str(v).strip() == "" for v in row):
                    continue
                desc = ""
                if ci_desc is not None and ci_desc < len(row) and row[ci_desc] is not None:
                    desc = str(row[ci_desc]).strip()[:600]
                rif = ""
                if ci_rif is not None and ci_rif < len(row) and row[ci_rif] is not None:
                    rif = str(row[ci_rif]).strip()[:200]
                imp_ex = None
                if ci_imp is not None and ci_imp < len(row) and row[ci_imp] is not None:
                    imp_ex = _parse_it_decimal(str(row[ci_imp]))
                d_ex = None
                if ci_data is not None and ci_data < len(row):
                    d_ex = _cell_to_date(row[ci_data])

                raw = row_vals(row)
                mov = None
                esito = "non_trovato"

                rif_clean = re.sub(r"\s+", "", rif).upper()
                strong_bonif = len(rif_clean) >= 10 or bool(
                    re.search(r"\b(CRO|TRN|BONIF|BONIFICO|DISPOSIZIONE)\b", (desc + " " + rif), re.I)
                )

                if strong_bonif:
                    mov = trova_movimento_bonifico_per_riferimento(azienda.id, rif or desc)
                else:
                    if desc:
                        mov = trova_movimento_documento_per_descrizione(azienda.id, desc)

                if mov:
                    esito = "agganciato"
                    agg += 1
                elif not desc and not rif:
                    esito = "saltato"
                letti += 1
                righe_create.append(
                    RigaEstrattoContoStudio(
                        importazione=imp_obj,
                        indice_riga=n_row,
                        descrizione=desc,
                        importo_excel=imp_ex,
                        data_excel=d_ex,
                        riferimento_excel=rif[:200],
                        celle_raw=raw,
                        movimento=mov,
                        esito_match=esito,
                    )
                )

            RigaEstrattoContoStudio.objects.bulk_create(righe_create, batch_size=300)
            imp_obj.righe_lette = letti
            imp_obj.righe_agganciate = agg
            imp_obj.save(update_fields=["righe_lette", "righe_agganciate"])

        if raw_bytes:
            imp_obj.file.save((nome_file or "estratto.xlsx")[:280], ContentFile(raw_bytes), save=True)
    finally:
        wb.close()

    msgs.append(f"Lette {imp_obj.righe_lette} righe; agganciate {imp_obj.righe_agganciate} a movimenti PDF/bonifici.")
    return imp_obj, msgs
