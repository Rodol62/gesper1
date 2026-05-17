"""
Servizi dominio contratti: TD in scadenza, cessazioni automatiche, contesto posizione/storico.
"""
from __future__ import annotations

from datetime import timedelta

from django.db import transaction
from django.utils import timezone

from anagrafiche.models import Dipendente


def sync_dipendente_da_rapporto_vigente(dipendente_id: int, azienda_id: int | None = None) -> bool:
	"""
	Allinea `Dipendente.data_assunzione` e `data_cessazione` al **contratto sottoscritto/sospeso**
	più recente per `data_inizio_rapporto` (stessa azienda se indicata).

	- `data_assunzione` = `data_inizio_rapporto` del rapporto vigente.
	- `data_cessazione` = `data_fine_rapporto` se valorizzata (es. TD), altrimenti ``None`` (TI / aperta).

	Non aggiorna dipendenti ``cessati``. I rapporti in ``proposta`` non concorrono alla scelta.
	Chiamare dopo ogni salvataggio di :class:`~rapporto_di_lavoro.models.RapportoDiLavoro` rilevante
	(vedi segnale ``post_save``).
	"""
	from .models import RapportoDiLavoro

	dip = Dipendente.objects.filter(pk=dipendente_id).first()
	if not dip or dip.stato not in ('attivo', 'candidato'):
		return False

	qs = RapportoDiLavoro.objects.filter(
		dipendente_id=dipendente_id,
		stato__in=('sottoscritto', 'sospeso'),
	)
	if azienda_id is not None:
		qs = qs.filter(azienda_id=azienda_id)
	r = qs.order_by('-data_inizio_rapporto', '-id').first()
	if not r:
		return False

	updates = []
	if dip.data_assunzione != r.data_inizio_rapporto:
		dip.data_assunzione = r.data_inizio_rapporto
		updates.append('data_assunzione')
	new_cess = r.data_fine_rapporto
	if dip.data_cessazione != new_cess:
		dip.data_cessazione = new_cess
		updates.append('data_cessazione')
	if updates:
		dip.save(update_fields=updates)
	return True


def is_tempo_determinato(tipo_contratto) -> bool:
	"""True se il tipo contratto prevede termine (TD, stagionale, ecc.)."""
	if tipo_contratto is None:
		return False
	t = (getattr(tipo_contratto, 'tipo', None) or '').strip().lower()
	return t.startswith('det_') or t.startswith('stag_')


def is_contratto_a_scadenza_fine_rapporto(tipo_contratto) -> bool:
	"""
	True se il rapporto ha tipicamente una data di fine da monitorare in agenda scadenze.

	Oltre a :func:`is_tempo_determinato` (codici ``det_*`` / ``stag_*``), include
	``somministrazione`` e tipi **legacy** il cui slug non segue ancora la convenzione FIPE
	ma il **nome** del tipo indica chiaramente determinato/stagionale (escludendo
	«indeterminato» per evitare falsi positivi su sottostringhe).
	"""
	if tipo_contratto is None:
		return False
	if is_tempo_determinato(tipo_contratto):
		return True
	t = (getattr(tipo_contratto, 'tipo', None) or '').strip().lower()
	if t == 'somministrazione':
		return True
	nome_l = (getattr(tipo_contratto, 'nome', '') or '').lower()
	if not nome_l or 'indeterminato' in nome_l:
		return False
	if 'determinato' in nome_l or 'stagionale' in nome_l:
		return True
	return False


def contratti_td_in_scadenza(azienda, giorni: int = 30, oggi=None):
	"""
	Rapporti a termine ancora vigenti con data fine entro `giorni` da oggi (incluso oggi).
	"""
	from .models import RapportoDiLavoro

	oggi = oggi or timezone.localdate()
	limite = oggi + timedelta(days=int(giorni))
	qs = (
		RapportoDiLavoro.objects.filter(
			azienda=azienda,
			stato__in=('sottoscritto', 'sospeso'),
			data_fine_rapporto__isnull=False,
			data_fine_rapporto__gte=oggi,
			data_fine_rapporto__lte=limite,
		)
		.exclude(dipendente__stato='cessato')
		.select_related('dipendente', 'tipo_contratto')
		.order_by('data_fine_rapporto', 'dipendente__cognome')
	)
	return [r for r in qs if is_contratto_a_scadenza_fine_rapporto(r.tipo_contratto)]


def posizione_contrattuale_per_dipendente(dipendente):
	"""
	Contratti del dipendente con addendum (storico variazioni) per profilo admin/consulente/dipendente.
	Ritorna lista di dict: rapporto, addenda, giorni_a_scadenza (None se non TD o senza fine).
	"""
	from .models import RapportoDiLavoro

	oggi = timezone.localdate()
	rapporti = (
		RapportoDiLavoro.objects.filter(dipendente=dipendente)
		.select_related('tipo_contratto')
		.prefetch_related('addenda__tipo_contratto', 'addenda__creato_da')
		.order_by('-data_inizio_rapporto', '-id')
	)
	righe = []
	for r in rapporti:
		addenda = list(r.addenda.order_by('-data_decorrenza', '-data_creazione', '-id'))
		giorni = None
		if r.data_fine_rapporto and is_tempo_determinato(r.tipo_contratto):
			giorni = (r.data_fine_rapporto - oggi).days
		righe.append({'rapporto': r, 'addenda': addenda, 'giorni_a_scadenza': giorni})
	return righe


def applica_cessazioni_td_scadute(*, azienda=None, oggi=None, dry_run: bool = False) -> dict:
	"""
	Imposta stato `cessato` sui Rapporto TD la cui data fine è passata e non esiste altro rapporto attivo
	per lo stesso dipendente. Aggiorna anagrafica dipendente a `cessato` se non restano rapporti attivi.

	Ritorna {'rapporti_chiusi': int, 'dipendenti_cessati': int, 'ids_rapporti': [...]}.
	"""
	from .models import RapportoDiLavoro

	oggi = oggi or timezone.localdate()
	qs = RapportoDiLavoro.objects.filter(
		stato__in=('sottoscritto', 'sospeso'),
		data_fine_rapporto__isnull=False,
		data_fine_rapporto__lt=oggi,
	).select_related('dipendente', 'tipo_contratto')
	if azienda is not None:
		qs = qs.filter(azienda=azienda)

	rapporti_chiusi = 0
	dipendenti_cessati = 0
	ids = []

	def _altri_attivi(dip_id, escludi_id):
		return (
			RapportoDiLavoro.objects.filter(dipendente_id=dip_id, stato__in=('sottoscritto', 'sospeso', 'proposta'))
			.exclude(id=escludi_id)
			.exists()
		)

	for r in qs:
		if not is_tempo_determinato(r.tipo_contratto):
			continue
		if dry_run:
			rapporti_chiusi += 1
			ids.append(r.id)
			continue
		with transaction.atomic():
			r2 = RapportoDiLavoro.objects.select_for_update().filter(pk=r.pk).first()
			if not r2 or r2.stato not in ('sottoscritto', 'sospeso'):
				continue
			if not is_tempo_determinato(r2.tipo_contratto) or not r2.data_fine_rapporto or r2.data_fine_rapporto >= oggi:
				continue
			r2.stato = 'cessato'
			r2.save(update_fields=['stato', 'data_modifica'])
			rapporti_chiusi += 1
			ids.append(r2.id)
			dip = r2.dipendente
			if dip and not _altri_attivi(dip.id, r2.id):
				dip2 = Dipendente.objects.select_for_update().filter(pk=dip.pk).first()
				if dip2 and dip2.stato != 'cessato':
					dip2.stato = 'cessato'
					if not dip2.data_cessazione:
						dip2.data_cessazione = r2.data_fine_rapporto
					dip2.save(update_fields=['stato', 'data_cessazione'])
					dipendenti_cessati += 1

	return {
		'rapporti_chiusi': rapporti_chiusi,
		'dipendenti_cessati': dipendenti_cessati,
		'ids_rapporti': ids,
	}


def contratti_td_scaduti_non_chiusi(azienda, oggi=None):
	"""Contratti a termine con data fine passata ancora in stato sottoscritto/sospeso."""
	from .models import RapportoDiLavoro

	oggi = oggi or timezone.localdate()
	qs = (
		RapportoDiLavoro.objects.filter(
			azienda=azienda,
			stato__in=('sottoscritto', 'sospeso'),
			data_fine_rapporto__isnull=False,
			data_fine_rapporto__lt=oggi,
		)
		.exclude(dipendente__stato='cessato')
		.select_related('dipendente', 'tipo_contratto')
		.order_by('data_fine_rapporto')
	)
	return [r for r in qs if is_contratto_a_scadenza_fine_rapporto(r.tipo_contratto)]
