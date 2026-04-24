from datetime import date
from decimal import Decimal

from django.test import TestCase

from .models import BonusFiscale, DetrazioneLavoroDipendente, PropostaAssunzione
from .utils_calcoli import calcola_detrazioni


class PropostaStatoPolicyTests(TestCase):
	def test_stati_equivalenti_includono_legacy(self):
		eq = PropostaAssunzione.stati_equivalenti('inviata_candidato')
		self.assertIn('inviata_candidato', eq)
		self.assertIn('inviata_al_dipendente', eq)

	def test_stato_canonico_su_istanza_non_persistita(self):
		p = PropostaAssunzione(stato='convertita_in_contratto')
		self.assertEqual(p.stato_canonico, 'contratto_attivo')


class FiscalTablesTests(TestCase):
	def test_calcola_detrazioni_usa_tabella_db(self):
		DetrazioneLavoroDipendente.objects.create(
			anno=2030,
			reddito_da=Decimal('0'),
			reddito_a=None,
			importo_base_annuo=Decimal('1200.00'),
			coefficiente_variabile_annuo=None,
			reddito_riferimento=None,
			divisore_fascia=None,
			data_validita_da=date(2030, 1, 1),
			data_validita_a=date(2030, 12, 31),
			attivo=True,
		)

		# 1200 annui -> 100 mensili
		self.assertEqual(calcola_detrazioni(1000, anno=2030), 100.00)

	def test_calcola_detrazioni_formula_variabile_db(self):
		DetrazioneLavoroDipendente.objects.create(
			anno=2031,
			reddito_da=Decimal('15000.01'),
			reddito_a=Decimal('28000.00'),
			importo_base_annuo=Decimal('1910.00'),
			coefficiente_variabile_annuo=Decimal('1190.00'),
			reddito_riferimento=Decimal('28000.00'),
			divisore_fascia=Decimal('13000.00'),
			data_validita_da=date(2031, 1, 1),
			data_validita_a=date(2031, 12, 31),
			attivo=True,
		)

		# imponibile mensile 2000 -> annuo 24000
		# det annua = 1910 + 1190*(28000-24000)/13000 = 2276.1538...
		# mensile = 189.68
		self.assertEqual(calcola_detrazioni(2000, anno=2031), 189.68)

	def test_bonus_fiscale_consente_stesso_codice_multi_anno(self):
		BonusFiscale.objects.create(
			codice='TI_MULTI',
			nome='TI test 2026',
			tipo='trattamento_integrativo',
			anno=2026,
			importo_mensile=Decimal('100.00'),
			data_validita_da=date(2026, 1, 1),
			data_validita_a=date(2026, 12, 31),
			attivo=True,
		)
		BonusFiscale.objects.create(
			codice='TI_MULTI',
			nome='TI test 2027',
			tipo='trattamento_integrativo',
			anno=2027,
			importo_mensile=Decimal('100.00'),
			data_validita_da=date(2027, 1, 1),
			data_validita_a=date(2027, 12, 31),
			attivo=True,
		)

		self.assertEqual(BonusFiscale.objects.filter(codice='TI_MULTI').count(), 2)
