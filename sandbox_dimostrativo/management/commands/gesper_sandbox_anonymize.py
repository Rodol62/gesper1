"""
Sostituisce nel database **sandbox** dati personali dipendenti/utenti/candidati con valori fittizi
e riduce i rischi sui documenti (PDF placeholder o rimozione allegati).

Eseguire tipicamente **dopo** ``gesper_sandbox_clone_operativo`` (e opzionalmente ``gesper_sandbox_seed``).
Le operazioni su file system non sono annullabili con un rollback DB: il comando non usa un'unica
transazione globale per evitare incoerenze tra file eliminati e rollback.
"""

from __future__ import annotations

import logging
from typing import Any

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from sandbox_dimostrativo import anonymize_demo as ad
from sandbox_dimostrativo.state import set_sandbox_routing

logger = logging.getLogger(__name__)

SANDBOX = "sandbox"


class Command(BaseCommand):
    help = (
        "Anonimizza dati personali e documenti sensibili nel DB sandbox (nomi fantasia, CF fittizi, "
        "testi generici, PDF documenti dimostrativi). Richiede GESPER_SANDBOX_ENABLED=1 e --yes."
    )

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument(
            "--yes",
            action="store_true",
            help="Conferma obbligatoria: modifica irreversibilmente il database sandbox e alcuni file.",
        )
        parser.add_argument(
            "--skip-documenti-pdf",
            action="store_true",
            help="Non sostituisce i file dei record Documento con PDF placeholder (restano i file clonati).",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        if not options["yes"]:
            raise CommandError(
                "Operazione irreversibile sulla sandbox: rilancia con --yes dopo aver verificato il backup."
            )
        if not getattr(settings, "GESPER_SANDBOX_ENABLED", False):
            raise CommandError("Abilitare GESPER_SANDBOX_ENABLED=1.")
        if SANDBOX not in settings.DATABASES:
            raise CommandError("Alias database «sandbox» non configurato.")

        def progress(msg: str) -> None:
            self.stdout.write(msg)

        self.stdout.write(self.style.WARNING("Avvio anonimizzazione sandbox…"))
        logger.info("gesper_sandbox_anonymize avviato")

        # Stesso problema di gesper_sandbox_seed: senza routing sandbox i segnali post_save su User
        # possono scrivere relazioni User↔Dipendente bloccate dal router.
        set_sandbox_routing(True)
        try:
            ad.anonymize_dipendenti(progress=progress)
            ad.anonymize_users_demo(progress=progress)
            ad.anonymize_profili_candidato(progress=progress)
            ad.anonymize_richieste_e_inbox(progress=progress)
            ad.anonymize_comunicazioni_recesso(progress=progress)
            ad.anonymize_simulazioni_voci(progress=progress)
            ad.anonymize_rapporti_allegati(progress=progress)
            if options.get("skip_documenti_pdf"):
                self.stdout.write(self.style.WARNING("Saltata sostituzione PDF documenti (--skip-documenti-pdf)."))
            else:
                ad.anonymize_documenti(progress=progress)
        finally:
            set_sandbox_routing(False)

        self.stdout.write(self.style.SUCCESS("Anonimizzazione sandbox completata."))
