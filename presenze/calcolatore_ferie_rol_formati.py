"""
Formati di esposizione per il motore «Calcolatore ferie e ROL»:
- ore in HH:MM per interfaccia
- ore in decimale (stringa con virgola) per report al consulente del lavoro
- equivalenza giorni ferie → ore (default 8 h/giorno lavorativo) per HH:MM
"""
from __future__ import annotations

from decimal import Decimal


def ore_decimali_a_hhmm(ore: float | Decimal | None) -> str:
    """Ore decimali (es. 7.5) → '7:30'."""
    if ore is None:
        return '0:00'
    try:
        x = float(ore)
    except (TypeError, ValueError):
        return '0:00'
    if x <= 0:
        return '0:00'
    total_min = int(round(x * 60))
    h, m = divmod(total_min, 60)
    return f'{h}:{m:02d}'


def giorni_ferie_a_ore_equivalenti(gg: float | Decimal | None, ore_per_giorno_lavorativo: float = 8.0) -> float:
    """Converte giorni di ferie (anche frazionari) in ore equivalenti per HH:MM."""
    if gg is None:
        return 0.0
    try:
        return float(gg) * float(ore_per_giorno_lavorativo)
    except (TypeError, ValueError):
        return 0.0


def giorni_ferie_a_hhmm(gg: float | Decimal | None, ore_per_giorno_lavorativo: float = 8.0) -> str:
    """Giorni ferie → equivalente HH:MM (× ore/giorno)."""
    return ore_decimali_a_hhmm(giorni_ferie_a_ore_equivalenti(gg, ore_per_giorno_lavorativo))


def ore_per_report_consulente(ore: float | Decimal | None, decimali: int = 4) -> str:
    """
    Ore in formato decimale con virgola (uso tipico export / consulente del lavoro).
    Esempio: 7.5 → '7,5000'
    """
    if ore is None:
        return '0,' + '0' * decimali
    try:
        d = Decimal(str(ore))
    except Exception:
        return '0,' + '0' * decimali
    s = f'{d:.{decimali}f}'.replace('.', ',')
    return s
