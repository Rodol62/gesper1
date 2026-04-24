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
