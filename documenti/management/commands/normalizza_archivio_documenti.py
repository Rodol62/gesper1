from __future__ import annotations

import os

from django.core.files.base import ContentFile
from django.core.management.base import BaseCommand

from documenti.models import Documento
from documenti.upload_paths import subdir_for_documento_tipo


class Command(BaseCommand):
    help = (
        "Normalizza i path file Documento secondo la mappa tipo->cartella "
        "(settings.DOCUMENTO_TIPO_MEDIA_SUBDIRS)."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--applica",
            action="store_true",
            help="Esegue copia file + update DB (default: dry-run).",
        )
        parser.add_argument(
            "--elimina-sorgente",
            action="store_true",
            help="Dopo copia riuscita elimina il file sorgente legacy.",
        )
        parser.add_argument(
            "--solo-tipo",
            type=str,
            default="",
            help="Normalizza solo un tipo Documento (es. busta_paga).",
        )

    def handle(self, *args, **options):
        apply_mode = bool(options.get("applica"))
        delete_source = bool(options.get("elimina_sorgente"))
        only_tipo = (options.get("solo_tipo") or "").strip()

        qs = Documento.objects.exclude(file="").order_by("id")
        if only_tipo:
            qs = qs.filter(tipo=only_tipo)

        scanned = 0
        already_ok = 0
        candidates = 0
        moved = 0
        errors = 0

        for doc in qs.iterator(chunk_size=300):
            scanned += 1
            if not doc.file:
                continue

            current = (doc.file.name or "").strip()
            if not current:
                continue

            target_subdir = subdir_for_documento_tipo(doc.tipo).strip("/")
            base = os.path.basename(current)
            if not base:
                continue

            desired = f"{target_subdir}/{base}"
            if current == desired:
                already_ok += 1
                continue

            candidates += 1
            self.stdout.write(f"{'[APPLY]' if apply_mode else '[DRY]'} doc={doc.id} {current} -> {desired}")

            if not apply_mode:
                continue

            try:
                if not doc.file.storage.exists(current):
                    self.stdout.write(self.style.WARNING(f"  SKIP doc={doc.id}: file sorgente non trovato"))
                    continue

                final_target = desired
                if doc.file.storage.exists(final_target):
                    root, ext = os.path.splitext(base)
                    final_target = f"{target_subdir}/{root}_{doc.id}{ext}"

                with doc.file.storage.open(current, "rb") as fh:
                    raw = fh.read()
                doc.file.storage.save(final_target, ContentFile(raw))

                old_name = current
                doc.file.name = final_target
                doc.save(update_fields=["file"])
                moved += 1

                if delete_source and old_name != final_target:
                    try:
                        if doc.file.storage.exists(old_name):
                            doc.file.storage.delete(old_name)
                    except Exception:
                        # Non bloccare la normalizzazione per una delete fallita.
                        pass
            except Exception as exc:
                errors += 1
                self.stdout.write(self.style.ERROR(f"  ERROR doc={doc.id}: {exc}"))

        self.stdout.write(
            self.style.SUCCESS(
                "Normalizzazione archivio completata: "
                f"scansionati={scanned}, gia_ok={already_ok}, candidati={candidates}, "
                f"migrati={moved}, errori={errors}, apply={apply_mode}"
            )
        )

