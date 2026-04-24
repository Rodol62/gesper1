from datetime import date
from decimal import Decimal

from django.core.management.base import BaseCommand

from rapporto_di_lavoro.models import ParametroCCNLTurismo


class Command(BaseCommand):
    help = "Popola/aggiorna tabelle retributive CCNL FIPE Pubblici Esercizi (decorrenza 06/2025)"

    def handle(self, *args, **options):
        # Dati forniti dall'utente (giugno 2025)
        # Livello, Minimo, Contingenza, Totale Mensile
        tabella = [
            ("1", Decimal("1517.16"), Decimal("536.71"), Decimal("2053.87")),
            ("2", Decimal("1337.33"), Decimal("531.59"), Decimal("1868.92")),
            ("3", Decimal("1228.88"), Decimal("528.26"), Decimal("1757.14")),
            ("4", Decimal("1127.75"), Decimal("524.94"), Decimal("1652.69")),
            ("5", Decimal("1021.49"), Decimal("522.37"), Decimal("1543.86")),
            ("6S", Decimal("960.13"), Decimal("520.64"), Decimal("1480.77")),
            ("6", Decimal("937.80"), Decimal("520.51"), Decimal("1458.31")),
            ("7", Decimal("841.89"), Decimal("518.45"), Decimal("1360.34")),
        ]

        creati = 0
        aggiornati = 0

        for idx, (livello, minimo, contingenza, totale) in enumerate(tabella, start=1):
            # Controllo coerenza importi
            calcolato = minimo + contingenza
            if calcolato != totale:
                self.stdout.write(
                    self.style.WARNING(
                        f"Attenzione livello {livello}: minimo+contingenza={calcolato} diverso da totale={totale}. Uso totale fornito."
                    )
                )

            _, created = ParametroCCNLTurismo.objects.update_or_create(
                ccnl="FIPE Pubblici Esercizi",
                versione="2025-06",
                sezione="ristoranti_pizzerie",
                livello=livello,
                qualifica=f"Livello {livello}",
                defaults={
                    "tipo_contratto_nazionale": "Tempo indeterminato full-time",
                    "decorrenza_validita_da": date(2025, 6, 1),
                    "decorrenza_validita_a": None,
                    "livello_ordinamento": idx,
                    "minimo_tabellare": minimo,
                    "totale_tabellare": totale,
                    "fonte_tabella": "tabelle retributive.pdf",
                    "data_rilevazione_tabella": date(2025, 6, 1),
                    "importo_lordo_mensile": totale,
                    "paga_base_mensile": minimo,
                    "contingenza_mensile": contingenza,
                    "edr_mensile": Decimal("0.00"),
                    "indennita_mensile": Decimal("0.00"),
                    "ore_settimanali": Decimal("40.00"),
                    "ore_mensili": Decimal("173.33"),
                    "ore_giornaliere": Decimal("8.00"),
                    "scatto_periodicita_mesi": 36,
                    "scatto_importo": Decimal("0.00"),
                    "numero_scatti_massimi": 6,
                    "straordinario_diurno_maggiorazione": Decimal("15.00"),
                    "straordinario_notturno_maggiorazione": Decimal("30.00"),
                    "straordinario_festivo_maggiorazione": Decimal("30.00"),
                    "riposi_compensativi_regola": "Da definire su base aziendale/CCNL vigente",
                    "note": "Tabella retributiva FIPE Pubblici Esercizi valida da giugno 2025 (dataset iniziale)",
                    "attivo": True,
                },
            )

            if created:
                creati += 1
            else:
                aggiornati += 1

        self.stdout.write(
            self.style.SUCCESS(
                f"✓ FIPE 06/2025 - record creati: {creati}, aggiornati: {aggiornati}, totale livelli: {len(tabella)}"
            )
        )
