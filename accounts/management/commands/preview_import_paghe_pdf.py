from __future__ import annotations

import json
import subprocess
import re
import sys
from collections import defaultdict
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from anagrafiche.models import Azienda, Dipendente
from accounts.models import MovimentoImportPaghe
from documenti.models import Documento
from documenti.buste_pdf_passwords import passwords_for_busta_pdf_read


MONTHS_ITA = {
    'GENNAIO': 1,
    'FEBBRAIO': 2,
    'MARZO': 3,
    'APRILE': 4,
    'MAGGIO': 5,
    'GIUGNO': 6,
    'LUGLIO': 7,
    'AGOSTO': 8,
    'SETTEMBRE': 9,
    'OTTOBRE': 10,
    'NOVEMBRE': 11,
    'DICEMBRE': 12,
}


def _infer_period_from_filename(filename: str):
    """Fallback periodo da nome file PDF (es. 'FEBBRAIO 2024.pdf')."""
    name_up = (filename or '').upper()

    m_num = re.search(r'\b(0?[1-9]|1[0-2])\s*[\/_\-]\s*(20\d{2})\b', name_up)
    if m_num:
        return int(m_num.group(1)), int(m_num.group(2))

    m_year = re.search(r'\b(20\d{2})\b', name_up)
    year = int(m_year.group(1)) if m_year else None
    if year:
        for nome, mese in MONTHS_ITA.items():
            if nome in name_up:
                return mese, year

    return None, None


def _infer_natura_busta_from_source(filename: str) -> str:
    up = (filename or '').upper()
    if 'TREDICESIMA' in up:
        return 'TREDICESIMA'
    if 'QUATTORDICESIMA' in up:
        return 'QUATTORDICESIMA'
    return 'ORDINARIA'


def _python_for_subprocess(project_root: Path) -> str:
    """
    Interprete Python per ``scripts/analizza_pdf_paghe.py``.
    Usa il venv del processo Django (Hetzner: ``venv/``, locale spesso ``.venv/``).
    """
    candidates = [
        sys.executable,
        project_root / "venv" / "bin" / "python",
        project_root / ".venv" / "bin" / "python",
    ]
    for c in candidates:
        p = Path(c) if not isinstance(c, str) else Path(c)
        if p.is_file() and p.exists():
            return str(p.resolve())
    return sys.executable


class Command(BaseCommand):
    help = (
        "Anteprima import PDF paghe unico (buste + F24): "
        "estrae record, deduplica e propone match dipendenti senza modificare il DB."
    )

    def add_arguments(self, parser):
        parser.add_argument("pdf", type=str, help="Percorso PDF da analizzare")
        parser.add_argument("--azienda-id", type=int, required=True, help="ID azienda target")
        parser.add_argument(
            "--source-name",
            type=str,
            default="",
            help="Nome file sorgente originale (usato come fallback per inferire il periodo)",
        )
        parser.add_argument(
            "--out",
            type=str,
            default="",
            help="Percorso output JSON (default: snapshots/preview_import_<nomefile>.json)",
        )
        parser.add_argument(
            "--allow-replace",
            action="store_true",
            help="Non marcare «già presente»: utile per re-import / sovrascrittura buste e PDF.",
        )

    def handle(self, *args, **options):
        pdf_path = Path(options["pdf"]).expanduser().resolve()
        if not pdf_path.exists():
            raise CommandError(f"File non trovato: {pdf_path}")

        try:
            azienda = Azienda.objects.get(id=options["azienda_id"])
        except Azienda.DoesNotExist as exc:
            raise CommandError(f"Azienda non trovata con id={options['azienda_id']}") from exc

        allow_replace = bool(options.get("allow_replace"))

        project_root = Path(__file__).resolve().parents[3]
        script_path = project_root / "scripts" / "analizza_pdf_paghe.py"
        if not script_path.exists():
            raise CommandError(f"Script analisi non trovato: {script_path}")

        tmp_out = Path("/tmp") / f"gesper_preview_{pdf_path.stem.replace(' ', '_')}.json"
        py_bin = _python_for_subprocess(project_root)
        cmd = [
            py_bin,
            str(script_path),
            str(pdf_path),
            "--out",
            str(tmp_out),
        ]
        try:
            subprocess.run(cmd, check=True, capture_output=True, text=True)
        except FileNotFoundError as exc:
            raise CommandError(
                f"Interprete Python non trovato per analisi PDF (provato: {py_bin}). "
                "Verificare venv sul server."
            ) from exc
        except subprocess.CalledProcessError as exc:
            detail = (exc.stderr or exc.stdout or "").strip()
            raise CommandError(
                f"Errore analisi PDF ({script_path.name}): {detail[:2000] or exc}"
            ) from exc

        report = json.loads(tmp_out.read_text(encoding="utf-8"))
        diagnostics = {
            "pdf_pages": None,
            "is_encrypted": None,
            "decrypt_with_configured_passwords": None,
            "hint": "",
        }
        try:
            from pypdf import PdfReader

            reader = PdfReader(str(pdf_path))
            diagnostics["pdf_pages"] = len(reader.pages)
            diagnostics["is_encrypted"] = bool(getattr(reader, "is_encrypted", False))
            if diagnostics["is_encrypted"]:
                ok = False
                for pwd in passwords_for_busta_pdf_read():
                    try:
                        if reader.decrypt(pwd):
                            ok = True
                            break
                    except Exception:
                        continue
                diagnostics["decrypt_with_configured_passwords"] = ok
            else:
                diagnostics["decrypt_with_configured_passwords"] = True
        except Exception:
            pass
        source_name = (options.get("source_name") or "").strip() or pdf_path.name
        natura_busta_file = _infer_natura_busta_from_source(source_name)
        fallback_mese, fallback_anno = _infer_period_from_filename(source_name)
        source_year_match = re.search(r"\b(20\d{2})\b", source_name.upper())
        source_year = int(source_year_match.group(1)) if source_year_match else None

        raw_records = report.get("records", [])
        buste = [r for r in raw_records if r.get("kind") == "BUSTA"]
        f24_rows = [r for r in raw_records if r.get("kind") == "F24"]
        f24_count = len(f24_rows)
        f24_importi = []
        for r in f24_rows:
            v = r.get("f24_importo")
            if v is not None:
                try:
                    f24_importi.append(float(v))
                except (TypeError, ValueError):
                    continue

        # deduplica per CF + periodo (fallback su nome)
        unique = {}
        duplicates = []
        for r in buste:
            key = (
                r.get("cf") or "",
                r.get("period_month"),
                r.get("period_year"),
                (r.get("full_name") or "").strip().upper(),
            )
            if key in unique:
                duplicates.append(r.get("page"))
            else:
                unique[key] = r

        unique_rows = list(unique.values())

        # mapping dipendenti azienda
        dips = Dipendente.objects.filter(azienda=azienda)
        by_cf = {((d.codice_fiscale or "").upper()): d for d in dips if d.codice_fiscale}

        by_name = defaultdict(list)
        for d in dips:
            nk = f"{(d.cognome or '').strip().upper()} {(d.nome or '').strip().upper()}".strip()
            if nk:
                by_name[nk].append(d)

        preview_rows = []
        to_create = 0
        matched = 0
        ambiguous = 0
        already_present = 0

        def _documento_ha_file_su_storage(doc: Documento | None) -> bool:
            if not doc or not doc.file:
                return False
            name = getattr(doc.file, "name", None)
            if not name:
                return False
            try:
                return bool(doc.file.storage.exists(name))
            except Exception:
                return False

        def _busta_gia_presente(dipendente, periodo_label, natura_busta='ORDINARIA'):
            """True solo se c'è ancora una busta «reale» (PDF su storage) per quel periodo/natura.

            - Considera **tutti** i MovimentoImportPaghe per la chiave (non solo ``.first()``: ordine
              non deterministico e duplicati storici potevano dare esito errato).
            - Se esiste almeno un movimento con documento e file su disco → già presente.
            - Se esistono movimenti ma **nessuno** ha PDF valido → **non** è già presente (re-import OK);
              in quel caso **non** si usa il fallback su ``Documento``: evita blocchi fantasma quando
              restano solo righe movimento orfane dopo pulizie parziali.
            - Fallback su ``Documento`` busta_paga solo se **non** c'è alcun movimento per quella chiave
              (dati legacy senza riga import).
            """
            if allow_replace:
                return False
            if not dipendente or not periodo_label:
                return False
            try:
                mese_s, anno_s = periodo_label.split('/')
                mese = int(mese_s)
                anno = int(anno_s)
            except Exception:
                return False

            mov_qs = (
                MovimentoImportPaghe.objects.filter(
                    azienda=azienda,
                    dipendente=dipendente,
                    tipo='BUSTA',
                    anno=anno,
                    mese=mese,
                    natura_busta=natura_busta,
                )
                .select_related("documento")
                .order_by("-pk")
            )
            mov_list = list(mov_qs)
            for mov in mov_list:
                if _documento_ha_file_su_storage(getattr(mov, "documento", None)):
                    return True
            if mov_list:
                # Movimenti solo orfani (DB/file): non bloccare; niente fallback Documento.
                return False

            # Fallback su documento busta_paga con stesso periodo in descrizione e file presente.
            if natura_busta != 'ORDINARIA':
                return False

            mese_nome = [
                '',
                'GENNAIO',
                'FEBBRAIO',
                'MARZO',
                'APRILE',
                'MAGGIO',
                'GIUGNO',
                'LUGLIO',
                'AGOSTO',
                'SETTEMBRE',
                'OTTOBRE',
                'NOVEMBRE',
                'DICEMBRE',
            ][mese]
            for doc in Documento.objects.filter(
                azienda=azienda,
                dipendente=dipendente,
                tipo='busta_paga',
            ).only("id", "descrizione", "file"):
                up = (doc.descrizione or "").upper()
                if not (
                    f"{mese:02d}/{anno}" in up
                    or f"{mese}/{anno}" in up
                    or f"{mese_nome} {anno}" in up
                ):
                    continue
                if _documento_ha_file_su_storage(doc):
                    return True
            return False

        for r in unique_rows:
            cf = (r.get("cf") or "").upper()
            full_name = (r.get("full_name") or "").strip()
            net = r.get("netto_busta")
            period_month = r.get("period_month")
            period_year = r.get("period_year")

            if (not period_month or not period_year) and fallback_mese and fallback_anno:
                period_month, period_year = fallback_mese, fallback_anno

            # Mensilità aggiuntive: se manca il periodo nel PDF, forzare mese canonico
            # dal nome file (13ª->12, 14ª->07) mantenendo anno del file quando disponibile.
            if not period_month or not period_year:
                if natura_busta_file == 'TREDICESIMA':
                    period_month = 12
                    period_year = period_year or source_year
                elif natura_busta_file == 'QUATTORDICESIMA':
                    period_month = 7
                    period_year = period_year or source_year

            periodo = f"{period_month:02d}/{period_year}" if period_month and period_year else "-"

            action = "create"
            dip_id = None
            dip_label = None

            if cf and cf in by_cf:
                d = by_cf[cf]
                action = "match_cf"
                dip_id = d.id
                dip_label = f"{d.cognome} {d.nome}".strip()
                matched += 1
            else:
                name_key = (full_name or "").upper()
                cands = by_name.get(name_key, []) if name_key else []
                if len(cands) == 1:
                    d = cands[0]
                    action = "match_name"
                    dip_id = d.id
                    dip_label = f"{d.cognome} {d.nome}".strip()
                    matched += 1
                elif len(cands) > 1:
                    action = "ambiguous"
                    ambiguous += 1
                else:
                    to_create += 1

            dip_obj = None
            if dip_id:
                dip_obj = by_cf.get(cf) if cf and cf in by_cf else Dipendente.objects.filter(id=dip_id, azienda=azienda).first()
            if dip_obj and action in {"match_cf", "match_name"} and _busta_gia_presente(dip_obj, periodo, natura_busta_file):
                action = "already_present"
                already_present += 1

            preview_rows.append(
                {
                    "page": r.get("page"),
                    "periodo": periodo,
                    "cf": cf or None,
                    "full_name": full_name or None,
                    "birth_date": r.get("birth_date"),
                    "lordo_busta": r.get("lordo_busta"),
                    "netto_busta": net,
                    "natura_busta": natura_busta_file,
                    "action": action,
                    "dipendente_id": dip_id,
                    "dipendente_nome": dip_label,
                }
            )

        out_path = Path(options["out"]) if options["out"] else Path("snapshots") / f"preview_import_{pdf_path.stem.replace(' ', '_')}.json"
        out_path.parent.mkdir(parents=True, exist_ok=True)

        output = {
            "pdf": str(pdf_path),
            "source_name": source_name,
            "natura_busta_file": natura_busta_file,
            "azienda": {"id": azienda.id, "nome": azienda.nome},
            "diagnostics": diagnostics,
            "f24_pages": [
                {
                    "page": x.get("page"),
                    "period_month": x.get("period_month"),
                    "period_year": x.get("period_year"),
                    "f24_importo": x.get("f24_importo"),
                }
                for x in f24_rows
            ],
            "summary": {
                "pages_total": report.get("pages_total"),
                "buste_pages": len(buste),
                "f24_pages": f24_count,
                "f24_importo_stimato": max(f24_importi) if f24_importi else None,
                "buste_uniche": len(unique_rows),
                "buste_duplicate_pages": duplicates,
                "matched": matched,
                "to_create": to_create,
                "ambiguous": ambiguous,
                "already_present": already_present,
            },
            "rows": preview_rows,
        }
        if (
            output["summary"]["buste_pages"] == 0
            and output["summary"]["f24_pages"] == 0
        ):
            if diagnostics.get("is_encrypted") and diagnostics.get("decrypt_with_configured_passwords") is False:
                output["diagnostics"]["hint"] = (
                    "PDF cifrato: nessuna password configurata ha funzionato. "
                    "Aggiungere la password corretta in configurazione sistema."
                )
            elif (diagnostics.get("pdf_pages") or 0) > 0:
                output["diagnostics"]["hint"] = (
                    "PDF con pagine presenti ma nessun testo riconosciuto "
                    "(possibile scansione degradata/OCR insufficiente)."
                )
        out_path.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")

        self.stdout.write(self.style.SUCCESS("Anteprima completata."))
        self.stdout.write(f"Report: {out_path}")
        self.stdout.write(
            f"Buste uniche={len(unique_rows)} | match={matched} | create={to_create} | ambiguous={ambiguous} | F24 pages={f24_count}"
        )
