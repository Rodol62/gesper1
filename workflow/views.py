from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.http import HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect, render

from accounts.tenant import get_azienda_operativa
from .models import RichiestaApprovazione
from .services import processa_azione_approvazione


def _is_autorizzato_workflow(user):
	return user.is_authenticated and (user.is_superuser or (user.has_ruolo('admin') or user.has_ruolo('hr')))


@login_required
def lista_da_approvare(request):
	if not _is_autorizzato_workflow(request.user):
		return HttpResponseForbidden('Accesso negato')

	queryset = RichiestaApprovazione.objects.select_related(
		'richiesta',
		'richiesta__dipendente',
		'step',
		'approvatore',
	).filter(stato='in_attesa')

	if request.user.is_superuser or request.user.has_ruolo('admin'):
		azienda_operativa = get_azienda_operativa(request.user, request.session)
		queryset = queryset.filter(richiesta__azienda=azienda_operativa) if azienda_operativa else queryset.none()
	elif request.user.has_ruolo('hr'):
		queryset = queryset.filter(
			richiesta__azienda=request.user.azienda,
			approvatore=request.user,
		)

	queryset = queryset.order_by('-richiesta__data_richiesta', '-id')
	paginator = Paginator(queryset, 25)
	page_obj = paginator.get_page(request.GET.get('page') or 1)

	return render(request, 'workflow/lista_da_approvare.html', {
		'page_obj': page_obj,
	})


@login_required
def azione_approvazione(request, approvazione_id, azione):
	if not _is_autorizzato_workflow(request.user):
		return HttpResponseForbidden('Accesso negato')
	if azione not in ('approvato', 'rifiutato'):
		return HttpResponseForbidden('Azione non valida')

	if request.user.is_superuser or request.user.has_ruolo('admin'):
		azienda_operativa = get_azienda_operativa(request.user, request.session)
		approvazione = get_object_or_404(
			RichiestaApprovazione,
			id=approvazione_id,
			richiesta__azienda=azienda_operativa,
		)
	elif request.user.has_ruolo('hr'):
		approvazione = get_object_or_404(
			RichiestaApprovazione,
			id=approvazione_id,
			richiesta__azienda=request.user.azienda,
		)
	else:
		return HttpResponseForbidden('Accesso negato')

	if request.user.has_ruolo('hr') and approvazione.approvatore_id != request.user.id:
		return HttpResponseForbidden('Questa approvazione non è assegnata a te')

	if request.method == 'POST':
		commento = request.POST.get('commento', '')
		processa_azione_approvazione(approvazione, request.user, azione, commento)

	return redirect('workflow:lista_da_approvare')
