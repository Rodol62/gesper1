from datetime import date
from decimal import Decimal
from types import SimpleNamespace

from django.contrib.auth import get_user_model
from django.test import Client, TestCase

from .models import BonusFiscale, DetrazioneLavoroDipendente, PropostaAssunzione
from .utils_calcoli import calcola_detrazioni, calcola_bonus_l207_2024
from .motore_paga_roel import costruisci_competenze_logica_v1, roel_tabellare_euro_oraria
from .utils_motore_paga import calcola_busta_paga_mese, ccnl_fipe_edr_assorbito_in_contingenza
from .utils_calendario import get_giorni_lavorativi_mese, count_giorni_ordinari_calendario


def _parametro_ccnl_test(**overrides):
    """Parametro finto per il motore (stessi campi usati da ``calcola_busta_paga_mese``)."""
    base = {
        'ccnl': '',
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
        'totale_tabellare': Decimal('0'),
    }
    base.update(overrides)
    return SimpleNamespace(**base)


class MotorePagaRetribuzioneOrariaTests(TestCase):
    """Retribuzione oraria di fatto = Σ voci tabellari FT ÷ ore contrattuali (172/173,33), incl. EDR se distinto,
    scatti, superminimo, EL.DIS.SAN/BIL; indennità di funzione resta fuori."""

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
        cont = next(v for v in r['voci_classificate'] if v['codice'] == 'CONTINGENZA')
        self.assertEqual(cont['descrizione'], 'Contingenza + EDR')
        self.assertEqual(cont['importo'], (r['contingenza'] + r['edr']).quantize(Decimal('0.01')))

    def test_forza_paga_oraria_sovrascrive_importo_ordinario(self):
        cp = _parametro_ccnl_test()
        rof = Decimal('20.0000')
        ore = Decimal('10')
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
            modalita_ore_effettive=True,
            ore_ordinarie_retribuite=ore,
            auto_ore_domenicali_da_calendario=False,
            forza_paga_oraria=rof,
        )
        self.assertEqual(r['paga_oraria'], rof)
        self.assertEqual(r['imp_ordinario_ore'], (ore * rof).quantize(Decimal('0.01')))

    def test_forza_addizionali_sostituiscono_mensilita_e_netto(self):
        cp = _parametro_ccnl_test()
        r0 = calcola_busta_paga_mese(
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
        r1 = calcola_busta_paga_mese(
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
            forza_add_reg_m=Decimal('10.00'),
            forza_add_com_m=Decimal('5.00'),
        )
        self.assertEqual(r1['add_reg_m'], Decimal('10.00'))
        self.assertEqual(r1['add_com_m'], Decimal('5.00'))
        self.assertEqual(
            r1['netto_totale'],
            (r0['netto_totale'] - r1['add_reg_m'] - r1['add_com_m']).quantize(Decimal('0.01')),
        )

    def test_fipe_non_include_edr_in_busta(self):
        """FIPE: EDR non è voce di cedolino — il motore azzera ``edr_mensile`` anche se valorizzato in tabella."""
        cp = _parametro_ccnl_test(ccnl='FIPE Pubblici Esercizi')
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
        self.assertEqual(r['edr'], Decimal('0'))
        self.assertEqual(r['oraria_tabellare_edr'], Decimal('0'))
        self.assertEqual(r['retribuzione_oraria_di_fatto'], Decimal('12.0000'))
        cont = next(v for v in r['voci_classificate'] if v['codice'] == 'CONTINGENZA')
        self.assertEqual(cont['descrizione'], 'Contingenza')
        self.assertEqual(cont['importo'], r['contingenza'])

    def test_part_time_orarie_tabellari_come_ft_excel_non_scalano_coeff(self):
        """Excel FIPE: ogni voce tabellare FT ÷ 172; il part-time non moltiplica l'importo prima del divisore."""
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
        self.assertEqual(r['oraria_tabellare_paga_base'], Decimal('10.0000'))
        self.assertEqual(r['oraria_tabellare_contingenza'], Decimal('2.0000'))
        self.assertEqual(r['oraria_tabellare_edr'], Decimal('0.5000'))
        self.assertEqual(r['retribuzione_oraria_di_fatto'], Decimal('12.5000'))
        self.assertEqual(r['paga_oraria'], Decimal('12.5000'))
        self.assertEqual(r['paga_base'], Decimal('860.00'))

    def test_superminimo_sm_ref_ft_oraria_effetto_part_time(self):
        """Sm_ref = mensilità superminimo a tempo pieno; €/h in rubrica = (Sm_ref × coeff) / 172."""
        cp = _parametro_ccnl_test(ccnl='FIPE Pubblici Esercizi')
        tipo_pt = SimpleNamespace(coefficiente_ore=Decimal('0.6'))
        sm_ref = (Decimal('4.26') * Decimal('172') / Decimal('0.6')).quantize(Decimal('0.01'))
        r0 = calcola_busta_paga_mese(
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
        r1 = calcola_busta_paga_mese(
            parametro_ccnl=cp,
            tipo_contratto=tipo_pt,
            anno=2026,
            mese=1,
            divisore_str='172',
            mensilita_contrattuale_piena=True,
            superminimo=sm_ref,
            scatto_anzianita=Decimal('0'),
            indennita_turno=Decimal('0'),
            indennita_extra=Decimal('0'),
            ccnl_obj=None,
        )
        self.assertEqual(r1['oraria_tabellare_superminimo'], Decimal('4.2600'))
        self.assertEqual(
            r1['retribuzione_oraria_di_fatto'],
            (r0['retribuzione_oraria_di_fatto'] + Decimal('4.2600')).quantize(Decimal('0.0001')),
        )
        self.assertEqual(r1['superminimo'], (sm_ref * Decimal('0.6')).quantize(Decimal('0.01')))

    def test_allineamento_excel_1021_49_522_37_scatto_90_percento(self):
        cp = _parametro_ccnl_test(
            paga_base_mensile=Decimal('1021.49'),
            contingenza_mensile=Decimal('522.37'),
            edr_mensile=Decimal('0'),
            indennita_mensile=Decimal('0'),
            scatto_importo=Decimal('32.54'),
        )
        tipo_pt = SimpleNamespace(coefficiente_ore=Decimal('0.9'))
        r = calcola_busta_paga_mese(
            parametro_ccnl=cp,
            tipo_contratto=tipo_pt,
            anno=2026,
            mese=1,
            divisore_str='172',
            ore_domenicali=Decimal('24'),
            mensilita_contrattuale_piena=True,
            superminimo=Decimal('0'),
            scatto_anzianita=Decimal('0'),
            indennita_turno=Decimal('0'),
            indennita_extra=Decimal('0'),
            ccnl_obj=None,
        )
        self.assertEqual(r['paga_base'], Decimal('919.34'))
        self.assertEqual(r['oraria_tabellare_paga_base'], Decimal('5.9389'))
        self.assertEqual(r['oraria_tabellare_contingenza'], Decimal('3.0370'))
        self.assertEqual(r['oraria_tabellare_scatto'], Decimal('0.1892'))
        self.assertEqual(r['retribuzione_oraria_di_fatto'], Decimal('9.1651'))
        self.assertEqual(r['paga_oraria'], Decimal('9.1651'))
        self.assertTrue(r['domenicale_compenso_completo'])
        self.assertEqual(
            r['imp_dom_magg'],
            (Decimal('24') * Decimal('9.1651') * Decimal('1.15')).quantize(Decimal('0.01')),
        )

    def test_domenicale_solo_maggiorazione_se_flag_esplicito(self):
        """Con ``domenicale_compenso_completo=False`` l’importo domenicale è solo la magg. su ROEL."""
        cp = _parametro_ccnl_test(
            paga_base_mensile=Decimal('1021.49'),
            contingenza_mensile=Decimal('522.37'),
            edr_mensile=Decimal('0'),
            indennita_mensile=Decimal('0'),
            scatto_importo=Decimal('32.54'),
        )
        tipo_pt = SimpleNamespace(coefficiente_ore=Decimal('0.9'))
        r = calcola_busta_paga_mese(
            parametro_ccnl=cp,
            tipo_contratto=tipo_pt,
            anno=2026,
            mese=1,
            divisore_str='172',
            ore_domenicali=Decimal('24'),
            domenicale_compenso_completo=False,
            mensilita_contrattuale_piena=True,
            superminimo=Decimal('0'),
            scatto_anzianita=Decimal('0'),
            indennita_turno=Decimal('0'),
            indennita_extra=Decimal('0'),
            ccnl_obj=None,
        )
        self.assertFalse(r['domenicale_compenso_completo'])
        self.assertEqual(
            r['imp_dom_magg'],
            (Decimal('24') * Decimal('9.1651') * Decimal('0.15')).quantize(Decimal('0.01')),
        )

    def test_totale_tabellare_ignorato_senza_scatto_importo_esplicito(self):
        """Il campo totale_tabellare a DB non sostituisce le voci né inventa lo scatto: serve scatto_importo."""
        cp = _parametro_ccnl_test(
            paga_base_mensile=Decimal('1021.49'),
            contingenza_mensile=Decimal('522.37'),
            edr_mensile=Decimal('0'),
            indennita_mensile=Decimal('0'),
            scatto_importo=Decimal('0'),
            totale_tabellare=Decimal('1576.40'),
        )
        tipo_pt = SimpleNamespace(coefficiente_ore=Decimal('0.9'))
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
        self.assertEqual(r['tabellare_gap_ft'], Decimal('0'))
        self.assertEqual(r['oraria_tabellare_scatto'], Decimal('0'))
        # Somma voci FT (frazione mese = 1); il part-time scala gli importi in busta, non le €/h tabellari.
        self.assertEqual(r['lordo_tabellare_ft_equiv'], Decimal('1543.86'))
        self.assertEqual(r['retribuzione_oraria_di_fatto'], Decimal('8.9759'))

    def test_minimo_tabellare_prioritario_per_euro_h_tab_div_172(self):
        """Se minimo_tabellare è valorizzato, le €/h tab. paga base usano quello (foglio INPS), non paga_base_mensile."""
        cp = _parametro_ccnl_test(
            paga_base_mensile=Decimal('1121.13'),
            minimo_tabellare=Decimal('1021.49'),
            contingenza_mensile=Decimal('522.37'),
            edr_mensile=Decimal('0'),
            indennita_mensile=Decimal('0'),
            scatto_importo=Decimal('32.54'),
        )
        tipo_pt = SimpleNamespace(coefficiente_ore=Decimal('0.9'))
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
        self.assertEqual(r['oraria_tabellare_paga_base'], Decimal('5.9389'))
        self.assertEqual(r['retribuzione_oraria_di_fatto'], Decimal('9.1651'))
        # 1021,49 + 522,37 + 32,54 (minimo + contingenza + scatto tabellare)
        self.assertEqual(r['lordo_tabellare_ft_equiv'], Decimal('1576.40'))

    def test_minimo_tabellare_maggiore_di_paga_base_non_gonfia_oraria_paga(self):
        """Minimo > paga base dichiarata: tipico totale/import errato in ``minimo_tabellare`` — €/h paga = paga base ÷ 172."""
        cp = _parametro_ccnl_test(
            paga_base_mensile=Decimal('1021.49'),
            minimo_tabellare=Decimal('1121.13'),
            contingenza_mensile=Decimal('522.37'),
            edr_mensile=Decimal('0'),
            indennita_mensile=Decimal('99.64'),
            scatto_importo=Decimal('32.54'),
        )
        tipo_pt = SimpleNamespace(coefficiente_ore=Decimal('0.9'))
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
        self.assertFalse(r['rof_usa_minimo_tabellare'])
        self.assertEqual(r['oraria_tabellare_paga_base'], Decimal('5.9389'))
        self.assertEqual(r['retribuzione_oraria_di_fatto'], Decimal('9.1651'))

    def test_fipe_minimo_uguale_paga_include_indennita_in_campo_paga_storna_per_div_172(self):
        """Produzione: paga e minimo entrambi 1121,13 (paga+ind tabellare) con indennità anche in colonna — FIPE storna l’ind. dal numeratore /172."""
        cp = _parametro_ccnl_test(
            ccnl='FIPE Pubblici Esercizi',
            paga_base_mensile=Decimal('1121.13'),
            minimo_tabellare=Decimal('1121.13'),
            contingenza_mensile=Decimal('522.37'),
            edr_mensile=Decimal('0'),
            indennita_mensile=Decimal('99.64'),
            scatto_importo=Decimal('32.54'),
        )
        tipo_pt = SimpleNamespace(coefficiente_ore=Decimal('0.9'))
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
        self.assertEqual(r['oraria_tabellare_paga_base'], Decimal('5.9389'))
        self.assertEqual(r['retribuzione_oraria_di_fatto'], Decimal('9.1651'))

    def test_confcommercio_non_storna_indennita_da_paga_tabellare(self):
        """Turismo Confcommercio: niente euristica FIPE — €/h paga = numeratore importato ÷ 172."""
        cp = _parametro_ccnl_test(
            ccnl='Turismo Confcommercio',
            paga_base_mensile=Decimal('1121.13'),
            minimo_tabellare=Decimal('1121.13'),
            contingenza_mensile=Decimal('522.37'),
            edr_mensile=Decimal('0'),
            indennita_mensile=Decimal('99.64'),
            scatto_importo=Decimal('32.54'),
        )
        tipo_pt = SimpleNamespace(coefficiente_ore=Decimal('0.9'))
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
        self.assertEqual(r['oraria_tabellare_paga_base'], Decimal('6.5182'))
        self.assertEqual(r['retribuzione_oraria_di_fatto'], Decimal('9.7444'))

    def test_modalita_ore_divisore_172_mantiene_rof_ft_su_presenze(self):
        """Con ore ordinarie da presenze e divisore 172: ROF = Σ FT÷172, imp. ord. = ore × ROF (non lordo_base÷ore_mensili)."""
        cp = _parametro_ccnl_test(
            paga_base_mensile=Decimal('1021.49'),
            contingenza_mensile=Decimal('522.37'),
            edr_mensile=Decimal('0'),
            indennita_mensile=Decimal('0'),
            scatto_importo=Decimal('32.54'),
        )
        tipo_pt = SimpleNamespace(coefficiente_ore=Decimal('0.9'))
        ore_o = Decimal('120')
        r = calcola_busta_paga_mese(
            parametro_ccnl=cp,
            tipo_contratto=tipo_pt,
            anno=2026,
            mese=1,
            divisore_str='172',
            ore_ordinarie_retribuite=ore_o,
            modalita_ore_effettive=True,
            mensilita_contrattuale_piena=True,
            superminimo=Decimal('0'),
            scatto_anzianita=Decimal('0'),
            indennita_turno=Decimal('0'),
            indennita_extra=Decimal('0'),
            ccnl_obj=None,
        )
        self.assertEqual(r['oraria_tabellare_paga_base'], Decimal('5.9389'))
        self.assertEqual(r['retribuzione_oraria_di_fatto'], Decimal('9.1651'))
        self.assertEqual(r['paga_oraria'], Decimal('9.1651'))
        self.assertEqual(
            r['imp_ordinario_ore'],
            (ore_o * Decimal('9.1651')).quantize(Decimal('0.01')),
        )
        self.assertEqual(
            r['oraria_ordinario_da_competenza'],
            (r['imp_ordinario_ore'] / ore_o).quantize(Decimal('0.0001')),
        )

    def test_modalita_ore_include_indennita_ccnl_in_lordo_imponibile(self):
        """Con ore effettive e divisore 172: l’indennità CCNL mensile è imponibile ma fuori dalla ROF €/h."""
        cp = _parametro_ccnl_test(indennita_mensile=Decimal('100.00'))
        ore_o = Decimal('10')
        r = calcola_busta_paga_mese(
            parametro_ccnl=cp,
            tipo_contratto=None,
            anno=2026,
            mese=1,
            divisore_str='172',
            ore_ordinarie_retribuite=ore_o,
            modalita_ore_effettive=True,
            mensilita_contrattuale_piena=True,
            superminimo=Decimal('0'),
            scatto_anzianita=Decimal('0'),
            indennita_turno=Decimal('0'),
            indennita_extra=Decimal('0'),
            ccnl_obj=None,
        )
        imp_o = (ore_o * r['retribuzione_oraria_di_fatto']).quantize(Decimal('0.01'))
        self.assertEqual(r['indennita'], Decimal('100.00'))
        self.assertEqual(r['lordo_mensile'], (imp_o + Decimal('100.00')).quantize(Decimal('0.01')))

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
        # 2 h × 12,50 €/h (paga+cont+EDR+scatti a 0) × (1 + 15% magg. CCNL diurno)
        self.assertEqual(r['paga_oraria'], Decimal('12.5000'))
        self.assertEqual(r['imp_sd'], Decimal('28.75'))

    def test_voci_classificate_include_el_dis_san_e_bil(self):
        """Le righe audit ``voci_classificate`` espongono EL.DIS.SAN / EL.DIS.BIL con importi mensili da parametro."""
        cp = _parametro_ccnl_test()
        cp.elemento_distinto_sanita = Decimal('0.10')
        cp.elemento_distinto_bilateralita = Decimal('0.05')
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
        by_cod = {v['codice']: v for v in r['voci_classificate']}
        self.assertIn('EL_DIS_SAN', by_cod)
        self.assertIn('EL_DIS_BIL', by_cod)
        # 0,10 €/h × 172 h × coeff 1 × frazione 1
        self.assertEqual(by_cod['EL_DIS_SAN']['importo'], Decimal('17.20'))
        self.assertEqual(by_cod['EL_DIS_BIL']['importo'], Decimal('8.60'))


class MotorePagaCompetenzeCedolinoTests(TestCase):
    """Rubrica ``competenze_logica_v1`` allineata a ``lordo_mensile`` (niente doppio superminimo con ROEL)."""

    def test_rubrica_no_riga_superminimo_se_gia_in_roel_ordinario(self):
        from .motore_paga_roel import costruisci_competenze_logica_v1

        cp = _parametro_ccnl_test()
        r = calcola_busta_paga_mese(
            parametro_ccnl=cp,
            tipo_contratto=None,
            anno=2026,
            mese=3,
            divisore_str='172',
            mensilita_contrattuale_piena=True,
            superminimo=Decimal('100.00'),
            scatto_anzianita=Decimal('0'),
            indennita_turno=Decimal('0'),
            indennita_extra=Decimal('0'),
            ccnl_obj=None,
        )
        self.assertGreater(Decimal(str(r.get('superminimo') or 0)), Decimal('0'))
        rub = costruisci_competenze_logica_v1(r)
        self.assertFalse(
            any((x.get('descrizione') or '').strip() == 'Superminimo' for x in rub),
            'Con gg ord. × h/gg × ROEL il superminimo è nella ROEL, non in riga duplicata',
        )
        sigma = next(x for x in rub if x.get('cod') == 'Σ')
        self.assertEqual(sigma['competenze'], r['lordo_mensile'])


class MotorePagaDetrazioniCedolinoTests(TestCase):
    """Con L207 in detrazione IRPEF (cedolino), ``detrazioni`` resta solo art. 13 (+ stima fam.); L207 è voce a parte."""

    def test_cedolino_l207_detrazione_non_gonfia_campo_detrazioni(self):
        cp = _parametro_ccnl_test()
        r = calcola_busta_paga_mese(
            parametro_ccnl=cp,
            tipo_contratto=None,
            anno=2026,
            mese=2,
            divisore_str='172',
            mensilita_contrattuale_piena=True,
            superminimo=Decimal('0'),
            scatto_anzianita=Decimal('0'),
            indennita_turno=Decimal('0'),
            indennita_extra=Decimal('0'),
            ccnl_obj=None,
            fiscale_modalita_cedolino=True,
            l207_percentuale_imponibile=Decimal('0.02'),
            l207_come_detrazione_irpef=True,
            ti_l207_non_cumulabili=True,
        )
        d_art13 = Decimal(str(calcola_detrazioni(
            float(r['imponibile_m']), anno=2026, num_familiari=0,
        ))).quantize(Decimal('0.01'))
        self.assertEqual(r['detrazioni'], d_art13)
        self.assertGreater(r['l207'], Decimal('0'))
        irpef_attesa = max(
            r['irpef_lorda'] - r['detrazioni'] - r['l207'],
            Decimal('0'),
        ).quantize(Decimal('0.01'))
        self.assertEqual(r['irpef_netta'], irpef_attesa)


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

	def test_calcola_bonus_l207_2026_fasce_percentuali_cedolino(self):
		"""Dal 2026 il bonus L207 segue fasce sul reddito annuo (es. ~58,85 €/m su ~13.324 €/anno)."""
		BonusFiscale.objects.update_or_create(
			codice='BONUS_L207_2024',
			anno=2026,
			defaults={
				'nome': 'Bonus L207 2026',
				'tipo': 'bonus_art1_l207',
				'importo_mensile': Decimal('70.82'),
				'soglia_reddito_max': Decimal('20000'),
				'data_validita_da': date(2026, 1, 1),
				'data_validita_a': date(2026, 12, 31),
				'attivo': True,
			},
		)
		self.assertEqual(calcola_bonus_l207_2024(Decimal('13324'), anno=2026), Decimal('58.85'))
		BonusFiscale.objects.update_or_create(
			codice='BONUS_L207_2024',
			anno=2025,
			defaults={
				'nome': 'Bonus L207 2025',
				'tipo': 'bonus_art1_l207',
				'importo_mensile': Decimal('70.82'),
				'soglia_reddito_min': Decimal('8500'),
				'soglia_reddito_max': Decimal('20000'),
				'data_validita_da': date(2025, 1, 1),
				'data_validita_a': date(2025, 12, 31),
				'attivo': True,
			},
		)
		self.assertEqual(calcola_bonus_l207_2024(Decimal('13324'), anno=2025), Decimal('70.82'))


class RoelTabellareEuroOrariaTests(TestCase):
    """``roel_tabellare_euro_oraria``: con divisore orario mai la vecchia ``retribuzione_oraria_di_fatto`` a 5 voci."""

    def test_divisore_172_somma_tre_voci_senza_fallback(self):
        r = {
            'divisore': Decimal('172'),
            'retribuzione_oraria_di_fatto': Decimal('9.7444'),
            'oraria_tabellare_paga_base': Decimal('5.9389'),
            'oraria_tabellare_contingenza': Decimal('3.0370'),
            'oraria_tabellare_scatto': Decimal('0.1892'),
        }
        self.assertEqual(roel_tabellare_euro_oraria(r), Decimal('9.1651'))

    def test_paga_base_assente_non_usa_retribuzione_obsoleta(self):
        r = {
            'divisore': Decimal('172'),
            'retribuzione_oraria_di_fatto': Decimal('9.7444'),
            'oraria_tabellare_contingenza': Decimal('3.0370'),
            'oraria_tabellare_scatto': Decimal('0.1892'),
        }
        self.assertEqual(roel_tabellare_euro_oraria(r), Decimal('3.2262'))


class CcnlFipeEdrAssorbitoFlagTests(TestCase):
    """``ccnl_fipe_edr_assorbito_in_contingenza`` — senza istanze CCNL reali (solo logica stringa/sigla)."""

    def test_fipe_in_stringa_ccnl_parametro(self):
        self.assertTrue(ccnl_fipe_edr_assorbito_in_contingenza(SimpleNamespace(ccnl='FIPE Pubblici Esercizi'), None))

    def test_pubblici_esercizi_in_stringa(self):
        self.assertTrue(ccnl_fipe_edr_assorbito_in_contingenza(SimpleNamespace(ccnl='Tabella Pubblici Esercizi'), None))

    def test_confcommercio_non_e_fipe(self):
        self.assertFalse(
            ccnl_fipe_edr_assorbito_in_contingenza(SimpleNamespace(ccnl='Turismo Confcommercio'), SimpleNamespace(sigla='FIPE')),
        )

    def test_stringa_vuota_ma_sigla_modello_fipe(self):
        self.assertTrue(ccnl_fipe_edr_assorbito_in_contingenza(SimpleNamespace(ccnl=''), SimpleNamespace(sigla='FIPE')))


class CountGiorniOrdinariCalendarioTests(TestCase):
    """Giorni ordinario = calendario senza dom./fest./chiusure; sabato solo con contratto 6 o 7 gg/sett."""

    def test_febbraio_2026_cinque_giorni_venti_gg(self):
        cd = get_giorni_lavorativi_mese(None, 2026, 2)
        self.assertEqual(cd['giorni_lavorativi'], 24)
        self.assertEqual(count_giorni_ordinari_calendario(2026, 2, cd, giorni_lavorativi_settimana=5), 20)
        self.assertEqual(count_giorni_ordinari_calendario(2026, 2, cd, giorni_lavorativi_settimana=6), 24)

    def test_aprile_2026_sei_giorni_esclude_festivi_weekday(self):
        cd = get_giorni_lavorativi_mese(None, 2026, 4)
        self.assertEqual(count_giorni_ordinari_calendario(2026, 4, cd, giorni_lavorativi_settimana=6), 24)

    def test_rubrica_febbraio_2026_part_time_5gg_ore_120(self):
        """Feb 2026: 20 gg ordinari (lun–ven) × 6 h/gg (36h sett. ÷ 6) = 120 h in rubrica."""
        cp = _parametro_ccnl_test()
        tc = SimpleNamespace(coefficiente_ore=Decimal('0.9'), giorni_lavorativi_settimana=5)
        r = calcola_busta_paga_mese(
            parametro_ccnl=cp,
            tipo_contratto=tc,
            anno=2026,
            mese=2,
            azienda=None,
            divisore_str='172',
            mensilita_contrattuale_piena=True,
            superminimo=Decimal('0'),
            scatto_anzianita=Decimal('0'),
            indennita_turno=Decimal('0'),
            indennita_extra=Decimal('0'),
            ccnl_obj=None,
        )
        self.assertEqual(r['cal_giorni_ordinari'], 20)
        self.assertEqual(r['cal_giorni_lavorativi'], 24)
        rows = costruisci_competenze_logica_v1(r)
        r1 = next(x for x in rows if x['cod'] == '1')
        self.assertEqual(r1['ore_o_gg'], Decimal('120.00'))


class RubricaCompetenzeLogicaV1Tests(TestCase):
    """``costruisci_competenze_logica_v1`` legge solo il dict risultato motore (niente ORM)."""

    def test_divisore_a_giorni_lista_vuota(self):
        self.assertEqual(costruisci_competenze_logica_v1({'divisore': Decimal('26')}), [])

    def test_modalita_ore_effettive_riga_ordinario(self):
        r = {
            'divisore': Decimal('172'),
            'retribuzione_oraria_di_fatto': Decimal('12.5'),
            'ore_ordinarie_retribuite': Decimal('86'),
            'modalita_ore_effettive': True,
            'imp_ordinario_ore': Decimal('1075.00'),
            'cal_giorni_lavorativi': 22,
            'ore_mensili': Decimal('172'),
            'oraria_tabellare_paga_base': Decimal('10'),
            'oraria_tabellare_contingenza': Decimal('2.5'),
        }
        rows = costruisci_competenze_logica_v1(r)
        r1 = next(x for x in rows if x['cod'] == '1')
        self.assertEqual(r1['base'], Decimal('12.5'))
        self.assertEqual(r1['competenze'], Decimal('1075.00'))
        roel = next(x for x in rows if x['cod'] == 'ROEL')
        self.assertEqual(roel['base'], Decimal('12.5'))

    def test_roel_somma_tre_voci_ignora_retribuzione_obsoleta(self):
        """La riga ROEL deve seguire paga+cont+scatto €/h, non una ROF vecchia che includeva EDR/ind."""
        r = {
            'divisore': Decimal('172'),
            'retribuzione_oraria_di_fatto': Decimal('9.7444'),
            'oraria_tabellare_paga_base': Decimal('5.9389'),
            'oraria_tabellare_contingenza': Decimal('3.0370'),
            'oraria_tabellare_scatto': Decimal('0.1892'),
            'ore_ordinarie_retribuite': Decimal('0'),
            'modalita_ore_effettive': False,
            'cal_giorni_lavorativi': 0,
            'ore_giornaliere': Decimal('0'),
            'ore_mensili': Decimal('172'),
        }
        rows = costruisci_competenze_logica_v1(r)
        roel_row = next(x for x in rows if x['cod'] == 'ROEL')
        self.assertEqual(roel_row['base'], Decimal('9.1651'))

    def test_mensilita_senza_modalita_usa_gg_lav_volte_ore_gg(self):
        r = {
            'divisore': Decimal('172'),
            'retribuzione_oraria_di_fatto': Decimal('12'),
            'ore_ordinarie_retribuite': Decimal('0'),
            'modalita_ore_effettive': False,
            'cal_giorni_lavorativi': 22,
            'cal_giorni_ordinari': 22,
            'ore_giornaliere': Decimal('5'),
            'ore_mensili': Decimal('110'),
            'paga_base': Decimal('1290.00'),
            'contingenza': Decimal('258.00'),
            'scatto': Decimal('0'),
            'edr': Decimal('0'),
            'indennita': Decimal('0'),
            'oraria_tabellare_paga_base': Decimal('10'),
            'oraria_tabellare_contingenza': Decimal('2'),
        }
        rows = costruisci_competenze_logica_v1(r)
        r1 = next(x for x in rows if x['cod'] == '1')
        self.assertEqual(r1['ore_o_gg'], Decimal('110.00'))
        self.assertEqual(r1['competenze'], Decimal('1320.00'))


class Simulazione2026DateNoneTests(TestCase):
    """Regressione: data_fine rapporto NULL (indeterminato) non deve causare TypeError in max/min date."""

    def test_giorni_attivi_con_data_fine_none(self):
        from datetime import date

        from rapporto_di_lavoro.views_simulazione_2026 import _calcola_giorni_attivi_nel_mese

        n = _calcola_giorni_attivi_nel_mese(2026, 1, date(2026, 1, 10), None)
        self.assertEqual(n, 22)

    def test_conta_mesi_ccnl_con_data_fine_none(self):
        from datetime import date

        from rapporto_di_lavoro.views_simulazione_2026 import _conta_mesi_ccnl

        c = _conta_mesi_ccnl(date(2026, 1, 1), None, date(2026, 1, 1), date(2026, 12, 31))
        self.assertEqual(c, 12)


class Simulazione2026RisultatoViewTests(TestCase):
    """Regressione: parametri form/query malformati non devono far esplodere la vista risultato."""

    def test_quantita_vuota_non_causa_500(self):
        u = get_user_model().objects.create_user(
            'u_sim2026_qta', 'sim@example.com', 'pw', is_superuser=True, is_staff=True
        )
        c = Client()
        c.force_login(u)
        r = c.get(
            '/rapporti/simulazione-annua/risultato/'
            '?ruolo_1=1&nome_1=Test&qta_1=&livello_1=1'
        )
        self.assertEqual(r.status_code, 200)

    def test_quantita_non_numerica_non_causa_500(self):
        u = get_user_model().objects.create_user(
            'u_sim2026_badq', 'sim2@example.com', 'pw', is_superuser=True, is_staff=True
        )
        c = Client()
        c.force_login(u)
        r = c.get(
            '/rapporti/simulazione-annua/risultato/'
            '?ruolo_1=1&nome_1=Test&qta_1=xyz&livello_1=1'
        )
        self.assertEqual(r.status_code, 200)


class BustaPagaLayoutCanonicoTests(TestCase):
    """Contratto sezioni/campi cedolino (allineamento motore → presentazione)."""

    def test_elenco_sezioni_e_griglia_ore(self):
        from .busta_paga_layout_canonico import (
            elenco_sezioni_con_campi,
            ids_campi_intestazione,
        )

        self.assertEqual(len(ids_campi_intestazione()), 21)
        sez = elenco_sezioni_con_campi()
        self.assertEqual(len(sez), 7)
        ore = next(x for x in sez if x[0].value == 'ore_lavorate')
        self.assertEqual(len(ore[1]), 31 + 6)

    def test_costruisci_riepilogo_simulatore_sezioni(self):
        from .busta_paga_layout_canonico import costruisci_riepilogo_simulatore_da_risultato

        r = {
            'anno': 2026,
            'mese': 3,
            'mese_nome': 'Marzo',
            'nome_test': 'Test scenario',
            'ccnl_qualifica': 'Impiegato',
            'ccnl_livello': '3',
            'coeff_ore': Decimal('1'),
            'cal_giorni_lavorativi': 22,
            'cal_giorni_ordinari': 21,
            'ore_mensili': Decimal('173.33'),
            'ore_giornaliere': Decimal('6.6667'),
            'giorni_nel_mese': 31,
            'paga_base': Decimal('100'),
            'contingenza': Decimal('20'),
            'scatto': Decimal('0'),
            'retribuzione_oraria_di_fatto': Decimal('12'),
            'paga_giornaliera': Decimal('80'),
            'lordo_mensile': Decimal('1500'),
            'lordo_imponibile_inps_m': Decimal('1400'),
            'inps_dip': Decimal('100'),
            'tot_contrib_dip': Decimal('100'),
            'imponibile_m': Decimal('1300'),
            'irpef_lorda': Decimal('200'),
            'detrazioni': Decimal('50'),
            'irpef_netta': Decimal('100'),
            'add_reg_m': Decimal('0'),
            'add_com_m': Decimal('0'),
            'netto_totale': Decimal('1100'),
            'rat13_n': Decimal('0'),
            'rat14_n': Decimal('0'),
            'rat13_m': Decimal('0'),
            'rat14_m': Decimal('0'),
            'lordo_con_1314': Decimal('1500'),
            'netto_mensile_con_1314': Decimal('1100'),
            'competenze_logica_v1': [
                {
                    'cod': '1',
                    'descrizione': 'Ordinario',
                    'ore_o_gg': '120 h',
                    'base': Decimal('12'),
                    'competenze': Decimal('1440'),
                    'trattenute': None,
                    'nota': '',
                },
            ],
            'voci': [],
            'cedolino_codice_fiscale': 'RSSMRA80A01H501U',
            'cedolino_comune_residenza': 'Palermo, PA',
            'cedolino_data_nascita': '15/03/1980',
            'cal_griglia': [
                [
                    None,
                    None,
                    None,
                    {
                        'giorno': 1,
                        'is_chiusura_extra': False,
                        'is_festivo': False,
                        'is_chiusura_sett': False,
                        'is_lavorativo': True,
                        'festivo_nome': '',
                    },
                    {
                        'giorno': 2,
                        'is_chiusura_extra': False,
                        'is_festivo': True,
                        'is_chiusura_sett': False,
                        'is_lavorativo': False,
                        'festivo_nome': 'Pasqua',
                    },
                ],
            ],
        }
        out = costruisci_riepilogo_simulatore_da_risultato(r)
        self.assertIn('intestazione_valori', out)
        self.assertEqual(out['intestazione_valori']['mese_riferimento'], 'Marzo 2026')
        self.assertEqual(out['intestazione_valori']['codice_fiscale'], 'RSSMRA80A01H501U')
        self.assertEqual(out['intestazione_valori']['comune_residenza'], 'Palermo, PA')
        self.assertEqual(out['intestazione_valori']['data_nascita'], '15/03/1980')
        self.assertEqual(len(out['righe_voce']), 1)
        self.assertEqual(out['righe_voce'][0]['codice'], '1')
        grid = out['ore_griglia_giorni']
        self.assertEqual(grid[0]['valore'], 'L')
        self.assertEqual(grid[1]['valore'], 'F')
        self.assertEqual(grid[1]['titolo'], 'Pasqua')

        r_feb = {**r, 'giorni_nel_mese': 28}
        out_f = costruisci_riepilogo_simulatore_da_risultato(r_feb)
        self.assertEqual(out_f['ore_griglia_giorni'][28]['valore'], '·')
        self.assertEqual(out_f['ore_griglia_giorni'][28]['giorno'], 29)

    def test_riepilogo_inail_usa_ore_posizione_inps_non_ore_mensili(self):
        from .busta_paga_layout_canonico import costruisci_riepilogo_simulatore_da_risultato

        r = {
            'anno': 2026,
            'mese_nome': 'Febbraio',
            'nome_test': 'Ore posizione',
            'coeff_ore': Decimal('0.6'),
            'ore_mensili': Decimal('103.20'),
            'ore_posizione_inps': Decimal('96.00'),
            'cal_giorni_lavorativi': 24,
            'giorni_lavorati': 24,
            'lordo_imponibile_inps_m': Decimal('1335.47'),
            'lordo_mensile': Decimal('1335.47'),
            'lordo_con_1314': Decimal('1335.47'),
            'inps_dip': Decimal('125'),
            'tot_contrib_dip': Decimal('125'),
            'imponibile_m': Decimal('1210.47'),
            'irpef_lorda': Decimal('278.41'),
            'detrazioni': Decimal('162.92'),
            'irpef_netta': Decimal('115.49'),
            'add_reg_m': Decimal('20.23'),
            'add_com_m': Decimal('11.27'),
            'netto_totale': Decimal('1100'),
            'rat13_n': Decimal('0'),
            'rat14_n': Decimal('0'),
            'rat13_m': Decimal('0'),
            'rat14_m': Decimal('0'),
            'netto_mensile_con_1314': Decimal('1100'),
            'competenze_logica_v1': [],
            'voci': [],
            'cal_griglia': [],
        }
        out = costruisci_riepilogo_simulatore_da_risultato(r)
        self.assertEqual(out['inail_valori']['ore_inps'], '96.00')
        self.assertEqual(out['inail_valori']['ore_inail'], '96.00')
        self.assertEqual(out['inail_valori']['giorni_inps'], '24')

    def test_intestazione_voci_tabellari_in_euro_ora_con_divisore_orario(self):
        from .busta_paga_layout_canonico import costruisci_riepilogo_simulatore_da_risultato

        r = {
            'anno': 2026,
            'mese_nome': 'Aprile',
            'nome_test': 'PT 90%',
            'coeff_ore': Decimal('0.9'),
            'divisore': Decimal('172'),
            'ore_mensili': Decimal('155.70'),
            'cal_giorni_lavorativi': 22,
            'ccnl_qualifica': 'Impiegato',
            'ccnl_livello': '4',
            'oraria_tabellare_paga_base': Decimal('5.9389'),
            'oraria_tabellare_contingenza': Decimal('3.0369'),
            'oraria_tabellare_scatto': Decimal('0.2500'),
            'retribuzione_oraria_di_fatto': Decimal('9.7444'),
            'paga_giornaliera': Decimal('58.00'),
            'competenze_logica_v1': [],
            'voci': [],
        }
        out = costruisci_riepilogo_simulatore_da_risultato(r)
        self.assertEqual(out['intestazione_valori']['paga_base'], '5.9389 €/h')
        self.assertEqual(out['intestazione_valori']['contingenza'], '3.0369 €/h')
        self.assertEqual(out['intestazione_valori']['scatti_anzianita'], '0.2500 €/h')
        self.assertEqual(out['intestazione_valori']['ore_contrattuali'], '172.00')
