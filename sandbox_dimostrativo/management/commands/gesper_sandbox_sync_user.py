"""
Copia un utente (e l'azienda collegata, se presente) dal database operativo al sandbox,
conservando lo stesso ``pk`` così la sessione web resta coerente con ``session['_auth_user_id']``.

Esempio: ``python manage.py gesper_sandbox_sync_user --username admin``

Richiede migrazioni già applicate sul sandbox.
"""

from __future__ import annotations

from django.conf import settings
from django.core import serializers
from django.core.management.base import BaseCommand, CommandError

SANDBOX = "sandbox"


class Command(BaseCommand):
    help = "Copia utente (+ azienda FK) dal default al sandbox mantenendo il pk."

    def add_arguments(self, parser) -> None:
        parser.add_argument("--username", type=str, required=True, help="Username nell'ambiente operativo")

    def handle(self, *args, **options) -> None:
        if not getattr(settings, "GESPER_SANDBOX_ENABLED", False):
            raise CommandError("Abilitare GESPER_SANDBOX_ENABLED=1.")
        if SANDBOX not in settings.DATABASES:
            raise CommandError("Database sandbox non configurato.")

        from accounts.models import User
        from anagrafiche.models import Azienda

        username = (options["username"] or "").strip()
        u = User.objects.using("default").filter(username=username).first()
        if not u:
            raise CommandError(f"Utente «{username}» non trovato nel database operativo.")

        to_dump: list = []
        if u.azienda_id:
            az = Azienda.objects.using("default").filter(pk=u.azienda_id).first()
            if az:
                to_dump.append(az)
        to_dump.append(u)

        data = serializers.serialize("json", to_dump)
        for obj in serializers.deserialize("json", data, using=SANDBOX, ignorenonexistent=True):
            obj.save()

        self.stdout.write(
            self.style.SUCCESS(
                f"Copiato nel sandbox: utente pk={u.pk} «{u.username}»"
                + (f" con azienda pk={u.azienda_id}" if u.azienda_id else "")
            )
        )
