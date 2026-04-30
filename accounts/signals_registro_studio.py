"""Segnali sul partitario consulente–azienda (libro movimenti)."""

from django.db.models.signals import post_delete
from django.dispatch import receiver

from accounts.models import MovimentoRegistroStudioConsulente


@receiver(post_delete, sender=MovimentoRegistroStudioConsulente)
def ricalcola_saldi_dopo_eliminazione_movimento(sender, instance, **kwargs):
    """Eliminazione da Admin o da codice: saldi progressivi coerenti sul resto delle righe."""
    aid = getattr(instance, "azienda_id", None)
    if not aid:
        return
    from accounts.consulente_registro_studio import ricalcola_saldi_progressivi

    ricalcola_saldi_progressivi(aid)
