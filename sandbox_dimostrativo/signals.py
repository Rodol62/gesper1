"""
Sessione post-login: marca l'uso del DB sandbox per gli utenti dimostrativi.
"""

from __future__ import annotations

import logging

from django.conf import settings
from django.contrib.auth.signals import user_logged_in, user_logged_out
from django.dispatch import receiver

from sandbox_dimostrativo.login_routing import (
    SESSION_API_2FA_SANDBOX,
    SESSION_AUTH_SANDBOX,
    SESSION_LOGIN_PENDING_SANDBOX,
    is_sandbox_demo_username,
)

logger = logging.getLogger(__name__)


def _clear_sandbox_session_markers(session) -> None:
    session.pop(SESSION_AUTH_SANDBOX, None)
    session.pop(SESSION_LOGIN_PENDING_SANDBOX, None)
    session.pop(SESSION_API_2FA_SANDBOX, None)
    session.pop("gesper_sandbox_attiva", None)


@receiver(user_logged_in)
def gesper_sandbox_on_login(sender, request, user, **kwargs) -> None:
    if not getattr(settings, "GESPER_SANDBOX_ENABLED", False):
        return
    if is_sandbox_demo_username(user.get_username()):
        request.session[SESSION_AUTH_SANDBOX] = True
        request.session.modified = True
        logger.info("Sessione sandbox attiva (username dimostrativo).")
        # Dopo clone + seed, ``demo`` è su un'azienda reale ma la sessione può restare sulla P.IVA fittizia
        # → liste dipendenti/documenti vuote (filtro per ``get_azienda_operativa``).
        _azienda_fittizia_piva = "SANDBOX00001"
        uid = getattr(user, "azienda_id", None)
        if uid:
            from anagrafiche.models import Azienda

            az_user = Azienda.objects.filter(pk=uid).first()
            if az_user and (az_user.partita_iva or "").strip().upper() != _azienda_fittizia_piva:
                sid = request.session.get("azienda_id") or request.session.get("azienda_operativa_id")
                if sid is not None and str(sid).strip().isdigit():
                    az_sess = Azienda.objects.filter(pk=int(str(sid).strip())).first()
                    if az_sess and (az_sess.partita_iva or "").strip().upper() == _azienda_fittizia_piva:
                        request.session.pop("azienda_id", None)
                        request.session.pop("azienda_operativa_id", None)
                        request.session.modified = True
                        logger.info(
                            "Sessione: rimossi riferimenti all'azienda dimostrativa fittizia "
                            "(allineamento utente demo a dati clonati)."
                        )
    request.session.pop(SESSION_LOGIN_PENDING_SANDBOX, None)
    request.session.pop(SESSION_API_2FA_SANDBOX, None)


@receiver(user_logged_out)
def gesper_sandbox_on_logout(sender, request, user, **kwargs) -> None:
    if not getattr(settings, "GESPER_SANDBOX_ENABLED", False):
        return
    if request is not None and hasattr(request, "session"):
        _clear_sandbox_session_markers(request.session)
        request.session.modified = True
