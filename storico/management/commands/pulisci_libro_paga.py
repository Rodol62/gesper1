"""
Rimuove le voci ``LibroPagaStorico`` (Libro Unico del Lavoro in /storico/libro_paga/).

Utile quando si passa a usare solo i dati estratti dalle buste (es. motore v4 / conciliazione)
e non si vuole più mantenere il registro popolato da ``popolalibropaga``.

Esempi::

    python manage.py pulisci_libro_paga --dry-run
    python manage.py pulisci_libro_paga --azienda-id=1
"""

from django.core.management.base import BaseCommand

from storico.models import LibroPagaStorico


class Command(BaseCommand):
    help = "Elimina voci LibroPagaStorico (opz. filtrate per azienda)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--azienda-id",
            type=int,
            default=None,
            help="Limita l'eliminazione a questa azienda.",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Mostra solo quante righe verrebbero eliminate.",
        )

    def handle(self, *args, **options):
        qs = LibroPagaStorico.objects.all()
        aid = options.get("azienda_id")
        if aid is not None:
            qs = qs.filter(azienda_id=aid)
        n = qs.count()
        if options.get("dry_run"):
            self.stdout.write(
                self.style.WARNING(f"DRY RUN: verrebbero eliminate {n} voci LibroPagaStorico.")
            )
            return
        if n == 0:
            self.stdout.write("Nessuna voce da eliminare.")
            return
        qs.delete()
        self.stdout.write(self.style.SUCCESS(f"Eliminate {n} voci LibroPagaStorico."))
