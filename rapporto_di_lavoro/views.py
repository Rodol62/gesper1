from django.views.decorators.http import require_http_methods
from django.views.decorators.clickjacking import xframe_options_sameorigin

from django.contrib import messages
from django.conf import settings
from django.contrib.auth.decorators import login_required, user_passes_test
from storico.models import EventoStorico
from django.core.paginator import Paginator
from django.http import HttpResponse, HttpResponseForbidden, JsonResponse, QueryDict, FileResponse, Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.db import transaction
from django.db.models.deletion import ProtectedError
from django.db.models import Q
from django.db.models import Model
from io import BytesIO
from decimal import Decimal
import os
import calendar
from datetime import date, datetime, timedelta
import json
import csv
import logging
from urllib.parse import urlencode
from io import StringIO
from types import SimpleNamespace

from reportlab.lib.pagesizes import A4, landscape
from reportlab.pdfgen import canvas
from reportlab.lib import colors
from reportlab.lib.colors import HexColor
from reportlab.lib.units import mm
from reportlab.lib.enums import TA_LEFT, TA_CENTER, TA_RIGHT, TA_JUSTIFY
from reportlab.lib.styles import ParagraphStyle
from reportlab.platypus import (
	SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,
	HRFlowable, KeepTogether,
)

from accounts.tenant import get_azienda_operativa
from gesper_next_url import sanitize_internal_next
from accounts.pagination import pagination_window
from accounts.formatting import euro_it_str, num_it_str
from anagrafiche.models import ComunicazioneRecessoProva, Dipendente
from .forms import (
	PropostaAssunzioneForm,
	IstruttoriaAssunzioneForm,
	ParametroCCNLTurismoForm,
	RegolaNormativaCCNLForm,
	RapportoDiLavoroForm,
	AddendumContrattualeForm,
	_descrizione_posizione_contrattuale,
)
from .models import (
	AddendumContrattuale,
	ParametroCCNLTurismo,
	PropostaAssunzione,
	RapportoDiLavoro,
	RuoloOrganico2026,
	TipoContratto,
	RegolaNormativaCCNL,
	SimulazioneOrganico,
	CCNL,
	ParametroOrario,
	ParametroMaggiorazione,
	ParametroScattiAnnuali,
	ParametroContributi,
	ParametroRatei,
	FestivitaCalendario,
	ChiusuraAziendale,
	CalendarioPresenzeDipendente,
	SimulazioneVoceRetributivaOre,
	VoceRetributiva,
)
from .utils_calcoli import (
	calcola_bonus_l207_2024,
	calcola_netto_dipendente,
	calcola_trattamento_integrativo,
)
from .utils_motore_paga import calcola_busta_paga_mese
from .parametro_ccnl_voci_retributive import carica_voci_retributive_da_tabella as _carica_voci_retributive_da_tabella
from .services_simulazione import (
	calcola_base_simulazione_motore_unico,
	calcola_giorni_attivi_mese,
	calcola_ore_retribuite_contrattuali,
	calcola_paga_oraria_contrattuale,
	parse_giorni_chiusura_mese,
	parse_iso_date,
	periodo_mese_da_riferimento,
)

try:
	from costo_lavoro import CostoLavoroAzienda, DatiContrattuali, RuleEngine
	from costo_lavoro.models.costi_eventuali import CostiEventuali
	from costo_lavoro.models.decontribuzioni import Decontribuzioni
	COSTO_LAVORO_ENABLED = True
except Exception:
	CostoLavoroAzienda = None
	DatiContrattuali = None
	RuleEngine = None
	CostiEventuali = None
	Decontribuzioni = None
	COSTO_LAVORO_ENABLED = False


logger = logging.getLogger(__name__)


def _get_azienda_operativa_per_utente(user, session):
	if user.is_superuser or getattr(user, 'ruolo', None) == 'admin':
		return get_azienda_operativa(user, session)
	if getattr(user, 'ruolo', None) == 'hr':
		return user.azienda
	return None


def _get_azienda_per_contratti_scadenze(user, session):
	"""Azienda per elenco scadenze TD: admin (sessione), HR e consulente (FK su utente)."""
	if user.is_superuser or getattr(user, 'ruolo', None) == 'admin':
		return get_azienda_operativa(user, session)
	if getattr(user, 'ruolo', None) == 'hr':
		return user.azienda
	if getattr(user, 'ruolo', None) == 'consulente':
		return getattr(user, 'azienda', None)
	return None


def _genera_numero_proposta():
	base = timezone.now().strftime('%Y%m%d%H%M%S')
	numero = f"PRP-{base}"
	index = 1
	while PropostaAssunzione.objects.filter(numero_proposta=numero).exists():
		numero = f"PRP-{base}-{index}"
		index += 1
	return numero


def _normalizza_ore_post_data(post_data):
	"""Normalizza i campi ore a 2 decimali prima della validazione form."""
	data = post_data.copy()
	for field in ('ore_settimanali', 'ore_mensili', 'ore_giornaliere'):
		raw = (data.get(field) or '').strip()
		if not raw:
			continue
		try:
			val = Decimal(raw.replace(',', '.')).quantize(Decimal('0.01'))
			data[field] = f'{val:.2f}'
		except Exception:
			# Lascia il valore originale: eventuale errore sarà gestito dal form.
			pass
	return data


def _eventi_documento_per_riferimenti(dipendente, azienda, *riferimenti, limite=20):
	filtri = Q()
	for riferimento in riferimenti:
		rif = str(riferimento or '').strip()
		if rif:
			filtri |= Q(descrizione__icontains=rif)
	qs = EventoStorico.objects.filter(dipendente=dipendente, azienda=azienda)
	if filtri:
		qs = qs.filter(filtri)
	return qs.order_by('-data_evento')[:limite]


def _autocompleta_retribuzione_proposta(proposta, azienda_operativa, preserve_manual=False):
	"""
	Ricalcola automaticamente tutte le voci retributive/normative della proposta,
	forzando l'allineamento al livello CCNL (con decorrenza corretta) e, per le
	proposte SIM2026, includendo le variabili del profilo ruolo (superminimo,
	indennità turno, scatti anzianità, extra).
	"""
	from .utils_motore_paga import ricava_parametri_proposta_contrattuale

	manual_data_inizio = proposta.data_inizio_rapporto
	manual_data_fine = proposta.data_fine_rapporto
	manual_tredicesima = proposta.tredicesima
	manual_quattordicesima = proposta.quattordicesima

	data_rif = proposta.data_inizio_rapporto or timezone.localdate()
	livello_rif = str(getattr(proposta, 'livello_ccnl', '') or '').strip()

	# 1) Parametro CCNL: priorità alla voce tabellare scelta (PK), poi fallback per livello.
	#    Prima veniva ignorato parametro_ccnl_id se livello_ccnl era valorizzato, scegliendo
	#    sempre la prima riga per livello — errato con più qualifiche sullo stesso livello.
	parametro = None
	if getattr(proposta, 'parametro_ccnl_id', None):
		cand = ParametroCCNLTurismo.objects.filter(id=proposta.parametro_ccnl_id, attivo=True).first()
		if cand:
			dec_ok = cand.decorrenza_validita_da is None or cand.decorrenza_validita_da <= data_rif
			liv_ok = (not livello_rif) or (str(cand.livello).strip() == livello_rif)
			if dec_ok and liv_ok:
				parametro = cand
	if parametro is None and livello_rif:
		parametro = (
			ParametroCCNLTurismo.objects.filter(
				livello=livello_rif,
				attivo=True,
				decorrenza_validita_da__lte=data_rif,
			)
			.order_by('-decorrenza_validita_da')
			.first()
		)
	if parametro is None and getattr(proposta, 'parametro_ccnl_id', None):
		parametro = ParametroCCNLTurismo.objects.filter(id=proposta.parametro_ccnl_id, attivo=True).first()

	if parametro is None:
		return

	proposta.parametro_ccnl = parametro
	proposta.livello_ccnl = parametro.livello
	if not (proposta.qualifica or '').strip():
		proposta.qualifica = parametro.qualifica

	# 2) Variabili extra da profilo SIM2026 (se numero proposta compatibile)
	superminimo = Decimal(str(getattr(proposta, 'superminimo_mensile', None) or 0))
	indennita_turno = Decimal('0')
	indennita_extra = Decimal('0')
	scatto_anzianita = Decimal('0')
	ccnl_obj = CCNL.objects.filter(sigla__icontains='FIPE').first()

	numero = str(getattr(proposta, 'numero_proposta', '') or '').strip()
	if numero.startswith('SIM2026-'):
		try:
			parts = numero.split('-')
			rid = int(parts[1]) if len(parts) >= 4 else None
			if rid is not None:
				ruolo = RuoloOrganico2026.objects.filter(
					azienda=proposta.azienda,
					ordinamento=max(rid - 1, 0),
				).first()
				if ruolo:
					superminimo = Decimal(str(ruolo.superminimo or 0))
					indennita_turno = Decimal(str(ruolo.indennita_turno or 0))
					if ruolo.premio_risultato_annuo:
						indennita_extra = (Decimal(str(ruolo.premio_risultato_annuo or 0)) / Decimal('12')).quantize(Decimal('0.01'))

					if ccnl_obj:
						anni = int(ruolo.anni_anzianita or 0)
						scatti = ParametroScattiAnnuali.objects.filter(
							ccnl=ccnl_obj,
							anno=data_rif.year,
							attivo=True,
							livello=str(parametro.livello),
						).order_by('anni_anzianita')
						scatto_anzianita = sum(
							(Decimal(str(s.importo_scatto or 0)) for s in scatti if int(s.anni_anzianita or 0) <= anni),
							Decimal('0'),
						)
					if ruolo.nome and not (proposta.posizione or '').strip():
						proposta.posizione = ruolo.nome
					if ruolo.data_inizio:
						data_rif = ruolo.data_inizio
						if not proposta.data_inizio_rapporto:
							proposta.data_inizio_rapporto = ruolo.data_inizio
		except Exception:
			logger.exception('Autocompletamento SIM2026 fallito per proposta %s', numero)

	# 3) Ricalcolo completo payload motore
	payload = ricava_parametri_proposta_contrattuale(
		parametro_ccnl=parametro,
		tipo_contratto=proposta.tipo_contratto,
		anno=data_rif.year,
		mese=data_rif.month,
		azienda=azienda_operativa,
		data_inizio_rapporto=proposta.data_inizio_rapporto,
		data_fine_rapporto=proposta.data_fine_rapporto,
		superminimo=superminimo,
		indennita_turno=indennita_turno,
		scatto_anzianita=scatto_anzianita,
		indennita_extra=indennita_extra,
		ccnl_obj=ccnl_obj,
	)

	for field, value in payload.items():
		if hasattr(proposta, field):
			setattr(proposta, field, value)

	if preserve_manual:
		# In creazione/modifica l'utente può impostare data rapporto e deroga ratei:
		# non devono essere sovrascritti dal payload automatico.
		proposta.data_inizio_rapporto = manual_data_inizio
		proposta.data_fine_rapporto = manual_data_fine
		proposta.tredicesima = bool(manual_tredicesima)
		proposta.quattordicesima = bool(manual_quattordicesima)

	# Coerenza minima per il nuovo motore: posizione/qualifica sempre valorizzate
	if not (proposta.qualifica or '').strip():
		proposta.qualifica = parametro.qualifica
	if not (proposta.posizione or '').strip():
		proposta.posizione = proposta.qualifica or parametro.qualifica

	# Stipendio lordo mensile = quanto entra in busta (tabellare + 1/12 se 13ª/14ª rateizzate).
	try:
		_ex = _proposta_context_extra(proposta)
		proposta.stipendio_lordo_mensile = _ex['lordo_mensile_totale']
	except Exception:
		logger.exception('Allineamento stipendio_lordo_mensile da Art. 5 fallito per proposta')


def _is_admin_like(user):
	return user.is_authenticated and (user.is_superuser or getattr(user, 'ruolo', None) in ['admin', 'hr'])


def _puo_accedere_centro_rapporti(user):
	"""Admin/HR come prima; consulente con azienda collegata (link dal dashboard consulente)."""
	if not user.is_authenticated:
		return False
	if user.is_superuser or user.has_ruolo('admin') or user.has_ruolo('hr'):
		return True
	return user.has_ruolo('consulente') and bool(getattr(user, 'azienda_id', None))


@login_required
@user_passes_test(_puo_accedere_centro_rapporti)
def centro_rapporti_lavoro(request):
	u = request.user
	if u.has_ruolo('admin') or u.is_superuser:
		azienda_operativa = get_azienda_operativa(u, request.session)
	else:
		azienda_operativa = getattr(u, 'azienda', None)

	base_qs = (
		ComunicazioneRecessoProva.objects.select_related('dipendente', 'rapporto', 'azienda')
		.order_by('-data_modifica')
	)
	if azienda_operativa:
		recesso_prova_recenti = base_qs.filter(azienda=azienda_operativa)[:50]
	elif u.is_superuser:
		recesso_prova_recenti = base_qs[:50]
	else:
		recesso_prova_recenti = ComunicazioneRecessoProva.objects.none()

	return render(
		request,
		'rapporto_di_lavoro/centro_rapporti_lavoro.html',
		{
			'azienda_operativa': azienda_operativa,
			'recesso_prova_recenti': recesso_prova_recenti,
		},
	)


def _is_admin_only(user):
	if not user.is_authenticated:
		return False
	if user.is_superuser:
		return True
	return getattr(user, 'has_ruolo', lambda _c: False)('admin')


def _get_mese_riferimento_request(request):
	"""Legge il mese da query supportando alias storici."""
	return (
		request.GET.get('mese_riferimento')
		or request.GET.get('periodo_riferimento')
		or timezone.localdate().strftime('%Y-%m')
	)


def _simulazione_config_session_key(azienda_operativa):
	azienda_id = getattr(azienda_operativa, 'id', 'default')
	return f"simulazione_organico_last_config_{azienda_id}"


def _serialize_querydict_for_session(querydict):
	return {k: list(vs) for k, vs in querydict.lists()}


def _deserialize_querydict_from_session(payload):
	q = QueryDict('', mutable=True)
	for k, vs in (payload or {}).items():
		if isinstance(vs, list):
			q.setlist(k, [str(x) for x in vs])
		elif vs is not None:
			q.setlist(k, [str(vs)])
	return q


def _get_proposta_con_permesso(request, proposta_id):
	proposta = get_object_or_404(PropostaAssunzione, id=proposta_id)

	if request.user.is_superuser or request.user.has_ruolo('admin') or request.user.has_ruolo('hr'):
		azienda_operativa = get_azienda_operativa(request.user, request.session) if request.user.has_ruolo('admin') else request.user.azienda
		if azienda_operativa and proposta.azienda_id != azienda_operativa.id:
			return None
		return proposta

	if request.user.has_ruolo('consulente'):
		azienda_consulente = getattr(request.user, 'azienda', None)
		if azienda_consulente and proposta.azienda_id == azienda_consulente.id:
			return proposta
		return None

	if (request.user.has_ruolo('dipendente') or request.user.has_ruolo('candidato')) and proposta.dipendente_id:
		dip = proposta.dipendente
		if dip.utente_id == request.user.id:
			return proposta
		# Fallback: dipendente collegato via ProfiloCandidato (utente non ancora impostato sul Dipendente)
		profilo = getattr(request.user, 'profilo_candidato', None)
		if profilo and profilo.dipendente_id and profilo.dipendente_id == proposta.dipendente_id:
			return proposta

	return None


def _proposta_attiva_per_dipendente(azienda, dipendente, exclude_id=None):
	"""Ultima proposta attiva per dipendente (esclude rifiutate/contratto attivo)."""
	qs = PropostaAssunzione.objects.filter(
		azienda=azienda,
		dipendente=dipendente,
	).exclude(
		stato__in=('rifiutata_candidato', 'rifiutata_dipendente', 'rifiutata_admin', 'contratto_attivo', 'convertita_in_contratto')
	)
	if exclude_id:
		qs = qs.exclude(id=exclude_id)
	return qs.order_by('-data_creazione').first()


def _get_contratto_con_permesso(request, contratto_id):
	contratto = get_object_or_404(RapportoDiLavoro, id=contratto_id)

	if request.user.is_superuser or (request.user.has_ruolo('admin') or request.user.has_ruolo('hr')):
		azienda_operativa = get_azienda_operativa(request.user, request.session) if request.user.has_ruolo('admin') else request.user.azienda
		if azienda_operativa and contratto.azienda_id != azienda_operativa.id:
			return None
		return contratto

	if (request.user.has_ruolo('dipendente') or request.user.has_ruolo('candidato')) and contratto.dipendente_id:
		dip = contratto.dipendente
		if dip.utente_id == request.user.id:
			return contratto
		# Fallback: dipendente collegato via ProfiloCandidato
		profilo = getattr(request.user, 'profilo_candidato', None)
		if profilo and profilo.dipendente_id and profilo.dipendente_id == contratto.dipendente_id:
			return contratto

	if request.user.has_ruolo('consulente'):
		az = getattr(request.user, 'azienda', None)
		if az and contratto.azienda_id == az.id:
			return contratto

	return None


@require_http_methods(['GET'])
@login_required
def api_ccnl_levels(request):
	"""API per ottenere i livelli disponibili da un CCNL selezionato."""
	parametro_id = request.GET.get('parametro_id')
	if not parametro_id:
		return JsonResponse({'errore': 'parametro_id mancante'}, status=400)
	
	try:
		base_param = ParametroCCNLTurismo.objects.get(id=parametro_id)
	except ParametroCCNLTurismo.DoesNotExist:
		return JsonResponse({'errore': 'Parametro CCNL non trovato'}, status=404)
	
	# Raccoglie tutti i livelli disponibili per la stessa sezione CCNL
	livelli = ParametroCCNLTurismo.objects.filter(
		ccnl=base_param.ccnl,
		versione=base_param.versione,
		sezione=base_param.sezione,
		attivo=True
	).values('id', 'livello', 'qualifica', 'importo_lordo_mensile').distinct()
	
	return JsonResponse({'livelli': list(livelli)})


@require_http_methods(['GET'])
@login_required
def api_ccnl_parametri(request):
	"""API per calcolare i valori economici/orari da livello + tipo_contratto selezionati.
	tipo_contratto_id è opzionale: se assente usa full-time (coeff=1.0).
	"""
	parametro_id = request.GET.get('parametro_id')
	tipo_contratto_id = request.GET.get('tipo_contratto_id')

	if not parametro_id:
		return JsonResponse({'errore': 'parametro_id richiesto'}, status=400)

	try:
		parametro = ParametroCCNLTurismo.objects.get(id=parametro_id, attivo=True)
	except ParametroCCNLTurismo.DoesNotExist:
		return JsonResponse({'errore': 'Parametro CCNL non trovato'}, status=404)

	tipo_contratto = None
	if tipo_contratto_id:
		try:
			tipo_contratto = TipoContratto.objects.get(id=tipo_contratto_id, attivo=True)
		except TipoContratto.DoesNotExist:
			return JsonResponse({'errore': 'Tipo contratto non trovato'}, status=404)
	# Se tipo_contratto non fornito, usa un tipo full-time come riferimento
	if tipo_contratto is None:
		tipo_contratto = TipoContratto.objects.filter(attivo=True, coefficiente_ore=1).order_by('id').first()
		if tipo_contratto is None:
			return JsonResponse({'errore': 'Nessun tipo contratto full-time trovato'}, status=500)

	azienda_operativa = _get_azienda_operativa_per_utente(request.user, request.session)
	anno_base = parametro.decorrenza_validita_da.year if parametro.decorrenza_validita_da else timezone.localdate().year
	anno_riferimento = _anno_riferimento_da_azienda(azienda_operativa, anno_base)
	
	# Regole normative opzionali per livello/versione (orario, ferie, permessi, scatti)
	regola = RegolaNormativaCCNL.objects.filter(
		ccnl=parametro.ccnl,
		versione=parametro.versione,
		sezione=parametro.sezione,
		livello=parametro.livello,
		attivo=True,
	).order_by('-decorrenza_validita_da').first()

	ore_settimanali_base = regola.ore_settimanali if regola else parametro.ore_settimanali
	ore_mensili_base = regola.ore_mensili if regola else parametro.ore_mensili
	ore_giornaliere_base = regola.ore_giornaliere if regola else parametro.ore_giornaliere
	scatto_periodicita_mesi = regola.scatto_periodicita_mesi if regola else parametro.scatto_periodicita_mesi
	scatto_importo = regola.scatto_importo if regola else parametro.scatto_importo
	numero_scatti_massimi = regola.numero_scatti_massimi if regola else parametro.numero_scatti_massimi
	ferie_annue_giorni = regola.ferie_annue_giorni if regola else Decimal('26')
	permessi_annui_ore = regola.permessi_annui_ore if regola else Decimal('72')

	# Override normativa da tabelle DB costo_lavoro (orari/ferie/permessi/scatti)
	normativa_db = _carica_regola_normativa_da_db(
		ccnl_label=parametro.ccnl,
		livello=parametro.livello,
		anno=anno_riferimento,
		coeff_ore=Decimal(str(tipo_contratto.coefficiente_ore)),
	)
	if normativa_db:
		ore_settimanali_base = normativa_db.get('ore_settimanali', ore_settimanali_base)
		ore_mensili_base = normativa_db.get('ore_mensili', ore_mensili_base)
		ore_giornaliere_base = normativa_db.get('ore_giornaliere', ore_giornaliere_base)
		ferie_annue_giorni = normativa_db.get('ferie_annue_giorni', ferie_annue_giorni)
		permessi_annui_ore = normativa_db.get('permessi_annui_ore', permessi_annui_ore)
		scatto_periodicita_mesi = normativa_db.get('scatto_periodicita_mesi') or scatto_periodicita_mesi
		scatto_importo = normativa_db.get('scatto_importo') or scatto_importo
		numero_scatti_massimi = normativa_db.get('numero_scatti_massimi') or numero_scatti_massimi

	# Calcola le ore mensili/giornaliere in base al tipo di contratto
	coeff = tipo_contratto.coefficiente_ore
	ore_settimanali = ore_settimanali_base * coeff
	ore_mensili = ore_mensili_base * coeff
	ore_giornaliere = ore_giornaliere_base * coeff

	# Aggancio nuovo stack costo_lavoro + tabelle DB parametrizzate
	override = _carica_parametri_tabellari_costo_lavoro(
		parametro=parametro,
		anno=anno_riferimento,
		coeff_ore=Decimal(str(coeff)),
	)

	stipendio_lordo = override.get('stipendio_lordo_mensile', Decimal(str(parametro.importo_lordo_mensile)))
	paga_base = override.get('paga_base_mensile', Decimal(str(parametro.paga_base_mensile)))
	contingenza = override.get('contingenza_mensile', Decimal(str(parametro.contingenza_mensile)))
	edr = override.get('edr_mensile', Decimal(str(parametro.edr_mensile)))
	indennita = override.get('indennita_mensile', Decimal(str(parametro.indennita_mensile)))
	ore_settimanali = override.get('ore_settimanali', ore_settimanali)
	ore_mensili = override.get('ore_mensili', ore_mensili)
	ore_giornaliere = override.get('ore_giornaliere', ore_giornaliere)
	scatto_periodicita_mesi = override.get('scatto_periodicita_mesi', scatto_periodicita_mesi)
	scatto_importo = override.get('scatto_importo', scatto_importo)
	numero_scatti_massimi = override.get('numero_scatti_massimi', numero_scatti_massimi)
	straord_diurno = override.get('straordinario_diurno_maggiorazione', Decimal(str(parametro.straordinario_diurno_maggiorazione)))
	straord_notturno = override.get('straordinario_notturno_maggiorazione', Decimal(str(parametro.straordinario_notturno_maggiorazione)))
	straord_festivo = override.get('straordinario_festivo_maggiorazione', Decimal(str(parametro.straordinario_festivo_maggiorazione)))

	# Importi tabellari full-time → proporziona alla % ore del tipo contratto (come motore paga / proposta salvata).
	coeff_dec = Decimal(str(tipo_contratto.coefficiente_ore or 1))
	stipendio_lordo = (stipendio_lordo * coeff_dec).quantize(Decimal('0.01'))
	paga_base = (paga_base * coeff_dec).quantize(Decimal('0.01'))
	contingenza = (contingenza * coeff_dec).quantize(Decimal('0.01'))
	edr = (edr * coeff_dec).quantize(Decimal('0.01'))
	indennita = (indennita * coeff_dec).quantize(Decimal('0.01'))
	scatto_importo = (scatto_importo * coeff_dec).quantize(Decimal('0.01'))

	# Tredicesima / quattordicesima: da ParametroRatei (anno allineato a tabella/righe DB)
	from .models import CCNL, ParametroRatei
	from .utils_motore_paga import anno_efficace_parametro_ratei
	_ccnl_obj = CCNL.objects.filter(sigla__icontains='FIPE').first()
	_anno_ratei = anno_efficace_parametro_ratei(_ccnl_obj, anno_riferimento, parametro)
	_ha_13 = _ccnl_obj and ParametroRatei.objects.filter(
		ccnl=_ccnl_obj, anno=_anno_ratei, tipo_rateo='tredicesima', attivo=True).exists()
	_ha_14 = _ccnl_obj and ParametroRatei.objects.filter(
		ccnl=_ccnl_obj, anno=_anno_ratei, tipo_rateo='quattordicesima', attivo=True).exists()

	# Giorni ferie e permessi: da regola normativa (già calcolata sopra come ferie_annue_giorni)
	_giorni_ferie = int(ferie_annue_giorni) if ferie_annue_giorni else 26
	_giorni_permesso = int(permessi_annui_ore // 8) if permessi_annui_ore else 3

	return JsonResponse({
		'livello': parametro.livello,
		'qualifica': parametro.qualifica,
		'stipendio_lordo_mensile': float(stipendio_lordo),
		'paga_base_mensile': float(paga_base),
		'contingenza_mensile': float(contingenza),
		'edr_mensile': float(edr),
		'indennita_mensile': float(indennita),
		'tredicesima': _ha_13,
		'quattordicesima': _ha_14,
		'giorni_ferie_annuali': _giorni_ferie,
		'giorni_permesso_annuali': _giorni_permesso,
		'ore_settimanali': float(ore_settimanali),
		'ore_mensili': float(ore_mensili),
		'ore_giornaliere': float(ore_giornaliere),
		'decorrenza_validita_da': parametro.decorrenza_validita_da.isoformat() if parametro.decorrenza_validita_da else None,
		'decorrenza_validita_a': parametro.decorrenza_validita_a.isoformat() if parametro.decorrenza_validita_a else None,
		'scatto_periodicita_mesi': scatto_periodicita_mesi,
		'scatto_importo': float(scatto_importo),
		'numero_scatti_massimi': numero_scatti_massimi,
		'ferie_annue_giorni': float(ferie_annue_giorni),
		'permessi_annui_ore': float(permessi_annui_ore),
		'straordinario_diurno_maggiorazione': float(straord_diurno),
		'straordinario_notturno_maggiorazione': float(straord_notturno),
		'straordinario_festivo_maggiorazione': float(straord_festivo),
		'riposi_compensativi_regola': parametro.riposi_compensativi_regola,
		'tipo_contratto_desc': tipo_contratto.nome,
		'coefficiente_ore': float(tipo_contratto.coefficiente_ore),
		'fonte_dati': 'costo_lavoro_db',
		'anno_riferimento': int(anno_riferimento),
	})


@require_http_methods(['GET'])
@login_required
def api_mansioni_per_livello(request):
	"""Qualifiche disponibili per lo stesso livello/CCNL/sezione del parametro selezionato.

	Preferire ``parametro_id`` (affidabile); ``livello`` + ``sezione`` restano per compatibilità.
	"""
	parametro_id = (request.GET.get('parametro_id') or '').strip()
	livello = (request.GET.get('livello') or '').strip()
	sezione = (request.GET.get('sezione') or '').strip() or 'ristoranti_pizzerie'

	if parametro_id:
		try:
			p = ParametroCCNLTurismo.objects.get(id=parametro_id, attivo=True)
		except ParametroCCNLTurismo.DoesNotExist:
			return JsonResponse({'errore': 'Parametro CCNL non trovato'}, status=404)
		qualifiche = list(
			ParametroCCNLTurismo.objects.filter(
				attivo=True,
				ccnl=p.ccnl,
				versione=p.versione,
				sezione=p.sezione,
				livello=p.livello,
			)
			.order_by('qualifica')
			.values_list('qualifica', flat=True)
			.distinct()
		)
		return JsonResponse({'mansioni': qualifiche})

	if not livello:
		return JsonResponse({'errore': 'livello richiesto'}, status=400)
	qualifiche = list(
		ParametroCCNLTurismo.objects.filter(attivo=True, livello=livello, sezione=sezione)
		.order_by('qualifica')
		.values_list('qualifica', flat=True)
		.distinct()
	)
	return JsonResponse({'mansioni': qualifiche})


def _build_ruoli_config(request):
	voce_defaults = {
		'minimo_tabellare': {'presente': True},
		'contingenza': {'presente': True},
		'el_dis_san': {'presente': False},
		'scatto_anzianita': {'presente': True},
		'superminimo': {'presente': False},
		'el_dis_bil': {'presente': False},
	}

	def _parse_presente(raw, default=False):
		v = str(raw if raw is not None else '').strip().lower()
		if v in ('1', 'true', 'si', 'sì', 'on'):
			return True
		if v in ('0', 'false', 'no', 'off'):
			return False
		return bool(default)

	def _parse_importo(raw):
		val = str(raw or '').strip().replace(',', '.')
		if not val:
			return Decimal('0.00')
		try:
			return Decimal(val).quantize(Decimal('0.01'))
		except Exception:
			return Decimal('0.00')

	default_start = _get_mese_riferimento_request(request) + '-01'
	ruoli = [
		{
			'id': 'cuoco',
			'label': 'Cuoco',
			'nome': request.GET.get('cuoco_nome', 'Mario Rossi'),
			'quantita': int(request.GET.get('cuoco_quantita', '1') or '1'),
			'livello': request.GET.get('cuoco_livello', '3'),
			'tipo_contratto_id': request.GET.get('cuoco_tipo_contratto', ''),
			'data_inizio': request.GET.get('cuoco_data_inizio', default_start),
			'data_fine': request.GET.get('cuoco_data_fine', ''),
		},
		{
			'id': 'pizzaiolo',
			'label': 'Pizzaiolo',
			'nome': request.GET.get('pizzaiolo_nome', 'Luigi Verdi'),
			'quantita': int(request.GET.get('pizzaiolo_quantita', '1') or '1'),
			'livello': request.GET.get('pizzaiolo_livello', '3'),
			'tipo_contratto_id': request.GET.get('pizzaiolo_tipo_contratto', ''),
			'data_inizio': request.GET.get('pizzaiolo_data_inizio', default_start),
			'data_fine': request.GET.get('pizzaiolo_data_fine', ''),
		},
		{
			'id': 'fattorino',
			'label': 'Fattorino',
			'nome': request.GET.get('fattorino_nome', 'Paolo Bianchi'),
			'quantita': int(request.GET.get('fattorino_quantita', '1') or '1'),
			'livello': request.GET.get('fattorino_livello', '5'),
			'tipo_contratto_id': request.GET.get('fattorino_tipo_contratto', ''),
			'data_inizio': request.GET.get('fattorino_data_inizio', default_start),
			'data_fine': request.GET.get('fattorino_data_fine', ''),
		},
		{
			'id': 'cameriere1',
			'label': 'Cameriere 1',
			'nome': request.GET.get('cameriere1_nome', 'Anna Neri'),
			'quantita': int(request.GET.get('cameriere1_quantita', '1') or '1'),
			'livello': request.GET.get('cameriere1_livello', '4'),
			'tipo_contratto_id': request.GET.get('cameriere1_tipo_contratto', ''),
			'data_inizio': request.GET.get('cameriere1_data_inizio', default_start),
			'data_fine': request.GET.get('cameriere1_data_fine', ''),
		},
		{
			'id': 'cameriere2',
			'label': 'Cameriere 2',
			'nome': request.GET.get('cameriere2_nome', 'Sara Gialli'),
			'quantita': int(request.GET.get('cameriere2_quantita', '1') or '1'),
			'livello': request.GET.get('cameriere2_livello', '4'),
			'tipo_contratto_id': request.GET.get('cameriere2_tipo_contratto', ''),
			'data_inizio': request.GET.get('cameriere2_data_inizio', default_start),
			'data_fine': request.GET.get('cameriere2_data_fine', ''),
		},
		{
			'id': 'responsabile',
			'label': 'Responsabile',
			'nome': request.GET.get('responsabile_nome', 'Marco Blu'),
			'quantita': int(request.GET.get('responsabile_quantita', '1') or '1'),
			'livello': request.GET.get('responsabile_livello', '1'),
			'tipo_contratto_id': request.GET.get('responsabile_tipo_contratto', ''),
			'data_inizio': request.GET.get('responsabile_data_inizio', default_start),
			'data_fine': request.GET.get('responsabile_data_fine', ''),
		},
		{
			'id': 'amministrativo',
			'label': 'Amministrativo',
			'nome': request.GET.get('amministrativo_nome', 'Elena Viola'),
			'quantita': int(request.GET.get('amministrativo_quantita', '1') or '1'),
			'livello': request.GET.get('amministrativo_livello', '4'),
			'tipo_contratto_id': request.GET.get('amministrativo_tipo_contratto', ''),
			'data_inizio': request.GET.get('amministrativo_data_inizio', default_start),
			'data_fine': request.GET.get('amministrativo_data_fine', ''),
		},
	]

	for ruolo in ruoli:
		rid = ruolo['id']
		regione = (request.GET.get(f'{rid}_regione', '') or '').strip().lower()
		categoria = (request.GET.get(f'{rid}_categoria', '') or '').strip().lower()
		tipo_incentivo = (request.GET.get(f'{rid}_tipo_incentivo', '') or '').strip().lower()
		percettore_naspi_raw = (request.GET.get(f'{rid}_percettore_naspi', '') or '').strip().lower()
		eta_raw = (request.GET.get(f'{rid}_eta', '') or '').strip()

		percettore_naspi = None
		if percettore_naspi_raw in ('si', 'true', '1'):
			percettore_naspi = True
		elif percettore_naspi_raw in ('no', 'false', '0'):
			percettore_naspi = False

		eta = None
		if eta_raw:
			try:
				eta = int(eta_raw)
			except ValueError:
				eta = None

		ruolo['regione'] = regione or None
		ruolo['categoria'] = categoria or None
		ruolo['tipo_incentivo'] = tipo_incentivo or None
		ruolo['percettore_naspi'] = percettore_naspi
		ruolo['eta'] = eta

		voci_excel = {}
		for voce_code, cfg in voce_defaults.items():
			presente_raw = request.GET.get(f'{rid}_voce_{voce_code}_presente', '1' if cfg.get('presente') else '0')
			importo_raw = request.GET.get(f'{rid}_voce_{voce_code}_importo', '0')
			voci_excel[voce_code] = {
				'presente': _parse_presente(presente_raw, default=cfg.get('presente', False)),
				'importo': _parse_importo(importo_raw),
			}

		ruolo['voci_excel'] = voci_excel

	return ruoli


def _parse_simulazione_generali(request):
	mese_riferimento = _get_mese_riferimento_request(request)
	ore_turno_pranzo = Decimal(request.GET.get('ore_turno_pranzo', '4') or '4')
	ore_turno_cena = Decimal(request.GET.get('ore_turno_cena', '4') or '4')
	base_oraria_mensile = Decimal(request.GET.get('base_oraria_mensile', '173.33') or '173.33')
	giorni_lavorativi_mese = Decimal(request.GET.get('giorni_lavorativi_mese', '26') or '26')
	aliquota_inail_perc = Decimal(request.GET.get('aliquota_inail_perc', '1.20') or '1.20')

	if ore_turno_pranzo < 0:
		ore_turno_pranzo = Decimal('0')
	if ore_turno_cena < 0:
		ore_turno_cena = Decimal('0')
	if base_oraria_mensile <= 0:
		base_oraria_mensile = Decimal('173.33')
	if giorni_lavorativi_mese <= 0:
		giorni_lavorativi_mese = Decimal('26')
	if aliquota_inail_perc < 0:
		aliquota_inail_perc = Decimal('1.20')

	return mese_riferimento, ore_turno_pranzo, ore_turno_cena, base_oraria_mensile, giorni_lavorativi_mese, aliquota_inail_perc


def _build_calendario_mese_config(azienda_operativa, mese_riferimento, giorni_chiusura=None):
	"""Costruisce griglia calendario mese per UI config con evidenza sab/dom/festivi/chiusure."""
	inizio_mese, fine_mese, _ = periodo_mese_da_riferimento(mese_riferimento)
	giorni_chiusura_set = set(giorni_chiusura or [])
	festivita_tabella = _festivita_mese(azienda_operativa, inizio_mese, fine_mese)
	festivita_tabella_set = {f.data for f in festivita_tabella}
	festivita_per_data = {}
	for f in festivita_tabella:
		festivita_per_data.setdefault(f.data, []).append(f.nome)

	settimane = []
	week = []

	# padding iniziale lunedì=0
	for _ in range(inizio_mese.weekday()):
		week.append(None)

	giorno = inizio_mese
	while giorno <= fine_mese:
		is_sabato = giorno.weekday() == 5
		is_domenica = giorno.weekday() == 6
		is_festivita_tabella = giorno in festivita_tabella_set
		is_festivo = is_domenica or is_festivita_tabella
		week.append({
			'data': giorno,
			'data_iso': giorno.isoformat(),
			'numero': giorno.day,
			'is_sabato': is_sabato,
			'is_domenica': is_domenica,
			'is_festivo': is_festivo,
			'is_festivita_tabella': is_festivita_tabella,
			'festivita_nome': ', '.join(festivita_per_data.get(giorno, [])),
			'is_chiusura': giorno in giorni_chiusura_set,
		})

		if len(week) == 7:
			settimane.append(week)
			week = []

		giorno = giorno + timedelta(days=1)

	if week:
		while len(week) < 7:
			week.append(None)
		settimane.append(week)

	return {
		'settimane': settimane,
		'giorni_chiusura': sorted(giorni_chiusura_set),
	}


def _normalizza_ccnl_key(ccnl_value):
	val = (ccnl_value or '').strip().lower()
	if 'turismo' in val or 'fipe' in val or 'ristor' in val:
		return 'turismo'
	if 'commercio' in val:
		return 'commercio'
	return 'turismo'


def _stima_dimensione_azienda(azienda_operativa):
	if not azienda_operativa:
		return 1
	tipologia = getattr(azienda_operativa, 'tipologia_dimensionale', None)
	if tipologia == 'piccola':
		return 10
	if tipologia == 'media':
		return 30
	if tipologia == 'grande':
		return 80
	try:
		return max(1, azienda_operativa.dipendenti.count())
	except Exception:
		return 1


def _anno_riferimento_da_azienda(azienda_operativa, fallback_anno):
	"""Usa data_attivazione_contratto come anno di aggancio decorrenze, se presente."""
	if azienda_operativa and getattr(azienda_operativa, 'data_attivazione_contratto', None):
		try:
			return int(azienda_operativa.data_attivazione_contratto.year)
		except Exception:
			pass
	return fallback_anno


def _sigla_ccnl_da_key(ccnl_key):
	key = (ccnl_key or '').strip().lower()
	if key == 'turismo':
		return 'FIPE'
	if key == 'commercio':
		return 'COMMERCIO'
	return 'FIPE'


def _categoria_contributiva_da_dimensione(dimensione):
	try:
		n_dip = int(dimensione or 1)
	except Exception:
		n_dip = 1

	if n_dip <= 15:
		return 'piccola_ristorazione'
	if n_dip <= 50:
		return 'media_ristorazione'
	return 'grande_ristorazione'


def _get_ccnl_db_attivo(ccnl_key, anno):
	sigla = _sigla_ccnl_da_key(ccnl_key)
	return (
		CCNL.objects.filter(
			sigla=sigla,
			attivo=True,
			anno_inizio_validita__lte=anno,
		)
		.filter(Q(anno_fine_validita__isnull=True) | Q(anno_fine_validita__gte=anno))
		.order_by('-anno_inizio_validita')
		.first()
	)


def _carica_regole_contributive_da_db(ccnl_key, anno, dimensione, aliquota_inail_fallback):
	"""Carica INPS/INAIL/Ratei da tabelle DB parametrizzate CCNL.
	Ritorna dict compatibile con CostoLavoroAzienda.
	"""
	ccnl_obj = _get_ccnl_db_attivo(ccnl_key, anno)
	if not ccnl_obj:
		return {}

	categoria = _categoria_contributiva_da_dimensione(dimensione)
	regole = {}

	categorie_tentativo = [categoria, 'piccola_ristorazione', 'media_ristorazione', 'grande_ristorazione']

	inps_obj = None
	for cat in categorie_tentativo:
		inps_obj = (
			ParametroContributi.objects.filter(
				ccnl=ccnl_obj,
				tipo_contributo='inps',
				categoria=cat,
				anno=anno,
				attivo=True,
			)
			.order_by('-data_validita_da')
			.first()
		)
		if inps_obj:
			break

	if inps_obj:
		regole['aliquota_inps_azienda'] = float(inps_obj.aliquota_azienda) / 100.0

	inail_obj = None
	for cat in categorie_tentativo:
		inail_obj = (
			ParametroContributi.objects.filter(
				ccnl=ccnl_obj,
				tipo_contributo='inail',
				categoria=cat,
				anno=anno,
				attivo=True,
			)
			.order_by('-data_validita_da')
			.first()
		)
		if inail_obj:
			break

	if inail_obj:
		regole['aliquota_inail'] = float(inail_obj.aliquota_azienda) / 100.0
	else:
		regole['aliquota_inail'] = float(aliquota_inail_fallback)

	ratei_qs = ParametroRatei.objects.filter(ccnl=ccnl_obj, anno=anno, attivo=True)
	for rateo in ratei_qs:
		coeff = float(rateo.coefficiente)
		if rateo.tipo_rateo == 'tfr':
			regole['aliquota_tfr'] = coeff / 100.0 if coeff > 1 else coeff
		elif rateo.tipo_rateo == 'tredicesima':
			regole['rateo_tredicesima'] = coeff / 12.0 if coeff >= 1 else coeff
		elif rateo.tipo_rateo == 'quattordicesima':
			regole['rateo_quattordicesima'] = coeff / 12.0 if coeff >= 1 else coeff
		elif rateo.tipo_rateo == 'indennita_ferie':
			regole['rateo_ferie'] = coeff / 12.0 if coeff >= 1 else coeff

	return regole


def _carica_regole_json(rule_engine, filename, fallback):
	if not rule_engine:
		return fallback
	try:
		return rule_engine.loader.load(filename)
	except Exception:
		return fallback


def _trova_decontribuzione_da_rules(rule_engine, ruolo):
	"""Seleziona la migliore decontribuzione da decontribuzioni.json per il ruolo."""
	regole = _carica_regole_json(rule_engine, 'decontribuzioni.json', [])
	if not isinstance(regole, list):
		return {}, ''

	regione = (ruolo.get('regione') or '').strip().lower() or None
	categoria = (ruolo.get('categoria') or '').strip().lower() or None
	tipo_incentivo = (ruolo.get('tipo_incentivo') or '').strip().lower() or None
	percettore_naspi = ruolo.get('percettore_naspi')
	eta = ruolo.get('eta')

	best = None
	best_priority = -10**9
	for regola in regole:
		if not isinstance(regola, dict):
			continue

		ok = True
		for key, value in {
			'regione': regione,
			'categoria': categoria,
			'tipo_incentivo': tipo_incentivo,
			'percettore_naspi': percettore_naspi,
		}.items():
			if key in regola:
				if value is None or regola.get(key) != value:
					ok = False
					break

		if ok and 'eta' in regola:
			cond_eta = regola.get('eta')
			if eta is None:
				ok = False
			elif isinstance(cond_eta, dict):
				op = cond_eta.get('op', '==')
				expected = cond_eta.get('value')
				if op == '<' and not (eta < expected):
					ok = False
				elif op == '<=' and not (eta <= expected):
					ok = False
				elif op == '>' and not (eta > expected):
					ok = False
				elif op == '>=' and not (eta >= expected):
					ok = False
				elif op == '==' and not (eta == expected):
					ok = False
			else:
				ok = eta == cond_eta

		if not ok:
			continue



	def _date_range(inizio, fine):
		giorno = inizio
		while giorno <= fine:
			yield giorno
			giorno = giorno + timedelta(days=1)


	def _get_giorni_chiusura_non_lavorativi(azienda_operativa, inizio_mese, fine_mese):
		giorni = set()
		if not azienda_operativa:
			return giorni
		chiusure = ChiusuraAziendale.objects.filter(
			azienda=azienda_operativa,
			attivo=True,
			data_inizio__lte=fine_mese,
			data_fine__gte=inizio_mese,
			trattamento__in=['ferie', 'riposo_compensativo', 'chiusura_non_retribuita'],
		)
		for chiusura in chiusure:
			for giorno in _date_range(max(inizio_mese, chiusura.data_inizio), min(fine_mese, chiusura.data_fine)):
				giorni.add(giorno)
		return giorni
		prio = int(regola.get('priority', 0) or 0)
		if prio > best_priority:
			best = regola
			best_priority = prio

	if not best or not isinstance(best.get('values'), dict):
		return {}, ''

	return dict(best['values']), str(best.get('name', ''))


def _estrai_aliquote_file_rules(rule_engine, dimensione, aliquota_inail_fallback):
	"""Carica aliquote INPS/INAIL/TFR da costo_lavoro/rules/*.json."""
	inps_raw = _carica_regole_json(rule_engine, 'inps_fipe.json', {})
	inail_raw = _carica_regole_json(rule_engine, 'inail_fipe.json', {})
	tfr_raw = _carica_regole_json(rule_engine, 'tfr_fipe.json', {})

	inps_cfg = inps_raw.get('inps_fipe', {}) if isinstance(inps_raw, dict) else {}
	inail_cfg = inail_raw.get('inail_fipe', {}) if isinstance(inail_raw, dict) else {}
	tfr_cfg = tfr_raw.get('tfr_fipe', {}) if isinstance(tfr_raw, dict) else {}

	def _to_decimal(value, fallback):
		try:
			return Decimal(str(value))
		except Exception:
			return Decimal(str(fallback))

	def _from_dict(cfg, keys, fallback):
		for key in keys:
			if isinstance(cfg, dict) and key in cfg and cfg.get(key) not in (None, ''):
				return _to_decimal(cfg.get(key), fallback)
		return _to_decimal(fallback, fallback)

	aliquota_inps_az = _from_dict(
		inps_cfg,
		['aliquota_inps_azienda', 'aliquota_azienda', 'inps_azienda', 'aliquota_totale_azienda'],
		'0.2787',
	)
	aliquota_inps_dip = _from_dict(
		inps_cfg,
		['aliquota_inps_dipendente', 'aliquota_dipendente', 'inps_dipendente'],
		'0.0919',
	)
	contributo_naspi = _from_dict(
		inps_cfg,
		['contributo_naspi', 'naspi', 'aliquota_naspi'],
		'0.0161',
	)
	aliquota_inail = _from_dict(
		inail_cfg,
		['aliquota_inail', 'aliquota', 'tasso', 'premio_inail'],
		str(aliquota_inail_fallback),
	)
	aliquota_tfr = _from_dict(
		tfr_cfg,
		['aliquota_tfr', 'tfr', 'coefficiente_tfr'],
		str(Decimal('1') / Decimal('13.5')),
	)

	# Normalizzazione percentuali se espresse come valori > 1 (es. 27.87)
	if aliquota_inps_az > Decimal('1'):
		aliquota_inps_az = (aliquota_inps_az / Decimal('100')).quantize(Decimal('0.0001'))
	if aliquota_inps_dip > Decimal('1'):
		aliquota_inps_dip = (aliquota_inps_dip / Decimal('100')).quantize(Decimal('0.0001'))
	if contributo_naspi > Decimal('1'):
		contributo_naspi = (contributo_naspi / Decimal('100')).quantize(Decimal('0.0001'))
	if aliquota_inail > Decimal('1'):
		aliquota_inail = (aliquota_inail / Decimal('100')).quantize(Decimal('0.0001'))
	if aliquota_tfr > Decimal('1'):
		aliquota_tfr = (aliquota_tfr / Decimal('100')).quantize(Decimal('0.0001'))

	return {
		'aliquota_inps_azienda': aliquota_inps_az.quantize(Decimal('0.0001')),
		'aliquota_inps_dipendente': aliquota_inps_dip.quantize(Decimal('0.0001')),
		'contributo_naspi': contributo_naspi.quantize(Decimal('0.0001')),
		'aliquota_inail': aliquota_inail.quantize(Decimal('0.0001')),
		'aliquota_tfr': aliquota_tfr.quantize(Decimal('0.0001')),
	}


def _build_riepilogo_generale_da_righe(righe):
	rows = []
	totals = {
		'imponibile_inps': Decimal('0.00'),
		'imponibile_inail': Decimal('0.00'),
		'imponibile_irpef': Decimal('0.00'),
		'detrazioni': Decimal('0.00'),
		'trattamento_integrativo': Decimal('0.00'),
		'bonus_l207': Decimal('0.00'),
		'inps_dipendente': Decimal('0.00'),
		'inps_azienda': Decimal('0.00'),
		'irpef_netta': Decimal('0.00'),
		'inail': Decimal('0.00'),
		'netto_da_pagare': Decimal('0.00'),
		'versamento_inps_netto': Decimal('0.00'),
		'versamento_inail_netto': Decimal('0.00'),
		'versamento_erario_netto': Decimal('0.00'),
		'accantonamenti_lordi': Decimal('0.00'),
		'accantonamenti_netti_stimati': Decimal('0.00'),
		'uscita_cassa_mese': Decimal('0.00'),
		'esborso_totale_azienda': Decimal('0.00'),
		'impegno_economico_complessivo': Decimal('0.00'),
	}

	for r in righe:
		if r.get('missing'):
			continue

		imponibile_inps = _safe_decimal(r.get('lordo_tot'))
		imponibile_inail = _safe_decimal(r.get('lordo_tot'))
		imponibile_irpef = _safe_decimal(r.get('imponibile_irpef_tot', r.get('lordo_tot')))
		inps_dip = _safe_decimal(r.get('inps_dipendente_tot'))
		inps_az = _safe_decimal(r.get('inps_azienda_tot'))
		detrazioni = _safe_decimal(r.get('detrazioni_tot'))
		trattamento_integrativo = _safe_decimal(r.get('trattamento_integrativo_tot'))
		bonus_l207 = _safe_decimal(r.get('bonus_l207_tot'))
		irpef_netta = _safe_decimal(r.get('irpef_netta_tot'))
		inail = _safe_decimal(r.get('inail_tot'))
		netto = _safe_decimal(r.get('netto_tot'))
		decontrib = _safe_decimal(r.get('decontrib_risparmio_tot'))
		ratei = (
			_safe_decimal(r.get('rateo_13_tot'))
			+ _safe_decimal(r.get('rateo_14_tot'))
			+ _safe_decimal(r.get('tfr_tot'))
		).quantize(Decimal('0.01'))

		vers_inps_lordo = (inps_dip + inps_az).quantize(Decimal('0.01'))
		vers_inps_netto = max((vers_inps_lordo - decontrib).quantize(Decimal('0.01')), Decimal('0.00'))
		vers_inail_netto = inail
		vers_erario_netto = irpef_netta

		aliquota_inps_dip = _safe_decimal(r.get('aliquota_inps_dipendente'))
		imposte_ratei_dip = (ratei * aliquota_inps_dip).quantize(Decimal('0.01')) if ratei else Decimal('0.00')
		effective_irpef_rate = Decimal('0.00')
		if imponibile_irpef > 0:
			effective_irpef_rate = (irpef_netta / imponibile_irpef).quantize(Decimal('0.0001'))
		imposte_ratei_erario = (ratei * effective_irpef_rate).quantize(Decimal('0.01')) if ratei else Decimal('0.00')
		accantonamenti_netti = max((ratei - imposte_ratei_dip - imposte_ratei_erario).quantize(Decimal('0.01')), Decimal('0.00'))
		esborso = (netto + vers_inps_netto + vers_inail_netto + vers_erario_netto).quantize(Decimal('0.01'))
		impegno_complessivo = (esborso + ratei).quantize(Decimal('0.01'))

		row = {
			'ruolo': r.get('ruolo'),
			'nome': r.get('nome'),
			'quantita': r.get('quantita'),
			'imponibile_inps': imponibile_inps,
			'imponibile_inail': imponibile_inail,
			'imponibile_irpef': imponibile_irpef,
			'detrazioni': detrazioni,
			'trattamento_integrativo': trattamento_integrativo,
			'bonus_l207': bonus_l207,
			'inps_dipendente': inps_dip,
			'inps_azienda': inps_az,
			'irpef_netta': irpef_netta,
			'inail': inail,
			'netto_da_pagare': netto,
			'versamento_inps_netto': vers_inps_netto,
			'versamento_inail_netto': vers_inail_netto,
			'versamento_erario_netto': vers_erario_netto,
			'accantonamenti_lordi': ratei,
			'accantonamenti_netti_stimati': accantonamenti_netti,
			'uscita_cassa_mese': esborso,
			'esborso_totale_azienda': esborso,
			'impegno_economico_complessivo': impegno_complessivo,
		}
		rows.append(row)

		for key in totals.keys():
			totals[key] += row[key]

	totals = {k: v.quantize(Decimal('0.01')) for k, v in totals.items()}
	return rows, totals


def _carica_regola_normativa_da_db(ccnl_label, livello, anno, coeff_ore=Decimal('1')):
	"""Carica regole normative (orario, ferie/permessi, scatti) da tabelle DB costo_lavoro.
	Ritorna dict con fallback implicito al chiamante se i dati non sono disponibili.
	"""
	ccnl_key = _normalizza_ccnl_key(ccnl_label)
	ccnl_obj = _get_ccnl_db_attivo(ccnl_key, anno)
	if not ccnl_obj:
		return {}

	coeff = Decimal(str(coeff_ore or 1))

	# Ore standard contrattuali dal campo CCNL (non da ParametroOrario che contiene limiti max)
	ore_settimanali = Decimal(str(getattr(ccnl_obj, 'orario_standard_settimanale', 40) or 40))

	ore_settimanali = (ore_settimanali * coeff).quantize(Decimal('0.01'))
	# Coefficiente 4.3 → 40h × 4.3 = 172 ore/mese (divisore contrattuale FIPE piccoli esercizi)
	ore_mensili = (ore_settimanali * Decimal('4.3')).quantize(Decimal('0.01'))
	# 6 giorni lavorativi (lun-dom escluso il giorno di riposo settimanale FIPE)
	ore_giornaliere = (ore_settimanali / Decimal('6')).quantize(Decimal('0.01'))

	ferie_annue_giorni = Decimal(str(getattr(ccnl_obj, 'giorni_ferie_base', 26) or 26))
	permessi_annui_ore = (Decimal(str(getattr(ccnl_obj, 'giorni_rol_base', 0) or 0)) * Decimal('8')).quantize(Decimal('0.01'))

	scatto_periodicita_mesi = None
	scatto_importo = None
	numero_scatti_massimi = None

	scatti_qs = ParametroScattiAnnuali.objects.filter(
		ccnl=ccnl_obj,
		anno=anno,
		livello=str(livello or ''),
		attivo=True,
	).order_by('anni_anzianita', '-data_validita_da')

	primo_scatto = scatti_qs.first()
	if primo_scatto:
		scatto_periodicita_mesi = max(12, int(primo_scatto.anni_anzianita or 1) * 12)
		scatto_importo = Decimal(str(primo_scatto.importo_scatto)).quantize(Decimal('0.01'))
		numero_scatti_massimi = scatti_qs.count()

	return {
		'ore_settimanali': ore_settimanali,
		'ore_mensili': ore_mensili,
		'ore_giornaliere': ore_giornaliere,
		'ferie_annue_giorni': ferie_annue_giorni,
		'permessi_annui_ore': permessi_annui_ore,
		'scatto_periodicita_mesi': scatto_periodicita_mesi,
		'scatto_importo': scatto_importo,
		'numero_scatti_massimi': numero_scatti_massimi,
		'fonte': 'costo_lavoro_db',
	}


def _carica_parametri_tabellari_costo_lavoro(parametro, anno=None, coeff_ore=Decimal('1')):
	"""Ritorna parametri tabellari/maggiorazioni/scatti da motore costo_lavoro + tabelle DB.
	Usa fallback ai valori legacy se le regole non sono disponibili.
	"""
	try:
		anno_riferimento = int(anno or (parametro.decorrenza_validita_da.year if parametro.decorrenza_validita_da else timezone.localdate().year))
	except Exception:
		anno_riferimento = timezone.localdate().year

	# Default legacy
	paga_base = Decimal(str(parametro.paga_base_mensile or 0))
	contingenza = Decimal(str(parametro.contingenza_mensile or 0))
	edr = Decimal(str(parametro.edr_mensile or 0))
	indennita = Decimal(str(parametro.indennita_mensile or 0))
	stipendio_lordo = Decimal(str(parametro.importo_lordo_mensile or 0))
	straord_diurno = Decimal(str(parametro.straordinario_diurno_maggiorazione or 0))
	straord_notturno = Decimal(str(parametro.straordinario_notturno_maggiorazione or 0))
	straord_festivo = Decimal(str(parametro.straordinario_festivo_maggiorazione or 0))
	scatto_importo = Decimal(str(parametro.scatto_importo or 0))
	scatto_periodicita_mesi = int(parametro.scatto_periodicita_mesi or 24)
	numero_scatti_massimi = int(parametro.numero_scatti_massimi or 10)

	ccnl_key = _normalizza_ccnl_key(getattr(parametro, 'ccnl', ''))
	ccnl_obj = _get_ccnl_db_attivo(ccnl_key, anno_riferimento)

	# 1) Regola tabellare dal motore costo_lavoro (valori livello)
	if COSTO_LAVORO_ENABLED and RuleEngine:
		try:
			rule_engine = RuleEngine()
			regola_tabellare = rule_engine.get(
				'ccnl_fipe_piccola_ristorazione',
				ccnl=ccnl_key,
				livello=str(getattr(parametro, 'livello', '') or ''),
				anno=anno_riferimento,
				azienda_minore=True,
			) or {}

			if regola_tabellare:
				paga_base_engine = Decimal(str(regola_tabellare.get('paga_base_ridotta') or paga_base))
				contingenza_engine = Decimal(str(regola_tabellare.get('contingenza') or contingenza))
				totale_engine = Decimal(str(regola_tabellare.get('totale_mensile') or stipendio_lordo))

				paga_base = paga_base_engine
				contingenza = contingenza_engine
				stipendio_lordo = totale_engine

				# Se non presente un EDR esplicito nel motore, lo ricaviamo a quadratura
				edr_calcolato = totale_engine - paga_base_engine - contingenza_engine - indennita
				edr = edr_calcolato if edr_calcolato > Decimal('0') else Decimal('0')
		except Exception:
			pass

	# 2) Maggiorazioni da tabelle DB parametrizzate
	if ccnl_obj:
		maggiorazioni_qs = ParametroMaggiorazione.objects.filter(
			ccnl=ccnl_obj,
			anno=anno_riferimento,
			attivo=True,
		).order_by('-data_validita_da')

		mappa_magg = {
			obj.tipo_maggiorazione: Decimal(str(obj.percentuale))
			for obj in maggiorazioni_qs
		}
		straord_diurno = mappa_magg.get('straordinario_feriale', straord_diurno)
		straord_notturno = mappa_magg.get('straordinario_notturno', straord_notturno)
		straord_festivo = mappa_magg.get('straordinario_festivo', straord_festivo)

		# 3) Scatti da tabelle DB parametrizzate
		scatto_obj = (
			ParametroScattiAnnuali.objects.filter(
				ccnl=ccnl_obj,
				anno=anno_riferimento,
				livello=str(getattr(parametro, 'livello', '') or ''),
				attivo=True,
			)
			.order_by('anni_anzianita', '-data_validita_da')
			.first()
		)
		if scatto_obj:
			scatto_importo = Decimal(str(scatto_obj.importo_scatto))
			scatto_periodicita_mesi = max(12, int(scatto_obj.anni_anzianita or 1) * 12)

	# 4) Orario da tabelle DB parametrizzate
	ore_settimanali_base = Decimal(str(getattr(parametro, 'ore_settimanali', 40) or 40))
	ore_mensili_base = Decimal(str(getattr(parametro, 'ore_mensili', 172) or 172))
	ore_giornaliere_base = Decimal(str(getattr(parametro, 'ore_giornaliere', 8) or 8))

	# ParametroOrario contiene limiti di validazione (min/max), non ore standard.
	# Le ore mensili standard vengono da parametro.ore_mensili (DB = 172 FIPE).

	coeff = Decimal(str(coeff_ore or 1))
	ore_settimanali = (ore_settimanali_base * coeff).quantize(Decimal('0.01'))
	ore_mensili = (ore_mensili_base * coeff).quantize(Decimal('0.01'))
	ore_giornaliere = (ore_giornaliere_base * coeff).quantize(Decimal('0.01'))

	stipendio_lordo = stipendio_lordo.quantize(Decimal('0.01'))
	paga_base = paga_base.quantize(Decimal('0.01'))
	contingenza = contingenza.quantize(Decimal('0.01'))
	edr = edr.quantize(Decimal('0.01'))

	return {
		'anno_riferimento': anno_riferimento,
		'stipendio_lordo_mensile': stipendio_lordo,
		'paga_base_mensile': paga_base,
		'contingenza_mensile': contingenza,
		'edr_mensile': edr,
		'indennita_mensile': indennita,
		'ore_settimanali': ore_settimanali,
		'ore_mensili': ore_mensili,
		'ore_giornaliere': ore_giornaliere,
		'scatto_periodicita_mesi': scatto_periodicita_mesi,
		'scatto_importo': scatto_importo.quantize(Decimal('0.01')),
		'numero_scatti_massimi': numero_scatti_massimi,
		'straordinario_diurno_maggiorazione': straord_diurno.quantize(Decimal('0.01')),
		'straordinario_notturno_maggiorazione': straord_notturno.quantize(Decimal('0.01')),
		'straordinario_festivo_maggiorazione': straord_festivo.quantize(Decimal('0.01')),
	}


def _calcola_costo_azienda_ruolo_costo_lavoro(
	parametro,
	coeff_ore,
	giorni_lavorativi_mese,
	giorni_attivi,
	mese_riferimento,
	aliquota_inail,
	azienda_operativa,
	rule_engine,
	ruolo=None,
):
	"""Calcolo costo azienda per ruolo con motore costo_lavoro. Ritorna dict o None."""
	if not COSTO_LAVORO_ENABLED or not rule_engine:
		return None

	try:
		anno = int(str(mese_riferimento).split('-')[0])
	except Exception:
		anno = timezone.localdate().year
	anno = _anno_riferimento_da_azienda(azienda_operativa, anno)

	ccnl_key = _normalizza_ccnl_key(getattr(parametro, 'ccnl', ''))
	livello = str(getattr(parametro, 'livello', '') or '')
	dimensione = _stima_dimensione_azienda(azienda_operativa)

	regola_tabellare = rule_engine.get(
		'ccnl_fipe_piccola_ristorazione',
		ccnl=ccnl_key,
		livello=livello,
		anno=anno,
		azienda_minore=True,
	) or {}

	ccnl_db = _get_ccnl_db_attivo(ccnl_key, anno)

	retribuzione_lorda = (
		Decimal(str(getattr(parametro, 'contingenza_mensile', 0) or 0))
		+ Decimal(str(getattr(parametro, 'paga_base_mensile', 0) or 0))
	)
	if retribuzione_lorda == 0:
		retribuzione_lorda = Decimal(str(getattr(parametro, 'importo_lordo_mensile', 0) or 0))
	retribuzione_lorda = (retribuzione_lorda * coeff_ore).quantize(Decimal('0.01'))
	mensilita = int(getattr(ccnl_db, 'mensilita', None) or regola_tabellare.get('mensilita') or 14)
	ore_settimanali = (Decimal(str(getattr(parametro, 'ore_settimanali', Decimal('40')))) * coeff_ore).quantize(Decimal('0.01'))

	dati = DatiContrattuali(
		retribuzione_lorda_mensile=float(retribuzione_lorda),
		giorni_lavorativi_mese=int(giorni_lavorativi_mese),
		giorni_lavorati=int(giorni_attivi),
		mensilita=mensilita,
		ore_settimanali=int(ore_settimanali),
		livello=livello,
		ccnl=ccnl_key,
	)

	regole_file = _estrai_aliquote_file_rules(
		rule_engine=rule_engine,
		dimensione=dimensione,
		aliquota_inail_fallback=aliquota_inail,
	)

	regole_ratei = {
		'rateo_ferie': float(regola_tabellare.get('rateo_ferie', Decimal('0.0833'))),
		'rateo_permessi': float(regola_tabellare.get('rateo_permessi', Decimal('0.0555'))),
		'rateo_tredicesima': float(regola_tabellare.get('rateo_tredicesima', Decimal('0.0833'))),
		'rateo_quattordicesima': float(regola_tabellare.get('rateo_quattordicesima', Decimal('0.0833'))),
	}

	regole_complete = {
		**regole_file,
		**regole_ratei,
	}

	# Override da database parametrico CCNL (se disponibile)
	regole_db = _carica_regole_contributive_da_db(
		ccnl_key=ccnl_key,
		anno=anno,
		dimensione=dimensione,
		aliquota_inail_fallback=aliquota_inail,
	)
	regole_complete.update(regole_db)
	# Il calcolatore costo_lavoro usa float: evita mix float/Decimal (TypeError).
	regole_complete = {
		k: (float(v) if isinstance(v, Decimal) else v)
		for k, v in regole_complete.items()
	}

	decontrib_values, decontrib_rule_name = _trova_decontribuzione_da_rules(rule_engine, ruolo or {})
	decontrib = {}
	if Decontribuzioni and decontrib_values:
		decontrib = Decontribuzioni(**decontrib_values).__dict__

	extra = CostiEventuali() if CostiEventuali else None

	risultato = CostoLavoroAzienda(
		contrattuali=dati,
		regole_inps=regole_complete,
		regole_decontrib=decontrib,
		costi_eventuali=extra,
	).calcola()

	# Calcolo baseline senza decontribuzione per stimare il risparmio effettivo INPS azienda
	risultato_senza_decontrib = CostoLavoroAzienda(
		contrattuali=dati,
		regole_inps=regole_complete,
		regole_decontrib={},
		costi_eventuali=extra,
	).calcola()

	inps_con_decontrib = Decimal(str(risultato.get('contributi_inps', 0))).quantize(Decimal('0.01'))
	inps_senza_decontrib = Decimal(str(risultato_senza_decontrib.get('contributi_inps', 0))).quantize(Decimal('0.01'))
	decontrib_risparmio_unit = max((inps_senza_decontrib - inps_con_decontrib).quantize(Decimal('0.01')), Decimal('0.00'))

	aliquota_inps_dip = Decimal(str(regole_complete.get('aliquota_inps_dipendente', 0.0919))).quantize(Decimal('0.0001'))
	aliquota_tfr = Decimal(str(regole_complete.get('aliquota_tfr', 0.0741))).quantize(Decimal('0.0001'))
	rateo_13_coeff = Decimal(str(regole_complete.get('rateo_tredicesima', 0.0833))).quantize(Decimal('0.0001'))
	rateo_14_coeff = Decimal(str(regole_complete.get('rateo_quattordicesima', 0.0833))).quantize(Decimal('0.0001'))
	rateo_ferie_unit = Decimal(str(risultato.get('rateo_ferie', 0))).quantize(Decimal('0.01'))
	rateo_permessi_unit = Decimal(str(risultato.get('rateo_permessi', 0))).quantize(Decimal('0.01'))
	lordo_unit = Decimal(str(risultato.get('retribuzione_proporzionata', 0))).quantize(Decimal('0.01'))
	inail_unit = Decimal(str(risultato.get('premio_inail', 0))).quantize(Decimal('0.01'))
	tfr_unit = Decimal(str(risultato.get('tfr', 0))).quantize(Decimal('0.01'))
	rateo_13_unit = Decimal(str(risultato.get('rateo_tredicesima', 0))).quantize(Decimal('0.01'))
	rateo_14_unit = Decimal(str(risultato.get('rateo_quattordicesima', 0))).quantize(Decimal('0.01'))
	costo_azienda_unit = (lordo_unit + inps_con_decontrib + inail_unit + tfr_unit + rateo_13_unit + rateo_14_unit).quantize(Decimal('0.01'))
	costo_azienda_esteso_unit = Decimal(str(risultato.get('costo_totale', 0))).quantize(Decimal('0.01'))

	return {
		'lordo_unit': lordo_unit,
		'inps_azienda_unit': inps_con_decontrib,
		'inail_unit': inail_unit,
		'tfr_unit': tfr_unit,
		'rateo_13_unit': rateo_13_unit,
		'rateo_14_unit': rateo_14_unit,
		'rateo_ferie_unit': rateo_ferie_unit,
		'rateo_permessi_unit': rateo_permessi_unit,
		'costo_azienda_unit': costo_azienda_unit,
		'costo_azienda_esteso_unit': costo_azienda_esteso_unit,
		'aliquota_inps_dipendente': aliquota_inps_dip,
		'aliquota_tfr_coeff': aliquota_tfr,
		'rateo_13_coeff': rateo_13_coeff,
		'rateo_14_coeff': rateo_14_coeff,
		'decontrib_rule_name': decontrib_rule_name,
		'decontrib_tipo': str((decontrib or {}).get('tipo', '')),
		'decontrib_valore': Decimal(str((decontrib or {}).get('valore', 0))).quantize(Decimal('0.0001')),
		'decontrib_risparmio_unit': decontrib_risparmio_unit,
		'regola_tabellare': regola_tabellare,
	}


def _estrai_parametri_irpef_da_rules(rule_engine):
	"""Ritorna configurazione IRPEF/detrazioni da rule engine con fallback standard."""
	default = {
		'scaglioni': [
			(Decimal('28000'), Decimal('0.23')),
			(Decimal('50000'), Decimal('0.35')),
			(None, Decimal('0.43')),
		],
		'detrazione': {
			'soglia_1': Decimal('15000'),
			'soglia_2': Decimal('28000'),
			'soglia_3': Decimal('50000'),
			'importo_base_1': Decimal('1880'),
			'importo_base_2': Decimal('1910'),
			'importo_var_2': Decimal('1190'),
			'importo_base_3': Decimal('1910'),
		},
	}

	if not rule_engine:
		return default

	try:
		regola = rule_engine.get('irpef') or {}
		scaglioni_raw = regola.get('scaglioni') or []
		scaglioni = []
		for s in scaglioni_raw:
			soglia = s.get('fino_a')
			aliquota = s.get('aliquota')
			if aliquota is None:
				continue
			soglia_dec = Decimal(str(soglia)) if soglia not in (None, '') else None
			aliquota_dec = Decimal(str(aliquota))
			if aliquota_dec > 1:
				aliquota_dec = (aliquota_dec / Decimal('100')).quantize(Decimal('0.0001'))
			scaglioni.append((soglia_dec, aliquota_dec))
		if scaglioni:
			default['scaglioni'] = scaglioni

		det = regola.get('detrazione_lavoro_dipendente') or {}
		if det:
			default['detrazione'].update({
				'soglia_1': Decimal(str(det.get('soglia_1', default['detrazione']['soglia_1']))),
				'soglia_2': Decimal(str(det.get('soglia_2', default['detrazione']['soglia_2']))),
				'soglia_3': Decimal(str(det.get('soglia_3', default['detrazione']['soglia_3']))),
				'importo_base_1': Decimal(str(det.get('importo_base_1', default['detrazione']['importo_base_1']))),
				'importo_base_2': Decimal(str(det.get('importo_base_2', default['detrazione']['importo_base_2']))),
				'importo_var_2': Decimal(str(det.get('importo_var_2', default['detrazione']['importo_var_2']))),
				'importo_base_3': Decimal(str(det.get('importo_base_3', default['detrazione']['importo_base_3']))),
			})
	except Exception:
		pass

	return default


def _calcola_netto_dipendente_con_regole(*, lordo, aliquota_inps_dipendente, irpef_cfg):
	"""Calcola netto dipendente usando regole IRPEF configurabili; fallback al calcolo standard."""
	try:
		lordo = _safe_decimal(lordo)
		aliquota_inps_dipendente = _safe_decimal(aliquota_inps_dipendente)
		if aliquota_inps_dipendente > 1:
			aliquota_inps_dipendente = (aliquota_inps_dipendente / Decimal('100')).quantize(Decimal('0.0001'))

		inps_dip = (lordo * aliquota_inps_dipendente).quantize(Decimal('0.01'))
		imponibile = (lordo - inps_dip).quantize(Decimal('0.01'))
		reddito_annuo = (imponibile * Decimal('12')).quantize(Decimal('0.01'))

		scaglioni = (irpef_cfg or {}).get('scaglioni', [])
		if not scaglioni:
			raise ValueError('Scaglioni IRPEF mancanti')

		irpef_annua = Decimal('0.00')
		precedente = Decimal('0.00')
		for soglia, aliquota in scaglioni:
			aliquota = _safe_decimal(aliquota)
			if soglia is None:
				base = max(reddito_annuo - precedente, Decimal('0.00'))
				irpef_annua += base * aliquota
				break
			soglia = _safe_decimal(soglia)
			if reddito_annuo > soglia:
				base = max(soglia - precedente, Decimal('0.00'))
				irpef_annua += base * aliquota
				precedente = soglia
			else:
				base = max(reddito_annuo - precedente, Decimal('0.00'))
				irpef_annua += base * aliquota
				break

		irpef_lorda = (irpef_annua / Decimal('12')).quantize(Decimal('0.01'))

		det_cfg = (irpef_cfg or {}).get('detrazione', {})
		s1 = _safe_decimal(det_cfg.get('soglia_1', 15000))
		s2 = _safe_decimal(det_cfg.get('soglia_2', 28000))
		s3 = _safe_decimal(det_cfg.get('soglia_3', 50000))
		b1 = _safe_decimal(det_cfg.get('importo_base_1', 1880))
		b2 = _safe_decimal(det_cfg.get('importo_base_2', 1910))
		v2 = _safe_decimal(det_cfg.get('importo_var_2', 1190))
		b3 = _safe_decimal(det_cfg.get('importo_base_3', 1910))

		if reddito_annuo <= s1:
			detrazioni_annue = b1
		elif reddito_annuo <= s2:
			den = max(s2 - s1, Decimal('1'))
			detrazioni_annue = b2 + (v2 * (s2 - reddito_annuo) / den)
		elif reddito_annuo <= s3:
			den = max(s3 - s2, Decimal('1'))
			detrazioni_annue = b3 * (s3 - reddito_annuo) / den
		else:
			detrazioni_annue = Decimal('0.00')

		detrazioni = (detrazioni_annue / Decimal('12')).quantize(Decimal('0.01'))
		irpef_netta = max((irpef_lorda - detrazioni).quantize(Decimal('0.01')), Decimal('0.00'))
		netto = (lordo - inps_dip - irpef_netta).quantize(Decimal('0.01'))

		return {
			'imponibile': imponibile,
			'inps_dipendente': inps_dip,
			'irpef_lorda': irpef_lorda,
			'detrazioni': detrazioni,
			'irpef_netta': irpef_netta,
			'netto': netto,
		}
	except Exception:
		legacy = calcola_netto_dipendente(_safe_decimal(lordo))
		return {
			'imponibile': _safe_decimal(legacy.get('imponibile')),
			'inps_dipendente': _safe_decimal(legacy.get('inps_dipendente')),
			'irpef_lorda': _safe_decimal(legacy.get('irpef_lorda')),
			'detrazioni': _safe_decimal(legacy.get('detrazioni')),
			'irpef_netta': _safe_decimal(legacy.get('irpef_netta')),
			'netto': _safe_decimal(legacy.get('netto')),
		}


def _calcola_netto_dipendente_con_basi(
	*,
	lordo_totale,
	imponibile_inps,
	imponibile_irpef,
	aliquota_inps_dipendente,
	irpef_cfg,
):
	"""Calcola il netto con basi separate: INPS su base previdenziale e IRPEF su base fiscale."""
	try:
		lordo_totale = _safe_decimal(lordo_totale).quantize(Decimal('0.01'))
		base_inps = _safe_decimal(imponibile_inps).quantize(Decimal('0.01'))
		base_irpef = _safe_decimal(imponibile_irpef).quantize(Decimal('0.01'))
		aliquota_inps_dipendente = _safe_decimal(aliquota_inps_dipendente)
		if aliquota_inps_dipendente > 1:
			aliquota_inps_dipendente = (aliquota_inps_dipendente / Decimal('100')).quantize(Decimal('0.0001'))

		inps_dip = (base_inps * aliquota_inps_dipendente).quantize(Decimal('0.01'))
		imponibile = max(base_irpef - inps_dip, Decimal('0.00')).quantize(Decimal('0.01'))
		reddito_annuo = (imponibile * Decimal('12')).quantize(Decimal('0.01'))

		scaglioni = (irpef_cfg or {}).get('scaglioni', [])
		if not scaglioni:
			raise ValueError('Scaglioni IRPEF mancanti')

		irpef_annua = Decimal('0.00')
		precedente = Decimal('0.00')
		for soglia, aliquota in scaglioni:
			aliquota = _safe_decimal(aliquota)
			if soglia is None:
				base = max(reddito_annuo - precedente, Decimal('0.00'))
				irpef_annua += base * aliquota
				break
			soglia = _safe_decimal(soglia)
			if reddito_annuo > soglia:
				base = max(soglia - precedente, Decimal('0.00'))
				irpef_annua += base * aliquota
				precedente = soglia
			else:
				base = max(reddito_annuo - precedente, Decimal('0.00'))
				irpef_annua += base * aliquota
				break

		irpef_lorda = (irpef_annua / Decimal('12')).quantize(Decimal('0.01'))

		det_cfg = (irpef_cfg or {}).get('detrazione', {})
		s1 = _safe_decimal(det_cfg.get('soglia_1', 15000))
		s2 = _safe_decimal(det_cfg.get('soglia_2', 28000))
		s3 = _safe_decimal(det_cfg.get('soglia_3', 50000))
		b1 = _safe_decimal(det_cfg.get('importo_base_1', 1880))
		b2 = _safe_decimal(det_cfg.get('importo_base_2', 1910))
		v2 = _safe_decimal(det_cfg.get('importo_var_2', 1190))
		b3 = _safe_decimal(det_cfg.get('importo_base_3', 1910))

		if reddito_annuo <= s1:
			detrazioni_annue = b1
		elif reddito_annuo <= s2:
			den = max(s2 - s1, Decimal('1'))
			detrazioni_annue = b2 + (v2 * (s2 - reddito_annuo) / den)
		elif reddito_annuo <= s3:
			den = max(s3 - s2, Decimal('1'))
			detrazioni_annue = b3 * (s3 - reddito_annuo) / den
		else:
			detrazioni_annue = Decimal('0.00')

		detrazioni = (detrazioni_annue / Decimal('12')).quantize(Decimal('0.01'))
		irpef_netta = max((irpef_lorda - detrazioni).quantize(Decimal('0.01')), Decimal('0.00'))
		netto = (lordo_totale - inps_dip - irpef_netta).quantize(Decimal('0.01'))

		return {
			'imponibile': imponibile,
			'inps_dipendente': inps_dip,
			'irpef_lorda': irpef_lorda,
			'detrazioni': detrazioni,
			'irpef_netta': irpef_netta,
			'netto': netto,
		}
	except Exception:
		return _calcola_netto_dipendente_con_regole(
			lordo=lordo_totale,
			aliquota_inps_dipendente=aliquota_inps_dipendente,
			irpef_cfg=irpef_cfg,
		)


def _calcola_simulazione(request):
	mese_riferimento, ore_turno_pranzo, ore_turno_cena, base_oraria_mensile, giorni_lavorativi_mese, aliquota_inail_perc = _parse_simulazione_generali(request)
	inizio_mese, fine_mese, giorni_nel_mese = periodo_mese_da_riferimento(mese_riferimento)
	giorni_chiusura_mese_date = parse_giorni_chiusura_mese(request, mese_riferimento)
	aliquota_inail = aliquota_inail_perc / Decimal('100')
	ore_totali_turni_mese = (ore_turno_pranzo + ore_turno_cena) * giorni_lavorativi_mese
	azienda_operativa = _get_azienda_operativa_per_utente(request.user, request.session)
	rule_engine = RuleEngine() if COSTO_LAVORO_ENABLED else None
	irpef_cfg = _estrai_parametri_irpef_da_rules(rule_engine)

	ruoli_config = _build_ruoli_config(request)
	parametri_ccnl = ParametroCCNLTurismo.objects.filter(attivo=True).order_by('livello', 'qualifica')
	tipi_contratto = TipoContratto.objects.filter(attivo=True).order_by('nome')

	righe = []
	totali = {
		'netto_mensile': Decimal('0'),
		'lordo_mensile': Decimal('0'),
		'maggiorazioni': Decimal('0'),
		'trattamento_integrativo': Decimal('0'),
		'bonus_l207': Decimal('0'),
		'inps_azienda': Decimal('0'),
		'inps_dipendente': Decimal('0'),
		'irpef_lorda': Decimal('0'),
		'detrazioni_irpef': Decimal('0'),
		'irpef_netta': Decimal('0'),
		'inail': Decimal('0'),
		'tfr': Decimal('0'),
		'rateo_13': Decimal('0'),
		'rateo_14': Decimal('0'),
		'costo_azienda_totale': Decimal('0'),
		'ore_disponibili_mese': Decimal('0'),
		'totale_f24_mese': Decimal('0'),
		'decontrib_risparmio': Decimal('0'),
	}

	for ruolo in ruoli_config:
		if ruolo['quantita'] <= 0:
			continue

		# Trova il parametro CCNL valido per il livello e la data inizio del ruolo
		from datetime import datetime
		data_inizio_ruolo = datetime.strptime(ruolo['data_inizio'], '%Y-%m-%d').date() if ruolo['data_inizio'] else inizio_mese
		
		# Filtra parametri per livello e validità alla data inizio
		parametri_candidati = parametri_ccnl.filter(livello=ruolo['livello'])
		parametro = None
		for p in parametri_candidati:
			valido = True
			if p.decorrenza_validita_da and data_inizio_ruolo < p.decorrenza_validita_da:
				valido = False
			if valido and p.decorrenza_validita_a and data_inizio_ruolo > p.decorrenza_validita_a:
				valido = False
			if valido:
				parametro = p
				break
		
		if not parametro:
			righe.append({
				'ruolo_id': ruolo['id'],
				'ruolo': ruolo['label'],
				'nome': ruolo['nome'],
				'quantita': ruolo['quantita'],
				'missing': True,
			})
			continue

		tipo_contratto = None
		coeff_ore = Decimal('1.00')
		if ruolo['tipo_contratto_id']:
			try:
				tipo_contratto = tipi_contratto.get(id=int(ruolo['tipo_contratto_id']))
				coeff_ore = Decimal(str(tipo_contratto.coefficiente_ore or Decimal('1.00')))
			except (TipoContratto.DoesNotExist, ValueError):
				pass

		data_inizio = parse_iso_date(ruolo.get('data_inizio'))
		data_fine = parse_iso_date(ruolo.get('data_fine'))
		giorni_attivi = calcola_giorni_attivi_mese(inizio_mese, fine_mese, data_inizio, data_fine)
		coeff_periodo = Decimal('0')
		if giorni_nel_mese > 0:
			coeff_periodo = (Decimal(giorni_attivi) / Decimal(giorni_nel_mese)).quantize(Decimal('0.0001'))

		# Lordo imponibile = contingenza + paga_base (totale_mensile nel database)
		# Fallback a importo_lordo_mensile se non disponibili
		lordo_imponibile = (
			Decimal(str(parametro.contingenza_mensile or 0))
			+ Decimal(str(parametro.paga_base_mensile or 0))
		)
		if lordo_imponibile == 0:
			lordo_imponibile = Decimal(str(parametro.importo_lordo_mensile))
		lordo_rapportato = (lordo_imponibile * coeff_ore).quantize(Decimal('0.01'))

		costo_lavoro_ruolo = _calcola_costo_azienda_ruolo_costo_lavoro(
			parametro=parametro,
			coeff_ore=coeff_ore,
			giorni_lavorativi_mese=giorni_lavorativi_mese,
			giorni_attivi=giorni_attivi,
			mese_riferimento=mese_riferimento,
			aliquota_inail=aliquota_inail,
			azienda_operativa=azienda_operativa,
			rule_engine=rule_engine,
			ruolo=ruolo,
		)

		calcolo_legacy = calcola_base_simulazione_motore_unico(
			parametro=parametro,
			tipo_contratto=tipo_contratto,
			anno=inizio_mese.year,
			mese=inizio_mese.month,
			azienda=azienda_operativa,
			data_inizio=data_inizio,
			data_fine=data_fine,
			lordo_fallback=lordo_rapportato,
		)
		lordo_unit_base = Decimal(str(calcolo_legacy['lordo_mensile']))
		inps_az_unit_base = Decimal(str(calcolo_legacy['costo_azienda']['inps_azienda']))
		tfr_unit_base = Decimal(str(calcolo_legacy['costo_azienda']['tfr']))
		rateo_13_unit_base = Decimal(str(calcolo_legacy['costo_azienda']['rateo_13']))
		rateo_14_unit_base = Decimal(str(calcolo_legacy['costo_azienda']['rateo_14']))
		ore_retribuite = calcola_ore_retribuite_contrattuali(
			base_oraria_mensile=base_oraria_mensile,
			giorni_lavorativi_mese=giorni_lavorativi_mese,
			coeff_ore=coeff_ore,
			coeff_periodo=coeff_periodo,
		)
		ore_mensili_unit = ore_retribuite['ore_mensili_retribuite']
		ore_giornaliere_retribuite = ore_retribuite['ore_giornaliere_retribuite']

		lordo_unit = (lordo_unit_base * coeff_periodo).quantize(Decimal('0.01'))
		inps_az_unit = (inps_az_unit_base * coeff_periodo).quantize(Decimal('0.01'))
		tfr_unit = (tfr_unit_base * coeff_periodo).quantize(Decimal('0.01'))
		rateo_13_unit = (rateo_13_unit_base * coeff_periodo).quantize(Decimal('0.01'))
		rateo_14_unit = (rateo_14_unit_base * coeff_periodo).quantize(Decimal('0.01'))

		tfr_giornaliero = (tfr_unit_base / Decimal(giorni_nel_mese)).quantize(Decimal('0.01')) if giorni_nel_mese else Decimal('0.00')
		rateo_13_giornaliero = (rateo_13_unit_base / Decimal(giorni_nel_mese)).quantize(Decimal('0.01')) if giorni_nel_mese else Decimal('0.00')
		rateo_14_giornaliero = (rateo_14_unit_base / Decimal(giorni_nel_mese)).quantize(Decimal('0.01')) if giorni_nel_mese else Decimal('0.00')

		paga_oraria_contratto = calcola_paga_oraria_contrattuale(
			parametro=parametro,
			ruolo=ruolo,
			coeff_ore=coeff_ore,
			coeff_periodo=coeff_periodo,
			ore_mensili_unit=ore_mensili_unit,
			giorni_lavorativi_mese=giorni_lavorativi_mese,
		)
		paga_oraria_contrattuale = _safe_decimal(paga_oraria_contratto.get('paga_oraria')).quantize(Decimal('0.0001'))
		lavoro_ordinario_contrattuale_unit = _safe_decimal(paga_oraria_contratto.get('totale_lavoro_ordinario')).quantize(Decimal('0.01'))
		componenti_orarie_contratto = paga_oraria_contratto.get('componenti', {})

		# ── Calcolo busta paga basato sulle ore (metodo alternativo) ─────────
		# Regola richiesta:
		# Giorni lavorati = tutti i giorni del periodo - giorni di chiusura (sabati inclusi)
		# Domeniche conteggiate a parte per lavoro domenicale/maggiorazione.
		inizio_attivo = max(inizio_mese, data_inizio) if data_inizio else inizio_mese
		fine_attivo = min(fine_mese, data_fine) if data_fine else fine_mese

		giorni_chiusura_set = set(giorni_chiusura_mese_date or [])
		try:
			giorni_chiusura_set |= set(_get_giorni_chiusura_non_lavorativi(azienda_operativa, inizio_attivo, fine_attivo))
		except Exception:
			pass

		# Logica corretta:
		# - Le domeniche NON entrano in giorni_lavorati (escluse a priori)
		# - Le domeniche non chiuse si contano separatamente come n_domeniche
		# - giorni_lavorati = lunedì-sabato NON chiusi
		_giorni_lavorati_calcolati = 0
		_n_domeniche_lavorate = 0
		for _d in _date_range(inizio_attivo, fine_attivo):
			if _d.weekday() == 6:  # domenica: conteggio separato, mai in giorni_lavorati
				if _d not in giorni_chiusura_set:
					_n_domeniche_lavorate += 1
				continue
			# Lun-Sab: conta solo se non giorno di chiusura
			if _d in giorni_chiusura_set:
				continue
			_giorni_lavorati_calcolati += 1

		_giorni_lavorati_senza_domeniche = _giorni_lavorati_calcolati  # già privi di domeniche

		# Auto-valorizzazione voci da DB (se mancanti in configurazione ruolo)
		voci_excel = ruolo.get('voci_excel') if isinstance(ruolo, dict) else None
		if isinstance(voci_excel, dict):
			voci_tabella = _carica_voci_retributive_da_tabella(parametro)
			fallback_voci = {
				'minimo_tabellare': _safe_decimal(voci_tabella.get('minimo_tabellare')) or _safe_decimal(getattr(parametro, 'minimo_tabellare', 0)) or _safe_decimal(getattr(parametro, 'paga_base_mensile', 0)),
				'contingenza': _safe_decimal(voci_tabella.get('contingenza')) or _safe_decimal(getattr(parametro, 'contingenza_mensile', 0)),
				'scatto_anzianita': _safe_decimal(voci_tabella.get('scatto_anzianita')) or _safe_decimal(getattr(parametro, 'scatto_importo', 0)),
				'superminimo': _safe_decimal(voci_tabella.get('superminimo')) or _safe_decimal(getattr(parametro, 'superminimo_mensile', 0)),
				'el_dis_san': _safe_decimal(voci_tabella.get('el_dis_san')) or _safe_decimal(getattr(parametro, 'elemento_distinto_sanita', 0)),
				'el_dis_bil': _safe_decimal(voci_tabella.get('el_dis_bil')) or _safe_decimal(getattr(parametro, 'elemento_distinto_bilateralita', 0)),
			}
			for _k, _fallback in fallback_voci.items():
				info = voci_excel.setdefault(_k, {'presente': False, 'importo': Decimal('0.00')})
				importo = _safe_decimal(info.get('importo'))
				if importo <= 0 and _fallback > 0:
					info['importo'] = _fallback.quantize(Decimal('0.01'))
					info['presente'] = True

		busta_ore = _calcola_busta_paga_ore(
			parametro=parametro,
			ruolo=ruolo,
			coeff_ore=coeff_ore,
			giorni_lavorativi_mese=giorni_lavorativi_mese,
			giorni_lavorativi_settimana=None,   # usa default 6 CCNL turismo
			ore_settimanali=_safe_decimal(getattr(parametro, 'ore_settimanali', 40) or 40),
			n_domeniche=_n_domeniche_lavorate,
			giorni_lavorati=Decimal(str(_giorni_lavorati_senza_domeniche)),
			giorni_lavorati_escludono_domeniche=True,
			base_oraria_mensile=base_oraria_mensile,  # da config simulazione
		)

		paga_oraria_netta = Decimal('0.00')

		netto_giornaliero = Decimal('0.00')

		inail_unit = (lordo_unit * aliquota_inail).quantize(Decimal('0.01'))
		costo_azienda_unit = lordo_unit + inps_az_unit + tfr_unit + rateo_13_unit + rateo_14_unit + inail_unit
		decontrib_rule_name = ''
		decontrib_tipo = ''
		decontrib_valore = Decimal('0.0000')
		decontrib_risparmio_unit = Decimal('0.00')
		aliquota_inps_dip = Decimal('0.0919')

		fonte_calcolo_costo = 'legacy'
		if costo_lavoro_ruolo:
			fonte_calcolo_costo = 'costo_lavoro'
			inps_az_unit = costo_lavoro_ruolo['inps_azienda_unit']
			inail_unit = costo_lavoro_ruolo['inail_unit']
			tfr_unit = costo_lavoro_ruolo['tfr_unit']
			rateo_13_unit = costo_lavoro_ruolo['rateo_13_unit']
			rateo_14_unit = costo_lavoro_ruolo['rateo_14_unit']
			costo_azienda_unit = costo_lavoro_ruolo['costo_azienda_unit']
			aliquota_inps_dip = Decimal(str(costo_lavoro_ruolo.get('aliquota_inps_dipendente', Decimal('0.0919'))))
			decontrib_rule_name = str(costo_lavoro_ruolo.get('decontrib_rule_name', ''))
			decontrib_tipo = str(costo_lavoro_ruolo.get('decontrib_tipo', ''))
			decontrib_valore = Decimal(str(costo_lavoro_ruolo.get('decontrib_valore', 0)))
			decontrib_risparmio_unit = Decimal(str(costo_lavoro_ruolo.get('decontrib_risparmio_unit', 0)))

			tfr_giornaliero = (tfr_unit / Decimal(giorni_attivi)).quantize(Decimal('0.01')) if giorni_attivi else Decimal('0.00')
			rateo_13_giornaliero = (rateo_13_unit / Decimal(giorni_attivi)).quantize(Decimal('0.01')) if giorni_attivi else Decimal('0.00')
			rateo_14_giornaliero = (rateo_14_unit / Decimal(giorni_attivi)).quantize(Decimal('0.01')) if giorni_attivi else Decimal('0.00')

		# Maggiorazioni da calendario presenze/festività/chiusure (se censite)
		lordo_base_senza_magg = lordo_unit
		maggiorazioni = _calcola_maggiorazioni_da_calendario(
			azienda_operativa=azienda_operativa,
			ruolo=ruolo,
			parametro=parametro,
			mese_riferimento=mese_riferimento,
			inizio_mese=inizio_mese,
			fine_mese=fine_mese,
			lordo_unit=lordo_unit,
			ore_mensili_unit=ore_mensili_unit,
			tariffa_oraria_contrattuale=paga_oraria_contrattuale,
			giorni_chiusura_mese=giorni_chiusura_mese_date,
		)
		qta = Decimal(str(ruolo['quantita']))
		maggiorazioni_tot = _safe_decimal(maggiorazioni.get('totale_gruppo', _safe_decimal(maggiorazioni.get('totale')) * qta)).quantize(Decimal('0.01'))
		maggiorazioni_unit = (maggiorazioni_tot / qta).quantize(Decimal('0.01')) if qta > 0 else Decimal('0.00')
		ratei_inclusi_nel_netto = bool(maggiorazioni.get('includi_ratei_nel_netto', False))
		if maggiorazioni_unit > 0:
			lordo_unit = (lordo_unit + maggiorazioni_unit).quantize(Decimal('0.01'))
			if lordo_base_senza_magg > 0:
				coeff_inps_az = (inps_az_unit / lordo_base_senza_magg).quantize(Decimal('0.0001'))
				coeff_tfr = (tfr_unit / lordo_base_senza_magg).quantize(Decimal('0.0001'))
				coeff_13 = (rateo_13_unit / lordo_base_senza_magg).quantize(Decimal('0.0001'))
				coeff_14 = (rateo_14_unit / lordo_base_senza_magg).quantize(Decimal('0.0001'))
				delta_magg = (lordo_unit - lordo_base_senza_magg).quantize(Decimal('0.01'))
				inps_az_unit = (inps_az_unit + (delta_magg * coeff_inps_az)).quantize(Decimal('0.01'))
				tfr_unit = (tfr_unit + (delta_magg * coeff_tfr)).quantize(Decimal('0.01'))
				rateo_13_unit = (rateo_13_unit + (delta_magg * coeff_13)).quantize(Decimal('0.01'))
				rateo_14_unit = (rateo_14_unit + (delta_magg * coeff_14)).quantize(Decimal('0.01'))
			inail_unit = (lordo_unit * aliquota_inail).quantize(Decimal('0.01'))
			costo_azienda_unit = lordo_unit + inps_az_unit + tfr_unit + rateo_13_unit + rateo_14_unit + inail_unit

		netto_dettaglio = _calcola_netto_dipendente_con_regole(
			lordo=lordo_unit,
			aliquota_inps_dipendente=aliquota_inps_dip,
			irpef_cfg=irpef_cfg,
		)
		imponibile_irpef_unit = netto_dettaglio['imponibile']
		inps_dip_unit = netto_dettaglio['inps_dipendente']
		irpef_lorda_unit = netto_dettaglio['irpef_lorda']
		detrazioni_unit = netto_dettaglio['detrazioni']
		irpef_netta_unit = netto_dettaglio['irpef_netta']
		netto_unit = netto_dettaglio['netto']

		# Netto dipendente secondo schema logico orario (paga oraria lorda -> imponibile INPS)
		imponibile_inps_ore_unit = _safe_decimal((busta_ore or {}).get('imponibile_inps')).quantize(Decimal('0.01'))
		imponibile_irpef_ore_unit = _safe_decimal((busta_ore or {}).get('imponibile_irpef', imponibile_inps_ore_unit)).quantize(Decimal('0.01'))
		lordo_ore_unit = _safe_decimal((busta_ore or {}).get('lordo_totale', imponibile_inps_ore_unit)).quantize(Decimal('0.01'))
		netto_dettaglio_ore = _calcola_netto_dipendente_con_basi(
			lordo_totale=lordo_ore_unit,
			imponibile_inps=imponibile_inps_ore_unit,
			imponibile_irpef=imponibile_irpef_ore_unit,
			aliquota_inps_dipendente=aliquota_inps_dip,
			irpef_cfg=irpef_cfg,
		)
		imponibile_irpef_ore_unit = _safe_decimal(netto_dettaglio_ore.get('imponibile')).quantize(Decimal('0.01'))
		inps_dip_ore_unit = _safe_decimal(netto_dettaglio_ore.get('inps_dipendente')).quantize(Decimal('0.01'))
		irpef_lorda_ore_unit = _safe_decimal(netto_dettaglio_ore.get('irpef_lorda')).quantize(Decimal('0.01'))
		detrazioni_ore_unit = _safe_decimal(netto_dettaglio_ore.get('detrazioni')).quantize(Decimal('0.01'))
		irpef_netta_ore_unit = _safe_decimal(netto_dettaglio_ore.get('irpef_netta')).quantize(Decimal('0.01'))
		netto_ore_unit = _safe_decimal(netto_dettaglio_ore.get('netto')).quantize(Decimal('0.01'))

		# Bonus fiscali non imponibili: TI DL3/2020 + Bonus Art.1 c.4 L.207/2024
		# NON concorrono a INPS, IRPEF, 13ª, 14ª, TFR (vengono aggiunti al netto)
		inps_dip_base_bonus = (lordo_rapportato * aliquota_inps_dip).quantize(Decimal('0.01'))
		imponibile_annuo_bonus = ((lordo_rapportato - inps_dip_base_bonus) * Decimal('12')).quantize(Decimal('0.01'))
		ti_mensile_pieno = calcola_trattamento_integrativo(imponibile_annuo_bonus)
		l207_mensile_pieno = calcola_bonus_l207_2024(imponibile_annuo_bonus)
		trattamento_integrativo_unit = (ti_mensile_pieno * coeff_periodo).quantize(Decimal('0.01'))
		bonus_l207_unit = (l207_mensile_pieno * coeff_periodo).quantize(Decimal('0.01'))
		netto_unit = (netto_unit + trattamento_integrativo_unit + bonus_l207_unit).quantize(Decimal('0.01'))

		# Bonus fiscali anche sul tracciato "metodo ore"
		imponibile_annuo_bonus_ore = (imponibile_irpef_ore_unit * Decimal('12')).quantize(Decimal('0.01'))
		trattamento_integrativo_ore_unit = calcola_trattamento_integrativo(imponibile_annuo_bonus_ore).quantize(Decimal('0.01'))
		bonus_l207_ore_unit = calcola_bonus_l207_2024(imponibile_annuo_bonus_ore).quantize(Decimal('0.01'))
		netto_ore_unit = (netto_ore_unit + trattamento_integrativo_ore_unit + bonus_l207_ore_unit).quantize(Decimal('0.01'))

		if ore_mensili_unit > 0:
			paga_oraria_netta = (netto_unit / ore_mensili_unit).quantize(Decimal('0.01'))
		giorni_lavorativi_effettivi = (giorni_lavorativi_mese * coeff_periodo).quantize(Decimal('0.01'))
		if giorni_lavorativi_effettivi > 0:
			netto_giornaliero = (netto_unit / giorni_lavorativi_effettivi).quantize(Decimal('0.01'))

		netto_tot = (netto_unit * qta).quantize(Decimal('0.01'))
		netto_ore_tot = (netto_ore_unit * qta).quantize(Decimal('0.01'))
		lordo_tot = (lordo_unit * qta).quantize(Decimal('0.01'))
		imponibile_inps_ore_tot = (imponibile_inps_ore_unit * qta).quantize(Decimal('0.01'))
		imponibile_irpef_ore_tot = (imponibile_irpef_ore_unit * qta).quantize(Decimal('0.01'))
		inps_dip_ore_tot = (inps_dip_ore_unit * qta).quantize(Decimal('0.01'))
		irpef_lorda_ore_tot = (irpef_lorda_ore_unit * qta).quantize(Decimal('0.01'))
		detrazioni_ore_tot = (detrazioni_ore_unit * qta).quantize(Decimal('0.01'))
		irpef_netta_ore_tot = (irpef_netta_ore_unit * qta).quantize(Decimal('0.01'))
		trattamento_integrativo_ore_tot = (trattamento_integrativo_ore_unit * qta).quantize(Decimal('0.01'))
		bonus_l207_ore_tot = (bonus_l207_ore_unit * qta).quantize(Decimal('0.01'))
		inps_az_tot = (inps_az_unit * qta).quantize(Decimal('0.01'))
		inps_dip_tot = (inps_dip_unit * qta).quantize(Decimal('0.01'))
		imponibile_irpef_tot = (imponibile_irpef_unit * qta).quantize(Decimal('0.01'))
		irpef_lorda_tot = (irpef_lorda_unit * qta).quantize(Decimal('0.01'))
		detrazioni_tot = (detrazioni_unit * qta).quantize(Decimal('0.01'))
		irpef_netta_tot = (irpef_netta_unit * qta).quantize(Decimal('0.01'))
		tfr_tot = (tfr_unit * qta).quantize(Decimal('0.01'))
		rateo_13_tot = (rateo_13_unit * qta).quantize(Decimal('0.01'))
		rateo_14_tot = (rateo_14_unit * qta).quantize(Decimal('0.01'))
		inail_tot = (inail_unit * qta).quantize(Decimal('0.01'))
		decontrib_risparmio_tot = (decontrib_risparmio_unit * qta).quantize(Decimal('0.01'))
		trattamento_integrativo_tot = (trattamento_integrativo_unit * qta).quantize(Decimal('0.01'))
		bonus_l207_tot = (bonus_l207_unit * qta).quantize(Decimal('0.01'))
		costo_tot = (costo_azienda_unit * qta).quantize(Decimal('0.01'))
		rateo_ferie_unit = Decimal(str((costo_lavoro_ruolo or {}).get('rateo_ferie_unit', 0))).quantize(Decimal('0.01'))
		rateo_permessi_unit = Decimal(str((costo_lavoro_ruolo or {}).get('rateo_permessi_unit', 0))).quantize(Decimal('0.01'))
		rateo_ferie_tot = (rateo_ferie_unit * qta).quantize(Decimal('0.01'))
		rateo_permessi_tot = (rateo_permessi_unit * qta).quantize(Decimal('0.01'))
		costo_azienda_esteso_unit = Decimal(str((costo_lavoro_ruolo or {}).get('costo_azienda_esteso_unit', costo_azienda_unit))).quantize(Decimal('0.01'))
		costo_azienda_esteso_tot = (costo_azienda_esteso_unit * qta).quantize(Decimal('0.01'))
		ore_disponibili = (ore_mensili_unit * qta).quantize(Decimal('0.01'))

		totali['netto_mensile'] += netto_tot
		totali['lordo_mensile'] += lordo_tot
		totali['maggiorazioni'] += maggiorazioni_tot
		totali['trattamento_integrativo'] += trattamento_integrativo_tot
		totali['bonus_l207'] += bonus_l207_tot
		totali['inps_azienda'] += inps_az_tot
		totali['inps_dipendente'] += inps_dip_tot
		totali['irpef_lorda'] += irpef_lorda_tot
		totali['detrazioni_irpef'] += detrazioni_tot
		totali['irpef_netta'] += irpef_netta_tot
		totali['inail'] += inail_tot
		totali['tfr'] += tfr_tot
		totali['rateo_13'] += rateo_13_tot
		totali['rateo_14'] += rateo_14_tot
		totali['costo_azienda_totale'] += costo_tot
		totali['ore_disponibili_mese'] += ore_disponibili
		totali['decontrib_risparmio'] += decontrib_risparmio_tot

		ratei_lordi_tot = (tfr_tot + rateo_13_tot + rateo_14_tot).quantize(Decimal('0.01'))
		aliquota_media_ratei = (irpef_netta_tot / lordo_tot).quantize(Decimal('0.0001')) if lordo_tot > 0 else Decimal('0')
		if aliquota_media_ratei < Decimal('0'):
			aliquota_media_ratei = Decimal('0')
		ratei_imposte_tot = (ratei_lordi_tot * aliquota_media_ratei).quantize(Decimal('0.01'))
		ratei_netti_stimati = (ratei_lordi_tot - ratei_imposte_tot).quantize(Decimal('0.01'))

		righe.append({
			'ruolo_id': ruolo['id'],
			'ruolo': ruolo['label'],
			'nome': ruolo['nome'],
			'quantita': ruolo['quantita'],
			'qualifica': parametro.qualifica,
			'livello': parametro.livello,
			'tipo_contratto': tipo_contratto,
			'coefficiente_ore': coeff_ore,
			'data_inizio': data_inizio,
			'data_fine': data_fine,
			'giorni_attivi': giorni_attivi,
			'lordo_unit': lordo_unit,
			'netto_unit': netto_unit,
			'netto_tot': netto_tot,
			'netto_ore_unit': netto_ore_unit,
			'netto_ore_tot': netto_ore_tot,
			'imponibile_inps_ore_unit': imponibile_inps_ore_unit,
			'imponibile_inps_ore_tot': imponibile_inps_ore_tot,
			'imponibile_irpef_ore_unit': imponibile_irpef_ore_unit,
			'imponibile_irpef_ore_tot': imponibile_irpef_ore_tot,
			'inps_dipendente_ore_unit': inps_dip_ore_unit,
			'inps_dipendente_ore_tot': inps_dip_ore_tot,
			'irpef_lorda_ore_unit': irpef_lorda_ore_unit,
			'irpef_lorda_ore_tot': irpef_lorda_ore_tot,
			'detrazioni_ore_unit': detrazioni_ore_unit,
			'detrazioni_ore_tot': detrazioni_ore_tot,
			'irpef_netta_ore_unit': irpef_netta_ore_unit,
			'irpef_netta_ore_tot': irpef_netta_ore_tot,
			'trattamento_integrativo_ore_unit': trattamento_integrativo_ore_unit,
			'trattamento_integrativo_ore_tot': trattamento_integrativo_ore_tot,
			'bonus_l207_ore_unit': bonus_l207_ore_unit,
			'bonus_l207_ore_tot': bonus_l207_ore_tot,
			'imponibile_irpef_tot': imponibile_irpef_tot,
			'inps_azienda_tot': inps_az_tot,
			'inps_dipendente_tot': inps_dip_tot,
			'irpef_lorda_tot': irpef_lorda_tot,
			'detrazioni_tot': detrazioni_tot,
			'irpef_netta_tot': irpef_netta_tot,
			'inail_tot': inail_tot,
			'tfr_tot': tfr_tot,
			'tfr_giornaliero': tfr_giornaliero,
			'rateo_13_tot': rateo_13_tot,
			'rateo_13_giornaliero': rateo_13_giornaliero,
			'rateo_14_tot': rateo_14_tot,
			'rateo_14_giornaliero': rateo_14_giornaliero,
			'rateo_ferie_tot': rateo_ferie_tot,
			'rateo_permessi_tot': rateo_permessi_tot,
			'costo_azienda_tot': costo_tot,
			'costo_azienda_esteso_tot': costo_azienda_esteso_tot,
			'ore_mensili_unit': ore_mensili_unit,
			'ore_giornaliere_retribuite': ore_giornaliere_retribuite,
			'ore_disponibili': ore_disponibili,
			'paga_oraria_contrattuale': paga_oraria_contrattuale,
			'lavoro_ordinario_contrattuale_unit': lavoro_ordinario_contrattuale_unit,
			'paga_oraria_netta': paga_oraria_netta,
			'netto_giornaliero': netto_giornaliero,
			'aliquota_inps_dipendente': aliquota_inps_dip,
			'decontrib_rule_name': decontrib_rule_name,
			'decontrib_tipo': decontrib_tipo,
			'decontrib_valore': decontrib_valore,
			'decontrib_risparmio_tot': decontrib_risparmio_tot,
			'maggiorazioni_tot': maggiorazioni_tot,
			'maggiorazioni_dettaglio': maggiorazioni.get('dettaglio', {}),
			'maggiorazioni_ore': maggiorazioni.get('ore', {}),
			'maggiorazioni_giorni': maggiorazioni.get('giorni', {}),
			'maggiorazioni_percentuali': maggiorazioni.get('percentuali', {}),
			'maggiorazioni_tariffa_oraria': maggiorazioni.get('tariffa_oraria', Decimal('0.00')),
			'maggiorazioni_fonte': maggiorazioni.get('fonte', 'nessuna'),
			'maggiorazioni_per_unita': maggiorazioni.get('per_unita', []),
			'trattamento_integrativo_tot': trattamento_integrativo_tot,
			'bonus_l207_tot': bonus_l207_tot,
			'ratei_lordi_tot': ratei_lordi_tot,
			'ratei_imposte_tot': ratei_imposte_tot,
			'ratei_netti_stimati': ratei_netti_stimati,
			'ratei_inclusi_nel_netto': ratei_inclusi_nel_netto,
			'fonte_calcolo_costo': fonte_calcolo_costo,
			'mese_riferimento': mese_riferimento,
			'parametro_ccnl_id': parametro.id,
			'parametro_ccnl_totale_tabellare': parametro.totale_tabellare,
		'parametro_ccnl_contingenza': parametro.contingenza_mensile,
		'parametro_ccnl_paga_base': parametro.paga_base_mensile,
		'parametro_ccnl_superminimo': componenti_orarie_contratto.get('superminimo', Decimal('0.00')),
		'parametro_ccnl_scatti_anzianita': componenti_orarie_contratto.get('scatti_anzianita', Decimal('0.00')),
		'parametro_ccnl_el_dis_san_oraria': componenti_orarie_contratto.get('el_dis_san_oraria', Decimal('0.0000')),
		'parametro_ccnl_el_dis_san_mensile': componenti_orarie_contratto.get('el_dis_san_mensile', Decimal('0.00')),
		'part_time_percentuale': componenti_orarie_contratto.get('coeff_part_time_percent', Decimal('100.00')),
		'parametro_ccnl_validita_da': parametro.decorrenza_validita_da,
		'parametro_ccnl_validita_a': parametro.decorrenza_validita_a,
		# ── Calcolo ore (metodo alternativo basato su ore giornaliere) ────────
		'busta_ore': busta_ore,
	})

	indice_copertura_turni = Decimal('0')
	if ore_totali_turni_mese > 0:
		indice_copertura_turni = (totali['ore_disponibili_mese'] / ore_totali_turni_mese).quantize(Decimal('0.01'))

	totali['inps_totale'] = totali['inps_azienda'] + totali['inps_dipendente']
	totali['accantonamenti_totali'] = totali['tfr'] + totali['rateo_13'] + totali['rateo_14']
	totali['totale_mese_netto_dipendente'] = totali['netto_mensile']
	totali['totale_mese_dipendente'] = (
		totali['netto_mensile']
		+ totali['inps_dipendente']
		+ totali['irpef_netta']
	).quantize(Decimal('0.01'))
	totali['totale_mese_azienda'] = (
		totali['inps_azienda']
		+ totali['rateo_13']
		+ totali['rateo_14']
		+ totali['tfr']
	).quantize(Decimal('0.01'))
	totali['totale_mese_complessivo'] = (
		totali['netto_mensile']
		+ totali['inps_dipendente']
		+ totali['irpef_netta']
		+ totali['inps_azienda']
		+ totali['rateo_13']
		+ totali['rateo_14']
		+ totali['tfr']
	).quantize(Decimal('0.01'))
	totali['totale_f24_mese'] = (
		totali['inps_azienda']
		+ totali['inps_dipendente']
		+ totali['inail']
		+ totali['irpef_netta']
	).quantize(Decimal('0.01'))
	totali['costo_totale_annuo'] = (totali['costo_azienda_totale'] * Decimal('12')).quantize(Decimal('0.01'))
	totali['totale_annuo_netto_dipendente'] = (totali['totale_mese_netto_dipendente'] * Decimal('12')).quantize(Decimal('0.01'))
	totali['totale_annuo_complessivo'] = (totali['totale_mese_complessivo'] * Decimal('12')).quantize(Decimal('0.01'))

	quadratura = {
		'paghe_lorde': totali['lordo_mensile'],
		'imposte_contributi_dipendente': (totali['inps_dipendente'] + totali['irpef_netta']).quantize(Decimal('0.01')),
		'oneri_azienda': (totali['inps_azienda'] + totali['inail']).quantize(Decimal('0.01')),
		'imposte_lorde_totali': (
			totali['inps_dipendente']
			+ totali['irpef_netta']
			+ totali['inps_azienda']
			+ totali['inail']
		).quantize(Decimal('0.01')),
		'bonus_fiscali': (totali.get('trattamento_integrativo', Decimal('0')) + totali.get('bonus_l207', Decimal('0'))).quantize(Decimal('0.01')),
		'ratei': (totali['rateo_13'] + totali['rateo_14'] + totali['tfr']).quantize(Decimal('0.01')),
	}
	quadratura['totale_quadratura'] = (
		quadratura['paghe_lorde']
		+ quadratura['imposte_lorde_totali']
		+ quadratura['bonus_fiscali']
		+ quadratura['ratei']
	).quantize(Decimal('0.01'))
	riepilogo_generale_righe, riepilogo_generale_totali = _build_riepilogo_generale_da_righe(righe)

	return {
		'righe': righe,
		'totali': totali,
		'quadratura': quadratura,
		'riepilogo_generale_righe': riepilogo_generale_righe,
		'riepilogo_generale_totali': riepilogo_generale_totali,
		'azienda_operativa': azienda_operativa,
		'usa_costo_lavoro': COSTO_LAVORO_ENABLED,
		'ruoli_config': ruoli_config,
		'parametri_ccnl': parametri_ccnl,
		'tipi_contratto': tipi_contratto,
		'mese_riferimento': mese_riferimento,
		'mese_inizio': inizio_mese,
		'mese_fine': fine_mese,
		'giorni_nel_mese': giorni_nel_mese,
		'ore_turno_pranzo': ore_turno_pranzo,
		'ore_turno_cena': ore_turno_cena,
		'base_oraria_mensile': base_oraria_mensile,
		'giorni_lavorativi_mese': giorni_lavorativi_mese,
		'ore_totali_turni_mese': ore_totali_turni_mese,
		'aliquota_inail_perc': aliquota_inail_perc,
		'giorni_chiusura_mese_date': giorni_chiusura_mese_date,
		'giorni_chiusura_mese': [d.isoformat() for d in giorni_chiusura_mese_date],
		'indice_copertura_turni': indice_copertura_turni,
	}
def _to_primitive(value):
	if isinstance(value, Decimal):
		return float(value)
	if isinstance(value, (datetime, date)):
		return value.isoformat()
	if isinstance(value, Model):
		return {
			'id': value.pk,
			'model': value._meta.label,
			'label': str(value),
		}
	if isinstance(value, list):
		return [_to_primitive(v) for v in value]
	if isinstance(value, dict):
		return {k: _to_primitive(v) for k, v in value.items()}
	return value


def _salva_simulazione(request, context):
	azienda_operativa = _get_azienda_operativa_per_utente(request.user, request.session)
	if not azienda_operativa:
		return None

	parametri = {
		'mese_riferimento': context.get('mese_riferimento'),
		'ore_turno_pranzo': context.get('ore_turno_pranzo'),
		'ore_turno_cena': context.get('ore_turno_cena'),
		'base_oraria_mensile': context.get('base_oraria_mensile'),
		'giorni_lavorativi_mese': context.get('giorni_lavorativi_mese'),
		'giorni_chiusura_mese': context.get('giorni_chiusura_mese', []),
		'aliquota_inail_perc': context.get('aliquota_inail_perc'),
		'ruoli_config': context.get('ruoli_config', []),
	}

	profili_ruolo = []
	for r in context.get('righe', []):
		if r.get('missing'):
			continue
		tc = r.get('tipo_contratto')
		profili_ruolo.append({
			'ruolo_id': r.get('ruolo_id'),
			'ruolo': r.get('ruolo'),
			'nome': r.get('nome'),
			'quantita': r.get('quantita'),
			'livello': r.get('livello'),
			'qualifica': r.get('qualifica'),
			'tipo_contratto_id': tc.id if tc else None,
			'tipo_contratto_nome': tc.nome if tc else None,
			'coefficiente_ore': r.get('coefficiente_ore'),
			'data_inizio': r.get('data_inizio'),
			'data_fine': r.get('data_fine'),
		})

	risultato = {
		'totali': context.get('totali', {}),
		'righe': context.get('righe', []),
		'quadratura': context.get('quadratura', {}),
		'indice_copertura_turni': context.get('indice_copertura_turni'),
		'riepilogo_generale_righe': context.get('riepilogo_generale_righe', []),
		'riepilogo_generale_totali': context.get('riepilogo_generale_totali', {}),
		'riepilogo_mensile_righe': context.get('riepilogo_mensile_righe', []),
		'riepilogo_mensile_totali_colonne': context.get('riepilogo_mensile_totali_colonne', {}),
		'profili_ruolo': profili_ruolo,
	}

	def _salva_voci_excel(record_obj):
		if not record_obj:
			return

		SimulazioneVoceRetributivaOre.objects.filter(simulazione=record_obj).delete()

		for ruolo_cfg in context.get('ruoli_config', []):
			rid = str(ruolo_cfg.get('id') or '')
			if not rid:
				continue
			voci = ruolo_cfg.get('voci_excel') or {}
			for voce_code in ['minimo_tabellare', 'contingenza', 'el_dis_san', 'scatto_anzianita', 'superminimo', 'el_dis_bil']:
				voce = voci.get(voce_code) or {}
				SimulazioneVoceRetributivaOre.objects.create(
					simulazione=record_obj,
					azienda=azienda_operativa,
					mese_riferimento=context.get('mese_riferimento', timezone.localdate().strftime('%Y-%m')),
					ruolo_id=rid,
					ruolo_label=str(ruolo_cfg.get('label') or ''),
					dipendente_nome=str(ruolo_cfg.get('nome') or ''),
					voce=voce_code,
					presente=bool(voce.get('presente', False)),
					importo_lordo=_safe_decimal(voce.get('importo')).quantize(Decimal('0.01')),
				)

	# Simulazione unica per azienda: aggiorna la esistente invece di creare record multipli
	record = SimulazioneOrganico.objects.filter(azienda=azienda_operativa).order_by('id').first()
	if record:
		record.utente = request.user
		record.mese_riferimento = context.get('mese_riferimento', timezone.localdate().strftime('%Y-%m'))
		record.parametri_json = _to_primitive(parametri)
		record.risultato_json = _to_primitive(risultato)
		record.querystring = request.GET.urlencode()
		record.save(update_fields=['utente', 'mese_riferimento', 'parametri_json', 'risultato_json', 'querystring'])
		_salva_voci_excel(record)
		return record

	record = SimulazioneOrganico.objects.create(
		azienda=azienda_operativa,
		utente=request.user,
		mese_riferimento=context.get('mese_riferimento', timezone.localdate().strftime('%Y-%m')),
		parametri_json=_to_primitive(parametri),
		risultato_json=_to_primitive(risultato),
		querystring=request.GET.urlencode(),
	)
	_salva_voci_excel(record)
	return record


def _get_record_simulazione_tabella(request):
	"""Restituisce il record simulazione unico dell'azienda operativa, se presente."""
	azienda_operativa = _get_azienda_operativa_per_utente(request.user, request.session)
	if not azienda_operativa:
		return None
	return SimulazioneOrganico.objects.filter(azienda=azienda_operativa).order_by('id').first()


def _record_simulazione_necessita_refresh(record):
	"""Rileva record salvati con struttura vecchia, da ricalcolare per dettaglio maggiorazioni."""
	if not record:
		return True
	righe = (record.risultato_json or {}).get('righe', [])
	if not righe:
		return True
	for r in righe:
		if 'maggiorazioni_tot' not in r:
			return True
		if 'maggiorazioni_fonte' not in r:
			return True
		if 'maggiorazioni_per_unita' not in r:
			return True
		if 'paga_oraria_contrattuale' not in r:
			return True
		if 'lavoro_ordinario_contrattuale_unit' not in r:
			return True
		if 'ore_giornaliere_retribuite' not in r:
			return True
		if 'busta_ore' not in r:
			return True
		if 'netto_ore_tot' not in r:
			return True
	return False


def _context_da_record_simulazione(record):
	"""Costruisce il context della pagina risultato leggendo SOLO il record salvato."""
	parametri = record.parametri_json or {}
	risultato = record.risultato_json or {}
	mese_riferimento = parametri.get('mese_riferimento', record.mese_riferimento)
	inizio_mese, fine_mese, giorni_nel_mese = periodo_mese_da_riferimento(mese_riferimento)
	ore_turno_pranzo = _safe_decimal(parametri.get('ore_turno_pranzo', Decimal('8')))
	ore_turno_cena = _safe_decimal(parametri.get('ore_turno_cena', Decimal('8')))
	giorni_lavorativi_mese = _safe_decimal(parametri.get('giorni_lavorativi_mese', Decimal('26')))
	righe = risultato.get('righe', [])
	for r in righe:
		r.setdefault('maggiorazioni_tot', Decimal('0.00'))
		r.setdefault('maggiorazioni_dettaglio', {})
		r.setdefault('maggiorazioni_ore', {})
		r.setdefault('maggiorazioni_giorni', {})
		r.setdefault('maggiorazioni_percentuali', {})
		r.setdefault('maggiorazioni_tariffa_oraria', Decimal('0.00'))
		r.setdefault('maggiorazioni_fonte', 'nessuna')
		r.setdefault('maggiorazioni_per_unita', [])
		r.setdefault('ratei_inclusi_nel_netto', False)
		if 'paga_oraria_contrattuale' not in r:
			r['paga_oraria_contrattuale'] = _safe_decimal(r.get('maggiorazioni_tariffa_oraria')).quantize(Decimal('0.0001'))
		if 'lavoro_ordinario_contrattuale_unit' not in r:
			ore_unit = _safe_decimal(r.get('ore_mensili_unit'))
			tariffa = _safe_decimal(r.get('paga_oraria_contrattuale'))
			r['lavoro_ordinario_contrattuale_unit'] = (tariffa * ore_unit).quantize(Decimal('0.01')) if ore_unit > 0 else Decimal('0.00')
		r.setdefault('part_time_percentuale', Decimal('100.00'))
		r.setdefault('parametro_ccnl_superminimo', Decimal('0.00'))
		r.setdefault('parametro_ccnl_scatti_anzianita', Decimal('0.00'))
		r.setdefault('parametro_ccnl_el_dis_san_oraria', Decimal('0.0000'))
		r.setdefault('parametro_ccnl_el_dis_san_mensile', Decimal('0.00'))
		r.setdefault('ore_giornaliere_retribuite', Decimal('0.00'))
		r.setdefault('busta_ore', {})
		r.setdefault('netto_ore_unit', _safe_decimal(r.get('netto_unit')))
		r.setdefault('netto_ore_tot', _safe_decimal(r.get('netto_tot')))
		r.setdefault('imponibile_inps_ore_unit', _safe_decimal((r.get('busta_ore') or {}).get('imponibile_inps')))
		r.setdefault('imponibile_inps_ore_tot', _safe_decimal(r.get('imponibile_inps_ore_unit')) * _safe_decimal(r.get('quantita', 1)))
		r.setdefault('imponibile_irpef_ore_unit', _safe_decimal(r.get('imponibile_irpef_tot')))
		r.setdefault('imponibile_irpef_ore_tot', _safe_decimal(r.get('imponibile_irpef_tot')))
		r.setdefault('inps_dipendente_ore_unit', _safe_decimal(r.get('inps_dipendente_tot')))
		r.setdefault('inps_dipendente_ore_tot', _safe_decimal(r.get('inps_dipendente_tot')))
		r.setdefault('irpef_lorda_ore_unit', _safe_decimal(r.get('irpef_lorda_tot')))
		r.setdefault('irpef_lorda_ore_tot', _safe_decimal(r.get('irpef_lorda_tot')))
		r.setdefault('detrazioni_ore_unit', _safe_decimal(r.get('detrazioni_tot')))
		r.setdefault('detrazioni_ore_tot', _safe_decimal(r.get('detrazioni_tot')))
		r.setdefault('irpef_netta_ore_unit', _safe_decimal(r.get('irpef_netta_tot')))
		r.setdefault('irpef_netta_ore_tot', _safe_decimal(r.get('irpef_netta_tot')))
		r.setdefault('trattamento_integrativo_ore_unit', _safe_decimal(r.get('trattamento_integrativo_tot')))
		r.setdefault('trattamento_integrativo_ore_tot', _safe_decimal(r.get('trattamento_integrativo_tot')))
		r.setdefault('bonus_l207_ore_unit', _safe_decimal(r.get('bonus_l207_tot')))
		r.setdefault('bonus_l207_ore_tot', _safe_decimal(r.get('bonus_l207_tot')))
		ratei_lordi = (
			_safe_decimal(r.get('tfr_tot'))
			+ _safe_decimal(r.get('rateo_13_tot'))
			+ _safe_decimal(r.get('rateo_14_tot'))
		).quantize(Decimal('0.01'))
		if 'ratei_lordi_tot' not in r:
			r['ratei_lordi_tot'] = ratei_lordi
		if 'ratei_imposte_tot' not in r:
			lordo_tot = _safe_decimal(r.get('lordo_tot'))
			irpef_netta_tot = _safe_decimal(r.get('irpef_netta_tot'))
			aliquota_media = (irpef_netta_tot / lordo_tot).quantize(Decimal('0.0001')) if lordo_tot > 0 else Decimal('0')
			if aliquota_media < Decimal('0'):
				aliquota_media = Decimal('0')
			r['ratei_imposte_tot'] = (ratei_lordi * aliquota_media).quantize(Decimal('0.01'))
		if 'ratei_netti_stimati' not in r:
			r['ratei_netti_stimati'] = (ratei_lordi - _safe_decimal(r.get('ratei_imposte_tot'))).quantize(Decimal('0.01'))
	riepilogo_generale_righe = risultato.get('riepilogo_generale_righe', [])
	riepilogo_generale_totali = risultato.get('riepilogo_generale_totali', {})
	if not riepilogo_generale_righe and righe:
		riepilogo_generale_righe, riepilogo_generale_totali = _build_riepilogo_generale_da_righe(righe)

	return {
		'righe': righe,
		'totali': risultato.get('totali', {}),
		'quadratura': risultato.get('quadratura', {}),
		'mese_inizio': inizio_mese,
		'mese_fine': fine_mese,
		'giorni_nel_mese': giorni_nel_mese,
		'ore_totali_turni_mese': ((ore_turno_pranzo + ore_turno_cena) * giorni_lavorativi_mese).quantize(Decimal('0.01')),
		'usa_costo_lavoro': COSTO_LAVORO_ENABLED,
		'riepilogo_generale_righe': riepilogo_generale_righe,
		'riepilogo_generale_totali': riepilogo_generale_totali,
		'mese_riferimento': mese_riferimento,
		'ore_turno_pranzo': ore_turno_pranzo,
		'ore_turno_cena': ore_turno_cena,
		'base_oraria_mensile': parametri.get('base_oraria_mensile', Decimal('173.33')),
		'giorni_lavorativi_mese': giorni_lavorativi_mese,
		'giorni_chiusura_mese': parametri.get('giorni_chiusura_mese', []),
		'aliquota_inail_perc': parametri.get('aliquota_inail_perc', Decimal('1.20')),
		'ruoli_config': parametri.get('ruoli_config', []),
		'indice_copertura_turni': risultato.get('indice_copertura_turni', Decimal('0')),
		'riepilogo_mensile_righe': risultato.get('riepilogo_mensile_righe', []),
		'riepilogo_mensile_totali_colonne': risultato.get('riepilogo_mensile_totali_colonne', {}),
	}


def _safe_decimal(value):
	try:
		return Decimal(str(value or 0))
	except Exception:
		return Decimal('0')


def _get_flags_imponibilita_voce(codice_voce):
	"""Ritorna i flag imponibilità per codice voce (fallback: imponibile su tutto)."""
	default = {
		'imponibile_inps': True,
		'imponibile_inail': True,
		'imponibile_irpef': True,
		'imponibile_parziale': False,
	}
	if not codice_voce:
		return default
	# Fallback espliciti per codici TeamSystem usati in conciliazione cedolino.
	# Usati quando la voce non e' ancora censita in VoceRetributiva.
	cod_norm = str(codice_voce).strip().upper()
	fallback_ts = {
		'8108': default,  # FEST. NON GODUTA (ore): competenza imponibile
	}
	if cod_norm in fallback_ts:
		return fallback_ts[cod_norm]
	try:
		voce = (
			VoceRetributiva.objects
			.filter(codice__iexact=cod_norm, attivo=True)
			.only('imponibile_inps', 'imponibile_inail', 'imponibile_irpef', 'imponibile_parziale')
			.first()
		)
		if not voce:
			return default
		return {
			'imponibile_inps': bool(voce.imponibile_inps),
			'imponibile_inail': bool(voce.imponibile_inail),
			'imponibile_irpef': bool(voce.imponibile_irpef),
			'imponibile_parziale': bool(voce.imponibile_parziale),
		}
	except Exception:
		return default


def _calcola_busta_paga_ore(
	*,
	parametro,
	ruolo,
	coeff_ore,
	giorni_lavorativi_mese,
	giorni_lavorativi_settimana=None,
	ore_settimanali=None,
	n_domeniche=0,
	giorni_lavorati=None,
	giorni_lavorati_escludono_domeniche=False,
	base_oraria_mensile=None,
):
	"""Calcolo retribuzione mensile basato sulle ore giornaliere (metodo immagine specifica).

	Logica:
	  1. ore_contrattuali = base_oraria_mensile del CCNL (172 o 173,33)
	     Il "divisore" è: ore_contrattuali / ore_settimanali = giorni_lavorativi_settimana
	     → giorni_lavorativi_mese = divisore (es. 172/40 * 6 = 25,80 oppure 173,33/40 * 6 = 26)
	  2. ore_settimanali_FT = 40 (da CCNL o parametro)
	  3. giorni_lavorativi_settimana_FT = 6 (CCNL turismo)
	  4. ore_giornaliere_FT = ore_settimanali_FT / giorni_lavorativi_settimana_FT
	  5. ore_giornaliere = ore_giornaliere_FT * coeff_ore  (part-time)
	  6. Paga base oraria = Minimo Tabellare / ore_contrattuali
	  7. Contingenza oraria = Contingenza mensile / ore_contrattuali
	  8. Scatti anzianità orari = scatto_importo * n_scatti / ore_contrattuali
	  9. Superminimo orario = superminimo / ore_contrattuali
	 10. EL.DIS.SAN. orario = el_dis_san / ore_contrattuali
	 11. Paga oraria totale = somma voci orarie
	 12. Giorni lavorati ordinari = giorni_lavorati - n_domeniche
	 13. Paga ordinaria = giorni_lav_ordinari * ore_giornaliere * paga_oraria
	 14. Paga domenicale = n_domeniche * ore_giornaliere * paga_oraria
	 15. Imponibile INPS = paga_ordinaria + paga_domenicale (arrotondato per eccesso)
	"""
	# ── Ore contrattuali (divisore): priorità a base_oraria_mensile da config ─
	# 1° priorità: base_oraria_mensile dal form di configurazione simulazione
	# 2° priorità: parametro.ore_mensili dal CCNL
	# 3° fallback: ore_settimanali * 4.3333
	# 4° fallback finale: 173.33
	ore_contrattuali_ft = _safe_decimal(base_oraria_mensile) if base_oraria_mensile else Decimal('0')
	if ore_contrattuali_ft <= 0:
		ore_contrattuali_ft = _safe_decimal(getattr(parametro, 'ore_mensili', 0))
	if ore_contrattuali_ft <= 0:
		ore_contrattuali_ft = _safe_decimal(getattr(parametro, 'ore_settimanali', 40) or 40) * Decimal('4.3333')
	if ore_contrattuali_ft <= 0:
		ore_contrattuali_ft = Decimal('173.33')
	ore_contrattuali_ft = ore_contrattuali_ft.quantize(Decimal('0.01'))

	# ── Ore settimanali FT (dal parametro, default 40) ───────────────────────
	ore_sett_ft = _safe_decimal(ore_settimanali) if ore_settimanali else Decimal('0')
	if ore_sett_ft <= 0:
		ore_sett_ft = _safe_decimal(getattr(parametro, 'ore_settimanali', 40) or 40)
	if ore_sett_ft <= 0:
		ore_sett_ft = Decimal('40')

	# ── Giorni lavorativi per settimana FT (default 6 CCNL turismo) ─────────
	giorni_sett_ft = _safe_decimal(giorni_lavorativi_settimana) if giorni_lavorativi_settimana else Decimal('0')
	if giorni_sett_ft <= 0:
		# Calcola dal divisore: ore_contrattuali / ore_sett_ft * (ore_sett_ft / ore_giornaliere_std)
		# Metodo diretto: dividiamo ore_contrattuali per ore_sett_ft per ottenere le settimane,
		# poi ricaviamo i giorni: standard CCNL turismo = 6
		giorni_sett_ft = Decimal('6')

	# ── Ore giornaliere FT e rapportate al part-time ─────────────────────────
	ore_giornaliere_ft = (ore_sett_ft / giorni_sett_ft).quantize(Decimal('0.0001'))
	coeff_pt = _safe_decimal(coeff_ore)
	if coeff_pt <= 0:
		coeff_pt = Decimal('1.00')
	ore_giornaliere = (ore_giornaliere_ft * coeff_pt).quantize(Decimal('0.0001'))

	# ── Ore contrattuali ──────────────────────────────────────────────────────
	# ATTENZIONE: per le voci orarie da importi mensili (minimo/contingenza/scatti/
	# superminimo) il divisore corretto è SEMPRE FT, non rapportato al part-time.
	# Il part-time incide sulle ore lavorate (ore_giornaliere), non sulla tariffa oraria base.
	ore_contrattuali = ore_contrattuali_ft

	voci_excel = ruolo.get('voci_excel', {}) if isinstance(ruolo, dict) else {}
	voci_tabella = _carica_voci_retributive_da_tabella(parametro)

	def _voce_valore(voce_code, fallback, forza_fallback_se_positivo=False):
		voce = voci_excel.get(voce_code, {}) if isinstance(voci_excel, dict) else {}
		fallback_dec = _safe_decimal(fallback).quantize(Decimal('0.01'))
		val = _safe_decimal(voce.get('importo'))
		if val > 0:
			return val.quantize(Decimal('0.01')), True
		presente = bool(voce.get('presente', False))
		if not presente:
			if forza_fallback_se_positivo and fallback_dec > 0:
				return fallback_dec, True
			return Decimal('0.00'), False
		return fallback_dec, True

	# ── Voci mensili lorde dal parametro CCNL/scheda ruolo ───────────────────
	minimo_tabellare_default = _safe_decimal(voci_tabella.get('minimo_tabellare')) or _safe_decimal(getattr(parametro, 'minimo_tabellare', 0))
	if minimo_tabellare_default <= 0:
		minimo_tabellare_default = _safe_decimal(getattr(parametro, 'paga_base_mensile', 0))

	paga_base_mensile, p_minimo = _voce_valore('minimo_tabellare', minimo_tabellare_default)
	contingenza_mensile, p_contingenza = _voce_valore('contingenza', _safe_decimal(voci_tabella.get('contingenza')) or getattr(parametro, 'contingenza_mensile', 0))

	superminimo_ruolo = ruolo.get('superminimo_mensile', ruolo.get('superminimo')) if isinstance(ruolo, dict) else None
	superminimo_default = superminimo_ruolo if superminimo_ruolo not in (None, '') else (_safe_decimal(voci_tabella.get('superminimo')) or getattr(parametro, 'superminimo_mensile', 0))
	superminimo_mensile, p_superminimo = _voce_valore('superminimo', superminimo_default, forza_fallback_se_positivo=True)

	scatto_importo = _safe_decimal(getattr(parametro, 'scatto_importo', 0)).quantize(Decimal('0.01'))
	numero_scatti_ruolo = 0
	if isinstance(ruolo, dict):
		numero_scatti_ruolo = int(_safe_decimal(ruolo.get('numero_scatti', ruolo.get('scatti_anzianita', 0))))
	if numero_scatti_ruolo <= 0 and scatto_importo > 0:
		numero_scatti_ruolo = 1
	if _safe_decimal(voci_tabella.get('scatto_anzianita')) > 0:
		scatto_default = _safe_decimal(voci_tabella.get('scatto_anzianita')).quantize(Decimal('0.01'))
	else:
		scatto_default = (scatto_importo * Decimal(str(numero_scatti_ruolo))).quantize(Decimal('0.01'))
	scatti_mensili, p_scatti = _voce_valore('scatto_anzianita', scatto_default, forza_fallback_se_positivo=True)

	# ── EL.DIS.SAN e EL.DIS.BIL: già in €/ora — NON dividere per ore_contrattuali ─
	# Sorgenti (in ordine di priorità): ParametroVoceRetributiva → ruolo → voci_excel → CCNL parametro
	el_dis_san_orario = _safe_decimal(voci_tabella.get('el_dis_san'))  # da ParametroVoceRetributiva (importo_orario)
	if el_dis_san_orario <= 0:
		_eds_ruolo = ruolo.get('el_dis_san_oraria', ruolo.get('elemento_distinto_sanita')) if isinstance(ruolo, dict) else None
		if _eds_ruolo not in (None, ''):
			el_dis_san_orario = _safe_decimal(_eds_ruolo)
	if el_dis_san_orario <= 0:
		_vex_eds = voci_excel.get('el_dis_san', {}) if isinstance(voci_excel, dict) else {}
		el_dis_san_orario = _safe_decimal(_vex_eds.get('importo'))
	if el_dis_san_orario <= 0:
		el_dis_san_orario = _safe_decimal(getattr(parametro, 'elemento_distinto_sanita', 0))
	el_dis_san_orario = el_dis_san_orario.quantize(Decimal('0.00001'))
	el_dis_san_mensile = (el_dis_san_orario * ore_contrattuali_ft).quantize(Decimal('0.01'))  # valore mensile (solo display)
	p_el_dis_san = el_dis_san_orario > 0

	el_dis_bil_orario = _safe_decimal(voci_tabella.get('el_dis_bil'))  # da ParametroVoceRetributiva (importo_orario)
	if el_dis_bil_orario <= 0:
		_edb_ruolo = ruolo.get('el_dis_bil_oraria', ruolo.get('elemento_distinto_bilateralita')) if isinstance(ruolo, dict) else None
		if _edb_ruolo not in (None, ''):
			el_dis_bil_orario = _safe_decimal(_edb_ruolo)
	if el_dis_bil_orario <= 0:
		_vex_edb = voci_excel.get('el_dis_bil', {}) if isinstance(voci_excel, dict) else {}
		el_dis_bil_orario = _safe_decimal(_vex_edb.get('importo'))
	if el_dis_bil_orario <= 0:
		el_dis_bil_orario = _safe_decimal(getattr(parametro, 'elemento_distinto_bilateralita', 0))
	el_dis_bil_orario = el_dis_bil_orario.quantize(Decimal('0.00001'))
	el_dis_bil_mensile = (el_dis_bil_orario * ore_contrattuali_ft).quantize(Decimal('0.01'))  # valore mensile (solo display)
	p_el_dis_bil = el_dis_bil_orario > 0

	# ── Voci orarie: mensile / ore_contrattuali FT (solo voci con importo mensile) ─
	if ore_contrattuali > 0:
		paga_base_oraria = (paga_base_mensile / ore_contrattuali).quantize(Decimal('0.0001'))
		contingenza_oraria = (contingenza_mensile / ore_contrattuali).quantize(Decimal('0.0001'))
		superminimo_orario = (superminimo_mensile / ore_contrattuali).quantize(Decimal('0.0001'))
		scatti_orari = (scatti_mensili / ore_contrattuali).quantize(Decimal('0.0001'))
		# el_dis_san_orario e el_dis_bil_orario già calcolati sopra come €/ora diretti
	else:
		paga_base_oraria = contingenza_oraria = superminimo_orario = scatti_orari = Decimal('0.0000')
		el_dis_san_orario = el_dis_bil_orario = Decimal('0.00000')

	paga_oraria_totale = (
		paga_base_oraria + contingenza_oraria + superminimo_orario + scatti_orari + el_dis_san_orario + el_dis_bil_orario
	).quantize(Decimal('0.0001'))

	# ── Giorni lavorati e domeniche ───────────────────────────────────────────
	giorni_lav = _safe_decimal(giorni_lavorati) if giorni_lavorati is not None else _safe_decimal(giorni_lavorativi_mese)
	n_dom = _safe_decimal(n_domeniche)
	if giorni_lavorati_escludono_domeniche:
		giorni_ordinari = max(giorni_lav, Decimal('0')).quantize(Decimal('0.01'))
	else:
		giorni_ordinari = max(giorni_lav - n_dom, Decimal('0')).quantize(Decimal('0.01'))

	# ── Paga ordinaria e domenicale ───────────────────────────────────────────
	paga_ordinaria = (giorni_ordinari * ore_giornaliere * paga_oraria_totale).quantize(Decimal('0.01'))
	paga_domenicale = (n_dom * ore_giornaliere * paga_oraria_totale).quantize(Decimal('0.01'))
	lordo_totale = (paga_ordinaria + paga_domenicale).quantize(Decimal('0.01'))

	# ── Basi imponibili da classificazione voci (VoceRetributiva) ─────────────
	ore_totali = ((giorni_ordinari + n_dom) * ore_giornaliere).quantize(Decimal('0.0001'))
	voci_orarie = {
		'minimo_tabellare': (paga_base_oraria, 'PAGA_BASE'),
		'contingenza': (contingenza_oraria, 'CONTINGENZA'),
		'scatto_anzianita': (scatti_orari, 'SCATTO_ANZ'),
		'superminimo': (superminimo_orario, 'SUPERMINIMO'),
		'el_dis_san': (el_dis_san_orario, 'EL_DIS_SAN'),
		'el_dis_bil': (el_dis_bil_orario, 'EL_DIS_BIL'),
	}

	imponibile_inps = Decimal('0.00')
	imponibile_inail = Decimal('0.00')
	imponibile_irpef = Decimal('0.00')
	for _k, (_importo_orario, _codice_voce) in voci_orarie.items():
		if _importo_orario <= 0:
			continue
		_importo_voce = (_importo_orario * ore_totali).quantize(Decimal('0.01'))
		flag = _get_flags_imponibilita_voce(_codice_voce)
		# Per voci parziali la gestione franchigia è demandata ai motori specifici;
		# nel metodo ore standard si applica la quota piena della voce.
		if flag.get('imponibile_inps', True):
			imponibile_inps += _importo_voce
		if flag.get('imponibile_inail', True):
			imponibile_inail += _importo_voce
		if flag.get('imponibile_irpef', True):
			imponibile_irpef += _importo_voce

	imponibile_inps = imponibile_inps.quantize(Decimal('0.01'))
	imponibile_inail = imponibile_inail.quantize(Decimal('0.01'))
	imponibile_irpef = imponibile_irpef.quantize(Decimal('0.01'))

	return {
		# Dati di input ricostruiti
		'ore_contrattuali_ft': ore_contrattuali_ft,
		'ore_contrattuali': ore_contrattuali,
		'ore_sett_ft': ore_sett_ft,
		'giorni_sett_ft': giorni_sett_ft,
		'ore_giornaliere_ft': ore_giornaliere_ft,
		'ore_giornaliere': ore_giornaliere,
		'coeff_part_time': coeff_pt,
		# Voci orarie
		'paga_base_oraria': paga_base_oraria,
		'contingenza_oraria': contingenza_oraria,
		'superminimo_orario': superminimo_orario,
		'scatti_orari': scatti_orari,
		'el_dis_san_orario': el_dis_san_orario,
		'el_dis_bil_orario': el_dis_bil_orario,
		'paga_oraria_totale': paga_oraria_totale,
		# Voci mensili input
		'paga_base_mensile': paga_base_mensile,
		'contingenza_mensile': contingenza_mensile,
		'superminimo_mensile': superminimo_mensile,
		'scatti_mensili': scatti_mensili,
		'el_dis_san_mensile': el_dis_san_mensile,
		'el_dis_bil_mensile': el_dis_bil_mensile,
		'presenze_voci': {
			'minimo_tabellare': p_minimo,
			'contingenza': p_contingenza,
			'scatto_anzianita': p_scatti,
			'superminimo': p_superminimo,
			'el_dis_san': p_el_dis_san,
			'el_dis_bil': p_el_dis_bil,
		},
		# Risultati
		'giorni_lavorati': giorni_lav,
		'n_domeniche': n_dom,
		'giorni_ordinari': giorni_ordinari,
		'paga_ordinaria': paga_ordinaria,
		'paga_domenicale': paga_domenicale,
		'lordo_totale': lordo_totale,
		'imponibile_inps': imponibile_inps,
		'imponibile_inail': imponibile_inail,
		'imponibile_irpef': imponibile_irpef,
	}


def _is_data_festiva(data_giorno, azienda_operativa):
	"""Verifica se una data è festiva (domenica o festività da tabella nazionale/locale)."""
	if not data_giorno:
		return False
	if data_giorno.weekday() == 6:
		return True

	provincia = str(getattr(azienda_operativa, 'provincia', '') or '').strip().upper()
	comune = str(getattr(azienda_operativa, 'comune', '') or '').strip().upper()
	regione = str(getattr(azienda_operativa, 'regione', '') or '').strip().upper()

	filters = Q(livello='nazionale')
	if regione:
		filters |= Q(livello='regionale', regione__iexact=regione)
	if provincia:
		filters |= Q(livello='provinciale', provincia__iexact=provincia)
	if comune:
		filters |= Q(livello='comunale', comune__iexact=comune)

	return FestivitaCalendario.objects.filter(data=data_giorno, attivo=True).filter(filters).exists()


def _get_percentuale_maggiorazione(parametro, anno, tipo_maggiorazione, fallback=Decimal('0')):
	"""Recupera la percentuale maggiorazione da tabella CCNL parametrica con fallback legacy."""
	ccnl_key = _normalizza_ccnl_key(getattr(parametro, 'ccnl', ''))
	ccnl_obj = _get_ccnl_db_attivo(ccnl_key, anno)
	if not ccnl_obj:
		return fallback

	obj = (
		ParametroMaggiorazione.objects.filter(
			ccnl=ccnl_obj,
			tipo_maggiorazione=tipo_maggiorazione,
			anno=anno,
			attivo=True,
		)
		.order_by('-data_validita_da')
		.first()
	)
	if not obj:
		return fallback
	return Decimal(str(obj.percentuale or 0)).quantize(Decimal('0.01'))


def _date_range(inizio, fine):
	giorno = inizio
	while giorno <= fine:
		yield giorno
		giorno = giorno + timedelta(days=1)


def _get_giorni_chiusura_non_lavorativi(azienda_operativa, inizio_mese, fine_mese):
	giorni = set()
	if not azienda_operativa:
		return giorni
	chiusure = ChiusuraAziendale.objects.filter(
		azienda=azienda_operativa,
		attivo=True,
		data_inizio__lte=fine_mese,
		data_fine__gte=inizio_mese,
		trattamento__in=['ferie', 'riposo_compensativo', 'chiusura_non_retribuita'],
	)
	for chiusura in chiusure:
		for giorno in _date_range(max(inizio_mese, chiusura.data_inizio), min(fine_mese, chiusura.data_fine)):
			giorni.add(giorno)
	return giorni


def _calcola_maggiorazioni_da_calendario(
	*,
	azienda_operativa,
	ruolo,
	parametro,
	mese_riferimento,
	inizio_mese,
	fine_mese,
	lordo_unit,
	ore_mensili_unit,
	tariffa_oraria_contrattuale=None,
	giorni_chiusura_mese=None,
):
	"""Calcola maggiorazioni da calendario presenze o, in fallback, dalla composizione del mese."""
	default = {
		'totale': Decimal('0.00'),
		'dettaglio': {
			'straordinario_diurno': Decimal('0.00'),
			'straordinario_notturno': Decimal('0.00'),
			'straordinario_festivo': Decimal('0.00'),
			'lavoro_domenicale': Decimal('0.00'),
			'lavoro_festivo': Decimal('0.00'),
		},
		'ore': {
			'straordinario_diurno': Decimal('0.00'),
			'straordinario_notturno': Decimal('0.00'),
			'straordinario_festivo': Decimal('0.00'),
			'lavoro_domenicale': Decimal('0.00'),
			'lavoro_festivo': Decimal('0.00'),
		},
		'giorni': {
			'domeniche': 0,
			'festivita': 0,
			'giorni_lavorabili_base': 0,
		},
		'percentuali': {
			'straordinario_diurno': Decimal('0.00'),
			'straordinario_notturno': Decimal('0.00'),
			'straordinario_festivo': Decimal('0.00'),
			'lavoro_domenicale': Decimal('0.00'),
			'lavoro_festivo': Decimal('0.00'),
		},
		'tariffa_oraria': Decimal('0.00'),
		'includi_ratei_nel_netto': False,
		'fonte': 'nessuna',
	}
	if not azienda_operativa:
		return default

	try:
		anno = int(str(mese_riferimento).split('-')[0])
	except Exception:
		anno = timezone.localdate().year

	perc_straord_diurno = _get_percentuale_maggiorazione(
		parametro,
		anno,
		'straordinario_feriale',
		fallback=Decimal(str(getattr(parametro, 'straordinario_diurno_maggiorazione', 0) or 0)),
	)
	perc_straord_notturno = _get_percentuale_maggiorazione(
		parametro,
		anno,
		'straordinario_notturno',
		fallback=Decimal(str(getattr(parametro, 'straordinario_notturno_maggiorazione', 0) or 0)),
	)
	perc_straord_festivo = _get_percentuale_maggiorazione(
		parametro,
		anno,
		'straordinario_festivo',
		fallback=Decimal(str(getattr(parametro, 'straordinario_festivo_maggiorazione', 0) or 0)),
	)
	perc_domenicale = _get_percentuale_maggiorazione(parametro, anno, 'lavoro_domenicale', fallback=Decimal('30.00'))
	perc_festivo = _get_percentuale_maggiorazione(parametro, anno, 'lavoro_festivo', fallback=Decimal('20.00'))

	default['percentuali'] = {
		'straordinario_diurno': perc_straord_diurno,
		'straordinario_notturno': perc_straord_notturno,
		'straordinario_festivo': perc_straord_festivo,
		'lavoro_domenicale': perc_domenicale,
		'lavoro_festivo': perc_festivo,
	}

	if ore_mensili_unit <= 0 or lordo_unit <= 0:
		return default

	tariffa_oraria = _safe_decimal(tariffa_oraria_contrattuale)
	if tariffa_oraria <= 0:
		tariffa_oraria = (lordo_unit / ore_mensili_unit).quantize(Decimal('0.0001'))
	default['tariffa_oraria'] = tariffa_oraria

	calendario = (
		CalendarioPresenzeDipendente.objects.filter(
			azienda=azienda_operativa,
			mese_riferimento=mese_riferimento,
		)
		.filter(
			Q(ruolo_riferimento=str(ruolo.get('id', '')))
			| Q(ruolo_riferimento__startswith=f"{ruolo.get('id', '')}__")
		)
		.order_by('-data_modifica')
	)

	if not calendario.exists():
		data_inizio_ruolo = parse_iso_date(ruolo.get('data_inizio')) or inizio_mese
		data_fine_ruolo = parse_iso_date(ruolo.get('data_fine')) or fine_mese
		inizio_attivo = max(inizio_mese, data_inizio_ruolo)
		fine_attivo = min(fine_mese, data_fine_ruolo)
		if fine_attivo < inizio_attivo:
			return default

		festivita_date = {f.data for f in _festivita_mese(azienda_operativa, inizio_mese, fine_mese)}
		giorni_chiusura_set = set(giorni_chiusura_mese or []) | _get_giorni_chiusura_non_lavorativi(azienda_operativa, inizio_mese, fine_mese)
		giorni_domenicali = 0
		giorni_festivi = 0
		giorni_lavorabili_base = 0

		for giorno in _date_range(inizio_attivo, fine_attivo):
			if giorno in giorni_chiusura_set:
				continue
			if giorno.weekday() == 6:
				giorni_domenicali += 1
			elif giorno in festivita_date:
				giorni_festivi += 1
			else:
				giorni_lavorabili_base += 1

		divisore = giorni_lavorabili_base or max(1, (fine_attivo - inizio_attivo).days + 1)
		ore_medie_giornaliere = (ore_mensili_unit / Decimal(str(divisore))).quantize(Decimal('0.01'))
		ore_domenicale = (ore_medie_giornaliere * Decimal(str(giorni_domenicali))).quantize(Decimal('0.01'))
		ore_festivo = (ore_medie_giornaliere * Decimal(str(giorni_festivi))).quantize(Decimal('0.01'))
		imp_domenicale = (tariffa_oraria * ore_domenicale * (perc_domenicale / Decimal('100'))).quantize(Decimal('0.01'))
		imp_festivo = (tariffa_oraria * ore_festivo * (perc_festivo / Decimal('100'))).quantize(Decimal('0.01'))

		return {
			**default,
			'totale': (imp_domenicale + imp_festivo).quantize(Decimal('0.01')),
			'dettaglio': {
				**default['dettaglio'],
				'lavoro_domenicale': imp_domenicale,
				'lavoro_festivo': imp_festivo,
			},
			'ore': {
				**default['ore'],
				'lavoro_domenicale': ore_domenicale,
				'lavoro_festivo': ore_festivo,
			},
			'giorni': {
				'domeniche': giorni_domenicali,
				'festivita': giorni_festivi,
				'giorni_lavorabili_base': giorni_lavorabili_base,
			},
			'fonte': 'stima_calendario_mese',
		}

	ore_straord_diurno = sum((_safe_decimal(c.ore_straordinario_diurno) for c in calendario), Decimal('0.00'))
	ore_straord_notturno = sum((_safe_decimal(c.ore_straordinario_notturno) for c in calendario), Decimal('0.00'))
	ore_straord_festivo = sum((_safe_decimal(c.ore_straordinario_festivo) for c in calendario), Decimal('0.00'))
	ore_domenicale = sum((_safe_decimal(c.ore_lavoro_domenicale) for c in calendario), Decimal('0.00'))
	ore_festivo = sum((_safe_decimal(c.ore_lavoro_festivo) for c in calendario), Decimal('0.00'))
	applica_chiusure = all(bool(c.applica_chiusure_aziendali) for c in calendario)
	includi_ratei_nel_netto = any(bool(c.includi_ratei_nel_netto) for c in calendario)

	if applica_chiusure:
		chiusure = ChiusuraAziendale.objects.filter(
			azienda=azienda_operativa,
			attivo=True,
			data_inizio__lte=fine_mese,
			data_fine__gte=inizio_mese,
		)
		if chiusure.filter(trattamento__in=['ferie', 'riposo_compensativo', 'chiusura_non_retribuita']).exists() or giorni_chiusura_mese:
			ore_domenicale = Decimal('0.00')
			ore_festivo = Decimal('0.00')

	imp_straord_diurno = (tariffa_oraria * ore_straord_diurno * (Decimal('1') + (perc_straord_diurno / Decimal('100')))).quantize(Decimal('0.01'))
	imp_straord_notturno = (tariffa_oraria * ore_straord_notturno * (Decimal('1') + (perc_straord_notturno / Decimal('100')))).quantize(Decimal('0.01'))
	imp_straord_festivo = (tariffa_oraria * ore_straord_festivo * (Decimal('1') + (perc_straord_festivo / Decimal('100')))).quantize(Decimal('0.01'))
	imp_domenicale = (tariffa_oraria * ore_domenicale * (perc_domenicale / Decimal('100'))).quantize(Decimal('0.01'))
	imp_festivo = (tariffa_oraria * ore_festivo * (perc_festivo / Decimal('100'))).quantize(Decimal('0.01'))

	totale = (imp_straord_diurno + imp_straord_notturno + imp_straord_festivo + imp_domenicale + imp_festivo).quantize(Decimal('0.01'))

	return {
		**default,
		'totale': totale,
		'dettaglio': {
			'straordinario_diurno': imp_straord_diurno,
			'straordinario_notturno': imp_straord_notturno,
			'straordinario_festivo': imp_straord_festivo,
			'lavoro_domenicale': imp_domenicale,
			'lavoro_festivo': imp_festivo,
		},
		'ore': {
			'straordinario_diurno': ore_straord_diurno,
			'straordinario_notturno': ore_straord_notturno,
			'straordinario_festivo': ore_straord_festivo,
			'lavoro_domenicale': ore_domenicale,
			'lavoro_festivo': ore_festivo,
		},
		'includi_ratei_nel_netto': includi_ratei_nel_netto,
		'fonte': 'calendario_presenze',
	}


def _iter_mesi_da_riferimento(mese_riferimento, numero_mesi=12):
	try:
		year_str, month_str = mese_riferimento.split('-')
		start_year = int(year_str)
		start_month = int(month_str)
	except Exception:
		today = timezone.localdate()
		start_year = today.year
		start_month = today.month

	for i in range(numero_mesi):
		idx = (start_month - 1) + i
		y = start_year + (idx // 12)
		m = (idx % 12) + 1
		yield f"{y:04d}-{m:02d}"


def _pasqua_data(anno):
	"""Calcolo Pasqua (Meeus/Jones/Butcher)."""
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


def _festivita_nazionali_fallback_mese(inizio_mese, fine_mese):
	"""Fallback festività nazionali se la tabella non copre l'anno richiesto."""
	anno = inizio_mese.year
	fisse = [
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
	results = []
	for mese, giorno, nome in fisse:
		d = date(anno, mese, giorno)
		if inizio_mese <= d <= fine_mese:
			results.append(SimpleNamespace(data=d, nome=nome, livello='nazionale'))

	pasqua = _pasqua_data(anno)
	pasquetta = pasqua + timedelta(days=1)
	for d, nome in [(pasqua, 'Pasqua'), (pasquetta, "Lunedì dell'Angelo")]:
		if inizio_mese <= d <= fine_mese:
			results.append(SimpleNamespace(data=d, nome=nome, livello='nazionale'))

	return results


def _festivita_mese(azienda_operativa, inizio_mese, fine_mese):
	provincia = str(getattr(azienda_operativa, 'provincia', '') or '').strip().upper()
	comune = str(getattr(azienda_operativa, 'comune', '') or '').strip().upper()
	regione = str(getattr(azienda_operativa, 'regione', '') or '').strip().upper()

	filters = Q(livello='nazionale')
	if regione:
		filters |= Q(livello='regionale', regione__iexact=regione)
	if provincia:
		filters |= Q(livello='provinciale', provincia__iexact=provincia)
	if comune:
		filters |= Q(livello='comunale', comune__iexact=comune)

	festivita_db = list(
		FestivitaCalendario.objects.filter(
			attivo=True,
			data__gte=inizio_mese,
			data__lte=fine_mese,
		)
		.filter(filters)
		.order_by('data', 'nome')
	)

	# Se manca copertura tabellare per l'anno/mese, integra con fallback nazionale.
	key_presenti = {(f.data, (f.nome or '').strip().lower()) for f in festivita_db}
	for f in _festivita_nazionali_fallback_mese(inizio_mese, fine_mese):
		key = (f.data, (f.nome or '').strip().lower())
		if key not in key_presenti:
			festivita_db.append(f)

	return sorted(festivita_db, key=lambda x: (x.data, (x.nome or '').lower()))


def _costruisci_stats_mese_calendario(azienda_operativa, mese_riferimento):
	inizio_mese, fine_mese, giorni_nel_mese = periodo_mese_da_riferimento(mese_riferimento)
	festivita = _festivita_mese(azienda_operativa, inizio_mese, fine_mese) if azienda_operativa else []
	festivita_date = {f.data for f in festivita}

	conteggi = {
		'giorni_nel_mese': giorni_nel_mese,
		'sabati': 0,
		'domeniche': 0,
		'festivita_infrasettimanali': 0,
		'giorni_feriali': 0,
	}

	giorno = inizio_mese
	while giorno <= fine_mese:
		if giorno.weekday() == 5:
			conteggi['sabati'] += 1
		elif giorno.weekday() == 6:
			conteggi['domeniche'] += 1
		elif giorno in festivita_date:
			conteggi['festivita_infrasettimanali'] += 1
		else:
			conteggi['giorni_feriali'] += 1
		giorno = giorno + timedelta(days=1)

	return {
		'inizio_mese': inizio_mese,
		'fine_mese': fine_mese,
		'conteggi': conteggi,
		'festivita': festivita,
		'chiusure': list(
			ChiusuraAziendale.objects.filter(
				azienda=azienda_operativa,
				attivo=True,
				data_inizio__lte=fine_mese,
				data_fine__gte=inizio_mese,
			).order_by('data_inizio')
		) if azienda_operativa else [],
	}


def _decimal_post(request, key, default='0'):
	return _safe_decimal(request.POST.get(key, default))


@login_required
@user_passes_test(_is_admin_only)
def simulazione_organico_calendario(request):
	"""Gestione mensile presenze/assenze/straordinari per i profili simulati."""
	azienda_operativa = _get_azienda_operativa_per_utente(request.user, request.session)
	if not azienda_operativa:
		return HttpResponseForbidden('Azienda operativa non selezionata.')

	record = _get_record_simulazione_tabella(request)
	mese_riferimento = request.GET.get('mese_riferimento') or request.POST.get('mese_riferimento')
	if not mese_riferimento:
		mese_riferimento = (record and record.mese_riferimento) or timezone.localdate().strftime('%Y-%m')

	querystring_origine = request.GET.urlencode() if request.method == 'GET' else request.POST.get('querystring_origine', '')

	if record and record.parametri_json:
		ruoli_config = record.parametri_json.get('ruoli_config', []) or []
	else:
		ruoli_config = _build_ruoli_config(request)

	ruoli_attivi = [r for r in ruoli_config if int(r.get('quantita', 0) or 0) > 0]
	ruoli_unita = []
	for ruolo in ruoli_attivi:
		quantita = int(ruolo.get('quantita', 0) or 0)
		for idx in range(1, quantita + 1):
			id_unita = str(ruolo.get('id')) if quantita == 1 else f"{ruolo.get('id')}__{idx}"
			label_unita = ruolo.get('label') if quantita == 1 else f"{ruolo.get('label')} #{idx}"
			ruoli_unita.append({
				**ruolo,
				'id_unita': id_unita,
				'label_unita': label_unita,
				'indice_unita': idx,
			})
	stats_mese = _costruisci_stats_mese_calendario(azienda_operativa, mese_riferimento)

	if request.method == 'POST':
		action = request.POST.get('action', 'save_calendario')
		if action == 'save_calendario':
			for ruolo in ruoli_unita:
				ruolo_id = str(ruolo.get('id_unita'))
				CalendarioPresenzeDipendente.objects.update_or_create(
					azienda=azienda_operativa,
					ruolo_riferimento=ruolo_id,
					mese_riferimento=mese_riferimento,
					defaults={
						'giorni_presenza': _decimal_post(request, f'{ruolo_id}_giorni_presenza'),
						'giorni_assenza': _decimal_post(request, f'{ruolo_id}_giorni_assenza'),
						'giorni_ferie': _decimal_post(request, f'{ruolo_id}_giorni_ferie'),
						'giorni_riposo_compensativo': _decimal_post(request, f'{ruolo_id}_giorni_riposo_compensativo'),
						'ore_straordinario_diurno': _decimal_post(request, f'{ruolo_id}_ore_straordinario_diurno'),
						'ore_straordinario_notturno': _decimal_post(request, f'{ruolo_id}_ore_straordinario_notturno'),
						'ore_straordinario_festivo': _decimal_post(request, f'{ruolo_id}_ore_straordinario_festivo'),
						'ore_lavoro_domenicale': _decimal_post(request, f'{ruolo_id}_ore_lavoro_domenicale'),
						'ore_lavoro_festivo': _decimal_post(request, f'{ruolo_id}_ore_lavoro_festivo'),
						'applica_chiusure_aziendali': bool(request.POST.get(f'{ruolo_id}_applica_chiusure_aziendali')),
						'includi_ratei_nel_netto': bool(request.POST.get(f'{ruolo_id}_includi_ratei_nel_netto')),
						'note': request.POST.get(f'{ruolo_id}_note', ''),
					},
				)
			messages.success(request, 'Calendario presenze e maggiorazioni salvato correttamente.')
		elif action == 'add_chiusura':
			data_inizio_raw = request.POST.get('chiusura_data_inizio', '')
			data_fine_raw = request.POST.get('chiusura_data_fine', '')
			trattamento = request.POST.get('chiusura_trattamento', 'ferie')
			descrizione = (request.POST.get('chiusura_descrizione', '') or '').strip()
			try:
				data_inizio = datetime.strptime(data_inizio_raw, '%Y-%m-%d').date()
				data_fine = datetime.strptime(data_fine_raw, '%Y-%m-%d').date()
				if data_fine < data_inizio:
					raise ValueError('Intervallo date non valido')
				ChiusuraAziendale.objects.create(
					azienda=azienda_operativa,
					data_inizio=data_inizio,
					data_fine=data_fine,
					trattamento=trattamento,
					descrizione=descrizione,
					attivo=True,
				)
				messages.success(request, 'Chiusura aziendale aggiunta con successo.')
			except Exception:
				messages.error(request, 'Impossibile aggiungere la chiusura aziendale: verifica date e campi.')
		elif action == 'delete_chiusura':
			chiusura_id = request.POST.get('chiusura_id')
			try:
				ChiusuraAziendale.objects.filter(id=int(chiusura_id), azienda=azienda_operativa).delete()
				messages.success(request, 'Chiusura aziendale eliminata.')
			except Exception:
				messages.error(request, 'Impossibile eliminare la chiusura selezionata.')

		redirect_url = f"{request.path}?mese_riferimento={mese_riferimento}"
		if querystring_origine:
			redirect_url += f"&{querystring_origine}"
		return redirect(redirect_url)

	calendari_map = {
		c.ruolo_riferimento: c
		for c in CalendarioPresenzeDipendente.objects.filter(
			azienda=azienda_operativa,
			mese_riferimento=mese_riferimento,
			ruolo_riferimento__in=[str(r.get('id_unita')) for r in ruoli_unita],
		)
	}

	ruoli_calendario = []
	for ruolo in ruoli_unita:
		ruolo_id = str(ruolo.get('id_unita'))
		cal = calendari_map.get(ruolo_id)
		ruoli_calendario.append({
			**ruolo,
			'calendario': cal,
		})

	return render(
		request,
		'rapporto_di_lavoro/simulazione_organico_calendario.html',
		{
			'mese_riferimento': mese_riferimento,
			'ruoli_calendario': ruoli_calendario,
			'querystring_origine': querystring_origine,
			'stats_mese': stats_mese,
			'chiusura_trattamenti': ChiusuraAziendale.TRATTAMENTO_CHOICES,
			'record_simulazione': record,
		},
	)


def _calcola_totali_mese_da_input(
	mese_riferimento,
	ruoli_config,
	parametri_ccnl,
	tipi_contratto,
	aliquota_inail_perc,
	base_oraria_mensile=Decimal('173.33'),
	azienda_operativa=None,
	giorni_lavorativi_mese=None,
	giorni_chiusura_mese=None,
):
	inizio_mese, fine_mese, giorni_nel_mese = periodo_mese_da_riferimento(mese_riferimento)
	aliquota_inail = aliquota_inail_perc / Decimal('100')
	rule_engine = RuleEngine() if COSTO_LAVORO_ENABLED else None
	irpef_cfg = _estrai_parametri_irpef_da_rules(rule_engine)
	if giorni_lavorativi_mese is None:
		giorni_lavorativi_mese = Decimal('26')
	else:
		giorni_lavorativi_mese = Decimal(str(giorni_lavorativi_mese))

	totali = {
		'netto_mensile': Decimal('0'),
		'lordo_mensile': Decimal('0'),
		'maggiorazioni': Decimal('0'),
		'trattamento_integrativo': Decimal('0'),
		'bonus_l207': Decimal('0'),
		'inps_azienda': Decimal('0'),
		'inps_dipendente': Decimal('0'),
		'irpef_lorda': Decimal('0'),
		'detrazioni_irpef': Decimal('0'),
		'irpef_netta': Decimal('0'),
		'inail': Decimal('0'),
		'tfr': Decimal('0'),
		'rateo_13': Decimal('0'),
		'rateo_14': Decimal('0'),
		'costo_azienda_totale': Decimal('0'),
		'totale_f24_mese': Decimal('0'),
		'decontrib_risparmio': Decimal('0'),
	}
	righe_mese = []

	for ruolo in ruoli_config:
		if ruolo['quantita'] <= 0:
			continue

		parametro = parametri_ccnl.filter(livello=ruolo['livello']).first()
		if not parametro:
			continue

		tipo_contratto = None
		coeff_ore = Decimal('1.00')
		if ruolo['tipo_contratto_id']:
			try:
				tipo_contratto = tipi_contratto.get(id=int(ruolo['tipo_contratto_id']))
				coeff_ore = Decimal(str(tipo_contratto.coefficiente_ore or Decimal('1.00')))
			except (TipoContratto.DoesNotExist, ValueError):
				pass

		data_inizio = parse_iso_date(ruolo.get('data_inizio'))
		data_fine = parse_iso_date(ruolo.get('data_fine'))
		giorni_attivi = calcola_giorni_attivi_mese(inizio_mese, fine_mese, data_inizio, data_fine)
		coeff_periodo = Decimal('0')
		if giorni_nel_mese > 0:
			coeff_periodo = (Decimal(giorni_attivi) / Decimal(giorni_nel_mese)).quantize(Decimal('0.0001'))

		lordo_imponibile = (
			Decimal(str(parametro.contingenza_mensile or 0))
			+ Decimal(str(parametro.paga_base_mensile or 0))
		)
		if lordo_imponibile == 0:
			lordo_imponibile = Decimal(str(parametro.importo_lordo_mensile))
		lordo_rapportato = (lordo_imponibile * coeff_ore).quantize(Decimal('0.01'))

		costo_lavoro_ruolo = _calcola_costo_azienda_ruolo_costo_lavoro(
			parametro=parametro,
			coeff_ore=coeff_ore,
			giorni_lavorativi_mese=giorni_lavorativi_mese,
			giorni_attivi=giorni_attivi,
			mese_riferimento=mese_riferimento,
			aliquota_inail=aliquota_inail,
			azienda_operativa=azienda_operativa,
			rule_engine=rule_engine,
			ruolo=ruolo,
		)

		calcolo = calcola_base_simulazione_motore_unico(
			parametro=parametro,
			tipo_contratto=tipo_contratto,
			anno=inizio_mese.year,
			mese=inizio_mese.month,
			azienda=azienda_operativa,
			data_inizio=data_inizio,
			data_fine=data_fine,
			lordo_fallback=lordo_rapportato,
		)

		lordo_unit_base = Decimal(str(calcolo['lordo_mensile']))
		netto_unit_base = Decimal(str(calcolo['netto']['netto']))
		inps_az_unit_base = Decimal(str(calcolo['costo_azienda']['inps_azienda']))
		inps_dip_unit_base = Decimal(str(calcolo['netto']['inps_dipendente']))
		tfr_unit_base = Decimal(str(calcolo['costo_azienda']['tfr']))
		rateo_13_unit_base = Decimal(str(calcolo['costo_azienda']['rateo_13']))
		rateo_14_unit_base = Decimal(str(calcolo['costo_azienda']['rateo_14']))

		lordo_unit = (lordo_unit_base * coeff_periodo).quantize(Decimal('0.01'))
		inps_az_unit = (inps_az_unit_base * coeff_periodo).quantize(Decimal('0.01'))
		tfr_unit = (tfr_unit_base * coeff_periodo).quantize(Decimal('0.01'))
		rateo_13_unit = (rateo_13_unit_base * coeff_periodo).quantize(Decimal('0.01'))
		rateo_14_unit = (rateo_14_unit_base * coeff_periodo).quantize(Decimal('0.01'))
		ore_retribuite = calcola_ore_retribuite_contrattuali(
			base_oraria_mensile=base_oraria_mensile,
			giorni_lavorativi_mese=giorni_lavorativi_mese,
			coeff_ore=coeff_ore,
			coeff_periodo=coeff_periodo,
		)
		ore_mensili_unit = ore_retribuite['ore_mensili_retribuite']

		paga_oraria_contratto = calcola_paga_oraria_contrattuale(
			parametro=parametro,
			ruolo=ruolo,
			coeff_ore=coeff_ore,
			coeff_periodo=coeff_periodo,
			ore_mensili_unit=ore_mensili_unit,
			giorni_lavorativi_mese=giorni_lavorativi_mese,
		)
		paga_oraria_contrattuale = _safe_decimal(paga_oraria_contratto.get('paga_oraria')).quantize(Decimal('0.0001'))

		inail_unit = (lordo_unit * aliquota_inail).quantize(Decimal('0.01'))
		costo_azienda_unit = lordo_unit + inps_az_unit + tfr_unit + rateo_13_unit + rateo_14_unit + inail_unit
		aliquota_inps_dip = Decimal('0.0919')
		decontrib_rule_name = ''
		decontrib_tipo = ''
		decontrib_valore = Decimal('0.0000')
		decontrib_risparmio_unit = Decimal('0.00')

		fonte_calcolo_costo = 'legacy'
		if costo_lavoro_ruolo:
			fonte_calcolo_costo = 'costo_lavoro'
			inps_az_unit = costo_lavoro_ruolo['inps_azienda_unit']
			inail_unit = costo_lavoro_ruolo['inail_unit']
			tfr_unit = costo_lavoro_ruolo['tfr_unit']
			rateo_13_unit = costo_lavoro_ruolo['rateo_13_unit']
			rateo_14_unit = costo_lavoro_ruolo['rateo_14_unit']
			costo_azienda_unit = costo_lavoro_ruolo['costo_azienda_unit']
			aliquota_inps_dip = Decimal(str(costo_lavoro_ruolo.get('aliquota_inps_dipendente', Decimal('0.0919'))))
			decontrib_rule_name = str(costo_lavoro_ruolo.get('decontrib_rule_name', ''))
			decontrib_tipo = str(costo_lavoro_ruolo.get('decontrib_tipo', ''))
			decontrib_valore = Decimal(str(costo_lavoro_ruolo.get('decontrib_valore', 0)))
			decontrib_risparmio_unit = Decimal(str(costo_lavoro_ruolo.get('decontrib_risparmio_unit', 0)))

		lordo_base_senza_magg = lordo_unit
		maggiorazioni = _calcola_maggiorazioni_da_calendario(
			azienda_operativa=azienda_operativa,
			ruolo=ruolo,
			parametro=parametro,
			mese_riferimento=mese_riferimento,
			inizio_mese=inizio_mese,
			fine_mese=fine_mese,
			lordo_unit=lordo_unit,
			ore_mensili_unit=ore_mensili_unit,
			tariffa_oraria_contrattuale=paga_oraria_contrattuale,
			giorni_chiusura_mese=giorni_chiusura_mese,
		)
		qta = Decimal(str(ruolo['quantita']))
		maggiorazioni_tot = _safe_decimal(maggiorazioni.get('totale_gruppo', _safe_decimal(maggiorazioni.get('totale')) * qta)).quantize(Decimal('0.01'))
		maggiorazioni_unit = (maggiorazioni_tot / qta).quantize(Decimal('0.01')) if qta > 0 else Decimal('0.00')
		ratei_inclusi_nel_netto = bool(maggiorazioni.get('includi_ratei_nel_netto', False))
		if maggiorazioni_unit > 0:
			lordo_unit = (lordo_unit + maggiorazioni_unit).quantize(Decimal('0.01'))
			if lordo_base_senza_magg > 0:
				coeff_inps_az = (inps_az_unit / lordo_base_senza_magg).quantize(Decimal('0.0001'))
				coeff_tfr = (tfr_unit / lordo_base_senza_magg).quantize(Decimal('0.0001'))
				coeff_13 = (rateo_13_unit / lordo_base_senza_magg).quantize(Decimal('0.0001'))
				coeff_14 = (rateo_14_unit / lordo_base_senza_magg).quantize(Decimal('0.0001'))
				delta_magg = (lordo_unit - lordo_base_senza_magg).quantize(Decimal('0.01'))
				inps_az_unit = (inps_az_unit + (delta_magg * coeff_inps_az)).quantize(Decimal('0.01'))
				tfr_unit = (tfr_unit + (delta_magg * coeff_tfr)).quantize(Decimal('0.01'))
				rateo_13_unit = (rateo_13_unit + (delta_magg * coeff_13)).quantize(Decimal('0.01'))
				rateo_14_unit = (rateo_14_unit + (delta_magg * coeff_14)).quantize(Decimal('0.01'))
			inail_unit = (lordo_unit * aliquota_inail).quantize(Decimal('0.01'))
			costo_azienda_unit = lordo_unit + inps_az_unit + tfr_unit + rateo_13_unit + rateo_14_unit + inail_unit

		netto_dettaglio = _calcola_netto_dipendente_con_regole(
			lordo=lordo_unit,
			aliquota_inps_dipendente=aliquota_inps_dip,
			irpef_cfg=irpef_cfg,
		)
		imponibile_irpef_unit = netto_dettaglio['imponibile']
		netto_unit = netto_dettaglio['netto']
		inps_dip_unit = netto_dettaglio['inps_dipendente']
		irpef_lorda_unit = netto_dettaglio['irpef_lorda']
		detrazioni_unit = netto_dettaglio['detrazioni']
		irpef_netta_unit = netto_dettaglio['irpef_netta']

		# Bonus fiscali non imponibili (non concorrono a INPS/IRPEF/13ª/14ª/TFR)
		# Base soglia: imponibile fiscale annualizzato su mensilità piena rapportata ore (non giorni)
		inps_dip_base_bonus = (lordo_rapportato * aliquota_inps_dip).quantize(Decimal('0.01'))
		imponibile_annuo_bonus = ((lordo_rapportato - inps_dip_base_bonus) * Decimal('12')).quantize(Decimal('0.01'))
		ti_mensile_pieno = calcola_trattamento_integrativo(imponibile_annuo_bonus)
		l207_mensile_pieno = calcola_bonus_l207_2024(imponibile_annuo_bonus)
		trattamento_integrativo_unit = (ti_mensile_pieno * coeff_periodo).quantize(Decimal('0.01'))
		bonus_l207_unit = (l207_mensile_pieno * coeff_periodo).quantize(Decimal('0.01'))
		netto_unit = (netto_unit + trattamento_integrativo_unit + bonus_l207_unit).quantize(Decimal('0.01'))

		totali['netto_mensile'] += (netto_unit * qta).quantize(Decimal('0.01'))
		totali['lordo_mensile'] += (lordo_unit * qta).quantize(Decimal('0.01'))
		totali['maggiorazioni'] += maggiorazioni_tot
		totali['trattamento_integrativo'] += (trattamento_integrativo_unit * qta).quantize(Decimal('0.01'))
		totali['bonus_l207'] += (bonus_l207_unit * qta).quantize(Decimal('0.01'))
		totali['inps_azienda'] += (inps_az_unit * qta).quantize(Decimal('0.01'))
		totali['inps_dipendente'] += (inps_dip_unit * qta).quantize(Decimal('0.01'))
		totali['irpef_lorda'] += (irpef_lorda_unit * qta).quantize(Decimal('0.01'))
		totali['detrazioni_irpef'] += (detrazioni_unit * qta).quantize(Decimal('0.01'))
		totali['irpef_netta'] += (irpef_netta_unit * qta).quantize(Decimal('0.01'))
		totali['inail'] += (inail_unit * qta).quantize(Decimal('0.01'))
		totali['tfr'] += (tfr_unit * qta).quantize(Decimal('0.01'))
		totali['rateo_13'] += (rateo_13_unit * qta).quantize(Decimal('0.01'))
		totali['rateo_14'] += (rateo_14_unit * qta).quantize(Decimal('0.01'))
		totali['costo_azienda_totale'] += (costo_azienda_unit * qta).quantize(Decimal('0.01'))
		totali['decontrib_risparmio'] += (decontrib_risparmio_unit * qta).quantize(Decimal('0.01'))
		totali['totale_f24_mese'] = (
			totali['inps_azienda']
			+ totali['inps_dipendente']
			+ totali['inail']
			+ totali['irpef_netta']
		).quantize(Decimal('0.01'))

		righe_mese.append({
			'ruolo_id': ruolo['id'],
			'ruolo': ruolo.get('label', ruolo['id']),
			'quantita': ruolo['quantita'],
			'livello': parametro.livello,
			'giorni_attivi': giorni_attivi,
			'lordo_tot': (lordo_unit * qta).quantize(Decimal('0.01')),
			'imponibile_irpef_tot': (imponibile_irpef_unit * qta).quantize(Decimal('0.01')),
			'netto_tot': (netto_unit * qta).quantize(Decimal('0.01')),
			'irpef_lorda_tot': (irpef_lorda_unit * qta).quantize(Decimal('0.01')),
			'detrazioni_tot': (detrazioni_unit * qta).quantize(Decimal('0.01')),
			'irpef_netta_tot': (irpef_netta_unit * qta).quantize(Decimal('0.01')),
			'costo_azienda_tot': (costo_azienda_unit * qta).quantize(Decimal('0.01')),
			'inps_azienda_tot': (inps_az_unit * qta).quantize(Decimal('0.01')),
			'inps_dipendente_tot': (inps_dip_unit * qta).quantize(Decimal('0.01')),
			'inail_tot': (inail_unit * qta).quantize(Decimal('0.01')),
			'tfr_tot': (tfr_unit * qta).quantize(Decimal('0.01')),
			'rateo_13_tot': (rateo_13_unit * qta).quantize(Decimal('0.01')),
			'rateo_14_tot': (rateo_14_unit * qta).quantize(Decimal('0.01')),
			'trattamento_integrativo_tot': (trattamento_integrativo_unit * qta).quantize(Decimal('0.01')),
			'bonus_l207_tot': (bonus_l207_unit * qta).quantize(Decimal('0.01')),
			'rateo_ferie_tot': (Decimal(str((costo_lavoro_ruolo or {}).get('rateo_ferie_unit', 0))) * qta).quantize(Decimal('0.01')),
			'rateo_permessi_tot': (Decimal(str((costo_lavoro_ruolo or {}).get('rateo_permessi_unit', 0))) * qta).quantize(Decimal('0.01')),
			'fonte_calcolo_costo': fonte_calcolo_costo,
			'costo_azienda_esteso_tot': (Decimal(str((costo_lavoro_ruolo or {}).get('costo_azienda_esteso_unit', costo_azienda_unit))) * qta).quantize(Decimal('0.01')),
			'decontrib_rule_name': decontrib_rule_name,
			'decontrib_tipo': decontrib_tipo,
			'decontrib_valore': decontrib_valore,
			'decontrib_risparmio_tot': (decontrib_risparmio_unit * qta).quantize(Decimal('0.01')),
			'maggiorazioni_tot': maggiorazioni_tot,
			'maggiorazioni_dettaglio': maggiorazioni.get('dettaglio', {}),
			'maggiorazioni_ore': maggiorazioni.get('ore', {}),
			'maggiorazioni_giorni': maggiorazioni.get('giorni', {}),
			'maggiorazioni_percentuali': maggiorazioni.get('percentuali', {}),
			'maggiorazioni_tariffa_oraria': maggiorazioni.get('tariffa_oraria', Decimal('0.00')),
			'maggiorazioni_fonte': maggiorazioni.get('fonte', 'nessuna'),
			'maggiorazioni_per_unita': maggiorazioni.get('per_unita', []),
			'ratei_inclusi_nel_netto': ratei_inclusi_nel_netto,
		})

	totali['inps_totale'] = totali['inps_azienda'] + totali['inps_dipendente']
	totali['accantonamenti_totali'] = totali['tfr'] + totali['rateo_13'] + totali['rateo_14']
	totali['totale_mese_dipendente'] = (
		totali['netto_mensile']
		+ totali['inps_dipendente']
		+ totali['irpef_netta']
	).quantize(Decimal('0.01'))
	totali['totale_mese_azienda'] = (
		totali['inps_azienda']
		+ totali['rateo_13']
		+ totali['rateo_14']
		+ totali['tfr']
	).quantize(Decimal('0.01'))
	totali['totale_mese_complessivo'] = (
		totali['netto_mensile']
		+ totali['inps_dipendente']
		+ totali['irpef_netta']
		+ totali['inps_azienda']
		+ totali['rateo_13']
		+ totali['rateo_14']
		+ totali['tfr']
	).quantize(Decimal('0.01'))
	totali['costo_totale_annuo'] = (totali['costo_azienda_totale'] * Decimal('12')).quantize(Decimal('0.01'))
	totali['totale_annuo_complessivo'] = (totali['totale_mese_complessivo'] * Decimal('12')).quantize(Decimal('0.01'))
	return totali, righe_mese


def _build_riepilogo_12_mesi_da_input(context):
	rows = []
	dettaglio_mensile_ruoli = []
	totals = {
		'lordo_mensile': Decimal('0'),
		'trattamento_integrativo': Decimal('0'),
		'bonus_l207': Decimal('0'),
		'netto_mensile': Decimal('0'),
		'inps_dipendente': Decimal('0'),
		'irpef_netta': Decimal('0'),
		'totale_f24_mese': Decimal('0'),
		'decontrib_risparmio': Decimal('0'),
		'totale_f24_netto_decontrib': Decimal('0'),
		'totale_mese_dipendente': Decimal('0'),
		'totale_mese_azienda': Decimal('0'),
		'totale_mese_complessivo': Decimal('0'),
		'rateo_13': Decimal('0'),
		'rateo_14': Decimal('0'),
		'tfr': Decimal('0'),
		'costo_azienda_totale': Decimal('0'),
	}

	mese_ref = str(context.get('mese_riferimento', timezone.localdate().strftime('%Y-%m')))
	try:
		anno_ref = int(mese_ref.split('-')[0])
	except Exception:
		anno_ref = timezone.localdate().year
	mesi_da_gennaio = f"{anno_ref:04d}-01"

	for mese in _iter_mesi_da_riferimento(mesi_da_gennaio, 12):
		giorni_chiusura_mese = context.get('giorni_chiusura_mese_date', []) if mese == mese_ref else []
		t, righe_mese = _calcola_totali_mese_da_input(
			mese,
			context.get('ruoli_config', []),
			context.get('parametri_ccnl'),
			context.get('tipi_contratto'),
			context.get('aliquota_inail_perc', Decimal('1.20')),
			context.get('base_oraria_mensile', Decimal('173.33')),
			context.get('azienda_operativa'),
			context.get('giorni_lavorativi_mese', Decimal('26')),
			giorni_chiusura_mese,
		)
		row = {
			'mese_riferimento': mese,
			'lordo_mensile': _safe_decimal(t.get('lordo_mensile')),
			'trattamento_integrativo': _safe_decimal(t.get('trattamento_integrativo')),
			'bonus_l207': _safe_decimal(t.get('bonus_l207')),
			'netto_mensile': _safe_decimal(t.get('netto_mensile')),
			'inps_dipendente': _safe_decimal(t.get('inps_dipendente')),
			'irpef_netta': _safe_decimal(t.get('irpef_netta')),
			'totale_f24_mese': _safe_decimal(t.get('totale_f24_mese')),
			'decontrib_risparmio': _safe_decimal(t.get('decontrib_risparmio')),
			'totale_mese_dipendente': _safe_decimal(t.get('totale_mese_dipendente')),
			'totale_mese_azienda': _safe_decimal(t.get('totale_mese_azienda')),
			'totale_mese_complessivo': _safe_decimal(t.get('totale_mese_complessivo')),
			'rateo_13': _safe_decimal(t.get('rateo_13')),
			'rateo_14': _safe_decimal(t.get('rateo_14')),
			'tfr': _safe_decimal(t.get('tfr')),
			'costo_azienda_totale': _safe_decimal(t.get('costo_azienda_totale')),
		}
		row['totale_f24_netto_decontrib'] = (row['totale_f24_mese'] - row['decontrib_risparmio']).quantize(Decimal('0.01'))

		for k in totals.keys():
			totals[k] += row[k]
		rows.append(row)
		dettaglio_mensile_ruoli.append({
			'mese_riferimento': mese,
			'righe': righe_mese,
		})

	# Verifica/ricalcolo totale colonne direttamente dalle righe mostrate
	totals_verificati = {k: Decimal('0') for k in totals.keys()}
	for row in rows:
		for k in totals_verificati.keys():
			totals_verificati[k] += _safe_decimal(row.get(k, 0))
	totals = {k: v.quantize(Decimal('0.01')) for k, v in totals_verificati.items()}

	return rows, totals, dettaglio_mensile_ruoli


def _build_riepilogo_mensile_azienda(azienda_operativa, max_mesi=24):
	rows = []
	totals = {
		'lordo_mensile': Decimal('0'),
		'netto_mensile': Decimal('0'),
		'totale_mese_netto_dipendente': Decimal('0'),
		'totale_mese_dipendente': Decimal('0'),
		'totale_mese_azienda': Decimal('0'),
		'totale_mese_complessivo': Decimal('0'),
		'rateo_13': Decimal('0'),
		'rateo_14': Decimal('0'),
		'tfr': Decimal('0'),
		'costo_azienda_totale': Decimal('0'),
		'costo_totale_annuo': Decimal('0'),
		'totale_annuo_netto_dipendente': Decimal('0'),
		'subtotale_colonne': Decimal('0'),
	}

	if not azienda_operativa:
		return rows, totals

	qs = SimulazioneOrganico.objects.filter(azienda=azienda_operativa).order_by('-data_creazione')
	per_mese = {}
	for s in qs:
		if s.mese_riferimento in per_mese:
			continue
		per_mese[s.mese_riferimento] = s
		if len(per_mese) >= max_mesi:
			break

	for mese in sorted(per_mese.keys()):
		s = per_mese[mese]
		t = (s.risultato_json or {}).get('totali', {})
		row = {
			'mese_riferimento': mese,
			'lordo_mensile': _safe_decimal(t.get('lordo_mensile')),
			'netto_mensile': _safe_decimal(t.get('netto_mensile')),
			'totale_mese_netto_dipendente': _safe_decimal(t.get('totale_mese_netto_dipendente')),
			'totale_mese_dipendente': _safe_decimal(t.get('totale_mese_dipendente')),
			'totale_mese_azienda': _safe_decimal(t.get('totale_mese_azienda')),
			'totale_mese_complessivo': _safe_decimal(t.get('totale_mese_complessivo')),
			'rateo_13': _safe_decimal(t.get('rateo_13')),
			'rateo_14': _safe_decimal(t.get('rateo_14')),
			'tfr': _safe_decimal(t.get('tfr')),
			'costo_azienda_totale': _safe_decimal(t.get('costo_azienda_totale')),
			'costo_totale_annuo': _safe_decimal(t.get('costo_totale_annuo')),
			'totale_annuo_netto_dipendente': _safe_decimal(t.get('totale_annuo_netto_dipendente')),
		}
		row['subtotale_colonne'] = (
			row['lordo_mensile']
			+ row['netto_mensile']
			+ row['totale_mese_dipendente']
			+ row['totale_mese_azienda']
			+ row['totale_mese_complessivo']
			+ row['rateo_13']
			+ row['rateo_14']
			+ row['tfr']
			+ row['costo_azienda_totale']
			+ row['costo_totale_annuo']
		).quantize(Decimal('0.01'))

		for k in totals.keys():
			totals[k] += row[k]

		rows.append(row)

	return rows, totals


@login_required
@user_passes_test(_is_admin_only)
def simulazione_organico(request):
	"""Pagina 1: configurazione input simulazione."""
	azienda_operativa = _get_azienda_operativa_per_utente(request.user, request.session)
	session_key = _simulazione_config_session_key(azienda_operativa)

	params_effettivi = request.GET.copy()
	if params_effettivi:
		request.session[session_key] = _serialize_querydict_for_session(params_effettivi)
	else:
		saved = request.session.get(session_key)
		if saved:
			params_effettivi = _deserialize_querydict_from_session(saved)

	request_params = SimpleNamespace(GET=params_effettivi)
	ruoli_config = _build_ruoli_config(request_params)
	parametri_ccnl = ParametroCCNLTurismo.objects.filter(attivo=True).order_by('livello', 'qualifica')
	tipi_contratto = TipoContratto.objects.filter(attivo=True).order_by('nome')
	mese_riferimento, ore_turno_pranzo, ore_turno_cena, base_oraria_mensile, giorni_lavorativi_mese, aliquota_inail_perc = _parse_simulazione_generali(request_params)
	giorni_chiusura_mese_date = parse_giorni_chiusura_mese(request_params, mese_riferimento)
	calendario_config = _build_calendario_mese_config(azienda_operativa, mese_riferimento, giorni_chiusura_mese_date)
	simulazioni_recenti = SimulazioneOrganico.objects.none()
	if azienda_operativa:
		simulazioni_recenti = SimulazioneOrganico.objects.filter(azienda=azienda_operativa).order_by('-data_creazione')[:20]

	# Serializzo parametri CCNL con date validità per filtro dinamico JavaScript
	import json
	from datetime import datetime
	parametri_ccnl_json = json.dumps([
		{
			'livello': p.livello,
			'qualifica': p.qualifica,
			'data_inizio': p.decorrenza_validita_da.isoformat() if p.decorrenza_validita_da else None,
			'data_fine': p.decorrenza_validita_a.isoformat() if p.decorrenza_validita_a else None,
		}
		for p in parametri_ccnl
	])
	
	# Data riferimento per filtro iniziale (primo giorno del mese_riferimento)
	try:
		data_ref = datetime.strptime(mese_riferimento, '%Y-%m').date()
	except:
		data_ref = timezone.localdate().replace(day=1)
	
	# Crea lista livelli unici per il select, includendo solo quelli validi per il mese_riferimento
	livelli_unici_dict = {}
	for p in parametri_ccnl:
		# Verifica se questo parametro è valido per la data di riferimento
		valido = True
		if p.decorrenza_validita_da and data_ref < p.decorrenza_validita_da:
			valido = False
		if valido and p.decorrenza_validita_a and data_ref > p.decorrenza_validita_a:
			valido = False
		
		# Se è valido e il livello non è già presente, aggiungilo
		if valido and p.livello not in livelli_unici_dict:
			livelli_unici_dict[p.livello] = p
	
	parametri_ccnl_unici = list(livelli_unici_dict.values())

	# Auto-valorizzazione voci ruoli da DB sulla configurazione (se non già impostate dall'utente)
	for ruolo in ruoli_config:
		livello_ruolo = str(ruolo.get('livello') or '')
		param = livelli_unici_dict.get(livello_ruolo)
		if not param:
			continue
		voci = ruolo.setdefault('voci_excel', {})
		voci_tabella = _carica_voci_retributive_da_tabella(param)
		fallback_voci = {
			'minimo_tabellare': _safe_decimal(voci_tabella.get('minimo_tabellare')) or _safe_decimal(getattr(param, 'minimo_tabellare', 0)) or _safe_decimal(getattr(param, 'paga_base_mensile', 0)),
			'contingenza': _safe_decimal(voci_tabella.get('contingenza')) or _safe_decimal(getattr(param, 'contingenza_mensile', 0)),
			'scatto_anzianita': _safe_decimal(voci_tabella.get('scatto_anzianita')) or _safe_decimal(getattr(param, 'scatto_importo', 0)),
			'superminimo': _safe_decimal(voci_tabella.get('superminimo')) or _safe_decimal(getattr(param, 'superminimo_mensile', 0)),
			'el_dis_san': _safe_decimal(voci_tabella.get('el_dis_san')) or _safe_decimal(getattr(param, 'elemento_distinto_sanita', 0)),
			'el_dis_bil': _safe_decimal(voci_tabella.get('el_dis_bil')) or _safe_decimal(getattr(param, 'elemento_distinto_bilateralita', 0)),
		}
		for k, fallback in fallback_voci.items():
			info = voci.setdefault(k, {'presente': False, 'importo': Decimal('0.00')})
			if _safe_decimal(info.get('importo')) <= 0 and fallback > 0:
				info['importo'] = fallback.quantize(Decimal('0.01'))
				info['presente'] = True

	return render(
		request,
		'rapporto_di_lavoro/simulazione_organico_config.html',
		{
			'ruoli_config': ruoli_config,
			'parametri_ccnl': parametri_ccnl_unici,
			'parametri_ccnl_json': parametri_ccnl_json,
			'tipi_contratto': tipi_contratto,
			'mese_riferimento': mese_riferimento,
			'ore_turno_pranzo': ore_turno_pranzo,
			'ore_turno_cena': ore_turno_cena,
			'base_oraria_mensile': base_oraria_mensile,
			'giorni_lavorativi_mese': giorni_lavorativi_mese,
			'giorni_chiusura_mese': [d.isoformat() for d in giorni_chiusura_mese_date],
			'calendario_config': calendario_config,
			'aliquota_inail_perc': aliquota_inail_perc,
			'simulazioni_recenti': simulazioni_recenti,
		}
	)


@login_required
@user_passes_test(_is_admin_only)
def simulazione_organico_risultato(request):
	"""Pagina 2: visualizzazione simulazione con dettaglio economico."""
	querystring = request.GET.urlencode()
	if request.GET:
		azienda_operativa = _get_azienda_operativa_per_utente(request.user, request.session)
		session_key = _simulazione_config_session_key(azienda_operativa)
		request.session[session_key] = _serialize_querydict_for_session(request.GET)
	record = _get_record_simulazione_tabella(request)

	# Regola operativa: se arrivano parametri GET, ricalcoliamo sempre.
	# In questo modo eventuali modifiche a Parametri Maggiorazioni (admin)
	# sono immediatamente visibili nella scheda dipendente.
	if request.GET:
		context_live = _calcola_simulazione(request)
		righe_mensili, totali_colonne_mensili, dettaglio_mensile_ruoli = _build_riepilogo_12_mesi_da_input(context_live)
		context_live['riepilogo_mensile_righe'] = righe_mensili
		context_live['riepilogo_mensile_totali_colonne'] = totali_colonne_mensili
		context_live['dettaglio_mensile_ruoli'] = dettaglio_mensile_ruoli
		record = _salva_simulazione(request, context_live)

	# Auto-refresh record legacy: garantisce maggiorazioni visibili nella card dipendente.
	if (not request.GET) and record and _record_simulazione_necessita_refresh(record):
		context_live = _calcola_simulazione(request)
		righe_mensili, totali_colonne_mensili, dettaglio_mensile_ruoli = _build_riepilogo_12_mesi_da_input(context_live)
		context_live['riepilogo_mensile_righe'] = righe_mensili
		context_live['riepilogo_mensile_totali_colonne'] = totali_colonne_mensili
		context_live['dettaglio_mensile_ruoli'] = dettaglio_mensile_ruoli
		record = _salva_simulazione(request, context_live)

	# Se non c'è alcun record e non ci sono parametri GET, usiamo fallback calcolo live.
	if not record:
		context = _calcola_simulazione(request)
		righe_mensili, totali_colonne_mensili, dettaglio_mensile_ruoli = _build_riepilogo_12_mesi_da_input(context)
		context['riepilogo_mensile_righe'] = righe_mensili
		context['riepilogo_mensile_totali_colonne'] = totali_colonne_mensili
		context['dettaglio_mensile_ruoli'] = dettaglio_mensile_ruoli
		context['querystring'] = querystring
		return render(request, 'rapporto_di_lavoro/simulazione_organico.html', context)

	context = _context_da_record_simulazione(record)
	context['querystring'] = record.querystring or querystring
	context['simulazione_salvata_id'] = record.id
	context['dettaglio_mensile_ruoli'] = []
	return render(request, 'rapporto_di_lavoro/simulazione_organico.html', context)


@login_required
@user_passes_test(_is_admin_only)
@require_http_methods(['POST'])
def simulazione_organico_elimina(request, simulazione_id):
	"""Elimina una simulazione salvata della stessa azienda operativa."""
	azienda_operativa = _get_azienda_operativa_per_utente(request.user, request.session)
	if not azienda_operativa:
		return HttpResponseForbidden('Azienda operativa non selezionata.')

	simulazione = get_object_or_404(
		SimulazioneOrganico,
		id=simulazione_id,
		azienda=azienda_operativa,
	)
	simulazione.delete()
	messages.success(request, 'Simulazione eliminata correttamente.')
	return redirect('simulazione_organico')


def _fmt_ita(valore):
	"""Formatta un numero nel formato italiano: 0.000,00"""
	if valore is None:
		return '0,00'
	try:
		num = float(valore)
		# Separa parte intera e decimale
		intero = int(num)
		decimale = int(round((num - intero) * 100))
		# Formatta parte intera con punti ogni tre cifre
		intero_str = f"{intero:,}".replace(',', '.')
		# Ritorna nel formato italiano
		return f"{intero_str},{decimale:02d}"
	except (ValueError, TypeError):
		return '0,00'


@login_required
@user_passes_test(_is_admin_only)
def simulazione_organico_pdf(request):
	"""Esporta la simulazione in PDF A4 orizzontale, margini stretti, una pagina."""
	next_raw = (request.GET.get('next') or '').strip()
	if (request.GET.get('ui') == '1' or next_raw) and request.GET.get('embed') != '1':
		next_safe = sanitize_internal_next(request, next_raw)
		q = request.GET.copy()
		for k in ('ui', 'next'):
			q.pop(k, None)
		q['embed'] = '1'
		embed_src = request.build_absolute_uri(
			reverse('simulazione_organico_pdf') + '?' + q.urlencode()
		)
		return render(
			request,
			'common/file_viewer_frame.html',
			{
				'titolo': 'Simulazione organico (PDF)',
				'embed_src': embed_src,
				'next_url': next_safe,
			},
		)
	context = _calcola_simulazione(request)

	buffer = BytesIO()
	p = canvas.Canvas(buffer, pagesize=landscape(A4))
	page_w, page_h = landscape(A4)
	margin = 18

	# Titolo
	p.setFont('Helvetica-Bold', 10)
	p.drawString(margin, page_h - 16, 'Simulazione organico - riepilogo economico')
	p.setFont('Helvetica', 7)
	p.drawString(
		margin,
		page_h - 28,
		f"Mese: {context['mese_riferimento']} | Ore pranzo: {context['ore_turno_pranzo']} | Ore cena: {context['ore_turno_cena']} | INAIL: {context['aliquota_inail_perc']}%"
	)

	columns = [
		('Ruolo', 42, False), ('Nome', 54, False), ('Q', 16, True), ('Liv', 16, True), ('Ctr', 34, False),
		('Lordo', 34, True), ('TI', 26, True), ('L207', 30, True), ('Netto', 34, True), ('€/h', 28, True), ('€/g', 28, True),
		('INPSd', 32, True), ('INPSa', 32, True), ('INAIL', 30, True),
		('TFRm', 30, True), ('TFRg', 24, True), ('13m', 28, True), ('13g', 24, True), ('14m', 28, True), ('14g', 24, True),
		('Costo', 38, True),
	]

	top = page_h - 42
	row_h = 10
	table_w = sum(c[1] for c in columns)
	table_h = row_h * (len(context['righe']) + 2)
	bottom = top - table_h

	# Sfondo intestazione + bordo tabella
	p.setFillColor(colors.HexColor('#f0f2f5'))
	p.rect(margin, top - row_h + 1, table_w, row_h, fill=1, stroke=0)
	p.setFillColor(colors.black)
	p.setStrokeColor(colors.black)
	p.setLineWidth(0.6)
	p.rect(margin, bottom, table_w, table_h, fill=0, stroke=1)

	# Griglia verticale
	x = margin
	for _, w, _ in columns:
		x += w
		p.setLineWidth(0.3)
		p.line(x, bottom, x, top + 1)

	# Griglia orizzontale
	y_line = top - row_h
	for _ in range(len(context['righe']) + 1):
		p.setLineWidth(0.3)
		p.line(margin, y_line, margin + table_w, y_line)
		y_line -= row_h

	# Header
	p.setFont('Helvetica-Bold', 6)
	x = margin
	for name, w, _ in columns:
		p.drawString(x + 1.5, top - 7.2, name)
		x += w

	# Righe
	y = top - row_h - 7.2
	p.setFont('Helvetica', 6)
	for r in context['righe']:
		vals = [
			r.get('ruolo', ''),
			r.get('nome', ''),
			str(r.get('quantita', '')),
			str(r.get('livello', '')),
			(r.get('tipo_contratto').nome[:10] if r.get('tipo_contratto') else 'Full'),
			_fmt_ita(r.get('lordo_unit', 0)),
			_fmt_ita(r.get('trattamento_integrativo_tot', 0)),
			_fmt_ita(r.get('bonus_l207_tot', 0)),
			_fmt_ita(r.get('netto_unit', 0)),
			num_it_str(r.get('paga_oraria_netta') or 0, 4),
			_fmt_ita(r.get('netto_giornaliero', 0)),
			_fmt_ita(r.get('inps_dipendente_tot', 0)),
			_fmt_ita(r.get('inps_azienda_tot', 0)),
			_fmt_ita(r.get('inail_tot', 0)),
			_fmt_ita(r.get('tfr_tot', 0)),
			_fmt_ita(r.get('tfr_giornaliero', 0)),
			_fmt_ita(r.get('rateo_13_tot', 0)),
			_fmt_ita(r.get('rateo_13_giornaliero', 0)),
			_fmt_ita(r.get('rateo_14_tot', 0)),
			_fmt_ita(r.get('rateo_14_giornaliero', 0)),
			_fmt_ita(r.get('costo_azienda_tot', 0)),
		]

		x = margin
		for (txt, (_, w, is_num)) in zip(vals, columns):
			testo = str(txt)[:20]
			if is_num:
				p.drawRightString(x + w - 1.5, y, testo)
			else:
				p.drawString(x + 1.5, y, testo)
			x += w
		y -= row_h

	# Totali riepilogo
	t = context['totali']
	
	# Calcolo tasse annue complessive
	tasse_annue = t['costo_totale_annuo'] - t['totale_annuo_netto_dipendente']
	
	summary_y = bottom - 16
	p.setFont('Helvetica-Bold', 7)
	p.drawString(margin, summary_y, 'Riepilogo mensile')
	p.setFont('Helvetica', 6.8)
	p.drawString(margin, summary_y - 10, f"Lordo: {_fmt_ita(t['lordo_mensile'])}   TI: {_fmt_ita(t.get('trattamento_integrativo', 0))}   L207: {_fmt_ita(t.get('bonus_l207', 0))}   Netto: {_fmt_ita(t['netto_mensile'])}")
	p.drawString(margin, summary_y - 20, f"INPS dip: {_fmt_ita(t['inps_dipendente'])}   INPS az: {_fmt_ita(t['inps_azienda'])}   INAIL: {_fmt_ita(t['inail'])}")
	p.drawString(margin, summary_y - 30, f"TFR: {_fmt_ita(t['tfr'])}   13a: {_fmt_ita(t['rateo_13'])}   14a: {_fmt_ita(t['rateo_14'])}")
	p.setFont('Helvetica-Bold', 7)
	p.drawString(margin, summary_y - 42, f"Tot. dipendente: {_fmt_ita(t['totale_mese_dipendente'])}   Mese azienda: {_fmt_ita(t['totale_mese_azienda'])}   Mese complessivo: {_fmt_ita(t['totale_mese_complessivo'])}")
	p.drawString(margin, summary_y - 52, f"Anno azienda: {_fmt_ita(t['costo_totale_annuo'])}   Anno netto dipendenti: {_fmt_ita(t['totale_annuo_netto_dipendente'])}   = Tasse Annue complessive: {_fmt_ita(tasse_annue)}")
	p.drawString(margin, summary_y - 62, f"Anno complessivo: {_fmt_ita(t['totale_annuo_complessivo'])}")

	p.showPage()
	p.save()

	pdf = buffer.getvalue()
	buffer.close()

	response = HttpResponse(pdf, content_type='application/pdf')
	response['Content-Disposition'] = 'inline; filename="simulazione_organico_a4.pdf"'
	return response


@login_required
@user_passes_test(_is_admin_only)
def simulazione_organico_excel(request):
	"""Esporta la simulazione in CSV (apribile in Excel)."""
	context = _calcola_simulazione(request)
	out = StringIO()
	writer = csv.writer(out, delimiter=';')

	writer.writerow(['Simulazione organico'])
	writer.writerow(['Mese riferimento', context['mese_riferimento']])
	writer.writerow([])
	writer.writerow([
		'Ruolo', 'Nome', 'Qta', 'Livello', 'Contratto', 'Giorni attivi',
		'Lordo mensile', 'Trat. Int. DL3/2020', 'Bonus L.207/2024', 'Netto mensile', 'Paga oraria netta', 'Paga netta giornaliera',
		'INPS dip.', 'INPS az.', 'INAIL', 'TFR mese', '13a mese', '14a mese', 'Costo azienda'
	])

	for r in context['righe']:
		writer.writerow([
			r.get('ruolo', ''),
			r.get('nome', ''),
			r.get('quantita', ''),
			r.get('livello', ''),
			(r.get('tipo_contratto').nome if r.get('tipo_contratto') else 'Full-time'),
			r.get('giorni_attivi', 0),
			euro_it_str(r.get('lordo_unit', 0)),
			euro_it_str(r.get('trattamento_integrativo_tot', 0)),
			euro_it_str(r.get('bonus_l207_tot', 0)),
			euro_it_str(r.get('netto_unit', 0)),
			num_it_str(r.get('paga_oraria_netta') or 0, 4),
			euro_it_str(r.get('netto_giornaliero', 0)),
			euro_it_str(r.get('inps_dipendente_tot', 0)),
			euro_it_str(r.get('inps_azienda_tot', 0)),
			euro_it_str(r.get('inail_tot', 0)),
			euro_it_str(r.get('tfr_tot', 0)),
			euro_it_str(r.get('rateo_13_tot', 0)),
			euro_it_str(r.get('rateo_14_tot', 0)),
			euro_it_str(r.get('costo_azienda_tot', 0)),
		])

	t = context['totali']
	writer.writerow([])
	writer.writerow(['RIEPILOGO MENSILE'])
	writer.writerow(['Paghe lorde', euro_it_str(t['lordo_mensile'])])
	writer.writerow(['Trattamento Integrativo DL3/2020', euro_it_str(t.get('trattamento_integrativo', 0))])
	writer.writerow(['Bonus Art.1 c.4 L.207/2024', euro_it_str(t.get('bonus_l207', 0))])
	writer.writerow(['Paghe nette', euro_it_str(t['netto_mensile'])])
	writer.writerow(['Totale mese netto dipendente', euro_it_str(t['totale_mese_netto_dipendente'])])
	writer.writerow(['Totale mese dipendente', euro_it_str(t['totale_mese_dipendente'])])
	writer.writerow(['Totale mese azienda', euro_it_str(t['totale_mese_azienda'])])
	writer.writerow(['Totale complessivo mese', euro_it_str(t['totale_mese_complessivo'])])
	writer.writerow(['13a mese', euro_it_str(t['rateo_13'])])
	writer.writerow(['14a mese', euro_it_str(t['rateo_14'])])
	writer.writerow(['TFR mese', euro_it_str(t['tfr'])])
	writer.writerow(['Totale mensile azienda', euro_it_str(t['costo_azienda_totale'])])
	writer.writerow(['Totale annuo netto dipendente', euro_it_str(t['totale_annuo_netto_dipendente'])])
	writer.writerow(['Totale annuo complessivo', euro_it_str(t['totale_annuo_complessivo'])])

	response = HttpResponse(out.getvalue(), content_type='text/csv; charset=utf-8')
	response['Content-Disposition'] = 'attachment; filename="simulazione_organico.csv"'
	return response


@login_required
@user_passes_test(_is_admin_only)
def gestione_riferimenti_economici(request):
	parametri_qs = ParametroCCNLTurismo.objects.all().order_by('ccnl', 'versione', 'livello', 'qualifica')
	regole_qs = RegolaNormativaCCNL.objects.all().order_by('ccnl', 'versione', 'livello', '-decorrenza_validita_da')

	paginator_p = Paginator(parametri_qs, 25)
	paginator_r = Paginator(regole_qs, 25)
	parametri_page = paginator_p.get_page(request.GET.get('p_page') or 1)
	regole_page = paginator_r.get_page(request.GET.get('r_page') or 1)

	parametri = list(parametri_page.object_list)
	for p in parametri:
		override = _carica_parametri_tabellari_costo_lavoro(p)
		lordo_legacy = Decimal(str(p.importo_lordo_mensile or 0))
		lordo_nuovo = Decimal(str(override.get('stipendio_lordo_mensile') or lordo_legacy))
		p.lordo_costo_lavoro = lordo_nuovo
		p.delta_lordo = (lordo_nuovo - lordo_legacy).quantize(Decimal('0.01'))
		p.agganciato_costo_lavoro = (lordo_nuovo != lordo_legacy)
	regole = list(regole_page.object_list)
	for r in regole:
		anno_rif = r.decorrenza_validita_da.year if r.decorrenza_validita_da else timezone.localdate().year
		normativa_db = _carica_regola_normativa_da_db(
			ccnl_label=r.ccnl,
			livello=r.livello,
			anno=anno_rif,
			coeff_ore=Decimal('1'),
		)
		r.ore_settimanali_agg = normativa_db.get('ore_settimanali', r.ore_settimanali) if normativa_db else r.ore_settimanali
		r.ferie_annue_giorni_agg = normativa_db.get('ferie_annue_giorni', r.ferie_annue_giorni) if normativa_db else r.ferie_annue_giorni
		r.permessi_annui_ore_agg = normativa_db.get('permessi_annui_ore', r.permessi_annui_ore) if normativa_db else r.permessi_annui_ore
		r.scatto_periodicita_mesi_agg = normativa_db.get('scatto_periodicita_mesi', r.scatto_periodicita_mesi) if normativa_db else r.scatto_periodicita_mesi
		r.scatto_importo_agg = normativa_db.get('scatto_importo', r.scatto_importo) if normativa_db else r.scatto_importo
		r.fonte_normativa = normativa_db.get('fonte', 'legacy') if normativa_db else 'legacy'
	ccnl_parametrizzati = CCNL.objects.filter(attivo=True).order_by('sigla', '-anno_inizio_validita')

	return render(
		request,
		'rapporto_di_lavoro/gestione_riferimenti_economici.html',
		{
			'parametri': parametri,
			'parametri_page': parametri_page,
			'regole': regole,
			'regole_page': regole_page,
			'ccnl_parametrizzati': ccnl_parametrizzati,
		},
	)


@login_required
@user_passes_test(_is_admin_only)
def parametro_economico_nuovo(request):
	if request.method == 'POST':
		form = ParametroCCNLTurismoForm(request.POST)
		if form.is_valid():
			form.save()
			messages.success(request, 'Parametro economico creato con successo.')
			return redirect('gestione_riferimenti_economici')
	else:
		form = ParametroCCNLTurismoForm()

	return render(
		request,
		'rapporto_di_lavoro/form_riferimento_economico.html',
		{
			'form': form,
			'titolo': 'Nuovo parametro economico CCNL',
			'back_url_name': 'gestione_riferimenti_economici',
		},
	)


@login_required
@user_passes_test(_is_admin_only)
def parametro_economico_modifica(request, parametro_id):
	parametro = get_object_or_404(ParametroCCNLTurismo, id=parametro_id)
	if request.method == 'POST':
		form = ParametroCCNLTurismoForm(request.POST, instance=parametro)
		if form.is_valid():
			form.save()
			messages.success(request, 'Parametro economico aggiornato con successo.')
			return redirect('gestione_riferimenti_economici')
	else:
		form = ParametroCCNLTurismoForm(instance=parametro)

	return render(
		request,
		'rapporto_di_lavoro/form_riferimento_economico.html',
		{
			'form': form,
			'titolo': f'Modifica parametro economico #{parametro.id}',
			'back_url_name': 'gestione_riferimenti_economici',
		},
	)


@login_required
@user_passes_test(_is_admin_only)
def parametro_economico_elimina(request, parametro_id):
	parametro = get_object_or_404(ParametroCCNLTurismo, id=parametro_id)
	if request.method == 'POST':
		try:
			parametro.delete()
			messages.warning(request, 'Parametro economico eliminato.')
		except ProtectedError:
			# Se usato da proposte/contratti, manteniamo lo storico e disattiviamo il parametro
			parametro.attivo = False
			parametro.save(update_fields=['attivo'])
			messages.info(
				request,
				'Parametro non eliminabile perché collegato a proposte esistenti: è stato disattivato.'
			)
		return redirect('gestione_riferimenti_economici')
	return HttpResponseForbidden('Metodo non consentito')


@login_required
@user_passes_test(_is_admin_only)
def parametri_economici_elimina_multipli(request):
	if request.method != 'POST':
		return HttpResponseForbidden('Metodo non consentito')

	ids = request.POST.getlist('parametro_ids')
	if not ids:
		messages.warning(request, 'Seleziona almeno una voce retributiva da eliminare.')
		return redirect('gestione_riferimenti_economici')

	queryset = ParametroCCNLTurismo.objects.filter(id__in=ids)
	eliminati = 0
	disattivati = 0

	for parametro in queryset:
		try:
			parametro.delete()
			eliminati += 1
		except ProtectedError:
			if parametro.attivo:
				parametro.attivo = False
				parametro.save(update_fields=['attivo'])
			disattivati += 1

	if eliminati:
		messages.success(request, f'Voci eliminate: {eliminati}.')
	if disattivati:
		messages.info(request, f'Voci non eliminabili e disattivate: {disattivati}.')

	return redirect('gestione_riferimenti_economici')


@login_required
@user_passes_test(_is_admin_only)
def regola_normativa_nuova(request):
	if request.method == 'POST':
		form = RegolaNormativaCCNLForm(request.POST)
		if form.is_valid():
			form.save()
			messages.success(request, 'Regola normativa creata con successo.')
			return redirect('gestione_riferimenti_economici')
	else:
		form = RegolaNormativaCCNLForm()

	return render(
		request,
		'rapporto_di_lavoro/form_riferimento_economico.html',
		{
			'form': form,
			'titolo': 'Nuova regola normativa CCNL',
			'back_url_name': 'gestione_riferimenti_economici',
		},
	)


@login_required
@user_passes_test(_is_admin_only)
def regola_normativa_modifica(request, regola_id):
	regola = get_object_or_404(RegolaNormativaCCNL, id=regola_id)
	if request.method == 'POST':
		form = RegolaNormativaCCNLForm(request.POST, instance=regola)
		if form.is_valid():
			form.save()
			messages.success(request, 'Regola normativa aggiornata con successo.')
			return redirect('gestione_riferimenti_economici')
	else:
		form = RegolaNormativaCCNLForm(instance=regola)

	return render(
		request,
		'rapporto_di_lavoro/form_riferimento_economico.html',
		{
			'form': form,
			'titolo': f'Modifica regola normativa #{regola.id}',
			'back_url_name': 'gestione_riferimenti_economici',
		},
	)


@login_required
@user_passes_test(_is_admin_only)
def regola_normativa_elimina(request, regola_id):
	regola = get_object_or_404(RegolaNormativaCCNL, id=regola_id)
	if request.method == 'POST':
		try:
			regola.delete()
			messages.warning(request, 'Regola normativa eliminata.')
		except ProtectedError:
			regola.attivo = False
			regola.save(update_fields=['attivo'])
			messages.info(
				request,
				'Regola non eliminabile perché referenziata: è stata disattivata.'
			)
		return redirect('gestione_riferimenti_economici')
	return HttpResponseForbidden('Metodo non consentito')


@login_required
@user_passes_test(_is_admin_only)
def regole_normative_elimina_multiple(request):
	if request.method != 'POST':
		return HttpResponseForbidden('Metodo non consentito')

	ids = request.POST.getlist('regola_ids')
	if not ids:
		messages.warning(request, 'Seleziona almeno una regola normativa da eliminare.')
		return redirect('gestione_riferimenti_economici')

	queryset = RegolaNormativaCCNL.objects.filter(id__in=ids)
	eliminate = 0
	disattivate = 0

	for regola in queryset:
		try:
			regola.delete()
			eliminate += 1
		except ProtectedError:
			if regola.attivo:
				regola.attivo = False
				regola.save(update_fields=['attivo'])
			disattivate += 1

	if eliminate:
		messages.success(request, f'Regole eliminate: {eliminate}.')
	if disattivate:
		messages.info(request, f'Regole non eliminabili e disattivate: {disattivate}.')

	return redirect('gestione_riferimenti_economici')


@login_required
def lista_proposte(request):
	if request.user.is_superuser or request.user.has_ruolo('admin'):
		azienda_operativa = get_azienda_operativa(request.user, request.session)
		proposte = PropostaAssunzione.objects.filter(azienda=azienda_operativa) if azienda_operativa else PropostaAssunzione.objects.none()
	elif request.user.has_ruolo('hr'):
		azienda_operativa = request.user.azienda
		proposte = PropostaAssunzione.objects.filter(azienda=azienda_operativa)
	elif (request.user.has_ruolo('dipendente') or request.user.has_ruolo('candidato')):
		azienda_operativa = request.user.azienda
		# Le bozze sono visibili solo all'HR; il candidato vede da stato equivalente a inviata_candidato in poi
		proposte = PropostaAssunzione.objects.filter(
			dipendente__utente=request.user,
		).exclude(stato='bozza')
		# Segnala se c'è una bozza in preparazione per questo candidato
		ha_bozza_in_lavorazione = PropostaAssunzione.objects.filter(
			dipendente__utente=request.user, stato='bozza',
		).exists()
	else:
		return HttpResponseForbidden("Accesso negato")

	# Candidati pronti per una proposta (profilo completo, convalidati, senza proposta attiva)
	candidati_pronti = []
	if request.user.is_superuser or (request.user.has_ruolo('admin') or request.user.has_ruolo('hr')):
		from django.contrib.auth import get_user_model
		User = get_user_model()
		candidati_qs = User.objects.filter(
			# ruolo='candidato',  # Assegna ruoli dopo la creazione
			convalidato=True,
			profilo_candidato__profilo_completato=True,
			profilo_candidato__azienda_interesse=azienda_operativa,
		).select_related('profilo_candidato')
		for cand in candidati_qs:
			ha_proposta_attiva = PropostaAssunzione.objects.filter(
				dipendente__utente=cand,
			).exclude(stato__in=['rifiutata_dipendente', 'rifiutata_admin']).exists()
			if not ha_proposta_attiva:
				candidati_pronti.append(cand)

	stato_filter = request.GET.get('stato', '')
	if stato_filter:
		proposte = proposte.filter(stato=stato_filter)

	proposte = proposte.select_related('dipendente', 'azienda', 'tipo_contratto').order_by('-data_creazione')
	proposte_for_alerts = proposte

	portale_candidato_o_dipendente = request.user.has_ruolo('dipendente') or request.user.has_ruolo('candidato')
	base_template = 'base_candidato.html' if portale_candidato_o_dipendente else 'base.html'

	contratti_td_prossimi = []
	contratti_td_scaduti = []
	if azienda_operativa and (
		request.user.is_superuser or request.user.has_ruolo('admin') or request.user.has_ruolo('hr')
	):
		from .services_contratti import contratti_td_in_scadenza, contratti_td_scaduti_non_chiusi

		contratti_td_prossimi = contratti_td_in_scadenza(azienda_operativa)
		contratti_td_scaduti = contratti_td_scaduti_non_chiusi(azienda_operativa)

	paginator = Paginator(proposte, 25)
	page_obj = paginator.get_page(request.GET.get('page') or 1)

	return render(
		request,
		'rapporto_di_lavoro/lista_proposte.html',
		{
			'base_template': base_template,
			'proposte': page_obj,
			'proposte_for_alerts': proposte_for_alerts,
			'page_obj': page_obj,
			'azienda_operativa': azienda_operativa,
			'candidati_pronti': candidati_pronti,
			'ha_bozza_in_lavorazione': locals().get('ha_bozza_in_lavorazione', False),
			'stato_filter': stato_filter,
			'stato_choices': PropostaAssunzione.STATO_CHOICES,
			'contratti_td_prossimi': contratti_td_prossimi,
			'contratti_td_scaduti': contratti_td_scaduti,
		},
	)


@login_required
@user_passes_test(_is_admin_like)
def lista_legacy_da_allineare(request):
	"""Elenco dipendenti attivi legacy senza proposta/contratto attivo da allineare."""
	azienda_operativa = _get_azienda_operativa_per_utente(request.user, request.session)
	if not azienda_operativa:
		messages.error(request, 'Seleziona prima un\'azienda operativa.')
		return redirect('lista_aziende')

	q = (request.GET.get('q') or '').strip()
	data_da_raw = (request.GET.get('data_da') or '').strip()
	data_a_raw = (request.GET.get('data_a') or '').strip()

	dipendenti_legacy = (
		Dipendente.objects.filter(azienda=azienda_operativa, stato='attivo')
		.exclude(rapporti_di_lavoro__stato__in=('proposta', 'sottoscritto', 'sospeso'))
		.exclude(
			proposte_assunzione__stato__in=(
				'bozza', 'inviata_candidato', 'firmata_candidato', 'contratto_attivo',
				'inviata_al_dipendente', 'accettata_dipendente', 'in_revisione_admin',
				'approvata_admin', 'convertita_in_contratto',
			)
		)
		.order_by('cognome', 'nome')
		.distinct()
	)

	if q:
		dipendenti_legacy = dipendenti_legacy.filter(
			Q(nome__icontains=q) |
			Q(cognome__icontains=q) |
			Q(codice_fiscale__icontains=q) |
			Q(email__icontains=q)
		)

	try:
		if data_da_raw:
			data_da = datetime.strptime(data_da_raw, '%Y-%m-%d').date()
			dipendenti_legacy = dipendenti_legacy.filter(data_assunzione__gte=data_da)
	except ValueError:
		messages.warning(request, 'Formato data inizio filtro non valido (usa AAAA-MM-GG).')

	try:
		if data_a_raw:
			data_a = datetime.strptime(data_a_raw, '%Y-%m-%d').date()
			dipendenti_legacy = dipendenti_legacy.filter(data_assunzione__lte=data_a)
	except ValueError:
		messages.warning(request, 'Formato data fine filtro non valido (usa AAAA-MM-GG).')

	paginator = Paginator(dipendenti_legacy, 25)
	page_obj = paginator.get_page(request.GET.get('page') or 1)

	return render(
		request,
		'rapporto_di_lavoro/lista_legacy_allineamento.html',
		{
			'azienda_operativa': azienda_operativa,
			'dipendenti_legacy': page_obj,
			'page_obj': page_obj,
			'filtri': {
				'q': q,
				'data_da': data_da_raw,
				'data_a': data_a_raw,
			},
		},
	)


@login_required
@user_passes_test(_is_admin_like)
def istruttoria_assunzione(request):
	"""
	Wizard unico di avvio pratica:
	- candidato -> proposta
	- dipendente attivo legacy -> proposta/contratto diretto (modulo categoria contratto_assunzione)
	"""
	azienda_operativa = _get_azienda_operativa_per_utente(request.user, request.session)
	if not azienda_operativa:
		messages.error(request, 'Seleziona prima un\'azienda operativa.')
		return redirect('lista_aziende')

	def _check_pratica_legacy(dipendente):
		blocchi = []
		avvisi = []
		if RapportoDiLavoro.objects.filter(
			dipendente=dipendente,
			stato__in=('proposta', 'sottoscritto', 'sospeso'),
		).exists():
			blocchi.append('Dipendente con rapporto di lavoro gia attivo/sospeso o bozza contratto.')
		if PropostaAssunzione.objects.filter(
			dipendente=dipendente,
			stato__in=(
				'bozza', 'inviata_candidato', 'firmata_candidato', 'contratto_attivo',
				'inviata_al_dipendente', 'accettata_dipendente', 'in_revisione_admin',
				'approvata_admin', 'convertita_in_contratto',
			),
		).exists():
			blocchi.append('Esiste gia una proposta di assunzione attiva per questo dipendente.')
		if dipendente.stato != 'attivo':
			avvisi.append('Dipendente non in stato attivo: verifica coerenza anagrafica prima della pratica.')
		return {'blocchi': blocchi, 'avvisi': avvisi}

	if request.method == 'POST':
		form = IstruttoriaAssunzioneForm(request.POST, azienda_operativa=azienda_operativa)
		if form.is_valid():
			profilo = form.cleaned_data.get('profilo_candidato')
			dip = form.cleaned_data.get('dipendente')
			percorso = form.cleaned_data.get('percorso') or 'auto'
			data_inizio = form.cleaned_data.get('data_inizio_rapporto')
			tipo = form.cleaned_data.get('tipo_contratto')
			livello = (form.cleaned_data.get('ccnl_livello') or '').strip()
			mansione = form.cleaned_data.get('mansione')

			if profilo and not dip:
				dip = profilo.dipendente
			if not dip:
				messages.error(request, 'Nessun dipendente collegato: riallinea anagrafica candidato prima di proseguire.')
				return redirect('candidato_admin_dettaglio', user_id=profilo.user_id) if profilo else redirect('lista_proposte_assunzione')

			esiti = _check_pratica_legacy(dip)
			if esiti['blocchi']:
				messages.error(
					request,
					'Impossibile avviare pratica: ' + ' | '.join(esiti['blocchi'])
				)
				return redirect('istruttoria_assunzione')
			for avv in esiti['avvisi']:
				messages.warning(request, avv)

			# Regola auto: per dipendente attivo legacy, preferisci contratto diretto.
			if percorso == 'auto':
				percorso = 'contratto_diretto' if dip.stato == 'attivo' else 'proposta'

			q = {
				'dipendente_id': str(dip.id),
				'profilo_data_inizio': data_inizio.isoformat() if data_inizio else '',
			}
			if tipo:
				q['profilo_tipo_contratto_id'] = str(tipo.id)
			if livello:
				q['profilo_livello'] = livello
			if mansione:
				q['profilo_nome'] = mansione.nome

			if profilo:
				q['profilo_qualifica'] = (profilo.mansione_aspirata or '').strip()
				if not q['profilo_livello']:
					q['profilo_livello'] = (profilo.livello_aspirato or '').strip()

			if percorso == 'contratto_diretto':
				q['modulo_categoria'] = 'contratto_assunzione'
			if dip.stato == 'attivo' and not profilo:
				q['legacy_allineamento'] = '1'

			url = reverse('crea_proposta_assunzione')
			return redirect(f"{url}?{urlencode({k: v for k, v in q.items() if v})}")
	else:
		initial = {'percorso': 'auto'}
		dipendente_prefill = (request.GET.get('dipendente_id') or '').strip()
		if dipendente_prefill:
			initial['dipendente'] = dipendente_prefill
			initial['percorso'] = 'contratto_diretto'
		form = IstruttoriaAssunzioneForm(azienda_operativa=azienda_operativa, initial=initial)

	checklist = [
		'Anagrafica completa (CF, data nascita, recapiti).',
		'Mansione operativa coerente con qualifica tabellare CCNL.',
		'Livello retributivo vigente ora e tipo contratto selezionati.',
		'Data inizio rapporto valorizzata.',
		'Assenza di proposta/contratto attivo per dipendenti legacy.',
		'Conformita normativa italiana (D.Lgs. 81/2015, 66/2003, 104/2022, CCNL).',
	]

	return render(
		request,
		'rapporto_di_lavoro/istruttoria_assunzione.html',
		{
			'form': form,
			'azienda_operativa': azienda_operativa,
			'checklist': checklist,
		},
	)


@login_required
@user_passes_test(_is_admin_like)
def crea_proposta(request):
	azienda_operativa = _get_azienda_operativa_per_utente(request.user, request.session)
	if not azienda_operativa:
		messages.error(request, 'Seleziona prima un\'azienda operativa.')
		return redirect('lista_aziende')

	# Precompilazione da candidato (via crea_proposta_da_candidato)
	dipendente_id_prefill = request.GET.get('dipendente_id') or request.POST.get('dipendente_id')

	forza_nuova = (request.GET.get('forza_nuova') or request.POST.get('forza_nuova') or '').strip() == '1'

	if request.method == 'POST':
		post_data = _normalizza_ore_post_data(request.POST)
		form = PropostaAssunzioneForm(post_data, azienda_operativa=azienda_operativa,
			dipendente_prefill_id=dipendente_id_prefill)
		if form.is_valid():
			proposta = form.save(commit=False)
			esistente = _proposta_attiva_per_dipendente(azienda_operativa, proposta.dipendente)
			if esistente and not forza_nuova:
				target = 'modifica_proposta_assunzione' if esistente.stato == 'bozza' else 'dettaglio_proposta'
				messages.warning(
					request,
					f"Esiste già una proposta attiva per {proposta.dipendente}. "
					"Per evitare duplicati, aggiorna quella esistente. "
					"Se vuoi crearne una nuova, riapri il form con 'forza_nuova=1'."
				)
				return redirect(target, proposta_id=esistente.id)
			proposta.azienda = azienda_operativa
			proposta.creato_da = request.user
			proposta.modificato_da = request.user
			proposta.numero_proposta = _genera_numero_proposta()
			proposta.stato = 'bozza'
			legacy_allineamento = (request.GET.get('legacy_allineamento') or '').strip() == '1'
			if legacy_allineamento:
				marker = '[ALLINEAMENTO LEGACY]'
				note_txt = (proposta.note or '').strip()
				if marker not in note_txt:
					proposta.note = (note_txt + f'\n{marker} Pratica generata da istruttoria legacy.').strip()

			# Precompilazione forte e completa per il nuovo motore
			_autocompleta_retribuzione_proposta(proposta, azienda_operativa, preserve_manual=True)

			proposta.compila_riferimenti_normativi()

			proposta.save()
			messages.success(request, f'Proposta {proposta.numero_proposta} creata.')
			return redirect('dettaglio_proposta', proposta_id=proposta.id)
	else:
		if dipendente_id_prefill and not forza_nuova:
			dip_pref = Dipendente.objects.filter(id=dipendente_id_prefill, azienda=azienda_operativa).first()
			if dip_pref:
				esistente = _proposta_attiva_per_dipendente(azienda_operativa, dip_pref)
				if esistente:
					target = 'modifica_proposta_assunzione' if esistente.stato == 'bozza' else 'dettaglio_proposta'
					messages.info(
						request,
						f"Per {dip_pref} esiste già la proposta {esistente.numero_proposta}. "
						"Ti porto alla proposta esistente per modifica."
					)
					return redirect(target, proposta_id=esistente.id)
		initial = {}
		if dipendente_id_prefill:
			initial['dipendente'] = dipendente_id_prefill
		modulo_categoria_prefill = (request.GET.get('modulo_categoria') or '').strip()
		if modulo_categoria_prefill:
			from .models import ModuloContrattuale
			mod = ModuloContrattuale.objects.filter(
				attivo=True,
				categoria=modulo_categoria_prefill,
			).order_by('nome').first()
			if mod:
				initial['modulo'] = mod.id
		if getattr(azienda_operativa, 'tipo_contratto_predefinito_id', None):
			initial['tipo_contratto'] = azienda_operativa.tipo_contratto_predefinito_id
		if getattr(azienda_operativa, 'ccnl_predefinito_id', None):
			parametro_default = (
				ParametroCCNLTurismo.objects.filter(attivo=True, ccnl__icontains=azienda_operativa.ccnl_predefinito.sigla)
				.order_by('-decorrenza_validita_da', 'livello')
				.first()
			)
			if parametro_default:
				initial['parametro_ccnl'] = parametro_default.id
				initial['ccnl_livello_scelta'] = str(parametro_default.livello)

		# Precompilazione da profilo simulazione (ruoli chiave)
		profilo_livello = request.GET.get('profilo_livello')
		profilo_qualifica = request.GET.get('profilo_qualifica')
		profilo_nome = request.GET.get('profilo_nome')
		profilo_tipo_contratto_id = request.GET.get('profilo_tipo_contratto_id')
		profilo_data_inizio = request.GET.get('profilo_data_inizio')
		profilo_data_fine = request.GET.get('profilo_data_fine')

		if profilo_tipo_contratto_id:
			initial['tipo_contratto'] = profilo_tipo_contratto_id
		if profilo_data_inizio:
			initial['data_inizio_rapporto'] = profilo_data_inizio
		if profilo_data_fine:
			initial['data_fine_rapporto'] = profilo_data_fine
		if profilo_nome:
			initial['titolo'] = f"Proposta assunzione - {profilo_nome}"

		if profilo_livello:
			parametro_prefill = ParametroCCNLTurismo.objects.filter(attivo=True, livello=profilo_livello)
			if profilo_qualifica:
				parametro_prefill = parametro_prefill.filter(qualifica=profilo_qualifica)
			if getattr(azienda_operativa, 'ccnl_predefinito_id', None):
				parametro_prefill = parametro_prefill.filter(ccnl__icontains=azienda_operativa.ccnl_predefinito.sigla)
			parametro_prefill = parametro_prefill.order_by('-decorrenza_validita_da').first()
			if parametro_prefill:
				initial['parametro_ccnl'] = parametro_prefill.id
				initial['ccnl_livello_scelta'] = str(parametro_prefill.livello)
				initial['livello_ccnl'] = parametro_prefill.livello
				initial['qualifica'] = parametro_prefill.qualifica
				tipo_pf = None
				if profilo_tipo_contratto_id:
					tipo_pf = TipoContratto.objects.filter(pk=profilo_tipo_contratto_id).first()
				initial['posizione'] = _descrizione_posizione_contrattuale(parametro_prefill, tipo_pf)

		# Fallback robusto: valorizza subito i campi economici in GET (server-side),
		# così la pagina è coerente anche se il JS/API non si attiva subito.
		parametro_seed = None
		parametro_seed_id = initial.get('parametro_ccnl')
		if parametro_seed_id:
			parametro_seed = ParametroCCNLTurismo.objects.filter(pk=parametro_seed_id, attivo=True).first()
		if parametro_seed is None:
			parametro_seed_qs = ParametroCCNLTurismo.objects.filter(attivo=True)
			if getattr(azienda_operativa, 'ccnl_predefinito_id', None):
				parametro_seed_qs = parametro_seed_qs.filter(ccnl__icontains=azienda_operativa.ccnl_predefinito.sigla)
			parametro_seed = parametro_seed_qs.order_by('-decorrenza_validita_da', 'livello_ordinamento', 'livello').first()
		if parametro_seed is not None:
			initial.setdefault('parametro_ccnl', parametro_seed.id)
			initial.setdefault('ccnl_livello_scelta', str(parametro_seed.livello))
			initial.setdefault('livello_ccnl', parametro_seed.livello)
			initial.setdefault('qualifica', parametro_seed.qualifica)

			tipo_seed = None
			tipo_seed_id = initial.get('tipo_contratto')
			if tipo_seed_id:
				tipo_seed = TipoContratto.objects.filter(pk=tipo_seed_id, attivo=True).first()
			if tipo_seed is None:
				tipo_seed = TipoContratto.objects.filter(attivo=True).order_by('id').first()

			data_inizio_seed = initial.get('data_inizio_rapporto') or timezone.localdate()
			if isinstance(data_inizio_seed, str):
				try:
					data_inizio_seed = date.fromisoformat(data_inizio_seed)
				except ValueError:
					data_inizio_seed = timezone.localdate()

			proposta_seed = PropostaAssunzione(
				azienda=azienda_operativa,
				parametro_ccnl=parametro_seed,
				tipo_contratto=tipo_seed,
				livello_ccnl=parametro_seed.livello,
				qualifica=initial.get('qualifica') or parametro_seed.qualifica,
				data_inizio_rapporto=data_inizio_seed,
			)
			_autocompleta_retribuzione_proposta(proposta_seed, azienda_operativa)

			for field_name in (
				'stipendio_lordo_mensile',
				'paga_base_mensile',
				'contingenza_mensile',
				'edr_mensile',
				'indennita_mensile',
				'giorni_ferie_annuali',
				'giorni_permesso_annuali',
				'ore_settimanali',
				'ore_mensili',
				'ore_giornaliere',
				'decorrenza_validita_da',
				'decorrenza_validita_a',
				'scatto_periodicita_mesi',
				'scatto_importo',
				'numero_scatti_massimi',
				'straordinario_diurno_maggiorazione',
				'straordinario_notturno_maggiorazione',
				'straordinario_festivo_maggiorazione',
				'riposi_compensativi_regola',
				'tredicesima',
				'quattordicesima',
				'ferie_annue_giorni',
				'permessi_annui_ore',
			):
				val = getattr(proposta_seed, field_name, None)
				if val is not None and val != '':
					initial.setdefault(field_name, val)
		form = PropostaAssunzioneForm(azienda_operativa=azienda_operativa, initial=initial,
			dipendente_prefill_id=dipendente_id_prefill)

	return render(
		request,
		'rapporto_di_lavoro/crea_proposta.html',
		{
			'form': form,
			'azienda_operativa': azienda_operativa,
			'page_title': 'Nuova Proposta di Assunzione',
			'submit_label': 'Crea proposta',
			'is_edit_mode': False,
			'dipendente_id_prefill': dipendente_id_prefill,
			'forza_nuova': forza_nuova,
			'proposta_js_version': getattr(settings, 'PROPOSTA_JS_VERSION', '2026-04-18-1'),
		},
	)


@login_required
@user_passes_test(_is_admin_only)
def modifica_proposta(request, proposta_id):
	proposta = get_object_or_404(PropostaAssunzione, id=proposta_id)
	azienda_operativa = _get_azienda_operativa_per_utente(request.user, request.session)
	if not azienda_operativa or proposta.azienda_id != azienda_operativa.id:
		return HttpResponseForbidden("Accesso negato")

	if request.method == 'POST':
		post_data = _normalizza_ore_post_data(request.POST)
		form = PropostaAssunzioneForm(post_data, request.FILES, instance=proposta, azienda_operativa=azienda_operativa)
		if form.is_valid():
			proposta = form.save(commit=False)
			proposta.modificato_da = request.user

			# Gestione upload mansionario
			if form.cleaned_data.get('mansionario_file'):
				proposta.mansionario_file = form.cleaned_data['mansionario_file']

			# In modifica admin ricalcola sempre tutte le voci per allineamento motore
			_autocompleta_retribuzione_proposta(proposta, azienda_operativa, preserve_manual=True)

			proposta.compila_riferimenti_normativi()
			proposta.save()
			messages.success(request, f'Proposta {proposta.numero_proposta} aggiornata con successo.')
			return redirect('dettaglio_proposta', proposta_id=proposta.id)
	else:
		form = PropostaAssunzioneForm(instance=proposta, azienda_operativa=azienda_operativa)

	return render(
		request,
		'rapporto_di_lavoro/crea_proposta.html',
		{
			'form': form,
			'azienda_operativa': azienda_operativa,
			'page_title': f'Modifica Proposta {proposta.numero_proposta}',
			'submit_label': 'Salva modifiche',
			'is_edit_mode': True,
			'proposta_js_version': getattr(settings, 'PROPOSTA_JS_VERSION', '2026-04-18-1'),
		},
	)


@login_required
@user_passes_test(_is_admin_like)
@require_http_methods(['POST'])
def elimina_proposta(request, proposta_id):
	proposta = get_object_or_404(PropostaAssunzione, id=proposta_id)
	azienda_operativa = _get_azienda_operativa_per_utente(request.user, request.session)
	if not azienda_operativa or proposta.azienda_id != azienda_operativa.id:
		return HttpResponseForbidden("Accesso negato")
	if proposta.stato != 'bozza':
		messages.error(request, 'È possibile eliminare solo proposte in stato bozza.')
		return redirect('dettaglio_proposta', proposta_id=proposta.id)
	numero = proposta.numero_proposta
	proposta.delete()
	messages.success(request, f'Proposta {numero} eliminata.')
	return redirect('lista_proposte_assunzione')


def _prova_fipe(livello_ccnl, tipo_contratto_nome=''):
	"""Restituisce (giorni, descrizione_testuale) del periodo di prova CCNL FIPE
	imprese minori, per contratti a tempo indeterminato.
	Per contratti a tempo determinato: non superiore a 1/4 della durata, e comunque
	non previsto per contratti < 30 giorni (art. 7 D.Lgs. 81/2015)."""
	l = str(livello_ccnl or '').strip().upper().replace(' ', '')
	if l in ('Q', 'QA', 'QB', 'QUADRO'):
		return 120, '4 mesi di calendario'
	if l in ('1', '1A', '1B', 'I'):
		return 90, '3 mesi di calendario'
	if l in ('2', '2A', 'II'):
		return 90, '3 mesi di calendario'
	if l in ('3', '3A', 'III'):
		return 60, '2 mesi di calendario'
	if l in ('4', '4A', 'IV'):
		return 60, '2 mesi di calendario'
	if l in ('5', '5A', 'V'):
		return 30, '1 mese di calendario'
	if l in ('6', 'VI'):
		return 30, '1 mese di calendario'
	if l in ('6S', 'VIS', '6-S'):
		return 10, '10 giorni di lavoro effettivo'
	if l in ('7', 'VII'):
		return 10, '10 giorni di lavoro effettivo'
	# default
	return 30, '1 mese di calendario'


def _proposta_context_extra(proposta):
	"""Dati calcolati aggiuntivi per il rendering della proposta."""
	from decimal import Decimal, ROUND_HALF_UP
	Q2 = Decimal('0.01')
	Q4 = Decimal('0.0001')

	# Coefficiente part-time effettivo: priorita al tipo contratto, fallback su ore.
	coeff_pt = getattr(getattr(proposta, 'tipo_contratto', None), 'coefficiente_ore', None)
	if coeff_pt in (None, Decimal('0')):
		try:
			ore_sett = Decimal(str(proposta.ore_settimanali or '0'))
			coeff_pt = (ore_sett / Decimal('40')) if ore_sett > 0 else Decimal('1')
		except Exception:
			coeff_pt = Decimal('1')
	coeff_pt = Decimal(str(coeff_pt or '1'))

	def _q2(v):
		return Decimal(str(v or '0')).quantize(Q2, rounding=ROUND_HALF_UP)

	# Art. 5: paga base / contingenza / EDR da tabella CCNL (full-time sul parametro) × coeff. part-time.
	# I campi sulla proposta possono restare disallineati in bozza; per PDF e dettaglio la fonte è il parametro.
	art5_indennita = _q2(proposta.indennita_mensile)
	art5_superminimo = _q2(getattr(proposta, 'superminimo_mensile', None))
	param = proposta.parametro_ccnl_risolto
	art5_paga_base = _q2(proposta.paga_base_mensile)
	art5_contingenza = _q2(proposta.contingenza_mensile)
	art5_edr = _q2(proposta.edr_mensile)
	art5_tabellare_tot = (
		art5_paga_base + art5_contingenza + art5_edr + art5_superminimo + art5_indennita
	).quantize(Q2, rounding=ROUND_HALF_UP)
	if param is not None:
		base_ft = _q2(getattr(param, 'paga_base_mensile', 0))
		if base_ft <= 0:
			base_ft = _q2(getattr(param, 'minimo_tabellare', 0))
		cont_ft = _q2(getattr(param, 'contingenza_mensile', 0))
		edr_ft = _q2(getattr(param, 'edr_mensile', 0))
		if base_ft > 0 or cont_ft > 0 or edr_ft > 0:
			art5_paga_base = (base_ft * coeff_pt).quantize(Q2, rounding=ROUND_HALF_UP)
			art5_contingenza = (cont_ft * coeff_pt).quantize(Q2, rounding=ROUND_HALF_UP)
			art5_edr = (edr_ft * coeff_pt).quantize(Q2, rounding=ROUND_HALF_UP)
			art5_tabellare_tot = (
				art5_paga_base + art5_contingenza + art5_edr + art5_superminimo + art5_indennita
			).quantize(Q2, rounding=ROUND_HALF_UP)

	# Retribuzione tabellare mensile (Art. 5): mai include i 1/12 di 13ª/14ª se non rateizzati in busta.
	lor_tab = art5_tabellare_tot
	tredicesima_attiva = bool(getattr(proposta, 'tredicesima', True))
	quattordicesima_attiva = bool(getattr(proposta, 'quattordicesima', False))
	# Ratei teorici (1/12): sempre esposti a video; entrano in busta solo se flag attivo.
	rat13_mensile = (lor_tab / Decimal('12')).quantize(Q2, rounding=ROUND_HALF_UP)
	rat14_mensile = (lor_tab / Decimal('12')).quantize(Q2, rounding=ROUND_HALF_UP)
	rat13_in_busta = rat13_mensile if tredicesima_attiva else Decimal('0')
	rat14_in_busta = rat14_mensile if quattordicesima_attiva else Decimal('0')
	tot_mensilita_aggiuntive = rat13_in_busta + rat14_in_busta
	lordo_mensile_totale = (lor_tab + tot_mensilita_aggiuntive).quantize(Q2, rounding=ROUND_HALF_UP)
	ha_ratei_in_busta = tot_mensilita_aggiuntive > 0
	# RAL convenzionale 14 mensilità (base tabellare × 14; le 13ª/14ª sono ulteriori mensilità CCNL).
	ral = (lor_tab * Decimal('14')).quantize(Q2, rounding=ROUND_HALF_UP)

	# Quota oraria / giornaliera: sul compenso mensile effettivamente corrisposto in busta (con ratei se attivi).
	ore_mensili = proposta.ore_mensili or Decimal('173.33')
	if ore_mensili > 0:
		paga_oraria = (lordo_mensile_totale / ore_mensili).quantize(Q4, rounding=ROUND_HALF_UP)
	else:
		paga_oraria = Decimal('0')
	paga_giornaliera = (lordo_mensile_totale / Decimal('26')).quantize(Q2, rounding=ROUND_HALF_UP)

	prova_gg, prova_desc = _prova_fipe(
		proposta.livello_ccnl,
		proposta.tipo_contratto.nome if proposta.tipo_contratto else '',
	)
	e_determinato = proposta.data_fine_rapporto is not None
	# Per determinato: prova = 1/4 della durata (se < prova calcolata per indet.)
	if e_determinato and proposta.data_inizio_rapporto and proposta.data_fine_rapporto:
		durata_gg = (proposta.data_fine_rapporto - proposta.data_inizio_rapporto).days
		prova_det = max(0, durata_gg // 4)
		if prova_det < prova_gg:
			prova_gg = prova_det
			if prova_gg <= 0:
				prova_desc = 'non previsto (contratto di breve durata)'
			else:
				prova_desc = f'{prova_gg} giorni (pari a 1/4 della durata del contratto)'
	return {
		'ral': ral,
		'paga_oraria': paga_oraria,
		'paga_giornaliera': paga_giornaliera,
		'coeff_pt': coeff_pt,
		'art5_paga_base': art5_paga_base,
		'art5_contingenza': art5_contingenza,
		'art5_edr': art5_edr,
		'art5_superminimo': art5_superminimo,
		'art5_indennita': art5_indennita,
		'art5_tabellare_tot': art5_tabellare_tot,
		'tot_retribuzione_lorda_tabellare_mensile': lor_tab,
		'ha_ratei_in_busta': ha_ratei_in_busta,
		'prova_giorni': prova_gg,
		'prova_descrizione': prova_desc,
		'e_determinato': e_determinato,
		'tredicesima_attiva': tredicesima_attiva,
		'quattordicesima_attiva': quattordicesima_attiva,
		'rat13_mensile': rat13_mensile,
		'rat14_mensile': rat14_mensile,
		'tot_mensilita_aggiuntive': tot_mensilita_aggiuntive,
		'lordo_mensile_totale': lordo_mensile_totale,
	}


def dettaglio_proposta(request, proposta_id):
	proposta = _get_proposta_con_permesso(request, proposta_id)
	if not proposta:
		return HttpResponseForbidden("Accesso negato")
	eventi_documento = _eventi_documento_per_riferimenti(
		proposta.dipendente,
		proposta.azienda,
		proposta.numero_proposta,
		getattr(proposta.contratto_generato, 'numero_contratto', ''),
	)
	show_service_data = request.user.is_superuser or (request.user.has_ruolo('admin') or request.user.has_ruolo('hr'))
	ctx = {
		'proposta': proposta,
		'show_service_data': show_service_data,
		'puo_convertire': proposta.puo_essere_convertita(),
		'motivi_blocco': proposta.motivi_blocco_conversione(),
		'eventi_documento': eventi_documento,
	}
	ctx.update(_proposta_context_extra(proposta))
	return render(request, 'rapporto_di_lavoro/dettaglio_proposta.html', ctx)


def _genera_proposta_pdf(proposta, extra):
	"""Genera il PDF professionale della proposta usando ReportLab Platypus."""
	from decimal import Decimal as D

	# ── Colori ────────────────────────────────────────────────────
	C_PRIMARY   = HexColor('#1b3a5f')
	C_BORDER    = HexColor('#c5d0dc')
	C_ALT       = HexColor('#e8eef5')
	C_GREEN     = HexColor('#0a3d1f')
	C_MUTED     = HexColor('#666666')
	C_HIGHLIGHT = HexColor('#ddeeff')

	# ── Misure ────────────────────────────────────────────────────
	W, H      = A4
	M_LR      = 18 * mm
	HEADER_H  = 30 * mm   # spazio riservato all'intestazione
	FOOTER_H  = 10 * mm   # spazio riservato al piè di pagina

	# ── Stili testo ───────────────────────────────────────────────
	def S(name, **kw):
		return ParagraphStyle(name, **kw)

	s_normal = S('normal',
		fontName='Times-Roman', fontSize=10.5, leading=15,
		alignment=TA_JUSTIFY, spaceAfter=2*mm)
	s_bold = S('bold',
		fontName='Times-Bold', fontSize=10.5, leading=15,
		alignment=TA_JUSTIFY, spaceAfter=2*mm)
	s_art_title = S('art_title',
		fontName='Helvetica-Bold', fontSize=10.5, leading=13,
		textColor=C_PRIMARY, spaceBefore=5*mm, spaceAfter=1*mm)
	s_small = S('small',
		fontName='Times-Roman', fontSize=8.5, leading=11,
		textColor=C_MUTED, spaceAfter=1.5*mm)
	s_dest_label = S('dest_label',
		fontName='Helvetica', fontSize=7.5, leading=10,
		textColor=C_MUTED, spaceAfter=0.5*mm,
		fontStyle='italic')  # textTransform non esiste, uppercase va in testo
	s_dest_name = S('dest_name',
		fontName='Times-Bold', fontSize=12, leading=15, spaceAfter=0.5*mm)
	s_dest_detail = S('dest_detail',
		fontName='Times-Roman', fontSize=9.5, leading=12, textColor=C_MUTED)
	s_oggetto = S('oggetto',
		fontName='Times-Bold', fontSize=11, leading=14,
		spaceBefore=3*mm, spaceAfter=3*mm)
	s_apertura = S('apertura',
		fontName='Times-Roman', fontSize=10.5, leading=15,
		alignment=TA_JUSTIFY, spaceAfter=4*mm)
	s_firma_label = S('firma_label',
		fontName='Helvetica-Bold', fontSize=8.5, leading=11,
		textColor=C_MUTED, spaceAfter=8*mm)
	s_firma_sub = S('firma_sub',
		fontName='Times-Roman', fontSize=9, leading=11,
		textColor=C_MUTED, spaceAfter=1*mm)
	s_dichiarazione = S('dichiarazione',
		fontName='Times-Roman', fontSize=9, leading=12,
		alignment=TA_JUSTIFY, spaceAfter=1.5*mm)
	s_clausola = S('clausola',
		fontName='Times-Roman', fontSize=10, leading=14,
		alignment=TA_JUSTIFY, spaceAfter=0)
	s_right = S('right',
		fontName='Times-Roman', fontSize=10.5, leading=15,
		alignment=TA_RIGHT, spaceAfter=2*mm)
	s_bold_right = S('bold_right',
		fontName='Times-Bold', fontSize=10.5, leading=15,
		alignment=TA_RIGHT, spaceAfter=2*mm)

	# ── Stile tabella standard ─────────────────────────────────────
	def tabella_style(highlight_rows=None, right_col1=False):
		cmds = [
			('FONTNAME',    (0, 0), (-1, 0),  'Helvetica-Bold'),
			('FONTSIZE',    (0, 0), (-1, -1), 9.5),
			('LEADING',     (0, 0), (-1, -1), 12),
			('BACKGROUND',  (0, 0), (-1, 0),  C_ALT),
			('TEXTCOLOR',   (0, 0), (-1, 0),  C_PRIMARY),
			('GRID',        (0, 0), (-1, -1), 0.4, C_BORDER),
			('TOPPADDING',  (0, 0), (-1, -1), 3),
			('BOTTOMPADDING',(0,0), (-1, -1), 3),
			('LEFTPADDING', (0, 0), (-1, -1), 4),
			('RIGHTPADDING',(0, 0), (-1, -1), 4),
			('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, HexColor('#f8fafc')]),
			('VALIGN',      (0, 0), (-1, -1), 'TOP'),
		]
		if highlight_rows:
			for r in highlight_rows:
				cmds.append(('BACKGROUND', (0, r), (-1, r), C_HIGHLIGHT))
				cmds.append(('FONTNAME',   (0, r), (-1, r), 'Times-Bold'))
		if right_col1:
			cmds.append(('ALIGN', (1, 0), (1, -1), 'RIGHT'))
		return TableStyle(cmds)

	# ── NumberedCanvas: piè di pagina con X/Y ─────────────────────
	# Cattura le variabili di layout nella closure
	_data_doc = proposta.data_creazione.strftime('%d/%m/%Y') if proposta.data_creazione else ''

	class _NumberedCanvas(canvas.Canvas):
		"""Canvas custom: memorizza le pagine per stampare "Pag. X di Y"."""
		def __init__(self, *args, **kwargs):
			canvas.Canvas.__init__(self, *args, **kwargs)
			self._saved_page_states = []

		def showPage(self):
			self._saved_page_states.append(dict(self.__dict__))
			self._startPage()

		def save(self):
			total = len(self._saved_page_states)
			for state in self._saved_page_states:
				self.__dict__.update(state)
				self._draw_footer(total)
				canvas.Canvas.showPage(self)
			canvas.Canvas.save(self)

		def _draw_footer(self, total):
			x0 = M_LR
			x1 = W - M_LR
			y_line = 15*mm
			y_text = 10*mm
			self.saveState()
			# Linea separatrice
			self.setStrokeColor(C_BORDER)
			self.setLineWidth(0.4)
			self.line(x0, y_line, x1, y_line)
			self.setFont('Helvetica', 7.5)
			self.setFillColor(C_MUTED)
			# Sinistra: data del documento
			self.drawString(x0, y_text, _data_doc)
			# Centro: Pagina X di Y
			self.drawCentredString(W / 2, y_text, f"Pagina {self._pageNumber} di {total}")
			# Destra: riferimento CCNL
			self.drawRightString(x1, y_text, "CCNL FIPE — Turismo Imprese minori")
			self.restoreState()

	# ── Callback solo intestazione (il footer è in _NumberedCanvas) ─
	def draw_header(canv, doc):
		canv.saveState()

		x0 = M_LR
		x1 = W - M_LR
		y_top = H - 15*mm

		# Nome azienda (sinistra)
		canv.setFont('Helvetica-Bold', 13)
		canv.setFillColor(C_PRIMARY)
		canv.drawString(x0, y_top, proposta.azienda.nome.upper())

		# Numero proposta (destra)
		canv.setFont('Helvetica-Bold', 9)
		canv.setFillColor(C_PRIMARY)
		canv.drawRightString(x1, y_top, f"Rif. {proposta.numero_proposta}")

		y2 = y_top - 4.5*mm
		# Indirizzo / email / tel (sinistra)
		canv.setFont('Helvetica', 8.5)
		canv.setFillColor(C_MUTED)
		addr_parts = []
		if proposta.azienda.indirizzo:
			addr_parts.append(proposta.azienda.indirizzo)
		if proposta.azienda.email:
			addr_parts.append(proposta.azienda.email)
		if proposta.azienda.telefono:
			addr_parts.append(f"Tel. {proposta.azienda.telefono}")
		if addr_parts:
			canv.drawString(x0, y2, '  —  '.join(addr_parts))

		y3 = y2 - 4*mm
		# P.IVA
		if proposta.azienda.partita_iva:
			canv.setFont('Helvetica', 8)
			canv.setFillColor(C_MUTED)
			canv.drawString(x0, y3, f"P.IVA {proposta.azienda.partita_iva}")

		# Linea separatrice blu (spessa)
		y_line = y3 - 3*mm
		canv.setStrokeColor(C_PRIMARY)
		canv.setLineWidth(1.5)
		canv.line(x0, y_line, x1, y_line)

		canv.restoreState()

	# ── Documento ─────────────────────────────────────────────────
	buffer = BytesIO()
	doc = SimpleDocTemplate(
		buffer,
		pagesize=A4,
		leftMargin=M_LR,
		rightMargin=M_LR,
		topMargin=HEADER_H + 8*mm,    # lascia spazio all'intestazione
		bottomMargin=FOOTER_H + 8*mm,
		title=f"Proposta {proposta.numero_proposta}",
		author=proposta.azienda.nome,
	)

	col_w = W - 2 * M_LR   # larghezza utile

	# ── Helper: articolo ──────────────────────────────────────────
	def articolo(num, titolo, *flowables):
		items = [
			Paragraph(f"Art. {num} — {titolo.upper()}", s_art_title),
			HRFlowable(width=col_w, thickness=0.5, color=C_BORDER, spaceAfter=2*mm),
		] + list(flowables)
		return KeepTogether(items)

	def p(text, style=None):
		return Paragraph(text, style or s_normal)

	def ul(*items):
		rows = []
		for item in items:
			rows.append(
				Table([[p('•', s_normal), p(item, s_normal)]],
					  colWidths=[5*mm, col_w - 5*mm],
					  style=TableStyle([
						  ('VALIGN', (0,0), (-1,-1), 'TOP'),
						  ('LEFTPADDING', (0,0), (-1,-1), 0),
						  ('RIGHTPADDING', (0,0), (-1,-1), 0),
						  ('TOPPADDING', (0,0), (-1,-1), 0),
						  ('BOTTOMPADDING', (0,0), (-1,-1), 1),
					  ]))
			)
		return rows

	def euro(v):
		if v is None:
			return '—'
		try:
			s = euro_it_str(v)
			return f'{s} €' if s else '—'
		except Exception:
			return str(v)

	# ── Dati extra ────────────────────────────────────────────────
	ral              = extra.get('ral', D('0'))
	paga_oraria      = extra.get('paga_oraria', D('0'))
	paga_giornaliera = extra.get('paga_giornaliera', D('0'))
	rat13_mensile    = extra.get('rat13_mensile', D('0'))
	rat14_mensile    = extra.get('rat14_mensile', D('0'))
	lordo_mensile_totale = extra.get('lordo_mensile_totale', D('0'))
	ha_ratei_in_busta = extra.get('ha_ratei_in_busta', False)
	tred_att_pdf     = extra.get('tredicesima_attiva', True)
	quatt_att_pdf    = extra.get('quattordicesima_attiva', False)
	prova_giorni    = extra.get('prova_giorni', 0)
	prova_desc      = extra.get('prova_descrizione', '')
	e_det           = extra.get('e_determinato', False)
	art5_pb         = extra.get('art5_paga_base', proposta.paga_base_mensile)
	art5_cont       = extra.get('art5_contingenza', proposta.contingenza_mensile)
	art5_edr_v      = extra.get('art5_edr', proposta.edr_mensile)
	art5_sup        = extra.get('art5_superminimo', getattr(proposta, 'superminimo_mensile', None))
	art5_ind        = extra.get('art5_indennita', proposta.indennita_mensile)
	art5_tab_tot    = extra.get('art5_tabellare_tot', extra.get('tot_retribuzione_lorda_tabellare_mensile', proposta.stipendio_lordo_mensile))

	has_scatti = bool(proposta.scatto_importo and proposta.scatto_importo > 0)
	art_n = lambda base: base if has_scatti else base - 1  # offset art. dopo scatti

	ore_sett = proposta.ore_settimanali or D('40')
	is_pt = ore_sett < D('40')

	# ── Story ─────────────────────────────────────────────────────
	story = []

	# Destinatario
	dest_rows = []
	dest_rows.append(p('EGREGIO / GENTILE', s_dest_label))
	dest_rows.append(p(f"{proposta.dipendente.nome} {proposta.dipendente.cognome}", s_dest_name))
	if proposta.dipendente.indirizzo:
		dest_rows.append(p(proposta.dipendente.indirizzo, s_dest_detail))
	if proposta.dipendente.data_nascita:
		cf_part = f"  —  C.F. {proposta.dipendente.codice_fiscale}" if proposta.dipendente.codice_fiscale else ''
		dest_rows.append(p(
			f"Nato/a il {proposta.dipendente.data_nascita.strftime('%d/%m/%Y')}{cf_part}",
			s_dest_detail))
	story += dest_rows
	story.append(HRFlowable(width=col_w, thickness=0.4, color=C_BORDER,
							spaceBefore=2*mm, spaceAfter=3*mm))

	# Oggetto
	story.append(p(
		f"<b>OGGETTO: Proposta di Assunzione — {proposta.tipo_contratto.nome} — "
		f"Livello {proposta.livello_ccnl} CCNL Turismo Confcommercio — Imprese minori</b>",
		s_oggetto))

	# Apertura
	azienda = proposta.azienda
	addr_testo = f", con sede legale in <b>{azienda.indirizzo}</b>" if azienda.indirizzo else ''
	piva_testo = f", P.IVA {azienda.partita_iva}" if azienda.partita_iva else ''
	story.append(p(
		f"Con la presente la società <b>{azienda.nome}</b>{addr_testo}{piva_testo}, "
		f"di seguito denominata <i>\"Datore di Lavoro\"</i>, è lieta di formulare a "
		f"<b>{proposta.dipendente.nome} {proposta.dipendente.cognome}</b>, di seguito "
		f"denominato/a <i>\"Lavoratore/Lavoratrice\"</i>, la seguente proposta di assunzione "
		f"con decorrenza dal <b>{proposta.data_inizio_rapporto.strftime('%d/%m/%Y')}</b>, "
		f"alle condizioni e ai patti di seguito indicati, nel rispetto della normativa "
		f"italiana vigente e del Contratto Collettivo Nazionale di Lavoro applicato.",
		s_apertura))

	# ── ART. 1 ────────────────────────────────────────────────────
	if e_det:
		tipo_testo = (
			f"a <b>tempo determinato</b> ai sensi dell'art. 19 del D.Lgs. 81/2015, "
			f"con termine fissato al <b>{proposta.data_fine_rapporto.strftime('%d/%m/%Y')}</b>.")
	else:
		tipo_testo = "a <b>tempo indeterminato</b> ai sensi dell'art. 1 del D.Lgs. 81/2015."
	pt_testo = (f"Tipologia: <b>{proposta.tipo_contratto.nome}</b>, orario part-time "
				f"{ore_sett} ore settimanali." if is_pt else
				f"Tipologia: <b>{proposta.tipo_contratto.nome}</b>.")
	story.append(articolo(1, "Tipologia contrattuale e normativa applicata",
		p(f"Il rapporto di lavoro si costituisce {tipo_testo}"),
		p("Al rapporto si applica il <b>CCNL FIPE — Turismo, Ristoranti, Pizzerie con cucina "
		  "e similari — Imprese minori</b> (Confcommercio / FILCAMS-CGIL, FISASCAT-CISL, UILTuCS), "
		  "ivi inclusi gli aggiornamenti delle tabelle retributive vigenti alla data di assunzione."),
		p(pt_testo),
	))

	# ── ART. 2 ────────────────────────────────────────────────────
	sede = azienda.indirizzo or azienda.nome
	tbl_inq = Table(
		[
			[p('<b>Elemento</b>', s_normal), p('<b>Dato</b>', s_normal)],
			[p('Livello di inquadramento', s_normal), p(f"<b>Livello {proposta.livello_ccnl}</b>", s_normal)],
			[p('Qualifica contrattuale', s_normal),   p(proposta.qualifica or '—', s_normal)],
			[p('Mansione prevalente', s_normal),       p(proposta.posizione or '—', s_normal)],
			[p('Sede di lavoro', s_normal),            p(sede, s_normal)],
		],
		colWidths=[col_w * 0.38, col_w * 0.62],
		style=tabella_style(),
	)
	story.append(articolo(2, "Categoria, livello e mansioni",
		p("Il/La Lavoratore/Lavoratrice è assunto/a con il seguente inquadramento ai sensi "
		  "dell'art. 89 e ss. del CCNL FIPE — Imprese minori:"),
		tbl_inq,
		Spacer(1, 2*mm),
		p("Le mansioni potranno essere integrate da attività accessorie coerenti con il livello "
		  "di inquadramento, ai sensi dell'art. 2103 c.c. come modificato dal D.Lgs. 81/2015."),
	))

	# ── ART. 3 ────────────────────────────────────────────────────
	fine_testo = f" fino al <b>{proposta.data_fine_rapporto.strftime('%d/%m/%Y')}</b>" if e_det else ''
	if prova_giorni > 0:
		prova_testo = [
			p(f"È previsto un periodo di prova di <b>{prova_desc}</b> ai sensi dell'art. 95 "
			  "del CCNL FIPE — Imprese minori. Durante il periodo entrambe le parti possono "
			  "recedere senza preavviso."),
			p("Al superamento della prova il rapporto si consolida con anzianità decorrente "
			  "dalla data di inizio del periodo di prova."),
		]
	else:
		prova_testo = [p("Il periodo di prova non è previsto in considerazione della breve "
						  "durata del contratto (art. 7, co. 3, D.Lgs. 81/2015).")]
	story.append(articolo(3, "Decorrenza e periodo di prova",
		p(f"Il rapporto di lavoro decorrerà dal "
		  f"<b>{proposta.data_inizio_rapporto.strftime('%d/%m/%Y')}</b>{fine_testo}."),
		*prova_testo,
	))

	# ── ART. 4 ────────────────────────────────────────────────────
	if is_pt:
		orario_intro = p(
			f"Il rapporto è a <b>tempo parziale</b> (part-time orizzontale), con orario "
			f"settimanale di <b>{ore_sett} ore</b> su 40 ore full-time, ai sensi del "
			f"D.Lgs. 81/2015 artt. 4 e ss.")
	else:
		orario_intro = p(
			f"L'orario di lavoro è pari a <b>{proposta.ore_settimanali or 40} ore settimanali</b> "
			f"(art. 74 CCNL FIPE — Imprese minori; D.Lgs. 66/2003).")
	story.append(articolo(4, "Orario di lavoro",
		orario_intro,
		p(f"Articolazione: {proposta.ore_mensili} ore mensili convenzionali — "
		  f"{proposta.ore_giornaliere} ore giornaliere standard. L'orario può prevedere:"),
		*ul(
			"distribuzione su 5 o 6 giorni lavorativi con riposi compensativi;",
			"turni spezzati compatibili con l'attività di somministrazione;",
			"lavoro festivo e/o notturno con le maggiorazioni previste dal CCNL.",
		),
		p("Il riposo settimanale è garantito (art. 9 D.Lgs. 66/2003) nella misura di almeno "
		  "24 ore consecutive ogni 7 giorni, con le maggiorazioni previste dal CCNL FIPE "
		  "per il lavoro domenicale."),
	))

	# ── ART. 5 ────────────────────────────────────────────────────
	tbl_ret_data = [
		[p('<b>Voce retributiva</b>', s_normal),
		 p('<b>Importo mensile lordo</b>', s_bold_right)],
		[p('Paga base tabellare (art. 117 CCNL FIPE)', s_normal),
		 p(f"{euro(art5_pb)}", s_right)],
		[p('Indennità di contingenza', s_normal),
		 p(f"{euro(art5_cont)}", s_right)],
	]
	if art5_edr_v and art5_edr_v > 0:
		tbl_ret_data.append([
			p('E.D.R. (Elemento Distinto dalla Retribuzione — Prot. 31/07/1992)', s_normal),
			p(f"{euro(art5_edr_v)}", s_right),
		])
	if art5_sup and art5_sup > 0:
		tbl_ret_data.append([
			p('Superminimo (oltre minimo tabellare CCNL)', s_normal),
			p(f"{euro(art5_sup)}", s_right),
		])
	if art5_ind and art5_ind > 0:
		tbl_ret_data.append([
			p('Altre indennità (es. di funzione)', s_normal),
			p(f"{euro(art5_ind)}", s_right),
		])
	if tred_att_pdf:
		tbl_ret_data.append([
			p('<b>Rateo mensile 13ª in busta (1/12)</b>', s_normal),
			p(f"<b>{euro(rat13_mensile)}</b>", s_bold_right),
		])
	if quatt_att_pdf:
		tbl_ret_data.append([
			p('<b>Rateo mensile 14ª in busta (1/12)</b>', s_normal),
			p(f"<b>{euro(rat14_mensile)}</b>", s_bold_right),
		])
	hl_tot = len(tbl_ret_data)
	tbl_ret_data.append([
		p('<b>Totale retribuzione lorda mensile</b>', s_bold),
		p(f"<b>{euro(lordo_mensile_totale if ha_ratei_in_busta else art5_tab_tot)}</b>", s_bold_right),
	])
	tbl_ret_data.append([
		p('Rateizzazione mensile 13ª (deroga richiesta dipendente)', s_normal),
		p(f"<b>{'SI' if tred_att_pdf else 'NO'}</b>", s_bold_right),
	])
	tbl_ret_data.append([
		p('Rateizzazione mensile 14ª (deroga richiesta dipendente)', s_normal),
		p(f"<b>{'SI' if quatt_att_pdf else 'NO'}</b>", s_bold_right),
	])
	if not tred_att_pdf:
		tbl_ret_data.append([
			p(
				'Rateo mensile 13ª in busta (1/12) <i>(rateo teorico non erogato mensilmente in busta)</i>',
				s_normal,
			),
			p(f"{euro(rat13_mensile)}", s_right),
		])
	if not quatt_att_pdf:
		tbl_ret_data.append([
			p(
				'Rateo mensile 14ª in busta (1/12) <i>(rateo teorico non erogato mensilmente in busta)</i>',
				s_normal,
			),
			p(f"{euro(rat14_mensile)}", s_right),
		])
	hl_tot2 = len(tbl_ret_data) if ha_ratei_in_busta else None
	if hl_tot2 is not None:
		tbl_ret_data.append([
			p('<b>Costo lordo mensile totale (retrib. + ratei mensilità aggiuntive)</b>', s_bold),
			p(f"<b>{euro(lordo_mensile_totale)}</b>", s_bold_right),
		])
	tbl_ret_data.append([
		p('<b>Retribuzione Annua Lorda (RAL) — 14 mensilità</b>', s_bold),
		p(f"<b>{euro(ral)}</b>", s_bold_right),
	])
	_oraria_lbl = (
		f"Quota oraria lorda  (lordo mensile in busta ÷ {proposta.ore_mensili} ore/mese)"
		if ha_ratei_in_busta
		else f"Quota oraria lorda  (retribuzione tabellare mensile ÷ {proposta.ore_mensili} ore/mese)"
	)
	tbl_ret_data.append([
		p(_oraria_lbl, s_normal),
		p(f"{num_it_str(paga_oraria, 4)} €/ora", s_right),
	])
	_gg_lbl = (
		"Quota giornaliera lorda  (lordo mensile in busta ÷ 26 gg convenzionali CCNL)"
		if ha_ratei_in_busta
		else "Quota giornaliera lorda  (retribuzione tabellare mensile ÷ 26 gg convenzionali CCNL)"
	)
	tbl_ret_data.append([
		p(_gg_lbl, s_normal),
		p(f"{euro(paga_giornaliera)}/giorno", s_right),
	])
	hl_rows = [hl_tot]
	if hl_tot2:
		hl_rows.append(hl_tot2)
	tbl_ret = Table(
		tbl_ret_data,
		colWidths=[col_w * 0.60, col_w * 0.40],
		style=tabella_style(highlight_rows=hl_rows, right_col1=True),
	)
	story.append(articolo(5, "Trattamento economico",
		p("Il trattamento economico mensile lordo, conforme alle tabelle retributive vigenti "
		  "del CCNL FIPE — Imprese minori, è il seguente:"),
		tbl_ret,
		Spacer(1, 2*mm),
		p("La retribuzione è erogata entro il <b>15 del mese successivo</b> a quello di "
		  "competenza (art. 128 CCNL FIPE), esclusivamente mediante <b>bonifico bancario</b> "
		  "sull'IBAN comunicato per iscritto dal/dalla Lavoratore/Lavoratrice (art. 1, co. 910, "
		  "L. 205/2017). Il cedolino è reso disponibile in formato elettronico entro la medesima data."),
	))

	# ── ART. 6 ────────────────────────────────────────────────────
	_art6_items = []
	_r13_txt = f" Il rateo mensile accantonato è <b>{euro(rat13_mensile)}</b>." if rat13_mensile and rat13_mensile > 0 else ""
	_r14_txt = f" Il rateo mensile accantonato è <b>{euro(rat14_mensile)}</b>." if rat14_mensile and rat14_mensile > 0 else ""
	_art6_items.append(p(
		f"<b>Tredicesima</b> (art. 133 CCNL FIPE): una mensilità intera, erogata entro il "
		f"<b>24 dicembre</b>, in proporzione ai mesi di servizio (1/12 per mese).{_r13_txt} "
		f"Il rateo è <b>accantonato mensilmente</b> e incluso nella base di calcolo del costo del lavoro."))
	_art6_items.append(p(
		f"<b>Quattordicesima</b> (art. 134 CCNL FIPE): una mensilità intera, erogata entro il "
		f"<b>10 luglio</b>, in proporzione ai mesi di servizio.{_r14_txt} "
		f"Il rateo è <b>accantonato mensilmente</b> e incluso nella base di calcolo del costo del lavoro."))
	_art6_items.append(p("Frazioni superiori a 15 giorni sono conteggiate come mese intero."))
	story.append(articolo(6, "Mensilità aggiuntive", *_art6_items))

	# ── ART. 7 ────────────────────────────────────────────────────
	story.append(articolo(7, "Trattamento di Fine Rapporto (TFR)",
		p("TFR maturato al <b>6,91%</b> della retribuzione imponibile (art. 2120 c.c.)."),
		p("Il lavoratore esprime la scelta destinazione TFR entro 6 mesi dall'assunzione:"),
		*ul(
			"previdenza complementare (D.Lgs. 252/2005);",
			"mantenimento in azienda (< 50 dipendenti) o Fondo Tesoreria INPS (≥ 50 dipendenti, L. 296/2006).",
		),
		p("In assenza di scelta, il TFR è destinato alla forma pensionistica collettiva del CCNL."),
	))

	# ── ART. 8 ────────────────────────────────────────────────────
	story.append(articolo(8, "Ferie, permessi e riposi",
		p("<b>Ferie</b> (art. 111 CCNL FIPE): <b>26 giorni lavorativi</b> per anno solare "
		  "(D.Lgs. 66/2003 art. 10: minimo 2 settimane consecutive). Residuo da fruire entro "
		  "18 mesi dalla maturazione."),
		p("<b>ROL — ex festività soppresse</b> (art. 113 CCNL FIPE): <b>88 ore annue</b> (4 giorni) "
		  "per festività soppresse (D.P.R. 792/1985). Maturazione pro-rata."),
		p("<b>Riposi settimanali e compensativi</b>: garantiti ai sensi dell'art. 36 Cost., "
		  "art. 9 D.Lgs. 66/2003 e art. 74 ss. CCNL FIPE."),
	))

	# ── ART. 9 — SCATTI (condizionale) ───────────────────────────
	if has_scatti:
		story.append(articolo(9, "Scatti di anzianità",
			p(f"Ai sensi dell'art. 121 CCNL FIPE, maturazione di <b>{euro(proposta.scatto_importo)} "
			  f"ogni {proposta.scatto_periodicita_mesi} mesi</b> di servizio, "
			  f"fino a <b>{proposta.numero_scatti_massimi} scatti</b> massimi."),
			p("Gli scatti decorrono dal primo giorno del mese successivo alla maturazione "
			  "e si cumulano ai fini del trattamento retributivo."),
		))

	# ── ART. 10/9 — STRAORDINARI ──────────────────────────────────
	n_straord = art_n(10)
	straord_items = []
	if is_pt:
		straord_items += [p(
			f"<b>Lavoro supplementare</b> (part-time): ore oltre {ore_sett} e fino a 40 ore "
			f"settimanali, maggiorazione <b>15%</b> (art. 6, co. 2, D.Lgs. 81/2015).")]
	straord_items += [
		p(f"<b>Lavoro straordinario</b> (oltre 40 ore/settimana) — maggiorazioni sulla quota "
		  f"oraria (artt. 81 ss. CCNL FIPE, D.Lgs. 66/2003):"),
		*ul(
			f"Straordinario diurno feriale: +{proposta.straordinario_diurno_maggiorazione}%",
			f"Straordinario notturno (00:00–06:00): +{proposta.straordinario_notturno_maggiorazione}%",
			f"Straordinario festivo: +{proposta.straordinario_festivo_maggiorazione}%",
		),
		p("<b>Maggiorazioni orari speciali</b> (art. 83 CCNL FIPE):"),
		*ul(
			"Lavoro domenicale: +15% sulla retribuzione oraria;",
			"Lavoro festivo infrasettimanale: +20%;",
			"Lavoro notturno ordinario (21:00–06:00): +20%;",
			"Lavoro festivo notturno: cumulo maggiorazioni (art. 83 CCNL).",
		),
		p("Limite straordinario: 2 ore/giorno, 8 ore/settimana. Il ricorso richiede "
		  "accordo preventivo o comprovate esigenze produttive."),
	]
	story.append(articolo(n_straord, "Lavoro straordinario, supplementare e maggiorazioni",
						  *straord_items))

	# ── ART. 11/10 — MALATTIA ─────────────────────────────────────
	story.append(articolo(art_n(11), "Malattia, infortuni e tutele previdenziali",
		p("<b>Malattia</b> (art. 101 CCNL FIPE): conservazione del posto 6 mesi (1° anno), "
		  "9 mesi (2°–5° anno), 12 mesi (oltre 5° anno). Integrazione salariale aziendale + "
		  "indennità INPS al 100% nei giorni di carenza (primi 3 gg) e per i periodi successivi."),
		p("<b>Infortuni</b> (art. 102 CCNL FIPE): copertura INAIL obbligatoria; conservazione "
		  "del posto per il periodo di comporto normativo."),
		p("<b>Contribuzione previdenziale</b>: versamenti INPS e INAIL nei termini di legge "
		  "(L. 218/1952, D.Lgs. 314/1997, L. 335/1995)."),
	))

	# ── ART. 12/11 — DISCIPLINARE ─────────────────────────────────
	story.append(articolo(art_n(12), "Obblighi del lavoratore e norme disciplinari",
		p("Il/La Lavoratore/Lavoratrice osserva diligentemente le mansioni affidategli/le e "
		  "mantiene il segreto aziendale (artt. 2104–2105 c.c.)."),
		p("Infrazioni disciplinari: artt. 116 ss. CCNL FIPE — Imprese minori e Codice "
		  "disciplinare aziendale ex art. 7 L. 300/1970 (Statuto dei Lavoratori)."),
		p("Preavviso di licenziamento: art. 126 CCNL FIPE, differenziato per livello e anzianità."),
	))

	# ── ART. 13/12 — PRIVACY ──────────────────────────────────────
	story.append(articolo(art_n(13), "Trattamento dei dati personali",
		p("Dati trattati dal Datore di Lavoro (Titolare) ai sensi del Reg. UE 2016/679 (GDPR) "
		  "e del D.Lgs. 196/2003 mod. D.Lgs. 101/2018, per la gestione del rapporto di lavoro "
		  "e gli adempimenti previdenziali, fiscali e assicurativi. Informativa art. 13 GDPR "
		  "consegnata contestualmente alla presente proposta."),
	))

	# ── ART. 14/13 — SICUREZZA ────────────────────────────────────
	story.append(articolo(art_n(14), "Sicurezza e salute sul luogo di lavoro",
		p("Il Datore di Lavoro adempie agli obblighi del D.Lgs. 81/2008 (TU Sicurezza): "
		  "valutazione rischi, formazione, sorveglianza sanitaria, DPI."),
		p("Il/La Lavoratore/Lavoratrice partecipa ai programmi di formazione e rispetta le "
		  "disposizioni aziendali di sicurezza (art. 20 D.Lgs. 81/2008)."),
	))

	# ── CLAUSOLA FINALE ───────────────────────────────────────────
	story.append(Spacer(1, 4*mm))
	clausola_box = Table(
		[[p(
			f"La presente proposta, redatta in doppio esemplare, costituisce offerta vincolante "
			f"ai sensi degli artt. 1321 ss. c.c. La proposta si intende accettata con restituzione "
			f"di copia controfirmata entro il <b>{proposta.data_inizio_rapporto.strftime('%d/%m/%Y')}</b>. "
			f"Decorso tale termine senza accettazione scritta, la presente si intende ritirata.",
			s_clausola)]],
		colWidths=[col_w],
		style=TableStyle([
			('BOX',         (0, 0), (-1, -1), 0.5, C_BORDER),
			('BACKGROUND',  (0, 0), (-1, -1), HexColor('#f0f5fb')),
			('LEFTPADDING', (0, 0), (-1, -1), 5),
			('RIGHTPADDING',(0, 0), (-1, -1), 5),
			('TOPPADDING',  (0, 0), (-1, -1), 5),
			('BOTTOMPADDING',(0,0), (-1, -1), 5),
		]),
	)
	story.append(KeepTogether([clausola_box]))

	# ── FIRME ─────────────────────────────────────────────────────
	story.append(Spacer(1, 8*mm))
	firma_line = '_' * 38
	firme = Table(
		[[
			Table([[p('Per il Datore di Lavoro', s_firma_label)],
				   [HRFlowable(width=(col_w/2 - 6*mm), thickness=0.5, color=colors.black)],
				   [p(azienda.nome, s_firma_sub)],
				   [p('Luogo e data: ________________________', s_firma_sub)]],
				  colWidths=[col_w/2 - 5*mm]),
			Table([[p('Il/La Lavoratore/Lavoratrice', s_firma_label)],
				   [HRFlowable(width=(col_w/2 - 6*mm), thickness=0.5, color=colors.black)],
				   [p(f"{proposta.dipendente.nome} {proposta.dipendente.cognome}", s_firma_sub)],
				   [p('Luogo e data: ________________________', s_firma_sub)]],
				  colWidths=[col_w/2 - 5*mm]),
		]],
		colWidths=[col_w/2, col_w/2],
		style=TableStyle([
			('VALIGN', (0,0), (-1,-1), 'TOP'),
			('LEFTPADDING', (0,0), (-1,-1), 0),
			('RIGHTPADDING',(0,0), (-1,-1), 0),
			('TOPPADDING',  (0,0), (-1,-1), 0),
			('BOTTOMPADDING',(0,0),(-1,-1), 0),
		]),
	)
	story.append(KeepTogether([firme]))

	# Dichiarazioni
	story.append(Spacer(1, 5*mm))
	story.append(HRFlowable(width=col_w, thickness=0.3, color=C_BORDER, spaceAfter=2*mm))
	story.append(KeepTogether([
		p("Il/La sottoscritto/a dichiara di aver ricevuto e preso visione dell'informativa "
		  "sul trattamento dei dati personali ai sensi dell'art. 13 Reg. UE 2016/679 (GDPR).",
		  s_dichiarazione),
		p("Il/La sottoscritto/a dichiara di aver preso visione delle disposizioni del CCNL "
		  "applicato e del codice disciplinare aziendale. Il testo integrale del CCNL FIPE è "
		  "liberamente consultabile su <b>www.fipe.it</b> e nella banca dati CCNL del CNEL "
		  "(<b>www.cnel.it/Contratti-Collettivi</b>), senza necessità di copia cartacea.",
		  s_dichiarazione),
		Spacer(1, 4*mm),
		p(f"{firma_line}   ___/___/______", s_dichiarazione),
		Spacer(1, 1*mm),
		p("<i>Firma specifica ai sensi degli artt. 1341–1342 c.c. per le clausole di cui agli "
		  f"artt. 4 (orario), {art_n(9) if has_scatti else 8} (ferie), "
		  f"{art_n(10)} (straordinari e maggiorazioni) della presente proposta.</i>",
		  s_small),
	]))

	# ── Build ─────────────────────────────────────────────────────
	doc.build(story, onFirstPage=draw_header, onLaterPages=draw_header,
			  canvasmaker=_NumberedCanvas)
	buffer.seek(0)
	return buffer


@login_required
@xframe_options_sameorigin
def proposta_pdf(request, proposta_id):
	proposta = _get_proposta_con_permesso(request, proposta_id)
	if not proposta:
		return HttpResponseForbidden("Accesso negato")
	next_raw = (request.GET.get('next') or '').strip()
	if (request.GET.get('ui') == '1' or next_raw) and request.GET.get('embed') != '1':
		next_safe = sanitize_internal_next(request, next_raw)
		embed_src = request.build_absolute_uri(
			reverse('proposta_pdf', args=[proposta_id]) + '?embed=1'
		)
		return render(
			request,
			'common/file_viewer_frame.html',
			{
				'titolo': f'Proposta {proposta.numero_proposta}',
				'embed_src': embed_src,
				'next_url': next_safe,
			},
		)
	extra = _proposta_context_extra(proposta)
	buffer = _genera_proposta_pdf(proposta, extra)
	response = HttpResponse(buffer.getvalue(), content_type='application/pdf')
	response['Content-Disposition'] = f'inline; filename="proposta_{proposta.numero_proposta}.pdf"'
	return response


@login_required
def proposta_stampa(request, proposta_id):
	proposta = _get_proposta_con_permesso(request, proposta_id)
	if not proposta:
		return HttpResponseForbidden("Accesso negato")
	show_service_data = request.user.is_superuser or (request.user.has_ruolo('admin') or request.user.has_ruolo('hr'))
	ctx = {
		'proposta': proposta,
		'show_service_data': show_service_data,
	}
	ctx.update(_proposta_context_extra(proposta))
	return render(request, 'rapporto_di_lavoro/stampa_proposta.html', ctx)


def _genera_contratto_pdf_bytes(contratto):
	buffer = BytesIO()
	pdf = canvas.Canvas(buffer, pagesize=A4)
	height = A4[1]
	y = height - 40

	def write_line(text, step=16):
		nonlocal y
		if y < 60:
			pdf.showPage()
			y = height - 40
		pdf.drawString(40, y, str(text)[:140])
		y -= step

	pdf.setTitle(f"Contratto {contratto.numero_contratto}")
	pdf.setFont("Helvetica-Bold", 14)
	write_line("CONTRATTO INDIVIDUALE DI LAVORO")
	pdf.setFont("Helvetica", 11)
	write_line("Testo in forma professionale secondo prassi dei consulenti del lavoro.")
	write_line(f"Numero contratto: {contratto.numero_contratto}")
	write_line(f"Azienda: {contratto.azienda.nome}")
	write_line(f"Dipendente: {contratto.dipendente.nome} {contratto.dipendente.cognome}")
	write_line(f"Tipo contratto: {contratto.tipo_contratto.nome}")
	write_line(f"Data sottoscrizione: {contratto.data_sottoscrizione or '-'}")
	write_line(f"Decorrenza rapporto: {contratto.data_inizio_rapporto} -> {contratto.data_fine_rapporto or '-'}")
	write_line(f"Mansione/Qualifica/Livello: {contratto.posizione} / {contratto.qualifica} / {contratto.livello_ccnl}")
	write_line(f"Lordo mensile: {contratto.stipendio_lordo_mensile} EUR")
	write_line(f"Paga base/Contingenza/EDR: {contratto.paga_base_mensile} / {contratto.contingenza_mensile} / {contratto.edr_mensile} EUR")
	write_line(
		f"Maggiorazioni straordinario (diurno/notturno/festivo): {contratto.ore_straordinario_diurno_maggiorazione}% / "
		f"{contratto.ore_straordinario_notturno_maggiorazione}% / {contratto.ore_straordinario_festivo_maggiorazione}%"
	)
	write_line(f"Riposi compensativi: {contratto.riposi_compensativi_regola or '-'}")
	write_line(f"Scatti: ogni {contratto.scatto_periodicita_mesi} mesi, importo {contratto.scatto_importo} EUR, max {contratto.numero_scatti_massimi}")

	write_line("Clausole contrattuali essenziali:")
	write_line("1) Assunzione e mansioni ex art. 2103 c.c.", step=14)
	write_line("2) Inquadramento, livello e disciplina CCNL Turismo Confcommercio.", step=14)
	write_line("3) Retribuzione e competenze accessorie (contingenza, EDR, indennita).", step=14)
	write_line("4) Orario di lavoro e lavoro straordinario (D.Lgs. 66/2003).", step=14)
	write_line("5) Riposi, ferie, permessi e trattamenti indiretti/differiti.", step=14)
	write_line("6) Sicurezza sul lavoro (D.Lgs. 81/2008) e obblighi delle parti.", step=14)
	write_line("7) Trattamento dati personali e riservatezza.", step=14)
	write_line("8) Recesso, preavviso e rinvio a legge/CCNL vigente.", step=14)
	pdf.showPage()
	pdf.save()
	return buffer.getvalue()


def _sync_file_contratto_pdf_da_documento(contratto, documento, nome_suggerito: str) -> None:
	"""Se il rapporto non ha ancora ``file_contratto_pdf``, copia il file dal documento archiviato."""
	from django.core.files.base import ContentFile

	if not contratto or not documento or not documento.file:
		return
	if contratto.file_contratto_pdf and contratto.file_contratto_pdf.name:
		return
	try:
		documento.file.open('rb')
		try:
			data = documento.file.read()
		finally:
			documento.file.close()
		contratto.file_contratto_pdf.save(nome_suggerito, ContentFile(data), save=True)
	except Exception:
		pass


def _allega_contratto_definitivo_documento(contratto, utente):
	"""Salva il PDF del contratto definitivo su RapportoDiLavoro e nell'area Documenti del dipendente."""
	from django.core.files.base import ContentFile
	from documenti.models import Documento

	if not contratto or not contratto.dipendente_id:
		return None

	# Evita duplicati per lo stesso contratto
	descr = f'Contratto definitivo {contratto.numero_contratto}'
	nome_file = f"contratto_definitivo_{contratto.numero_contratto}.pdf"
	esistente = Documento.objects.filter(
		dipendente=contratto.dipendente,
		tipo='contratto',
		descrizione=descr,
	).first()
	if esistente:
		_sync_file_contratto_pdf_da_documento(contratto, esistente, nome_file)
		return esistente

	pdf_bytes = _genera_contratto_pdf_bytes(contratto)

	from django.conf import settings as dj_settings

	from documenti.upload_paths import ensure_documenti_media_subdirs

	ensure_documenti_media_subdirs()
	try:
		# 1) Copia sul record contratto (allineato a firma cartacea con upload: stesso campo ``file_contratto_pdf``)
		if not (contratto.file_contratto_pdf and contratto.file_contratto_pdf.name):
			contratto.file_contratto_pdf.save(nome_file, ContentFile(pdf_bytes), save=True)

		# 2) Archivio documenti dipendente
		return Documento.objects.create(
			azienda=contratto.azienda,
			dipendente=contratto.dipendente,
			tipo='contratto',
			descrizione=descr,
			file=ContentFile(pdf_bytes, name=nome_file),
			caricato_da=utente,
			caricato_dal_dipendente=False,
			visibile_al_dipendente=True,
		)
	except PermissionError as exc:
		media_root = getattr(dj_settings, 'MEDIA_ROOT', '')
		raise ValueError(
			'Impossibile salvare il PDF del contratto in area documenti: permessi insufficienti '
			f'sulla cartella media ({media_root}). Verificare che esista la sottocartella '
			'«documenti/contratti» e che l’utente del processo web abbia permesso di scrittura '
			'(es. chown/chmod su MEDIA_ROOT).'
		) from exc


def _allega_contratto_firmato_cartaceo_documento(contratto, utente, uploaded_file):
	"""Salva il PDF firmato cartaceo nell'area Documenti del dipendente."""
	from documenti.models import Documento

	if not contratto or not contratto.dipendente_id or uploaded_file is None:
		return None

	nome = str(getattr(uploaded_file, 'name', '') or '')
	if not nome.lower().endswith('.pdf'):
		raise ValueError('Il file firmato deve essere in formato PDF.')

	descr = f'Contratto firmato cartaceo {contratto.numero_contratto}'
	esistente = Documento.objects.filter(
		dipendente=contratto.dipendente,
		tipo='contratto',
		descrizione=descr,
	).first()
	if esistente:
		return esistente

	from django.conf import settings as dj_settings

	from documenti.upload_paths import ensure_documenti_media_subdirs

	ensure_documenti_media_subdirs()
	try:
		return Documento.objects.create(
			azienda=contratto.azienda,
			dipendente=contratto.dipendente,
			tipo='contratto',
			descrizione=descr,
			file=uploaded_file,
			caricato_da=utente,
			caricato_dal_dipendente=False,
			visibile_al_dipendente=True,
		)
	except PermissionError as exc:
		media_root = getattr(dj_settings, 'MEDIA_ROOT', '')
		raise ValueError(
			'Impossibile salvare il PDF firmato in area documenti: permessi insufficienti '
			f'sulla cartella media ({media_root}). Verificare permessi su MEDIA_ROOT e la cartella '
			'«documenti/contratti».'
		) from exc


def _promuovi_dipendente_da_proposta(proposta):
	"""Promuove candidato a dipendente attivo e aggiorna anagrafica minima."""
	dip = proposta.dipendente
	utente_candidato = getattr(dip, 'utente', None)
	if utente_candidato and utente_candidato.has_ruolo('candidato'):
		utente_candidato.azienda = proposta.azienda
		utente_candidato.save(update_fields=['azienda'])
		from accounts.models import Ruolo as _Ruolo
		_r, _ = _Ruolo.objects.get_or_create(codice='dipendente', defaults={'nome': 'Dipendente'})
		utente_candidato.ruoli.add(_r)
	update_dip = []
	if dip.stato == 'candidato':
		dip.stato = 'attivo'
		update_dip.append('stato')
	if not dip.data_assunzione:
		dip.data_assunzione = proposta.data_inizio_rapporto
		update_dip.append('data_assunzione')
	if update_dip:
		dip.save(update_fields=update_dip)


def _resolve_fieldfile_pdf_contratto_archiviato(contratto):
	"""
	File PDF da servire al dipendente: stesso ordine usato in area Documenti dopo firma admin.
	1) upload su RapportoDiLavoro.file_contratto_pdf
	2) Documento «Contratto firmato cartaceo …» (scanner)
	3) Documento «Contratto definitivo …» (copia archiviata da _allega_contratto_definitivo_documento)
	Se nessuno è disponibile, il caller genera da ReportLab.
	"""
	from documenti.models import Documento

	if contratto.file_contratto_pdf and contratto.file_contratto_pdf.name:
		return contratto.file_contratto_pdf

	num = contratto.numero_contratto
	for descr in (
		f'Contratto firmato cartaceo {num}',
		f'Contratto definitivo {num}',
	):
		doc = (
			Documento.objects.filter(
				dipendente_id=contratto.dipendente_id,
				tipo='contratto',
				descrizione=descr,
				visibile_al_dipendente=True,
			)
			.order_by('-data_caricamento')
			.first()
		)
		if doc and doc.file and doc.file.name:
			return doc.file
	return None


@login_required
@xframe_options_sameorigin
def contratto_pdf(request, contratto_id):
	"""PDF contratto: con ?ui=1 apre il viewer; con ?embed=1 serve il bytes per l'iframe (serve SAMEORIGIN, non DENY)."""
	contratto = _get_contratto_con_permesso(request, contratto_id)
	if not contratto:
		return HttpResponseForbidden("Accesso negato")
	next_raw = (request.GET.get('next') or '').strip()
	if (request.GET.get('ui') == '1' or next_raw) and request.GET.get('embed') != '1':
		next_safe = sanitize_internal_next(request, next_raw)
		embed_src = request.build_absolute_uri(
			reverse('contratto_pdf', args=[contratto_id]) + '?embed=1'
		)
		return render(
			request,
			'common/file_viewer_frame.html',
			{
				'titolo': f'Contratto {contratto.numero_contratto}',
				'embed_src': embed_src,
				'next_url': next_safe,
			},
		)
	ff = _resolve_fieldfile_pdf_contratto_archiviato(contratto)
	if ff is not None:
		try:
			return FileResponse(
				ff.open('rb'),
				as_attachment=False,
				filename=os.path.basename(ff.name) or f'contratto_{contratto.numero_contratto}.pdf',
			)
		except FileNotFoundError:
			pass
	pdf_bytes = _genera_contratto_pdf_bytes(contratto)
	response = HttpResponse(pdf_bytes, content_type='application/pdf')
	response['Content-Disposition'] = f'inline; filename="contratto_{contratto.numero_contratto}.pdf"'
	return response


@login_required
def proposta_mansionario_file(request, proposta_id):
	"""Serve il PDF mansionario della proposta con anteprima (ui/next) come gli altri PDF."""
	proposta = _get_proposta_con_permesso(request, proposta_id)
	if not proposta:
		return HttpResponseForbidden('Accesso negato')
	if not proposta.mansionario_file:
		raise Http404('Mansionario non presente.')
	next_raw = (request.GET.get('next') or '').strip()
	if (request.GET.get('ui') == '1' or next_raw) and request.GET.get('embed') != '1':
		next_safe = sanitize_internal_next(request, next_raw)
		embed_src = request.build_absolute_uri(
			reverse('proposta_mansionario_file', args=[proposta_id]) + '?embed=1'
		)
		return render(
			request,
			'common/file_viewer_frame.html',
			{
				'titolo': f'Mansionario — {proposta.numero_proposta}',
				'embed_src': embed_src,
				'next_url': next_safe,
			},
		)
	try:
		return FileResponse(
			proposta.mansionario_file.open('rb'),
			as_attachment=False,
			filename=os.path.basename(proposta.mansionario_file.name),
		)
	except FileNotFoundError:
		raise Http404('File non trovato.')


@login_required
def addendum_allegato_file(request, addendum_id):
	"""Serve l'allegato PDF di un addendum (permessi come scheda contratto)."""
	add = get_object_or_404(AddendumContrattuale.objects.select_related('rapporto'), pk=addendum_id)
	contratto = _get_contratto_con_permesso(request, add.rapporto_id)
	if not contratto:
		return HttpResponseForbidden('Accesso negato')
	if not add.file_allegato:
		raise Http404('Allegato non presente.')
	next_raw = (request.GET.get('next') or '').strip()
	if (request.GET.get('ui') == '1' or next_raw) and request.GET.get('embed') != '1':
		next_safe = sanitize_internal_next(request, next_raw)
		embed_src = request.build_absolute_uri(
			reverse('addendum_allegato_file', args=[addendum_id]) + '?embed=1'
		)
		return render(
			request,
			'common/file_viewer_frame.html',
			{
				'titolo': f'Allegato addendum — {add.rapporto.numero_contratto}',
				'embed_src': embed_src,
				'next_url': next_safe,
			},
		)
	try:
		return FileResponse(
			add.file_allegato.open('rb'),
			as_attachment=False,
			filename=os.path.basename(add.file_allegato.name),
		)
	except FileNotFoundError:
		raise Http404('File non trovato.')


@login_required
def contratto_stampa(request, contratto_id):
	from decimal import Decimal, ROUND_HALF_UP
	contratto = _get_contratto_con_permesso(request, contratto_id)
	if not contratto:
		return HttpResponseForbidden("Accesso negato")
	lordo = contratto.stipendio_lordo_mensile or Decimal('0')
	Q2 = Decimal('0.01')
	rat13 = (lordo / 12).quantize(Q2, rounding=ROUND_HALF_UP) if getattr(contratto, 'tredicesima', True) else Decimal('0')
	rat14 = (lordo / 12).quantize(Q2, rounding=ROUND_HALF_UP) if getattr(contratto, 'quattordicesima', False) else Decimal('0')
	ctx = {
		'contratto': contratto,
		'rat13_mensile': rat13,
		'rat14_mensile': rat14,
		'lordo_mensile_totale': lordo + rat13 + rat14,
	}
	return render(request, 'rapporto_di_lavoro/stampa_contratto.html', ctx)


@login_required
@user_passes_test(lambda u: u.is_authenticated and (u.has_ruolo('candidato') or u.has_ruolo('dipendente')))
def firma_proposta_candidato(request, proposta_id):
	"""
	Il candidato firma digitalmente la proposta (checkbox + timestamp + IP).
	Transizione: inviata_candidato → firmata_candidato.
	"""
	proposta = get_object_or_404(
		PropostaAssunzione,
		id=proposta_id,
		dipendente__utente=request.user,
		stato__in=PropostaAssunzione.stati_equivalenti('inviata_candidato'),
	)

	if request.method == 'POST':
		if not request.POST.get('accetto'):
			messages.error(request, 'Devi spuntare la casella di accettazione per procedere.')
			return redirect('firma_proposta_candidato', proposta_id=proposta_id)

		firma_ts = timezone.now()
		ip = (
			request.META.get('HTTP_X_FORWARDED_FOR', '').split(',')[0].strip()
			or request.META.get('REMOTE_ADDR', '')
		)
		proposta.accettata_dipendente = True
		proposta.data_accettazione_dipendente = firma_ts
		proposta.data_firma_candidato = firma_ts
		proposta.luogo_firma_candidato = 'Palermo'
		proposta.ip_firma_candidato = ip[:45]
		proposta.note_dipendente = request.POST.get('note_dipendente', '')
		proposta.stato = 'firmata_candidato'
		proposta.modificato_da = request.user
		proposta.save(update_fields=[
			'accettata_dipendente', 'data_accettazione_dipendente',
			'data_firma_candidato', 'luogo_firma_candidato', 'ip_firma_candidato',
			'note_dipendente', 'stato', 'modificato_da', 'data_modifica',
		])
		logger.info(
			'[FIRMA_CANDIDATO] Proposta %s firmata da %s (IP: %s)',
			proposta.numero_proposta, request.user.username, ip,
		)
		EventoStorico.objects.create(
			dipendente=proposta.dipendente,
			azienda=proposta.azienda,
			tipo='documento',
			data_evento=proposta.data_firma_candidato,
			descrizione=(
				f'Proposta {proposta.numero_proposta} firmata dal candidato '
				f'{proposta.dipendente} — {proposta.luogo_firma_candidato_effettivo}, '
				f'{proposta.data_firma_candidato.strftime("%d/%m/%Y %H:%M")} '
				f'(IP: {ip})'
			),
		)
		messages.success(request, 'Proposta firmata con successo! L\'azienda procederà con la firma definitiva.')
		return redirect('candidato_dashboard')

	ctx = {'proposta': proposta}
	ctx.update(_proposta_context_extra(proposta))
	return render(request, 'candidato/firma_proposta.html', ctx)


@login_required
@user_passes_test(lambda u: u.is_authenticated and (u.has_ruolo('candidato') or u.has_ruolo('dipendente')))
def accetta_proposta_dipendente(request, proposta_id):
	"""Alias legacy: deprecato, mantenuto per compatibilità URL storici."""
	logger.warning(
		"[DEPRECATION] URL legacy accetta_proposta_dipendente usata da %s per proposta %s",
		request.user.username,
		proposta_id,
	)
	return firma_proposta_candidato(request, proposta_id)


@login_required
@user_passes_test(lambda u: u.is_authenticated and (u.has_ruolo('candidato') or u.has_ruolo('dipendente')))
def rifiuta_proposta_dipendente(request, proposta_id):
	proposta = get_object_or_404(PropostaAssunzione, id=proposta_id, dipendente__utente=request.user)
	if not proposta.is_inviata_al_candidato():
		messages.error(request, 'Non puoi rifiutare questa proposta nello stato attuale.')
		return redirect('dettaglio_proposta', proposta_id=proposta.id)
	if request.method == 'POST':
		proposta.accettata_dipendente = False
		proposta.data_accettazione_dipendente = timezone.now()
		proposta.note_dipendente = request.POST.get('note_dipendente', '')
		proposta.stato = 'rifiutata_candidato'
		proposta.modificato_da = request.user
		proposta.save(update_fields=[
			'accettata_dipendente', 'data_accettazione_dipendente',
			'note_dipendente', 'stato', 'modificato_da', 'data_modifica',
		])
		messages.info(request, 'Proposta rifiutata.')
	return redirect('dettaglio_proposta', proposta_id=proposta.id)


@login_required
@user_passes_test(_is_admin_like)
def approva_proposta_admin(request, proposta_id):
	"""Alias legacy: deprecato, mantenuto per compatibilità URL storici."""
	logger.warning(
		"[DEPRECATION] URL legacy approva_proposta_admin usata da %s per proposta %s",
		request.user.username,
		proposta_id,
	)
	return firma_definitiva_admin(request, proposta_id)


@login_required
@user_passes_test(_is_admin_like)
def firma_definitiva_admin(request, proposta_id):
	"""
	Firma definitiva del datore: crea il contratto, promuove il candidato,
	imposta stato contratto_attivo.
	Transizione: firmata_candidato → contratto_attivo.
	"""
	proposta = get_object_or_404(PropostaAssunzione, id=proposta_id)
	azienda_operativa = get_azienda_operativa(request.user, request.session) if request.user.has_ruolo('admin') else getattr(request.user, 'azienda', None)
	if azienda_operativa and proposta.azienda_id != azienda_operativa.id:
		return HttpResponseForbidden("Accesso negato")

	if not proposta.is_firmata_da_candidato():
		messages.error(request, f'La proposta deve essere in stato "Firmata dal candidato" (stato attuale: {proposta.get_stato_display()}).')
		return redirect('dettaglio_proposta', proposta_id=proposta.id)

	if request.method == 'POST':
		try:
			contratto = proposta.firma_definitiva_admin(request.user)
			doc_contratto = _allega_contratto_definitivo_documento(contratto, request.user)
			messages.success(
				request,
				f'Contratto {contratto.numero_contratto} emesso. '
				f'{proposta.dipendente} è ora un dipendente attivo.'
			)
			logger.info(
				'[FIRMA_DEFINITIVA_ADMIN] %s → contratto_attivo, contratto %s, da %s',
				proposta.numero_proposta, contratto.numero_contratto, request.user.username,
			)
			EventoStorico.objects.create(
				dipendente=proposta.dipendente,
				azienda=proposta.azienda,
				tipo='assunzione',
				data_evento=proposta.data_firma_datore,
				descrizione=(
					f'Contratto {contratto.numero_contratto} firmato dal datore di lavoro '
					f'({request.user}) — {proposta.luogo_firma_datore_effettivo}, '
					f'{proposta.data_firma_datore.strftime("%d/%m/%Y %H:%M")}. '
					f'Proposta origine: {proposta.numero_proposta}.'
				),
			)
			if doc_contratto:
				EventoStorico.objects.create(
					dipendente=proposta.dipendente,
					azienda=proposta.azienda,
					tipo='documento',
					data_evento=timezone.now(),
					descrizione=(
						f'Documento contratto definitivo {contratto.numero_contratto} '
						f'reso disponibile al dipendente in area Documenti.'
					),
					documento=doc_contratto,
				)
		except (ValueError, PermissionError) as exc:
			messages.error(request, str(exc))

	return redirect('dettaglio_proposta', proposta_id=proposta.id)


@login_required
@user_passes_test(_is_admin_like)
def invia_proposta_al_dipendente(request, proposta_id):
	"""Transizione bozza → inviata_candidato."""
	proposta = get_object_or_404(PropostaAssunzione, id=proposta_id, stato='bozza')
	azienda_operativa = get_azienda_operativa(request.user, request.session) if request.user.has_ruolo('admin') else getattr(request.user, 'azienda', None)
	if azienda_operativa and proposta.azienda_id != azienda_operativa.id:
		return HttpResponseForbidden("Accesso negato")

	if request.method == 'POST':
		proposta.stato = 'inviata_candidato'
		proposta.modificato_da = request.user
		proposta.save(update_fields=['stato', 'modificato_da', 'data_modifica'])
		messages.success(request, f'Proposta {proposta.numero_proposta} inviata al candidato.')
		logger.info('[INVIA_PROPOSTA] %s inviata da %s', proposta.numero_proposta, request.user.username)
	return redirect('dettaglio_proposta', proposta_id=proposta.id)


@login_required
@user_passes_test(_is_admin_like)
def rifiuta_proposta_admin(request, proposta_id):
	proposta = get_object_or_404(PropostaAssunzione, id=proposta_id)
	azienda_operativa = get_azienda_operativa(request.user, request.session) if request.user.has_ruolo('admin') else request.user.azienda
	if azienda_operativa and proposta.azienda_id != azienda_operativa.id:
		return HttpResponseForbidden("Accesso negato")

	if request.method == 'POST':
		proposta.approvata_admin = False
		proposta.data_approvazione_admin = timezone.now()
		proposta.note_admin = request.POST.get('note_admin', '')
		proposta.stato = 'rifiutata_admin'
		proposta.modificato_da = request.user
		proposta.save(
			update_fields=[
				'approvata_admin',
				'data_approvazione_admin',
				'note_admin',
				'stato',
				'modificato_da',
				'data_modifica',
			]
		)
		messages.warning(request, 'Proposta rifiutata da amministrazione.')
	return redirect('dettaglio_proposta', proposta_id=proposta.id)


@login_required
@user_passes_test(_is_admin_like)
def converti_proposta_in_contratto(request, proposta_id):
	"""Legacy: reindirizza a firma_definitiva_admin."""
	logger.warning(
		"[DEPRECATION] URL legacy converti_proposta_in_contratto usata da %s per proposta %s",
		getattr(request.user, 'username', ''),
		proposta_id,
	)
	return firma_definitiva_admin(request, proposta_id)


@login_required
@user_passes_test(_is_admin_like)
def trasforma_proposta_in_contratto(request, proposta_id):
	"""
	Trasforma proposta in contratto con scelta metodo:
	- digitale: usa il flusso esistente (richiede proposta firmata dal candidato)
	- cartacea: consente conversione con firma cartacea + eventuale upload PDF scannerizzato.
	"""
	proposta = get_object_or_404(PropostaAssunzione, id=proposta_id)
	azienda_operativa = get_azienda_operativa(request.user, request.session) if request.user.has_ruolo('admin') else getattr(request.user, 'azienda', None)
	if azienda_operativa and proposta.azienda_id != azienda_operativa.id:
		return HttpResponseForbidden("Accesso negato")

	if proposta.contratto_generato_id:
		messages.info(request, 'La proposta è già stata trasformata in contratto.')
		return redirect('dettaglio_proposta', proposta_id=proposta.id)

	if request.method != 'POST':
		return redirect('dettaglio_proposta', proposta_id=proposta.id)

	metodo_firma = (request.POST.get('metodo_firma') or 'digitale').strip().lower()
	forza_trasformazione = (request.POST.get('forza_trasformazione') or '').strip() == '1'
	luogo_datore = (request.POST.get('luogo_firma_datore') or proposta.luogo_firma_datore or proposta.luogo_firma_datore_effettivo or '').strip() or 'Palermo'
	luogo_lavoratore = (request.POST.get('luogo_firma_lavoratore') or proposta.luogo_firma_candidato or proposta.luogo_firma_candidato_effettivo or '').strip() or luogo_datore
	file_firmato = request.FILES.get('contratto_firmato_pdf')
	file_firmato_name = ''
	file_firmato_bytes = None
	if file_firmato is not None:
		file_firmato_name = str(getattr(file_firmato, 'name', '') or '').strip()
		try:
			file_firmato_bytes = file_firmato.read()
		except Exception:
			file_firmato_bytes = None
	data_firma_datore_raw = (request.POST.get('data_firma_datore') or '').strip()
	data_firma_lavoratore_raw = (request.POST.get('data_firma_lavoratore') or '').strip()

	if metodo_firma == 'digitale':
		# Reindirizza al flusso canonico già validato per firma digitale.
		return firma_definitiva_admin(request, proposta_id)

	if metodo_firma != 'cartacea':
		messages.error(request, 'Metodo firma non valido.')
		return redirect('dettaglio_proposta', proposta_id=proposta.id)

	if proposta.stato_canonico not in ('inviata_candidato', 'firmata_candidato'):
		if proposta.stato_canonico == 'bozza' and metodo_firma == 'cartacea' and forza_trasformazione:
			pass
		else:
			messages.error(request, f'Stato non convertibile con firma cartacea: {proposta.get_stato_display()}.')
			return redirect('dettaglio_proposta', proposta_id=proposta.id)

	if proposta.stato_canonico == 'bozza' and not forza_trasformazione:
		messages.error(request, f'Stato non convertibile con firma cartacea: {proposta.get_stato_display()}.')
		return redirect('dettaglio_proposta', proposta_id=proposta.id)

	if file_firmato is not None and not file_firmato_name.lower().endswith('.pdf'):
		messages.error(request, 'Il contratto firmato deve essere caricato in formato PDF.')
		return redirect('dettaglio_proposta', proposta_id=proposta.id)
	if file_firmato is not None and not file_firmato_bytes:
		messages.error(request, 'File PDF firmato non leggibile. Ricarica il file e riprova.')
		return redirect('dettaglio_proposta', proposta_id=proposta.id)

	def _parse_dt_local_or_none(raw: str):
		if not raw:
			return None
		dt = parse_datetime(raw)
		if dt is None:
			return None
		if timezone.is_naive(dt):
			return timezone.make_aware(dt, timezone.get_current_timezone())
		return dt

	firma_datore_ts = _parse_dt_local_or_none(data_firma_datore_raw)
	firma_lavoratore_ts = _parse_dt_local_or_none(data_firma_lavoratore_raw)
	if firma_datore_ts is None:
		messages.error(request, 'Inserisci data e ora firma amministratore (contratto cartaceo).')
		return redirect('dettaglio_proposta', proposta_id=proposta.id)
	if firma_lavoratore_ts is None:
		messages.error(request, 'Inserisci data e ora firma dipendente (contratto cartaceo).')
		return redirect('dettaglio_proposta', proposta_id=proposta.id)

	try:
		with transaction.atomic():
			contratto = proposta._crea_rapporto_di_lavoro(
				request.user,
				stato_finale='sottoscritto',
				data_ora_sottoscrizione=firma_datore_ts,
				luogo_sottoscrizione=luogo_datore,
			)

			# Se disponibile il PDF scannerizzato, lo lega anche al record contratto.
			if file_firmato_bytes is not None:
				from django.core.files.base import ContentFile
				contratto.file_contratto_pdf = ContentFile(file_firmato_bytes, name=file_firmato_name)
				contratto.save(update_fields=['file_contratto_pdf'])

			if file_firmato_bytes is not None:
				from django.core.files.base import ContentFile
				doc_contratto = _allega_contratto_firmato_cartaceo_documento(
					contratto,
					request.user,
					ContentFile(file_firmato_bytes, name=file_firmato_name),
				)
			else:
				doc_contratto = _allega_contratto_definitivo_documento(contratto, request.user)

			proposta.accettata_dipendente = True
			proposta.data_accettazione_dipendente = proposta.data_accettazione_dipendente or firma_lavoratore_ts
			proposta.data_firma_candidato = proposta.data_firma_candidato or firma_lavoratore_ts
			proposta.luogo_firma_candidato = luogo_lavoratore
			proposta.data_firma_datore = firma_datore_ts
			proposta.luogo_firma_datore = luogo_datore
			proposta.contratto_generato = contratto
			proposta.stato = 'contratto_attivo'
			proposta.modificato_da = request.user
			proposta.save(update_fields=[
				'accettata_dipendente',
				'data_accettazione_dipendente',
				'data_firma_candidato',
				'luogo_firma_candidato',
				'data_firma_datore',
				'luogo_firma_datore',
				'contratto_generato',
				'stato',
				'modificato_da',
				'data_modifica',
			])

			_promuovi_dipendente_da_proposta(proposta)

			from accounts.contratto_utente_definitivo import (
				ribalta_utente_candidato_su_dipendente_se_contratto_definitivo,
			)

			ribalta_utente_candidato_su_dipendente_se_contratto_definitivo(
				proposta.dipendente,
				contratto,
				motivo="trasforma_proposta_in_contratto_cartaceo",
			)

			EventoStorico.objects.create(
				dipendente=proposta.dipendente,
				azienda=proposta.azienda,
				tipo='assunzione',
				data_evento=firma_datore_ts,
				descrizione=(
					f'Contratto {contratto.numero_contratto} trasformato da proposta '
					f'{proposta.numero_proposta} con firma cartacea '
					f'({luogo_datore}, {firma_datore_ts.strftime("%d/%m/%Y %H:%M")}) '
					f'e firma lavoratore ({luogo_lavoratore}, {firma_lavoratore_ts.strftime("%d/%m/%Y %H:%M")}).'
				),
			)
			if doc_contratto:
				EventoStorico.objects.create(
					dipendente=proposta.dipendente,
					azienda=proposta.azienda,
					tipo='documento',
					data_evento=timezone.now(),
					descrizione=(
						f'Contratto firmato ({contratto.numero_contratto}) acquisito in archivio documentale '
						f'({doc_contratto.descrizione}).'
					),
					documento=doc_contratto,
				)
	except ValueError as exc:
		messages.error(request, str(exc))
		return redirect('dettaglio_proposta', proposta_id=proposta.id)

	messages.success(
		request,
		f'Proposta {proposta.numero_proposta} trasformata in contratto {contratto.numero_contratto} '
		f'con firma cartacea.'
	)
	return redirect('dettaglio_proposta', proposta_id=proposta.id)


@login_required
@user_passes_test(_is_admin_only)
def dettaglio_calcolo_economico(request, parametro_id):
	"""Vista dettagliata del calcolo economico con tutti i dettagli INPS, IRPEF, TFR, ecc."""
	parametro = get_object_or_404(ParametroCCNLTurismo, id=parametro_id)
	calcolo = parametro.calcolo_completo()
	
	# Calcoli aggiuntivi per il template
	lordo = calcolo['lordo_mensile']
	netto = calcolo['netto']['netto']
	costo_mensile = calcolo['costo_azienda']['costo_totale_mensile']
	imponibile = calcolo['netto']['imponibile']
	
	# Calcoli percentuali e annui
	reddito_annuo = imponibile * 12
	netto_percentuale = (netto / lordo * 100) if lordo > 0 else 0
	costo_percentuale = (costo_mensile / lordo * 100) if lordo > 0 else 0
	incidenza_costo = costo_mensile - lordo
	incidenza_costo_perc = (incidenza_costo / lordo * 100) if lordo > 0 else 0
	trattenute = lordo - netto
	trattenute_perc = (trattenute / lordo * 100) if lordo > 0 else 0
	
	return render(request, 'rapporto_di_lavoro/dettaglio_calcolo_economico.html', {
		'parametro': parametro,
		'calcolo': calcolo,
		'reddito_annuo': reddito_annuo,
		'netto_percentuale': netto_percentuale,
		'costo_percentuale': costo_percentuale,
		'incidenza_costo': incidenza_costo,
		'incidenza_costo_perc': incidenza_costo_perc,
		'trattenute': trattenute,
		'trattenute_perc': trattenute_perc,
	})


@login_required
@user_passes_test(_is_admin_like)
def modifica_contratto(request, contratto_id):
	"""
	HR/Admin: modifica la bozza contratto (RapportoDiLavoro stato='proposta').
	Il form è pre-popolato con i dati della proposta di origine.
	Mostra i parametri CCNL di riferimento e una simulazione del netto mensile.
	"""
	contratto = get_object_or_404(RapportoDiLavoro, id=contratto_id, stato='proposta')
	azienda_operativa = get_azienda_operativa(request.user, request.session) if request.user.has_ruolo('admin') else getattr(request.user, 'azienda', None)
	if azienda_operativa and contratto.azienda_id != azienda_operativa.id:
		return HttpResponseForbidden("Accesso negato")

	# Proposta di origine (per riferimento CCNL e dati candidato)
	proposta = getattr(contratto, 'proposta_origine', None)
	parametro_ccnl = proposta.parametro_ccnl if proposta else None

	# Profilo candidato (per num_familiari_a_carico e regione_residenza)
	dip = contratto.dipendente
	utente_dip = getattr(dip, 'utente', None)
	profilo = getattr(utente_dip, 'profilo_candidato', None) if utente_dip else None
	num_familiari = int(getattr(profilo, 'num_familiari_a_carico', 0) or 0)
	regione = (getattr(profilo, 'regione_residenza', None) or 'Sicilia') if profilo else 'Sicilia'

	# Simulazione netto dal motore (solo in lettura, per preview)
	# Risolve parametro_ccnl dal contratto (via proposta origine o lookup livello)
	simulazione = None
	try:
		cp = parametro_ccnl  # dalla proposta_origine
		if not cp:
			livello = contratto.livello_ccnl or ''
			if livello:
				cp = ParametroCCNLTurismo.objects.filter(
					livello=livello, attivo=True,
					decorrenza_validita_da__lte=contratto.data_inizio_rapporto,
				).order_by('-decorrenza_validita_da').first()
		if cp:
			ccnl_fipe = CCNL.objects.filter(sigla='FIPE').first()
			divisore = str(round(float(cp.ore_mensili))) if cp.ore_mensili else '26'
			simulazione = calcola_busta_paga_mese(
				parametro_ccnl=cp,
				tipo_contratto=contratto.tipo_contratto,
				anno=timezone.localdate().year,
				mese=timezone.localdate().month,
				azienda=contratto.azienda,
				data_inizio_rapporto=contratto.data_inizio_rapporto,
				data_fine_rapporto=contratto.data_fine_rapporto,
				divisore_str=divisore,
				ccnl_obj=ccnl_fipe,
				num_familiari_a_carico=num_familiari,
				regione_residenza=regione,
				rateo_13_mensile_in_imponibile=bool(
					getattr(contratto, 'tredicesima_rateo_mensile_in_imponibile', False)
				),
				rateo_14_mensile_in_imponibile=bool(
					getattr(contratto, 'quattordicesima_rateo_mensile_in_imponibile', False)
				),
			)
	except Exception:
		pass

	if request.method == 'POST':
		form = RapportoDiLavoroForm(request.POST, instance=contratto)
		if form.is_valid():
			obj = form.save(commit=False)
			obj.modificato_da = request.user
			obj.save()
			messages.success(request, f'Contratto {contratto.numero_contratto} aggiornato.')
			logger.info(
				'[MODIFICA_CONTRATTO] %s modificato da %s',
				contratto.numero_contratto, request.user.username,
			)
			return redirect('modifica_contratto', contratto_id=contratto.id)
	else:
		form = RapportoDiLavoroForm(instance=contratto)

	ctx = {
		'contratto': contratto,
		'proposta': proposta,
		'parametro_ccnl': parametro_ccnl,
		'form': form,
		'simulazione': simulazione,
		'dip': dip,
		'profilo': profilo,
	}
	return render(request, 'rapporto_di_lavoro/modifica_contratto.html', ctx)


def _sincronizza_rapporto_da_addendum(rapporto, addendum, user):
	"""Aggiorna i campi del RapportoDiLavoro con i valori non null dell'addendum."""
	fupd = []
	if addendum.stipendio_lordo_mensile is not None:
		rapporto.stipendio_lordo_mensile = addendum.stipendio_lordo_mensile
		fupd.append('stipendio_lordo_mensile')
	if addendum.paga_base_mensile is not None:
		rapporto.paga_base_mensile = addendum.paga_base_mensile
		fupd.append('paga_base_mensile')
	if addendum.contingenza_mensile is not None:
		rapporto.contingenza_mensile = addendum.contingenza_mensile
		fupd.append('contingenza_mensile')
	if addendum.edr_mensile is not None:
		rapporto.edr_mensile = addendum.edr_mensile
		fupd.append('edr_mensile')
	if addendum.ore_settimanali is not None:
		rapporto.ore_settimanali = addendum.ore_settimanali
		fupd.append('ore_settimanali')
	if addendum.tipo_contratto_id:
		rapporto.tipo_contratto_id = addendum.tipo_contratto_id
		fupd.append('tipo_contratto_id')
	if addendum.data_fine_rapporto_aggiornata is not None:
		rapporto.data_fine_rapporto = addendum.data_fine_rapporto_aggiornata
		fupd.append('data_fine_rapporto')
	if (addendum.livello_ccnl or '').strip():
		rapporto.livello_ccnl = addendum.livello_ccnl.strip()
		fupd.append('livello_ccnl')
	if (addendum.qualifica or '').strip():
		rapporto.qualifica = addendum.qualifica.strip()
		fupd.append('qualifica')
	if not fupd:
		return
	rapporto.modificato_da = user
	fupd.extend(['modificato_da', 'data_modifica'])
	rapporto.save(update_fields=list(dict.fromkeys(fupd)))


@login_required
def dettaglio_contratto(request, contratto_id):
	"""Scheda contratto con storico addendum (lettura per dipendente/candidato; HR vede azioni)."""
	contratto = _get_contratto_con_permesso(request, contratto_id)
	if not contratto:
		return HttpResponseForbidden('Accesso negato')
	addenda = (
		AddendumContrattuale.objects.filter(rapporto=contratto)
		.select_related('tipo_contratto', 'parametro_ccnl', 'creato_da')
		.order_by('-data_decorrenza', '-data_creazione')
	)
	proposta = getattr(contratto, 'proposta_origine', None)
	puo_gestire_addendum = bool(
		request.user.is_superuser or request.user.has_ruolo('admin') or request.user.has_ruolo('hr')
	)
	sincronizzabile = contratto.stato in ('sottoscritto', 'sospeso')
	return render(
		request,
		'rapporto_di_lavoro/dettaglio_contratto.html',
		{
			'contratto': contratto,
			'proposta': proposta,
			'addenda': addenda,
			'puo_gestire_addendum': puo_gestire_addendum,
			'sincronizzabile': sincronizzabile,
		},
	)


@login_required
@user_passes_test(_is_admin_like)
def addendum_contratto_nuovo(request, contratto_id):
	"""Registra un addendum / variazione sul contratto definitivo."""
	contratto = _get_contratto_con_permesso(request, contratto_id)
	if not contratto:
		return HttpResponseForbidden('Accesso negato')
	if contratto.stato not in ('sottoscritto', 'sospeso', 'cessato'):
		messages.error(
			request,
			'Addendum disponibili solo per contratti gia definiti (sottoscritti, sospesi o cessati).',
		)
		return redirect('dettaglio_contratto', contratto_id=contratto.id)

	azienda_operativa = _get_azienda_operativa_per_utente(request.user, request.session)
	if not azienda_operativa or contratto.azienda_id != azienda_operativa.id:
		return HttpResponseForbidden('Accesso negato')

	sincronizzabile = contratto.stato in ('sottoscritto', 'sospeso')

	if request.method == 'POST':
		form = AddendumContrattualeForm(
			request.POST,
			request.FILES,
			azienda_operativa=azienda_operativa,
		)
		if form.is_valid():
			addendum = form.save(commit=False)
			addendum.rapporto = contratto
			addendum.creato_da = request.user
			addendum.save()
			applica = bool(form.cleaned_data.get('applica_valori_al_contratto')) and sincronizzabile
			if applica:
				_sincronizza_rapporto_da_addendum(contratto, addendum, request.user)
				messages.success(
					request,
					f'Addendum registrato e valori aggiornati sul contratto {contratto.numero_contratto}.',
				)
			else:
				messages.success(request, 'Addendum registrato nello storico.')
			logger.info(
				'[ADDENDUM] contratto=%s tipo=%s decorrenza=%s applica=%s utente=%s',
				contratto.numero_contratto,
				addendum.tipo,
				addendum.data_decorrenza,
				applica,
				request.user.username,
			)
			return redirect('dettaglio_contratto', contratto_id=contratto.id)
	else:
		initial = {}
		if contratto.data_fine_rapporto:
			initial['data_fine_rapporto_aggiornata'] = contratto.data_fine_rapporto
		form = AddendumContrattualeForm(
			azienda_operativa=azienda_operativa,
			initial=initial,
		)
		# Precompila riferimenti economici dal contratto (modificabili)
		form.fields['stipendio_lordo_mensile'].initial = contratto.stipendio_lordo_mensile
		form.fields['paga_base_mensile'].initial = contratto.paga_base_mensile
		form.fields['contingenza_mensile'].initial = contratto.contingenza_mensile
		form.fields['edr_mensile'].initial = contratto.edr_mensile
		form.fields['ore_settimanali'].initial = contratto.ore_settimanali
		form.fields['tipo_contratto'].initial = contratto.tipo_contratto_id
		form.fields['livello_ccnl'].initial = contratto.livello_ccnl
		form.fields['qualifica'].initial = contratto.qualifica
		form.fields['data_decorrenza'].initial = timezone.localdate()

	return render(
		request,
		'rapporto_di_lavoro/addendum_contratto_form.html',
		{
			'contratto': contratto,
			'form': form,
			'sincronizzabile': sincronizzabile,
		},
	)


def _puo_vedere_scadenze_contratti(user):
	if not user.is_authenticated:
		return False
	if user.is_superuser or user.has_ruolo('admin') or user.has_ruolo('hr'):
		return True
	return user.has_ruolo('consulente')


@login_required
@user_passes_test(_puo_vedere_scadenze_contratti)
def lista_contratti_scadenza(request):
	"""Elenco TD in scadenza (≤30 gg) e TD scaduti ancora aperti; guida rinnovo / comando cessazioni."""
	from .services_contratti import contratti_td_in_scadenza, contratti_td_scaduti_non_chiusi

	azienda_operativa = _get_azienda_per_contratti_scadenze(request.user, request.session)
	if not azienda_operativa:
		messages.error(request, 'Seleziona prima un\'azienda operativa.')
		return redirect('lista_aziende')
	oggi = timezone.localdate()
	prossimi_raw = contratti_td_in_scadenza(azienda_operativa, giorni=30, oggi=oggi)
	scaduti_raw = contratti_td_scaduti_non_chiusi(azienda_operativa, oggi=oggi)
	contratti_prossimi = [
		{
			'rapporto': r,
			'giorni_mancanti': (r.data_fine_rapporto - oggi).days if r.data_fine_rapporto else None,
		}
		for r in prossimi_raw
	]
	contratti_scaduti = [
		{
			'rapporto': r,
			'giorni_ritardo': (oggi - r.data_fine_rapporto).days if r.data_fine_rapporto else None,
		}
		for r in scaduti_raw
	]
	return render(
		request,
		'rapporto_di_lavoro/lista_contratti_scadenza.html',
		{
			'azienda_operativa': azienda_operativa,
			'oggi': oggi,
			'contratti_prossimi': contratti_prossimi,
			'contratti_scaduti': contratti_scaduti,
		},
	)
