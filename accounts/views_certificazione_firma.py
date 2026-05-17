"""Invio link certificazione firma (admin) e conferma pubblica (firmato)."""

from __future__ import annotations

import logging

from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required, user_passes_test
from django.http import Http404, HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from django.core.signing import BadSignature, SignatureExpired

from accounts.certificazione_firma import (
    costruisci_url_certificazione,
    crea_token_certificazione_firma,
    decodifica_token_certificazione_firma,
    invia_email_certificazione_firma,
)
from accounts.models import ConfigurazioneSistema
from accounts.views_admin_candidati import _candidato_gestionabile_da_richiedente, _is_hr_or_admin
from anagrafiche.models import Dipendente
from log_attivita.utils import registra_log

logger = logging.getLogger(__name__)
User = get_user_model()


def _dipendente_hr_access(request, dip: Dipendente) -> bool:
    u = request.user
    if u.is_superuser or u.has_ruolo("admin"):
        from accounts.tenant import get_azienda_operativa

        az = get_azienda_operativa(u, request.session)
        return az is None or dip.azienda_id == az.id
    if u.has_ruolo("hr"):
        return getattr(u, "azienda_id", None) == dip.azienda_id
    return False


@login_required
@user_passes_test(_is_hr_or_admin)
@require_POST
def invia_certificazione_firma_candidato(request, user_id):
    candidato = get_object_or_404(User, pk=user_id)
    if not candidato.is_candidato_portale():
        raise Http404()
    if not _candidato_gestionabile_da_richiedente(request, candidato):
        raise Http404()
    profilo = getattr(candidato, "profilo_candidato", None)
    dip_id = profilo.dipendente_id if profilo else None
    token = crea_token_certificazione_firma("candidato", candidato.pk, dip_id)
    url = costruisci_url_certificazione(request, token)
    nome_sito = ConfigurazioneSistema.get().nome_sito or "GESPER"
    try:
        invia_email_certificazione_firma(
            destinatario_email=candidato.email,
            nome_destinatario=f"{candidato.first_name} {candidato.last_name}".strip() or candidato.username,
            url_cert=url,
            nome_sito=nome_sito,
        )
    except Exception as exc:
        logger.exception("[CERT_FIRMA] email candidato %s: %s", user_id, exc)
        messages.error(request, f"Invio e-mail non riuscito: {exc}")
        return redirect("candidato_admin_dettaglio", user_id=user_id)

    messages.success(request, "Link di certificazione inviato via e-mail.")

    registra_log(
        request.user,
        getattr(profilo, "azienda_interesse", None) if profilo else None,
        "altro",
        descrizione=f"Invio link certificazione firma a candidato {candidato.username}",
        oggetto_id=str(user_id),
        request=request,
    )
    return redirect("candidato_admin_dettaglio", user_id=user_id)


@login_required
@user_passes_test(_is_hr_or_admin)
@require_POST
def invia_certificazione_firma_dipendente(request, pk):
    dip = get_object_or_404(Dipendente.objects.select_related("azienda", "utente"), pk=pk)
    if not _dipendente_hr_access(request, dip):
        return HttpResponseForbidden("Accesso negato")
    user = dip.utente
    if not user or not user.email:
        messages.error(request, "Nessun utente collegato al dipendente o e-mail mancante.")
        return redirect("dettaglio_dipendente", pk=pk)
    token = crea_token_certificazione_firma("dipendente", user.pk, dip.pk)
    url = costruisci_url_certificazione(request, token)
    nome_sito = ConfigurazioneSistema.get().nome_sito or "GESPER"
    try:
        invia_email_certificazione_firma(
            destinatario_email=user.email,
            nome_destinatario=f"{user.first_name} {user.last_name}".strip() or user.username,
            url_cert=url,
            nome_sito=nome_sito,
        )
    except Exception as exc:
        logger.exception("[CERT_FIRMA] email dipendente %s: %s", pk, exc)
        messages.error(request, f"Invio e-mail non riuscito: {exc}")
        return redirect("dettaglio_dipendente", pk=pk)

    messages.success(request, "Link di certificazione inviato via e-mail.")

    registra_log(
        request.user,
        dip.azienda,
        "altro",
        descrizione=f"Invio link certificazione firma a dipendente {dip.pk} ({dip})",
        oggetto_id=str(pk),
        request=request,
    )
    return redirect("dettaglio_dipendente", pk=pk)


def certificazione_firma_pubblica(request, token: str):
    """Pagina pubblica (solo token) per confermare la ricezione del link di certificazione."""
    try:
        kind, uid, did = decodifica_token_certificazione_firma(token)
    except SignatureExpired:
        return render(
            request,
            "accounts/certificazione_firma_esito.html",
            {"ok": False, "titolo": "Link scaduto", "messaggio": "Richiedi un nuovo invio all'ufficio HR."},
            status=410,
        )
    except (BadSignature, ValueError):
        raise Http404()

    user = get_object_or_404(User, pk=uid)
    nome = f"{user.first_name} {user.last_name}".strip() or user.username

    if request.method == "POST":
        registra_log(
            user,
            getattr(user, "azienda", None),
            "altro",
            descrizione=f"Conferma certificazione firma ({kind}) da link firmato",
            oggetto_id=str(did or uid),
            request=request,
        )
        return render(
            request,
            "accounts/certificazione_firma_esito.html",
            {
                "ok": True,
                "titolo": "Grazie",
                "messaggio": "La conferma è stata registrata. Puoi chiudere questa pagina.",
            },
        )

    return render(
        request,
        "accounts/certificazione_firma_conferma.html",
        {"nome": nome, "kind": kind, "token": token},
    )
