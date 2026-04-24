from datetime import date, timedelta

from django.core.management.base import BaseCommand

from rapporto_di_lavoro.models import FestivitaCalendario


class Command(BaseCommand):
	help = 'Popola festività nazionali italiane + festività locale Palermo (Santa Rosalia) per un intervallo di anni.'

	def add_arguments(self, parser):
		parser.add_argument('--anno-inizio', type=int, default=2024)
		parser.add_argument('--anno-fine', type=int, default=2030)

	@staticmethod
	def _pasqua(anno):
		"""Algoritmo di Meeus/Jones/Butcher per data Pasqua (calendario gregoriano)."""
		a = anno % 19
		b = anno // 100
		c = anno % 100
		d = b // 4
		e = b % 4
		f = (b + 8) // 25
		g = (b - f + 1) // 3
		h = (19 * a + b - d - g + 15) % 30
		i = c // 4
		k = c % 4
		l = (32 + 2 * e + 2 * i - h - k) % 7
		m = (a + 11 * h + 22 * l) // 451
		mese = (h + l - 7 * m + 114) // 31
		giorno = ((h + l - 7 * m + 114) % 31) + 1
		return date(anno, mese, giorno)

	def handle(self, *args, **options):
		anno_inizio = options['anno_inizio']
		anno_fine = options['anno_fine']

		if anno_inizio > anno_fine:
			self.stdout.write(self.style.ERROR('Intervallo anni non valido.'))
			return

		nazionali_fisse = [
			(1, 1, 'Capodanno'),
			(1, 6, 'Epifania'),
			(4, 25, 'Anniversario della Liberazione'),
			(5, 1, 'Festa dei Lavoratori'),
			(6, 2, 'Festa della Repubblica'),
			(8, 15, 'Ferragosto'),
			(11, 1, 'Tutti i Santi'),
			(12, 8, 'Immacolata Concezione'),
			(12, 25, 'Natale'),
			(12, 26, 'Santo Stefano'),
		]

		created = 0
		updated = 0

		for anno in range(anno_inizio, anno_fine + 1):
			for mese, giorno, nome in nazionali_fisse:
				obj, is_created = FestivitaCalendario.objects.update_or_create(
					data=date(anno, mese, giorno),
					nome=nome,
					livello='nazionale',
					regione='',
					provincia='',
					comune='',
					defaults={'attivo': True},
				)
				created += int(is_created)
				updated += int(not is_created)

			pasqua = self._pasqua(anno)
			pasquetta = pasqua + timedelta(days=1)
			for data_fest, nome in [(pasqua, 'Pasqua'), (pasquetta, "Lunedì dell'Angelo")]:
				obj, is_created = FestivitaCalendario.objects.update_or_create(
					data=data_fest,
					nome=nome,
					livello='nazionale',
					regione='',
					provincia='',
					comune='',
					defaults={'attivo': True},
				)
				created += int(is_created)
				updated += int(not is_created)

			# Palermo (PA) - Santa Rosalia
			obj, is_created = FestivitaCalendario.objects.update_or_create(
				data=date(anno, 7, 15),
				nome='Santa Rosalia',
				livello='provinciale',
				regione='SICILIA',
				provincia='PA',
				comune='PALERMO',
				defaults={'attivo': True},
			)
			created += int(is_created)
			updated += int(not is_created)

		self.stdout.write(self.style.SUCCESS(
			f'Festività popolate. Create: {created} - Aggiornate: {updated} (anni {anno_inizio}-{anno_fine})'
		))
