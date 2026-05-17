import logging
from django.contrib import messages
from django.core.mail import get_connection, EmailMessage
from django.shortcuts import render, redirect
from django.urls import reverse
from django.utils import timezone
from django.conf import settings

from django.core.exceptions import ValidationError

from .forms import (
    CandidatoRegistrazioneForm,
    CandidatoRegistrazioneOtpConfermaForm,
    CustomUserCreationForm,
)
from .registrazione_otp import (
    completa_registrazione_con_otp,
    costruisci_payload_da_form_cleaned,
    crea_sessione_e_invia_otp,
)
from .models import User, ConfigurazioneSistema
from .outbound_uri import outbound_absolute_uri
from log_attivita.utils import registra_log

logger = logging.getLogger('django')


# ── Registrazione interna (HR / admin) ───────────────────────────
def register(request):
    if request.method == 'POST':
        form = CustomUserCreationForm(request.POST)
        if form.is_valid():
            user = form.save(commit=False)
            user.privacy_accettata = form.cleaned_data['privacy_accettata']
            user.privacy_data = timezone.now()
            user.convalidato = False
            user.save()
            messages.success(request, 'Registrazione completata! Attendi la convalida dal supervisore.')
            return redirect('login')
    else:
        form = CustomUserCreationForm()
    return render(request, 'registration/register.html', {'form': form})


# ── Registrazione candidato (pubblica: OTP e-mail + verifica indirizzo) ─
def register_candidato(request):
    """
    Passo 1 — dati + invio codice OTP via e-mail.
    Passo 2 — codice OTP; crea utente (nome utente nome.cognome, password = CF), invia e-mail verifica.
    """
    if request.user.is_authenticated:
        return redirect('candidato_dashboard')

    if request.method == 'POST' and request.POST.get('reg_step') == 'otp':
        sid = request.session.get('regcand_session_id')
        form_otp = CandidatoRegistrazioneOtpConfermaForm(request.POST)
        if not sid:
            messages.error(request, 'Sessione scaduta. Ricompila il modulo di registrazione.')
            return redirect('register_candidato')
        if form_otp.is_valid():
            try:
                user, token = completa_registrazione_con_otp(sid, form_otp.cleaned_data['otp'])
            except ValidationError as e:
                msg = e.messages[0] if e.messages else str(e)
                form_otp.add_error('otp', msg)
            else:
                link = _invia_email_verifica(request, user, token)
                request.session['verifica_link'] = link
                request.session['verifica_email'] = user.email
                request.session['verifica_username'] = user.username
                request.session.pop('regcand_session_id', None)

                logger.info(f"[CANDIDATO REG] Nuovo: {user.email} (username={user.username})")
                registra_log(
                    user, None, 'registrazione',
                    descrizione=f'Nuova registrazione candidato: {user.first_name} {user.last_name} ({user.email})',
                    request=request,
                )
                try:
                    _notifica_hr_nuova_registrazione(request, user)
                except Exception:
                    pass
                return redirect('candidato_verifica_email_inviata')
        return render(
            request,
            'candidato/registrazione.html',
            {'form': CandidatoRegistrazioneForm(), 'otp_form': form_otp, 'step': 2},
        )

    if request.method == 'POST':
        form = CandidatoRegistrazioneForm(request.POST)
        if form.is_valid():
            try:
                payload = costruisci_payload_da_form_cleaned(form.cleaned_data)
                sid = crea_sessione_e_invia_otp(payload)
            except ValidationError as e:
                msg = e.messages[0] if e.messages else str(e)
                form.add_error(None, msg)
            else:
                request.session['regcand_session_id'] = sid
                return render(
                    request,
                    'candidato/registrazione.html',
                    {
                        'form': CandidatoRegistrazioneForm(),
                        'otp_form': CandidatoRegistrazioneOtpConfermaForm(),
                        'step': 2,
                    },
                )
        else:
            logger.warning(f"[CANDIDATO REG] Form non valido: {form.errors}")
    else:
        request.session.pop('regcand_session_id', None)
        form = CandidatoRegistrazioneForm()

    return render(request, 'candidato/registrazione.html', {'form': form, 'step': 1})


def _invia_email_verifica(request, user, token):
    """
    Invia l'e-mail con il link di verifica (48 ore).
    Usa le credenziali SMTP dalla ConfigurazioneSistema se disponibili,
    altrimenti usa il backend di default (filebased in sviluppo).
    Restituisce il link.
    """
    config = ConfigurazioneSistema.get()
    nome_sito = config.nome_sito or 'GESPER'
    path = reverse('verifica_email', kwargs={'token': token})
    link = outbound_absolute_uri(request, path)
    corpo = (
        f"Gentile {user.first_name} {user.last_name},\n\n"
        f"grazie per esserti registrato/a su {nome_sito}.\n\n"
        f"Per accedere alla procedura utilizza:\n"
        f"  User ID (nome utente): {user.username}\n"
        f"  Password: il codice fiscale indicato in fase di registrazione\n\n"
        f"Per attivare il tuo account clicca sul link seguente (valido 48 ore):\n\n"
        f"  {link}\n\n"
        f"Se non hai richiesto questa registrazione, ignora questa e-mail.\n\n"
        f"Il team {nome_sito}"
    )
    try:
        if config.smtp_user and config.smtp_password:
            # Usa SMTP configurato nel DB
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
                subject=f"{nome_sito} — Verifica il tuo indirizzo e-mail",
                body=corpo,
                from_email=config.from_email(),
                to=[user.email],
                connection=conn,
            )
            msg.send()
            logger.info(f"[EMAIL VERIFICA] Inviata via SMTP a {user.email}")
        else:
            # Fallback: backend di default (filebased in sviluppo)
            msg = EmailMessage(
                subject=f"{nome_sito} — Verifica il tuo indirizzo e-mail",
                body=corpo,
                from_email=getattr(settings, 'DEFAULT_FROM_EMAIL', 'noreply@gesper.it'),
                to=[user.email],
            )
            msg.send()
            logger.info(f"[EMAIL VERIFICA] Inviata via backend default a {user.email}")
    except Exception as exc:
        logger.error(f"[EMAIL VERIFICA] Errore invio a {user.email}: {exc}")
    # Link sempre visibile nel log (utile in sviluppo)
    logger.info(f"[EMAIL VERIFICA LINK] {user.email} → {link}")
    return link


def invia_email_testuale(destinatario, oggetto, corpo_text):
    """
    Invio e-mail testuale con SMTP da ConfigurazioneSistema se configurato,
    altrimenti backend predefinito Django (stesso schema di _invia_email_verifica).
    Ritorna True se l'invio è riuscito.
    """
    config = ConfigurazioneSistema.get()
    try:
        if config.smtp_user and config.smtp_password:
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
                subject=oggetto,
                body=corpo_text,
                from_email=config.from_email(),
                to=[destinatario],
                connection=conn,
            )
            msg.send()
            logger.info(f"[EMAIL TESTUALE] Inviata via SMTP a {destinatario}")
        else:
            msg = EmailMessage(
                subject=oggetto,
                body=corpo_text,
                from_email=getattr(settings, 'DEFAULT_FROM_EMAIL', 'noreply@gesper.it'),
                to=[destinatario],
            )
            msg.send()
            logger.info(f"[EMAIL TESTUALE] Inviata via backend default a {destinatario}")
        return True
    except Exception as exc:
        logger.error(f"[EMAIL TESTUALE] Errore invio a {destinatario}: {exc}")
        return False


def _notifica_hr_nuova_registrazione(request, user):
    """
    Invia una notifica all'indirizzo HR configurato nelle impostazioni
    quando un nuovo candidato completa la registrazione.
    """
    config = ConfigurazioneSistema.get()
    destinatario_hr = config.email_notifiche_hr
    if not destinatario_hr:
        logger.info(f"[NOTIFICA HR] Nessun indirizzo HR configurato, notifica non inviata.")
        return

    nome_sito = config.nome_sito or 'GESPER'
    link_dettaglio = outbound_absolute_uri(
        request,
        reverse('candidato_admin_dettaglio', kwargs={'user_id': user.id}),
    )
    corpo = (
        f"Nuovo candidato registrato su {nome_sito}.\n\n"
        f"Nome: {user.first_name} {user.last_name}\n"
        f"E-mail: {user.email}\n"
        f"Data registrazione: {user.date_joined.strftime('%d/%m/%Y %H:%M')}\n\n"
        f"Il candidato deve ancora verificare il proprio indirizzo e-mail.\n\n"
        f"Visualizza il profilo nel pannello HR:\n  {link_dettaglio}\n\n"
        f"— {nome_sito}"
    )
    try:
        if config.smtp_user and config.smtp_password:
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
                subject=f"[{nome_sito}] Nuovo candidato: {user.first_name} {user.last_name}",
                body=corpo,
                from_email=config.from_email(),
                to=[destinatario_hr],
                connection=conn,
            )
        else:
            msg = EmailMessage(
                subject=f"[{nome_sito}] Nuovo candidato: {user.first_name} {user.last_name}",
                body=corpo,
                from_email=getattr(settings, 'DEFAULT_FROM_EMAIL', 'noreply@gesper.it'),
                to=[destinatario_hr],
            )
        msg.send()
        logger.info(f"[NOTIFICA HR] Inviata a {destinatario_hr} per candidato {user.email}")
    except Exception as exc:
        logger.error(f"[NOTIFICA HR] Errore invio a {destinatario_hr}: {exc}")


def candidato_verifica_email_inviata(request):
    from django.conf import settings
    link = request.session.pop('verifica_link', None)
    email = request.session.pop('verifica_email', None)
    username = request.session.pop('verifica_username', None)
    # Mostra il link solo se DEBUG=True (mai in produzione)
    mostra_link = bool(getattr(settings, 'DEBUG', False))
    return render(request, 'candidato/verifica_email_inviata.html', {
        'verifica_link': link,
        'verifica_email': email,
        'verifica_username': username,
        'mostra_link': mostra_link,
    })


def verifica_email(request, token):
    """Verifica il token e attiva l'account del candidato."""

    try:
        user = User.objects.get(email_token=token)
    except User.DoesNotExist:
        return render(request, 'candidato/verifica_email_errore.html', {
            'motivo': 'Il link di verifica non è valido.'
        })

    # Verifica che abbia il ruolo candidato (dopo il salvataggio ManyToMany)
    if not user.has_ruolo('candidato'):
        # Se non ha ancora il ruolo, lo assegna ora (fix per race condition)
        from .models import Ruolo
        ruolo_candidato, _ = Ruolo.objects.get_or_create(codice='candidato', defaults={'nome': 'Candidato'})
        user.ruoli.add(ruolo_candidato)
        user.save()

    if not user.token_valido(token):
        return render(request, 'candidato/verifica_email_errore.html', {
            'motivo': 'Il link di verifica è scaduto (validità 48 ore). Richiedine uno nuovo.'
        })

    if not user.email_verificata:
        user.email_verificata = True
        user.is_active = True
        user.email_token = ''
        user.email_token_scadenza = None
        user.save(update_fields=[
            'email_verificata', 'is_active', 'email_token', 'email_token_scadenza'
        ])
        logger.info(f"[EMAIL VERIFICA] Account attivato: {user.email}")
        registra_log(user, None, 'verifica_email',
                     descrizione=f'E-mail verificata e account attivato: {user.email}',
                     request=request)

    return render(request, 'candidato/verifica_email_ok.html', {'user': user})


def reinvia_verifica(request):
    """Il candidato può richiedere un nuovo link di verifica."""
    if request.method == 'POST':
        email = request.POST.get('email', '').lower().strip()
        logger.info(f"[REINVIA VERIFICA] Richiesta reinvio per: {email}")
        try:
            user = User.objects.get(email__iexact=email, ruoli__codice='candidato', is_active=False)
            token = user.genera_token_verifica()
            logger.info(f"[REINVIA VERIFICA] Token generato per {user.email}: {token}")
            link = _invia_email_verifica(request, user, token)
            logger.info(f"[REINVIA VERIFICA] Link generato per {user.email}: {link}")
            request.session['verifica_link'] = link
            request.session['verifica_email'] = user.email
            request.session['verifica_username'] = user.username
        except User.DoesNotExist:
            logger.warning(f"[REINVIA VERIFICA] Nessun utente candidato inattivo trovato per: {email}")
        messages.success(request, "Se l'e-mail è registrata e non ancora verificata, riceverai un nuovo link a breve.")
        return redirect('candidato_verifica_email_inviata')

    return render(request, 'candidato/reinvia_verifica.html')
