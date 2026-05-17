"""
Genera ZIP con estrazione cedolino (motore Claude) per tutte le buste paga di un anno.

  python manage.py leggi_buste_anno_cedolino --anno 2025 --azienda-id 1 --zip /tmp/cedolini_2025.zip

Criterio anno: come la dashboard buste (MovimentoImportPaghe BUSTA + documenti con periodo da descrizione).
"""

from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from anagrafiche.models import Azienda

from documenti.buste_cedolino_batch import (
    build_cedolini_zip_bytes,
    documento_ids_busta_per_anno,
)


class Command(BaseCommand):
    help = "ZIP HTML cedolino per tutte le buste paga di un anno (stesso motore della prova lettura)."

    def add_arguments(self, parser):
        parser.add_argument("--anno", type=int, required=True, help="Anno (es. 2025)")
        parser.add_argument(
            "--azienda-id",
            type=int,
            required=True,
            help="ID azienda (anagrafiche.Azienda)",
        )
        parser.add_argument(
            "--zip",
            metavar="PATH",
            required=True,
            help="Percorso file ZIP di output",
        )

    def handle(self, *args, **options):
        anno = int(options["anno"])
        aid = int(options["azienda_id"])
        out_path = Path(options["zip"]).expanduser().resolve()

        azienda = Azienda.objects.filter(pk=aid).first()
        if not azienda:
            raise CommandError(f"Azienda id={aid} non trovata.")

        n_ids = len(documento_ids_busta_per_anno(azienda, anno))
        if n_ids == 0:
            raise CommandError(
                f"Nessun documento busta per anno {anno} e azienda {azienda.nome}."
            )

        self.stdout.write(
            f"Elaborazione {n_ids} documenti (anno {anno}, {azienda.nome})…"
        )
        buf, _rows, n_ok, n_err = build_cedolini_zip_bytes(azienda, anno)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_bytes(buf.getvalue())
        self.stdout.write(
            self.style.SUCCESS(
                f"Scritto {out_path} — OK: {n_ok}, errori/non PDF: {n_err}"
            )
        )
