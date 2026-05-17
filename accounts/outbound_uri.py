# -*- coding: utf-8 -*-
"""URL assoluti per e-mail: richiesta reale o ``url_pubblica_base`` in sviluppo su localhost."""
from __future__ import annotations

from urllib.parse import urlsplit

from accounts.models import ConfigurazioneSistema


def _is_local_dev_host(host: str) -> bool:
    """Host tipici di sviluppo locale (link e-mail devono usare ``url_pubblica_base``)."""
    h = (host or "").lower().strip()
    if not h:
        return False
    if h.startswith("127.0.0.1") or h.startswith("localhost") or h.startswith("[::1]"):
        return True
    # Hostname Docker / rete interna senza DNS pubblico
    if h.endswith(".local") or h.endswith(".internal"):
        return True
    return False


def outbound_absolute_uri(request, path: str) -> str:
    """
    ``path`` di solito da ``reverse()``; include il prefisso script (es. ``/gesper/``) se configurato.
    Con host localhost/127.0.0.1 e ``url_pubblica_base`` valorizzata, antepone la base pubblica.
    """
    cfg = ConfigurazioneSistema.get()
    base_cfg = (cfg.url_pubblica_base or "").strip().rstrip("/")
    p = (path or "").strip()
    if not p.startswith("/"):
        p = "/" + p
    host = (request.get_host() or "").lower()
    if base_cfg and _is_local_dev_host(host):
        raw = base_cfg if "://" in base_cfg else f"https://{base_cfg.lstrip('/')}"
        return f"{raw.rstrip('/')}{p}"
    return request.build_absolute_uri(p)


def outbound_email_scheme_and_netloc(request) -> tuple[str, str]:
    """
    Scheme e host (``netloc`` include la porta se non standard) per template tipo
    ``{{ protocol }}://{{ domain }}`` + path da ``{% url %}``.
    """
    cfg = ConfigurazioneSistema.get()
    base_cfg = (cfg.url_pubblica_base or "").strip().rstrip("/")
    host = (request.get_host() or "").lower()
    if base_cfg and _is_local_dev_host(host):
        raw = base_cfg if "://" in base_cfg else f"https://{base_cfg.lstrip('/')}"
        parsed = urlsplit(raw)
        scheme = parsed.scheme or "https"
        netloc = parsed.netloc or host
        return scheme, netloc
    parsed = urlsplit(request.build_absolute_uri("/"))
    scheme = parsed.scheme or ("https" if request.is_secure() else "http")
    return scheme, parsed.netloc
