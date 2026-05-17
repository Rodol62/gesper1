from django.shortcuts import render, redirect
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import Group, Permission
from anagrafiche.permissions import admin_required

@login_required
@admin_required
def lista_ruoli(request):
    ruoli = Group.objects.all()
    return render(request, 'ruoli/lista.html', {'ruoli': ruoli})

@login_required
@admin_required
def crea_ruolo(request):
    if request.method == 'POST':
        nome = request.POST.get('nome')
        if nome:
            Group.objects.create(name=nome)
            return redirect('lista_ruoli')
    return render(request, 'ruoli/crea.html')

@login_required
@admin_required
def modifica_ruolo(request, pk):
    ruolo = Group.objects.get(pk=pk)
    if request.method == 'POST':
        ruolo.name = request.POST.get('nome')
        ruolo.save()
        return redirect('lista_ruoli')
    return render(request, 'ruoli/modifica.html', {'ruolo': ruolo})

@login_required
@admin_required
def elimina_ruolo(request, pk):
    ruolo = Group.objects.get(pk=pk)
    ruolo.delete()
    return redirect('lista_ruoli')
