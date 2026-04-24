from django.contrib.auth.decorators import user_passes_test
from django.shortcuts import render, redirect
from .models import User

def is_supervisore(user):
    return user.is_authenticated and user.has_ruolo('admin')

@user_passes_test(is_supervisore)
def dashboard_supervisore(request):
    utenti_da_convalidare = User.objects.filter(convalidato=False)
    if request.method == 'POST':
        user_id = request.POST.get('user_id')
        user = User.objects.get(id=user_id)
        user.convalidato = True
        user.save()
        return redirect('dashboard_supervisore')
    return render(request, 'accounts/dashboard_supervisore.html', {'utenti': utenti_da_convalidare})
