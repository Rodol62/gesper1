"""
Radici media ordinate per la sessione corrente (demo vs operativo).
"""

from __future__ import annotations

from pathlib import Path

from django.conf import settings

from sandbox_dimostrativo.state import is_sandbox_routing


def gesper_media_roots_for_read() -> tuple[Path, ...]:
    """
    Percorsi assoluti in cui cercare file (ordine: prima sandbox se attiva, poi operativo).
    """
    roots: list[Path] = []
    if is_sandbox_routing():
        sb = getattr(settings, "GESPER_SANDBOX_MEDIA_ROOT", None)
        if sb:
            roots.append(Path(str(sb)).resolve())
    roots.append(Path(str(settings.MEDIA_ROOT)).resolve())
    return tuple(roots)


def gesper_media_root_write() -> Path:
    """Radice usata per nuovi upload (solo sandbox in sessione demo, altrimenti MEDIA_ROOT)."""
    if is_sandbox_routing():
        sb = getattr(settings, "GESPER_SANDBOX_MEDIA_ROOT", None)
        if sb:
            return Path(str(sb)).resolve()
    return Path(str(settings.MEDIA_ROOT)).resolve()


def media_path_is_under_sandbox_writable(path: Path) -> bool:
    """
    True se ``path`` è sotto ``GESPER_SANDBOX_MEDIA_ROOT`` (unica area cancellabile in demo).

    Fuori dalla sessione sandbox restituisce sempre True (nessun vincolo aggiuntivo).
    """
    if not is_sandbox_routing():
        return True
    sb = getattr(settings, "GESPER_SANDBOX_MEDIA_ROOT", None)
    if not sb:
        return True
    try:
        path.resolve().relative_to(Path(str(sb)).resolve())
        return True
    except ValueError:
        return False
