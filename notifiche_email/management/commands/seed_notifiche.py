from django.core.management.base import BaseCommand
from notifiche_email.models import TipoNotifica


class Command(BaseCommand):
    help = 'Crea i tipi notifica base'

    def handle(self, *args, **options):
        tipi = [
            (
                'Richiesta Approvata',
                'richiesta_approvata',
                'La tua richiesta è stata approvata',
                '<p>Caro {dipendente_nome}, la tua richiesta di {tipo} dal {data_inizio} al {data_fine} è approvata.</p>',
            ),
            (
                'Richiesta Rifiutata',
                'richiesta_rifiutata',
                'La tua richiesta è stata rifiutata',
                '<p>Caro {dipendente_nome}, la tua richiesta è stata rifiutata.</p>',
            ),
            (
                'Richiesta da Approvare',
                'richiesta_da_approvare',
                'Richiesta in attesa',
                '<p>{dipendente_nome} ha una richiesta in attesa.</p>',
            ),
            (
                'Documento Caricato',
                'documento_caricato',
                'Nuovo documento nel fascicolo',
                '<p>Caro {dipendente_nome}, è stato caricato un documento.</p>',
            ),
        ]

        creati = 0
        for nome, trigger, subject, body in tipi:
            _, created = TipoNotifica.objects.get_or_create(
                evento_trigger=trigger,
                defaults={
                    'nome': nome,
                    'template_subject': subject,
                    'template_body': body,
                    'attivo': True,
                }
            )
            if created:
                creati += 1

        self.stdout.write(self.style.SUCCESS(f'✓ Tipi notifica creati: {creati}'))
