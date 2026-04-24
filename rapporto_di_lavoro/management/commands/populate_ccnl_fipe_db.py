"""
Management command per popolare il database con i parametri CCNL FIPE.
Estrae i dati dal JSON e li inserisce nelle tabelle parametriche.
"""
import json
from django.core.management.base import BaseCommand
from django.utils import timezone
from datetime import date, datetime
from decimal import Decimal
from rapporto_di_lavoro.models import (
	CCNL, ParametroOrario, ParametroMaggiorazione, ParametroScattiAnnuali,
	ParametroContributi, ParametroRatei, ValidazioneOrario, TipoAssenza,
	DecontribuzioneParametro, FringeBenefitSoglia
)
import os


class Command(BaseCommand):
	help = 'Popola il database con i parametri CCNL FIPE dal JSON'

	def add_arguments(self, parser):
		parser.add_argument(
			'--json-path',
			type=str,
			default=None,
			help='Percorso al file JSON con i parametri FIPE (default: cerca automaticamente)',
		)
		parser.add_argument(
			'--clear',
			action='store_true',
			help='Cancella i dati FIPE esistenti prima di popolare',
		)

	def handle(self, *args, **options):
		verbosity = options['verbosity']
		
		# Se --clear, elimina i dati FIPE
		if options['clear']:
			self.stdout.write("Cancellando dati FIPE esistenti...")
			CCNL.objects.filter(sigla='FIPE').delete()
			self.stdout.write(self.style.SUCCESS("✓ Dati cancellati"))
		
		# Crea/ottiene il CCNL FIPE
		ccnl_fipe, created = CCNL.objects.get_or_create(
			sigla='FIPE',
			defaults={
				'nome': 'Turismo - FIPE (Ristoranti, Hotel, Bar)',
				'anno_inizio_validita': 2024,
				'anno_fine_validita': None,
				'orario_standard_settimanale': Decimal('40.00'),
				'mensilita': 14,
				'giorni_ferie_base': 26,
				'giorni_rol_base': 0,
				'descrizione': 'CCNL Turismo FIPE - Piccola Ristorazione',
				'attivo': True,
			}
		)
		
		if created:
			self.stdout.write(self.style.SUCCESS(f"✓ CCNL FIPE creato: {ccnl_fipe}"))
		else:
			self.stdout.write(f"✓ CCNL FIPE esistente: {ccnl_fipe}")
		
		# Popola parametri orario
		self._populate_parametri_orario(ccnl_fipe)
		
		# Popola maggiorazioni
		self._populate_maggiorazioni(ccnl_fipe)
		
		# Popola scatti di anzianità
		self._populate_scatti(ccnl_fipe)
		
		# Popola contributi INPS/INAIL
		self._populate_contributi(ccnl_fipe)
		
		# Popola ratei (TFR, 13ª, 14ª)
		self._populate_ratei(ccnl_fipe)
		
		# Popola validazioni orario
		self._populate_validazioni_orario(ccnl_fipe)
		
		# Popola tipi assenza
		self._populate_tipi_assenza(ccnl_fipe)
		
		# Popola decontribuzioni (template base)
		self._populate_decontribuzioni(ccnl_fipe)
		
		# Popola fringe benefit soglie
		self._populate_fringe_benefit(ccnl_fipe)
		
		self.stdout.write(self.style.SUCCESS("\n✓ Popolazione CCNL FIPE completata!"))

	def _populate_parametri_orario(self, ccnl):
		"""Popola ParametroOrario."""
		self.stdout.write("\n📋 Parametri Orario...")
		
		params = [
			# Full-time
			('giornaliero', 'full_time', Decimal('3'), Decimal('13'), '3-13 ore giornaliere'),
			('settimanale', 'full_time', Decimal('38'), Decimal('48'), '38-48 ore settimanali'),
			('mensile', 'full_time', Decimal('165'), Decimal('220'), '165-220 ore mensili'),
			# Part-time
			('giornaliero', 'part_time', Decimal('3'), Decimal('10'), '3-10 ore giornaliere'),
			('settimanale', 'part_time', Decimal('16'), Decimal('30'), '16-30 ore settimanali'),
			('mensile', 'part_time', Decimal('80'), Decimal('165'), '80-165 ore mensili'),
			# Stagionale
			('giornaliero', 'stagionale', Decimal('3'), Decimal('13'), '3-13 ore giornaliere'),
			('settimanale', 'stagionale', Decimal('38'), Decimal('48'), '38-48 ore settimanali'),
		]
		
		for anno in [2024, 2025, 2026, 2027]:
			for tipo_cat, tipo_contr, vmin, vmax, desc in params:
				ParametroOrario.objects.get_or_create(
					ccnl=ccnl,
					anno=anno,
					tipo_categoria=tipo_cat,
					tipo_contratto=tipo_contr,
					defaults={
						'valore_minimo': vmin,
						'valore_massimo': vmax,
						'data_validita_da': date(anno, 1, 1),
						'data_validita_a': date(anno, 12, 31),
						'descrizione': desc,
						'attivo': True,
					}
				)
		
		self.stdout.write(self.style.SUCCESS("  ✓ Parametri orario"))

	def _populate_maggiorazioni(self, ccnl):
		"""Popola ParametroMaggiorazione."""
		self.stdout.write("📋 Maggiorazioni...")
		
		maggiorazioni = [
			('straordinario_feriale', Decimal('15.00')),
			('straordinario_festivo', Decimal('30.00')),
			('straordinario_domenicale', Decimal('60.00')),
			('straordinario_notturno', Decimal('30.00')),
			('straordinario_notturno_festivo', Decimal('50.00')),
			('lavoro_festivo', Decimal('20.00')),
			('lavoro_domenicale', Decimal('30.00')),
			('lavoro_notturno', Decimal('20.00')),
			('lavoro_supplementare_part_time', Decimal('15.00')),
		]
		
		for anno in [2024, 2025, 2026, 2027]:
			for tipo, perc in maggiorazioni:
				ParametroMaggiorazione.objects.get_or_create(
					ccnl=ccnl,
					anno=anno,
					tipo_maggiorazione=tipo,
					defaults={
						'percentuale': perc,
						'data_validita_da': date(anno, 1, 1),
						'data_validita_a': date(anno, 12, 31),
						'attivo': True,
					}
				)
		
		self.stdout.write(self.style.SUCCESS("  ✓ Maggiorazioni"))

	def _populate_scatti(self, ccnl):
		"""Popola ParametroScattiAnnuali."""
		self.stdout.write("📋 Scatti di Anzianità...")
		
		# Template: livello L5/L6, scatti ogni 36 mesi
		for anno in [2024, 2025, 2026, 2027]:
			for livello in ['L1', 'L2', 'L3', 'L4', 'L5', 'L6']:
				# Scatti a 3 anni, 6 anni, 9 anni, etc.
				for anni in [3, 6, 9, 12, 15]:
					importo = Decimal('50.00') * (Decimal(anni) / 3)  # Scala lineare
					ParametroScattiAnnuali.objects.get_or_create(
						ccnl=ccnl,
						anno=anno,
						livello=livello,
						anni_anzianita=anni,
						defaults={
							'importo_scatto': importo,
							'data_validita_da': date(anno, 1, 1),
							'data_validita_a': date(anno, 12, 31),
							'descrizione': f'Scatto {anni}° anno - {livello}',
							'attivo': True,
						}
					)
		
		self.stdout.write(self.style.SUCCESS("  ✓ Scatti di anzianità"))

	def _populate_contributi(self, ccnl):
		"""Popola ParametroContributi (INPS/INAIL)."""
		self.stdout.write("📋 Contributi INPS/INAIL...")
		
		# INPS Turismo - Piccola ristorazione (<15 dipendenti)
		inps_dati = [
			(2024, Decimal('32.64'), Decimal('9.19')),  # azienda, dipendente
			(2025, Decimal('32.64'), Decimal('9.19')),
			(2026, Decimal('32.64'), Decimal('9.19')),
			(2027, Decimal('32.64'), Decimal('9.19')),
		]
		
		for anno, aliq_az, aliq_dip in inps_dati:
			ParametroContributi.objects.get_or_create(
				ccnl=ccnl,
				anno=anno,
				tipo_contributo='inps',
				categoria='piccola_ristorazione',
				defaults={
					'aliquota_azienda': aliq_az,
					'aliquota_dipendente': aliq_dip,
					'data_validita_da': date(anno, 1, 1),
					'data_validita_a': date(anno, 12, 31),
					'descrizione': f'INPS Turismo {anno} - Aziende <15 dipendenti',
					'attivo': True,
				}
			)
		
		# INAIL Turismo
		inail_dati = [
			(2024, Decimal('1.20')),
			(2025, Decimal('1.20')),
			(2026, Decimal('1.20')),
			(2027, Decimal('1.20')),
		]
		
		for anno, tasso in inail_dati:
			ParametroContributi.objects.get_or_create(
				ccnl=ccnl,
				anno=anno,
				tipo_contributo='inail',
				categoria='piccola_ristorazione',
				defaults={
					'aliquota_azienda': tasso,
					'aliquota_dipendente': Decimal('0.00'),
					'data_validita_da': date(anno, 1, 1),
					'data_validita_a': date(anno, 12, 31),
					'descrizione': f'INAIL Turismo {anno} - Ristorazione',
					'attivo': True,
				}
			)
		
		self.stdout.write(self.style.SUCCESS("  ✓ Contributi"))

	def _populate_ratei(self, ccnl):
		"""Popola ParametroRatei (TFR, 13ª, 14ª)."""
		self.stdout.write("📋 Ratei (TFR, 13ª, 14ª)...")
		
		ratei_dati = [
			('tfr', Decimal('6.4100'), 'TFR standard 6.41%'),
			('tredicesima', Decimal('1.0000'), '13ª mensilità'),
			('quattordicesima', Decimal('1.0000'), '14ª mensilità'),
			('indennita_ferie', Decimal('1.0000'), 'Ferie non godute'),
		]
		
		for anno in [2024, 2025, 2026, 2027]:
			for tipo_rateo, coeff, desc in ratei_dati:
				ParametroRatei.objects.get_or_create(
					ccnl=ccnl,
					anno=anno,
					tipo_rateo=tipo_rateo,
					defaults={
						'coefficiente': coeff,
						'data_validita_da': date(anno, 1, 1),
						'data_validita_a': date(anno, 12, 31),
						'note': desc,
						'attivo': True,
					}
				)
		
		self.stdout.write(self.style.SUCCESS("  ✓ Ratei"))

	def _populate_validazioni_orario(self, ccnl):
		"""Popola ValidazioneOrario."""
		self.stdout.write("📋 Validazioni Orario...")
		
		validazioni = [
			('full_time', Decimal('3'), Decimal('13'), Decimal('38'), Decimal('48'), Decimal('165'), Decimal('220')),
			('part_time', Decimal('3'), Decimal('10'), Decimal('16'), Decimal('30'), Decimal('80'), Decimal('165')),
			('stagionale', Decimal('3'), Decimal('13'), Decimal('38'), Decimal('48'), None, None),
		]
		
		for anno in [2024, 2025, 2026, 2027]:
			for tipo_cat, min_g, max_g, min_s, max_s, min_m, max_m in validazioni:
				ValidazioneOrario.objects.get_or_create(
					ccnl=ccnl,
					anno=anno,
					tipo_categoria=tipo_cat,
					defaults={
						'min_ore_giornaliere': min_g,
						'max_ore_giornaliere': max_g,
						'min_ore_settimanali': min_s,
						'max_ore_settimanali': max_s,
						'min_ore_mensili': min_m,
						'max_ore_mensili': max_m,
						'data_validita_da': date(anno, 1, 1),
						'data_validita_a': date(anno, 12, 31),
						'attivo': True,
					}
				)
		
		self.stdout.write(self.style.SUCCESS("  ✓ Validazioni orario"))

	def _populate_tipi_assenza(self, ccnl):
		"""Popola TipoAssenza."""
		self.stdout.write("📋 Tipi Assenza...")
		
		assenze = [
			('malattia', True, Decimal('100'), 180),
			('infortunio', True, Decimal('100'), None),
			('maternita', True, Decimal('100'), 180),
			('paternita', True, Decimal('100'), 30),
			('congedo_parentale', True, Decimal('100'), 1000),
			('ferie', False, Decimal('100'), 26),
			('permesso_retribuito', False, Decimal('100'), 3),
			('permesso_non_retribuito', False, Decimal('0'), None),
			('104', True, Decimal('100'), None),
			('rol', False, Decimal('100'), None),
		]
		
		for anno in [2024, 2025, 2026, 2027]:
			for tipo, carica_inps, retrib_perc, giorni_max in assenze:
				TipoAssenza.objects.get_or_create(
					ccnl=ccnl,
					anno=anno,
					tipo_assenza=tipo,
					defaults={
						'carica_inps': carica_inps,
						'retribuzione_percentuale': retrib_perc,
						'giorni_max_anno': giorni_max,
						'data_validita_da': date(anno, 1, 1),
						'data_validita_a': date(anno, 12, 31),
						'attivo': True,
					}
				)
		
		self.stdout.write(self.style.SUCCESS("  ✓ Tipi assenza"))

	def _populate_decontribuzioni(self, ccnl):
		"""Popola DecontribuzioneParametro (template base)."""
		self.stdout.write("📋 Decontribuzioni...")
		
		decontrib = [
			('giovanile_under35', None, None, None, None, 18, 35, Decimal('100'), 10),
			('giovanile_under36', None, None, None, None, 18, 36, Decimal('100'), 9),
			('donne_svantaggiate', None, None, None, None, None, None, Decimal('50'), 8),
			('naspi', None, None, None, None, None, None, Decimal('50'), 7),
			('territoriale', 'sicilia', None, None, None, None, None, Decimal('20'), 5),
			('territoriale', 'sardegna', None, None, None, None, None, Decimal('20'), 5),
			('territoriale', 'calabria', None, None, None, None, None, Decimal('20'), 5),
			('territoriale', 'basilicata', None, None, None, None, None, Decimal('20'), 5),
			('nessuno', None, None, None, None, None, None, Decimal('0'), 0),
		]
		
		for anno in [2024, 2025, 2026, 2027]:
			for tipo, regione, prov, cat, tipo_contr, eta_min, eta_max, perc, priorita in decontrib:
				DecontribuzioneParametro.objects.get_or_create(
					ccnl=ccnl,
					anno=anno,
					tipo_incentivo=tipo,
					regione=regione,
					defaults={
						'provincia': prov,
						'categoria': cat,
						'tipo_contratto': tipo_contr,
						'eta_minima': eta_min,
						'eta_massima': eta_max,
						'percentuale_sconto': perc,
						'priorita': priorita,
						'data_validita_da': date(anno, 1, 1),
						'data_validita_a': date(anno, 12, 31),
						'attivo': True,
					}
				)
		
		self.stdout.write(self.style.SUCCESS("  ✓ Decontribuzioni"))

	def _populate_fringe_benefit(self, ccnl):
		"""Popola FringeBenefitSoglia."""
		self.stdout.write("📋 Fringe Benefit...")
		
		benefit = [
			('mensa', Decimal('3000.00')),
			('trasporto', Decimal('3000.00')),
			('buoni_pasto', Decimal('3000.00')),
			('carburante', Decimal('3000.00')),
			('altro', Decimal('3000.00')),
		]
		
		for anno in [2024, 2025, 2026, 2027]:
			for tipo, soglia in benefit:
				FringeBenefitSoglia.objects.get_or_create(
					ccnl=ccnl,
					anno=anno,
					tipo_benefit=tipo,
					defaults={
						'soglia_importo': soglia,
						'data_validita_da': date(anno, 1, 1),
						'data_validita_a': date(anno, 12, 31),
						'attivo': True,
					}
				)
		
		self.stdout.write(self.style.SUCCESS("  ✓ Fringe benefit"))
