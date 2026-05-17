"""
Segnali per coerenza referenziale con ``documenti`` (cedolini v4 e buste).

Politica: alla cancellazione di una riga :class:`documenti.models.CedolinoMotoreV4`
si eliminano i movimenti partitario **DARE** che la referenziavano, così non restano
importi «da busta» senza sorgente contabile (il FK sul modello è ``SET_NULL`` solo per
non bloccare operazioni legacy; la pulizia avviene qui).
"""

from __future__ import annotations

import logging

from django.db.models.signals import pre_delete
from django.dispatch import receiver

from documenti.models import CedolinoMotoreV4

from .models import MovimentoPartitarioNettoDipendente

logger = logging.getLogger(__name__)


@receiver(pre_delete, sender=CedolinoMotoreV4)
def rimuovi_movimenti_partitario_dare_collegati_a_cedolino_v4(
    sender,
    instance: CedolinoMotoreV4,
    **kwargs,
) -> None:
    """Rimuove i DARE partitario legati a questa estrazione v4 (prima che il record v4 sparisca)."""
    qs = MovimentoPartitarioNettoDipendente.objects.filter(
        cedolino_motore_v4_id=instance.pk,
        tipo_movimento=MovimentoPartitarioNettoDipendente.TipoMovimento.BUSTA_NETTO,
    )
    n, _ = qs.delete()
    if n:
        logger.info(
            "Partitario netti: eliminati %s movimenti DARE collegati a CedolinoMotoreV4 id=%s",
            n,
            instance.pk,
        )
