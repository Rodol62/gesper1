from django.core.management.base import BaseCommand
from django.db import transaction

from rapporto_di_lavoro.models import Mansione, MansioneLivelloCCNL


DEFAULT_WHITELIST = {
    'pizzaiolo/a': {'3', '4', '5'},
    'cuoco/a': {'3', '4', '5'},
    'cameriere/a': {'5', '6', '6S', '7'},
    'fattorino': {'6', '6S', '7'},
    'lavapiatti': {'6', '6S', '7'},
    'barman': {'4', '5', '6'},
    'responsabile': {'1', '2', '3'},
    'amministrativo': {'3', '4', '5'},
}


def _norm(value):
    return ' '.join(str(value or '').strip().lower().split())


class Command(BaseCommand):
    help = (
        'Normalizza mappature mansione-livello: applica whitelist livelli per mansione, '
        'disattiva fallback incoerenti e assicura la presenza delle righe consentite.'
    )

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true', help='Anteprima senza salvataggio.')
        parser.add_argument(
            '--solo-fallback',
            action='store_true',
            help='Interviene solo su righe con fonte=custom_admin e note bootstrap fallback.',
        )

    def handle(self, *args, **options):
        dry_run = bool(options.get('dry_run'))
        solo_fallback = bool(options.get('solo_fallback'))

        mansioni = { _norm(m.nome): m for m in Mansione.objects.filter(attivo=True) }
        activate_count = 0
        deactivate_count = 0
        create_count = 0
        untouched = 0

        @transaction.atomic
        def _run():
            nonlocal activate_count, deactivate_count, create_count, untouched
            for mansione_key, livelli_allowed in DEFAULT_WHITELIST.items():
                mansione = mansioni.get(_norm(mansione_key))
                if not mansione:
                    continue

                qs = MansioneLivelloCCNL.objects.filter(mansione=mansione)
                if solo_fallback:
                    qs = qs.filter(fonte='custom_admin', note__icontains='bootstrap fallback')

                for rec in qs:
                    liv = str(rec.livello).strip()
                    is_allowed = liv in livelli_allowed
                    changed = False
                    if is_allowed:
                        if not rec.attivo:
                            rec.attivo = True
                            changed = True
                        if rec.fonte == 'custom_admin' and rec.priorita < 20:
                            rec.priorita = 20
                            changed = True
                        if changed:
                            rec.save(update_fields=['attivo', 'priorita', 'data_modifica'])
                            activate_count += 1
                        else:
                            untouched += 1
                    else:
                        if rec.attivo:
                            rec.attivo = False
                            rec.save(update_fields=['attivo', 'data_modifica'])
                            deactivate_count += 1
                        else:
                            untouched += 1

                # Assicura presenza combinazioni whitelist base (contesto generico vuoto)
                for liv in livelli_allowed:
                    _, created = MansioneLivelloCCNL.objects.get_or_create(
                        mansione=mansione,
                        livello=liv,
                        ccnl='',
                        versione='',
                        sezione='',
                        defaults={
                            'qualifica_tabellare': '',
                            'fonte': 'custom_admin',
                            'priorita': 30,
                            'attivo': True,
                            'note': 'Whitelist operativa iniziale (normalizzazione).',
                        },
                    )
                    if created:
                        create_count += 1

            if dry_run:
                transaction.set_rollback(True)

        _run()

        self.stdout.write(
            self.style.SUCCESS(
                f'Normalizzazione mappature: attivate={activate_count}, disattivate={deactivate_count}, create={create_count}, invariato={untouched}'
            )
        )
        if dry_run:
            self.stdout.write(self.style.WARNING('Dry-run attivo: nessuna modifica salvata.'))
