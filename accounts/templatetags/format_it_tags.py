"""
Filtri template per formati italiani coerenti con settings (migliaia . decimali ,).

- num_it: numero con separatore migliaia e cifre decimali richieste
- euro_it: come num_it a 2 decimali (simbolo € nel template: «€ {{ v|euro_it }}»)
- it_date: data in gg/mm/aaaa (alias esplicito a date_format locale)
- anno_it: anno calendario a 4 cifre (aaaa), senza separatore migliaia — per etichette, query string e URL
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation

from django import template
from django.utils.formats import date_format

from accounts.formatting import normalize_anno_calendario, num_it_str

register = template.Library()


@register.filter(name='dict_get')
def dict_get(mapping, key):
    """Lookup su dict (es. mappa id campo layout → valore già risolto)."""
    if not isinstance(mapping, dict):
        return None
    return mapping.get(key)


@register.filter(name='abs_num')
def abs_num(value):
    """Valore assoluto numerico (per mostrare importi con segno gestito a mano nel template)."""
    if value is None or value == '':
        return value
    try:
        return abs(Decimal(str(value)))
    except (InvalidOperation, TypeError, ValueError):
        return value


@register.filter(name='num_it')
def num_it(value, decimal_pos=2):
    """Numero localizzato (migliaia ., decimali ,), allineato a accounts.formatting.num_it_str."""
    if value is None or value == '':
        return '—'
    try:
        pos = int(decimal_pos)
    except (TypeError, ValueError):
        pos = 2
    out = num_it_str(value, pos)
    return out if out else '—'


@register.filter(name='euro_it')
def euro_it(value):
    """Importo con 2 decimali e separatori italiani (senza simbolo)."""
    if value is None or value == '':
        return '—'
    out = num_it_str(value, 2)
    return out if out else '—'


@register.filter(name='it_date')
def it_date(value, fmt='d/m/Y'):
    """Data in formato italiano (default gg/mm/aaaa)."""
    if not value:
        return ''
    return date_format(value, fmt)


@register.filter(name='anno_it')
def anno_it(value):
    """
    Anno calendario su 4 cifre (aaaa), senza separatore migliaia (USE_L10N).
    Accetta date/datetime, int, float (es. 2026.0), o stringa ('2026', '2.026' da locale/query).
    """
    if value is None or value == '':
        return ''
    if hasattr(value, 'year'):
        return str(int(value.year))
    if isinstance(value, bool):
        return str(value)
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    s = str(value).strip().replace(' ', '')
    out = normalize_anno_calendario(s)
    return out if out else s
