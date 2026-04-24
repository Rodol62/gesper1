"""
Template tags personalizzati per la formattazione di valori monetari e ore.
"""
from django import template

from accounts.formatting import num_it_str

register = template.Library()


@register.filter(name='euro')
def euro(value, decimali=2):
    """
    Formatta un valore numerico come importo (solo cifre, senza simbolo €) in formato italiano,
    allineato a euro_it / num_it (migliaia ., decimali ,).

    Usage: {{ valore|euro }} oppure {{ valore|euro:4 }}
    """
    if value is None or value == '':
        return '—'
    try:
        pos = int(decimali)
    except (TypeError, ValueError):
        pos = 2
    out = num_it_str(value, pos)
    return out if out else '—'


@register.filter(name='euro_full')
def euro_full(value, decimali=2):
    """
    Formatta un valore numerico come importo in euro con simbolo € e formato italiano.
    Esempio: 1234.56 -> € 1.234,56
    SEMPRE con 2 decimali per importi monetari.
    
    Usage: {{ valore|euro_full }}
    """
    # Ignora il parametro decimali, usa sempre 2 per euro
    formatted = euro(value, 2)
    return f'€ {formatted}'


@register.filter(name='ore_hm')
def ore_hm(value):
    """
    Converte ore decimali nel formato H:MM (ore e minuti interi).
    Esempi: 5.7143 → 5:43 | 22.857 → 22:51 | 6.6667 → 6:40

    Usage: {{ ore_value|ore_hm }}
    """
    if value is None or value == '':
        return '0:00'
    try:
        ore_dec = float(value)
        ore_int = int(ore_dec)
        minuti  = round((ore_dec - ore_int) * 60)
        if minuti == 60:
            ore_int += 1
            minuti = 0
        return f'{ore_int}:{minuti:02d}'
    except (ValueError, TypeError):
        return str(value)


@register.filter(name='get_item')
def get_item(value, key):
    """Restituisce value[key] per dict/list in template, altrimenti None."""
    try:
        if value is None:
            return None
        return value.get(key) if hasattr(value, 'get') else value[key]
    except Exception:
        return None
