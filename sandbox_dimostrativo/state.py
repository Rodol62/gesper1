"""
Stato richiesta corrente: se True, il router instrada tutte le query ORM sul database ``sandbox``.
"""

from __future__ import annotations

import threading

_tls = threading.local()


def set_sandbox_routing(active: bool) -> None:
    """Attiva/disattiva l'uso del DB sandbox per il thread della richiesta HTTP."""
    _tls.sandbox = bool(active)


def is_sandbox_routing() -> bool:
    return bool(getattr(_tls, "sandbox", False))
