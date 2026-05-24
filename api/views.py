from datetime import date, datetime
from django.conf import settings
from django.contrib.auth import authenticate, login as auth_login, logout as auth_logout
from django.core import signing
from rest_framework.decorators import api_view, permission_classes, parser_classes
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework import status
from rest_framework_simplejwt.tokens import RefreshToken
from rest_framework_simplejwt.exceptions import TokenError, InvalidToken
from pywebpush import webpush, WebPushException
import pyotp
import qrcode
import qrcode.image.svg
import base64
import io
import json
from django.db.models import Q
from django.urls import reverse

from accounts.gesper_paths import api_base_path, portal_web_base_path
from accounts.outbound_uri import outbound_absolute_uri
from anagrafiche.models import Dipendente
from presenze.models import Presenza
from documenti.models import Documento
from documenti.buste_cedolino_batch import parse_periodo_busta
from richieste.models import Richiesta

from .models import PushSubscription


# ---------------------------------------------------------------------------
# AUTH
# ---------------------------------------------------------------------------

def _user_payload_for_api(user, request=None):
    """Dati utente per JWT / app: ruoli, candidato, completamento profilo, link sito."""
    from accounts.models import ProfiloCandidato
    from accounts.utils import controlla_completezza_profilo

    ruoli = list(user.ruoli.values_list('codice', flat=True)) if hasattr(user, 'ruoli') else []
    # has_ruolo è la fonte di verità (evita disallineamenti M2M ↔ lista ruoli nel payload JWT).
    if hasattr(user, 'has_ruolo'):
        is_consulente_api = user.has_ruolo('consulente')
        has_dipendente_ruolo = user.has_ruolo('dipendente')
        is_admin_api = bool(
            user.is_superuser or user.has_ruolo('admin') or user.has_ruolo('hr')
        )
    else:
        is_consulente_api = 'consulente' in ruoli
        has_dipendente_ruolo = 'dipendente' in ruoli
        is_admin_api = bool(user.is_superuser or 'admin' in ruoli or 'hr' in ruoli)

    from accounts.gestione_database import can_gestione_database
    can_dash_admin = can_gestione_database(user)

    payload = {
        'id': None,
        'username': user.username,
        'first_name': user.first_name or '',
        'last_name': user.last_name or '',
        'email': getattr(user, 'email', '') or '',
        'email_verificata': bool(getattr(user, 'email_verificata', False)),
        'ruoli': ruoli,
        'is_candidato': 'candidato' in ruoli,
        'is_dipendente_ruolo': 'dipendente' in ruoli,
        'is_admin': is_admin_api,
        'is_consulente': is_consulente_api,
        'is_superuser': bool(getattr(user, 'is_superuser', False)),
        'can_dashboard_admin': can_dash_admin,
        'dipendente_id': None,
        'azienda': '',
        'profilo_completato': None,
        'needs_completa_profilo': False,
        'portal_web_base': portal_web_base_path(request),
        'api_base_path': api_base_path(),
    }
    try:
        dip = user.dipendente
        payload['id'] = dip.id
        payload['dipendente_id'] = dip.id
        payload['first_name'] = dip.nome
        payload['last_name'] = dip.cognome
        payload['azienda'] = dip.azienda.nome if dip.azienda else ''
    except Exception:
        pass

    profilo = getattr(user, 'profilo_candidato', None)
    if profilo is None:
        profilo = ProfiloCandidato.objects.filter(user=user).first()
    if profilo:
        payload['profilo_completato'] = profilo.profilo_completato
        comp = controlla_completezza_profilo(profilo)
        missing_files = (
            not (profilo.file_documento and profilo.file_documento.name)
            or not (profilo.file_codice_fiscale and profilo.file_codice_fiscale.name)
        )
        payload['needs_completa_profilo'] = (
            ('candidato' in ruoli)
            and (
                (not profilo.profilo_completato)
                or (not comp['completo'])
                or missing_files
            )
        )
    elif 'candidato' in ruoli:
        payload['needs_completa_profilo'] = True
        payload['profilo_completato'] = False

    # Routing app PWA: timbratura / API dipendente
    # Con solo ruolo consulente (senza «dipendente») non mostrare mai l’area timbratura,
    # anche se esiste un collegamento Dipendente (OneToOne) per altri motivi.
    dip_id = payload['dipendente_id']
    # Candidato senza anagrafica dipendente collegata: nessuna timbratura / accesso rapido dipendente in app.
    if payload.get('is_candidato') and not dip_id:
        payload['mostra_area_dipendente_app'] = False
    elif is_consulente_api and not has_dipendente_ruolo:
        payload['mostra_area_dipendente_app'] = False
    elif dip_id:
        payload['mostra_area_dipendente_app'] = bool(
            has_dipendente_ruolo or not is_consulente_api
        )
    elif is_admin_api and not dip_id:
        payload['mostra_area_dipendente_app'] = False
    elif is_consulente_api:
        payload['mostra_area_dipendente_app'] = False
    else:
        payload['mostra_area_dipendente_app'] = True

    return payload


@api_view(['POST'])
@permission_classes([AllowAny])
def login_view(request):
    from accounts.models import User
    import re

    username = request.data.get('username', '').strip()
    password_raw = request.data.get('password', '')

    if not username or not password_raw:
        return Response({'detail': 'Credenziali mancanti.'}, status=status.HTTP_400_BAD_REQUEST)

    # Consenti accesso con e-mail al posto dello username (nome.cognome)
    if '@' in username:
        u = User.objects.filter(email__iexact=username).first()
        if u:
            username = u.username

    # Prima prova: password come inserita; fallback utile per password iniziale CF:
    # - rimozione spazi involontari
    # - maiuscolo automatico se formato codice fiscale (16 alfanumerici)
    variants = []
    for p in (password_raw, password_raw.strip()):
        if p and p not in variants:
            variants.append(p)
    for p in list(variants):
        if re.fullmatch(r'[A-Za-z0-9]{16}', p):
            up = p.upper()
            if up not in variants:
                variants.append(up)

    user = None
    for pwd in variants:
        user = authenticate(request, username=username, password=pwd)
        if user is not None:
            break

    if user is None:
        # Se username/password sono corretti ma account non attivo, restituiamo
        # un errore esplicito (altrimenti Django authenticate torna None).
        maybe_user = User.objects.filter(username__iexact=username).first()
        if maybe_user and not maybe_user.is_active:
            for pwd in variants:
                try:
                    if maybe_user.check_password(pwd):
                        return Response(
                            {'detail': 'Account non ancora attivo. Apri il link di verifica ricevuto via e-mail.'},
                            status=status.HTTP_403_FORBIDDEN,
                        )
                except Exception:
                    pass
        return Response({'detail': 'Credenziali non valide.'}, status=status.HTTP_401_UNAUTHORIZED)

    if not user.is_active:
        return Response({'detail': 'Account disattivato.'}, status=status.HTTP_403_FORBIDDEN)

    user_data = _user_payload_for_api(user, request)

    # Se 2FA abilitato → restituisci token temporaneo (5 min), non JWT completo
    if user.totp_enabled:
        temp_token = signing.dumps({'user_id': user.id}, salt='gesper_2fa', compress=True)
        return Response({
            'requires_2fa': True,
            'temp_token':   temp_token,
            'user':         user_data,
        })

    # Sessione sito (cookie) allineata al JWT: stesso flusso per aprire /moduli/, /documenti/ in altra scheda
    # senza rifare il login su /accounts/login/.
    auth_login(request, user)
    refresh = RefreshToken.for_user(user)
    return Response({
        'access':  str(refresh.access_token),
        'refresh': str(refresh),
        'user':    user_data,
    })


@api_view(['POST'])
@permission_classes([AllowAny])
def recover_username_api(request):
    """
    Invia all'e-mail indicata il/i nome utente associati (come il reset password,
    non rivela se l'indirizzo è registrato — messaggio di risposta sempre uguale).
    """
    from django.contrib.auth import get_user_model
    from accounts.models import ConfigurazioneSistema
    from accounts.views_registration import invia_email_testuale

    email = (request.data.get('email') or '').strip()
    if not email or '@' not in email:
        return Response({'detail': 'Indirizzo e-mail non valido.'}, status=status.HTTP_400_BAD_REQUEST)

    User = get_user_model()
    users = list(
        User.objects.filter(email__iexact=email, is_active=True).exclude(is_superuser=True)[:10]
    )
    users = [
        u
        for u in users
        if not (hasattr(u, 'has_ruolo') and (u.has_ruolo('admin') or u.has_ruolo('hr')))
    ]

    if users:
        config = ConfigurazioneSistema.get()
        nome_sito = config.nome_sito or 'GESPER'
        lines = []
        for u in users:
            nome = f'{u.first_name} {u.last_name}'.strip() or '—'
            lines.append(f'  • {u.username}  ({nome})')
        lines_txt = '\n'.join(lines)
        corpo = (
            f'Gentile utente,\n\n'
            f'hai richiesto il recupero del nome utente per accedere a {nome_sito}.\n\n'
            f'Nome utente (User ID) associato a questa e-mail:\n'
            f'{lines_txt}\n\n'
            f'Per il login nell\'app o sul sito usa questo nome utente oppure la stessa e-mail, '
            f'se previsto, e la tua password.\n\n'
            f'Se non hai richiesto questo messaggio, ignora questa e-mail.\n\n'
            f'— Il team {nome_sito}'
        )
        invia_email_testuale(
            email,
            f'{nome_sito} — Recupero nome utente',
            corpo,
        )

    return Response(
        {
            'detail': (
                'Se l\'indirizzo è associato a un account GESPER, riceverai un messaggio con il '
                'nome utente. Controlla anche la cartella spam.'
            ),
        },
        status=status.HTTP_200_OK,
    )


@api_view(['POST'])
@permission_classes([AllowAny])
def refresh_view(request):
    from django.contrib.auth import get_user_model

    User = get_user_model()
    refresh_token = request.data.get('refresh')
    if not refresh_token:
        return Response({'detail': 'Refresh token mancante.'}, status=status.HTTP_400_BAD_REQUEST)
    try:
        refresh = RefreshToken(refresh_token)
        uid = refresh['user_id']
        user = User.objects.get(pk=uid)
        if not user.is_active:
            return Response({'detail': 'Account disattivato.'}, status=status.HTTP_403_FORBIDDEN)
        _b = getattr(user, "backend", settings.AUTHENTICATION_BACKENDS[0])
        auth_login(request, user, backend=_b)
        user_data = _user_payload_for_api(user, request)
        return Response({
            'access':  str(refresh.access_token),
            'refresh': str(refresh),
            'user':    user_data,
        })
    except User.DoesNotExist:
        return Response({'detail': 'Utente non trovato.'}, status=status.HTTP_401_UNAUTHORIZED)
    except (TokenError, InvalidToken) as e:
        return Response({'detail': str(e)}, status=status.HTTP_401_UNAUTHORIZED)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def portal_session_view(request):
    """
    Sincronizza la sessione cookie del portale (link /moduli/, /documenti/ in altra scheda) con
    l'utente autenticato via JWT. Chiamata dalla PWA con credentials dopo login o all'avvio.
    """
    u = request.user
    if not u.is_active:
        return Response({'detail': 'Account disattivato.'}, status=status.HTTP_403_FORBIDDEN)
    _b = getattr(u, "backend", settings.AUTHENTICATION_BACKENDS[0])
    auth_login(request, u, backend=_b)
    return Response(status=status.HTTP_204_NO_CONTENT)


@api_view(['POST'])
@permission_classes([AllowAny])
def logout_api_view(request):
    """
    Chiude la sessione cookie Django (portale in iframe / altra scheda).
    La PWA azzera i JWT in locale; non reindirizza (evita fetch verso /accounts/login/).
    """
    if getattr(request, 'user', None) and request.user.is_authenticated:
        auth_logout(request)
    return Response(status=status.HTTP_204_NO_CONTENT)


# ---------------------------------------------------------------------------
# VERIFICA IN DUE PASSAGGI (TOTP)
# ---------------------------------------------------------------------------

def _make_jwt(user):
    refresh = RefreshToken.for_user(user)
    return {'access': str(refresh.access_token), 'refresh': str(refresh)}


@api_view(['POST'])
@permission_classes([AllowAny])
def otp_verify(request):
    """Step 2 del login: verifica codice TOTP e restituisce JWT completo."""
    temp_token = request.data.get('temp_token', '')
    otp_code   = request.data.get('otp', '').strip()

    if not temp_token or not otp_code:
        return Response({'detail': 'Campi mancanti.'}, status=status.HTTP_400_BAD_REQUEST)

    try:
        payload = signing.loads(temp_token, salt='gesper_2fa', max_age=300)
    except signing.SignatureExpired:
        return Response({'detail': 'Sessione scaduta, ricomincia il login.'}, status=status.HTTP_401_UNAUTHORIZED)
    except signing.BadSignature:
        return Response({'detail': 'Token non valido.'}, status=status.HTTP_400_BAD_REQUEST)

    from django.contrib.auth import get_user_model
    User = get_user_model()
    try:
        user = User.objects.get(id=payload['user_id'])
    except User.DoesNotExist:
        return Response({'detail': 'Utente non trovato.'}, status=status.HTTP_404_NOT_FOUND)

    if not user.totp_enabled or not user.totp_secret:
        return Response({'detail': '2FA non configurato.'}, status=status.HTTP_400_BAD_REQUEST)

    totp = pyotp.TOTP(user.totp_secret)
    if not totp.verify(otp_code, valid_window=1):
        return Response({'detail': 'Codice non valido o scaduto.'}, status=status.HTTP_401_UNAUTHORIZED)

    _backend = getattr(
        user,
        "backend",
        settings.AUTHENTICATION_BACKENDS[0],
    )
    auth_login(request, user, backend=_backend)
    tokens = _make_jwt(user)
    user_data = _user_payload_for_api(user, request)
    return Response({**tokens, 'user': user_data})


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def totp_setup(request):
    """Genera un nuovo segreto TOTP + QR code SVG (non ancora attivato)."""
    secret = pyotp.random_base32()
    label  = f'GESPER:{request.user.username}'
    uri    = pyotp.totp.TOTP(secret).provisioning_uri(name=label, issuer_name='GESPER')

    # QR code come SVG inline (nessuna dipendenza Pillow)
    factory = qrcode.image.svg.SvgImage
    img     = qrcode.make(uri, image_factory=factory, box_size=8)
    buf     = io.BytesIO()
    img.save(buf)
    svg_b64 = base64.b64encode(buf.getvalue()).decode()

    return Response({
        'secret':   secret,
        'qr_svg':   svg_b64,   # base64 di SVG
    })


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def totp_enable(request):
    """Attiva il 2FA dopo aver verificato il primo codice OTP."""
    secret   = request.data.get('secret', '').strip()
    otp_code = request.data.get('otp', '').strip()

    if not secret or not otp_code:
        return Response({'detail': 'Campi mancanti.'}, status=status.HTTP_400_BAD_REQUEST)

    totp = pyotp.TOTP(secret)
    if not totp.verify(otp_code, valid_window=1):
        return Response({'detail': 'Codice non valido. Riprova.'}, status=status.HTTP_400_BAD_REQUEST)

    request.user.totp_secret  = secret
    request.user.totp_enabled = True
    request.user.save(update_fields=['totp_secret', 'totp_enabled'])
    return Response({'detail': 'Verifica in due passaggi attivata.'})


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def totp_disable(request):
    """Disattiva il 2FA dopo aver verificato il codice OTP corrente."""
    otp_code = request.data.get('otp', '').strip()

    if not otp_code:
        return Response({'detail': 'Inserisci il codice OTP corrente per disattivare.'}, status=status.HTTP_400_BAD_REQUEST)

    if not request.user.totp_enabled:
        return Response({'detail': '2FA non attivo.'}, status=status.HTTP_400_BAD_REQUEST)

    totp = pyotp.TOTP(request.user.totp_secret)
    if not totp.verify(otp_code, valid_window=1):
        return Response({'detail': 'Codice non valido.'}, status=status.HTTP_400_BAD_REQUEST)

    request.user.totp_secret  = ''
    request.user.totp_enabled = False
    request.user.save(update_fields=['totp_secret', 'totp_enabled'])
    return Response({'detail': 'Verifica in due passaggi disattivata.'})


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def totp_status(request):
    """Restituisce lo stato attuale del 2FA per l'utente."""
    return Response({'enabled': request.user.totp_enabled})


# ---------------------------------------------------------------------------
# TIMBRATURA (Entrata / Uscita — fino a 3 turni per giorno)
# ---------------------------------------------------------------------------

def _get_dipendente(user):
    try:
        return user.dipendente
    except Exception:
        return None


def _api_can_access_proposta(user, proposta):
    """Come _get_proposta_con_permesso per dipendente/candidato (JWT)."""
    if not getattr(proposta, 'dipendente_id', None):
        return False
    dip = _get_dipendente(user)
    if dip and proposta.dipendente_id == dip.pk:
        return True
    profilo = getattr(user, 'profilo_candidato', None)
    if profilo and getattr(profilo, 'dipendente_id', None) == proposta.dipendente_id:
        return True
    return False


def _turni_oggi(p):
    """Restituisce lista turni [{entrata, uscita, slot}] dalla presenza."""
    slots = [
        (p.ora_entrata,  p.ora_uscita,  1),
        (p.ora_entrata2, p.ora_uscita2, 2),
        (p.ora_entrata3, p.ora_uscita3, 3),
    ]
    result = []
    for ent, usc, slot in slots:
        if ent:
            result.append({
                'slot':    slot,
                'entrata': ent.strftime('%H:%M'),
                'uscita':  usc.strftime('%H:%M') if usc else None,
            })
    return result


def _stato_response(p):
    turni = _turni_oggi(p) if p else []
    # Può entrare se: nessun turno aperto (tutti i turni aperti hanno uscita)
    #   E ha ancora slot liberi (< 3 turni registrati)
    turno_aperto = next((t for t in turni if t['uscita'] is None), None)
    slot_occupati = len(turni)
    return {
        'turni':        turni,
        'in_servizio':  turno_aperto is not None,
        'puo_entrare':  turno_aperto is None and slot_occupati < 3,
        'puo_uscire':   turno_aperto is not None,
        # retrocompatibilità con Dashboard
        'checkin':  turni[0]['entrata'] if turni else None,
        'checkout': turni[-1]['uscita'] if turni and turni[-1]['uscita'] else None,
    }


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def checkin_stato(request):
    dip = _get_dipendente(request.user)
    if not dip:
        return Response({'detail': 'Dipendente non trovato.'}, status=status.HTTP_404_NOT_FOUND)
    try:
        p = Presenza.objects.get(dipendente=dip, data=date.today())
    except Presenza.DoesNotExist:
        p = None
    return Response(_stato_response(p))


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def checkin_view(request):
    """Registra un'entrata nel primo slot libero."""
    dip = _get_dipendente(request.user)
    if not dip:
        return Response({'detail': 'Dipendente non trovato.'}, status=status.HTTP_404_NOT_FOUND)

    ora_ora = datetime.now().time().replace(second=0, microsecond=0)

    p, _ = Presenza.objects.get_or_create(
        dipendente=dip,
        data=date.today(),
        defaults={'azienda': dip.azienda, 'causale': 'P', 'registrata_da': request.user},
    )

    # Trova il primo slot con entrata nulla
    if not p.ora_entrata:
        p.ora_entrata = ora_ora
        p.save(update_fields=['ora_entrata'])
    elif p.ora_uscita and not p.ora_entrata2:
        p.ora_entrata2 = ora_ora
        p.save(update_fields=['ora_entrata2'])
    elif p.ora_uscita2 and not p.ora_entrata3:
        p.ora_entrata3 = ora_ora
        p.save(update_fields=['ora_entrata3'])
    else:
        # C'è un turno aperto o tutti e 3 i slot sono occupati
        if not p.ora_uscita or (p.ora_entrata2 and not p.ora_uscita2) or (p.ora_entrata3 and not p.ora_uscita3):
            return Response({'detail': 'Hai già un turno aperto. Registra prima l\'uscita.'}, status=status.HTTP_409_CONFLICT)
        return Response({'detail': 'Hai già registrato 3 turni oggi. Contatta l\'ufficio.'}, status=status.HTTP_409_CONFLICT)

    return Response({'ora': ora_ora.strftime('%H:%M'), **_stato_response(p)}, status=status.HTTP_201_CREATED)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def checkout_view(request):
    """Registra un'uscita nel primo turno aperto (entrata senza uscita)."""
    dip = _get_dipendente(request.user)
    if not dip:
        return Response({'detail': 'Dipendente non trovato.'}, status=status.HTTP_404_NOT_FOUND)

    ora_ora = datetime.now().time().replace(second=0, microsecond=0)

    try:
        p = Presenza.objects.get(dipendente=dip, data=date.today())
    except Presenza.DoesNotExist:
        return Response({'detail': 'Nessuna entrata registrata oggi.'}, status=status.HTTP_400_BAD_REQUEST)

    if p.ora_entrata3 and not p.ora_uscita3:
        p.ora_uscita3 = ora_ora
        p.save(update_fields=['ora_uscita3'])
    elif p.ora_entrata2 and not p.ora_uscita2:
        p.ora_uscita2 = ora_ora
        p.save(update_fields=['ora_uscita2'])
    elif p.ora_entrata and not p.ora_uscita:
        p.ora_uscita = ora_ora
        p.save(update_fields=['ora_uscita'])
    else:
        return Response({'detail': 'Nessun turno aperto da chiudere.'}, status=status.HTTP_400_BAD_REQUEST)

    return Response({'ora': ora_ora.strftime('%H:%M'), **_stato_response(p)})


# ---------------------------------------------------------------------------
# PRESENZE
# ---------------------------------------------------------------------------

@api_view(['GET'])
@permission_classes([IsAuthenticated])
def presenze_view(request):
    dip = _get_dipendente(request.user)
    if not dip:
        return Response({'detail': 'Dipendente non trovato.'}, status=status.HTTP_404_NOT_FOUND)

    oggi = date.today()
    try:
        anno = int(request.query_params.get('anno', oggi.year))
        mese = int(request.query_params.get('mese', oggi.month))
    except ValueError:
        return Response({'detail': 'Parametri non validi.'}, status=status.HTTP_400_BAD_REQUEST)

    presenze = Presenza.objects.filter(
        dipendente=dip, data__year=anno, data__month=mese
    ).order_by('data')

    result = []
    for p in presenze:
        ore = None
        try:
            ore_float = p.ore_lavorate()
            if ore_float:
                h = int(ore_float)
                m = int((ore_float - h) * 60)
                ore = f'{h}h {m:02d}m'
        except Exception:
            pass

        result.append({
            'data':       p.data.isoformat(),
            'entrata':    p.ora_entrata.strftime('%H:%M')  if p.ora_entrata  else None,
            'uscita':     p.ora_uscita.strftime('%H:%M')   if p.ora_uscita   else None,
            'entrata2':   p.ora_entrata2.strftime('%H:%M') if p.ora_entrata2 else None,
            'uscita2':    p.ora_uscita2.strftime('%H:%M')  if p.ora_uscita2  else None,
            'entrata3':   p.ora_entrata3.strftime('%H:%M') if p.ora_entrata3 else None,
            'uscita3':    p.ora_uscita3.strftime('%H:%M')  if p.ora_uscita3  else None,
            'causale':    p.causale,
            'ore_totali': ore,
        })

    return Response(result)


# ---------------------------------------------------------------------------
# DOCUMENTI
# ---------------------------------------------------------------------------

@api_view(['GET'])
@permission_classes([IsAuthenticated])
def documenti_view(request):
    dip = _get_dipendente(request.user)
    if not dip:
        return Response({'detail': 'Dipendente non trovato.'}, status=status.HTTP_404_NOT_FOUND)

    # Come portale dipendente: buste e contratti sempre elencabili; altri tipi se marcati visibili.
    docs = (
        Documento.objects.filter(dipendente=dip)
        .filter(
            Q(visibile_al_dipendente=True)
            | Q(tipo='busta_paga')
            | Q(tipo='contratto'),
        )
        .prefetch_related(
            'estrazioni_motore_v4',
            'movimenti_import_paghe',
        )
        .order_by('-data_caricamento', '-id')
    )

    MESI = ['', 'Gennaio', 'Febbraio', 'Marzo', 'Aprile', 'Maggio', 'Giugno',
            'Luglio', 'Agosto', 'Settembre', 'Ottobre', 'Novembre', 'Dicembre']

    TIPO_LABEL = {
        'busta_paga':  'Busta paga',
        'certificato': 'CUD / Cert. fiscale',
        'contratto':   'Contratto',
        'privacy':     'Privacy',
        'carichi_famiglia': 'Carichi famiglia',
        'documento_identita': 'Documento identità',
        'permesso_soggiorno': 'Permesso soggiorno',
        'codice_fiscale_doc': 'Tessera sanitaria',
        'curriculum':  'Curriculum',
        'attestato':   'Attestato',
        'abilitazione':'Abilitazione',
        'titolo_studio':'Titolo studio',
        'certificazione':'Certificazione',
        'altro':       'F24',
    }

    def _periodo_sort(d):
        """Chiave unica decrescente: periodo retributivo reale (più grande = più recente)."""
        import re
        import os

        # 1) Motore v4: mese/anno/natura ufficiali sul cedolino
        try:
            ced = d.estrazioni_motore_v4.first()
        except Exception:
            ced = None
        if ced and getattr(ced, 'anno', None) and getattr(ced, 'mese', None):
            try:
                cm, ca = int(ced.mese), int(ced.anno)
            except (TypeError, ValueError):
                cm, ca = 0, 0
            if 1 <= cm <= 12 and ca >= 1990:
                nat_key = str(getattr(ced, 'natura_busta', '') or 'ORDINARIA').upper()
                nat_rank = {'ORDINARIA': 1, 'TREDICESIMA': 2, 'QUATTORDICESIMA': 3}.get(nat_key, 1)
                return ca * 10000 + cm * 100 + nat_rank

        # 2) Import paghe collegato (stesso criterio usato altrove nel gestionale)
        try:
            mov = (
                d.movimenti_import_paghe.filter(tipo='BUSTA')
                .order_by('-anno', '-mese', '-id')
                .first()
            )
        except Exception:
            mov = None
        if mov and getattr(mov, 'mese', None) and getattr(mov, 'anno', None):
            try:
                cm, ca = int(mov.mese), int(mov.anno)
            except (TypeError, ValueError):
                cm, ca = 0, 0
            if 1 <= cm <= 12 and ca >= 1990:
                nat_key = str(getattr(mov, 'natura_busta', '') or 'ORDINARIA').upper()
                nat_rank = {'ORDINARIA': 1, 'TREDICESIMA': 2, 'QUATTORDICESIMA': 3}.get(nat_key, 1)
                return ca * 10000 + cm * 100 + nat_rank

        try:
            mov_f24 = (
                d.movimenti_import_paghe.filter(tipo='F24')
                .order_by('-anno', '-mese', '-id')
                .first()
            )
        except Exception:
            mov_f24 = None
        if mov_f24 and getattr(mov_f24, 'mese', None) and getattr(mov_f24, 'anno', None):
            try:
                cm, ca = int(mov_f24.mese), int(mov_f24.anno)
            except (TypeError, ValueError):
                cm, ca = 0, 0
            if 1 <= cm <= 12 and ca >= 1990:
                return ca * 10000 + cm * 100 + 1

        filename = os.path.basename(d.file.name) if d.file else ''

        # 3) Busta paga: periodo da descrizione / data (stessa logica portale web)
        if d.tipo == 'busta_paga':
            pm, pa = parse_periodo_busta(d)
            if pm and pa and 1 <= int(pm) <= 12:
                return int(pa) * 10000 + int(pm) * 100 + 1

        # 4) Pattern nome file noti
        m = re.match(r'busta_(\d{2})_(\d{4})_', filename)
        if m:
            mese, anno = int(m.group(1)), int(m.group(2))
            return anno * 10000 + mese * 100 + 1
        m = re.match(r'f24_(\d{2})_(\d{4})_', filename)
        if m:
            mese, anno = int(m.group(1)), int(m.group(2))
            return anno * 10000 + mese * 100 + 1
        m = re.match(r'cud_(\d{4})_', filename)
        if m:
            y = int(m.group(1))
            return y * 10000 + 101

        dt = d.data_caricamento.date()
        return dt.year * 10000 + dt.month * 100 + dt.day

    def _nome_leggibile(d):
        import re, os
        filename = os.path.basename(d.file.name) if d.file else ''
        # Come portale «Miei documenti»: titolo da descrizione se presente
        if d.tipo == 'contratto':
            tit = (d.descrizione or '').strip()
            if tit:
                return tit
        # busta_01_2024_dip_18_p1.pdf → Busta paga Gennaio 2024
        m = re.match(r'busta_(\d{2})_(\d{4})_', filename)
        if m:
            mese, anno = int(m.group(1)), m.group(2)
            return f'Busta paga {MESI[mese]} {anno}'
        # f24_01_2024_... → F24 Gennaio 2024
        m = re.match(r'f24_(\d{2})_(\d{4})_', filename)
        if m:
            mese, anno = int(m.group(1)), m.group(2)
            return f'F24 {MESI[mese]} {anno}'
        # cud_2024_dip_18_p1-3.pdf → CUD 2024
        m = re.match(r'cud_(\d{4})_', filename)
        if m:
            return f'CUD {m.group(1)}'
        # contratto_definitivo_... → Contratto
        if 'contratto' in filename.lower():
            return 'Contratto di lavoro'
        # fallback: descrizione o filename leggibile
        return d.descrizione or filename.replace('_', ' ').replace('.pdf', '').title() or 'Documento'

    # ── Contratti: stessa risoluzione PDF di rapporto_di_lavoro.views._resolve_fieldfile_pdf_contratto_archiviato
    from rapporto_di_lavoro.models import PropostaAssunzione, RapportoDiLavoro
    import os as _os
    import re as _re

    rapporti = list(
        RapportoDiLavoro.objects.filter(dipendente=dip).order_by('-data_inizio_rapporto', '-id')
    )
    numero_to_rapporto_id = {r.numero_contratto: r.id for r in rapporti}

    # Una query: ultimo documento per descrizione (stesso effetto di .filter(…, descr).first() in loop)
    _contr_desc_to_latest: dict[str, Documento] = {}
    for _d in (
        Documento.objects.filter(
            dipendente_id=dip.pk,
            tipo='contratto',
            visibile_al_dipendente=True,
        )
        .exclude(file='')
        .order_by('-data_caricamento', '-id')
    ):
        _k = (_d.descrizione or '').strip()
        if _k and _k not in _contr_desc_to_latest:
            _contr_desc_to_latest[_k] = _d

    def _sorgente_pdf_contratto_rapporto(rap):
        """'rapporto' | 'documento' | 'generato' — allineato a _resolve_fieldfile_pdf_contratto_archiviato."""
        if rap.file_contratto_pdf and rap.file_contratto_pdf.name:
            return 'rapporto'
        num = rap.numero_contratto
        for descr in (
            f'Contratto firmato cartaceo {num}',
            f'Contratto definitivo {num}',
        ):
            doc = _contr_desc_to_latest.get(descr)
            if doc and doc.file and doc.file.name:
                return 'documento'
        return 'generato'

    def _rapporto_id_per_documento_contratto(d):
        """Se il PDF va servito come contratto_pdf(rap), la PWA usa contratto_rapporto_download."""
        des = (d.descrizione or '').strip()
        for num, rid in numero_to_rapporto_id.items():
            if des == f'Contratto definitivo {num}' or des == f'Contratto firmato cartaceo {num}':
                return rid
        fn = _os.path.basename(d.file.name) if d.file else ''
        m = _re.match(r'(?i)contratto_definitivo_(.+)\.pdf$', fn)
        if m:
            num = m.group(1).strip()
            return numero_to_rapporto_id.get(num)
        return None

    # Se il PDF effettivo è su file_contratto_pdf, non mostrare anche le copie archivio con stesso numero
    # (stesso criterio: solo rapporti con PDF sul rapporto; una query per tutti gli id da nascondere)
    _descr_hide: list[str] = []
    for rap in rapporti:
        if not (rap.file_contratto_pdf and rap.file_contratto_pdf.name):
            continue
        num = rap.numero_contratto
        _descr_hide.append(f'Contratto firmato cartaceo {num}')
        _descr_hide.append(f'Contratto definitivo {num}')
    if _descr_hide:
        doc_ids_to_hide = set(
            Documento.objects.filter(
                dipendente=dip,
                tipo='contratto',
                descrizione__in=set(_descr_hide),
            ).values_list('id', flat=True)
        )
    else:
        doc_ids_to_hide = set()

    rap_ids = [r.id for r in rapporti]
    proposte_by_rapporto = {}
    if rap_ids:
        for p in PropostaAssunzione.objects.filter(contratto_generato_id__in=rap_ids).select_related(
            'dipendente', 'dipendente__azienda'
        ):
            proposte_by_rapporto[p.contratto_generato_id] = p

    def _etichetta_contratto_rapporto(rap, proposta):
        """Stesso testo dello storico eventi alla firma candidato (firma_proposta_candidato → EventoStorico)."""
        if proposta is not None and proposta.data_firma_candidato:
            ts = proposta.data_firma_candidato
            luogo = proposta.luogo_firma_candidato_effettivo
            ip = (proposta.ip_firma_candidato or '').strip() or '—'
            dip_str = str(proposta.dipendente)
            return (
                f'Proposta {proposta.numero_proposta} firmata dal candidato '
                f'{dip_str} — {luogo}, {ts.strftime("%d/%m/%Y %H:%M")} (IP: {ip})'
            )
        return f'Contratto n. {rap.numero_contratto}'

    def _etichetta_proposta_senza_contratto(p):
        """Titolo lista per proposta non ancora collegata a un RapportoDiLavoro."""
        if p.data_firma_candidato:
            ts = p.data_firma_candidato
            luogo = p.luogo_firma_candidato_effettivo
            ip = (p.ip_firma_candidato or '').strip() or '—'
            dip_str = str(p.dipendente)
            return (
                f'Proposta {p.numero_proposta} firmata dal candidato '
                f'{dip_str} — {luogo}, {ts.strftime("%d/%m/%Y %H:%M")} (IP: {ip})'
            )
        return f'Proposta {p.numero_proposta} — {p.get_stato_display()}'

    result = []
    for d in docs:
        if not d.file:
            continue
        if d.id in doc_ids_to_hide:
            continue
        rid_contratto = None
        if d.tipo == 'contratto':
            rid_contratto = _rapporto_id_per_documento_contratto(d)
        pobj = proposte_by_rapporto.get(rid_contratto) if rid_contratto else None
        result.append({
            'id':              d.id,
            'rapporto_id':     rid_contratto,
            'proposta_id':     pobj.id if pobj else None,
            'numero_proposta': pobj.numero_proposta if pobj else None,
            'nome':            _nome_leggibile(d),
            'tipo':            d.tipo,
            'tipo_label':      TIPO_LABEL.get(d.tipo, d.tipo),
            'data':            d.data_caricamento.date().isoformat(),
            'periodo_sort':    _periodo_sort(d),
        })

    for rap in rapporti:
        if _sorgente_pdf_contratto_rapporto(rap) == 'documento':
            # PDF servito dalla riga Documento (visibile + file); evita duplicato sintetico
            continue
        prop = proposte_by_rapporto.get(rap.id)
        d0 = rap.data_inizio_rapporto
        nome = _etichetta_contratto_rapporto(rap, prop)
        if prop is not None and prop.data_firma_candidato:
            data_disp = prop.data_firma_candidato
            ps = data_disp.year * 10000 + data_disp.month * 100 + data_disp.day
        else:
            ps = d0.year * 10000 + d0.month * 100 + 1 if d0 else 0
            data_disp = rap.data_sottoscrizione or d0
        result.append({
            'id': None,
            'rapporto_id': rap.id,
            'proposta_id': prop.id if prop else None,
            'numero_proposta': prop.numero_proposta if prop else None,
            'nome': nome,
            'tipo': 'contratto',
            'tipo_label': TIPO_LABEL['contratto'],
            'data': data_disp.isoformat() if data_disp else '',
            'periodo_sort': ps,
        })

    # Proposte senza contratto generato (PDF proposta ancora rilevante, niente riga rapporto duplicata)
    proposte_già_in_lista = {row['proposta_id'] for row in result if row.get('proposta_id')}
    for p in (
        PropostaAssunzione.objects.filter(dipendente=dip, contratto_generato__isnull=True)
        .select_related('dipendente', 'dipendente__azienda')
        .order_by('-data_creazione', '-id')
    ):
        if p.id in proposte_già_in_lista:
            continue
        dc = p.data_firma_candidato or p.data_creazione
        if dc:
            ddate = dc.date() if isinstance(dc, datetime) else dc
            ps = ddate.year * 10000 + ddate.month * 100 + ddate.day
        else:
            ps = 0
        result.append({
            'id': None,
            'rapporto_id': None,
            'proposta_id': p.id,
            'numero_proposta': p.numero_proposta,
            'nome': _etichetta_proposta_senza_contratto(p),
            'tipo': 'proposta',
            'tipo_label': 'Proposta di assunzione',
            'data': dc.isoformat() if dc else '',
            'periodo_sort': ps,
        })

    # Ordine stabile: periodo decrescente, poi id documento, poi id rapporto
    result.sort(
        key=lambda row: (
            -(row['periodo_sort'] or 0),
            -(row.get('id') or 0),
            -(row.get('rapporto_id') or 0),
        )
    )

    return Response(result)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def documento_download(request, doc_id):
    """Scarica un documento del dipendente autenticato via JWT."""
    import os
    from django.http import FileResponse
    dip = _get_dipendente(request.user)
    if not dip:
        return Response({'detail': 'Dipendente non trovato.'}, status=status.HTTP_404_NOT_FOUND)
    try:
        doc = Documento.objects.filter(pk=doc_id, dipendente=dip).filter(
            Q(visibile_al_dipendente=True)
            | Q(tipo='busta_paga')
            | Q(tipo='contratto'),
        ).get()
    except Documento.DoesNotExist:
        return Response({'detail': 'Documento non trovato.'}, status=status.HTTP_404_NOT_FOUND)
    if not doc.file:
        return Response({'detail': 'File non disponibile.'}, status=status.HTTP_404_NOT_FOUND)
    try:
        filename = os.path.basename(doc.file.name)
        return FileResponse(doc.file.open('rb'), as_attachment=True, filename=filename)
    except Exception:
        return Response({'detail': 'Errore nel download.'}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def contratto_rapporto_download(request, rapporto_id):
    """
    PDF contratto da RapportoDiLavoro (stessa logica della vista web contratto_pdf):
    file archiviato, documento scanner, oppure generazione al volo.
    """
    import os
    from django.http import FileResponse, HttpResponse
    from rapporto_di_lavoro.models import RapportoDiLavoro
    from rapporto_di_lavoro.views import (
        _genera_contratto_pdf_bytes,
        _resolve_fieldfile_pdf_contratto_archiviato,
    )

    dip = _get_dipendente(request.user)
    if not dip:
        return Response({'detail': 'Dipendente non trovato.'}, status=status.HTTP_404_NOT_FOUND)
    try:
        rap = RapportoDiLavoro.objects.get(pk=rapporto_id, dipendente=dip)
    except RapportoDiLavoro.DoesNotExist:
        return Response({'detail': 'Contratto non trovato.'}, status=status.HTTP_404_NOT_FOUND)

    ff = _resolve_fieldfile_pdf_contratto_archiviato(rap)
    if ff is not None:
        try:
            fn = os.path.basename(ff.name) or f'contratto_{rap.numero_contratto}.pdf'
            return FileResponse(ff.open('rb'), as_attachment=True, filename=fn)
        except FileNotFoundError:
            pass
    try:
        pdf_bytes = _genera_contratto_pdf_bytes(rap)
    except Exception:
        return Response({'detail': 'Impossibile generare il PDF del contratto.'}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
    fn = f'contratto_{rap.numero_contratto}.pdf'
    resp = HttpResponse(pdf_bytes, content_type='application/pdf')
    resp['Content-Disposition'] = f'attachment; filename="{fn}"'
    return resp


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def proposta_download(request, proposta_id):
    """PDF proposta di assunzione (stesso output di rapporto_di_lavoro.views.proposta_pdf)."""
    from django.http import HttpResponse
    from rapporto_di_lavoro.models import PropostaAssunzione
    from rapporto_di_lavoro.views import _genera_proposta_pdf, _proposta_context_extra

    try:
        proposta = PropostaAssunzione.objects.get(pk=proposta_id)
    except PropostaAssunzione.DoesNotExist:
        return Response({'detail': 'Proposta non trovata.'}, status=status.HTTP_404_NOT_FOUND)
    if not _api_can_access_proposta(request.user, proposta):
        return Response({'detail': 'Accesso negato.'}, status=status.HTTP_403_FORBIDDEN)
    extra = _proposta_context_extra(proposta)
    buffer = _genera_proposta_pdf(proposta, extra)
    fn = f'proposta_{proposta.numero_proposta}.pdf'
    resp = HttpResponse(buffer.getvalue(), content_type='application/pdf')
    resp['Content-Disposition'] = f'attachment; filename="{fn}"'
    return resp


# ---------------------------------------------------------------------------
# SESSIONE / REGISTRAZIONE CANDIDATO / PROFILO CANDIDATO
# ---------------------------------------------------------------------------

@api_view(['GET'])
@permission_classes([IsAuthenticated])
def api_me_view(request):
    """Stato sessione e ruoli (routing app: candidato, HR, consulente)."""
    return Response(_user_payload_for_api(request.user, request))


@api_view(['POST'])
@permission_classes([AllowAny])
def register_candidato_request_otp_api(request):
    """Passo 1 — valida dati e invia OTP via e-mail."""
    from django.core.exceptions import ValidationError as DjangoValidationError

    from accounts.forms import CandidatoRegistrazioneForm
    from accounts.registrazione_otp import costruisci_payload_da_form_cleaned, crea_sessione_e_invia_otp

    if request.user.is_authenticated:
        return Response({'detail': 'Già autenticato.'}, status=status.HTTP_400_BAD_REQUEST)

    data = request.data
    if not isinstance(data, dict):
        data = dict(data)
    if 'website' not in data:
        data = {**data, 'website': ''}

    form = CandidatoRegistrazioneForm(data)
    if not form.is_valid():
        return Response({'errors': form.errors}, status=status.HTTP_400_BAD_REQUEST)

    try:
        payload = costruisci_payload_da_form_cleaned(form.cleaned_data)
        session_id = crea_sessione_e_invia_otp(payload)
    except DjangoValidationError as e:
        msg = e.messages[0] if e.messages else str(e)
        return Response({'detail': msg}, status=status.HTTP_400_BAD_REQUEST)

    return Response(
        {
            'detail': 'Ti abbiamo inviato un codice a 6 cifre via e-mail. Inseriscilo per completare la registrazione.',
            'session_id': session_id,
        },
        status=status.HTTP_200_OK,
    )


@api_view(['POST'])
@permission_classes([AllowAny])
def register_candidato_complete_api(request):
    """Passo 2 — verifica OTP e crea account (nome utente nome.cognome, password = codice fiscale)."""
    from django.core.exceptions import ValidationError as DjangoValidationError

    from accounts.registrazione_otp import completa_registrazione_con_otp
    from accounts.views_registration import _invia_email_verifica, _notifica_hr_nuova_registrazione
    from log_attivita.utils import registra_log

    if request.user.is_authenticated:
        return Response({'detail': 'Già autenticato.'}, status=status.HTTP_400_BAD_REQUEST)

    session_id = (request.data.get('session_id') or '').strip()
    otp = request.data.get('otp', '')
    if not session_id:
        return Response({'detail': 'session_id obbligatorio.'}, status=status.HTTP_400_BAD_REQUEST)

    try:
        user, token = completa_registrazione_con_otp(session_id, otp)
    except DjangoValidationError as e:
        msg = e.messages[0] if e.messages else str(e)
        return Response({'detail': msg}, status=status.HTTP_400_BAD_REQUEST)

    _invia_email_verifica(request, user, token)
    registra_log(
        user, None, 'registrazione',
        descrizione=f'Nuova registrazione candidato (API app): {user.first_name} {user.last_name} ({user.email})',
        request=request,
    )
    try:
        _notifica_hr_nuova_registrazione(request, user)
    except Exception:
        pass

    return Response(
        {
            'detail': 'Registrazione completata. Controlla la posta elettronica per attivare l’account.',
            'username': user.username,
            'email': user.email,
        },
        status=status.HTTP_201_CREATED,
    )


def _serialize_profilo_candidato(profilo, request):
    from accounts.models import ProfiloCandidato
    from accounts.utils import controlla_completezza_profilo

    try:
        from rapporto_di_lavoro.models import Mansione

        mansioni_choices = list(
            Mansione.objects.filter(attivo=True)
            .order_by('ordinamento', 'nome')
            .values_list('nome', flat=True)
        )
    except Exception:
        mansioni_choices = []

    p = profilo

    def _fu(fieldfile):
        if fieldfile and fieldfile.name:
            try:
                return outbound_absolute_uri(request, fieldfile.url)
            except Exception:
                return None
        return None

    comp = controlla_completezza_profilo(p)

    def _d(val):
        return val.isoformat() if val else None

    return {
        'tipo_documento_choices': [{'value': c[0], 'label': c[1]} for c in ProfiloCandidato.TIPO_DOCUMENTO_CHOICES],
        'tipo_rapporto_choices': [{'value': c[0], 'label': c[1]} for c in ProfiloCandidato.TIPO_RAPPORTO_CHOICES],
        'mansioni_choices': mansioni_choices,
        'codice_fiscale': p.codice_fiscale or '',
        'data_nascita': _d(p.data_nascita),
        'luogo_nascita': p.luogo_nascita or '',
        'sesso': p.sesso or '',
        'nazionalita': p.nazionalita or '',
        'indirizzo': p.indirizzo or '',
        'cap': p.cap or '',
        'citta': p.citta or '',
        'provincia': p.provincia or '',
        'regione_residenza': p.regione_residenza or '',
        'telefono': p.telefono or '',
        'tipo_documento': p.tipo_documento or '',
        'numero_documento': p.numero_documento or '',
        'data_emissione_documento': _d(p.data_emissione_documento),
        'scadenza_documento': _d(p.scadenza_documento),
        'file_documento_url': _fu(p.file_documento),
        'file_codice_fiscale_url': _fu(p.file_codice_fiscale),
        'iban': p.iban or '',
        'num_familiari_a_carico': p.num_familiari_a_carico,
        'dettaglio_familiari': p.dettaglio_familiari or '',
        'dichiarazione_no_condanne': p.dichiarazione_no_condanne,
        'mansione_aspirata': p.mansione_aspirata or '',
        'competenze': p.competenze or '',
        'data_disponibilita': _d(p.data_disponibilita),
        'tipo_rapporto_preferito': p.tipo_rapporto_preferito or 'entrambi',
        'ore_settimanali_preferite': str(p.ore_settimanali_preferite) if p.ore_settimanali_preferite is not None else '',
        'livello_aspirato': p.livello_aspirato or '',
        'note_candidatura': p.note_candidatura or '',
        'paga_giornaliera_attesa': str(p.paga_giornaliera_attesa) if p.paga_giornaliera_attesa is not None else '',
        'profilo_completato': p.profilo_completato,
        'completezza': {
            'completo': comp['completo'],
            'mancanti': [{'campo': a[0], 'label': a[1]} for a in comp['mancanti']],
            'consigliati': [{'campo': a[0], 'label': a[1]} for a in comp['consigliati']],
            'percentuale': comp['percentuale'],
            'doc_scaduto': comp['doc_scaduto'],
            'ha_file_documento': bool(p.file_documento and p.file_documento.name),
            'ha_file_codice_fiscale': bool(p.file_codice_fiscale and p.file_codice_fiscale.name),
        },
    }


@api_view(['GET', 'PATCH'])
@permission_classes([IsAuthenticated])
@parser_classes([JSONParser, FormParser, MultiPartParser])
def candidato_profilo_api(request):
    """Lettura e aggiornamento profilo candidato (multipart per allegati e foto)."""
    from django.utils import timezone
    from accounts.forms import ProfiloCandidatoForm
    from accounts.models import ProfiloCandidato
    from accounts.views_candidato import _sincronizza_dipendente

    user = request.user
    if not user.has_ruolo('candidato'):
        return Response({'detail': 'Funzione riservata ai candidati.'}, status=status.HTTP_403_FORBIDDEN)

    profilo, _ = ProfiloCandidato.objects.get_or_create(user=user)

    if request.method == 'GET':
        return Response(_serialize_profilo_candidato(profilo, request))

    # PATCH
    form = ProfiloCandidatoForm(request.POST, request.FILES, instance=profilo)
    if not form.is_valid():
        return Response({'errors': form.errors}, status=status.HTTP_400_BAD_REQUEST)

    p = form.save(commit=False)
    if not p.profilo_completato:
        p.data_completamento = timezone.now()
    p.profilo_completato = True
    _sincronizza_dipendente(user, p)
    p.save()
    form.save_m2m()

    profilo.refresh_from_db()
    return Response(_serialize_profilo_candidato(profilo, request))


# ---------------------------------------------------------------------------
# PROFILO DIPENDENTE
# ---------------------------------------------------------------------------

@api_view(['GET'])
@permission_classes([IsAuthenticated])
def profilo_view(request):
    dip = _get_dipendente(request.user)
    if not dip:
        return Response({'detail': 'Dipendente non trovato.'}, status=status.HTTP_404_NOT_FOUND)

    # Rapporto di lavoro attivo più recente
    rdl_data = None
    try:
        from rapporto_di_lavoro.models import RapportoDiLavoro
        rdl = RapportoDiLavoro.objects.filter(
            dipendente=dip
        ).order_by('-decorrenza_validita_da').first()
        if rdl:
            rdl_data = {
                'tipo_contratto':       str(rdl.tipo_contratto) if rdl.tipo_contratto else None,
                'livello_ccnl':         str(rdl.livello_ccnl)   if rdl.livello_ccnl   else None,
                'qualifica':            rdl.qualifica,
                'ore_settimanali':      str(rdl.ore_settimanali) if rdl.ore_settimanali else None,
                'paga_base_mensile':    str(rdl.paga_base_mensile) if rdl.paga_base_mensile else None,
                'stipendio_lordo':      str(rdl.stipendio_lordo_mensile) if rdl.stipendio_lordo_mensile else None,
                'data_inizio':          rdl.data_inizio_rapporto.isoformat() if rdl.data_inizio_rapporto else None,
                'data_fine':            rdl.data_fine_rapporto.isoformat()   if rdl.data_fine_rapporto   else None,
                'stato':                rdl.stato,
                'ferie_annuali':        rdl.giorni_ferie_annuali,
                'permessi_annuali':     rdl.giorni_permesso_annuali,
                'tredicesima':          rdl.tredicesima,
                'quattordicesima':      rdl.quattordicesima,
            }
    except Exception:
        pass

    return Response({
        'nome':            dip.nome,
        'cognome':         dip.cognome,
        'codice_fiscale':  dip.codice_fiscale,
        'data_nascita':    dip.data_nascita.isoformat()    if dip.data_nascita    else None,
        'data_assunzione': dip.data_assunzione.isoformat() if dip.data_assunzione else None,
        'ruolo':           dip.ruolo,
        'azienda':         dip.azienda.nome if dip.azienda else '',
        'email':           dip.email or '',
        'telefono':        dip.telefono or '',
        'stato':           dip.stato,
        'contratto':       rdl_data,
    })


# ---------------------------------------------------------------------------
# NOTIFICHE
# ---------------------------------------------------------------------------

@api_view(['GET'])
@permission_classes([IsAuthenticated])
def notifiche_view(request):
    """Notifiche del dipendente allineate a stato richiesta/workflow."""
    dip = _get_dipendente(request.user)
    if not dip:
        return Response({'detail': 'Dipendente non trovato.'}, status=status.HTTP_404_NOT_FOUND)

    # Workflow pending: richiesta con almeno uno step in attesa.
    from workflow.models import RichiestaApprovazione
    from django.db.models import Exists, OuterRef
    workflow_pending_qs = RichiestaApprovazione.objects.filter(
        richiesta_id=OuterRef('pk'),
        stato='in_attesa',
    )

    # Ultime 20 richieste:
    # - già gestite (approvata/rifiutata/chiusa), oppure
    # - inviate ma in workflow di approvazione (stato operativo "in approvazione").
    richieste = Richiesta.objects.filter(
        dipendente=dip,
    ).annotate(
        workflow_pending=Exists(workflow_pending_qs),
    ).order_by('-data_richiesta')[:20]

    result = []
    for r in richieste:
        workflow_pending = bool(getattr(r, 'workflow_pending', False))
        if r.stato == 'inviata' and not workflow_pending:
            # Non mostrare richieste appena inviate senza workflow attivo.
            continue

        if r.stato == 'approvata':
            emoji = '✅'
            stato_api = 'approvata'
            titolo_stato = 'approvata'
        elif r.stato == 'rifiutata':
            emoji = '❌'
            stato_api = 'rifiutata'
            titolo_stato = 'rifiutata'
        elif workflow_pending:
            emoji = '🕒'
            stato_api = 'in_approvazione'
            titolo_stato = 'in approvazione'
        else:
            emoji = 'ℹ️'
            stato_api = r.stato
            titolo_stato = r.get_stato_display().lower()

        result.append({
            'id':          r.id,
            'titolo':      f'{emoji} Richiesta {r.get_tipo_display()} {titolo_stato}',
            'testo':       f'{r.data_inizio} → {r.data_fine}' + (f' — {r.note_risposta}' if r.note_risposta else ''),
            'stato':       stato_api,
            'workflow_pending': workflow_pending,
            'data':        r.data_risposta.date().isoformat() if r.data_risposta else r.data_richiesta.date().isoformat(),
        })

    # Conteggio non lette (approvate/rifiutate nell'ultima settimana)
    from django.utils import timezone
    from datetime import timedelta
    cutoff = timezone.now() - timedelta(days=7)
    non_lette = Richiesta.objects.filter(
        dipendente=dip,
        stato__in=['approvata', 'rifiutata'],
        data_risposta__gte=cutoff,
    ).count()

    return Response({'non_lette': non_lette, 'notifiche': result})


# ---------------------------------------------------------------------------
# CAMBIO PASSWORD
# ---------------------------------------------------------------------------

@api_view(['POST'])
@permission_classes([IsAuthenticated])
def cambio_password(request):
    vecchia = request.data.get('vecchia_password', '')
    nuova   = request.data.get('nuova_password', '')

    if not vecchia or not nuova:
        return Response({'detail': 'Campi mancanti.'}, status=status.HTTP_400_BAD_REQUEST)

    if len(nuova) < 8:
        return Response({'detail': 'La nuova password deve essere di almeno 8 caratteri.'}, status=status.HTTP_400_BAD_REQUEST)

    if not request.user.check_password(vecchia):
        return Response({'detail': 'Password attuale non corretta.'}, status=status.HTTP_400_BAD_REQUEST)

    request.user.set_password(nuova)
    request.user.save()
    return Response({'detail': 'Password aggiornata con successo.'})


# ---------------------------------------------------------------------------
# FERIE / PERMESSI
# ---------------------------------------------------------------------------

@api_view(['GET', 'POST'])
@permission_classes([IsAuthenticated])
def ferie_view(request):
    dip = _get_dipendente(request.user)
    if not dip:
        return Response({'detail': 'Dipendente non trovato.'}, status=status.HTTP_404_NOT_FOUND)

    if request.method == 'GET':
        from workflow.models import RichiestaApprovazione
        from django.db.models import Exists, OuterRef
        workflow_pending_qs = RichiestaApprovazione.objects.filter(
            richiesta_id=OuterRef('pk'),
            stato='in_attesa',
        )
        richieste = Richiesta.objects.filter(
            dipendente=dip,
            tipo__in=['ferie', 'permesso'],
        ).annotate(
            workflow_pending=Exists(workflow_pending_qs),
        ).order_by('-data_richiesta')[:20]

        result = []
        for r in richieste:
            workflow_pending = bool(getattr(r, 'workflow_pending', False))
            stato_api = 'in_approvazione' if (r.stato == 'inviata' and workflow_pending) else r.stato
            stato_label = 'In approvazione' if (r.stato == 'inviata' and workflow_pending) else r.get_stato_display()
            result.append({
                'id':             r.id,
                'tipo':           r.tipo,
                'tipo_label':     r.get_tipo_display(),
                'data_inizio':    r.data_inizio.isoformat() if r.data_inizio else None,
                'data_fine':      r.data_fine.isoformat()   if r.data_fine   else None,
                'motivo':         r.motivo,
                'stato':          stato_api,
                'stato_label':    stato_label,
                'workflow_pending': workflow_pending,
                'data_richiesta': r.data_richiesta.date().isoformat(),
                'note_risposta':  r.note_risposta,
            })
        return Response(result)

    # POST — nuova richiesta
    tipo        = request.data.get('tipo', 'ferie')
    data_inizio = request.data.get('data_inizio')
    data_fine   = request.data.get('data_fine')
    motivo      = request.data.get('motivo', '')

    if tipo not in ['ferie', 'permesso']:
        return Response({'detail': 'Tipo non valido.'}, status=status.HTTP_400_BAD_REQUEST)
    if not data_inizio or not data_fine:
        return Response({'detail': 'Date obbligatorie.'}, status=status.HTTP_400_BAD_REQUEST)

    try:
        d_inizio = date.fromisoformat(data_inizio)
        d_fine   = date.fromisoformat(data_fine)
    except ValueError:
        return Response({'detail': 'Formato data non valido (YYYY-MM-DD).'}, status=status.HTTP_400_BAD_REQUEST)

    if d_fine < d_inizio:
        return Response({'detail': 'La data fine non può essere precedente alla data inizio.'}, status=status.HTTP_400_BAD_REQUEST)

    r = Richiesta.objects.create(
        dipendente=dip,
        azienda=dip.azienda,
        tipo=tipo,
        data_inizio=d_inizio,
        data_fine=d_fine,
        motivo=motivo,
        richiesta_da=request.user,
    )
    return Response({
        'id':     r.id,
        'stato':  r.stato,
        'detail': 'Richiesta inviata con successo.',
    }, status=status.HTTP_201_CREATED)


# ---------------------------------------------------------------------------
# PUSH NOTIFICATIONS
# ---------------------------------------------------------------------------

def send_push_to_user(user, title, body, url=None):
    """Invia una notifica push a tutte le subscription di un utente.

    ``url`` è il path o URL aperto al tap (stessa origine del sito GESPER). Se omesso,
    punta alla lista richieste del dipendente (coerente con ``FORCE_SCRIPT_NAME``).
    """
    if url is None:
        url = reverse('lista_richieste')
    subs = PushSubscription.objects.filter(user=user)
    payload = json.dumps({'title': title, 'body': body, 'url': url})
    failed = []
    for sub in subs:
        try:
            webpush(
                subscription_info={
                    'endpoint': sub.endpoint,
                    'keys': {'p256dh': sub.p256dh, 'auth': sub.auth},
                },
                data=payload,
                vapid_private_key=settings.VAPID_PRIVATE_KEY,
                vapid_claims={
                    'sub': f'mailto:{settings.VAPID_CLAIMS_EMAIL}',
                },
            )
        except WebPushException as e:
            # Subscription scaduta/invalida → rimuovila
            if e.response and e.response.status_code in (404, 410):
                failed.append(sub.id)
    if failed:
        PushSubscription.objects.filter(id__in=failed).delete()


@api_view(['GET'])
@permission_classes([AllowAny])
def push_vapid_public(request):
    """Restituisce la chiave pubblica VAPID per il frontend."""
    return Response({'publicKey': settings.VAPID_PUBLIC_KEY})


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def push_subscribe(request):
    """Salva o aggiorna una push subscription."""
    endpoint = request.data.get('endpoint')
    p256dh   = request.data.get('p256dh')
    auth     = request.data.get('auth')

    if not endpoint or not p256dh or not auth:
        return Response({'detail': 'endpoint, p256dh e auth sono obbligatori.'}, status=status.HTTP_400_BAD_REQUEST)

    PushSubscription.objects.update_or_create(
        endpoint=endpoint,
        defaults={'user': request.user, 'p256dh': p256dh, 'auth': auth},
    )
    return Response({'detail': 'Subscription salvata.'}, status=status.HTTP_201_CREATED)


@api_view(['DELETE', 'POST'])
@permission_classes([IsAuthenticated])
def push_unsubscribe(request):
    """Rimuove una push subscription."""
    endpoint = request.data.get('endpoint')
    if endpoint:
        PushSubscription.objects.filter(user=request.user, endpoint=endpoint).delete()
    else:
        PushSubscription.objects.filter(user=request.user).delete()
    return Response({'detail': 'Subscription rimossa.'}, status=status.HTTP_204_NO_CONTENT)
