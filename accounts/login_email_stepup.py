"""
Secondo passaggio accesso web: codice monouso inviato via e-mail (SMTP da ConfigurazioneSistema).

Distinto dalla verifica e-mail iniziale (token su ``User.email_token``) e dal TOTP API (app authenticator).
"""
from __future__ import annotations

import hmac
import hashlib
import logging
import secrets

from django.conf import settings
from django.core.cache import cache
from django.core.mail import EmailMessage, get_connection

from accounts.models import ConfigurazioneSistema, User

logger = logging.getLogger(__name__)

CACHE_PREFIX = 'gesper_web_login_email_otp:'
SESSION_SID_KEY = 'gesper_login_email_otp_sid'
CACHE_TTL = 600  # 10 minuti
MAX_OTP_ATTEMPTS = 7
RL_PREFIX = 'gesper_web_login_email_otp_rl:'
RL_MAX = 8
RL_TTL = 3600


def _otp_hash(sid: str, otp: str) -> str:
    msg = f'weblogin:{sid}:{otp}'.encode()
    return hmac.new(settings.SECRET_KEY.encode(), msg, hashlib.sha256).hexdigest()


def pending_otp_session(request) -> bool:
    sid = request.session.get(SESSION_SID_KEY)
    if not sid:
        return False
    return cache.get(f'{CACHE_PREFIX}{sid}') is not None


def clear_pending(request) -> None:
    sid = request.session.pop(SESSION_SID_KEY, None)
    if sid:
        cache.delete(f'{CACHE_PREFIX}{sid}')


def _invia_codice_email(user: User, codice: str) -> None:
    config = ConfigurazioneSistema.get()
    dest = (user.email or '').strip().lower()
    if not dest:
        raise ValueError('E-mail utente mancante.')

    nome_sito = (config.nome_sito or 'GESPER').strip() or 'GESPER'
    corpo = (
        f'Codice di accesso {nome_sito}: {codice}\n\n'
        'Il codice è valido per 10 minuti.\n'
        'Se non hai tentato di accedere, ignora questo messaggio e verifica la sicurezza del tuo account.\n'
    )
    if not (config.smtp_user and config.smtp_password):
        raise ValueError('SMTP non configurato in Impostazioni di sistema.')

    conn = get_connection(
        backend='accounts.email_backend.ConfigurazioneSistemaEmailBackend',
        host=config.smtp_host,
        port=config.smtp_port,
        username=config.smtp_user,
        password=config.smtp_password,
        use_tls=config.smtp_use_tls and not config.smtp_use_ssl,
        use_ssl=config.smtp_use_ssl,
        fail_silently=False,
    )
    msg = EmailMessage(
        subject=f'[{nome_sito}] Codice di verifica accesso',
        body=corpo,
        from_email=config.from_email(),
        to=[dest],
        connection=conn,
    )
    msg.send()


def avvia_stepup_email(request, user: User) -> None:
    """Genera OTP, lo salva in cache e invia l'e-mail. Imposta la sessione in attesa."""
    rl_key = f'{RL_PREFIX}{user.pk}'
    n = int(cache.get(rl_key) or 0)
    if n >= RL_MAX:
        raise ValueError('Troppi invii di codice per questo account. Riprova tra un’ora.')

    sid = secrets.token_urlsafe(24)
    otp = f'{secrets.randbelow(900000) + 100000:06d}'
    cache.set(
        f'{CACHE_PREFIX}{sid}',
        {'uid': user.pk, 'otp_hash': _otp_hash(sid, otp), 'attempts': 0},
        CACHE_TTL,
    )
    request.session[SESSION_SID_KEY] = sid
    _invia_codice_email(user, otp)
    cache.set(rl_key, n + 1, RL_TTL)
    logger.info('[LOGIN EMAIL OTP] Codice inviato a uid=%s', user.pk)


def verifica_e_recupera_uid(request, otp_in: str) -> int | None:
    """
    Verifica il codice. In caso di successo elimina cache/sessione e ritorna user id.
    Ritorna None se sid assente, scaduto, troppi tentativi o codice errato.
    """
    sid = request.session.get(SESSION_SID_KEY)
    if not sid:
        return None
    key = f'{CACHE_PREFIX}{sid}'
    entry = cache.get(key)
    if not entry:
        request.session.pop(SESSION_SID_KEY, None)
        return None

    otp_in = (otp_in or '').strip()
    if len(otp_in) != 6 or not otp_in.isdigit():
        entry['attempts'] = int(entry.get('attempts') or 0) + 1
        if entry['attempts'] >= MAX_OTP_ATTEMPTS:
            cache.delete(key)
            request.session.pop(SESSION_SID_KEY, None)
        else:
            cache.set(key, entry, CACHE_TTL)
        return None

    if not hmac.compare_digest(entry['otp_hash'], _otp_hash(sid, otp_in)):
        entry['attempts'] = int(entry.get('attempts') or 0) + 1
        if entry['attempts'] >= MAX_OTP_ATTEMPTS:
            cache.delete(key)
            request.session.pop(SESSION_SID_KEY, None)
        else:
            cache.set(key, entry, CACHE_TTL)
        return None

    uid = int(entry['uid'])
    cache.delete(key)
    request.session.pop(SESSION_SID_KEY, None)
    return uid
