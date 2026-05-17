"""
Mostra percorsi SQLite operativo vs sandbox e conteggi record (lettura esplicita per alias DB).

Utile per verificare che ``gesper_sandbox_clone_operativo`` abbia copiato i dati attesi:
``migrate`` sulla sandbox crea solo lo schema, non replica i dati dall'operativo.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import DatabaseError


def _db_counts(alias: str) -> tuple[int, int, int]:
    """Restituisce (aziende, dipendenti, utenti) sul database ``alias``."""
    from accounts.models import User
    from anagrafiche.models import Azienda, Dipendente

    return (
        Azienda.objects.using(alias).count(),
        Dipendente.objects.using(alias).count(),
        User.objects.using(alias).count(),
    )


class Command(BaseCommand):
    help = (
        "Percorsi e conteggi DB default vs sandbox. "
        "I dati operativi finiscono nella sandbox solo con gesper_sandbox_clone_operativo --yes, non con migrate."
    )

    def handle(self, *args: Any, **options: Any) -> None:
        if not getattr(settings, "GESPER_SANDBOX_ENABLED", False):
            raise CommandError("Abilitare GESPER_SANDBOX_ENABLED=1 per vedere lo stato della sandbox.")
        if "sandbox" not in settings.DATABASES:
            raise CommandError("Alias database «sandbox» non configurato.")

        def _line(label: str, path: Path) -> None:
            exists = path.is_file()
            size = path.stat().st_size if exists else 0
            self.stdout.write(f"  {label}: {path}")
            self.stdout.write(f"    esiste={exists}  dimensione_bytes={size}")

        src = Path(settings.DATABASES["default"]["NAME"]).expanduser().resolve()
        dst = Path(settings.DATABASES["sandbox"]["NAME"]).expanduser().resolve()
        self.stdout.write(self.style.NOTICE("Database operativo (default):"))
        _line("default", src)
        self.stdout.write(self.style.NOTICE("Database dimostrativo (sandbox):"))
        _line("sandbox", dst)

        # Conteggi senza dipendere dal router della richiesta HTTP: .using esplicito
        self.stdout.write("")
        try:
            az_d, dip_d, usr_d = _db_counts("default")
            az_s, dip_s, usr_s = _db_counts("sandbox")
        except DatabaseError as exc:
            raise CommandError(
                f"Impossibile leggere i database (permessi, file bloccato o percorso errato): {exc}"
            ) from exc

        self.stdout.write(f"  Conteggi default:   Aziende={az_d}  Dipendenti={dip_d}  Utenti={usr_d}")
        self.stdout.write(f"  Conteggi sandbox:   Aziende={az_s}  Dipendenti={dip_s}  Utenti={usr_s}")

        if dip_d > 0 and dip_s == 0:
            self.stdout.write(
                self.style.WARNING(
                    "Attenzione: l'operativo ha dipendenti ma la sandbox no. "
                    "Esegui «python manage.py gesper_sandbox_clone_operativo --yes» (non basta gesper_sandbox_migrate)."
                )
            )
        elif dip_d > 0 and dip_s > 0 and dip_s != dip_d:
            self.stdout.write(
                self.style.WARNING(
                    "I conteggi operativo e sandbox differiscono: la sandbox non è una copia aggiornata "
                    "dell'operativo (oppure hai eseguito solo seed/migrate). Rilancia gesper_sandbox_clone_operativo --yes."
                )
            )
        if src == dst:
            self.stdout.write(
                self.style.ERROR(
                    "Percorsi default e sandbox coincidono: configurazione errata (stesso file per entrambi)."
                )
            )
