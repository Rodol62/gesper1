"""
Filtri template per il modulo Presenze.

Regola fondamentale del progetto GESPER:
  - Le ORE sono SEMPRE memorizzate e calcolate in formato DECIMALE (es. 8.5 = 8h30)
  - La visualizzazione usa HH:MM  (es. 8:30)
  - Mai mescolare i due formati in calcoli

Conversione:  ore_dec_to_hhmm(8.5)  → '8:30'
              ore_dec_to_hhmm(8.75) → '8:45'
              ore_dec_to_hhmm(0.25) → '0:15'
"""
from django import template

register = template.Library()

_GIORNI_ITA = ['Lun', 'Mar', 'Mer', 'Gio', 'Ven', 'Sab', 'Dom']


@register.filter(name='giorno_ita')
def giorno_ita(d):
    """Restituisce il nome del giorno in italiano da un oggetto date.
    Uso: {{ data|giorno_ita }}  →  'Lun' … 'Dom'
    """
    try:
        return _GIORNI_ITA[d.weekday()]
    except (AttributeError, IndexError):
        return ''


def ore_dec_to_hhmm(decimal_ore) -> str:
    """
    Converte ore decimali in stringa HH:MM.
    8.5 → '8:30' | 8.25 → '8:15' | 0.0 → '0:00'
    Funzione standalone usabile anche fuori dai template.
    """
    if decimal_ore is None:
        return '0:00'
    try:
        total_min = round(float(decimal_ore) * 60)
        if total_min < 0:
            return '0:00'
        h = total_min // 60
        m = total_min % 60
        return f'{h}:{m:02d}'
    except (ValueError, TypeError):
        return '0:00'


@register.filter(name='ore_hhmm')
def ore_hhmm(decimal_ore):
    """
    Filtro template: ore decimali → HH:MM
    Uso: {{ valore_decimale|ore_hhmm }}  →  '8:30'
    """
    return ore_dec_to_hhmm(decimal_ore)


@register.filter(name='ore_hhmm_h')
def ore_hhmm_h(decimal_ore):
    """
    Filtro template: ore decimali → HH:MMh  (con suffisso 'h')
    Uso: {{ valore_decimale|ore_hhmm_h }}  →  '8:30h'
    """
    val = ore_dec_to_hhmm(decimal_ore)
    if val == '0:00':
        return '—'
    return val + 'h'


@register.filter(name='ore_hhmm_h0')
def ore_hhmm_h0(decimal_ore):
    """
    Come ore_hhmm_h ma mostra '0:00h' invece di '—' per i valori zero.
    Utile nelle celle di riepilogo dove occorre sempre un valore.
    """
    return ore_dec_to_hhmm(decimal_ore) + 'h'
