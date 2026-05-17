"""
Verifica post-deploy: registrazione candidato senza SMS (OTP solo e-mail).

Esempi:
  python manage.py verifica_registrazione_candidato
  python manage.py verifica_registrazione_candidato --fix-db   # imposta sms_abilitato=False se ancora True
"""
from __future__ import annotations

from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand
from django.db.migrations.recorder import MigrationRecorder

from accounts.models import ConfigurazioneSistema


class Command(BaseCommand):
    help = (
        'Controlla che la registrazione candidato sia allineata a OTP e-mail: '
        'migrazione 0026, flag DB, template e modulo registrazione_otp.'
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--fix-db',
            action='store_true',
            help='Se sms_abilitato è ancora True, lo imposta a False (solo tabella ConfigurazioneSistema).',
        )

    def handle(self, *args, **options):
        fix_db = options['fix_db']
        errors: list[str] = []

        applied = MigrationRecorder.Migration.objects.filter(
            app='accounts',
            name='0026_force_disable_sms_config',
        ).exists()
        if not applied:
            errors.append(
                'Migrazione accounts 0026_force_disable_sms_config non applicata: '
                'eseguire: python manage.py migrate accounts'
            )

        cfg = ConfigurazioneSistema.get()
        if getattr(cfg, 'sms_abilitato', False):
            if fix_db:
                ConfigurazioneSistema.objects.filter(pk=cfg.pk).update(sms_abilitato=False)
                self.stdout.write(
                    self.style.WARNING(
                        'Corretto: sms_abilitato=False nel DB. '
                        'Serve comunque il deploy del codice e di templates/candidato/registrazione.html '
                        'per testi e logica OTP e-mail.'
                    )
                )
            else:
                errors.append(
                    'ConfigurazioneSistema.sms_abilitato è ancora True. '
                    'Con codice vecchio provoca invio SMS. '
                    'Esegui migrate oppure: python manage.py verifica_registrazione_candidato --fix-db'
                )

        tpl = Path(settings.BASE_DIR) / 'templates' / 'candidato' / 'registrazione.html'
        if not tpl.is_file():
            errors.append(f'Template assente: {tpl}')
        else:
            raw = tpl.read_text(encoding='utf-8')
            low = raw.lower()
            for phrase in (
                'invio sms',
                'via sms',
                'cellulare (sms)',
                'invia codice sms',
                'accesso e verifica cellulare',
            ):
                if phrase in low:
                    errors.append(f'Template registrazione.html contiene ancora: «{phrase}»')

        import accounts.registrazione_otp as ro

        src = Path(ro.__file__).read_text(encoding='utf-8')
        for bad in ('invia_sms', 'sms_gateway', 'Invio SMS'):
            if bad in src:
                errors.append(f'accounts/registrazione_otp.py contiene ancora «{bad}»')

        if errors:
            self.stderr.write(self.style.ERROR('Verifica fallita:'))
            for e in errors:
                self.stderr.write(self.style.ERROR(f'  • {e}'))
            self.stderr.write(
                '\nSul server: 1) deploy/rsync del codice aggiornato da questo repository '
                '2) migrate  3) restart applicazione (gunicorn/systemd). '
                'Solo --fix-db non aggiorna i template né il codice Python.\n'
            )
            raise SystemExit(1)

        self.stdout.write(
            self.style.SUCCESS('OK: registrazione candidato — OTP e-mail, template e modulo senza SMS.')
        )
