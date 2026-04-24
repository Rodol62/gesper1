"""Chiude rapporti TD scaduti e cessa dipendenti senza altri contratti attivi."""

from django.core.management.base import BaseCommand

from anagrafiche.models import Azienda

from rapporto_di_lavoro.services_contratti import applica_cessazioni_td_scadute


class Command(BaseCommand):
	help = 'Imposta cessati i rapporti TD oltre termine e aggiorna dipendenti senza altri contratti attivi'

	def add_arguments(self, parser):
		parser.add_argument(
			'--dry-run',
			action='store_true',
			help='Conta i record interessati senza salvare',
		)
		parser.add_argument(
			'--azienda-id',
			type=int,
			default=None,
			help='Limita all\'azienda indicata (opzionale)',
		)

	def handle(self, *args, **options):
		az = None
		if options.get('azienda_id'):
			az = Azienda.objects.filter(pk=options['azienda_id']).first()
			if not az:
				self.stderr.write(self.style.ERROR('Azienda non trovata'))
				return
		res = applica_cessazioni_td_scadute(azienda=az, dry_run=bool(options.get('dry_run')))
		self.stdout.write(
			self.style.SUCCESS(
				f"Rapporti TD chiusi: {res['rapporti_chiusi']} | Dipendenti cessati: {res['dipendenti_cessati']}"
			)
		)
		if res['ids_rapporti']:
			self.stdout.write(f"ID rapporti: {res['ids_rapporti']}")
