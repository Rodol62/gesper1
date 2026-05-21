"""
Corregge movimenti BUSTA con mese/anno DB diversi dal periodo del documento/PDF.

Caso tipico: busta gennaio 2026 (file ``busta_01_2026_...``) etichettata come 04/2026,
che duplica aprile in archivio e partitario.

  python manage.py correggi_buste_periodo_smarrito --azienda-id 1
  python manage.py correggi_buste_periodo_smarrito --azienda-id 1 --dry-run
  python manage.py correggi_buste_periodo_smarrito --azienda-id 1 --anno 2026 --mese 4
"""

from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from accounts.models import MovimentoImportPaghe
from anagrafiche.models import Azienda
from documenti.buste_cedolino_batch import parse_periodo_busta, periodo_retributivo_effettivo
from documenti.busta_acquisizione import acquisisci_busta_da_documento, leggi_pdf_busta_documento
from documenti.models import Documento


def _periodo_atteso_da_documento(doc: Documento) -> tuple[int | None, int | None]:
    raw = leggi_pdf_busta_documento(doc)
    if raw and len(raw) >= 5 and raw[:5] == b"%PDF-":
        res = acquisisci_busta_da_documento(doc, raw_pdf=raw)
        if not res.errore:
            return periodo_retributivo_effettivo(doc, res.report)
    return parse_periodo_busta(doc)


class Command(BaseCommand):
    help = "Allinea mese/anno dei movimenti BUSTA al periodo reale del documento collegato."

    def add_arguments(self, parser):
        parser.add_argument("--azienda-id", type=int, required=True)
        parser.add_argument("--anno", type=int, default=0, help="Filtra movimenti per anno DB (0=tutti)")
        parser.add_argument("--mese", type=int, default=0, help="Filtra movimenti per mese DB (0=tutti)")
        parser.add_argument("--dry-run", action="store_true")

    @transaction.atomic
    def handle(self, *args, **options):
        aid = int(options["azienda_id"])
        az = Azienda.objects.filter(pk=aid).first()
        if not az:
            raise CommandError(f"Azienda id={aid} non trovata.")

        anno_f = int(options["anno"] or 0) or None
        mese_f = int(options["mese"] or 0) or None
        dry = bool(options["dry_run"])

        qs = MovimentoImportPaghe.objects.filter(
            azienda=az,
            tipo="BUSTA",
            documento_id__isnull=False,
        ).select_related("documento", "dipendente")
        if anno_f:
            qs = qs.filter(anno=anno_f)
        if mese_f:
            qs = qs.filter(mese=mese_f)

        corretti = 0
        conflitti = 0
        ok_gia = 0

        for mov in qs.order_by("id"):
            doc = mov.documento
            if not doc:
                continue
            pm, pa = _periodo_atteso_da_documento(doc)
            if not pm or not pa:
                continue
            if int(mov.mese) == int(pm) and int(mov.anno) == int(pa):
                ok_gia += 1
                continue

            dup = MovimentoImportPaghe.objects.filter(
                azienda=az,
                dipendente_id=mov.dipendente_id,
                tipo="BUSTA",
                anno=pa,
                mese=pm,
                natura_busta=mov.natura_busta,
            ).exclude(pk=mov.pk).first()
            if dup:
                conflitti += 1
                self.stdout.write(
                    self.style.WARNING(
                        f"mov {mov.pk} ({mov.dipendente}): {mov.mese:02d}/{mov.anno} -> {pm:02d}/{pa} "
                        f"ma esiste già mov {dup.pk} — verifica manualmente"
                    )
                )
                continue

            self.stdout.write(
                f"mov {mov.pk} {mov.dipendente}: {mov.mese:02d}/{mov.anno} -> {pm:02d}/{pa} "
                f"doc={doc.pk} {doc.file.name}"
            )
            if not dry:
                mov.mese = int(pm)
                mov.anno = int(pa)
                mov.periodo_label = f"{int(pm):02d}/{int(pa)}"
                mov.save(update_fields=["mese", "anno", "periodo_label", "updated_at"])
            corretti += 1

        self.stdout.write(
            self.style.SUCCESS(
                f"Già allineati: {ok_gia} | corretti: {corretti} | conflitti: {conflitti}"
                + (" (DRY-RUN)" if dry else "")
            )
        )
