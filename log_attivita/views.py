from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.core.paginator import Paginator
from django.shortcuts import render, redirect, get_object_or_404
from django.urls import reverse
from django.utils import timezone
from datetime import timedelta

from accounts.gestione_database import can_gestione_database
from .models import LogAttivita, LogErrore


def _solo_admin(request):
    return can_gestione_database(request.user)


@login_required
def lista_log_attivita(request):
    if not _solo_admin(request):
        messages.error(request, 'Accesso non autorizzato.')
        return redirect('dashboard_admin')

    qs = LogAttivita.objects.select_related('utente', 'azienda').prefetch_related('utente__ruoli')

    operazione = request.GET.get('op', '')
    utente_id = request.GET.get('utente', '')
    giorni = request.GET.get('giorni', '7')
    geofence_only = request.GET.get('geofence', '') == '1'

    try:
        giorni_int = int(giorni)
    except ValueError:
        giorni_int = 7

    if giorni_int > 0:
        qs = qs.filter(data_ora__gte=timezone.now() - timedelta(days=giorni_int))
    if operazione:
        qs = qs.filter(operazione=operazione)
    if utente_id:
        qs = qs.filter(utente_id=utente_id)
    if geofence_only:
        qs = qs.filter(operazione='presenza')
        qs = qs.filter(descrizione__icontains='[TIMBRATURA_GEO]')
        qs = qs.filter(descrizione__icontains='motivo=fuori_perimetro')

    qs = qs.order_by('-data_ora', '-id')
    paginator = Paginator(qs, 25)
    page_obj = paginator.get_page(request.GET.get('page') or 1)

    return render(request, 'log_attivita/lista_log.html', {
        'page_obj': page_obj,
        'operazioni': LogAttivita.TIPO_OPERAZIONE,
        'filtro_op': operazione,
        'filtro_utente': utente_id,
        'filtro_giorni': giorni,
        'filtro_geofence': geofence_only,
    })


@login_required
def lista_log_errori(request):
    if not _solo_admin(request):
        messages.error(request, 'Accesso non autorizzato.')
        return redirect('dashboard_admin')

    qs = LogErrore.objects.select_related('utente').all()

    livello = request.GET.get('livello', '')
    risolto = request.GET.get('risolto', '')
    giorni = request.GET.get('giorni', '7')

    try:
        giorni_int = int(giorni)
    except ValueError:
        giorni_int = 7

    if giorni_int > 0:
        qs = qs.filter(data_ora__gte=timezone.now() - timedelta(days=giorni_int))
    if livello:
        qs = qs.filter(livello=livello)
    if risolto == '1':
        qs = qs.filter(risolto=True)
    elif risolto == '0':
        qs = qs.filter(risolto=False)

    qs = qs.order_by('-data_ora', '-id')
    paginator = Paginator(qs, 25)
    page_obj = paginator.get_page(request.GET.get('page') or 1)

    return render(request, 'log_attivita/lista_errori.html', {
        'page_obj': page_obj,
        'filtro_livello': livello,
        'filtro_risolto': risolto,
        'filtro_giorni': giorni,
        'livelli': LogErrore.LIVELLO_CHOICES,
        'totale_non_risolti': LogErrore.objects.filter(risolto=False).count(),
    })


@login_required
def segna_errore_risolto(request, errore_id):
    if not _solo_admin(request):
        return redirect('dashboard_admin')
    if request.method == 'POST':
        LogErrore.objects.filter(pk=errore_id).update(risolto=True)
        messages.success(request, 'Errore segnato come risolto.')
    return redirect(request.META.get('HTTP_REFERER') or reverse('lista_log_errori'))


@login_required
def dettaglio_errore(request, errore_id):
    if not _solo_admin(request):
        return redirect('dashboard_admin')
    errore = get_object_or_404(LogErrore, pk=errore_id)
    return render(request, 'log_attivita/dettaglio_errore.html', {'errore': errore})
