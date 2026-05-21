"""Collega Dipendente (stato=candidato) senza utente a User + ProfiloCandidato."""
from django.core.management.base import BaseCommand

from accounts.candidato_da_dipendente import assicura_candidati_hr_azienda


class Command(BaseCommand):
    help = (
        'Crea account portale (User + ProfiloCandidato) per dipendenti in stato candidato '
        'inseriti da HR senza utente collegato.'
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--azienda-id',
            type=int,
            default=None,
            help='Limita a una azienda (ID).',
        )

    def handle(self, *args, **options):
        n = assicura_candidati_hr_azienda(options.get('azienda_id'))
        self.stdout.write(self.style.SUCCESS(f'Collegati o verificati {n} candidati HR.'))
