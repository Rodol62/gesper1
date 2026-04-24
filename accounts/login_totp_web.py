"""
Secondo passaggio accesso web: codice TOTP (RFC 6238).

Compatibile con Google Authenticator, Microsoft Authenticator, Aruba Key e altre app standard TOTP.
Il segreto resta solo sul modello ``User``; in cache c’è solo l’id utente in attesa dopo password corretta.
"""
from __future__ import annotations

import logging
import secrets

from django.core.cache import cache

logger = logging.getLogger(__name__)

CACHE_PREFIX = 'gesper_web_login_totp:'
SESSION_SID_KEY = 'gesper_login_totp_sid'
CACHE_TTL = 600  # 10 minuti
MAX_ATTEMPTS = 7


def pending_totp_session(request) -> bool:
    sid = request.session.get(SESSION_SID_KEY)
    if not sid:
        return False
    return cache.get(f'{CACHE_PREFIX}{sid}') is not None


def clear_totp_pending(request) -> None:
    sid = request.session.pop(SESSION_SID_KEY, None)
    if sid:
        cache.delete(f'{CACHE_PREFIX}{sid}')


def start_totp_pending(request, user) -> None:
    """Dopo password corretta: sessione in attesa di codice dall’app authenticator."""
    sid = secrets.token_urlsafe(24)
    cache.set(
        f'{CACHE_PREFIX}{sid}',
        {'uid': user.pk, 'attempts': 0},
        CACHE_TTL,
    )
    request.session[SESSION_SID_KEY] = sid
    logger.info('[LOGIN TOTP WEB] In attesa codice app per uid=%s', user.pk)


def verify_totp_and_get_uid(request, otp_in: str):
    """
    Verifica TOTP per l’utente in pending. Ritorna uid se ok, altrimenti None.
    """
    import pyotp
    from django.contrib.auth import get_user_model

    User = get_user_model()
    sid = request.session.get(SESSION_SID_KEY)
    if not sid:
        return None
    key = f'{CACHE_PREFIX}{sid}'
    entry = cache.get(key)
    if not entry:
        request.session.pop(SESSION_SID_KEY, None)
        return None

    otp_in = (otp_in or '').strip().replace(' ', '')
    if not otp_in.isdigit() or len(otp_in) < 6:
        entry['attempts'] = int(entry.get('attempts') or 0) + 1
        if entry['attempts'] >= MAX_ATTEMPTS:
            cache.delete(key)
            request.session.pop(SESSION_SID_KEY, None)
        else:
            cache.set(key, entry, CACHE_TTL)
        return None

    uid = int(entry['uid'])
    try:
        user = User.objects.get(pk=uid)
    except User.DoesNotExist:
        cache.delete(key)
        request.session.pop(SESSION_SID_KEY, None)
        return None

    if not user.totp_enabled or not (user.totp_secret or '').strip():
        cache.delete(key)
        request.session.pop(SESSION_SID_KEY, None)
        return None

    totp = pyotp.TOTP(user.totp_secret)
    if not totp.verify(otp_in, valid_window=1):
        entry['attempts'] = int(entry.get('attempts') or 0) + 1
        if entry['attempts'] >= MAX_ATTEMPTS:
            cache.delete(key)
            request.session.pop(SESSION_SID_KEY, None)
        else:
            cache.set(key, entry, CACHE_TTL)
        return None

    cache.delete(key)
    request.session.pop(SESSION_SID_KEY, None)
    return uid
