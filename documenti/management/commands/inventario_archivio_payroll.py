"""
Inventario file buste paga, F24 e CUD sul filesystem (MEDIA_ROOT) rispetto ai record Documento.

Il purge elimina solo i PDF collegati a righe ``Documento``; i file copiati/importati
senza record restano su disco: questo comando li elenca e conta.

Esempi::

    python manage.py inventario_archivio_payroll
    python manage.py inventario_archivio_payroll --azienda-id=1
    python manage.py inventario_archivio_payroll --list-orphans --max-list=200
    python manage.py inventario_archivio_payroll --solo-buste-f24-cud
"""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from documenti.models import Documento
from documenti.upload_paths import busta_paga_file_path_prefixes, subdir_for_documento_tipo


def _media_root() -> Path:
    return Path(settings.MEDIA_ROOT).resolve()


def _doc_subdir() -> str:
    s = (getattr(settings, "DOCUMENTI_MEDIA_SUBDIR", "documenti") or "documenti").strip().strip("/")
    return s or "documenti"


def _payroll_subdirs_under_documenti() -> frozenset[str]:
    """Sottocartelle documenti/ considerate buste / F24 / CUD (nomi path relativi)."""
    buste = subdir_for_documento_tipo("busta_paga").strip("/").split("/")[-1]
    f24 = subdir_for_documento_tipo("altro").strip("/").split("/")[-1]
    cud = subdir_for_documento_tipo("certificato").strip("/").split("/")[-1]
    return frozenset({buste, f24, cud, "Liquidazioni_mensili"})


class Command(BaseCommand):
    help = (
        "Conta PDF (o estensioni indicate) sotto MEDIA_ROOT in documenti/ e cartelle legacy; "
        "confronta con Documento e segnala orfani (file senza record DB)."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--azienda-id",
            type=int,
            default=None,
            help="Se impostato, «in DB» = esiste Documento per quel file e questa azienda.",
        )
        parser.add_argument(
            "--estensioni",
            type=str,
            default="pdf",
            help="Estensioni separate da virgola (default: pdf).",
        )
        parser.add_argument(
            "--solo-buste-f24-cud",
            action="store_true",
            help=(
                "Sotto la cartella documenti/, considera solo sottocartelle buste_paghe, "
                "f24, cud e Liquidazioni_mensili (come da settings + legacy)."
            ),
        )
        parser.add_argument(
            "--list-orphans",
            action="store_true",
            help="Stampa i percorsi relativi dei file orfani (non in Documento).",
        )
        parser.add_argument(
            "--max-list",
            type=int,
            default=500,
            help="Massimo righe di orfani da stampare con --list-orphans.",
        )

    def handle(self, *args, **options):
        media = _media_root()
        if not media.is_dir():
            raise CommandError(f"MEDIA_ROOT non è una cartella: {media}")

        azienda_id = options.get("azienda_id")
        solo_payroll = bool(options.get("solo_buste_f24_cud"))
        list_orphans = bool(options.get("list_orphans"))
        max_list = max(0, int(options.get("max_list") or 0))

        ext_csv = (options.get("estensioni") or "").strip()
        exts = {("." + x.strip().lower().lstrip(".")) for x in ext_csv.split(",") if x.strip()}
        if not exts:
            exts = {".pdf"}

        doc_sub = _doc_subdir()
        payroll_leafs = _payroll_subdirs_under_documenti()

        files_rel: list[tuple[str, str]] = []
        # (rel_posix, bucket) bucket = etichetta per raggruppamento in output

        seen: set[str] = set()

        def push(rel: str, bucket: str) -> None:
            rel = rel.replace("\\", "/").strip().lstrip("/")
            if not rel or rel in seen:
                return
            seen.add(rel)
            files_rel.append((rel, bucket))

        # 1) Tutta la cartella documenti (default) — ricorsivo
        doc_root = media / doc_sub
        if doc_root.is_dir():
            for f in doc_root.rglob("*"):
                if not f.is_file():
                    continue
                if f.suffix.lower() not in exts:
                    continue
                try:
                    rel = f.relative_to(media).as_posix()
                except ValueError:
                    continue
                parts = rel.split("/")
                if parts[0] == doc_sub and solo_payroll:
                    if len(parts) < 3:
                        # file nella radice documenti/ non è sotto buste|f24|cud|Liquidazioni
                        continue
                    leaf = parts[1]
                    if leaf not in payroll_leafs:
                        continue
                # Raggruppa: sottocartella documenti/X oppure PDF sparsi in documenti/
                if parts[0] == doc_sub:
                    if len(parts) >= 3:
                        bucket = f"{doc_sub}/{parts[1]}"
                    else:
                        bucket = f"{doc_sub}/(radice)"
                else:
                    bucket = f"{doc_sub}/…"
                push(rel, bucket)

        # 2) Legacy alla radice di MEDIA (non dentro documenti/)
        for legacy in ("F24", "CUD", "Liquidazioni_mensili", "buste_paghe"):
            root = media / legacy
            if not root.is_dir():
                continue
            try:
                if root.resolve() == doc_root.resolve() or str(root.resolve()).startswith(
                    str(doc_root.resolve()) + "/"
                ):
                    continue
            except OSError:
                pass
            for f in root.rglob("*"):
                if not f.is_file():
                    continue
                if f.suffix.lower() not in exts:
                    continue
                try:
                    rel = f.relative_to(media).as_posix()
                except ValueError:
                    continue
                push(rel, legacy + "/")

        if not files_rel:
            self.stdout.write(self.style.WARNING(f"Nessun file trovato sotto {media} con estensioni {exts}."))
            return

        rels = [r for r, _ in files_rel]
        in_db: set[str] = set()
        chunk = 800
        for i in range(0, len(rels), chunk):
            batch = rels[i : i + chunk]
            if azienda_id is not None:
                qs = Documento.objects.filter(azienda_id=azienda_id, file__in=batch)
            else:
                qs = Documento.objects.filter(file__in=batch)
            in_db.update(row for row in qs.values_list("file", flat=True) if row)

        by_bucket: dict[str, dict[str, int]] = defaultdict(lambda: {"files": 0, "in_db": 0, "orphan": 0})
        orphans: list[str] = []
        for rel, bucket in files_rel:
            by_bucket[bucket]["files"] += 1
            if rel in in_db:
                by_bucket[bucket]["in_db"] += 1
            else:
                by_bucket[bucket]["orphan"] += 1
                orphans.append(rel)

        self.stdout.write(self.style.NOTICE(f"MEDIA_ROOT = {media}"))
        self.stdout.write(f"Estensioni: {sorted(exts)}")
        if azienda_id is not None:
            self.stdout.write(f"Match DB: Documento con azienda_id={azienda_id}")
        else:
            self.stdout.write("Match DB: Documento (qualsiasi azienda)")
        if solo_payroll:
            self.stdout.write(
                f"Filtro --solo-buste-f24-cud: solo sottocartelle {sorted(payroll_leafs)} sotto {doc_sub}/"
            )
        self.stdout.write("")

        total_f = total_db = total_o = 0
        for bucket in sorted(by_bucket.keys()):
            st = by_bucket[bucket]
            total_f += st["files"]
            total_db += st["in_db"]
            total_o += st["orphan"]
            self.stdout.write(
                f"  [{bucket}] file={st['files']} in_db={st['in_db']} orfani={st['orphan']}"
            )

        self.stdout.write("")
        self.stdout.write(
            self.style.SUCCESS(
                f"TOTALE file={total_f} con record Documento={total_db} orfani={total_o}"
            )
        )

        if total_o and list_orphans:
            self.stdout.write(self.style.WARNING(f"Orfani (max {max_list}):"))
            for rel in orphans[:max_list]:
                self.stdout.write(f"  {rel}")
            if len(orphans) > max_list:
                self.stdout.write(self.style.WARNING(f"  … altri {len(orphans) - max_list} non mostrati"))

        if not solo_payroll and total_o:
            self.stdout.write("")
            self.stdout.write(
                "Suggerimento: per un elenco solo buste/F24/CUD sotto documenti/, "
                "rieseguire con --solo-buste-f24-cud."
            )

        # Riferimento cartelle configurate
        self.stdout.write("")
        self.stdout.write("Prefissi busta_paga (import/lista) in upload_paths:")
        for p in busta_paga_file_path_prefixes():
            self.stdout.write(f"  {p}")
