"""Geocoding Italia via Nominatim (OpenStreetMap). Usato da anagrafiche e impostazioni."""

from __future__ import annotations

import json
import logging
import re
import ssl
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

logger = logging.getLogger(__name__)

# Nominatim usage policy: max ~1 richiesta al secondo per installazioni senza quota dedicata.
_NOMINATIM_MIN_INTERVAL_S = 1.1


def _sanitize_geocode_query(q: str) -> str:
    """Rimuove residui di placeholder UI che rovinano la ricerca Nominatim."""
    s = (q or '').strip()
    s = re.sub(r'[—\-–]\s*Seleziona[^,]*', '', s, flags=re.IGNORECASE)
    s = re.sub(r',\s*,+', ',', s)
    s = re.sub(r'\s+', ' ', s).strip(' ,')
    return s


def ssl_context_for_https():
    try:
        import certifi

        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        return ssl.create_default_context()


def _geocode_query_variants(q_raw: str) -> list[str]:
    """
    Più formulazioni della stessa richiesta: Nominatim è sensibile a virgole,
    suffissi «(PA)» tipici dell’anagrafica GESPER, assenza di paese, ecc.
    """
    q = _sanitize_geocode_query(q_raw)
    if len(q) < 5:
        return []
    out: list[str] = []
    seen: set[str] = set()

    def add(s: str) -> None:
        t = _sanitize_geocode_query(s)
        if len(t) < 5:
            return
        k = t.casefold()
        if k in seen:
            return
        seen.add(k)
        out.append(t)

    add(q)
    # Es. "Via Roma 1, 90100 Palermo (PA)" → spesso risponde meglio senza la sigla finale
    no_sigla = re.sub(r'\s*\([A-Z]{2}\)\s*$', '', q, flags=re.IGNORECASE).strip()
    if no_sigla != q:
        add(no_sigla)
    if 'italia' not in q.casefold() and 'italy' not in q.casefold():
        add(f'{q}, Italia')
        if no_sigla != q:
            add(f'{no_sigla}, Italia')
    return out


def _nominatim_search_once(
    q: str,
    *,
    user_agent: str,
    countrycodes: str | None,
    timeout: float,
) -> dict:
    """
    Una sola richiesta search. countrycodes=None → parametro omesso (ricerca globale, ultima risorsa).
    Restituisce dict ok True/False come geocode_indirizzo_it.
    """
    params: dict[str, str | int] = {
        'q': q,
        'format': 'jsonv2',
        'limit': 1,
        'addressdetails': 0,
    }
    if countrycodes:
        params['countrycodes'] = countrycodes
    query = urlencode(params)
    url = f'https://nominatim.openstreetmap.org/search?{query}'

    try:
        req = Request(
            url,
            headers={
                'User-Agent': user_agent,
                'Accept': 'application/json',
                'Accept-Language': 'it',
            },
        )
        ctx = ssl_context_for_https()
        with urlopen(req, timeout=timeout, context=ctx) as resp:
            payload = resp.read().decode('utf-8')
        data = json.loads(payload)
        if not data:
            return {'ok': False, 'error': 'empty', 'q_tried': q}
        item = data[0]
        lat = float(item['lat'])
        lon = float(item['lon'])
        return {
            'ok': True,
            'lat': round(lat, 6),
            'lon': round(lon, 6),
            'display_name': item.get('display_name', ''),
        }
    except HTTPError as exc:
        code = getattr(exc, 'code', None)
        logger.error('[NOMINATIM] HTTP %s q=%r: %s', code, q, exc)
        if code == 429:
            msg = 'Troppe richieste al servizio di geocoding. Riprova tra qualche minuto.'
        elif code in (403, 503):
            msg = 'Il servizio di geocoding ha temporaneamente rifiutato la richiesta. Riprova più tardi.'
        else:
            msg = 'Servizio geocoding non disponibile al momento.'
        return {'ok': False, 'error': msg, 'http_status': code, 'q_tried': q}
    except (URLError, ValueError, KeyError, json.JSONDecodeError, OSError) as exc:
        logger.error('[NOMINATIM] Errore q=%r: %s', q, exc)
        err = str(exc).lower()
        if 'certificate verify failed' in err or ('ssl' in err and 'cert' in err):
            msg = (
                'Verifica SSL fallita verso il servizio di geocoding. '
                'Installa «certifi» (`pip install certifi`) o esegui «Install Certificates» del Python su macOS.'
            )
        else:
            msg = 'Servizio geocoding non disponibile al momento.'
        return {'ok': False, 'error': msg, 'q_tried': q}


def geocode_indirizzo_it(
    indirizzo: str,
    *,
    user_agent: str,
    countrycodes: str = 'it',
    timeout: float = 15.0,
) -> dict:
    """
    Restituisce dict con chiavi:
      - ok True: lat, lon, display_name
      - ok False: error, opzionale http_status
    """
    variants = _geocode_query_variants(indirizzo or '')
    if not variants:
        return {'ok': False, 'error': 'Indirizzo troppo corto.'}

    last_fail: dict | None = None
    for idx, vq in enumerate(variants):
        if idx > 0:
            time.sleep(_NOMINATIM_MIN_INTERVAL_S)
        r = _nominatim_search_once(vq, user_agent=user_agent, countrycodes=countrycodes, timeout=timeout)
        if r.get('ok'):
            return r
        if r.get('http_status') or (r.get('error') and r['error'] != 'empty'):
            return {k: v for k, v in r.items() if k != 'q_tried'}
        last_fail = r

    # Ultimo tentativo: query con ", Italia" senza filtro country (evita edge case su confini / encoding)
    tail = variants[0]
    if 'italia' not in tail.casefold():
        tail = f'{tail}, Italia'
    time.sleep(_NOMINATIM_MIN_INTERVAL_S)
    r = _nominatim_search_once(tail, user_agent=user_agent, countrycodes=None, timeout=timeout)
    if r.get('ok'):
        return r
    if r.get('http_status') or (r.get('error') and r['error'] != 'empty'):
        return {k: v for k, v in r.items() if k != 'q_tried'}

    q_show = _sanitize_geocode_query(indirizzo or '')[:120]
    return {
        'ok': False,
        'error': (
            f'OpenStreetMap non ha trovato coordinate per «{q_show}». '
            'Prova via, CAP e comune su righe distinte in anagrafica, oppure usa «Estrai da Google Maps» '
            'con l’URL completo del luogo (non dipende dall’indirizzo testuale).'
        ),
    }


def is_geocode_address_not_found(error: str) -> bool:
    """True se Nominatim non ha trovato il luogo (risposta 404 lato API), non errore di rete."""
    e = (error or '').strip()
    return e.startswith('Nessun risultato') or e.startswith('OpenStreetMap non ha trovato')


def user_agent_gesper(contact_email: str = '') -> str:
    c = (contact_email or '').strip()
    if '@' in c:
        return f'GESPER/1.0 ({c})'
    return 'GESPER/1.0 (anagrafica; contatto amministratore installazione)'
