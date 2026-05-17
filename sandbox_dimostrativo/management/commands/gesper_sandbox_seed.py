"""
Popola il database sandbox con dati **non sensibili** minimi (azienda fittizia + utente demo).

Eseguire dopo ``gesper_sandbox_migrate``. Copia i codici ruolo dall'ambiente operativo (default)
se disponibili, così il login demo ha il ruolo «admin».

L'utente dimostrato ha username in ``GESPER_SANDBOX_USERNAMES`` (default ``demo``)
e password ``GESPER_SANDBOX_DEMO_PASSWORD`` (default in ``settings``).

Dopo ``gesper_sandbox_clone_operativo``: se nel sandbox esistono aziende «reali» (non SANDBOX00001)
senza dipendenti sulla sola azienda fittizia, l'utente ``demo`` viene collegato alla prima azienda reale
così liste dipendenti / documenti / contratti (filtrati per azienda operativa) non risultano vuoti.
Non sovrascrive più l'``azienda`` dell'utente demo esistente solo per aggiornare la password.
"""

from __future__ import annotations

import os

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from sandbox_dimostrativo.state import set_sandbox_routing

SANDBOX = "sandbox"
AZIENDA_PIVA = "SANDBOX00001"
AZIENDA_NOME = "AZIENDA DIMOSTRATIVA (sandbox)"


class Command(BaseCommand):
    help = "Crea azienda e utente demo nel database sandbox (dati fittizi)."

    def handle(self, *args, **options) -> None:
        if not getattr(settings, "GESPER_SANDBOX_ENABLED", False):
            raise CommandError("Abilitare GESPER_SANDBOX_ENABLED=1.")
        if SANDBOX not in settings.DATABASES:
            raise CommandError("Database sandbox non configurato.")

        from accounts.models import Ruolo, User
        from anagrafiche.models import Azienda, Dipendente

        # Senza routing sandbox attivo, il router può mandare le scritture M2M (es. ``ruoli.add``) sul
        # ``default`` mentre User/Ruolo sono sul sandbox → IntegrityError FK.
        set_sandbox_routing(True)
        try:
            for r in Ruolo.objects.using("default").iterator():
                Ruolo.objects.using(SANDBOX).update_or_create(
                    codice=r.codice,
                    defaults={"nome": r.nome},
                )

            az, _ = Azienda.objects.using(SANDBOX).get_or_create(
                partita_iva=AZIENDA_PIVA,
                defaults={
                    "nome": AZIENDA_NOME,
                    "indirizzo": "Via Dimostrativa 1 — dati fittizi",
                    "email": "sandbox-azienda@invalid.local",
                },
            )

            demo_names = getattr(settings, "GESPER_SANDBOX_USERNAMES", frozenset({"demo"}))
            username = min(demo_names) if demo_names else "demo"
            pw = (
                os.environ.get("GESPER_SANDBOX_DEMO_PASSWORD", "").strip()
                or getattr(settings, "GESPER_SANDBOX_DEMO_PASSWORD", "Demo2026@!")
            )

            real_az = (
                Azienda.objects.using(SANDBOX).exclude(partita_iva=AZIENDA_PIVA).order_by("pk").first()
            )
            demo_azienda_pk = real_az.pk if real_az is not None else az.pk

            # ``azienda_id``: con DATABASE_ROUTERS attivo e nessun routing sandbox sul thread del comando,
            # ``azienda=az`` può far fallire allow_relation (istanza letta con .using(sandbox) vs User nuovo).
            u, created = User.objects.db_manager(SANDBOX).get_or_create(
                username=username,
                defaults={
                    "email": f"{username}@invalid.local",
                    "first_name": "Utente",
                    "last_name": "Dimostrativo",
                    "is_staff": True,
                    "is_superuser": True,
                    "is_active": True,
                    "convalidato": True,
                    "privacy_accettata": True,
                    "azienda_id": demo_azienda_pk,
                },
            )
            if created:
                u.set_password(pw)
                u.save(using=SANDBOX)
            else:
                u.is_staff = True
                u.is_superuser = True
                u.convalidato = True
                u.privacy_accettata = True
                u.is_active = True
                u.set_password(pw)
                u.save(
                    using=SANDBOX,
                    update_fields=[
                        "is_staff",
                        "is_superuser",
                        "convalidato",
                        "privacy_accettata",
                        "is_active",
                        "password",
                    ],
                )

            if u.azienda_id == az.pk:
                ha_reale = Azienda.objects.using(SANDBOX).exclude(partita_iva=AZIENDA_PIVA).exists()
                dip_su_fake = Dipendente.objects.using(SANDBOX).filter(azienda_id=az.pk).count()
                if ha_reale and dip_su_fake == 0:
                    real = Azienda.objects.using(SANDBOX).exclude(partita_iva=AZIENDA_PIVA).order_by("pk").first()
                    if real is not None:
                        u.azienda_id = real.pk
                        u.save(using=SANDBOX, update_fields=["azienda_id"])
                        self.stdout.write(
                            self.style.WARNING(
                                f"Utente «{username}» collegato all'azienda reale «{real.nome}» "
                                f"(P.IVA {real.partita_iva}) per visualizzare dipendenti e documenti clonati."
                            )
                        )

            admin_r = Ruolo.objects.using(SANDBOX).filter(codice="admin").first()
            if admin_r:
                u.ruoli.add(admin_r)

            az_msg = Azienda.objects.using(SANDBOX).get(pk=u.azienda_id)
            self.stdout.write(
                self.style.SUCCESS(
                    f"Sandbox seed: utente «{username}» (password da GESPER_SANDBOX_DEMO_PASSWORD), "
                    f"azienda operativa «{az_msg.nome}» (P.IVA {az_msg.partita_iva}). "
                    "Se le liste restano vuote, esci e rifai login (sessione «azienda»)."
                )
            )
        finally:
            set_sandbox_routing(False)
