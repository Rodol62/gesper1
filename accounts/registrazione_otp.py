"""
Registrazione candidato in due passaggi: OTP via e-mail e completamento.
Username: nome.cognome (normalizzato); password iniziale: codice fiscale.
"""
from __future__ import annotations

import hashlib
import hmac
import logging
import re
import secrets
import unicodedata

from django.conf import settings
from django.core import signing
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.core.mail import EmailMessage, get_connection
from django.utils import timezone

from accounts.models import ConfigurazioneSistema

logger = logging.getLogger('django')

OTP_TTL = 900  # 15 minuti
MAX_OTP_SEND_PER_HOUR = 8
MAX_OTP_ATTEMPTS = 7

REGCAND_OTP_PREFIX = 'regcand_otp:'
REGCAND_RL_PREFIX = 'regcand_rl:'  # rate limit invii OTP per e-mail

PAYLOAD_SALT = 'gesper.regcand.payload.v1'


def _hash_otp(session_id: str, otp: str) -> str:
    msg = f'{session_id}:{otp}'.encode()
    return hmac.new(settings.SECRET_KEY.encode(), msg, hashlib.sha256).hexdigest()


def slug_nome(txt: str) -> str:
    if not txt:
        return ''
    txt = unicodedata.normalize('NFKD', txt.strip())
    txt = ''.join(c for c in txt if not unicodedata.combining(c))
    txt = re.sub(r'[^a-zA-Z0-9]+', '', txt)
    return txt.lower()


def build_username_nome_cognome(first_name: str, last_name: str) -> str:
    a = slug_nome(first_name)
    b = slug_nome(last_name)
    if not a or not b:
        raise ValidationError('Nome e cognome devono contenere caratteri validi per il nome utente.')
    base = f'{a}.{b}'
    if len(base) > 150:
        base = base[:150]
    return base


def allocate_username(base: str) -> str:
    from accounts.models import User

    username = base
    n = 1
    while User.objects.filter(username__iexact=username).exists():
        suffix = str(n)
        trim = max(1, 150 - len(suffix))
        username = f'{base[:trim]}{suffix}'
        n += 1
        if n > 100000:
            raise ValidationError('Impossibile assegnare un nome utente univoco. Contatta il supporto.')
    return username


def normalizza_cellulare_it(raw: str) -> str | None:
    """Restituisce 10 cifre (cellulare italiano) o None."""
    s = re.sub(r'[\s\-]', '', (raw or '').strip())
    if s.startswith('+'):
        s = s[1:]
    if s.startswith('0039'):
        s = s[4:]
    elif s.startswith('39') and len(s) >= 11:
        s = s[2:]
    if not re.match(r'^[0-9]{10}$', s):
        return None
    if s[0] != '3':
        return None
    return s


def invia_otp_email(dest_email: str, codice_otp: str) -> None:
    """Invio OTP via e-mail (SMTP da ConfigurazioneSistema)."""
    email_to = (dest_email or '').strip().lower()
    if not email_to:
        raise ValidationError('E-mail destinatario non valida per invio OTP.')

    config = ConfigurazioneSistema.get()
    nome_sito = (config.nome_sito or 'GESPER').strip() or 'GESPER'
    subject = f'[{nome_sito}] Codice verifica registrazione candidato'
    body = (
        f'Il tuo codice di verifica è {codice_otp}.\n'
        'Il codice è valido per 15 minuti.\n\n'
        'Se non hai richiesto questa operazione, ignora questo messaggio.\n'
    )
    if not (config.smtp_user and config.smtp_password):
        raise ValidationError(
            'Invio e-mail non configurato: imposta SMTP in Impostazioni di sistema.',
        )
    try:
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
            subject=subject,
            body=body,
            from_email=config.from_email(),
            to=[email_to],
            connection=conn,
        )
        msg.send()
    except Exception as exc:
        logger.exception('[REGISTRAZIONE OTP EMAIL] Errore invio a %s: %s', email_to, exc)
        raise ValidationError(
            'Invio codice via e-mail non riuscito. Riprova tra qualche minuto.',
        ) from exc
    logger.info('[REGISTRAZIONE OTP EMAIL] OTP inviato a %s', email_to)


def crea_sessione_e_invia_otp(payload: dict) -> str:
    """
    payload: first_name, last_name, email (lower), cf (upper), telefono (10 cifre),
             consenso_conservazione, consenso_comunicazione (bool)
    """
    email = payload['email']
    rl_key = f'{REGCAND_RL_PREFIX}{email}'
    n = cache.get(rl_key) or 0
    if n >= MAX_OTP_SEND_PER_HOUR:
        raise ValidationError(
            'Troppi invii di codice verso questa e-mail. Riprova tra un’ora.',
            code='rate_limit_email',
        )

    session_id = secrets.token_urlsafe(32)
    otp = f'{secrets.randbelow(900000) + 100000:06d}'
    otp_hash = _hash_otp(session_id, otp)
    sig = _firmare_payload(payload)

    cache_key = f'{REGCAND_OTP_PREFIX}{session_id}'
    cache.set(
        cache_key,
        {'otp_hash': otp_hash, 'payload_sig': sig, 'attempts': 0},
        OTP_TTL,
    )
    cache.set(rl_key, n + 1, 3600)

    invia_otp_email(payload['email'], otp)
    logger.info('[REGISTRAZIONE OTP] Sessione creata (email=%s)', email)
    return session_id


def _firmare_payload(data: dict) -> str:
    return signing.dumps(data, salt=PAYLOAD_SALT, compress=True)


def apri_payload_firmato(sig: str, max_age: int = OTP_TTL) -> dict:
    return signing.loads(sig, salt=PAYLOAD_SALT, max_age=max_age)


def completa_registrazione_con_otp(session_id: str, otp_in: str):
    """
    Verifica OTP e crea User + ProfiloCandidato + email verifica.
    Restituisce l'utente creato.
    """
    from anagrafiche.models import Azienda
    from accounts.models import ProfiloCandidato, Ruolo, User
    otp_in = (otp_in or '').strip()
    if len(otp_in) != 6 or not otp_in.isdigit():
        raise ValidationError('Inserisci il codice a 6 cifre ricevuto via e-mail.')

    cache_key = f'{REGCAND_OTP_PREFIX}{session_id}'
    entry = cache.get(cache_key)
    if not entry:
        raise ValidationError('Sessione scaduta o non valida. Ricompila il modulo e richiedi un nuovo codice.')

    if entry.get('attempts', 0) >= MAX_OTP_ATTEMPTS:
        cache.delete(cache_key)
        raise ValidationError('Troppi tentativi errati. Ricompila il modulo e richiedi un nuovo codice.')

    if not hmac.compare_digest(entry['otp_hash'], _hash_otp(session_id, otp_in)):
        entry['attempts'] = entry.get('attempts', 0) + 1
        cache.set(cache_key, entry, OTP_TTL)
        raise ValidationError('Codice non valido.')

    cache.delete(cache_key)

    try:
        data = apri_payload_firmato(entry['payload_sig'])
    except signing.SignatureExpired:
        raise ValidationError('Sessione scaduta. Ricompila il modulo e richiedi un nuovo codice.')
    except signing.BadSignature:
        raise ValidationError('Dati di registrazione non validi.')

    email = data['email']
    cf = data['cf']
    tel = data['telefono']

    if User.objects.filter(email__iexact=email).exists():
        raise ValidationError('Esiste già un account con questa e-mail.')
    if ProfiloCandidato.objects.filter(codice_fiscale__iexact=cf).exists():
        raise ValidationError('Questo codice fiscale risulta già registrato.')

    base_user = build_username_nome_cognome(data['first_name'], data['last_name'])
    username = allocate_username(base_user)

    user = User(
        username=username,
        email=email.lower(),
        first_name=data['first_name'].strip().upper(),
        last_name=data['last_name'].strip().upper(),
        is_active=False,
        convalidato=False,
        privacy_accettata=True,
    )
    user.set_password(cf)
    user.privacy_data = timezone.now()
    user.save()

    ruolo_candidato = Ruolo.objects.filter(codice='candidato').first()
    if ruolo_candidato:
        user.ruoli.add(ruolo_candidato)

    pc = ProfiloCandidato.objects.create(
        user=user,
        codice_fiscale=cf,
        telefono=tel,
        consenso_conservazione=bool(data.get('consenso_conservazione')),
        consenso_comunicazione=bool(data.get('consenso_comunicazione')),
        data_consensi=timezone.now(),
    )
    try:
        if Azienda.objects.count() == 1:
            az = Azienda.objects.first()
            if az:
                pc.azienda_interesse = az
                pc.save(update_fields=['azienda_interesse'])
    except Exception:
        pass

    token = user.genera_token_verifica()
    return user, token


def costruisci_payload_da_form_cleaned(cleaned: dict) -> dict:
    tel = normalizza_cellulare_it(cleaned['telefono'])
    if not tel:
        raise ValidationError('Inserisci un numero di cellulare italiano valido (10 cifre, es. 3xx xxx xxxx).')
    cf = cleaned['codice_fiscale'].strip().upper()
    return {
        'first_name': cleaned['first_name'].strip(),
        'last_name': cleaned['last_name'].strip(),
        'email': cleaned['email'].lower().strip(),
        'cf': cf,
        'telefono': tel,
        'consenso_conservazione': cleaned.get('consenso_conservazione', False),
        'consenso_comunicazione': cleaned.get('consenso_comunicazione', False),
    }
