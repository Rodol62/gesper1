from django.shortcuts import render, redirect
from django.contrib.auth.decorators import login_required
import logging

logger = logging.getLogger(__name__)


def root_redirect(request):
    """Home `/`: utenti autenticati → centro moduli; altri → login."""
    if request.user.is_authenticated:
        return redirect('centro_moduli')
    return redirect('login')


@login_required
def home(request):
    from accounts.tenant import get_azienda_operativa

    return render(
        request,
        'home.html',
        {'azienda_operativa': get_azienda_operativa(request.user, request.session)},
    )


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
