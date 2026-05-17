from django.shortcuts import render, redirect
from django.contrib.auth.decorators import login_required
import logging

logger = logging.getLogger(__name__)


def root_redirect(request):
    """Home `/`: stessa logica di destinazione del post-login (evita doppia «home» moduli vs dashboard)."""
    if not request.user.is_authenticated:
        return redirect('login')
    from accounts.gestione_database import can_gestione_database

    user = request.user
    if can_gestione_database(user):
        return redirect('centro_moduli')
    has_ruolo = getattr(user, 'has_ruolo', None)
    if callable(has_ruolo) and user.has_ruolo('consulente'):
        return redirect('centro_moduli')
    return redirect('centro_moduli')


@login_required
def home(request):
    from accounts.consulente_portale_context import get_consulente_portale_context
    from accounts.dashboard_admin_context import get_dashboard_admin_context
    from accounts.gestione_database import can_gestione_database
    from accounts.tenant import get_azienda_operativa

    context: dict = {'azienda_operativa': get_azienda_operativa(request.user, request.session)}
    if can_gestione_database(request.user):
        context['mostra_pannello_dashboard_admin'] = True
        context.update(get_dashboard_admin_context(request))
    else:
        ctx_cons = get_consulente_portale_context(request)
        if ctx_cons is not None:
            context.update(ctx_cons)
            context['mostra_pannello_consulente'] = True
    return render(request, 'home.html', context)


def csrf_failure(request, reason=""):
    logger.warning(
        "CSRF failure | reason=%s | path=%s | method=%s | referer=%s | origin=%s | host=%s",
        reason,
        request.path,
        request.method,
        request.META.get('HTTP_REFERER', ''),
        request.META.get('HTTP_ORIGIN', ''),
        request.get_host(),
    )
    return render(request, '403_csrf.html', {'reason': reason}, status=403)
