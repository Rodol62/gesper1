from django.core.management.base import BaseCommand
from django.db import transaction

from rapporto_di_lavoro.models import Mansione, MansioneLivelloCCNL, ParametroCCNLTurismo


def _norm(s):
    return ' '.join(str(s or '').strip().lower().replace('-', ' ').replace('/', ' ').split())


class Command(BaseCommand):
    help = (
        "Bootstrap mappature Mansione-Livello CCNL da ParametroCCNLTurismo "
        "(matching su nome mansione e qualifica tabellare)."
    )

    def add_arguments(self, parser):
        parser.add_argument('--ccnl', type=str, default='', help='Filtra ParametroCCNLTurismo.ccnl (icontains)')
        parser.add_argument('--versione', type=str, default='', help='Filtra ParametroCCNLTurismo.versione (iexact)')
        parser.add_argument('--sezione', type=str, default='', help='Filtra ParametroCCNLTurismo.sezione (iexact)')
        parser.add_argument(
            '--fallback-all-mansioni-per-livello',
            action='store_true',
            help='Se non trova match nome/qualifica, crea mappature di fallback per tutte le mansioni del livello.',
        )
        parser.add_argument('--dry-run', action='store_true', help='Mostra anteprima senza salvare')

    def handle(self, *args, **options):
        ccnl_filter = (options.get('ccnl') or '').strip()
        versione_filter = (options.get('versione') or '').strip()
        sezione_filter = (options.get('sezione') or '').strip()
        fallback_all = bool(options.get('fallback_all_mansioni_per_livello'))
        dry_run = bool(options.get('dry_run'))

        mansioni = list(Mansione.objects.filter(attivo=True).order_by('ordinamento', 'nome'))
        if not mansioni:
            self.stdout.write(self.style.WARNING('Nessuna mansione attiva trovata.'))
            return

        pqs = ParametroCCNLTurismo.objects.filter(attivo=True)
        if ccnl_filter:
            pqs = pqs.filter(ccnl__icontains=ccnl_filter)
        if versione_filter:
            pqs = pqs.filter(versione__iexact=versione_filter)
        if sezione_filter:
            pqs = pqs.filter(sezione__iexact=sezione_filter)
        parametri = list(pqs.order_by('ccnl', 'versione', 'sezione', 'livello_ordinamento', 'livello', 'qualifica'))
        if not parametri:
            self.stdout.write(self.style.WARNING('Nessun parametro CCNL trovato con i filtri indicati.'))
            return

        mansioni_by_name = {_norm(m.nome): m for m in mansioni}
        creati = 0
        gia_presenti = 0
        non_match = 0
        fallback_creati = 0
        fallback_aggiornati = 0

        @transaction.atomic
        def _run():
            nonlocal creati, gia_presenti, non_match, fallback_creati, fallback_aggiornati
            for p in parametri:
                qnorm = _norm(p.qualifica)
                mansione = mansioni_by_name.get(qnorm)
                if mansione is None:
                    # Fallback molto semplice per casi "Aiuto cameriere / Runner" ecc.
                    candidates = [m for m in mansioni if _norm(m.nome) in qnorm or qnorm in _norm(m.nome)]
                    mansione = candidates[0] if candidates else None
                if mansione is None:
                    non_match += 1
                    continue

                defaults = {
                    'qualifica_tabellare': p.qualifica or '',
                    'attivo': True,
                }
                obj, created = MansioneLivelloCCNL.objects.update_or_create(
                    mansione=mansione,
                    livello=str(p.livello).strip(),
                    ccnl=p.ccnl or '',
                    versione=p.versione or '',
                    sezione=p.sezione or '',
                    defaults=defaults,
                )
                _ = obj
                if created:
                    creati += 1
                else:
                    gia_presenti += 1

            if fallback_all and non_match > 0:
                livelli = sorted(
                    set(
                        str(x).strip()
                        for x in ParametroCCNLTurismo.objects.filter(attivo=True)
                        .values_list('livello', flat=True)
                        if str(x).strip()
                    )
                )
                for livello in livelli:
                    for m in mansioni:
                        obj, created = MansioneLivelloCCNL.objects.update_or_create(
                            mansione=m,
                            livello=livello,
                            ccnl='',
                            versione='',
                            sezione='',
                            defaults={
                                'qualifica_tabellare': '',
                                'attivo': True,
                                'fonte': 'custom_admin',
                                'priorita': 10,
                                'note': 'Bootstrap fallback automatico: da rifinire in admin.',
                            },
                        )
                        _ = obj
                        if created:
                            fallback_creati += 1
                        else:
                            fallback_aggiornati += 1

            if dry_run:
                transaction.set_rollback(True)

        _run()

        self.stdout.write(
            self.style.SUCCESS(
                f"Mappature elaborate: create={creati}, aggiornate/esistenti={gia_presenti}, non_collegate={non_match}"
            )
        )
        if fallback_all:
            self.stdout.write(
                self.style.WARNING(
                    f"Fallback livello->tutte mansioni: create={fallback_creati}, aggiornate/esistenti={fallback_aggiornati}"
                )
            )
        if dry_run:
            self.stdout.write(self.style.WARNING('Dry-run attivo: nessuna modifica salvata.'))
