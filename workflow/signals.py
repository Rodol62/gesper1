from django.db.models.signals import post_save
from django.dispatch import receiver

from richieste.models import Richiesta

from .services import inizializza_workflow_richiesta


@receiver(post_save, sender=Richiesta)
def avvia_workflow_su_nuova_richiesta(sender, instance, created, **kwargs):
    if not created:
        return
    if instance.stato != 'inviata':
        return
    inizializza_workflow_richiesta(instance)
