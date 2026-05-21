"""
Riallineamento massivo archivio buste: periodo «Mese Retribuito» + netto/lordo da PDF (pdfplumber/v4).

  python manage.py riallinea_buste_archivio --azienda-id 1
  python manage.py riallinea_buste_archivio --azienda-id 1 --dry-run
  python manage.py riallinea_buste_archivio --azienda-id 1 --anno 2026 --limit 10 -v 2
  python manage.py riallinea_buste_archivio --tutte-aziende
"""

from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError

from anagrafiche.models import Azienda
from documenti.models import Documento
from documenti.riallinea_buste_archivio import esegui_riallineamento_massivo


def _aziende_con_buste():
    return Azienda.objects.filter(
        id__in=Documento.objects.filter(tipo="busta_paga")
        .exclude(file="")
        .values_list("azienda_id", flat=True)
        .distinct()
    ).order_by("nome")


class Command(BaseCommand):
    help = (
        "Rilegge ogni busta in archivio e allinea MovimentoImportPaghe "
        "(periodo, netto, lordo) e CedolinoMotoreV4 dove possibile."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--azienda-id",
            type=int,
            default=0,
            help="ID azienda (0 = richiede --tutte-aziende)",
        )
        parser.add_argument(
            "--tutte-aziende",
            action="store_true",
            help="Elabora tutte le aziende con buste in archivio",
        )
        parser.add_argument("--anno", type=int, default=0, help="Filtra per anno (0 = tutti)")
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Simula senza scrivere sul database",
        )
        parser.add_argument(
            "--no-v4",
            action="store_true",
            help="Non aggiornare CedolinoMotoreV4",
        )
        parser.add_argument(
            "--no-forza",
            action="store_true",
            help="Non sovrascrivere valori già presenti (solo campi vuoti)",
        )
        parser.add_argument("--limit", type=int, default=0, help="Max documenti per azienda (0=tutti)")

    def handle(self, *args, **options):
        tutte = bool(options["tutte_aziende"])
        aid = int(options["azienda_id"] or 0)
        anno = int(options["anno"] or 0) or None
        dry = bool(options["dry_run"])
        forza = not bool(options["no_forza"])
        persisti_v4 = not bool(options["no_v4"])
        limit = int(options["limit"] or 0)
        verbosity = int(options["verbosity"] or 1)

        if tutte:
            aziende = list(_aziende_con_buste())
            if not aziende:
                raise CommandError("Nessuna azienda con buste in archivio.")
        elif aid:
            az = Azienda.objects.filter(pk=aid).first()
            if not az:
                raise CommandError(f"Azienda id={aid} non trovata.")
            aziende = [az]
        else:
            raise CommandError("Specificare --azienda-id N o --tutte-aziende.")

        tot_riep = None
        for az in aziende:
            self.stdout.write(self.style.MIGRATE_HEADING(f"=== {az.nome} (id {az.pk}) ==="))
            riep = esegui_riallineamento_massivo(
                azienda_id=az.pk,
                anno=anno,
                forza=forza,
                persisti_v4=persisti_v4,
                dry_run=dry,
                limit=limit,
                verbosity=verbosity,
            )
            self._stampa_riepilogo(riep, dry)
            if tot_riep is None:
                tot_riep = riep
            else:
                tot_riep.totale += riep.totale
                tot_riep.ok += riep.ok
                tot_riep.errori += riep.errori
                tot_riep.senza_periodo += riep.senza_periodo
                tot_riep.senza_pdf += riep.senza_pdf
                tot_riep.movimenti_creati += riep.movimenti_creati
                tot_riep.movimenti_aggiornati += riep.movimenti_aggiornati
                tot_riep.v4_aggiornati += riep.v4_aggiornati

        if len(aziende) > 1 and tot_riep is not None:
            self.stdout.write(self.style.MIGRATE_HEADING("=== Totale complessivo ==="))
            self._stampa_riepilogo(tot_riep, dry)

    def _stampa_riepilogo(self, riep, dry: bool) -> None:
        self.stdout.write(
            f"Documenti in elenco: {riep.totale} | OK: {riep.ok} | errori: {riep.errori} | "
            f"senza periodo: {riep.senza_periodo} | senza PDF: {riep.senza_pdf}"
        )
        if not dry:
            self.stdout.write(
                self.style.SUCCESS(
                    f"Movimenti creati: {riep.movimenti_creati} | "
                    f"aggiornati: {riep.movimenti_aggiornati} | "
                    f"CedolinoMotoreV4: {riep.v4_aggiornati}"
                )
            )
        else:
            self.stdout.write(self.style.WARNING("DRY-RUN: nessuna modifica al database."))
        if riep.dettaglio_errori:
            self.stdout.write("Errori (prime righe):")
            for line in riep.dettaglio_errori[:50]:
                self.stdout.write(f"  {line}")
