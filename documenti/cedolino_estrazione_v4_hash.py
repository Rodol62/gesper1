"""SHA-256 del file PDF per tracciabilità estrazione v4."""

from __future__ import annotations

import hashlib

from documenti.models import Documento


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def pdf_sha256_per_documento(
    documento: Documento | None, pdf_raw_bytes: bytes | None
) -> str:
    if pdf_raw_bytes:
        return sha256_bytes(pdf_raw_bytes)
    if documento and getattr(documento, "file", None):
        try:
            with documento.file.open("rb") as fh:
                return sha256_bytes(fh.read())
        except Exception:
            return ""
    return ""
