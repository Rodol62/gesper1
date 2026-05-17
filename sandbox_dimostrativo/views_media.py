"""
Servizio file ``/media/`` in DEBUG con più radici (sandbox + operativo).

In produzione i file statici/media sono serviti da Nginx: configurare due alias
o ``try_files`` se si espone la demo sulla stessa URL (vedi commento in settings).
"""

from __future__ import annotations

import mimetypes
from pathlib import Path

from django.conf import settings
from django.http import FileResponse, Http404
from django.utils._os import safe_join

from sandbox_dimostrativo.media_paths import gesper_media_roots_for_read


def serve_debug_media_multiroot(request, path: str):
    """Restituisce il primo file trovato tra le radici configurate per la sessione."""
    rel = (path or "").replace("\\", "/").strip().lstrip("/")
    if not rel or ".." in rel.split("/"):
        raise Http404()
    for root in gesper_media_roots_for_read():
        if not root.is_dir():
            continue
        try:
            full = Path(safe_join(str(root), rel)).resolve()
        except (ValueError, OSError):
            continue
        try:
            full.relative_to(root)
        except ValueError:
            continue
        if full.is_file():
            ctype, _enc = mimetypes.guess_type(str(full))
            return FileResponse(open(full, "rb"), content_type=ctype or "application/octet-stream")
    raise Http404()
