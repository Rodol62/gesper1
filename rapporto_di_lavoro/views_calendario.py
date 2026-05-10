"""
View per la configurazione del calendario lavorativo aziendale.
Permette di impostare per ogni mese:
  - Giorni di chiusura settimanale (per mese, variabile)
  - Festività aziendali (patrono, ecc.)
Mostra festività nazionali (read-only) e chiusure extra (ChiusuraAziendale).
"""
import json
import logging
from datetime import date

from django.contrib import messages
from django.contrib.auth.decorators import login_required, user_passes_test
from django.http import JsonResponse
from django.shortcuts import redirect, render
from django.views.decorators.http import require_POST

from accounts.tenant import get_azienda_operativa
from .models import CalendarioLavoroMensile, ChiusuraAziendale, FestivitaCalendario
from .utils_calendario import (
    build_griglia_mese,
    get_chiusure_extra_mese,
    get_chiusura_settimanale,
    get_festivita_mese,
    get_giorni_lavorativi_mese,
    _festivita_per_anno,
)

logger = logging.getLogger(__name__)

GIORNI_SETTIMANA = ['Lun', 'Mar', 'Mer', 'Gio', 'Ven', 'Sab', 'Dom']
MESI_NOMI = [
    'Gennaio', 'Febbraio', 'Marzo', 'Aprile', 'Maggio', 'Giugno',
    'Luglio', 'Agosto', 'Settembre', 'Ottobre', 'Novembre', 'Dicembre',
]


def _is_admin(user):
    return user.is_superuser or getattr(user, 'ruolo', '') == 'admin'


def _get_azienda(user, session):
    """Azienda operativa: stessa logica del resto dell'app (``get_azienda_operativa``)."""
    return get_azienda_operativa(user, session)


@login_required
@user_passes_test(_is_admin)
def calendario_aziendale(request, anno=None):
    """
    Pagina principale del calendario lavorativo aziendale.
    Mostra 12 mesi con griglia interattiva.
    """
    anno = int(anno or request.GET.get('anno', date.today().year))
    azienda = _get_azienda(request.user, request.session)

    if not azienda:
        messages.warning(request, 'Seleziona prima un\'azienda operativa.')
        return redirect('lista_proposte_assunzione')

    # Carica configurazioni esistenti per l'anno
    clm_qs = CalendarioLavoroMensile.objects.filter(azienda=azienda, anno=anno)
    clm_map = {c.mese: c for c in clm_qs}

    # Festività nazionali dell'anno (per il pannello laterale)
    festivita_anno = _festivita_per_anno(anno)

    mesi_data = []
    for mese_num in range(1, 13):
        clm = clm_map.get(mese_num)
        chiusura_sett = clm.chiusura_settimanale if clm else [6]  # default domenica

        griglia = build_griglia_mese(anno, mese_num, azienda, chiusura_sett)
        info = get_giorni_lavorativi_mese(azienda, anno, mese_num)

        # Festività aziendali del mese (per form aggiunta)
        fest_az = FestivitaCalendario.objects.filter(
            azienda=azienda, data__year=anno, data__month=mese_num, attivo=True,
        )
        # Chiusure extra del mese
        chiusure_ex = ChiusuraAziendale.objects.filter(
            azienda=azienda,
            attivo=True,
            data_inizio__year__lte=anno,
            data_fine__year__gte=anno,
        ).filter(
            data_inizio__month__lte=mese_num,
            data_fine__month__gte=mese_num,
        )

        mesi_data.append({
            'mese_num':          mese_num,
            'mese_nome':         MESI_NOMI[mese_num - 1],
            'griglia':           griglia,
            'chiusura_sett':     chiusura_sett,
            'giorni_lavorativi': info['giorni_lavorativi'],
            'giorni_conv_26':    info['giorni_conv_26'],
            'festivi':           info['festivi'],
            'chiusure_extra_n':  info['chiusure_extra'],
            'note':              clm.note if clm else '',
            'festivita_az':      list(fest_az),
            'chiusure_extra':    list(chiusure_ex),
        })

    context = {
        'anno':               anno,
        'anno_prec':          anno - 1,
        'anno_succ':          anno + 1,
        'azienda':            azienda,
        'mesi_data':          mesi_data,
        'giorni_sett':        GIORNI_SETTIMANA,
        'festivita_anno':     sorted(festivita_anno.items()),
    }
    return render(request, 'rapporto_di_lavoro/calendario_aziendale.html', context)


@login_required
@user_passes_test(_is_admin)
@require_POST
def calendario_salva_mese(request):
    """
    Salva la configurazione di un singolo mese (AJAX o form POST).
    Payload JSON: {anno, mese, chiusura_settimanale: [int,...], note: str}
    """
    azienda = _get_azienda(request.user, request.session)
    if not azienda:
        return JsonResponse({'ok': False, 'error': 'Azienda non trovata'}, status=400)

    try:
        data = json.loads(request.body)
        anno  = int(data['anno'])
        mese  = int(data['mese'])
        cs    = [int(d) for d in data.get('chiusura_settimanale', []) if 0 <= int(d) <= 6]
        note  = str(data.get('note', ''))[:200]
    except (KeyError, ValueError, json.JSONDecodeError) as e:
        return JsonResponse({'ok': False, 'error': str(e)}, status=400)

    clm, created = CalendarioLavoroMensile.objects.update_or_create(
        azienda=azienda, anno=anno, mese=mese,
        defaults={'chiusura_settimanale': cs, 'note': note},
    )
    info = get_giorni_lavorativi_mese(azienda, anno, mese)
    return JsonResponse({
        'ok':                True,
        'giorni_lavorativi': info['giorni_lavorativi'],
        'giorni_conv_26':    info['giorni_conv_26'],
        'festivi':           info['festivi'],
    })


@login_required
@user_passes_test(_is_admin)
@require_POST
def calendario_copia_mese(request):
    """
    Copia la configurazione di un mese a tutti i mesi successivi dell'anno (AJAX).
    Payload JSON: {anno, mese_da, mesi_a: [int,...]}
    """
    azienda = _get_azienda(request.user, request.session)
    if not azienda:
        return JsonResponse({'ok': False, 'error': 'Azienda non trovata'}, status=400)

    try:
        data    = json.loads(request.body)
        anno    = int(data['anno'])
        mese_da = int(data['mese_da'])
        mesi_a  = [int(m) for m in data.get('mesi_a', []) if 1 <= int(m) <= 12]
    except (KeyError, ValueError, json.JSONDecodeError) as e:
        return JsonResponse({'ok': False, 'error': str(e)}, status=400)

    try:
        sorgente = CalendarioLavoroMensile.objects.get(azienda=azienda, anno=anno, mese=mese_da)
    except CalendarioLavoroMensile.DoesNotExist:
        return JsonResponse({'ok': False, 'error': 'Mese sorgente non configurato'}, status=404)

    for m in mesi_a:
        CalendarioLavoroMensile.objects.update_or_create(
            azienda=azienda, anno=anno, mese=m,
            defaults={
                'chiusura_settimanale': sorgente.chiusura_settimanale,
                'note': sorgente.note,
            },
        )

    return JsonResponse({'ok': True, 'copiati': mesi_a})


@login_required
@user_passes_test(_is_admin)
@require_POST
def festivita_aziendale_aggiungi(request):
    """
    Aggiunge una festività aziendale (patrono, ecc.) — AJAX.
    Payload JSON: {data: 'YYYY-MM-DD', nome: str}
    """
    azienda = _get_azienda(request.user, request.session)
    if not azienda:
        return JsonResponse({'ok': False, 'error': 'Azienda non trovata'}, status=400)

    try:
        payload = json.loads(request.body)
        d    = date.fromisoformat(payload['data'])
        nome = str(payload.get('nome', 'Festività aziendale')).strip()[:120]
    except (KeyError, ValueError, json.JSONDecodeError) as e:
        return JsonResponse({'ok': False, 'error': str(e)}, status=400)

    fest, created = FestivitaCalendario.objects.get_or_create(
        data=d,
        nome=nome,
        livello='aziendale',
        azienda=azienda,
        defaults={'attivo': True, 'regione': '', 'provincia': '', 'comune': ''},
    )
    return JsonResponse({'ok': True, 'id': fest.pk, 'created': created,
                         'data': d.isoformat(), 'nome': fest.nome})


@login_required
@user_passes_test(_is_admin)
@require_POST
def festivita_aziendale_elimina(request, fest_id):
    """Elimina una festività aziendale (AJAX)."""
    azienda = _get_azienda(request.user, request.session)
    deleted, _ = FestivitaCalendario.objects.filter(
        pk=fest_id, azienda=azienda, livello='aziendale',
    ).delete()
    return JsonResponse({'ok': deleted > 0})
