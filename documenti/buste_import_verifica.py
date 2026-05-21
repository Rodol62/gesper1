"""Verifica post-import buste da PDF unico (admin/HR)."""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

from django.conf import settings

from accounts.models import MovimentoImportPaghe
from documenti.file_path_resolution import (
    first_existing_relpath_for_stored_name,
    stored_relpath_equivalent,
)
from documenti.models import Documento


def _documento_ha_file_su_storage(doc: Documento | None) -> bool:
    if not doc or not getattr(doc, "file", None):
        return False
    name = getattr(doc.file, "name", None)
    if not name:
        return False
    resolved = first_existing_relpath_for_stored_name(doc.file.storage, name)
    return bool(resolved and stored_relpath_equivalent(resolved, name))


def _safe_upload_basename(name: str) -> str:
    base = (name or "upload.pdf").strip()
    base = Path(base).name
    return re.sub(r"[^\w.\-]+", "_", base)[:120] or "upload.pdf"


def parse_import_paghe_stdout(stdout_text: str) -> dict[str, int]:
    """Estrae contatori dalla riga finale di ``import_paghe_pdf``."""
    text = stdout_text or ""
    out: dict[str, int] = {}
    for key in ("docs_created", "movimenti_upsert", "errors", "skipped", "created"):
        m = re.search(rf"\b{key}=(\d+)", text)
        if m:
            out[key] = int(m.group(1))
    return out


def storage_paths_info() -> dict[str, str]:
    """Percorsi utili per spiegare dove finiscono DB e PDF in produzione."""
    media_root = Path(settings.MEDIA_ROOT).resolve()
    db_name = settings.DATABASES.get("default", {}).get("NAME", "")
    db_path = str(Path(db_name).resolve()) if db_name else "—"
    mapping = getattr(settings, "DOCUMENTO_TIPO_MEDIA_SUBDIRS", None) or {}
    buste_sub = mapping.get("busta_paga", "buste_paghe/")
    f24_sub = mapping.get("altro", "f24/")
    return {
        "media_root": str(media_root),
        "db_path": db_path,
        "buste_dir": str(media_root / buste_sub.strip("/")),
        "f24_dir": str(media_root / f24_sub.strip("/")),
        "snapshots_dir": str(Path(settings.BASE_DIR) / "snapshots"),
    }


def verifica_righe_dopo_import(
    azienda,
    preview_data: dict[str, Any],
) -> list[dict[str, Any]]:
    """
    Controlla ogni riga del preview: movimento BUSTA, record Documento e file su disco.
    """
    from anagrafiche.models import Dipendente

    natura_file = (preview_data.get("natura_busta_file") or "ORDINARIA").upper()
    righe: list[dict[str, Any]] = []

    for row in preview_data.get("rows") or []:
        periodo = (row.get("periodo") or "").strip()
        mese = anno = None
        m_periodo = re.match(r"^(\d{2})/(\d{4})$", periodo)
        if m_periodo:
            mese = int(m_periodo.group(1))
            anno = int(m_periodo.group(2))

        action = (row.get("action") or "").strip()
        dip_id = row.get("dipendente_id")
        dip = None
        if dip_id:
            dip = Dipendente.objects.filter(id=dip_id, azienda=azienda).first()

        natura_busta = (row.get("natura_busta") or natura_file or "ORDINARIA").upper()
        natura_label = {
            "ORDINARIA": "",
            "TREDICESIMA": " - Tredicesima",
            "QUATTORDICESIMA": " - Quattordicesima",
        }.get(natura_busta, "")
        descr = f"Busta paga {periodo}{natura_label} (import PDF)" if periodo else ""

        mov = None
        doc = None
        if mese and anno:
            mov_qs = MovimentoImportPaghe.objects.filter(
                azienda=azienda,
                tipo="BUSTA",
                anno=anno,
                mese=mese,
                natura_busta=natura_busta,
            )
            if dip:
                mov = mov_qs.filter(dipendente=dip).first()
            if mov is None and (row.get("cf") or "").strip():
                mov = mov_qs.filter(cf_estratto=(row.get("cf") or "").strip().upper()).first()
            if mov is None:
                mov = mov_qs.first()
            if mov and mov.documento_id:
                doc = mov.documento

        if doc is None and dip and descr:
            doc = (
                Documento.objects.filter(
                    azienda=azienda,
                    dipendente=dip,
                    tipo="busta_paga",
                    descrizione=descr,
                )
                .order_by("-id")
                .first()
            )

        file_ok = _documento_ha_file_su_storage(doc)
        path_rel = ""
        path_abs = ""
        if doc and getattr(doc, "file", None) and getattr(doc.file, "name", None):
            path_rel = doc.file.name
            path_abs = str(Path(settings.MEDIA_ROOT) / path_rel)

        if action == "ambiguous":
            stato = "ambiguo"
            messaggio = "Dipendente non identificato in anagrafica (CF/nome ambiguo)."
        elif not dip:
            stato = "senza_dipendente"
            messaggio = "Nessun dipendente collegato: la busta non può essere archiviata."
        elif file_ok:
            stato = "ok"
            messaggio = f"PDF su disco (#{doc.id})."
        elif doc:
            stato = "pdf_mancante"
            messaggio = f"Record documento #{doc.id} senza file in MEDIA_ROOT."
        elif mov:
            stato = "solo_movimento"
            messaggio = f"Movimento paghe #{mov.id} senza PDF collegato."
        else:
            stato = "assente"
            messaggio = "Nessun documento né movimento dopo l'import."

        righe.append(
            {
                "pagina": row.get("page"),
                "periodo": periodo or "—",
                "dipendente": dip,
                "action_preview": action,
                "stato": stato,
                "messaggio": messaggio,
                "documento_id": doc.id if doc else None,
                "movimento_id": mov.id if mov else None,
                "path_rel": path_rel,
                "path_abs": path_abs,
                "file_ok": file_ok,
            }
        )

    return righe


def check_prerequisiti_server() -> dict[str, Any]:
    """Verifica binari necessari all'analisi PDF su VPS (poppler, tesseract, venv)."""
    project_root = Path(settings.BASE_DIR)
    venv_py = project_root / "venv" / "bin" / "python"
    if not venv_py.is_file():
        venv_py = project_root / ".venv" / "bin" / "python"
    pypdf_ok = False
    if venv_py.is_file():
        try:
            subprocess.run(
                [str(venv_py), "-c", "import pypdf"],
                check=True,
                capture_output=True,
                timeout=15,
            )
            pypdf_ok = True
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError):
            pypdf_ok = False
    return {
        "pdftotext": shutil.which("pdftotext") or "",
        "pdftoppm": shutil.which("pdftoppm") or "",
        "tesseract": shutil.which("tesseract") or "",
        "venv_python": str(venv_py) if venv_py.is_file() else "",
        "pypdf_in_venv": pypdf_ok,
        "ok": bool(
            shutil.which("pdftotext")
            and shutil.which("tesseract")
            and venv_py.is_file()
            and pypdf_ok
        ),
    }


def diagnostica_pdf_unico(
    pdf_path: Path,
    azienda,
    source_name: str = "",
    *,
    simula_forza_reimport: bool = False,
) -> dict[str, Any]:
    """
    Diagnostica in 3 passi (senza scrivere in DB):
    A) il PDF è leggibile?
    B) buste riconosciute e aggancio dipendenti?
    C) dove verrebbero salvati i file?
    """
    from io import StringIO

    from django.core.management import call_command

    paths = storage_paths_info()
    prereq = check_prerequisiti_server()
    pdf_path = pdf_path.resolve()
    size_b = pdf_path.stat().st_size if pdf_path.is_file() else 0

    step_a: dict[str, Any] = {
        "file": str(pdf_path),
        "nome": source_name or pdf_path.name,
        "dimensione_kb": round(size_b / 1024, 1),
        "leggibile": False,
        "pagine": None,
        "caratteri_testo": 0,
        "prerequisiti": prereq,
        "errore": "",
    }
    preview_data: dict[str, Any] = {}
    step_b: dict[str, Any] = {"righe": [], "errore": ""}
    step_c: dict[str, Any] = {
        "buste_dir": paths["buste_dir"],
        "f24_dir": paths["f24_dir"],
        "db_path": paths["db_path"],
        "esempi_file": [],
        "errore": "",
    }

    if not pdf_path.is_file():
        step_a["errore"] = "File non trovato."
        return {"step_a": step_a, "step_b": step_b, "step_c": step_c, "preview": {}}

    if not prereq["ok"]:
        step_a["errore"] = (
            "Mancano componenti server per leggere i PDF (poppler/tesseract o venv). "
            "Senza pdftotext le scansioni non vengono lette: 0 buste in import."
        )

    snap = Path(paths["snapshots_dir"])
    snap.mkdir(parents=True, exist_ok=True)
    out_json = snap / f"diagnostica_{pdf_path.stem[:60]}.json"
    buff = StringIO()
    buff_err = StringIO()
    try:
        call_command(
            "preview_import_paghe_pdf",
            str(pdf_path),
            azienda_id=azienda.id,
            source_name=source_name or pdf_path.name,
            out=str(out_json),
            allow_replace=simula_forza_reimport,
            stdout=buff,
            stderr=buff_err,
        )
        preview_data = json.loads(out_json.read_text(encoding="utf-8"))
    except Exception as exc:
        err = str(exc).strip() or type(exc).__name__
        extra = (buff_err.getvalue() or "").strip()
        if extra and extra not in err:
            err = f"{err}\n{extra[-800:]}"
        step_a["errore"] = err
        return {"step_a": step_a, "step_b": step_b, "step_c": step_c, "preview": preview_data}

    diag = preview_data.get("diagnostics") or {}
    summary = preview_data.get("summary") or {}
    step_a.update(
        {
            "leggibile": (summary.get("buste_pages") or 0) > 0 or (summary.get("f24_pages") or 0) > 0,
            "pagine": summary.get("pages_total"),
            "buste_pagine": summary.get("buste_pages", 0),
            "f24_pagine": summary.get("f24_pages", 0),
            "buste_uniche": summary.get("buste_uniche", 0),
            "hint": diag.get("hint") or "",
            "pdf_cifrato": diag.get("is_encrypted"),
            "password_ok": diag.get("decrypt_with_configured_passwords"),
        }
    )
    if not step_a["leggibile"] and not step_a["errore"]:
        step_a["errore"] = (
            diag.get("hint")
            or "Nessuna pagina classificata come busta o F24: PDF senza testo utile o layout non riconosciuto."
        )

    from anagrafiche.models import Dipendente

    for row in preview_data.get("rows") or []:
        action = row.get("action") or ""
        dip_id = row.get("dipendente_id")
        dip_label = row.get("dipendente_nome") or "—"
        if dip_id:
            d = Dipendente.objects.filter(id=dip_id, azienda=azienda).first()
            if d:
                dip_label = f"{d.cognome} {d.nome} (#{d.id})"
        periodo = row.get("periodo") or "—"
        mese_s, anno_s = "??", "????"
        m = re.match(r"^(\d{2})/(\d{4})$", periodo)
        if m:
            mese_s, anno_s = m.group(1), m.group(2)
        natura = (row.get("natura_busta") or preview_data.get("natura_busta_file") or "ORDINARIA").upper()
        esempio_file = f"buste_paghe/busta_{mese_s}_{anno_s}_dip_{dip_id or 'X'}_p{row.get('page')}.pdf"
        step_b["righe"].append(
            {
                "pagina": row.get("page"),
                "periodo": periodo,
                "cf": row.get("cf") or "—",
                "nome_estratto": row.get("full_name") or "—",
                "azione": action,
                "dipendente": dip_label,
                "importabile": action in {"match_cf", "match_name", "create", "already_present"},
            }
        )
        if action in {"match_cf", "match_name", "create"} and len(step_c["esempi_file"]) < 8:
            step_c["esempi_file"].append(
                {
                    "periodo": periodo,
                    "path_rel": esempio_file,
                    "path_abs": str(Path(paths["media_root"]) / esempio_file),
                    "descrizione_db": f"Busta paga {periodo} (import PDF)",
                }
            )

    for fp in preview_data.get("f24_pages") or []:
        if len(step_c["esempi_file"]) >= 12:
            break
        pm = fp.get("period_month") or "00"
        py = fp.get("period_year") or "0000"
        f24_name = f"f24/f24_{pm}_{py}_azienda_{azienda.id}.pdf"
        step_c["esempi_file"].append(
            {
                "periodo": f"{pm}/{py}",
                "path_rel": f24_name,
                "path_abs": str(Path(paths["media_root"]) / f24_name),
                "descrizione_db": f"F24 {pm}/{py} (import PDF)",
            }
        )

    step_b["summary"] = summary
    return {"step_a": step_a, "step_b": step_b, "step_c": step_c, "preview": preview_data}


def riepilogo_verifica(righe: list[dict[str, Any]]) -> dict[str, int]:
    """Conteggi per stato verifica."""
    counts: dict[str, int] = {}
    for r in righe:
        k = r.get("stato") or "?"
        counts[k] = counts.get(k, 0) + 1
    return counts
