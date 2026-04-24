"""Segnali dominio contratti → anagrafica dipendente."""

from django.db.models.signals import post_save
from django.dispatch import receiver

from .models import RapportoDiLavoro
from .services_contratti import sync_dipendente_da_rapporto_vigente


@receiver(post_save, sender=RapportoDiLavoro)
def rapporto_salvato_aggiorna_anagrafica_dipendente(sender, instance, **kwargs):
	"""
	Allinea date anagrafiche al contratto vigente (sottoscritto/sospeso) più recente.
	Ignora bozze in «proposta» (la sync sceglie solo tra sottoscritto/sospeso).
	"""
	# Sempre ricalcolare: cambio stato es. proposta → sottoscritto o modifica date
	sync_dipendente_da_rapporto_vigente(instance.dipendente_id, instance.azienda_id)
