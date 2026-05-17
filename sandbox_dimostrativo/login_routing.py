"""
Rilevamento utenti dimostrativi e percorsi login per il routing DB ``sandbox`` (pre-auth).
"""

from __future__ import annotations

from django.conf import settings

SESSION_AUTH_SANDBOX = "gesper_auth_sandbox"
SESSION_LOGIN_PENDING_SANDBOX = "gesper_login_pending_sandbox"
SESSION_API_2FA_SANDBOX = "gesper_api_2fa_sandbox"


def sandbox_demo_usernames_lower() -> frozenset[str]:
    return frozenset(
        x.strip().lower()
        for x in getattr(settings, "GESPER_SANDBOX_USERNAMES", frozenset())
        if x.strip()
    )


def is_sandbox_demo_username(username: str | None) -> bool:
    if not username:
        return False
    return username.strip().lower() in sandbox_demo_usernames_lower()


def is_sandbox_demo_login_identifier(raw: str | None) -> bool:
    """
    True se l'input è uno username dimostrativo oppure l'e-mail seed ``{user}@invalid.local``.
    Usato per il routing DB sandbox sul POST login (prima dell'autenticazione).
    """
    if not raw:
        return False
    s = raw.strip()
    if is_sandbox_demo_username(s):
        return True
    low = s.lower()
    if "@" in low and low.endswith("@invalid.local"):
        local = low.split("@", 1)[0]
        return local in sandbox_demo_usernames_lower()
    return False


def resolve_sandbox_demo_username_for_auth(raw: str) -> str:
    """
    Username da passare a ``authenticate`` (come nel DB sandbox / seed).
    Accetta varianti di maiuscole, ``demo@invalid.local``, ecc.
    """
    s = raw.strip()
    if is_sandbox_demo_username(s):
        return canonical_sandbox_demo_username(s)
    low = s.lower()
    if "@" in low and low.endswith("@invalid.local"):
        local = low.split("@", 1)[0]
        if local in sandbox_demo_usernames_lower():
            names = getattr(settings, "GESPER_SANDBOX_USERNAMES", frozenset())
            for n in names:
                if n.lower() == local:
                    return n
            return local
    return s


def canonical_sandbox_demo_username(username: str) -> str:
    """
    Username dimostrativo come in DB (allineato a ``gesper_sandbox_seed``: ``min(GESPER_SANDBOX_USERNAMES)``).

    Il middleware attiva il sandbox in modo case-insensitive; ``authenticate`` invece usa l'username
    esatto del modello: senza questa normalizzazione, es. «Demo» vs «demo» fallisce sempre.
    """
    if not is_sandbox_demo_username(username):
        return username.strip()
    names = getattr(settings, "GESPER_SANDBOX_USERNAMES", frozenset())
    if names:
        return min(names)
    return username.strip().lower()


def accounts_login_post_uses_sandbox_demo(request) -> bool:
    """True sul POST ``/accounts/login/`` con username dimostrativo (password già verificata dal form)."""
    if request.method != "POST":
        return False
    pi = (request.path_info or "").rstrip("/") or "/"
    # Supporto URL con prefisso (es. ``/gesper/accounts/login`` in alcuni deploy).
    if not (pi == "/accounts/login" or pi.endswith("/accounts/login")):
        return False
    return is_sandbox_demo_login_identifier(request.POST.get("username"))


def session_requests_sandbox_db(request) -> bool:
    """Sessione che deve usare il DB sandbox (prima di ``request.user`` caricato)."""
    if not getattr(settings, "GESPER_SANDBOX_ENABLED", False):
        return False
    s = request.session
    if s.get(SESSION_AUTH_SANDBOX):
        return True
    if s.get("gesper_sandbox_attiva"):
        return True
    if s.get(SESSION_LOGIN_PENDING_SANDBOX):
        return True
    if s.get(SESSION_API_2FA_SANDBOX):
        return True
    if accounts_login_post_uses_sandbox_demo(request):
        return True
    return False
