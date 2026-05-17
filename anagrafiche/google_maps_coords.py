"""
Estrae latitudine/longitudine da testo incollato o URL Google Maps.

Gestisce @lat,lng, !3d!4d!, parametri q=/ll=/center=, numeri con virgola decimale
e, in assenza di coordinate nel testo, segue i redirect HTTP dei link corti
(maps.app.goo.gl, goo.gl) fino all’URL lungo.
"""

from __future__ import annotations

import logging
import re
import ssl
from urllib.parse import unquote, urlparse
from urllib.request import Request, urlopen

logger = logging.getLogger(__name__)

try:
    import certifi

    _SSL_CTX = ssl.create_default_context(cafile=certifi.where())
except Exception:
    _SSL_CTX = ssl.create_default_context()

READ_BODY_BYTES = 450_000
UA_BROWSER = (
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
    '(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36 (GESPER maps resolver)'
)


def _valid(lat: float, lon: float) -> bool:
    if lat != lat or lon != lon:
        return False
    return -90.0 <= lat <= 90.0 and -180.0 <= lon <= 180.0


def _norm_decimal_token(s: str) -> str:
    return s.strip().replace(',', '.')


def _pair_from_strings(a: str, b: str) -> tuple[float, float] | None:
    try:
        la = float(_norm_decimal_token(a))
        lo = float(_norm_decimal_token(b))
        if _valid(la, lo):
            return (la, lo)
    except ValueError:
        pass
    return None


def _allowed_maps_host(hostname: str) -> bool:
    """Evita SSRF: solo host noti Maps / Google short links."""
    h = (hostname or '').lower().strip('.')
    if not h:
        return False
    allowed = (
        'maps.app.goo.gl',
        'goo.gl',
        'g.co',
        'google.com',
        'google.it',
        'google.co.uk',
        'googleusercontent.com',
        'maps.google.com',
    )
    for a in allowed:
        if h == a or h.endswith('.' + a):
            return True
    return False


def extract_lat_lon_from_maps_blob(text: str) -> tuple[float, float] | None:
    """
    Cerca coordinate nel testo (URL già espanso, HTML, o coppia lat,lon).
    Restituisce (lat, lon) o None.
    """
    if not text or not str(text).strip():
        return None

    raw = unquote(str(text).strip().replace('+', ' '))
    raw = raw.replace('%2C', ',').replace('%2c', ',').replace('%3F', '?').replace('%26', '&')

    # !3dLAT!4dLNG (ordine standard Google)
    for m in reversed(list(re.finditer(r'!3d(-?\d+(?:\.\d+)?)!4d(-?\d+(?:\.\d+)?)', raw, re.I))):
        p = _pair_from_strings(m.group(1), m.group(2))
        if p:
            return p

    # Variante rara !4dLNG!3dLAT
    for m in reversed(list(re.finditer(r'!4d(-?\d+(?:\.\d+)?)!3d(-?\d+(?:\.\d+)?)', raw, re.I))):
        p = _pair_from_strings(m.group(2), m.group(1))
        if p:
            return p

    # @lat,lng (Google mette spesso @lat,lng,17z): prendi l’ultima coppia plausibile
    for m in reversed(list(re.finditer(r'@(-?\d+(?:[.,]\d+)?),(-?\d+(?:[.,]\d+)?)', raw))):
        end = m.end()
        if end < len(raw) and raw[end].isdigit():
            continue
        p = _pair_from_strings(m.group(1), m.group(2))
        if p:
            return p

    # Query: q=, ll=, center=, …
    for key in ('q', 'll', 'center', 'query', 'daddr', 'destination'):
        for mm in re.finditer(rf'(?:[?&]){key}=([^&\s#]+)', raw, re.I):
            val = unquote(mm.group(1).replace('+', ' '))
            pm = re.search(r'(-?\d+(?:[.,]\d+)?)\s*,\s*(-?\d+(?:[.,]\d+)?)', val)
            if pm:
                p = _pair_from_strings(pm.group(1), pm.group(2))
                if p:
                    return p

    # Riga quasi solo coordinate: 45.4642, 9.19 oppure 45,4642, 9,1901 (virgole decimali IT)
    stripped = raw.strip()
    m = re.match(r'^\s*(-?\d+(?:[.,]\d+)?)\s*[,;]\s*(-?\d+(?:[.,]\d+)?)\s*$', stripped)
    if m:
        p = _pair_from_strings(m.group(1), m.group(2))
        if p:
            return p

    return None


def _first_http_url(text: str) -> str | None:
    m = re.search(r'https?://[^\s<>"\'\)]+', text)
    if not m:
        return None
    u = m.group(0)
    while u and u[-1] in '.,);':
        u = u[:-1]
    return u or None


def fetch_expanded_maps_context(pasted: str) -> str:
    """
    Incolla + (se c’è un URL consentito) URL finale dopo redirect + pezzo di HTML.
    """
    chunks: list[str] = [pasted]
    url = _first_http_url(pasted)
    if not url:
        return pasted

    try:
        p = urlparse(url)
        if p.scheme not in ('http', 'https') or not _allowed_maps_host(p.netloc):
            return pasted
    except Exception:
        return pasted

    try:
        req = Request(url, headers={'User-Agent': UA_BROWSER, 'Accept-Language': 'it,it;q=0.9,en;q=0.8'})
        with urlopen(req, timeout=14.0, context=_SSL_CTX) as resp:
            final_url = resp.geturl() or url
            body = resp.read(READ_BODY_BYTES).decode('utf-8', errors='ignore')
        chunks.append(final_url)
        chunks.append(body)
    except Exception as exc:
        logger.info('[maps_coords] follow URL skip: %s — %s', url, exc)

    return '\n'.join(chunks)


def estrai_coordinate_maps(pasted: str) -> dict:
    """
    Restituisce dict:
      ok True: lat, lon (float arrotondati a 6 decimali nel chiamante)
      ok False: error (str)
    """
    pasted = (pasted or '').strip()
    if len(pasted) < 4:
        return {'ok': False, 'error': 'Incolla un URL o le coordinate (almeno 4 caratteri).'}

    c = extract_lat_lon_from_maps_blob(pasted)
    if c:
        return {'ok': True, 'lat': round(c[0], 6), 'lon': round(c[1], 6)}

    expanded = fetch_expanded_maps_context(pasted)
    c2 = extract_lat_lon_from_maps_blob(expanded)
    if c2:
        return {'ok': True, 'lat': round(c2[0], 6), 'lon': round(c2[1], 6)}

    return {
        'ok': False,
        'error': (
            'Coordinate non trovate. Apri il luogo in Google Maps (punto sulla mappa), copia l’URL dalla barra '
            'dell’indirizzo, oppure incolla due numeri come 45.4642, 9.1900. I link corti maps.app.goo.gl '
            'funzionano solo se portano a una posizione precisa (il server segue i redirect).'
        ),
    }
