"""
Instrada letture/scritture sul database ``sandbox`` quando la modalità dimostrativa è attiva sul thread.

I record applicativi creati in demo restano sul DB ``sandbox`` e non sul ``default``.
La tabella ``django_session`` resta sempre sul ``default`` (il middleware Session gira prima
del routing): evita letture/scritture sessione incoerenti e non sposta dati di business sul DB operativo.
"""

from __future__ import annotations

from typing import Any

from django.conf import settings

from .state import is_sandbox_routing


def _session_model(model: Any) -> bool:
    meta = getattr(model, "_meta", None)
    if meta is None:
        return False
    return meta.app_label == "sessions"


class SandboxRouter:
    """ORM applicativo: ``sandbox`` o ``default``; sessioni Django sempre ``default``."""

    def db_for_read(self, model: Any, **hints: Any) -> str:
        if _session_model(model):
            return "default"
        return self._alias()

    def db_for_write(self, model: Any, **hints: Any) -> str:
        if _session_model(model):
            return "default"
        return self._alias()

    def allow_relation(self, obj1: Any, obj2: Any, **hints: Any) -> bool | None:
        db1 = getattr(obj1._state, "db", None)
        db2 = getattr(obj2._state, "db", None)
        if db1 and db2 and db1 != db2:
            return False
        return None

    def allow_migrate(self, db: str, app_label: str, model_name: str | None = None, **hints: Any) -> bool | None:
        if db == "sandbox":
            return True
        if db == "default":
            return True
        return None

    def _alias(self) -> str:
        if not getattr(settings, "GESPER_SANDBOX_ENABLED", False):
            return "default"
        if "sandbox" not in getattr(settings, "DATABASES", {}):
            return "default"
        return "sandbox" if is_sandbox_routing() else "default"
