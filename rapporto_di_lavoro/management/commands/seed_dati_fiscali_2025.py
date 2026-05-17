"""
Management command per popolare dati fiscali e bonus 2025:
- Scaglioni IRPEF 2025
- Bonus Fiscale Trattamento Integrativo DL 3/2020
- Bonus Art.1 L.207/2024
- EDS e EDB per CCNL Turismo Confcommercio
"""

from django.core.management.base import BaseCommand
from django.utils import timezone
from decimal import Decimal
from datetime import date

from rapporto_di_lavoro.models import (
	ScaglioneIRPEF,
	BonusFiscale,
	ParametroCCNLTurismo,
)


class Command(BaseCommand):
	help = 'Popola dati fiscali 2025: scaglioni IRPEF, bonus fiscali, EDS/EDB'

	def handle(self, *args, **options):
		self.stdout.write(self.style.SUCCESS('\n' + '='*60))
		self.stdout.write(self.style.SUCCESS('POPOLAMENTO DATI FISCALI 2025'))
		self.stdout.write(self.style.SUCCESS('='*60 + '\n'))

		self.popola_scaglioni_irpef_2025()
		self.popola_bonus_fiscali_2025()
		self.aggiorna_eds_edb_ccnl_turismo()

		self.stdout.write(self.style.SUCCESS('\n' + '='*60))
		self.stdout.write(self.style.SUCCESS('✅ POPOLAMENTO COMPLETATO'))
		self.stdout.write(self.style.SUCCESS('='*60 + '\n'))

	def popola_scaglioni_irpef_2025(self):
		"""Popola scaglioni IRPEF 2025 (validi anche per 2026)."""
		self.stdout.write('\n📊 Popolamento Scaglioni IRPEF 2025...')

		scaglioni = [
			{
				'anno': 2025,
				'scaglione_numero': 1,
				'reddito_da': Decimal('0'),
				'reddito_a': Decimal('28000'),
				'aliquota': Decimal('23.00'),
				'detrazione_base_annua': Decimal('1955.00'),
				'data_validita_da': date(2025, 1, 1),
				'data_validita_a': date(2026, 12, 31),
			},
			{
				'anno': 2025,
				'scaglione_numero': 2,
				'reddito_da': Decimal('28000.01'),
				'reddito_a': Decimal('50000'),
				'aliquota': Decimal('35.00'),
				'detrazione_base_annua': Decimal('1910.00'),
				'data_validita_da': date(2025, 1, 1),
				'data_validita_a': date(2026, 12, 31),
			},
			{
				'anno': 2025,
				'scaglione_numero': 3,
				'reddito_da': Decimal('50000.01'),
				'reddito_a': None,  # Infinito
				'aliquota': Decimal('43.00'),
				'detrazione_base_annua': Decimal('0'),
				'data_validita_da': date(2025, 1, 1),
				'data_validita_a': date(2026, 12, 31),
			},
		]

		creati = 0
		aggiornati = 0

		for s in scaglioni:
			obj, created = ScaglioneIRPEF.objects.update_or_create(
				anno=s['anno'],
				scaglione_numero=s['scaglione_numero'],
				defaults={
					'reddito_da': s['reddito_da'],
					'reddito_a': s['reddito_a'],
					'aliquota': s['aliquota'],
					'detrazione_base_annua': s['detrazione_base_annua'],
					'data_validita_da': s['data_validita_da'],
					'data_validita_a': s['data_validita_a'],
					'attivo': True,
				}
			)
			if created:
				creati += 1
				self.stdout.write(f"  ✅ Creato: Scaglione {s['scaglione_numero']} - {s['aliquota']}%")
			else:
				aggiornati += 1
				self.stdout.write(f"  ♻️  Aggiornato: Scaglione {s['scaglione_numero']} - {s['aliquota']}%")

		self.stdout.write(self.style.SUCCESS(f"\n  📈 Scaglioni IRPEF: {creati} creati, {aggiornati} aggiornati"))

	def popola_bonus_fiscali_2025(self):
		"""Popola bonus fiscali 2025."""
		self.stdout.write('\n💰 Popolamento Bonus Fiscali 2025...')

		bonus = [
			{
				'codice': 'TI_DL3_2020',
				'nome': 'Trattamento Integrativo DL 3/2020',
				'tipo': 'trattamento_integrativo',
				'anno': 2025,
				'importo_mensile': Decimal('101.92'),  # Da busta paga analizzata
				'importo_annuale': Decimal('1200.00'),  # 100€/mese * 12
				'soglia_reddito_min': None,
				'soglia_reddito_max': Decimal('15000'),  # Reddito complessivo < 15k
				'formula_calcolo': '',
				'contribuisce_imponibile': False,
				'contribuisce_irpef': False,
				'data_validita_da': date(2025, 1, 1),
				'data_validita_a': date(2025, 12, 31),
				'descrizione': 'Trattamento integrativo per redditi fino a 15.000€ annui (DL 3/2020 art.1)',
			},
			{
				'codice': 'BONUS_L207_2024',
				'nome': 'Bonus Art.1 Comma 4 L.207/2024',
				'tipo': 'bonus_art1_l207',
				'anno': 2025,
				'importo_mensile': Decimal('70.82'),  # Da busta paga analizzata
				'importo_annuale': Decimal('850.00'),  # Circa 70€/mese * 12
				'soglia_reddito_min': None,
				'soglia_reddito_max': Decimal('20000'),  # Ipotetico
				'formula_calcolo': '',
				'contribuisce_imponibile': False,
				'contribuisce_irpef': False,
				'data_validita_da': date(2024, 1, 1),
				'data_validita_a': date(2025, 12, 31),
				'descrizione': 'Bonus previsto da Legge 207/2024 Art.1 Comma 4',
			},
		]

		creati = 0
		aggiornati = 0

		for b in bonus:
			obj, created = BonusFiscale.objects.update_or_create(
				codice=b['codice'],
				anno=b['anno'],
				defaults={
					'nome': b['nome'],
					'tipo': b['tipo'],
					'importo_mensile': b['importo_mensile'],
					'importo_annuale': b['importo_annuale'],
					'soglia_reddito_min': b['soglia_reddito_min'],
					'soglia_reddito_max': b['soglia_reddito_max'],
					'formula_calcolo': b['formula_calcolo'],
					'contribuisce_imponibile': b['contribuisce_imponibile'],
					'contribuisce_irpef': b['contribuisce_irpef'],
					'data_validita_da': b['data_validita_da'],
					'data_validita_a': b['data_validita_a'],
					'descrizione': b['descrizione'],
					'attivo': True,
				}
			)
			if created:
				creati += 1
				self.stdout.write(f"  ✅ Creato: {b['nome']}")
			else:
				aggiornati += 1
				self.stdout.write(f"  ♻️  Aggiornato: {b['nome']}")

		self.stdout.write(self.style.SUCCESS(f"\n  💸 Bonus Fiscali: {creati} creati, {aggiornati} aggiornati"))

	def aggiorna_eds_edb_ccnl_turismo(self):
		"""Aggiorna EDS e EDB per CCNL Turismo Confcommercio."""
		self.stdout.write('\n🏥 Aggiornamento EDS/EDB per CCNL Turismo...')

		# Valori da busta paga analizzata (orari):
		# EDS: 0,09302 €/h
		# EDB: 0,05386 €/h
		eds_orario = Decimal('0.09302')
		edb_orario = Decimal('0.05386')

		# Aggiorna tutti i record ParametroCCNLTurismo
		parametri = ParametroCCNLTurismo.objects.filter(
			ccnl__icontains='Turismo',
			attivo=True
		)

		aggiornati = 0
		for param in parametri:
			param.elemento_distinto_sanita = eds_orario
			param.elemento_distinto_bilateralita = edb_orario
			param.save(update_fields=['elemento_distinto_sanita', 'elemento_distinto_bilateralita'])
			aggiornati += 1

		if aggiornati > 0:
			self.stdout.write(self.style.SUCCESS(f"  ✅ Aggiornati {aggiornati} record ParametroCCNLTurismo"))
			self.stdout.write(f"    - EDS: €{eds_orario}/ora")
			self.stdout.write(f"    - EDB: €{edb_orario}/ora")
		else:
			self.stdout.write(self.style.WARNING("  ⚠️  Nessun record ParametroCCNLTurismo trovato"))

		self.stdout.write(self.style.SUCCESS(f"\n  🩺 EDS/EDB: {aggiornati} record aggiornati"))
