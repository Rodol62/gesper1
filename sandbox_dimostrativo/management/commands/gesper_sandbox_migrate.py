"""
Applica le migrazioni Django sul database ``sandbox`` (file SQLite separato).

Crea o aggiorna **solo lo schema** (tabelle/colonne); **non** importa righe dall'operativo.
Per copiare i dati aziendali nella sandbox: ``gesper_sandbox_clone_operativo --yes``.

Prerequisito: ``GESPER_SANDBOX_ENABLED=1`` e variabili in ``settings.py`` caricate.
"""

from __future__ import annotations

from django.conf import settings
from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError

from sandbox_dimostrativo.state import set_sandbox_routing


class Command(BaseCommand):
    help = "Esegue migrate sul database sandbox (db parallelo)."

    def handle(self, *args, **options) -> None:
        if not getattr(settings, "GESPER_SANDBOX_ENABLED", False):
            raise CommandError("Impostare GESPER_SANDBOX_ENABLED=1 nell'ambiente prima di migrare il sandbox.")
        if "sandbox" not in settings.DATABASES:
            raise CommandError("Alias database «sandbox» non configurato.")
        # Senza questo, il router manda l'ORM del RunPython su «default» mentre lo schema_editor
        # lavora su «sandbox» (migrazioni incoerenti / errori FK).
        set_sandbox_routing(True)
        try:
            call_command("migrate", database="sandbox", interactive=False)
        finally:
            set_sandbox_routing(False)
        self.stdout.write(self.style.SUCCESS("Migrazioni sandbox completate."))
        self.stdout.write(
            self.style.NOTICE(
                "Nota: migrate aggiorna solo lo schema SQLite della sandbox, non copia i dati dall'operativo. "
                "Per duplicare i dati: python manage.py gesper_sandbox_clone_operativo --yes"
            )
        )
