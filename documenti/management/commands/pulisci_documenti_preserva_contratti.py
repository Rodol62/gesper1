"""
Rimuove documenti e file su disco lasciando intatti contratti e proposte contrattuali.

- **Database**: elimina tutti i ``Documento`` tranne ``tipo='contratto'`` (opz. filtro azienda).
- **Disco**: elimina i file collegati a quei record; poi rimuove PDF «orfani» sotto
  ``documenti/`` (tranne ``documenti/contratti/``), cartelle legacy paghe/F24 in radice
  media, ecc., solo se il path **non** è ancora referenziato da alcun ``Documento`` né
  da ``RapportoDiLavoro`` / ``PropostaAssunzione`` (PDF contratto, proposta, mansionario).

Non elimina mai nulla sotto il prefisso ``contratti/`` (proposte, pdf firmati, mansionari).

Esempi::

    python manage.py pulisci_documenti_preserva_contratti --dry-run
    python manage.py pulisci_documenti_preserva_contratti --dry-run --azienda-id=1
    python manage.py pulisci_documenti_preserva_contratti --apply --azienda-id=1
"""

from __future__ import annotations

from pathlib import Path

from django.conf import settings
from django.core.files.storage import default_storage
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from documenti.models import Documento
from documenti.upload_paths import subdir_for_documento_tipo


def _media_root() -> Path:
    return Path(settings.MEDIA_ROOT).resolve()


def _norm_rel(name: str | None) -> str:
    if not name:
        return ""
    return str(name).replace("\\", "/").strip().lstrip("/")


def _protected_prefixes() -> tuple[str, ...]:
    contr_doc = _norm_rel(subdir_for_documento_tipo("contratto")).lower().rstrip("/") + "/"
    return ("contratti/", contr_doc)


def _is_protected(rel: str) -> bool:
    r = _norm_rel(rel).lower()
    if not r:
        return True
    for pref in _protected_prefixes():
        p = pref.lower()
        base = p.rstrip("/")
        if r == base or r.startswith(base + "/"):
            return True
    return False


def _referenced_media_paths() -> set[str]:
    """Path relativi a MEDIA_ROOT ancora usati da modelli che non vanno toccati."""
    out: set[str] = set()
    for name in Documento.objects.exclude(file="").values_list("file", flat=True):
        x = _norm_rel(name)
        if x:
            out.add(x)
    try:
        from rapporto_di_lavoro.models import PropostaAssunzione, RapportoDiLavoro
    except Exception:
        return out

    for rdl in RapportoDiLavoro.objects.only(
        "mansionario_file", "file_contratto_pdf", "file_proposta"
    ).iterator(chunk_size=500):
        for attr in ("mansionario_file", "file_contratto_pdf", "file_proposta"):
            f = getattr(rdl, attr, None)
            if f and getattr(f, "name", None):
                out.add(_norm_rel(f.name))
    for pa in PropostaAssunzione.objects.only("mansionario_file").iterator(chunk_size=500):
        f = pa.mansionario_file
        if f and getattr(f, "name", None):
            out.add(_norm_rel(f.name))
    return out


def _doc_subdir() -> str:
    s = (getattr(settings, "DOCUMENTI_MEDIA_SUBDIR", "documenti") or "documenti").strip().strip("/")
    return s or "documenti"


def _iter_candidate_files(media: Path, exts: frozenset[str]):
    doc_sub = _doc_subdir()
    doc_root = media / doc_sub
    if doc_root.is_dir():
        for f in doc_root.rglob("*"):
            if not f.is_file() or f.suffix.lower() not in exts:
                continue
            try:
                rel = f.relative_to(media).as_posix()
            except ValueError:
                continue
            if _is_protected(rel):
                continue
            yield rel

    for legacy in ("F24", "CUD", "Liquidazioni_mensili", "buste_paghe"):
        root = media / legacy
        if not root.is_dir():
            continue
        for f in root.rglob("*"):
            if not f.is_file() or f.suffix.lower() not in exts:
                continue
            try:
                rel = f.relative_to(media).as_posix()
            except ValueError:
                continue
            if _is_protected(rel):
                continue
            yield rel


class Command(BaseCommand):
    help = (
        "Elimina Documento (tranne tipo=contratto) e file correlati/orfani su MEDIA, "
        "preservando contratti e proposte (cartelle contratti/ e documenti/contratti, "
        "file su RapportoDiLavoro / PropostaAssunzione)."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Solo conteggi, nessuna modifica.",
        )
        parser.add_argument(
            "--apply",
            action="store_true",
            help="Esegue cancellazioni su DB e su disco.",
        )
        parser.add_argument(
            "--azienda-id",
            type=int,
            default=None,
            help="Limita l'eliminazione dei Documento (non-contratto) a questa azienda.",
        )
        parser.add_argument(
            "--estensioni",
            type=str,
            default="pdf",
            help="Per la pulizia orfani su disco: estensioni separate da virgola (default: pdf).",
        )

    def handle(self, *args, **options):
        dry = bool(options.get("dry_run"))
        apply_mode = bool(options.get("apply"))
        if dry and apply_mode:
            raise CommandError("Specificare solo uno tra --dry-run e --apply.")
        if not dry and not apply_mode:
            raise CommandError("Obbligatorio: --dry-run oppure --apply.")

        azienda_id = options.get("azienda_id")
        ext_csv = (options.get("estensioni") or "").strip()
        exts = frozenset(
            ("." + x.strip().lower().lstrip(".")) for x in ext_csv.split(",") if x.strip()
        ) or frozenset({".pdf"})

        media = _media_root()
        if not media.is_dir():
            raise CommandError(f"MEDIA_ROOT non è una cartella: {media}")

        doc_qs = Documento.objects.exclude(tipo="contratto")
        if azienda_id is not None:
            doc_qs = doc_qs.filter(azienda_id=azienda_id)
        n_doc = doc_qs.count()

        ref_before = _referenced_media_paths()
        orphan_candidates: list[str] = []
        for rel in sorted(set(_iter_candidate_files(media, exts))):
            if rel in ref_before:
                continue
            if _is_protected(rel):
                continue
            orphan_candidates.append(rel)
        n_orphan = len(orphan_candidates)

        if dry:
            self.stdout.write(
                self.style.WARNING(
                    "DRY RUN — nessuna modifica.\n"
                    f"  Documento da eliminare (tipo ≠ contratto): {n_doc}\n"
                    f"  File orfani su disco da rimuovere: {n_orphan}\n"
                    f"  Prefissi protetti: {', '.join(_protected_prefixes())}"
                )
            )
            return

        paths_storage: list[str] = []
        deleted_docs = 0
        pks = list(doc_qs.values_list("pk", flat=True))

        with transaction.atomic():
            for pk in pks:
                try:
                    doc = Documento.objects.get(pk=pk)
                except Documento.DoesNotExist:
                    continue
                if doc.file and doc.file.name:
                    rel = _norm_rel(doc.file.name)
                    if rel and not _is_protected(rel):
                        paths_storage.append(doc.file.name)
                doc.delete()
                deleted_docs += 1

        self.stdout.write(
            self.style.SUCCESS(f"Eliminati {deleted_docs} Documento (tipi diversi da contratto).")
        )

        ref_after = _referenced_media_paths()
        removed_orphan = 0
        for rel in orphan_candidates:
            if rel in ref_after:
                continue
            if _is_protected(rel):
                continue
            try:
                if default_storage.exists(rel):
                    default_storage.delete(rel)
                    removed_orphan += 1
            except Exception as exc:
                self.stderr.write(self.style.WARNING(f"Orfano non rimosso {rel!r}: {exc}"))

        for path in dict.fromkeys(paths_storage):
            if _is_protected(path):
                continue
            try:
                if default_storage.exists(path):
                    default_storage.delete(path)
            except Exception as exc:
                self.stderr.write(self.style.WARNING(f"File non rimosso {path!r}: {exc}"))

        self.stdout.write(
            self.style.SUCCESS(
                f"Completato: rimossi {removed_orphan} file orfani su disco (oltre ai file dei documenti eliminati)."
            )
        )
        self.stdout.write(
            "Contratti (Documento tipo=contratto), cartella contratti/ e documenti/contratti/ non sono stati toccati."
        )
