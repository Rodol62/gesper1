"""
Password da provare per aprire i PDF busta paga (cifrati dallo studio / TeamSystem).

Ordine: prima la password «studio» tipica, poi eventuali valori in
``settings.BUSTE_PAGA_PDF_PASSWORDS``, infine stringa vuota (PDF non protetti).
Provare la password prima del vuoto evita fallimenti su molti PDF cifrati.
"""

from __future__ import annotations

STUDIO_DEFAULT_PASSWORD = "DOLCEMASCOLO"


def passwords_for_busta_pdf_read() -> list[str]:
    try:
        from django.conf import settings

        extra = getattr(settings, "BUSTE_PAGA_PDF_PASSWORDS", None)
    except Exception:
        extra = None
    if not isinstance(extra, (list, tuple)):
        extra = []
    extras = [str(x).strip() for x in extra if str(x).strip()]
    seen: set[str] = set()
    out: list[str] = []
    for p in (STUDIO_DEFAULT_PASSWORD, *extras, ""):
        if p not in seen:
            seen.add(p)
            out.append(p)
    return out
