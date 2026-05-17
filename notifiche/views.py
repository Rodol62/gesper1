from django.shortcuts import render, redirect
from django.contrib.auth.decorators import login_required
from .models import Notifica

@login_required
def lista_notifiche(request):
    notifiche = Notifica.objects.filter(destinatario=request.user).order_by('-data_invio')
    return render(request, 'notifiche/lista.html', {'notifiche': notifiche})

@login_required
def dettaglio_notifica(request, pk):
    notifica = Notifica.objects.get(pk=pk, destinatario=request.user)
    notifica.letta = True
    notifica.save()
    return render(request, 'notifiche/dettaglio.html', {'notifica': notifica})
