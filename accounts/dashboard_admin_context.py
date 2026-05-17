"""
Contesto per il pannello dashboard admin incorporato in ``/moduli/`` (home portale).

La route ``/accounts/dashboard_admin/`` reindirizza a ``centro_moduli``; la logica
numeri/agenda resta centralizzata qui per evitare duplicazione con ``accounts.views``.
"""

from __future__ import annotations

from typing import Any

from django.contrib.auth import get_user_model
from django.db.models import Count, Q
from django.http import HttpRequest
from django.utils import timezone

from accounts.agenda_scadenze import agenda_popup_items, build_agenda_scadenze
from accounts.tenant import get_azienda_operativa
from accounts.models import MovimentoImportPaghe
from anagrafiche.models import Azienda, Dipendente
from documenti.models import Documento
from log_attivita.models import LogAttivita
from presenze.models import Presenza
from richieste.models import Richiesta


def get_dashboard_admin_context(request: HttpRequest) -> dict[str, Any]:
    """Restituisce i conteggi e i dati agenda per il pannello admin sulla home moduli."""
    User = get_user_model()
    azienda_operativa = get_azienda_operativa(request.user, request.session)

    if azienda_operativa:
        utenti_count = User.objects.filter(azienda=azienda_operativa).count()
        dipendenti_count = Dipendente.objects.filter(azienda=azienda_operativa).count()
        doc_agg = Documento.objects.filter(azienda=azienda_operativa).aggregate(
            total=Count('id'),
            buste=Count('id', filter=Q(tipo='busta_paga')),
            f24=Count('id', filter=Q(tipo='altro', descrizione__icontains='F24')),
            cud=Count('id', filter=Q(tipo='certificato')),
        )
        documenti_count = doc_agg['total'] or 0
        buste_doc_count = doc_agg['buste'] or 0
        buste_mov_count = MovimentoImportPaghe.objects.filter(
            azienda=azienda_operativa,
            tipo='BUSTA',
        ).count()
        buste_count = max(buste_doc_count, buste_mov_count)
        f24_count = doc_agg['f24'] or 0
        cud_count = doc_agg['cud'] or 0
        presenze_count = Presenza.objects.filter(azienda=azienda_operativa).count()
        richieste_count = Richiesta.objects.filter(azienda=azienda_operativa).count()
        log_count = LogAttivita.objects.filter(azienda=azienda_operativa).count()
    else:
        utenti_count = User.objects.count()
        dipendenti_count = Dipendente.objects.count()
        doc_agg = Documento.objects.aggregate(
            total=Count('id'),
            buste=Count('id', filter=Q(tipo='busta_paga')),
            f24=Count('id', filter=Q(tipo='altro', descrizione__icontains='F24')),
            cud=Count('id', filter=Q(tipo='certificato')),
        )
        documenti_count = doc_agg['total'] or 0
        buste_doc_count = doc_agg['buste'] or 0
        buste_mov_count = MovimentoImportPaghe.objects.filter(tipo='BUSTA').count()
        buste_count = max(buste_doc_count, buste_mov_count)
        f24_count = doc_agg['f24'] or 0
        cud_count = doc_agg['cud'] or 0
        presenze_count = Presenza.objects.count()
        richieste_count = Richiesta.objects.count()
        log_count = LogAttivita.objects.count()

    aziende_count = Azienda.objects.count()
    oggi = timezone.localdate()
    agenda_all = build_agenda_scadenze(azienda_operativa, oggi=oggi)
    return {
        'utenti_count': utenti_count,
        'dipendenti_count': dipendenti_count,
        'aziende_count': aziende_count,
        'documenti_count': documenti_count,
        'buste_count': buste_count,
        'f24_count': f24_count,
        'cud_count': cud_count,
        'presenze_count': presenze_count,
        'richieste_count': richieste_count,
        'log_count': log_count,
        'azienda_operativa': azienda_operativa,
        'agenda_todo': agenda_popup_items(agenda_all, oggi=oggi),
        'agenda_oggi': oggi,
    }
