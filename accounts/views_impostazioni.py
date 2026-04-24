import logging
import json
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from urllib.error import URLError
from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.mail import get_connection, EmailMessage
from django.http import JsonResponse
from django.shortcuts import render, redirect
from django.urls import reverse

from .models import ConfigurazioneSistema
from .gestione_database import can_gestione_database
from log_attivita.utils import registra_log

logger = logging.getLogger('django')


class ImpostazioniForm(forms.ModelForm):
    smtp_password = forms.CharField(
        required=False,
        widget=forms.PasswordInput(render_value=True, attrs={'autocomplete': 'new-password'}),
        label='Password SMTP',
        help_text='Per Gmail usa una "Password per le app" (16 caratteri). Lascia vuoto per non modificarla.',
    )

    class Meta:
        model = ConfigurazioneSistema
        fields = [
            'nome_sito', 'nome_azienda', 'indirizzo_sede', 'partita_iva',
            'presenze_geo_enabled', 'presenze_geo_require_gps', 'presenze_geo_enforce_geofence',
            'presenze_geo_center_lat', 'presenze_geo_center_lon', 'presenze_geo_radius_m',
            'presenze_geo_enforce_for_test',
            'smtp_host', 'smtp_port', 'smtp_use_tls',
            'smtp_user', 'smtp_password',
            'email_mittente_nome', 'email_notifiche_hr',
            'url_pubblica_base',
            'colore_primario', 'colore_accent', 'colore_sfondo',
            'font_famiglia', 'font_dimensione_base',
        ]
        widgets = {
            'colore_primario': forms.TextInput(attrs={'type': 'color', 'style': 'width:56px;height:32px;padding:2px'}),
            'colore_accent':   forms.TextInput(attrs={'type': 'color', 'style': 'width:56px;height:32px;padding:2px'}),
            'colore_sfondo':   forms.TextInput(attrs={'type': 'color', 'style': 'width:56px;height:32px;padding:2px'}),
            'presenze_geo_center_lat': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.000001'}),
            'presenze_geo_center_lon': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.000001'}),
            'presenze_geo_radius_m': forms.NumberInput(attrs={'class': 'form-control', 'min': '1', 'step': '1'}),
        }

    def save(self, commit=True):
        obj = super().save(commit=False)
        prev = ConfigurazioneSistema.objects.get(pk=1)
        if not self.cleaned_data.get('smtp_password'):
            obj.smtp_password = prev.smtp_password
        if commit:
            obj.save()
        return obj


@login_required
def impostazioni_sistema(request):
    if not can_gestione_database(request.user):
        messages.error(request, 'Accesso non autorizzato.')
        return redirect('dashboard_admin')

    config = ConfigurazioneSistema.get()
    tab = request.GET.get('tab', 'sito')

    if request.method == 'POST':
        action = request.POST.get('action', 'salva')

        if action == 'test_email':
            _test_email(request, config)
            return redirect(f"{reverse('impostazioni_sistema')}?tab=email")

        if action == 'test_connessione':
            _test_connessione_smtp(request, config)
            return redirect(f"{reverse('impostazioni_sistema')}?tab=email")

        form = ImpostazioniForm(request.POST, instance=config)
        if form.is_valid():
            form.save()
            tab = request.POST.get('tab_corrente', 'sito')
            messages.success(request, 'Impostazioni salvate.')
            registra_log(request.user, None, 'impostazioni',
                         descrizione=f'Modificate impostazioni di sistema (tab: {tab})',
                         request=request)
            return redirect(f"{reverse('impostazioni_sistema')}?tab={tab}")
    else:
        form = ImpostazioniForm(instance=config)

    return render(request, 'accounts/impostazioni.html', {
        'form': form,
        'config': config,
        'tab': tab,
    })


@login_required
def geocode_impostazioni(request):
    """Risoluzione coordinate da indirizzo (Nominatim/OpenStreetMap)."""
    if request.method != 'POST':
        return JsonResponse({'ok': False, 'error': 'Metodo non consentito.'}, status=405)

    if not can_gestione_database(request.user):
        return JsonResponse({'ok': False, 'error': 'Accesso non autorizzato.'}, status=403)

    indirizzo = (request.POST.get('indirizzo') or '').strip()
    if len(indirizzo) < 5:
        return JsonResponse({'ok': False, 'error': 'Indirizzo troppo corto.'}, status=400)

    query = urlencode({
        'q': indirizzo,
        'format': 'jsonv2',
        'limit': 1,
        'addressdetails': 0,
        'countrycodes': 'it',
    })
    url = f'https://nominatim.openstreetmap.org/search?{query}'

    try:
        req = Request(
            url,
            headers={
                'User-Agent': 'GESPER/1.0 (impostazioni geolocalizzazione)',
                'Accept': 'application/json',
            },
        )
        with urlopen(req, timeout=10) as resp:
            payload = resp.read().decode('utf-8')
        data = json.loads(payload)

        if not data:
            return JsonResponse({'ok': False, 'error': 'Nessun risultato trovato per questo indirizzo.'}, status=404)

        item = data[0]
        lat = float(item['lat'])
        lon = float(item['lon'])
        return JsonResponse({
            'ok': True,
            'lat': round(lat, 6),
            'lon': round(lon, 6),
            'display_name': item.get('display_name', ''),
        })

    except (URLError, ValueError, KeyError, json.JSONDecodeError) as exc:
        logger.error('[GEOCODE IMPOSTAZIONI] Errore geocoding indirizzo "%s": %s', indirizzo, exc)
        return JsonResponse({'ok': False, 'error': 'Servizio geocoding non disponibile al momento.'}, status=502)


def _test_email(request, config):
    """Invia una e-mail di test e salva l'esito nel modello."""
    from django.utils import timezone as tz

    if not config.smtp_host or not config.smtp_user or not config.smtp_password:
        messages.warning(request, 'Configura prima host SMTP, utente e password prima di eseguire il test.')
        return

    destinatario = (request.POST.get('test_destinatario') or '').strip() or config.smtp_user

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
            subject=f'[{config.nome_sito}] Test configurazione e-mail — {tz.now().strftime("%d/%m/%Y %H:%M")}',
            body=(
                f'Questa è una e-mail di test inviata da {config.nome_sito}.\n\n'
                f'Configurazione SMTP utilizzata:\n'
                f'  Host:  {config.smtp_host}:{config.smtp_port}\n'
                f'  TLS:   {"Sì" if config.smtp_use_tls else "No"}\n'
                f'  Utente: {config.smtp_user}\n'
                f'  Mittente: {config.from_email()}\n\n'
                f'Se ricevi questo messaggio la configurazione è corretta.\n\n'
                f'— {config.nome_sito}'
            ),
            from_email=config.from_email(),
            to=[destinatario],
            connection=conn,
        )
        msg.send()
        esito_msg = f'E-mail inviata correttamente a {destinatario}.'
        config.ultimo_test_email_data = tz.now()
        config.ultimo_test_email_esito = 'ok'
        config.ultimo_test_email_messaggio = esito_msg
        config.ultimo_test_email_destinatario = destinatario
        config.save(update_fields=[
            'ultimo_test_email_data', 'ultimo_test_email_esito',
            'ultimo_test_email_messaggio', 'ultimo_test_email_destinatario',
        ])
        messages.success(request, f'✓ {esito_msg} Controlla la casella di posta.')
        logger.info('[SMTP TEST] %s', esito_msg)

    except Exception as exc:
        esito_msg = str(exc)
        config.ultimo_test_email_data = tz.now()
        config.ultimo_test_email_esito = 'errore'
        config.ultimo_test_email_messaggio = esito_msg
        config.ultimo_test_email_destinatario = destinatario
        config.save(update_fields=[
            'ultimo_test_email_data', 'ultimo_test_email_esito',
            'ultimo_test_email_messaggio', 'ultimo_test_email_destinatario',
        ])
        messages.error(request, f'✗ Errore invio e-mail: {exc}')
        logger.error('[SMTP TEST] Errore: %s', exc)


def _test_connessione_smtp(request, config):
    """Verifica solo la connessione SMTP senza inviare messaggi."""
    import smtplib
    import ssl

    if not config.smtp_host:
        messages.warning(request, 'Host SMTP non configurato.')
        return

    try:
        if config.smtp_use_ssl:
            server = smtplib.SMTP_SSL(config.smtp_host, config.smtp_port, timeout=10)
            server.ehlo()
        else:
            context = ssl.create_default_context()
            server = smtplib.SMTP(config.smtp_host, config.smtp_port, timeout=10)
            server.ehlo()
            server.starttls(context=context)
            server.ehlo()

        if config.smtp_user and config.smtp_password:
            server.login(config.smtp_user, config.smtp_password)
            server.quit()
            messages.success(
                request,
                f'✓ Connessione e autenticazione SMTP riuscite '
                f'({config.smtp_host}:{config.smtp_port}, utente: {config.smtp_user}).'
            )
        else:
            server.quit()
            messages.info(
                request,
                f'✓ Connessione al server SMTP riuscita ({config.smtp_host}:{config.smtp_port}). '
                f'Credenziali non impostate — autenticazione non testata.'
            )
        logger.info('[SMTP CONN TEST] OK — %s:%s', config.smtp_host, config.smtp_port)

    except Exception as exc:
        messages.error(request, f'✗ Connessione SMTP fallita: {exc}')
        logger.error('[SMTP CONN TEST] Errore: %s', exc)
