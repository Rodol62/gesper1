from django.core.management.base import BaseCommand
from accounts.models import User, Ruolo

class Command(BaseCommand):
    help = 'Migra i ruoli dal vecchio campo ruolo (backup) alla nuova tabella Ruolo e ManyToMany.'

    def handle(self, *args, **options):
        # Crea i ruoli standard se non esistono
        ruoli = [
            ('admin', 'Amministratore'),
            ('hr', 'Risorse Umane'),
            ('dipendente', 'Dipendente'),
            ('consulente', 'Consulente'),
            ('candidato', 'Candidato'),
        ]
        for codice, nome in ruoli:
            Ruolo.objects.get_or_create(codice=codice, defaults={'nome': nome})

        # Per ogni utente, se ha un attributo 'ruolo_backup', assegna il ruolo corrispondente
        for user in User.objects.all():
            ruolo_val = getattr(user, 'ruolo_backup', None)
            if ruolo_val:
                ruolo_obj = Ruolo.objects.filter(codice=ruolo_val).first()
                if ruolo_obj:
                    user.ruoli.add(ruolo_obj)
                    self.stdout.write(f"Assegnato ruolo {ruolo_val} a {user.username}")
        self.stdout.write(self.style.SUCCESS('Migrazione ruoli completata.'))
