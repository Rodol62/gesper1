from __future__ import annotations

from typing import Optional

from .utils import registra_log


def build_anomalia_import_export(*, action: Optional[str] = None, periodo: str = '', dipendente_presente: bool = True,
                                movimento_presente: bool = True, netto_raw: Optional[str] = None) -> Optional[dict]:
    """Classifica in modo centralizzato le anomalie import/export."""
    if action == 'ambiguous':
        return {'codice': 'MATCH_AMBIGUO', 'livello': 'errore', 'messaggio': 'Match dipendente ambiguo (omonimia)'}
    if action == 'already_present':
        return {'codice': 'DUPLICATO', 'livello': 'warning', 'messaggio': 'Movimento già presente (stessa chiave logica)'}
    if not periodo or periodo == '-':
        return {'codice': 'PERIODO_MANCANTE', 'livello': 'warning', 'messaggio': 'Periodo non riconosciuto'}
    if not dipendente_presente:
        return {'codice': 'DIPENDENTE_NON_ASSOCIATO', 'livello': 'warning', 'messaggio': 'Dipendente non associato automaticamente'}
    if not movimento_presente:
        if not netto_raw:
            return {'codice': 'NETTO_NON_ESTRATTO', 'livello': 'warning', 'messaggio': 'Netto non estratto dal PDF'}
        return {'codice': 'MOVIMENTO_NON_CREATO', 'livello': 'warning', 'messaggio': 'Movimento non creato'}
    return None


def registra_evento_anomalia(*, utente, azienda, contesto: str, anomalia: Optional[dict], request=None):
    """Registra su log attività eventuale anomalia import/export."""
    if not anomalia:
        return
    codice = anomalia.get('codice', 'ANOMALIA')
    msg = anomalia.get('messaggio', '')
    registra_log(
        utente=utente,
        azienda=azienda,
        operazione=f'{contesto.lower()}_{codice.lower()}',
        descrizione=msg,
        request=request,
    )
