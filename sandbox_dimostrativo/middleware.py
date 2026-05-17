"""
Instrada letture/scritture ORM sul DB ``sandbox`` **prima** di ``AuthenticationMiddleware``,
così ``get_user`` / ``authenticate`` non interrogano mai l'operativo per sessioni dimostrative.
"""

from __future__ import annotations

from typing import Callable

from django.conf import settings

from .login_routing import session_requests_sandbox_db
from .state import set_sandbox_routing


class SandboxDimostrativoMiddleware:
    """Prima dell'autenticazione: sessione demo / login dimostrativo → DB ``sandbox``."""

    def __init__(self, get_response: Callable) -> None:
        self.get_response = get_response

    def __call__(self, request):
        if not getattr(settings, "GESPER_SANDBOX_ENABLED", False):
            request.gesper_sandbox = False
            return self.get_response(request)

        use_sandbox = session_requests_sandbox_db(request)

        set_sandbox_routing(use_sandbox)
        request.gesper_sandbox = use_sandbox
        try:
            return self.get_response(request)
        finally:
            set_sandbox_routing(False)
