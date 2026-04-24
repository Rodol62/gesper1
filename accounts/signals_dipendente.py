"""Sincronizzazione ruoli/gruppo portale quando cambia l’anagrafica dipendente."""

from django.db.models.signals import post_save
from django.dispatch import receiver

from anagrafiche.models import Dipendente

from .portale_dipendente_defaults import sync_ruoli_e_gruppo_da_dipendente


@receiver(post_save, sender=Dipendente)
def dipendente_post_save_sync_portale(sender, instance, **kwargs):
    if instance.utente_id:
        sync_ruoli_e_gruppo_da_dipendente(instance)
