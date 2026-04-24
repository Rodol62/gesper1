"""
Risoluzione read-only del path `Documento.file` su storage, con le stesse
regole usate in ``documenti.views._documento_file_disponibile`` (senza scrivere sul DB).
"""

from __future__ import annotations

import os
import re

from .upload_paths import all_documento_storage_subdirs


def _norm_relpath(name: str) -> str:
    return (name or "").replace("\\", "/")


def stored_relpath_equivalent(a: str, b: str) -> bool:
    """Confronta due path relativo storage (slash normalizzati)."""
    return _norm_relpath(a) == _norm_relpath(b)


def first_existing_relpath_for_stored_name(storage, name: str) -> str | None:
    """
    Primo path relativo sotto ``storage`` dove il file esiste.
    Ritorna ``None`` se assente in tutte le posizioni note (healing / legacy subdir).
    """
    n = _norm_relpath(name)
    if not n:
        return None
    try:
        if storage.exists(n):
            return n
    except Exception:
        pass
    # Dopo migrazione layout piatto: path in DB ancora con prefisso documenti/
    if n.startswith("documenti/"):
        alt = n[len("documenti/") :]
        if alt:
            try:
                if storage.exists(alt):
                    return alt
            except Exception:
                pass

    try:
        base = os.path.basename(n)
        if not base:
            return None
        candidates: list[str] = []
        stem, ext = os.path.splitext(base)
        base_without_token = re.sub(r"_[A-Za-z0-9]{7}$", "", stem) + ext
        for cand in (
            f"documenti/{base}",
            f"buste_paghe/{base}",
            f"documenti/{base_without_token}" if base_without_token != base else "",
            f"buste_paghe/{base_without_token}" if base_without_token != base else "",
            base,
            base_without_token if base_without_token != base else "",
        ):
            if cand and cand != n and cand not in candidates:
                candidates.append(cand)
        for cand in candidates:
            try:
                if storage.exists(cand):
                    return cand
            except Exception:
                continue
    except Exception:
        pass

    try:
        base = os.path.basename(n)
        if not base:
            return None
        for sub in all_documento_storage_subdirs():
            sub = (sub or "").strip().strip("/")
            if not sub:
                continue
            rel = f"{sub}/{base}"
            if rel == n:
                continue
            try:
                if storage.exists(rel):
                    return rel
            except Exception:
                continue
    except Exception:
        pass
    return None
