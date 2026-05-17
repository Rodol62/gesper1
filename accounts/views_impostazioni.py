import logging
from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.mail import get_connection, EmailMessage
from django.http import JsonResponse
from django.shortcuts import render, redirect
from django.urls import reverse

from .models import ConfigurazioneSistema
from .gestione_database import can_gestione_database
from .tenant import get_azienda_operativa
from log_attivita.utils import registra_log
from anagrafiche.models import Azienda
from anagrafiche.forms import indirizzo_sede_legale_riga_da_azienda, query_geocode_nominatim_da_azienda
from anagrafiche.nominatim_geocode import (
    geocode_indirizzo_it,
    is_geocode_address_not_found,
    user_agent_gesper,
)

logger = logging.getLogger('django')

IMPOSTAZIONI_TABS = frozenset({'sito', 'email', 'aspetto', 'paghe'})


def _azienda_riferimento_sede_legale(request):
    """Azienda di cui mostrare la sede legale: unica in archivio o operativa in sessione."""
    if Azienda.objects.count() == 1:
        return Azienda.objects.first()
    return get_azienda_operativa(request.user, request.session)


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
            'nome_sito', 'nome_azienda', 'partita_iva',
            'firmatario_amministratore_nome', 'firmatario_amministratore_ruolo',
            'simulatore_paga_riepilogo_cedolino_canonico',
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
        # Checkbox assente nel POST se si salva da un tab che non include il campo
        if 'simulatore_paga_riepilogo_cedolino_canonico' not in self.data:
            obj.simulatore_paga_riepilogo_cedolino_canonico = prev.simulatore_paga_riepilogo_cedolino_canonico
        if self._azienda_sede:
            obj.indirizzo_sede = indirizzo_sede_legale_riga_da_azienda(self._azienda_sede)
        else:
            obj.indirizzo_sede = prev.indirizzo_sede
        if commit:
            obj.save()
        return obj

    def __init__(self, *args, azienda_sede=None, **kwargs):
        self._azienda_sede = azienda_sede
        super().__init__(*args, **kwargs)
        if self.is_bound:
            for name, field in self.fields.items():
                if name in self.errors:
                    cls = field.widget.attrs.get('class', '')
                    field.widget.attrs['class'] = f'{cls} is-invalid'.strip()


@login_required
def impostazioni_sistema(request):
    if not can_gestione_database(request.user):
        messages.error(request, 'Accesso non autorizzato.')
        return redirect('centro_moduli')

    config = ConfigurazioneSistema.get()
    azienda_sede = _azienda_riferimento_sede_legale(request)
    tab = request.GET.get('tab', 'sito')
    if tab not in IMPOSTAZIONI_TABS:
        tab = 'sito'

    if request.method == 'POST':
        action = request.POST.get('action', 'salva')

        if action == 'test_email':
            _test_email(request, config)
            return redirect(f"{reverse('impostazioni_sistema')}?tab=email")

        if action == 'test_connessione':
            _test_connessione_smtp(request, config)
            return redirect(f"{reverse('impostazioni_sistema')}?tab=email")

        form = ImpostazioniForm(request.POST, instance=config, azienda_sede=azienda_sede)
        tab_da_post = request.POST.get('tab_corrente', tab)
        if tab_da_post not in IMPOSTAZIONI_TABS:
            tab_da_post = 'sito'
        if form.is_valid():
            form.save()
            messages.success(request, 'Impostazioni salvate.')
            registra_log(request.user, None, 'impostazioni',
                         descrizione=f'Modificate impostazioni di sistema (tab: {tab_da_post})',
                         request=request)
            return redirect(f"{reverse('impostazioni_sistema')}?tab={tab_da_post}")
        tab = tab_da_post
    else:
        form = ImpostazioniForm(instance=config, azienda_sede=azienda_sede)

    indirizzo_sede_display = (
        indirizzo_sede_legale_riga_da_azienda(azienda_sede)
        if azienda_sede
        else (config.indirizzo_sede or '')
    )

    geocode_nominatim_query = (
        query_geocode_nominatim_da_azienda(azienda_sede) if azienda_sede else ''
    )
    if len((geocode_nominatim_query or '').strip()) < 8:
        geocode_nominatim_query = (indirizzo_sede_display or config.indirizzo_sede or '').strip()

    return render(request, 'accounts/impostazioni.html', {
        'form': form,
        'config': config,
        'tab': tab,
        'azienda_sede_riferimento': azienda_sede,
        'indirizzo_sede_display': indirizzo_sede_display,
        'geocode_nominatim_query': geocode_nominatim_query,
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

    cfg = ConfigurazioneSistema.get()
    contact = (
        (getattr(request.user, 'email', None) or '')
        or (cfg.smtp_user or '')
        or (cfg.email_notifiche_hr or '')
    ).strip()
    ua = user_agent_gesper(contact)

    result = geocode_indirizzo_it(indirizzo, user_agent=ua)
    if result.get('ok'):
        return JsonResponse(
            {
                'ok': True,
                'lat': result['lat'],
                'lon': result['lon'],
                'display_name': result.get('display_name', ''),
            }
        )
    err = result.get('error', 'Errore sconosciuto.')
    if err == 'Indirizzo troppo corto.':
        return JsonResponse({'ok': False, 'error': err}, status=400)
    if is_geocode_address_not_found(err):
        return JsonResponse({'ok': False, 'error': err}, status=404)
    return JsonResponse({'ok': False, 'error': err}, status=502)


@login_required
def maps_estrai_coordinate_impostazioni(request):
    """Estrae lat/lon da testo/URL Google Maps (regex + eventuale follow HTTP dei link corti)."""
    if request.method != 'POST':
        return JsonResponse({'ok': False, 'error': 'Metodo non consentito.'}, status=405)

    if not can_gestione_database(request.user):
        return JsonResponse({'ok': False, 'error': 'Accesso non autorizzato.'}, status=403)

    incolla = (request.POST.get('incolla') or request.POST.get('url') or '').strip()
    if len(incolla) < 4:
        return JsonResponse({'ok': False, 'error': 'Incolla almeno 4 caratteri.'}, status=400)

    from anagrafiche.google_maps_coords import estrai_coordinate_maps

    out = estrai_coordinate_maps(incolla)
    if out.get('ok'):
        return JsonResponse({'ok': True, 'lat': out['lat'], 'lon': out['lon']})
    return JsonResponse({'ok': False, 'error': out.get('error', 'Coordinate non trovate.')}, status=404)


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
