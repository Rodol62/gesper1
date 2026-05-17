"""
Sincronizzazione movimenti DARE da :class:`documenti.models.CedolinoMotoreV4`.
"""

from __future__ import annotations

import logging
from decimal import Decimal
from typing import TYPE_CHECKING, Any, Literal

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import DatabaseError, IntegrityError, transaction

from documenti.models import CedolinoMotoreV4

from .models import MovimentoPartitarioNettoDipendente

if TYPE_CHECKING:
    from anagrafiche.models import Azienda, Dipendente

logger = logging.getLogger(__name__)

SyncEsito = Literal["creato", "aggiornato", "saltato"]


def _defaults_movimento_dare(
    v4: CedolinoMotoreV4,
    dip: "Dipendente",
    netto: Decimal,
    data_cont,
    utente,
) -> dict[str, Any]:
    """Campi per ``update_or_create`` del movimento DARE (senza chiavi univoche)."""
    d: dict[str, Any] = {
        "importo": netto,
        "cedolino_motore_v4": v4,
        "documento_busta": v4.documento if v4.documento_id else None,
        "data_contabile": data_cont,
        "tipo_movimento": MovimentoPartitarioNettoDipendente.TipoMovimento.BUSTA_NETTO,
        "lato": MovimentoPartitarioNettoDipendente.Lato.DARE,
    }
    if utente is not None:
        d["inserito_da"] = utente
    return d


def sincronizza_netto_busta_da_cedolino_v4(
    v4: CedolinoMotoreV4,
    *,
    utente=None,
) -> SyncEsito:
    """
    Crea o aggiorna **un solo** movimento DARE dal netto della riga ``CedolinoMotoreV4``.

    Usato dopo persistenza v4 (upload/import buste) e dalla sincronizzazione massiva in UI.

    ``utente`` opzionale: se valorizzato viene scritto in ``inserito_da``; se ``None`` quel campo
    non viene incluso nei ``defaults`` (evita di azzerarlo a ogni rilettura busta).
    """
    if v4.netto_busta is None:
        return "saltato"
    dip = v4.dipendente
    if dip is None:
        return "saltato"
    azienda = dip.azienda
    netto = Decimal(str(v4.netto_busta)).quantize(Decimal("0.01"))
    if netto <= 0:
        return "saltato"

    data_cont = MovimentoPartitarioNettoDipendente.ultimo_giorno_mese(v4.anno, v4.mese)
    defaults = _defaults_movimento_dare(v4, dip, netto, data_cont, utente)
    _obj, created = MovimentoPartitarioNettoDipendente.objects.update_or_create(
        azienda=azienda,
        dipendente=dip,
        anno=v4.anno,
        mese=v4.mese,
        natura_busta=v4.natura_busta or "ORDINARIA",
        tipo_movimento=MovimentoPartitarioNettoDipendente.TipoMovimento.BUSTA_NETTO,
        defaults=defaults,
    )
    return "creato" if created else "aggiornato"


def sincronizza_netto_dopo_persistenza_cedolino_v4(cedolino_motore_v4_pk: int, *, utente_id: int | None = None) -> None:
    """
    Esegue la sincronizzazione DARE dopo il commit della transazione che ha salvato il cedolino v4.

    Carica l'utente opzionale (es. ``Documento.caricato_da``) solo se serve valorizzare ``inserito_da``.
    """
    utente = None
    if utente_id is not None:
        User = get_user_model()
        utente = User.objects.filter(pk=utente_id).first()

    v4 = (
        CedolinoMotoreV4.objects.filter(pk=cedolino_motore_v4_pk)
        .select_related("dipendente", "dipendente__azienda", "documento")
        .first()
    )
    if not v4:
        logger.warning("Partitario netti post-v4: cedolino id=%s non trovato", cedolino_motore_v4_pk)
        return

    try:
        esito = sincronizza_netto_busta_da_cedolino_v4(v4, utente=utente)
        if esito != "saltato":
            logger.info(
                "Partitario netti post-v4: cedolino_id=%s dipendente_id=%s esito=%s",
                cedolino_motore_v4_pk,
                v4.dipendente_id,
                esito,
            )
    except (DatabaseError, IntegrityError, ValidationError, TypeError, ValueError) as exc:
        logger.warning(
            "Partitario netti post-v4 non riuscito (cedolino_id=%s): %s",
            cedolino_motore_v4_pk,
            exc,
            exc_info=True,
        )


def sincronizza_netto_buste_da_cedolini(azienda: Azienda, utente=None) -> dict[str, int]:
    """
    Per ogni cedolino motore v4 dell'azienda con ``netto_busta`` valorizzato, crea o aggiorna
    un movimento ``busta_netto`` (DARE) univoco per dipendente / anno / mese / natura busta.

    Ritorna contatori ``creati``, ``aggiornati``, ``saltati`` (senza netto o dipendente incoerente).
    """
    creati = 0
    aggiornati = 0
    saltati = 0

    qs = (
        CedolinoMotoreV4.objects.filter(dipendente__azienda=azienda)
        .select_related("dipendente", "documento")
        .order_by("id")
    )

    with transaction.atomic():
        for v4 in qs:
            esito = sincronizza_netto_busta_da_cedolino_v4(v4, utente=utente)
            if esito == "saltato":
                saltati += 1
            elif esito == "creato":
                creati += 1
            else:
                aggiornati += 1

    logger.info(
        "Sincronizzazione partitario netti azienda=%s: creati=%s aggiornati=%s saltati=%s",
        azienda.id,
        creati,
        aggiornati,
        saltati,
    )
    return {"creati": creati, "aggiornati": aggiornati, "saltati": saltati}
