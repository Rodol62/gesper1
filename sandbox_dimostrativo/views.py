"""
Attivazione / disattivazione sessione dimostrativa (solo superuser, richiede DB sandbox inizializzato).
"""

from __future__ import annotations

import logging

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.http import HttpResponseForbidden
from django.shortcuts import redirect
from django.urls import reverse
from django.views.decorators.http import require_POST

logger = logging.getLogger(__name__)


def _sandbox_demo_username_hint() -> str:
    """Username dimostrativo da mostrare nei messaggi (allineato a GESPER_SANDBOX_USERNAMES)."""
    names = getattr(settings, "GESPER_SANDBOX_USERNAMES", frozenset({"demo"}))
    return min(names) if names else "demo"


@login_required
@require_POST
def sandbox_sessione_attiva(request):
    """Imposta la sessione su DB sandbox (ORM instradato) per il superuser corrente."""
    if not getattr(settings, "GESPER_SANDBOX_ENABLED", False):
        return HttpResponseForbidden("Sandbox non abilitato in configurazione.")
    if not request.user.is_superuser:
        return HttpResponseForbidden("Solo i superuser possono attivare la modalità dimostrativa.")
    User = get_user_model()
    if not User.objects.using("sandbox").filter(pk=request.user.pk).exists():
        messages.error(
            request,
            "Il tuo utente non è presente nel database sandbox. Usa il login "
            f"«{_sandbox_demo_username_hint()}» "
            "oppure il comando «gesper_sandbox_sync_user» per copiare il profilo.",
        )
        return redirect(reverse("profile"))
    request.session["gesper_sandbox_attiva"] = True
    request.session.modified = True
    messages.warning(
        request,
        (
            "Modalità dimostrativa attiva: database sandbox e cartella file dedicata "
            "(nuovi upload e cancellazioni non toccano l’archivio operativo; i PDF esistenti restano leggibili in sola lettura). "
            f"Per accessi dedicati usare anche l’utente «{_sandbox_demo_username_hint()}» (vedi gesper_sandbox_seed)."
        ),
    )
    logger.info("Sandbox session attivata (user_id=%s)", request.user.pk)
    return redirect(request.POST.get("next") or reverse("profile"))


@login_required
@require_POST
def sandbox_sessione_disattiva(request):
    """Torna al database operativo (default)."""
    if not getattr(settings, "GESPER_SANDBOX_ENABLED", False):
        return HttpResponseForbidden("Sandbox non abilitato in configurazione.")
    if not request.user.is_superuser:
        return HttpResponseForbidden("Solo i superuser possono disattivare la modalità dimostrativa.")
    request.session.pop("gesper_sandbox_attiva", None)
    request.session.modified = True
    messages.success(request, "Modalità dimostrativa disattivata: connessione al database operativo.")
    logger.info("Sandbox session disattivata (user_id=%s)", request.user.pk)
    return redirect(request.POST.get("next") or reverse("profile"))
