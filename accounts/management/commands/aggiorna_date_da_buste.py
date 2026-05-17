"""
Management command: aggiorna_date_da_buste
==========================================
Rilegge tutti i PDF di buste paga gia importati e aggiorna i campi anagrafici
del Dipendente correlato:
  - data_assunzione, data_cessazione, data_nascita
  - ruolo  (estratto dalla QUALIFICA del cedolino, solo se vuoto / "Da completare")
  - stato  (-> cessato se viene trovata una data cessazione)
  - nome, cognome  (solo con --aggiorna-nomi)

Matching: codice fiscale estratto dal contenuto del PDF.

Uso:
    python manage.py aggiorna_date_da_buste
    python manage.py aggiorna_date_da_buste --dry-run --verboso
    python manage.py aggiorna_date_da_buste --aggiorna-nomi --dry-run
"""
from __future__ import annotations

import re
import subprocess
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path
from typing import Optional

from django.core.management.base import BaseCommand

from anagrafiche.models import Dipendente
from documenti.models import Documento


PDF_PASSWORD = "DOLCEMASCOLO"


# ===========================================================================
# Estrazione testo dal PDF
# ===========================================================================

def _get_pdf_text(filepath: str) -> str:
    """Testo dal PDF: pdftotext -layout, fallback pypdf."""
    try:
        proc = subprocess.run(
            ["pdftotext", "-layout", "-upw", PDF_PASSWORD, filepath, "-"],
            check=True, capture_output=True, text=True, timeout=30,
        )
        if proc.stdout.strip():
            return proc.stdout
    except Exception:
        pass
    try:
        import importlib
        PdfReader = importlib.import_module("pypdf").PdfReader
        reader = PdfReader(filepath)
        if getattr(reader, "is_encrypted", False):
            reader.decrypt(PDF_PASSWORD)
        pages: list[str] = []
        for p in reader.pages:
            try:
                pages.append(p.extract_text() or "")
            except Exception:
                pages.append("")
        return "\n".join(pages)
    except Exception:
        return ""


# ===========================================================================
# Helpers - date
# ===========================================================================

def _parse_date_raw(fragment: str) -> Optional[str]:
    """Prima data DD/MM/YYYY trovata (anno 2 o 4 cifre)."""
    m = re.search(r"(\d{1,2})\s*/\s*(\d{1,2})\s*/\s*(\d{2,4})", fragment)
    if not m:
        return None
    try:
        d_v, mo_v, y_v = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if y_v < 100:
            y_v = 2000 + y_v if y_v <= 30 else 1900 + y_v
        if 1 <= d_v <= 31 and 1 <= mo_v <= 12 and 1940 <= y_v <= 2100:
            return f"{d_v:02d}/{mo_v:02d}/{y_v:04d}"
    except Exception:
        pass
    return None


def _parse_date_obj(raw: Optional[str]) -> Optional[date]:
    """DD/MM/YYYY -> oggetto date."""
    if not raw:
        return None
    try:
        parts = raw.replace(" ", "").split("/")
        if len(parts) != 3:
            return None
        d_v, m_v, y_v = int(parts[0]), int(parts[1]), int(parts[2])
        if y_v < 100:
            y_v = 2000 + y_v if y_v <= 30 else 1900 + y_v
        if 1 <= d_v <= 31 and 1 <= m_v <= 12 and 1940 <= y_v <= 2100:
            return date(y_v, m_v, d_v)
    except Exception:
        pass
    return None


def _all_dates_in_text(text: str) -> list[str]:
    return [
        d
        for d in (
            _parse_date_raw(m.group(0))
            for m in re.finditer(r"\d{1,2}\s*/\s*\d{1,2}\s*/\s*\d{2,4}", text or "")
        )
        if d
    ]


def _pick_assunzione(candidates: list[str], birth: Optional[str]) -> Optional[str]:
    """Data assunzione piu plausibile (preferisce anni >= 2000)."""
    if not candidates:
        return None
    cur = date.today().year
    birth_year: Optional[int] = None
    if birth:
        try:
            birth_year = int(birth.split("/")[-1])
        except Exception:
            pass
    for d in candidates:
        if birth and d == birth:
            continue
        try:
            y = int(d.split("/")[-1])
        except Exception:
            continue
        if 2000 <= y <= cur + 1:
            return d
    for d in candidates:
        if birth and d == birth:
            continue
        try:
            y = int(d.split("/")[-1])
        except Exception:
            y = None
        if y is not None:
            if birth_year and y < birth_year + 14:
                continue
            if y < 1980 or y > 2100:
                continue
            return d
    for d in candidates:
        if not birth or d != birth:
            return d
    return candidates[0]


# ===========================================================================
# Helpers - estrazione campi anagrafici
# ===========================================================================

def _extract_cf(text: str) -> Optional[str]:
    m = re.search(r"\b([A-Z]{6}[0-9]{2}[A-Z][0-9]{2}[A-Z][0-9]{3}[A-Z])\b", text.upper())
    return m.group(1) if m else None


def _extract_data_nascita(text: str) -> Optional[str]:
    m = re.search(r"DATA\s*DI\s*NAS\.?", text, re.IGNORECASE)
    if not m:
        return None
    tail = text[m.end(): m.end() + 500]
    d = re.search(r"(\d{1,2}\s*/\s*\d{1,2}\s*/\s*\d{2,4})", tail)
    return _parse_date_raw(d.group(1)) if d else None


def _extract_data_assunzione(text: str) -> Optional[str]:
    upper = text.upper()
    birth = _extract_data_nascita(text)
    patterns = [
        r"DATA\s+ASS\.?\s*CONV\.?",
        r"DATA\s+ASSUNZ\.?",
        r"DATA\s+ASS\.\s+CONTR\.?",
        r"DATA\s+ASSUNZIONE\s+CONV\.?",
        r"DATA\s+ASSUNZIONE",
        r"DATA\s+INIZIO\s+RAPPORTO",
    ]
    for pat in patterns:
        m = re.search(pat, upper)
        if not m:
            continue
        tail = text[m.end(): m.end() + 3500]
        selected = _pick_assunzione(_all_dates_in_text(tail), birth)
        if selected:
            return selected
    cf = _extract_cf(text)
    if cf:
        pos_cf = upper.find(cf)
        if pos_cf >= 0:
            window = text[max(0, pos_cf - 500): pos_cf + 900]
            return _pick_assunzione(_all_dates_in_text(window), birth)
    return None


def _extract_data_cessazione(text: str) -> Optional[str]:
    upper = text.upper()
    for pat in [
        r"DATA\s+CESSAZIONE",
        r"DATA\s+FINE\s+RAPPORTO",
        r"CESSAZIONE\s+(?:AL|IL|DAL|DEL)\s",
        r"DATA\s+TERM\.?\s*RAPPORTO",
    ]:
        m = re.search(pat, upper)
        if not m:
            continue
        parsed = _parse_date_raw(text[m.end(): m.end() + 120])
        if parsed:
            return parsed
    m2 = re.search(r"CESS\.?\s*RAPP\.?[^\d]{0,20}(\d{1,2}\s*/\s*\d{1,2}\s*/\s*\d{2,4})", upper)
    if m2:
        return _parse_date_raw(m2.group(1))
    return None


_STOP_WORDS: set[str] = {
    "COGNOME", "NOME", "CODICE", "FISCALE", "DATA", "ASSUNZ", "COMUNE",
    "RESIDENZA", "POSIZIONE", "INAIL", "MATR", "INPS", "AZIENDA", "MESE",
    "RETRIBUITO", "NETTO", "BUSTA", "QUALIFICA", "DESCRIZIONE", "TRATTENUTE",
    "ATT", "PREC", "SIGLA", "QUANTITA", "DITTA", "FOGLIO", "STAMPATO",
    "AUTORIZZAZIONE", "VOCE", "TARIFFA", "SCAD", "DOC", "STATISTICHE",
    "LIVELLO", "SCATTI", "ANZ", "CCNL", "CONTRATTO", "RETRIBUZIONE", "BASE",
}


def _merge_tokens(tokens: list[str]) -> list[str]:
    """Unisce token brevi OCR: MASS I MO -> MASSIMO."""
    merged: list[str] = []
    for t in tokens:
        if merged and len(t) <= 2 and t.isalpha():
            merged[-1] = merged[-1] + t
        else:
            merged.append(t)
    return merged


def _nsp(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip()


def _extract_full_name(text: str) -> Optional[str]:
    """COGNOME+NOME come stringa unica dal cedolino."""
    upper = text.upper()
    m = re.search(
        r"COGNOME\s+E\s+NOME(?P<body>[\s\S]{0,240}?)CODICE\s+FISCALE",
        upper, re.IGNORECASE,
    )
    if m:
        for ln in m.group("body").splitlines():
            ln = _nsp(ln)
            if not ln:
                continue
            if re.fullmatch(r"[A-Z'\- ]{4,}", ln):
                words = [w for w in ln.split() if w not in _STOP_WORDS and len(w) > 1]
                if len(words) >= 2:
                    return " ".join(_merge_tokens(words[:5])).title()
    for ln in text.splitlines():
        line = _nsp(ln.upper())
        if not line or "COGNOME E NOME" in line:
            continue
        m2 = re.search(r"\b([A-Z' ]{6,}?)\s+(\d{1,2}\s*/\s*\d{1,2}\s*/\s*\d{2,4})\b", line)
        if m2:
            candidate = _nsp(re.sub(r"\b\d+\b", " ", m2.group(1)))
            words = [w for w in candidate.split() if w not in _STOP_WORDS and len(w) > 1]
            if len(words) >= 2:
                return " ".join(_merge_tokens(words[-5:])).title()
    return None


def _split_name(full: str) -> tuple[str, str]:
    """COGNOME NOME -> (cognome, nome). Ultima parola = nome, resto = cognome."""
    if not full:
        return "", ""
    parts = full.strip().split()
    if len(parts) == 1:
        return parts[0], ""
    return " ".join(parts[:-1]), parts[-1]


def _extract_qualifica(text: str) -> Optional[str]:
    """Qualifica/ruolo dal cedolino paga."""
    upper = text.upper()
    m = re.search(r"QUALIFICA", upper)
    if not m:
        return None
    tail = text[m.end(): m.end() + 400]
    for ln in tail.splitlines()[:7]:
        ln_up = _nsp(ln.upper())
        if not ln_up:
            continue
        if re.search(r"\b(?:LIVELLO|SCATTI|CCNL|RETRIBUZ|DATA|ASSUNZ)\b", ln_up):
            continue
        words = re.findall(r"[A-Z][A-Z']{2,}", ln_up)
        if not words:
            continue
        filtered = [w for w in words if w not in _STOP_WORDS]
        if filtered:
            return " ".join(_merge_tokens(filtered[:5])).title()
    return None


# ===========================================================================
# Estrazione completa da un singolo PDF
# ===========================================================================

_EMPTY_FIELDS: dict = {
    "cf": None, "full_name": None, "data_nascita": None,
    "data_assunzione": None, "data_cessazione": None, "qualifica": None,
}


def _extract_all_fields(filepath: str) -> dict:
    """Estrae tutti i campi anagrafici da un PDF di busta paga."""
    text = _get_pdf_text(filepath)
    if not text.strip():
        return dict(_EMPTY_FIELDS)
    return {
        "cf": _extract_cf(text),
        "full_name": _extract_full_name(text),
        "data_nascita": _extract_data_nascita(text),
        "data_assunzione": _extract_data_assunzione(text),
        "data_cessazione": _extract_data_cessazione(text),
        "qualifica": _extract_qualifica(text),
    }


# ===========================================================================
# Consolidamento valori su piu PDF dello stesso dipendente
# ===========================================================================

def _consolidate(fields_list: list[dict]) -> dict:
    """Consolida i valori migliori da piu PDF:
    cf/qualifica = piu frequente, full_name = piu lungo,
    data_assunzione = piu antica, data_cessazione = piu recente.
    """
    result: dict = dict(_EMPTY_FIELDS)

    cfs = [f["cf"] for f in fields_list if f.get("cf")]
    if cfs:
        result["cf"] = Counter(cfs).most_common(1)[0][0]

    names = [f["full_name"] for f in fields_list if f.get("full_name")]
    if names:
        result["full_name"] = max(names, key=len)

    nascite = [f["data_nascita"] for f in fields_list if f.get("data_nascita")]
    if nascite:
        result["data_nascita"] = Counter(nascite).most_common(1)[0][0]

    ass_objs: list[tuple[date, str]] = []
    for f in fields_list:
        raw = f.get("data_assunzione")
        obj = _parse_date_obj(raw)
        if obj and raw:
            ass_objs.append((obj, raw))
    if ass_objs:
        ass_objs.sort()
        result["data_assunzione"] = ass_objs[0][1]

    cess_objs: list[tuple[date, str]] = []
    for f in fields_list:
        raw = f.get("data_cessazione")
        obj = _parse_date_obj(raw)
        if obj and raw:
            cess_objs.append((obj, raw))
    if cess_objs:
        cess_objs.sort(reverse=True)
        result["data_cessazione"] = cess_objs[0][1]

    quals = [f["qualifica"] for f in fields_list if f.get("qualifica")]
    if quals:
        result["qualifica"] = Counter(quals).most_common(1)[0][0]

    return result


# ===========================================================================
# Command
# ===========================================================================

_RUOLO_PLACEHOLDER = {"da completare", ""}


class Command(BaseCommand):
    help = (
        "Rilegge tutti i PDF buste paga importati, estrae CF, nome, cognome, "
        "data_nascita, data_assunzione, data_cessazione, ruolo e aggiorna i "
        "record Dipendente solo dove i valori differiscono."
    )

    def add_arguments(self, parser):
        parser.add_argument("--azienda-id", type=int, default=None,
                            help="Limita a una singola azienda.")
        parser.add_argument("--dry-run", action="store_true",
                            help="Simula senza salvare.")
        parser.add_argument("--verboso", action="store_true",
                            help="Output dettagliato per ogni PDF.")
        parser.add_argument("--solo-mancanti", action="store_true",
                            help="Aggiorna solo Dipendenti senza data_assunzione.")
        parser.add_argument("--aggiorna-nomi", action="store_true",
                            help="Aggiorna anche nome e cognome dal PDF.")

    def handle(self, *args, **options):  # noqa: C901
        dry_run: bool = options["dry_run"]
        verboso: bool = options["verboso"]
        solo_mancanti: bool = options["solo_mancanti"]
        aggiorna_nomi: bool = options["aggiorna_nomi"]
        azienda_id: Optional[int] = options.get("azienda_id")

        if dry_run:
            self.stdout.write(self.style.WARNING("=== DRY-RUN: nessuna modifica verra salvata ===\n"))

        qs = Documento.objects.filter(
            tipo="busta_paga", dipendente__isnull=False
        ).select_related("dipendente", "azienda")
        if azienda_id:
            qs = qs.filter(azienda_id=azienda_id)

        dip_docs: dict[int, list[Documento]] = defaultdict(list)
        skipped_no_file = 0

        for doc in qs.order_by("-data_caricamento", "-id"):
            if not getattr(doc, "file", None):
                skipped_no_file += 1
                continue
            name = getattr(doc.file, "name", None)
            if not name:
                skipped_no_file += 1
                continue
            try:
                if not doc.file.storage.exists(name):
                    skipped_no_file += 1
                    continue
            except Exception:
                skipped_no_file += 1
                continue
            dip_docs[doc.dipendente.id].append(doc)  # type: ignore[union-attr]

        total_dip = len(dip_docs)
        self.stdout.write(
            f"Dipendenti con buste paga: {total_dip} | Documenti senza file: {skipped_no_file}\n"
        )

        aggiornati = 0
        nessuna_data = 0
        gia_ok = 0
        errori = 0

        for dip_id, docs in dip_docs.items():
            try:
                dip = Dipendente.objects.get(id=dip_id)
            except Dipendente.DoesNotExist:
                continue

            if solo_mancanti and dip.data_assunzione is not None:
                gia_ok += 1
                if verboso:
                    self.stdout.write(f"  SKIP {dip} -- data_assunzione gia presente ({dip.data_assunzione})")
                continue

            fields_per_pdf: list[dict] = []
            for doc in docs:
                try:
                    filepath = doc.file.storage.path(doc.file.name)
                    fields = _extract_all_fields(filepath)
                    fields_per_pdf.append(fields)
                    if verboso:
                        self.stdout.write(
                            f"    PDF {Path(doc.file.name).name}: "
                            f"cf={fields['cf'] or '-'} | "
                            f"nome={fields['full_name'] or '-'} | "
                            f"nasc={fields['data_nascita'] or '-'} | "
                            f"ass={fields['data_assunzione'] or '-'} | "
                            f"cess={fields['data_cessazione'] or '-'} | "
                            f"qual={fields['qualifica'] or '-'}"
                        )
                except Exception as exc:
                    if verboso:
                        self.stdout.write(self.style.ERROR(f"    ERR {doc}: {exc}"))
                    errori += 1

            if not fields_per_pdf:
                nessuna_data += 1
                if verboso:
                    self.stdout.write(self.style.WARNING(f"  NODATA {dip}: nessun campo estratto"))
                continue

            best = _consolidate(fields_per_pdf)

            pdf_cf = best.get("cf")
            if pdf_cf and dip.codice_fiscale:
                if pdf_cf.upper() != dip.codice_fiscale.upper():
                    self.stdout.write(self.style.WARNING(
                        f"  WARN {dip}: CF nel PDF ({pdf_cf}) != DB ({dip.codice_fiscale})"
                    ))

            changed: list[str] = []
            riepilogo: list[str] = []

            best_ass = _parse_date_obj(best.get("data_assunzione"))
            if best_ass is not None and dip.data_assunzione != best_ass:
                dip.data_assunzione = best_ass
                changed.append("data_assunzione")
                riepilogo.append(f"ass={best_ass}")

            best_cess = _parse_date_obj(best.get("data_cessazione"))
            if best_cess is not None and dip.data_cessazione != best_cess:
                dip.data_cessazione = best_cess
                changed.append("data_cessazione")
                riepilogo.append(f"cess={best_cess}")
                if dip.stato != "cessato":
                    dip.stato = "cessato"
                    changed.append("stato")
                    riepilogo.append("stato->cessato")

            best_nasc = _parse_date_obj(best.get("data_nascita"))
            if best_nasc is not None and dip.data_nascita != best_nasc:
                dip.data_nascita = best_nasc
                changed.append("data_nascita")
                riepilogo.append(f"nasc={best_nasc}")

            best_qual = (best.get("qualifica") or "").strip()
            ruolo_attuale = (dip.ruolo or "").strip().lower()
            if best_qual and ruolo_attuale in _RUOLO_PLACEHOLDER:
                dip.ruolo = best_qual
                changed.append("ruolo")
                riepilogo.append(f"ruolo={best_qual!r}")

            if aggiorna_nomi and best.get("full_name"):
                new_cogn, new_nome = _split_name(best["full_name"])
                if new_cogn and new_cogn.strip().lower() != (dip.cognome or "").strip().lower():
                    dip.cognome = new_cogn
                    changed.append("cognome")
                    riepilogo.append(f"cognome={new_cogn!r}")
                if new_nome and new_nome.strip().lower() != (dip.nome or "").strip().lower():
                    dip.nome = new_nome
                    changed.append("nome")
                    riepilogo.append(f"nome={new_nome!r}")

            if not changed:
                gia_ok += 1
                if verboso:
                    self.stdout.write(f"  OK  {dip}: tutti i campi gia aggiornati")
                continue

            label = ", ".join(riepilogo)

            if not dry_run:
                try:
                    dip.save(update_fields=changed)
                    aggiornati += 1
                    self.stdout.write(self.style.SUCCESS(f"  UPD [{dip.azienda}] {dip}: {label}"))
                except Exception as exc:
                    errori += 1
                    self.stdout.write(self.style.ERROR(f"  ERR {dip}: {exc}"))
            else:
                aggiornati += 1
                self.stdout.write(self.style.SUCCESS(f"  DRY [{dip.azienda}] {dip}: {label}"))

        self.stdout.write("\n" + "-" * 60)
        self.stdout.write(
            f"Dipendenti processati : {total_dip}\n"
            f"  Aggiornati          : {aggiornati}\n"
            f"  Gia completi / skip : {gia_ok}\n"
            f"  Nessun campo trovato: {nessuna_data}\n"
            f"  Errori              : {errori}"
        )
        if dry_run:
            self.stdout.write(self.style.WARNING("\nDRY-RUN completato -- nessuna modifica salvata."))
        else:
            self.stdout.write(self.style.SUCCESS("\nAggiornamento completato."))
