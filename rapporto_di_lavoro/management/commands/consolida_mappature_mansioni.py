from collections import defaultdict

from django.core.management.base import BaseCommand
from django.db import transaction

from rapporto_di_lavoro.models import MansioneLivelloCCNL


def _is_fallback(rec):
    return rec.fonte == 'custom_admin' and 'fallback' in (rec.note or '').lower()


def _is_whitelist_seed(rec):
    return 'whitelist operativa iniziale' in (rec.note or '').lower()


class Command(BaseCommand):
    help = (
        'Consolida mappature mansione-livello: disattiva fallback bootstrap '
        'quando esistono regole migliori e promuove whitelist seed a standard.'
    )

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true', help='Anteprima senza salvataggio.')
        parser.add_argument(
            '--promuovi-whitelist',
            action='store_true',
            help='Promuove le regole whitelist seed a fonte=standard con priorita operativa.',
        )
        parser.add_argument(
            '--forza-standard-da-fallback',
            action='store_true',
            help='Per ogni fallback attivo crea/aggiorna una regola standard equivalente e disattiva il fallback.',
        )

    def handle(self, *args, **options):
        dry_run = bool(options.get('dry_run'))
        promuovi_whitelist = bool(options.get('promuovi_whitelist'))
        forza_standard = bool(options.get('forza_standard_da_fallback'))

        qs = MansioneLivelloCCNL.objects.filter(attivo=True).select_related('mansione').order_by('-priorita', '-data_modifica')
        per_coppia = defaultdict(list)
        for rec in qs:
            key = (rec.mansione_id, str(rec.livello).strip())
            per_coppia[key].append(rec)

        deactivated = 0
        promoted = 0
        untouched = 0

        @transaction.atomic
        def _run():
            nonlocal deactivated, promoted, untouched
            for _, records in per_coppia.items():
                has_stronger = any(
                    (not _is_fallback(r)) and (
                        r.fonte == 'standard' or r.priorita >= 60 or _is_whitelist_seed(r)
                    )
                    for r in records
                )
                for rec in records:
                    changed = False
                    if promuovi_whitelist and _is_whitelist_seed(rec):
                        if rec.fonte != 'standard':
                            rec.fonte = 'standard'
                            changed = True
                        if rec.priorita < 60:
                            rec.priorita = 60
                            changed = True
                        if changed:
                            rec.save(update_fields=['fonte', 'priorita', 'data_modifica'])
                            promoted += 1
                            continue

                    if has_stronger and _is_fallback(rec):
                        rec.attivo = False
                        rec.save(update_fields=['attivo', 'data_modifica'])
                        deactivated += 1
                    elif forza_standard and _is_fallback(rec):
                        std, _ = MansioneLivelloCCNL.objects.get_or_create(
                            mansione=rec.mansione,
                            livello=rec.livello,
                            ccnl=rec.ccnl,
                            versione=rec.versione,
                            sezione=rec.sezione,
                            defaults={
                                'qualifica_tabellare': rec.qualifica_tabellare,
                                'fonte': 'standard',
                                'priorita': max(rec.priorita, 60),
                                'attivo': True,
                                'note': 'Promossa automaticamente da fallback.',
                            },
                        )
                        changed_std = False
                        if std.fonte != 'standard':
                            std.fonte = 'standard'
                            changed_std = True
                        if std.priorita < 60:
                            std.priorita = 60
                            changed_std = True
                        if not std.attivo:
                            std.attivo = True
                            changed_std = True
                        if changed_std:
                            std.save(update_fields=['fonte', 'priorita', 'attivo', 'data_modifica'])
                        if std.id != rec.id:
                            rec.attivo = False
                            rec.save(update_fields=['attivo', 'data_modifica'])
                            deactivated += 1
                        promoted += 1
                    else:
                        untouched += 1

            if dry_run:
                transaction.set_rollback(True)

        _run()

        self.stdout.write(
            self.style.SUCCESS(
                f'Consolidamento mappature: fallback_disattivati={deactivated}, whitelist_promosse={promoted}, invariato={untouched}'
            )
        )
        if dry_run:
            self.stdout.write(self.style.WARNING('Dry-run attivo: nessuna modifica salvata.'))
