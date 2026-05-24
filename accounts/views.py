# --- IMPORTS ---
from django.http import HttpResponseRedirect
from django.urls import reverse, reverse_lazy
from django.contrib.auth import get_user_model
from django.contrib.auth.views import LoginView, PasswordResetView
from django.contrib.auth.forms import AuthenticationForm
from django.contrib.auth.decorators import login_required, user_passes_test
from django.shortcuts import render, redirect
from django.contrib import messages
from django.utils import timezone
from .forms import CustomPasswordResetForm
from django import forms
from anagrafiche.models import Azienda, Dipendente
from documenti.models import Documento, VoceCedolinoMotoreV4
from accounts.models import MovimentoImportPaghe
from presenze.models import Presenza
from richieste.models import Richiesta
from log_attivita.models import LogAttivita
from log_attivita.utils import registra_log
from django.contrib.auth import logout
from accounts.tenant import get_azienda_operativa
from accounts.gestione_database import can_gestione_database
from accounts.agenda_scadenze import (
    agenda_popup_items,
    build_agenda_scadenze,
    items_in_calendar_month,
)
from django.db.models import Count, Q
from django.core.paginator import Paginator, EmptyPage, PageNotAnInteger
from django.conf import settings as django_settings
from django.utils.http import url_has_allowed_host_and_scheme
import logging

logger_accounts = logging.getLogger(__name__)

# --- UTILS ---
def ensure_admin_supervisor():
    User = get_user_model()
    if not User.objects.filter(username='admin').exists():
        User.objects.create_superuser(
            username='admin',
            password='0070',
            email='admin@example.com',
            convalidato=True,
            privacy_accettata=True,
            # ruolo='admin',  # Assegna ruoli dopo la creazione
        )
    else:
        user = User.objects.get(username='admin')
        user.set_password('0070')
        user.save()


def _navigate_after_login(request, user):
    """Destinazione post-login (portale web) dopo password o secondo passaggio e-mail."""
    if user.is_superuser or user.has_ruolo('admin'):
        return redirect('dashboard_admin')
    if user.has_ruolo('consulente'):
        return redirect('consulente_dashboard')
    if user.has_ruolo('candidato'):
        profilo = getattr(user, 'profilo_candidato', None)
        if profilo and profilo.profilo_completato:
            return redirect('candidato_dashboard')
        return redirect('candidato_completa_profilo')
    if not user.convalidato:
        messages.error(request, "Il tuo account non è ancora stato convalidato dal supervisore.")
        return redirect('profile')
    return redirect('profile')


# --- VIEWS ---
class CustomLoginForm(AuthenticationForm):
    def __init__(self, *args, **kwargs):
        request = kwargs.pop('request', None)
        super().__init__(*args, request=request, **kwargs)
        self.fields['username'].widget = forms.TextInput(attrs={'class': 'form-control', 'autofocus': True})
        self.fields['password'].widget = forms.PasswordInput(attrs={'class': 'form-control'})

class CustomLoginView(LoginView):
    template_name = 'registration/login.html'
    authentication_form = CustomLoginForm

    def dispatch(self, request, *args, **kwargs):
        from accounts.login_email_stepup import pending_otp_session
        from accounts.login_totp_web import pending_totp_session as pending_totp_web_session

        if request.method == 'GET':
            if pending_totp_web_session(request):
                return redirect('login_totp_web')
            if pending_otp_session(request):
                return redirect('login_email_otp')
        return super().dispatch(request, *args, **kwargs)

    def form_valid(self, form):
        user = form.get_user()
        from accounts.login_email_stepup import clear_pending, avvia_stepup_email
        from accounts.login_totp_web import clear_totp_pending, start_totp_pending

        # TOTP (Google Authenticator, Microsoft Authenticator, Aruba Key, …) ha priorità sull’OTP e-mail.
        if getattr(user, 'totp_enabled', False) and (getattr(user, 'totp_secret', None) or '').strip():
            clear_pending(self.request)
            clear_totp_pending(self.request)
            start_totp_pending(self.request, user)
            redirect_field = getattr(django_settings, 'REDIRECT_FIELD_NAME', 'next')
            raw_next = self.request.POST.get(redirect_field) or self.request.GET.get(redirect_field) or ''
            self.request.session['gesper_login_next'] = raw_next
            return redirect('login_totp_web')

        if getattr(user, 'email_stepup_login', False):
            email = (getattr(user, 'email', None) or '').strip()
            if not email:
                messages.warning(
                    self.request,
                    'È attiva la verifica via e-mail al login, ma sul tuo profilo non risulta un indirizzo e-mail. '
                    'Contatta l’amministratore. Accesso senza secondo passaggio.',
                )
            else:
                clear_pending(self.request)
                clear_totp_pending(self.request)
                try:
                    avvia_stepup_email(self.request, user)
                except ValueError as exc:
                    messages.error(self.request, str(exc))
                    return self.form_invalid(form)
                redirect_field = getattr(django_settings, 'REDIRECT_FIELD_NAME', 'next')
                raw_next = self.request.POST.get(redirect_field) or self.request.GET.get(redirect_field) or ''
                self.request.session['gesper_login_next'] = raw_next
                return redirect('login_email_otp')

        redirect_target = self.get_redirect_url()
        response = super().form_valid(form)
        descr_ruoli = ", ".join([r.nome for r in user.ruoli.all()])
        registra_log(user, getattr(user, 'azienda', None), 'login',
                 descrizione=f'Accesso di {user.username} ({descr_ruoli})',
                 request=self.request)
        if redirect_target:
            return response
        return _navigate_after_login(self.request, user)


def login_email_otp(request):
    """Secondo passaggio dopo password: codice monouso inviato all’e-mail del profilo utente."""
    from django.core.cache import cache
    from django.contrib.auth import login as auth_login

    from accounts.login_email_stepup import (
        CACHE_PREFIX,
        SESSION_SID_KEY,
        avvia_stepup_email,
        clear_pending,
        pending_otp_session,
        verifica_e_recupera_uid,
    )

    if not pending_otp_session(request):
        return redirect('login')

    if request.method == 'POST':
        if request.POST.get('cancel'):
            clear_pending(request)
            request.session.pop('gesper_login_next', None)
            messages.info(request, 'Accesso annullato.')
            return redirect('login')

        if request.POST.get('action') == 'resend':
            sid = request.session.get(SESSION_SID_KEY)
            entry = cache.get(f'{CACHE_PREFIX}{sid}') if sid else None
            if not entry:
                messages.error(request, 'Sessione scaduta. Effettua di nuovo il login.')
                return redirect('login')
            User = get_user_model()
            user = User.objects.get(pk=entry['uid'])
            clear_pending(request)
            try:
                avvia_stepup_email(request, user)
                messages.success(request, 'Nuovo codice inviato.')
            except ValueError as exc:
                messages.error(request, str(exc))
                return redirect('login')
            return redirect('login_email_otp')

        uid = verifica_e_recupera_uid(request, request.POST.get('otp', ''))
        if uid is None:
            messages.error(request, 'Codice non valido o scaduto.')
            return render(request, 'registration/login_email_otp.html', {})

        User = get_user_model()
        user = User.objects.get(pk=uid)
        auth_login(request, user, backend='django.contrib.auth.backends.ModelBackend')
        descr_ruoli = ", ".join([r.nome for r in user.ruoli.all()])
        registra_log(
            user,
            getattr(user, 'azienda', None),
            'login',
            descrizione=f'Accesso di {user.username} ({descr_ruoli})',
            request=request,
        )
        next_raw = request.session.pop('gesper_login_next', '') or ''
        if next_raw and url_has_allowed_host_and_scheme(
            next_raw,
            allowed_hosts={request.get_host()},
            require_https=request.is_secure(),
        ):
            return HttpResponseRedirect(next_raw)
        return _navigate_after_login(request, user)

    return render(request, 'registration/login_email_otp.html', {})


def login_totp_web(request):
    """Secondo passaggio dopo password: codice TOTP da app authenticator (RFC 6238)."""
    from django.contrib.auth import login as auth_login

    from accounts.login_totp_web import (
        clear_totp_pending,
        pending_totp_session,
        verify_totp_and_get_uid,
    )

    if not pending_totp_session(request):
        return redirect('login')

    if request.method == 'POST':
        if request.POST.get('cancel'):
            clear_totp_pending(request)
            request.session.pop('gesper_login_next', None)
            messages.info(request, 'Accesso annullato.')
            return redirect('login')

        uid = verify_totp_and_get_uid(request, request.POST.get('otp', ''))
        if uid is None:
            messages.error(request, 'Codice non valido o scaduto.')
            return render(request, 'registration/login_totp_web.html', {})

        User = get_user_model()
        user = User.objects.get(pk=uid)
        auth_login(request, user, backend='django.contrib.auth.backends.ModelBackend')
        descr_ruoli = ", ".join([r.nome for r in user.ruoli.all()])
        registra_log(
            user,
            getattr(user, 'azienda', None),
            'login',
            descrizione=f'Accesso di {user.username} ({descr_ruoli})',
            request=request,
        )
        next_raw = request.session.pop('gesper_login_next', '') or ''
        if next_raw and url_has_allowed_host_and_scheme(
            next_raw,
            allowed_hosts={request.get_host()},
            require_https=request.is_secure(),
        ):
            return HttpResponseRedirect(next_raw)
        return _navigate_after_login(request, user)

    return render(request, 'registration/login_totp_web.html', {})


class CustomPasswordResetView(PasswordResetView):
    """Link nell'e-mail: host/scheme dalla richiesta (o ``url_pubblica_base`` se da localhost)."""

    template_name = 'registration/password_reset_form.html'
    form_class = CustomPasswordResetForm
    email_template_name = 'registration/password_reset_email.html'
    subject_template_name = 'registration/password_reset_subject.txt'
    redirect_authenticated_user = True
    success_url = reverse_lazy('home')

    def form_valid(self, form):
        from accounts.models import ConfigurazioneSistema
        from accounts.outbound_uri import outbound_email_scheme_and_netloc

        proto, netloc = outbound_email_scheme_and_netloc(self.request)
        cfg = ConfigurazioneSistema.get()
        nome = (cfg.nome_sito or "GESPER").strip() or "GESPER"
        self.extra_email_context = {
            "domain": netloc,
            "protocol": proto,
            "site_name": nome,
            "request": self.request,
        }
        return super().form_valid(form)

@login_required
def profile(request):
    user = request.user
    context = {}
    # Variabili di ruolo per il template
    is_admin = user.is_superuser or user.has_ruolo('admin')
    is_dipendente = user.has_ruolo('dipendente')
    is_consulente = user.has_ruolo('consulente')
    context['is_admin'] = is_admin
    context['is_dipendente'] = is_dipendente
    context['is_consulente'] = is_consulente

    # Candidati hanno la propria dashboard
    if user.has_ruolo('candidato'):
        return redirect('candidato_dashboard')

    aziende = Azienda.objects.all()

    if user.is_superuser or user.has_ruolo('admin'):
        context['aziende'] = aziende
        context['azienda_operativa'] = get_azienda_operativa(user, request.session)
        if request.method == 'POST' and 'azienda_id' in request.POST:
            azienda_id = request.POST.get('azienda_id')
            try:
                azienda = Azienda.objects.get(id=azienda_id)
                user.azienda = azienda
                user.save(update_fields=['azienda'])
                request.session['azienda_id'] = azienda.id
                messages.success(request, f"Azienda selezionata: {azienda.nome}")
                return redirect('lista_dipendenti')
            except Azienda.DoesNotExist:
                messages.error(request, "Azienda non trovata.")

    if not user.privacy_accettata:
        messages.error(request, 'Devi accettare la privacy per accedere alle funzioni. Vai su "Modifica profilo" per dare il consenso.')
        context['privacy_blocked'] = True
    if not user.convalidato:
        messages.error(request, 'Il tuo account non è ancora stato convalidato dal supervisore.')
        context['convalida_blocked'] = True

    # Dati specifici per dipendente / HR / supervisore
    if user.has_ruolo('dipendente'):
        try:
            from anagrafiche.models import Dipendente

            dipendente = Dipendente.objects.select_related('azienda').get(utente=user)
            context['dipendente'] = dipendente
            context['documenti'] = (
                Documento.objects.filter(dipendente=dipendente)
                .filter(Q(visibile_al_dipendente=True) | Q(tipo='busta_paga'))
                .order_by('-data_caricamento')[:5]
            )
            context['presenze'] = Presenza.objects.filter(dipendente=dipendente).order_by('-data')[:5]
            context['richieste'] = Richiesta.objects.filter(dipendente=dipendente).order_by('-data_richiesta')[:5]
            from rapporto_di_lavoro.models import RapportoDiLavoro, PropostaAssunzione
            context['contratti'] = RapportoDiLavoro.objects.filter(dipendente=dipendente).order_by('-data_creazione')[:3]
            context['proposte'] = PropostaAssunzione.objects.filter(dipendente=dipendente).order_by('-data_creazione')[:3]

            context['calcolatore_ferie_rol'] = None
            try:
                from datetime import date as date_today

                from rapporto_di_lavoro.calcolatore_ferie_rol_bridge import (
                    preview_parametri_calcolatore_ferie_rol,
                )

                context['calcolatore_ferie_rol'] = preview_parametri_calcolatore_ferie_rol(
                    dipendente, dipendente.azienda, date_today.today()
                )
            except Exception:
                context['calcolatore_ferie_rol'] = None

            contratto_attivo = RapportoDiLavoro.objects.filter(
                dipendente=dipendente,
                stato='sottoscritto',
            ).order_by('-data_ora_sottoscrizione', '-data_creazione').first()
            if contratto_attivo:
                from accounts.views_candidato import _busta_per_fonte
                profilo = getattr(user, 'profilo_candidato', None)
                num_familiari = int(getattr(profilo, 'num_familiari_a_carico', 0) or 0)
                regione = getattr(profilo, 'regione_residenza', 'Sicilia') or 'Sicilia'
                riepilogo_busta = _busta_per_fonte(
                    contratto_attivo,
                    tredicesima=contratto_attivo.tredicesima,
                    quattordicesima=contratto_attivo.quattordicesima,
                    num_familiari_a_carico=num_familiari,
                    regione_residenza=regione,
                )
                if riepilogo_busta:
                    ore_mensili = riepilogo_busta.get('ore_mensili') or 0
                    lordo_base = riepilogo_busta.get('lordo_mensile') or 0
                    rateo_13 = riepilogo_busta.get('rateo_13_lordo_m') or 0
                    rateo_14 = riepilogo_busta.get('rateo_14_lordo_m') or 0
                    lordo_con_ratei = lordo_base + rateo_13 + rateo_14
                    riepilogo_busta['lordo_con_ratei'] = lordo_con_ratei
                    riepilogo_busta['totale_ratei_lordi'] = rateo_13 + rateo_14
                    riepilogo_busta['paga_oraria_lorda_base'] = riepilogo_busta.get('paga_oraria_lorda') or 0
                    riepilogo_busta['paga_oraria_netta_base'] = riepilogo_busta.get('paga_oraria_netta') or 0
                    riepilogo_busta['paga_oraria_netta_con_ratei'] = (
                        (riepilogo_busta.get('netto_con_ratei') or 0) / ore_mensili
                        if ore_mensili else 0
                    )
                context['riepilogo_busta'] = riepilogo_busta
                context['contratto_attivo'] = contratto_attivo
        except Exception:
            pass

    return render(request, 'accounts/profile.html', context)

def is_admin_or_supervisore(user):
    return user.is_superuser or user.has_ruolo('admin') or user.has_ruolo('hr')

@login_required
@user_passes_test(is_admin_or_supervisore)
def test_stato_utente(request):
    user = request.user
    context = {
        'username': user.username,
        'convalidato': user.convalidato,
        'privacy_accettata': user.privacy_accettata,
        'privacy_data': user.privacy_data,
        'ruolo': ', '.join(user.ruoli.values_list('nome', flat=True)) or '—',
        'azienda': user.azienda.nome if user.azienda else '—',
    }
    return render(request, 'accounts/test_stato_utente.html', context)

@login_required
@user_passes_test(can_gestione_database)
def dashboard_admin(request):
    User = get_user_model()
    azienda_operativa = get_azienda_operativa(request.user, request.session)

    if azienda_operativa:
        utenti_count = User.objects.filter(azienda=azienda_operativa).count()
        dipendenti_count = Dipendente.objects.filter(azienda=azienda_operativa).count()
        doc_agg = Documento.objects.filter(azienda=azienda_operativa).aggregate(
            total=Count('id'),
            buste=Count('id', filter=Q(tipo='busta_paga')),
            f24=Count('id', filter=Q(tipo='altro', descrizione__icontains='F24')),
            cud=Count('id', filter=Q(tipo='certificato')),
        )
        documenti_count = doc_agg['total'] or 0
        buste_doc_count = doc_agg['buste'] or 0
        buste_mov_count = MovimentoImportPaghe.objects.filter(
            azienda=azienda_operativa,
            tipo='BUSTA',
        ).count()
        buste_count = max(buste_doc_count, buste_mov_count)
        f24_count = doc_agg['f24'] or 0
        cud_count = doc_agg['cud'] or 0
        presenze_count = Presenza.objects.filter(azienda=azienda_operativa).count()
        richieste_count = Richiesta.objects.filter(azienda=azienda_operativa).count()
        log_count = LogAttivita.objects.filter(azienda=azienda_operativa).count()
    else:
        utenti_count = User.objects.count()
        dipendenti_count = Dipendente.objects.count()
        doc_agg = Documento.objects.aggregate(
            total=Count('id'),
            buste=Count('id', filter=Q(tipo='busta_paga')),
            f24=Count('id', filter=Q(tipo='altro', descrizione__icontains='F24')),
            cud=Count('id', filter=Q(tipo='certificato')),
        )
        documenti_count = doc_agg['total'] or 0
        buste_doc_count = doc_agg['buste'] or 0
        buste_mov_count = MovimentoImportPaghe.objects.filter(tipo='BUSTA').count()
        buste_count = max(buste_doc_count, buste_mov_count)
        f24_count = doc_agg['f24'] or 0
        cud_count = doc_agg['cud'] or 0
        presenze_count = Presenza.objects.count()
        richieste_count = Richiesta.objects.count()
        log_count = LogAttivita.objects.count()

    aziende_count = Azienda.objects.count()
    oggi = timezone.localdate()
    agenda_all = build_agenda_scadenze(azienda_operativa, oggi=oggi)
    context = {
        'utenti_count': utenti_count,
        'dipendenti_count': dipendenti_count,
        'aziende_count': aziende_count,
        'documenti_count': documenti_count,
        'buste_count': buste_count,
        'f24_count': f24_count,
        'cud_count': cud_count,
        'presenze_count': presenze_count,
        'richieste_count': richieste_count,
        'log_count': log_count,
        'azienda_operativa': azienda_operativa,
        'agenda_todo': agenda_popup_items(agenda_all, oggi=oggi),
        'agenda_oggi': oggi,
    }
    return render(request, 'accounts/dashboard_admin.html', context)


@login_required
@user_passes_test(can_gestione_database)
def admin_agenda_scadenze(request):
    """Calendario e elenco scadenze (contratti TD, promemoria F24)."""
    import calendar

    oggi = timezone.localdate()
    try:
        cy = int(request.GET.get('y') or oggi.year)
        cm = int(request.GET.get('m') or oggi.month)
        if cm < 1 or cm > 12 or cy < 2000 or cy > 2100:
            raise ValueError
    except (TypeError, ValueError):
        cy, cm = oggi.year, oggi.month

    azienda_operativa = get_azienda_operativa(request.user, request.session)
    agenda_full = build_agenda_scadenze(azienda_operativa, oggi=oggi)
    month_items = items_in_calendar_month(agenda_full, cy, cm)

    by_day: dict[int, list] = {}
    for it in month_items:
        by_day.setdefault(it['data'].day, []).append(it)

    cal_rows = []
    for week in calendar.monthcalendar(cy, cm):
        row = []
        for day in week:
            if day == 0:
                row.append({'day': 0, 'items': []})
            else:
                row.append({'day': day, 'items': by_day.get(day, [])})
        cal_rows.append(row)

    def _shift(y, mo, d):
        mo += d
        while mo > 12:
            mo -= 12
            y += 1
        while mo < 1:
            mo += 12
            y -= 1
        return y, mo

    py, pm = _shift(cy, cm, -1)
    ny, nm = _shift(cy, cm, 1)

    return render(
        request,
        'accounts/admin_agenda_scadenze.html',
        {
            'azienda_operativa': azienda_operativa,
            'oggi': oggi,
            'cal_year': cy,
            'cal_month': cm,
            'cal_rows': cal_rows,
            'month_items': sorted(month_items, key=lambda x: (x['data'], x['priorita'])),
            'agenda_full': agenda_full,
            'prev_month_url': f"?y={py}&m={pm}",
            'next_month_url': f"?y={ny}&m={nm}",
            'month_title': f'{cm:02d} / {cy}',
        },
    )


@login_required
@user_passes_test(can_gestione_database)
def admin_voci_cedolino_motore_v4_list(request):
    """
    Elenco di tutte le righe voce persistite dal motore cedolino v4 (tabella ``voci_cedolino``),
    con dipendente, periodo e controlli F2 ove presenti.
    """
    azienda_operativa = get_azienda_operativa(request.user, request.session)
    qs = (
        VoceCedolinoMotoreV4.objects.select_related(
            "cedolino",
            "cedolino__dipendente",
            "cedolino__dipendente__azienda",
            "cedolino__documento",
        )
        .order_by(
            "-cedolino__anno",
            "-cedolino__mese",
            "cedolino__dipendente__cognome",
            "cedolino__dipendente__nome",
            "codice",
            "pk",
        )
    )
    if azienda_operativa:
        qs = qs.filter(cedolino__dipendente__azienda=azienda_operativa)

    q = (request.GET.get("q") or "").strip()
    if q:
        qs = qs.filter(
            Q(codice__icontains=q)
            | Q(descrizione__icontains=q)
            | Q(tipo__icontains=q)
            | Q(cedolino__dipendente__nome__icontains=q)
            | Q(cedolino__dipendente__cognome__icontains=q)
            | Q(cedolino__dipendente__codice_fiscale__icontains=q)
        )

    tipo_f = (request.GET.get("tipo") or "").strip()
    if tipo_f:
        qs = qs.filter(tipo=tipo_f)

    try:
        mese_f = int(request.GET.get("mese") or 0)
    except ValueError:
        mese_f = 0
    if 1 <= mese_f <= 12:
        qs = qs.filter(cedolino__mese=mese_f)

    try:
        anno_f = int(request.GET.get("anno") or 0)
    except ValueError:
        anno_f = 0
    if 2000 <= anno_f <= 2100:
        qs = qs.filter(cedolino__anno=anno_f)

    total_filtered = qs.count()
    paginator = Paginator(qs, 80)
    page_param = request.GET.get("page")
    try:
        page_obj = paginator.page(page_param)
    except PageNotAnInteger:
        page_obj = paginator.page(1)
    except EmptyPage:
        page_obj = paginator.page(paginator.num_pages)

    tipi_base = VoceCedolinoMotoreV4.objects.all()
    if azienda_operativa:
        tipi_base = tipi_base.filter(cedolino__dipendente__azienda=azienda_operativa)
    tipi_distinti = sorted(
        {t for t in tipi_base.values_list("tipo", flat=True).distinct() if t},
        key=lambda x: x.upper(),
    )

    context = {
        "page_obj": page_obj,
        "total_filtered": total_filtered,
        "azienda_operativa": azienda_operativa,
        "q": q,
        "tipo_f": tipo_f,
        "mese_f": mese_f,
        "anno_f": anno_f,
        "tipi_distinti": tipi_distinti,
    }
    return render(request, "accounts/admin_voci_cedolino_motore_v4.html", context)


@login_required
def edit_profile(request):
    user = request.user
    if request.method == 'POST':
        # Consenso privacy
        if not user.privacy_accettata and request.POST.get('privacy_accettata') == 'true':
            user.privacy_accettata = True
            user.privacy_data = timezone.now()

        # Campi profilo
        first_name = request.POST.get('first_name', '').strip()
        last_name = request.POST.get('last_name', '').strip()
        email = request.POST.get('email', '').strip().lower()

        errori = []
        if not first_name:
            errori.append('Il nome è obbligatorio.')
        if not last_name:
            errori.append('Il cognome è obbligatorio.')
        if email and get_user_model().objects.filter(email__iexact=email).exclude(pk=user.pk).exists():
            errori.append('Questa email è già utilizzata da un altro account.')

        if errori:
            for e in errori:
                messages.error(request, e)
        else:
            user.first_name = first_name
            user.last_name = last_name
            if email:
                user.email = email
            user.save()
            messages.success(request, 'Profilo aggiornato correttamente.')
            return redirect('edit_profile')

    return render(request, 'accounts/edit_profile.html', {'user': user})

def logout_view(request):
    if request.user.is_authenticated:
        try:
            registra_log(
                request.user,
                getattr(request.user, 'azienda', None),
                'logout',
                descrizione=f'Uscita di {request.user.username}',
                request=request,
            )
        except Exception:
            # Il logout non deve fallire per errori di logging (DB, IP proxy, ecc.)
            logger_accounts.exception('registra_log su logout non riuscita')
    logout(request)
    from accounts.gesper_paths import pwa_app_path

    return redirect(pwa_app_path(request))

# --- VIEW CAMBIO PASSWORD ADMIN (ULTIMA DEL FILE) ---
@login_required
@user_passes_test(can_gestione_database)
def cambia_password_admin(request):
    User = get_user_model()
    user = User.objects.get(username='admin')
    if request.method == 'POST':
        nuova_password = request.POST.get('nuova_password')
        if nuova_password:
            user.set_password(nuova_password)
            user.save()
            messages.success(request, 'Password admin aggiornata!')
            return HttpResponseRedirect(reverse('profile'))
    return render(request, 'accounts/cambia_password_admin.html', {})

# ── Contatta lo sviluppatore ─────────────────────────────────────────────
from django.http import JsonResponse
from django.views.decorators.http import require_POST
from django.core.mail import send_mail

@require_POST
def contatta_sviluppatore(request):
    """Riceve il form di contatto dal footer e invia email allo sviluppatore."""
    nome    = request.POST.get('nome', '').strip()
    email   = request.POST.get('email', '').strip()
    oggetto = request.POST.get('oggetto', '').strip()
    testo   = request.POST.get('testo', '').strip()

    if not (nome and email and testo):
        return JsonResponse({'ok': False, 'errore': 'Compila tutti i campi obbligatori.'})

    soggetto = f'[GESPER] Contatto da {nome}: {oggetto or "(nessun oggetto)"}'
    corpo = (
        f'Messaggio inviato dal footer di GESPER\n'
        f'{"─" * 40}\n'
        f'Da:      {nome}\n'
        f'E-mail:  {email}\n'
        f'Oggetto: {oggetto or "—"}\n'
        f'{"─" * 40}\n\n'
        f'{testo}\n'
    )

    try:
        # Il destinatario è esclusivamente lato server — non compare mai nell'HTML
        _DEV_EMAIL = 'rodol@hotmail.it'
        send_mail(soggetto, corpo, 'rosario.dolcemascolo@gmail.com', [_DEV_EMAIL])
        return JsonResponse({'ok': True})
    except Exception as exc:
        return JsonResponse({'ok': False, 'errore': f'Invio non riuscito: {exc}'})
