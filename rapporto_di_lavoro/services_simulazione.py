from decimal import Decimal
import logging
import calendar
from datetime import date
from datetime import datetime

from django.utils import timezone

from .models import CCNL
from .utils_motore_paga import calcola_busta_paga_mese


logger = logging.getLogger(__name__)


def invoca_calcola_busta_paga_mese(*, log_prefix='BUSTA_PAGA', **kwargs):
	"""Wrapper unico attorno al motore busta: log strutturato e ri-lancio eccezioni."""
	try:
		return calcola_busta_paga_mese(**kwargs)
	except Exception:
		logger.exception(
			'[%s] Errore calcola_busta_paga_mese (parametro_ccnl=%s, anno=%s, mese=%s)',
			log_prefix,
			getattr(kwargs.get('parametro_ccnl'), 'id', None),
			kwargs.get('anno'),
			kwargs.get('mese'),
		)
		raise


def _safe_decimal(value):
	try:
		if value in (None, ''):
			return Decimal('0')
		return Decimal(str(value))
	except Exception:
		return Decimal('0')


def parse_iso_date(value):
	if not value:
		return None
	try:
		return datetime.strptime(value, '%Y-%m-%d').date()
	except ValueError:
		return None


def periodo_mese_da_riferimento(mese_riferimento):
	try:
		year_str, month_str = mese_riferimento.split('-')
		year = int(year_str)
		month = int(month_str)
	except Exception:
		today = timezone.localdate()
		year = today.year
		month = today.month
	last_day = calendar.monthrange(year, month)[1]
	inizio = date(year, month, 1)
	fine = date(year, month, last_day)
	return inizio, fine, last_day


def calcola_giorni_attivi_mese(inizio_mese, fine_mese, data_inizio, data_fine):
	in_eff = data_inizio or inizio_mese
	fi_eff = data_fine or fine_mese
	if fi_eff < in_eff:
		return 0
	inizio_overlap = max(inizio_mese, in_eff)
	fine_overlap = min(fine_mese, fi_eff)
	if fine_overlap < inizio_overlap:
		return 0
	return (fine_overlap - inizio_overlap).days + 1


def parse_giorni_chiusura_mese(request_obj, mese_riferimento):
	"""Legge i giorni di chiusura (YYYY-MM-DD) e filtra sul mese corrente."""
	inizio_mese, fine_mese, _ = periodo_mese_da_riferimento(mese_riferimento)
	validi = []
	for raw in request_obj.GET.getlist('giorni_chiusura_mese'):
		d = parse_iso_date(raw)
		if d and inizio_mese <= d <= fine_mese:
			validi.append(d)
	return sorted(set(validi))


def calcola_ore_retribuite_contrattuali(
	*,
	base_oraria_mensile,
	giorni_lavorativi_mese,
	coeff_ore,
	coeff_periodo,
):
	"""Criterio unico ore retribuite: base mensile / giorni lavorativi."""
	base_oraria = _safe_decimal(base_oraria_mensile)
	if base_oraria <= 0:
		base_oraria = Decimal('173.33')

	giorni_lavorativi = _safe_decimal(giorni_lavorativi_mese)
	if giorni_lavorativi <= 0:
		giorni_lavorativi = Decimal('26')

	coeff_pt = _safe_decimal(coeff_ore)
	if coeff_pt <= 0:
		coeff_pt = Decimal('1.00')

	coeff_p = _safe_decimal(coeff_periodo)
	if coeff_p <= 0:
		coeff_p = Decimal('0.00')

	ore_giornaliere_full_time = (base_oraria / giorni_lavorativi).quantize(Decimal('0.0001'))
	ore_giornaliere_retribuite = (ore_giornaliere_full_time * coeff_pt).quantize(Decimal('0.0001'))
	giorni_retribuiti = (giorni_lavorativi * coeff_p).quantize(Decimal('0.0001'))
	ore_mensili_retribuite = (ore_giornaliere_retribuite * giorni_retribuiti).quantize(Decimal('0.01'))

	return {
		'ore_giornaliere_full_time': ore_giornaliere_full_time,
		'ore_giornaliere_retribuite': ore_giornaliere_retribuite,
		'giorni_retribuiti': giorni_retribuiti,
		'ore_mensili_retribuite': ore_mensili_retribuite,
	}


def calcola_paga_oraria_contrattuale(
	*,
	parametro,
	ruolo,
	coeff_ore,
	coeff_periodo,
	ore_mensili_unit,
	giorni_lavorativi_mese,
):
	"""Calcola la paga oraria contrattuale dalle voci retributive del livello."""
	paga_base = _safe_decimal(getattr(parametro, 'paga_base_mensile', 0)).quantize(Decimal('0.01'))
	contingenza = _safe_decimal(getattr(parametro, 'contingenza_mensile', 0)).quantize(Decimal('0.01'))

	superminimo_ruolo = ruolo.get('superminimo_mensile', ruolo.get('superminimo')) if isinstance(ruolo, dict) else None
	superminimo = _safe_decimal(superminimo_ruolo if superminimo_ruolo not in (None, '') else getattr(parametro, 'superminimo_mensile', 0)).quantize(Decimal('0.01'))

	scatto_importo = _safe_decimal(getattr(parametro, 'scatto_importo', 0)).quantize(Decimal('0.01'))
	numero_scatti_ruolo = 0
	if isinstance(ruolo, dict):
		numero_scatti_ruolo = int(_safe_decimal(ruolo.get('numero_scatti', ruolo.get('scatti_anzianita', 0))))
	if numero_scatti_ruolo <= 0 and scatto_importo > 0:
		numero_scatti_ruolo = 1
	scatti_anzianita = (scatto_importo * Decimal(str(numero_scatti_ruolo))).quantize(Decimal('0.01'))

	el_dis_san_oraria_ruolo = ruolo.get('el_dis_san_oraria', ruolo.get('elemento_distinto_sanita')) if isinstance(ruolo, dict) else None
	el_dis_san_oraria = _safe_decimal(
		el_dis_san_oraria_ruolo if el_dis_san_oraria_ruolo not in (None, '') else getattr(parametro, 'elemento_distinto_sanita', 0)
	).quantize(Decimal('0.0001'))

	coeff_part_time = _safe_decimal(coeff_ore)
	if coeff_part_time <= 0:
		coeff_part_time = Decimal('1.00')
	coeff_periodo = _safe_decimal(coeff_periodo)
	if coeff_periodo <= 0:
		coeff_periodo = Decimal('1.00')

	ore_divisore = _safe_decimal(ore_mensili_unit)
	if ore_divisore <= 0:
		ore_giornaliere = _safe_decimal(getattr(parametro, 'ore_giornaliere', 0))
		ore_divisore = (ore_giornaliere * _safe_decimal(giorni_lavorativi_mese) * coeff_part_time * coeff_periodo).quantize(Decimal('0.01'))

	componenti_mensili = (paga_base + contingenza + superminimo + scatti_anzianita).quantize(Decimal('0.01'))
	componenti_rapportate = (componenti_mensili * coeff_part_time * coeff_periodo).quantize(Decimal('0.01'))
	el_dis_san_mensile = (el_dis_san_oraria * ore_divisore).quantize(Decimal('0.01')) if el_dis_san_oraria > 0 else Decimal('0.00')
	totale_contrattuale = (componenti_rapportate + el_dis_san_mensile).quantize(Decimal('0.01'))

	paga_oraria = Decimal('0.0000')
	if ore_divisore > 0:
		paga_oraria = (totale_contrattuale / ore_divisore).quantize(Decimal('0.0001'))

	return {
		'paga_oraria': paga_oraria,
		'totale_lavoro_ordinario': totale_contrattuale,
		'componenti': {
			'paga_base': paga_base,
			'contingenza': contingenza,
			'superminimo': superminimo,
			'scatti_anzianita': scatti_anzianita,
			'el_dis_san_oraria': el_dis_san_oraria,
			'el_dis_san_mensile': el_dis_san_mensile,
			'coeff_part_time_percent': (coeff_part_time * Decimal('100')).quantize(Decimal('0.01')),
		},
	}


def calcola_base_simulazione_motore_unico(
	*,
	parametro,
	tipo_contratto,
	anno,
	mese,
	azienda,
	data_inizio,
	data_fine,
	lordo_fallback,
):
	"""
	Facciata simulazioni organico / viste: **solo** motore busta paga canonico
	(:func:`calcola_busta_paga_mese` via :func:`invoca_calcola_busta_paga_mese`).

	Nessun fallback su ``utils_calcoli.calcola_completo`` — in caso di errore
	sale l'eccezione dopo il log, così non si mescolano due modelli di calcolo.

	``lordo_fallback`` resta solo per compatibilità firma chiamanti storici; non
	viene più usato per calcoli alternativi.
	"""
	_ = lordo_fallback
	ccnl_fipe = CCNL.objects.filter(sigla='FIPE').first()
	divisore = str(round(float(parametro.ore_mensili))) if getattr(parametro, 'ore_mensili', None) else '26'
	simulazione = invoca_calcola_busta_paga_mese(
		log_prefix='SIMULAZIONE_BASE',
		parametro_ccnl=parametro,
		tipo_contratto=tipo_contratto,
		anno=anno,
		mese=mese,
		azienda=azienda,
		data_inizio_rapporto=data_inizio,
		data_fine_rapporto=data_fine,
		divisore_str=divisore,
		ccnl_obj=ccnl_fipe,
		num_familiari_a_carico=0,
		regione_residenza='Sicilia',
		rateo_13_mensile_in_imponibile=False,
		rateo_14_mensile_in_imponibile=False,
	)
	return {
		'lordo_mensile': simulazione.get('lordo_mensile', Decimal('0.00')),
		'netto': {
			'netto': simulazione.get('netto_totale', Decimal('0.00')),
			'inps_dipendente': simulazione.get('inps_dip', Decimal('0.00')),
			'irpef_lorda': simulazione.get('irpef_lorda', Decimal('0.00')),
			'detrazioni': simulazione.get('detrazioni', Decimal('0.00')),
			'irpef_netta': simulazione.get('irpef_netta', Decimal('0.00')),
		},
		'costo_azienda': {
			'inps_azienda': simulazione.get('inps_az', Decimal('0.00')),
			'tfr': simulazione.get('tfr_m', Decimal('0.00')),
			'rateo_13': simulazione.get('rat13_m', Decimal('0.00')),
			'rateo_14': simulazione.get('rat14_m', Decimal('0.00')),
		},
	}
