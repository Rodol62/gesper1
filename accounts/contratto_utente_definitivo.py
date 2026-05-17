"""
Allineamento utente ↔ dipendente al momento in cui il contratto è «definitivo».

Definitivo significa:
- rapporto in stato ``sottoscritto`` e
- proposta collegata (se presente) in stato equivalente a ``contratto_attivo`` con firma datore,
  e firma lavoratore (digitale o cartacea / accettazione registrata).

In quel caso:
- ``Dipendente.utente`` viene agganciato all'utente del candidato se mancante;
- ruolo ``candidato`` rimosso, ruolo ``dipendente`` assicurato;
- ``ProfiloCandidato`` eliminato (i dati restano su ``Dipendente`` / contratto / documenti).
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from django.db import transaction

if TYPE_CHECKING:
    from anagrafiche.models import Dipendente
    from rapporto_di_lavoro.models import PropostaAssunzione, RapportoDiLavoro

logger = logging.getLogger(__name__)


def contratto_e_definitivo(
    contratto: RapportoDiLavoro | None,
    proposta: PropostaAssunzione | None = None,
) -> bool:
    """True se il rapporto è sottoscritto e la proposta (se c'è) è in stato «contratto attivo» con firme complete."""
    if not contratto or getattr(contratto, "stato", None) != "sottoscritto":
        return False
    if proposta is None:
        try:
            proposta = contratto.proposta_origine  # type: ignore[attr-defined]
        except Exception:
            proposta = None
    if proposta is None:
        return True
    from rapporto_di_lavoro.models import PropostaAssunzione

    if proposta.stato not in PropostaAssunzione.stati_equivalenti("contratto_attivo"):
        return False
    if not proposta.data_firma_datore:
        return False
    if proposta.data_firma_candidato or proposta.accettata_dipendente:
        return True
    return False


def ribalta_utente_candidato_su_dipendente_se_contratto_definitivo(
    dipendente: Dipendente,
    contratto: RapportoDiLavoro | None = None,
    *,
    motivo: str = "",
) -> bool:
    """
    Esegue il ribaltamento se le condizioni sono soddisfatte.
    Idempotente: se non c'è più profilo candidato e l'utente è già solo dipendente, ritorna True senza errori.
    """
    from anagrafiche.models import Dipendente
    from rapporto_di_lavoro.models import RapportoDiLavoro

    from accounts.models import ProfiloCandidato, Ruolo
    from accounts.portale_dipendente_defaults import sync_ruoli_e_gruppo_da_dipendente

    if contratto is None:
        contratto = (
            RapportoDiLavoro.objects.filter(dipendente=dipendente, stato="sottoscritto")
            .order_by("-data_ora_sottoscrizione", "-id")
            .first()
        )
    proposta = None
    if contratto is not None:
        try:
            proposta = contratto.proposta_origine  # type: ignore[attr-defined]
        except Exception:
            proposta = None

    if not contratto_e_definitivo(contratto, proposta):
        return False

    profilo = ProfiloCandidato.objects.filter(dipendente=dipendente).select_related("user").first()
    user = getattr(dipendente, "utente", None)
    if user is None and profilo is not None:
        user = profilo.user
    if user is None:
        logger.warning(
            "[RIBALTA_UTENTE] Nessun utente risolvibile per dipendente %s (%s)",
            dipendente.pk,
            motivo,
        )
        return False

    if dipendente.utente_id and dipendente.utente_id != user.pk:
        logger.error(
            "[RIBALTA_UTENTE] Dipendente %s già collegato ad altro utente (%s ≠ %s). Intervento manuale.",
            dipendente.pk,
            dipendente.utente_id,
            user.pk,
        )
        return False

    with transaction.atomic():
        dip = Dipendente.objects.select_for_update().get(pk=dipendente.pk)
        if not dip.utente_id:
            dip.utente = user
            dip.save(update_fields=["utente"])
        user.azienda = dip.azienda
        user.save(update_fields=["azienda"])

        r_cand = Ruolo.objects.filter(codice="candidato").first()
        if r_cand and user.ruoli.filter(pk=r_cand.pk).exists():
            user.ruoli.remove(r_cand)
        r_dip, _ = Ruolo.objects.get_or_create(codice="dipendente", defaults={"nome": "Dipendente"})
        user.ruoli.add(r_dip)

        ProfiloCandidato.objects.filter(user=user).delete()

        sync_ruoli_e_gruppo_da_dipendente(dip)

    logger.info(
        "[RIBALTA_UTENTE] Utente %s agganciato a dipendente %s; profilo candidato rimosso. %s",
        user.pk,
        dipendente.pk,
        motivo,
    )
    return True
