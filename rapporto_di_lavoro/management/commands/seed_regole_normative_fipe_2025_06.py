from datetime import date
from decimal import Decimal

from django.core.management.base import BaseCommand

from rapporto_di_lavoro.models import RegolaNormativaCCNL


class Command(BaseCommand):
    help = "Popola regole normative CCNL FIPE Pubblici Esercizi (decorrenza 06/2025)"

    def handle(self, *args, **options):
        livelli = ["1", "2", "3", "4", "5", "6S", "6", "7"]
        creati = 0
        aggiornati = 0

        for livello in livelli:
            _, created = RegolaNormativaCCNL.objects.update_or_create(
                ccnl="FIPE Pubblici Esercizi",
                versione="2025-06",
                sezione="ristoranti_pizzerie",
                livello=livello,
                decorrenza_validita_da=date(2025, 6, 1),
                defaults={
                    "decorrenza_validita_a": None,
                    "ore_settimanali": Decimal("40.00"),
                    "ore_mensili": Decimal("173.33"),
                    "ore_giornaliere": Decimal("8.00"),
                    "ferie_annue_giorni": Decimal("26.00"),
                    "permessi_annui_ore": Decimal("72.00"),
                    "scatto_periodicita_mesi": 36,
                    "scatto_importo": Decimal("0.00"),
                    "numero_scatti_massimi": 6,
                    "note": "Regole normative base FIPE 06/2025 - valori da rifinire su documento ufficiale aziendale",
                    "attivo": True,
                },
            )
            if created:
                creati += 1
            else:
                aggiornati += 1

        self.stdout.write(
            self.style.SUCCESS(
                f"✓ Regole normative FIPE 06/2025 - creati: {creati}, aggiornati: {aggiornati}, totale livelli: {len(livelli)}"
            )
        )
