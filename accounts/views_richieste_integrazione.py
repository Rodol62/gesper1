from django.contrib.auth.decorators import login_required, user_passes_test
from django.core.paginator import Paginator
from django.shortcuts import render
from .models import RichiestaIntegrazioneCandidato
from .views_admin_candidati import _is_hr_or_admin, get_candidato_gestionabile_o_404


@login_required
@user_passes_test(_is_hr_or_admin)
def lista_richieste_integrazione_candidato(request, user_id):
    candidato = get_candidato_gestionabile_o_404(request, user_id)
    richieste_qs = RichiestaIntegrazioneCandidato.objects.filter(candidato=candidato).order_by('-data_invio')
    paginator = Paginator(richieste_qs, 20)
    page_obj = paginator.get_page(request.GET.get('page') or 1)
    return render(request, 'accounts/lista_richieste_integrazione.html', {
        'candidato': candidato,
        'richieste': page_obj,
        'page_obj': page_obj,
    })
