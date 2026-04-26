from datetime import date
from decimal import Decimal
from types import SimpleNamespace

from django.test import TestCase

from .models import BonusFiscale, DetrazioneLavoroDipendente, PropostaAssunzione
from .utils_calcoli import calcola_detrazioni
from .utils_motore_paga import calcola_busta_paga_mese


def _parametro_ccnl_test(**overrides):
    """Parametro finto per il motore (stessi campi usati da ``calcola_busta_paga_mese``)."""
    base = {
        'paga_base_mensile': Decimal('1720.00'),
        'minimo_tabellare': Decimal('0'),
        'contingenza_mensile': Decimal('344.00'),
        'edr_mensile': Decimal('86.00'),
        'indennita_mensile': Decimal('0'),
        'importo_lordo_mensile': Decimal('2500.00'),
        'ore_settimanali': Decimal('40'),
        'ore_mensili': Decimal('172'),
        'straordinario_diurno_maggiorazione': Decimal('15'),
        'straordinario_notturno_maggiorazione': Decimal('30'),
        'straordinario_festivo_maggiorazione': Decimal('30'),
        'scatto_importo': Decimal('0'),
    }
    base.update(overrides)
    return SimpleNamespace(**base)


class MotorePagaRetribuzioneOrariaTests(TestCase):
    """Retribuzione oraria di fatto = Σ (voce tabellare mensile ÷ divisore orario)."""

    def test_somma_voci_tabellari_div_172_tempo_pieno(self):
        cp = _parametro_ccnl_test()
        r = calcola_busta_paga_mese(
            parametro_ccnl=cp,
            tipo_contratto=None,
            anno=2026,
            mese=1,
            divisore_str='172',
            mensilita_contrattuale_piena=True,
            superminimo=Decimal('0'),
            scatto_anzianita=Decimal('0'),
            indennita_turno=Decimal('0'),
            indennita_extra=Decimal('0'),
            ccnl_obj=None,
        )
        self.assertEqual(r['oraria_tabellare_paga_base'], Decimal('10.0000'))
        self.assertEqual(r['oraria_tabellare_contingenza'], Decimal('2.0000'))
        self.assertEqual(r['oraria_tabellare_edr'], Decimal('0.5000'))
        self.assertEqual(r['retribuzione_oraria_di_fatto'], Decimal('12.5000'))
        self.assertEqual(r['paga_oraria'], Decimal('12.5000'))
        self.assertEqual(r['lordo_tabellare_ft_equiv'], Decimal('2150.00'))

    def test_part_time_moltiplica_voci_prima_del_divisore(self):
        cp = _parametro_ccnl_test()
        tipo_pt = SimpleNamespace(coefficiente_ore=Decimal('0.5'))
        r = calcola_busta_paga_mese(
            parametro_ccnl=cp,
            tipo_contratto=tipo_pt,
            anno=2026,
            mese=1,
            divisore_str='172',
            mensilita_contrattuale_piena=True,
            superminimo=Decimal('0'),
            scatto_anzianita=Decimal('0'),
            indennita_turno=Decimal('0'),
            indennita_extra=Decimal('0'),
            ccnl_obj=None,
        )
        self.assertEqual(r['oraria_tabellare_paga_base'], Decimal('5.0000'))
        self.assertEqual(r['oraria_tabellare_contingenza'], Decimal('1.0000'))
        self.assertEqual(r['oraria_tabellare_edr'], Decimal('0.2500'))
        self.assertEqual(r['retribuzione_oraria_di_fatto'], Decimal('6.2500'))
        self.assertEqual(r['paga_oraria'], Decimal('6.2500'))

    def test_straordinario_diurno_sulla_retribuzione_oraria_di_fatto(self):
        cp = _parametro_ccnl_test()
        r = calcola_busta_paga_mese(
            parametro_ccnl=cp,
            tipo_contratto=None,
            anno=2026,
            mese=1,
            divisore_str='172',
            ore_straord_diurno=Decimal('2'),
            mensilita_contrattuale_piena=True,
            superminimo=Decimal('0'),
            scatto_anzianita=Decimal('0'),
            indennita_turno=Decimal('0'),
            indennita_extra=Decimal('0'),
            ccnl_obj=None,
        )
        # 2 h × 12,50 €/h × (1 + 15% magg. CCNL diurno)
        self.assertEqual(r['paga_oraria'], Decimal('12.5000'))
        self.assertEqual(r['imp_sd'], Decimal('28.75'))


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
