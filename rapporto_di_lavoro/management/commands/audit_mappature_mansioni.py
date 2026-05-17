from collections import defaultdict

from django.core.management.base import BaseCommand

from rapporto_di_lavoro.models import Mansione, MansioneLivelloCCNL, ParametroCCNLTurismo


class Command(BaseCommand):
    help = (
        "Audit qualità mappature mansione-livello CCNL: "
        "segnala copertura mansioni/livelli e fallback ancora attivi."
    )

    def handle(self, *args, **options):
        mansioni_attive = list(Mansione.objects.filter(attivo=True).order_by('ordinamento', 'nome'))
        livelli_attivi = sorted(
            {
                str(l).strip()
                for l in ParametroCCNLTurismo.objects.filter(attivo=True).values_list('livello', flat=True)
                if str(l).strip()
            }
        )

        mappature_attive = MansioneLivelloCCNL.objects.filter(attivo=True).select_related('mansione')
        per_mansione = defaultdict(set)
        per_livello = defaultdict(set)
        fallback_attivi = []

        for rec in mappature_attive:
            mn = rec.mansione.nome
            lv = str(rec.livello).strip()
            per_mansione[mn].add(lv)
            per_livello[lv].add(mn)
            if rec.fonte == 'custom_admin' and 'fallback' in (rec.note or '').lower():
                fallback_attivi.append(rec)

        mansioni_senza_copertura = [m.nome for m in mansioni_attive if not per_mansione.get(m.nome)]
        livelli_senza_copertura = [lv for lv in livelli_attivi if not per_livello.get(lv)]

        self.stdout.write(self.style.SUCCESS('=== AUDIT MAPPATURE MANSIONI ==='))
        self.stdout.write(f"Mansioni attive: {len(mansioni_attive)}")
        self.stdout.write(f"Livelli CCNL attivi: {len(livelli_attivi)}")
        self.stdout.write(f"Mappature attive: {mappature_attive.count()}")
        self.stdout.write(f"Fallback attivi residui: {len(fallback_attivi)}")

        if mansioni_senza_copertura:
            self.stdout.write(self.style.WARNING('\nMansioni senza alcuna mappatura attiva:'))
            for nome in mansioni_senza_copertura:
                self.stdout.write(f" - {nome}")

        if livelli_senza_copertura:
            self.stdout.write(self.style.WARNING('\nLivelli senza mansioni attive mappate:'))
            for lv in livelli_senza_copertura:
                self.stdout.write(f" - Livello {lv}")

        if fallback_attivi:
            self.stdout.write(self.style.WARNING('\nPrime 20 mappature fallback ancora attive (da rifinire):'))
            for rec in fallback_attivi[:20]:
                self.stdout.write(
                    f" - {rec.mansione.nome} -> L{rec.livello} (priorita={rec.priorita}, fonte={rec.fonte})"
                )

        if not mansioni_senza_copertura and not livelli_senza_copertura:
            self.stdout.write(self.style.SUCCESS('\nCopertura minima OK: tutte le mansioni/livelli hanno almeno una mappatura attiva.'))
