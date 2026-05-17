from django.shortcuts import render
from django.contrib.auth.decorators import login_required
from anagrafiche.models import Dipendente


@login_required
def report_hr(request):
    dipendenti = Dipendente.objects.all()
    return render(request, 'report/hr.html', {'report': [], 'dipendenti': dipendenti})
