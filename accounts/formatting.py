"""Formato numeri/importi per Python (CSV, Admin, PDF helper) — allineato ai filtri template euro_it / num_it."""
from __future__ import annotations

from decimal import Decimal, InvalidOperation

from django.utils.formats import number_format


def num_it_str(value, decimal_pos: int = 2) -> str:
    if value is None or value == '':
        return ''
    try:
        pos = int(decimal_pos)
    except (TypeError, ValueError):
        pos = 2
    try:
        return number_format(value, decimal_pos=pos, use_l10n=True, force_grouping=True)
    except (TypeError, ValueError):
        return str(value)


def euro_it_str(value) -> str:
    return num_it_str(value, 2)


def abs_decimal(value):
    if value is None or value == '':
        return value
    try:
        return abs(Decimal(str(value)))
    except (InvalidOperation, TypeError, ValueError):
        return value


def normalize_anno_calendario(value) -> str:
    """
    Anno su 4 cifre senza separatore migliaia (per query string / POST).

    Con USE_THOUSAND_SEPARATOR i template possono emettere «2.026» invece di «2026»;
    questa funzione ripristina «2026». Stringa vuota se non interpretabile.
    """
    if value is None or value == '':
        return ''
    if hasattr(value, 'year'):
        return str(int(value.year))
    if isinstance(value, bool):
        return ''
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    s = str(value).strip().replace(' ', '')
    collapsed = s.replace('.', '').replace(',', '')
    if collapsed.isdigit():
        return str(int(collapsed))
    try:
        return str(int(value))
    except (TypeError, ValueError):
        return ''
