from decimal import Decimal
from datetime import date

from django.core.management.base import BaseCommand

from rapporto_di_lavoro.models import ModuloContrattuale, ParametroCCNLTurismo


class Command(BaseCommand):
    help = "Popola parametri base CCNL Turismo Confcommercio (piccoli esercizi commerciali)"

    def handle(self, *args, **options):
        moduli = [
            ("Proposta assunzione standard", "proposta_assunzione", True),
            ("Integrazione dati anagrafici", "integrazione_dati", True),
            ("Consenso privacy assunzione", "consenso_privacy", True),
            ("Allegati obbligatori preassunzione", "allegati_obbligatori", True),
        ]

        for nome, categoria, compilabile_da_dipendente in moduli:
            ModuloContrattuale.objects.get_or_create(
                nome=nome,
                defaults={
                    "categoria": categoria,
                    "compilabile_da_dipendente": compilabile_da_dipendente,
                    "attivo": True,
                },
            )

        parametri = [
            {
                "sezione": "ristoranti_pizzerie",
                "livello": "1",
                "qualifica": "Capo cucina / Chef",
                "tipo_contratto_nazionale": "Tempo indeterminato full-time",
                "decorrenza_validita_da": date(2024, 1, 1),
                "decorrenza_validita_a": date(2026, 12, 31),
                "importo_lordo_mensile": Decimal("2300.00"),
                "paga_base_mensile": Decimal("2000.00"),
                "contingenza_mensile": Decimal("240.00"),
                "edr_mensile": Decimal("60.00"),
                "indennita_mensile": Decimal("120.00"),
                "ore_settimanali": Decimal("40.00"),
                "ore_mensili": Decimal("173.33"),
                "ore_giornaliere": Decimal("8.00"),
                "scatto_periodicita_mesi": 24,
                "scatto_importo": Decimal("35.00"),
                "numero_scatti_massimi": 10,
                "straordinario_diurno_maggiorazione": Decimal("15.00"),
                "straordinario_notturno_maggiorazione": Decimal("30.00"),
                "straordinario_festivo_maggiorazione": Decimal("30.00"),
                "riposi_compensativi_regola": "Riposo compensativo per lavoro festivo secondo CCNL Turismo Confcommercio.",
            },
            {
                "sezione": "ristoranti_pizzerie",
                "livello": "3",
                "qualifica": "Cuoco",
                "tipo_contratto_nazionale": "Tempo indeterminato full-time",
                "decorrenza_validita_da": date(2024, 1, 1),
                "decorrenza_validita_a": date(2026, 12, 31),
                "importo_lordo_mensile": Decimal("1850.00"),
                "paga_base_mensile": Decimal("1600.00"),
                "contingenza_mensile": Decimal("200.00"),
                "edr_mensile": Decimal("50.00"),
                "indennita_mensile": Decimal("90.00"),
                "ore_settimanali": Decimal("40.00"),
                "ore_mensili": Decimal("173.33"),
                "ore_giornaliere": Decimal("8.00"),
                "scatto_periodicita_mesi": 24,
                "scatto_importo": Decimal("30.00"),
                "numero_scatti_massimi": 10,
                "straordinario_diurno_maggiorazione": Decimal("15.00"),
                "straordinario_notturno_maggiorazione": Decimal("30.00"),
                "straordinario_festivo_maggiorazione": Decimal("30.00"),
                "riposi_compensativi_regola": "Riposo compensativo per lavoro festivo secondo CCNL Turismo Confcommercio.",
            },
            {
                "sezione": "somministrazione_tavoli",
                "livello": "4",
                "qualifica": "Cameriere di sala",
                "tipo_contratto_nazionale": "Tempo indeterminato full-time",
                "decorrenza_validita_da": date(2024, 1, 1),
                "decorrenza_validita_a": date(2026, 12, 31),
                "importo_lordo_mensile": Decimal("1650.00"),
                "paga_base_mensile": Decimal("1420.00"),
                "contingenza_mensile": Decimal("180.00"),
                "edr_mensile": Decimal("50.00"),
                "indennita_mensile": Decimal("70.00"),
                "ore_settimanali": Decimal("40.00"),
                "ore_mensili": Decimal("173.33"),
                "ore_giornaliere": Decimal("8.00"),
                "scatto_periodicita_mesi": 24,
                "scatto_importo": Decimal("25.00"),
                "numero_scatti_massimi": 10,
                "straordinario_diurno_maggiorazione": Decimal("15.00"),
                "straordinario_notturno_maggiorazione": Decimal("30.00"),
                "straordinario_festivo_maggiorazione": Decimal("30.00"),
                "riposi_compensativi_regola": "Riposo compensativo per lavoro festivo secondo CCNL Turismo Confcommercio.",
            },
            {
                "sezione": "somministrazione_tavoli",
                "livello": "5",
                "qualifica": "Aiuto cameriere / Runner",
                "tipo_contratto_nazionale": "Tempo determinato full-time",
                "decorrenza_validita_da": date(2024, 1, 1),
                "decorrenza_validita_a": date(2026, 12, 31),
                "importo_lordo_mensile": Decimal("1530.00"),
                "paga_base_mensile": Decimal("1320.00"),
                "contingenza_mensile": Decimal("170.00"),
                "edr_mensile": Decimal("40.00"),
                "indennita_mensile": Decimal("50.00"),
                "ore_settimanali": Decimal("40.00"),
                "ore_mensili": Decimal("173.33"),
                "ore_giornaliere": Decimal("8.00"),
                "scatto_periodicita_mesi": 24,
                "scatto_importo": Decimal("22.00"),
                "numero_scatti_massimi": 10,
                "straordinario_diurno_maggiorazione": Decimal("15.00"),
                "straordinario_notturno_maggiorazione": Decimal("30.00"),
                "straordinario_festivo_maggiorazione": Decimal("30.00"),
                "riposi_compensativi_regola": "Riposo compensativo per lavoro festivo secondo CCNL Turismo Confcommercio.",
            },
            {
                "sezione": "ristoranti_pizzerie",
                "livello": "6",
                "qualifica": "Commis di cucina",
                "tipo_contratto_nazionale": "Apprendistato professionalizzante",
                "decorrenza_validita_da": date(2024, 1, 1),
                "decorrenza_validita_a": date(2026, 12, 31),
                "importo_lordo_mensile": Decimal("1380.00"),
                "paga_base_mensile": Decimal("1200.00"),
                "contingenza_mensile": Decimal("140.00"),
                "edr_mensile": Decimal("40.00"),
                "indennita_mensile": Decimal("30.00"),
                "ore_settimanali": Decimal("40.00"),
                "ore_mensili": Decimal("173.33"),
                "ore_giornaliere": Decimal("8.00"),
                "scatto_periodicita_mesi": 24,
                "scatto_importo": Decimal("18.00"),
                "numero_scatti_massimi": 10,
                "straordinario_diurno_maggiorazione": Decimal("15.00"),
                "straordinario_notturno_maggiorazione": Decimal("30.00"),
                "straordinario_festivo_maggiorazione": Decimal("30.00"),
                "riposi_compensativi_regola": "Riposo compensativo per lavoro festivo secondo CCNL Turismo Confcommercio.",
            },
        ]

        creati = 0
        aggiornati = 0
        for p in parametri:
            _, created = ParametroCCNLTurismo.objects.update_or_create(
                ccnl="Turismo Confcommercio",
                versione="2024-2026",
                sezione=p["sezione"],
                livello=p["livello"],
                qualifica=p["qualifica"],
                defaults={
                    "tipo_contratto_nazionale": p["tipo_contratto_nazionale"],
                    "decorrenza_validita_da": p["decorrenza_validita_da"],
                    "decorrenza_validita_a": p["decorrenza_validita_a"],
                    "importo_lordo_mensile": p["importo_lordo_mensile"],
                    "paga_base_mensile": p["paga_base_mensile"],
                    "contingenza_mensile": p["contingenza_mensile"],
                    "edr_mensile": p["edr_mensile"],
                    "indennita_mensile": p["indennita_mensile"],
                    "ore_settimanali": p["ore_settimanali"],
                    "ore_mensili": p["ore_mensili"],
                    "ore_giornaliere": p["ore_giornaliere"],
                    "scatto_periodicita_mesi": p["scatto_periodicita_mesi"],
                    "scatto_importo": p["scatto_importo"],
                    "numero_scatti_massimi": p["numero_scatti_massimi"],
                    "straordinario_diurno_maggiorazione": p["straordinario_diurno_maggiorazione"],
                    "straordinario_notturno_maggiorazione": p["straordinario_notturno_maggiorazione"],
                    "straordinario_festivo_maggiorazione": p["straordinario_festivo_maggiorazione"],
                    "riposi_compensativi_regola": p["riposi_compensativi_regola"],
                    "attivo": True,
                },
            )
            if created:
                creati += 1
            else:
                aggiornati += 1

        self.stdout.write(self.style.SUCCESS(f"✓ Parametri CCNL creati: {creati}, aggiornati: {aggiornati}"))
