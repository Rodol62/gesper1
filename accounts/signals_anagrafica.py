"""Sincronizzazione anagrafica leggera tra User/ProfiloCandidato/Dipendente."""

from django.db.models.signals import post_save
from django.dispatch import receiver

from accounts.models import ProfiloCandidato, User
from accounts.sync_anagrafica import sincronizza_dipendente_da_profilo


@receiver(post_save, sender=ProfiloCandidato)
def profilo_candidato_post_save_sync_dipendente(sender, instance, **kwargs):
    # Allineamento conservativo: aggiorna solo se già collegato o se esiste match CF;
    # non crea nuovi Dipendente in background.
    sincronizza_dipendente_da_profilo(instance.user, instance, create_if_missing=False)


@receiver(post_save, sender=User)
def user_post_save_sync_dipendente(sender, instance, **kwargs):
    profilo = getattr(instance, "profilo_candidato", None)
    if not profilo:
        return
    sincronizza_dipendente_da_profilo(instance, profilo, create_if_missing=False)

