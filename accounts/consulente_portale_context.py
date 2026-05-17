"""
Contesto per il pannello consulente incorporato in ``/moduli/``.

La route ``/accounts/consulente/`` reindirizza a ``centro_moduli``; la logica numeri/tabelle
è centralizzata qui (la view dedicata non renderizza più un template separato).
"""

from __future__ import annotations

from datetime import date
from typing import Any

from django.db.models import Count, Q
from django.http import HttpRequest

from accounts.views_consulente import MESI_ITA, _get_azienda_consulente, _safe_id
from anagrafiche.models import ComunicazioneRecessoProva, Dipendente
from documenti.models import Documento
from presenze.models import Presenza
from rapporto_di_lavoro.models import AddendumContrattuale, PropostaAssunzione, RapportoDiLavoro


def get_consulente_portale_context(request: HttpRequest) -> dict[str, Any] | None:
    """
    Restituisce il contesto del pannello consulente per la home ``/moduli/``.

    ``None`` se l'utente non è un consulente «puro» (senza ruolo admin piattaforma).
    """
    user = request.user
    if not user.is_authenticated or not user.has_ruolo('consulente'):
        return None
    if user.is_superuser or user.has_ruolo('admin'):
        return None

    azienda = _get_azienda_consulente(user)
    if not azienda:
        return {'consulente_portale_senza_azienda': True}

    oggi = date.today()
    dip_st = Dipendente.objects.filter(azienda=azienda, stato__in=('attivo', 'candidato')).aggregate(
        dipendenti_attivi=Count('id', filter=Q(stato='attivo')),
        candidati_count=Count('id', filter=Q(stato='candidato')),
    )
    dipendenti_attivi = dip_st['dipendenti_attivi'] or 0
    candidati_count = dip_st['candidati_count'] or 0
    candidati_recenti = Dipendente.objects.filter(
        azienda=azienda,
        stato='candidato',
    ).order_by('-id')[:8]

    candidati_ids = [_safe_id(d) for d in candidati_recenti if _safe_id(d)]
    candidati_posizione: dict[int, dict[str, str]] = {}
    for p in PropostaAssunzione.objects.filter(
        dipendente_id__in=candidati_ids,
    ).order_by('-id').values('dipendente_id', 'posizione', 'livello_ccnl'):
        did = p['dipendente_id']
        if did not in candidati_posizione:
            candidati_posizione[did] = {
                'posizione': p['posizione'] or '',
                'livello': p['livello_ccnl'] or '',
            }
    _stati_dip = {'candidato', 'attivo', 'cessato'}
    for dip in candidati_recenti:
        did = _safe_id(dip)
        if did is None:
            continue
        if did not in candidati_posizione:
            r = (getattr(dip, 'ruolo', '') or '').strip()
            if r and r.lower() not in _stati_dip and not r.isdigit():
                candidati_posizione[did] = {'posizione': r, 'livello': ''}
            else:
                candidati_posizione[did] = {'posizione': '', 'livello': ''}

    _stati_firmata_equiv = PropostaAssunzione.stati_equivalenti('firmata_candidato')
    proposte_da_approvare = PropostaAssunzione.objects.filter(
        azienda=azienda, stato__in=_stati_firmata_equiv
    ).count()

    doc_kpi = Documento.objects.filter(azienda=azienda).aggregate(
        buste_paga_count=Count('id', filter=Q(tipo='busta_paga')),
        f24_documenti_count=Count('id', filter=Q(tipo='altro', descrizione__icontains='F24')),
        cud_certificati_count=Count('id', filter=Q(tipo='certificato')),
    )
    buste_paga_count = doc_kpi['buste_paga_count'] or 0
    f24_documenti_count = doc_kpi['f24_documenti_count'] or 0
    cud_certificati_count = doc_kpi['cud_certificati_count'] or 0

    presenze_mese_count = Presenza.objects.filter(
        azienda=azienda,
        data__year=oggi.year,
        data__month=oggi.month,
    ).count()

    from rapporto_di_lavoro.services_contratti import contratti_td_in_scadenza, contratti_td_scaduti_non_chiusi

    td_in_scadenza = len(contratti_td_in_scadenza(azienda))
    td_scaduti_aperti = len(contratti_td_scaduti_non_chiusi(azienda))

    contratti_registrati_count = RapportoDiLavoro.objects.filter(azienda=azienda).count()
    addenda_recenti = (
        AddendumContrattuale.objects.filter(rapporto__azienda=azienda)
        .select_related('rapporto', 'rapporto__dipendente', 'creato_da')
        .order_by('-data_creazione', '-id')[:10]
    )
    addendum_anno_count = AddendumContrattuale.objects.filter(
        rapporto__azienda=azienda,
        data_creazione__year=oggi.year,
    ).count()

    recesso_qs = (
        ComunicazioneRecessoProva.per_azienda(azienda)
        .filter(stato='in_verifica_consulente')
        .select_related('dipendente', 'rapporto')
        .order_by('-data_modifica')
    )
    recesso_prova_in_verifica_count = recesso_qs.count()
    recesso_prova_in_verifica = list(recesso_qs[:12])

    return {
        'azienda': azienda,
        'dipendenti_attivi': dipendenti_attivi,
        'candidati_count': candidati_count,
        'candidati_recenti': candidati_recenti,
        'candidati_posizione': candidati_posizione,
        'proposte_da_approvare': proposte_da_approvare,
        'recesso_prova_in_verifica_count': recesso_prova_in_verifica_count,
        'recesso_prova_in_verifica': recesso_prova_in_verifica,
        'buste_paga_count': buste_paga_count,
        'f24_documenti_count': f24_documenti_count,
        'cud_certificati_count': cud_certificati_count,
        'presenze_mese_count': presenze_mese_count,
        'mese_corrente': MESI_ITA[oggi.month],
        'mese_corrente_num': oggi.month,
        'anno_corrente': oggi.year,
        'td_in_scadenza': td_in_scadenza,
        'td_scaduti_aperti': td_scaduti_aperti,
        'contratti_registrati_count': contratti_registrati_count,
        'addenda_recenti': addenda_recenti,
        'addendum_anno_count': addendum_anno_count,
    }
