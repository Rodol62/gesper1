"""
Percorsi relativi a ``MEDIA_ROOT`` per :class:`documenti.models.Documento`.

La sottocartella dipende da ``Documento.tipo`` tramite ``settings.DOCUMENTO_TIPO_MEDIA_SUBDIRS``
(layout piatto sotto MEDIA, es. ``buste_paghe/``);
i tipi non elencati usano ``settings.DOCUMENTI_MEDIA_SUBDIR`` (default ``varie``).
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from django.conf import settings
from django.db.models import Q
from django.utils.text import get_valid_filename

logger = logging.getLogger(__name__)


def _normalized_subdir(name: str) -> str:
    s = (name or "").strip().strip("/")
    return s or "varie"


def subdir_for_documento_tipo(tipo: str | None) -> str:
    """Cartella (solo nome, senza slash) per un dato ``tipo`` documento."""
    t = (tipo or "").strip()
    mapping = getattr(settings, "DOCUMENTO_TIPO_MEDIA_SUBDIRS", None) or {}
    if t in mapping:
        return _normalized_subdir(str(mapping[t]))
    return _normalized_subdir(getattr(settings, "DOCUMENTI_MEDIA_SUBDIR", "varie"))


def documento_file_upload_to(instance, filename: str) -> str:
    """``upload_to`` per ``Documento.file``."""
    safe = get_valid_filename(os.path.basename(filename) or "file.bin")
    sub = subdir_for_documento_tipo(getattr(instance, "tipo", None))
    return f"{sub}/{safe}"


def busta_paga_file_path_prefixes() -> tuple[str, ...]:
    """
    Prefissi path file (sotto MEDIA_ROOT) considerati «busta paga» in lista, import e batch.

    Include il percorso configurato per ``busta_paga`` e cartelle legacy ancora supportate
    per compatibilità (file già salvati prima del cambio cartella).
    """
    main = _normalized_subdir(subdir_for_documento_tipo("busta_paga")).strip("/") + "/"
    out: list[str] = []
    seen: set[str] = set()
    for p in (main, "documenti/", "Liquidazioni_mensili/", "buste_paghe/", "f24/", "F24/"):
        if p not in seen:
            seen.add(p)
            out.append(p)
    return tuple(out)


def busta_paga_storage_q(prefix: str = "file") -> Q:
    """Q per OR di ``{prefix}__startswith`` sulle cartelle busta ammesse."""
    q = Q()
    for pref in busta_paga_file_path_prefixes():
        q |= Q(**{f"{prefix}__startswith": pref})
    return q


def ensure_documenti_media_subdirs() -> None:
    """
    Crea sotto MEDIA_ROOT le cartelle usate da ``documento_file_upload_to``
    (es. ``contratti/`` sotto ``MEDIA_ROOT``), così il primo salvataggio non dipende solo
    da ``FileField`` e si evitano errori se la cartella manca o i permessi sono stretti.
    """
    root = getattr(settings, "MEDIA_ROOT", None)
    if not root:
        return
    root_path = Path(str(root))
    try:
        if not root_path.is_dir():
            root_path.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        logger.warning("MEDIA_ROOT non utilizzabile (%s): %s", root_path, exc)
        return

    mapping = getattr(settings, "DOCUMENTO_TIPO_MEDIA_SUBDIRS", None) or {}
    names: set[str] = set()
    for v in mapping.values():
        names.add(_normalized_subdir(str(v)))
    names.add(_normalized_subdir(getattr(settings, "DOCUMENTI_MEDIA_SUBDIR", "varie")))
    for rel in sorted(names):
        if not rel:
            continue
        target = root_path / rel
        try:
            target.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            logger.warning("Cartella media non creabile (%s): %s", target, exc)

    arch = getattr(settings, "GESPER_ARCHIVIO_ROOT", None)
    if arch:
        try:
            Path(str(arch)).mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            logger.warning("Cartella archivio non creabile (%s): %s", arch, exc)

    sb_root = getattr(settings, "GESPER_SANDBOX_MEDIA_ROOT", None)
    if sb_root:
        sb_path = Path(str(sb_root))
        try:
            if not sb_path.is_dir():
                sb_path.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            logger.warning("GESPER_SANDBOX_MEDIA_ROOT non utilizzabile (%s): %s", sb_path, exc)
        else:
            for rel in sorted(names):
                if not rel:
                    continue
                target = sb_path / rel
                try:
                    target.mkdir(parents=True, exist_ok=True)
                except OSError as exc:
                    logger.warning("Cartella media sandbox non creabile (%s): %s", target, exc)


def all_documento_storage_subdirs() -> list[str]:
    """
    Elenco sottocartelle da provare per URL legacy (solo nome file) e compatibilità percorsi vecchi.
    """
    mapping = getattr(settings, "DOCUMENTO_TIPO_MEDIA_SUBDIRS", None) or {}
    seen: set[str] = set()
    out: list[str] = []
    for v in mapping.values():
        x = _normalized_subdir(str(v))
        if x not in seen:
            seen.add(x)
            out.append(x)
    default = _normalized_subdir(getattr(settings, "DOCUMENTI_MEDIA_SUBDIR", "varie"))
    if default not in seen:
        out.append(default)
        seen.add(default)
    for legacy in (
        "Liquidazioni_mensili",
        "buste_paghe",
        "documenti",
        "f24",
        "F24",
    ):
        if legacy not in seen:
            out.append(legacy)
            seen.add(legacy)
    # Compat: path sotto documenti/ ancora in DB
    for v in list(mapping.values()):
        s = (v or "").strip().strip("/")
        if s and not s.startswith("documenti/"):
            leg = f"documenti/{s}"
            if leg not in seen:
                out.append(leg)
                seen.add(leg)
    return out
