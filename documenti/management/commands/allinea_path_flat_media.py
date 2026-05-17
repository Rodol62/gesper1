from __future__ import annotations

from django.core.management.base import BaseCommand
from django.db.models import F
from django.db.models.functions import Substr

from documenti.models import Documento

PREFIX = "documenti/"


class Command(BaseCommand):
    help = (
        "Aggiorna Documento.file togliendo il prefisso documenti/ (ordine: prima spostare i file "
        "con deploy/migrate_media_layout_flat.sh o equivalente, poi eseguire questo comando con --applica)."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--applica",
            action="store_true",
            help="Esegue l'update sul database (default: conteggio e anteprima).",
        )
        parser.add_argument(
            "--limite",
            type=int,
            default=0,
            help="Max righe da aggiornare (0 = tutte le righe con prefisso documenti/).",
        )

    def handle(self, *args, **options):
        apply_mode = bool(options.get("applica"))
        limite = int(options.get("limite") or 0)

        qs = Documento.objects.exclude(file="").filter(file__startswith=PREFIX).order_by("id")
        tot = qs.count()
        self.stdout.write(f"Record con file che inizia con {PREFIX!r}: {tot}")
        if tot == 0:
            return

        if limite > 0:
            qs = qs[:limite]

        if not apply_mode:
            for doc_id, name in qs.values_list("id", "file")[:20]:
                new_name = (name or "")[len(PREFIX) :]
                self.stdout.write(f"  [DRY] id={doc_id} {name!r} -> {new_name!r}")
            show = min(20, tot if limite == 0 else min(tot, limite))
            if (limite or tot) > show:
                self.stdout.write("  ...")
            self.stdout.write(self.style.WARNING("Esegui con --applica per aggiornare il database."))
            return

        # Substr posizione 1-based: primo carattere dopo documenti/ è 11
        start = len(PREFIX) + 1
        to_run = Documento.objects.filter(file__startswith=PREFIX)
        if limite > 0:
            ids = list(to_run.values_list("pk", flat=True)[:limite])
            to_run = Documento.objects.filter(pk__in=ids)

        n = to_run.update(file=Substr(F("file"), start))
        self.stdout.write(
            self.style.SUCCESS(f"Aggiornate {n} righe: rimosso prefisso {PREFIX!r} da Documento.file.")
        )
