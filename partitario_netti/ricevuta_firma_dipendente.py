"""
Pubblicazione e revoca della ricevuta PDF «acconto contanti» nell'area documenti del dipendente.
"""

from __future__ import annotations

import logging
from datetime import date
from decimal import Decimal
from typing import Any

from django.core.files.base import ContentFile
from django.db import transaction
from django.utils import timezone

from documenti.models import Documento

from .constants import MESI_NOMI
from .models import MovimentoPartitarioNettoDipendente

logger = logging.getLogger(__name__)


def descrizione_documento_ricevuta_firma_acconto(mov: MovimentoPartitarioNettoDipendente) -> str:
    """Titolo breve in elenco documenti dipendente."""
    mese_n = MESI_NOMI[mov.mese] if 1 <= int(mov.mese) <= 12 else str(mov.mese)
    imp = mov.importo if mov.importo is not None else Decimal("0")
    return (
        f"Ricevuta acconto retribuzione (contanti) — competenza {mese_n} {mov.anno} "
        f"— € {imp} — da firmare"
    )


def revoca_ricevuta_firma_dipendente(mov: MovimentoPartitarioNettoDipendente) -> bool:
    """
    Rimuove la ricevuta dall'area dipendente eliminando il documento collegato.

    Ritorna True se esisteva una pubblicazione attiva.
    """
    doc = mov.documento_ricevuta_firma
    if doc is None:
        return False
    MovimentoPartitarioNettoDipendente.objects.filter(pk=mov.pk).update(documento_ricevuta_firma=None)
    mov.documento_ricevuta_firma = None
    doc.delete()
    return True


def movimento_richiede_revoca_ricevuta_firma(
    mov: MovimentoPartitarioNettoDipendente,
    *,
    nuovo_importo: Decimal,
    nuova_data_contabile: date,
    nuovo_anno: int,
    nuovo_mese: int,
    nuovo_dipendente_id: int,
    nuovo_metodo: str,
) -> bool:
    """True se i dati riportati sulla ricevuta PDF cambiano rispetto al movimento salvato."""
    if not mov.documento_ricevuta_firma_id:
        return False
    if nuovo_metodo != mov.metodo_pagamento:
        return True
    if int(nuovo_dipendente_id) != int(mov.dipendente_id):
        return True
    if nuovo_importo != mov.importo:
        return True
    if int(nuovo_anno) != int(mov.anno) or int(nuovo_mese) != int(mov.mese):
        return True
    if nuova_data_contabile != mov.data_contabile:
        return True
    return False


def pubblica_ricevuta_firma_dipendente(
    mov: MovimentoPartitarioNettoDipendente,
    azienda: Any,
    utente: Any,
    pdf_bytes: bytes,
) -> Documento:
    """
    Crea (o sostituisce) il PDF in «I miei documenti» del dipendente, tipo ricevuta pagamento netto.

    ``mov`` deve essere coerente con contanti e pagamento; la validazione resta nelle viste.
    """
    nome_file = f"ricevuta_acconto_mov{mov.pk}_{timezone.now().strftime('%Y%m%d%H%M%S')}.pdf"
    with transaction.atomic():
        if mov.documento_ricevuta_firma_id:
            revoca_ricevuta_firma_dipendente(mov)
        doc = Documento.objects.create(
            azienda=azienda,
            dipendente=mov.dipendente,
            tipo="ricevuta_pagamento_netto",
            descrizione=descrizione_documento_ricevuta_firma_acconto(mov),
            file=ContentFile(pdf_bytes, name=nome_file),
            caricato_da=utente,
            caricato_dal_dipendente=False,
            visibile_al_dipendente=True,
        )
        mov.documento_ricevuta_firma = doc
        mov.save(update_fields=["documento_ricevuta_firma", "aggiornato_il"])
    return doc
