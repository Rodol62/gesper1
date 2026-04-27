"""Geocoding Italia via Nominatim (OpenStreetMap). Usato da anagrafiche e impostazioni."""

from __future__ import annotations

import json
import logging
import re
import ssl
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

logger = logging.getLogger(__name__)


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
    q = _sanitize_geocode_query(indirizzo or '')
    if len(q) < 5:
        return {'ok': False, 'error': 'Indirizzo troppo corto.'}

    query = urlencode(
        {
            'q': q,
            'format': 'jsonv2',
            'limit': 1,
            'addressdetails': 0,
            'countrycodes': countrycodes,
        }
    )
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
            return {'ok': False, 'error': 'Nessun risultato trovato per questo indirizzo.'}
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
        return {'ok': False, 'error': msg, 'http_status': code}
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
        return {'ok': False, 'error': msg}


def user_agent_gesper(contact_email: str = '') -> str:
    c = (contact_email or '').strip()
    if '@' in c:
        return f'GESPER/1.0 ({c})'
    return 'GESPER/1.0 (anagrafica; contatto amministratore installazione)'
