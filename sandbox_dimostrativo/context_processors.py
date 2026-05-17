"""Bandierina UI per la modalità dimostrativa."""

from __future__ import annotations

from typing import Any

from django.conf import settings


def gesper_sandbox_banner(request: Any) -> dict[str, Any]:
    _names = getattr(settings, "GESPER_SANDBOX_USERNAMES", frozenset())
    _hint = min(_names) if _names else "demo"
    return {
        "gesper_sandbox_attiva": bool(getattr(request, "gesper_sandbox", False)),
        "gesper_sandbox_abilitato": bool(getattr(settings, "GESPER_SANDBOX_ENABLED", False)),
        "gesper_sandbox_demo_username_hint": _hint,
    }
