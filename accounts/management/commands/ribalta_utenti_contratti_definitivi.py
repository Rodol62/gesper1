"""
Allinea utenti ancora con profilo candidato ma con contratto già definitivo in archivio.

Utile dopo introduzione della logica automatica, per dati storici.

    python manage.py ribalta_utenti_contratti_definitivi --dry-run
    python manage.py ribalta_utenti_contratti_definitivi --apply
"""

from django.core.management.base import BaseCommand, CommandError

from accounts.contratto_utente_definitivo import (
    contratto_e_definitivo,
    ribalta_utente_candidato_su_dipendente_se_contratto_definitivo,
)
from accounts.models import ProfiloCandidato
from rapporto_di_lavoro.models import RapportoDiLavoro


class Command(BaseCommand):
    help = "Ribalta utente su dipendente e rimuove ProfiloCandidato se il contratto è già definitivo."

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true", help="Solo conteggio, nessuna modifica.")
        parser.add_argument("--apply", action="store_true", help="Esegue il ribaltamento.")

    def handle(self, *args, **options):
        dry = bool(options.get("dry_run"))
        apply_mode = bool(options.get("apply"))
        if dry == apply_mode:
            raise CommandError("Specificare esattamente uno tra --dry-run e --apply.")

        candidati = 0
        fatti = 0
        for pc in ProfiloCandidato.objects.select_related("dipendente", "user").iterator(chunk_size=100):
            dip = pc.dipendente
            if not dip:
                continue
            contratto = (
                RapportoDiLavoro.objects.filter(dipendente=dip, stato="sottoscritto")
                .order_by("-data_ora_sottoscrizione", "-id")
                .first()
            )
            if not contratto or not contratto_e_definitivo(contratto, None):
                continue
            candidati += 1
            if apply_mode:
                if ribalta_utente_candidato_su_dipendente_se_contratto_definitivo(
                    dip, contratto, motivo="management ribalta_utenti_contratti_definitivi"
                ):
                    fatti += 1

        if dry:
            self.stdout.write(self.style.WARNING(f"DRY RUN: profili candidato con contratto sottoscritto: {candidati}"))
        else:
            self.stdout.write(self.style.SUCCESS(f"Ribaltati {fatti} / {candidati} casi idonei."))
