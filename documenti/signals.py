"""
Segnali documenti: coerenza dati tra modelli collegati.
"""
from __future__ import annotations

import contextvars

from django.db.models.signals import pre_delete
from django.dispatch import receiver

from documenti.models import CedolinoMotoreV4, Documento

# Durante ``purge_buste_paga --keep-cedolini-v4`` va impostato a False attorno al delete
# dei Documento busta_paga, per non rimuovere le estrazioni (comportamento legacy SET_NULL).
busta_paga_delete_cascade_motore_v4: contextvars.ContextVar[bool] = contextvars.ContextVar(
    "busta_paga_delete_cascade_motore_v4",
    default=True,
)


@receiver(pre_delete, sender=Documento)
def elimina_estrazioni_motore_v4_collegate_a_busta(sender, instance: Documento, **kwargs):
    """Elimina CedolinoMotoreV4 legati a questo PDF prima che il FK vada in SET_NULL."""
    if instance.tipo != "busta_paga":
        return
    if not busta_paga_delete_cascade_motore_v4.get():
        return
    pk = instance.pk
    if pk is None:
        return
    CedolinoMotoreV4.objects.filter(documento_id=pk).delete()
