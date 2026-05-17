"""
Copia il database SQLite **operativo** e la cartella **MEDIA_ROOT** nella sandbox dimostrativa.

Sostituisce ``db_sandbox.sqlite3`` con una copia del file dell'operativo e, se richiesto,
sincronizza i file sotto ``GESPER_SANDBOX_MEDIA_ROOT`` (opzione di azzeramento prima della copia).

Avvertenze:
- Possibile **duplicazione di dati sensibili** nella copia dimostrativa; usare solo in ambienti controllati.
  Dopo il clone eseguire ``gesper_sandbox_anonymize --yes`` per anonimizzare anagrafiche e documenti.
- Con SQLite conviene **fermare** ``runserver`` / Gunicorn (o almeno evitare scritture) durante la copia del DB.
- Dopo la copia del DB, esegue ``migrate`` sull'alias ``sandbox`` per allineare eventuali migrazioni pendenti.
- L'utente dedicato ``demo`` del seed potrebbe non esistere più se nel DB operativo non c'è: in tal caso
  rieseguire ``gesper_sandbox_seed`` (``get_or_create`` sul clone).
"""

from __future__ import annotations

import logging
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any

from django.conf import settings
from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError
from django.db import connections

from sandbox_dimostrativo.state import set_sandbox_routing

logger = logging.getLogger(__name__)


def _path_setting(name: str) -> Path:
    raw = getattr(settings, name, None)
    if raw is None:
        raise CommandError(f"Impostazione {name!r} assente.")
    return Path(raw).expanduser().resolve()


def _conteggi_demo_db(alias: str) -> tuple[int, int, int]:
    """(aziende, dipendenti, utenti) sul database ``alias`` — ``.using`` esplicito (ignora router thread-local)."""
    from accounts.models import User
    from anagrafiche.models import Azienda, Dipendente

    return (
        Azienda.objects.using(alias).count(),
        Dipendente.objects.using(alias).count(),
        User.objects.using(alias).count(),
    )


class Command(BaseCommand):
    help = (
        "Clona DB SQLite operativo e (opzionale) i file MEDIA nella sandbox dimostrativa. "
        "Richiede --yes e GESPER_SANDBOX_ENABLED=1."
    )

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument(
            "--yes",
            action="store_true",
            help="Conferma obbligatoria: sovrascrive il database sandbox (e opzionalmente i file media).",
        )
        parser.add_argument(
            "--skip-db",
            action="store_true",
            help="Non copiare il database operativo.",
        )
        parser.add_argument(
            "--skip-media",
            action="store_true",
            help="Non copiare i file media.",
        )
        parser.add_argument(
            "--reset-sandbox-media",
            action="store_true",
            help="Svuota il contenuto di GESPER_SANDBOX_MEDIA_ROOT prima della copia (specchio più fedele).",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        if not options["yes"]:
            raise CommandError(
                "Operazione distruttiva sulla sandbox: rilancia con --yes dopo aver fermato o ridotto il traffico."
            )
        if not getattr(settings, "GESPER_SANDBOX_ENABLED", False):
            raise CommandError("Abilitare GESPER_SANDBOX_ENABLED=1.")
        if "sandbox" not in settings.DATABASES:
            raise CommandError("Alias database «sandbox» non configurato.")

        src_db = Path(settings.DATABASES["default"]["NAME"]).expanduser().resolve()
        dst_db = Path(settings.DATABASES["sandbox"]["NAME"]).expanduser().resolve()

        if not options["skip_db"]:
            self.stdout.write(
                self.style.NOTICE(
                    "Sorgente (default) e destinazione (sandbox) devono essere i file SQLite attesi "
                    "(verifica GESPER_SQLITE_PATH / GESPER_DATA_ROOT se i conteggi non coincidono)."
                )
            )
            self.stdout.write(f"  Sorgente DB:      {src_db}")
            self.stdout.write(f"  Destinazione DB: {dst_db}")
            if src_db == dst_db:
                raise CommandError("Percorsi default e sandbox coincidono: impossibile clonare in sé stesso.")

            if not src_db.is_file():
                raise CommandError(f"Database operativo non trovato: {src_db}")
            az_d, dip_d, usr_d = _conteggi_demo_db("default")
            self.stdout.write(
                f"  Conteggi operativo prima della copia: Aziende={az_d}  Dipendenti={dip_d}  Utenti={usr_d}"
            )
            if dip_d == 0 and az_d <= 1:
                self.stdout.write(
                    self.style.WARNING(
                        "L'operativo ha pochi o zero dipendenti: se i dati reali sono in un altro file SQLite, "
                        "imposta GESPER_SQLITE_PATH (o sposta il DB sotto GESPER_DATA_ROOT/db.sqlite3) e rilancia."
                    )
                )

            dst_db.parent.mkdir(parents=True, exist_ok=True)
            if dst_db.is_file():
                bak = dst_db.with_suffix(f"{dst_db.suffix}.bak-{datetime.now().strftime('%Y%m%d-%H%M%S')}")
                shutil.move(str(dst_db), str(bak))
                self.stdout.write(self.style.WARNING(f"Backup sandbox DB: {bak}"))
            shutil.copy2(src_db, dst_db)
            # Connessione già aperta sul path sandbox punterebbe al file sostituito in modo incoerente: chiudi.
            if "sandbox" in connections:
                connections["sandbox"].close()
            logger.info("Sandbox DB clonato da operativo (src=%s)", src_db)
            self.stdout.write(self.style.SUCCESS(f"Database sandbox sostituito con copia di: {src_db}"))
            set_sandbox_routing(True)
            try:
                call_command("migrate", database="sandbox", interactive=False)
            finally:
                set_sandbox_routing(False)
            self.stdout.write(self.style.SUCCESS("Migrazioni eseguite sull'alias «sandbox»."))
            az_s, dip_s, usr_s = _conteggi_demo_db("sandbox")
            self.stdout.write(
                f"  Conteggi sandbox dopo migrate: Aziende={az_s}  Dipendenti={dip_s}  Utenti={usr_s}"
            )
            if dip_d > 0 and dip_s != dip_d:
                self.stdout.write(
                    self.style.WARNING(
                        f"I dipendenti nella sandbox ({dip_s}) differiscono dall'operativo ({dip_d}) dopo la copia: "
                        "controlla lock SQLite, copia interrotta o percorsi diversi tra runserver e questo comando."
                    )
                )

        if not options["skip_media"]:
            media_root = _path_setting("MEDIA_ROOT")
            sb_root = getattr(settings, "GESPER_SANDBOX_MEDIA_ROOT", None)
            if sb_root is None:
                raise CommandError("GESPER_SANDBOX_MEDIA_ROOT non configurato.")
            sb = Path(sb_root).expanduser().resolve()
            if media_root == sb:
                raise CommandError("MEDIA_ROOT e GESPER_SANDBOX_MEDIA_ROOT coincidono: operazione annullata.")
            sb.mkdir(parents=True, exist_ok=True)
            if options["reset_sandbox_media"]:
                for child in sb.iterdir():
                    if child.is_dir():
                        shutil.rmtree(child)
                    else:
                        child.unlink()
                self.stdout.write(self.style.WARNING(f"Svuotata cartella sandbox media: {sb}"))
            if not media_root.is_dir():
                self.stdout.write(self.style.WARNING(f"MEDIA_ROOT non è una cartella: {media_root}"))
            else:
                for item in media_root.iterdir():
                    dest = sb / item.name
                    try:
                        if item.is_dir():
                            if dest.exists():
                                shutil.rmtree(dest)
                            shutil.copytree(item, dest, dirs_exist_ok=True)
                        else:
                            shutil.copy2(item, dest)
                    except OSError as exc:
                        raise CommandError(f"Errore copia {item} → {dest}: {exc}") from exc
                logger.info("Media sandbox sincronizzati da MEDIA_ROOT=%s", media_root)
                self.stdout.write(self.style.SUCCESS(f"File media copiati da: {media_root} → {sb}"))

        self.stdout.write(
            "Suggerimento: «python manage.py gesper_sandbox_anonymize --yes» per nomi/CF fittizi e documenti "
            "dimostrativi; poi «python manage.py gesper_sandbox_seed» per password/ruoli utente «demo» "
            "e collegamento all'azienda reale se hai clonato dati operativi."
        )
