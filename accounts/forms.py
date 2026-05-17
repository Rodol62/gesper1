from django import forms
from django.contrib.auth.forms import UserCreationForm, PasswordResetForm
from django.core.exceptions import ValidationError as DjangoValidationError
from django.core.validators import RegexValidator
from django.utils import timezone
from .models import User, ProfiloCandidato
from .validators import CodiceFiscalePasswordValidator
from anagrafiche.codice_fiscale_it import valida_cf as _valida_cf
from anagrafiche.territorio_it import (
    regioni as regioni_it,
    province_per_regione,
    comuni_per_regione_provincia,
    paesi_istat,
)

# ── Costanti validazione ─────────────────────────────────────────
_REGIONI_ITALIANE = {
    'ABRUZZO', 'BASILICATA', 'CALABRIA', 'CAMPANIA', 'EMILIA-ROMAGNA',
    'FRIULI-VENEZIA GIULIA', 'FRIULI VENEZIA GIULIA', 'LAZIO', 'LIGURIA',
    'LOMBARDIA', 'MARCHE', 'MOLISE', 'PIEMONTE', 'PUGLIA', 'SARDEGNA',
    'SICILIA', 'TOSCANA', 'TRENTINO-ALTO ADIGE', 'TRENTINO ALTO ADIGE',
    'UMBRIA', "VALLE D'AOSTA", 'VALLE D AOSTA', 'VENETO',
}

# ── Form esistente (HR / admin) ──────────────────────────────────
class CustomUserCreationForm(UserCreationForm):
    privacy_accettata = forms.BooleanField(
        required=True,
        label="Autorizzo il trattamento dei dati personali ai sensi della normativa privacy italiana (GDPR)",
        help_text="Devi accettare per registrarti.",
    )

    class Meta:
        model = User
        fields = ("username", "email", "password1", "password2", "privacy_accettata")


class CustomPasswordResetForm(PasswordResetForm):
    email = forms.EmailField(
        max_length=254,
        widget=forms.EmailInput(attrs={'autocomplete': 'email', 'class': 'form-control'}),
        label="Email",
    )

    def get_users(self, email):
        users = [u for u in super().get_users(email) if not u.is_superuser and not u.has_ruolo('admin') and not u.has_ruolo('hr')]
        return users

    def send_mail(self, subject_template_name, email_template_name, context,
                  from_email, to_email, html_email_template_name=None):
        import logging
        logging.debug(f"[RESET PASSWORD] INIZIO send_mail per {to_email}")
        from django.core.mail import get_connection, EmailMultiAlternatives
        from django.template import loader
        from .models import ConfigurazioneSistema
        from django.contrib import messages
        config = ConfigurazioneSistema.get()
        subject = loader.render_to_string(subject_template_name, context)
        subject = ''.join(subject.splitlines())
        body = loader.render_to_string(email_template_name, context)
        # Prova invio con parametri da tabella
        try:
            if config.smtp_user and config.smtp_password and config.smtp_host and config.smtp_port:
                use_ssl = config.smtp_port == 465 or config.smtp_use_ssl
                use_tls = config.smtp_port != 465 and config.smtp_use_tls
                connection = get_connection(
                    backend='accounts.email_backend.ConfigurazioneSistemaEmailBackend',
                    host=config.smtp_host,
                    port=config.smtp_port,
                    username=config.smtp_user,
                    password=config.smtp_password,
                    use_tls=use_tls,
                    use_ssl=use_ssl,
                    fail_silently=False,
                )
                from_email = config.from_email()
                logging.info(f"[RESET PASSWORD] Invio tramite configurazione tabella: {config.smtp_host}:{config.smtp_port} user={config.smtp_user}")
                try:
                    email_message = EmailMultiAlternatives(subject, body, from_email, [to_email], connection=connection)
                    email_message.send()
                    logging.info(f"[RESET PASSWORD] Email inviata con successo tramite tabella a {to_email}")
                except Exception as exc:
                    logging.error(f"[RESET PASSWORD] Errore invio mail tramite tabella a {to_email}: {exc}")
                    raise
                return
            else:
                logging.warning(f"[RESET PASSWORD] Configurazione SMTP tabella mancante o incompleta, uso settings Django.")
        except Exception as exc:
            logging.error(f"[RESET PASSWORD] Errore invio mail tramite tabella a {to_email}: {exc}")
            # Se disponibile, mostra errore a video (solo lato admin)
            request = context.get('request')
            if request:
                messages.error(request, f"Errore invio email reset password (configurazione tabella): {exc}")
            # Prosegue con fallback
        # Fallback: invio con settings.py
        try:
            logging.info(f"[RESET PASSWORD] Invio tramite settings Django EMAIL_BACKEND={getattr(get_connection(), 'backend', None)} host={getattr(get_connection(), 'host', None)}")
            try:
                email_message = EmailMultiAlternatives(subject, body, from_email, [to_email])
                email_message.send()
                logging.info(f"[RESET PASSWORD] Email inviata con successo tramite settings a {to_email}")
            except Exception as exc:
                logging.error(f"[RESET PASSWORD] Errore invio mail fallback settings a {to_email}: {exc}")
                request = context.get('request')
                if request:
                    messages.error(request, f"Errore invio email reset password (fallback settings): {exc}")
                raise
        except Exception as exc:
            logging.error(f"[RESET PASSWORD] Errore invio mail fallback settings a {to_email}: {exc}")
            raise


# ── Registrazione candidato (GDPR + OTP e-mail) ───────────────────
class CandidatoRegistrazioneForm(forms.Form):
    """
    Passo 1 — Dati candidato: nome/cognome (per nome utente nome.cognome), e-mail,
    codice fiscale come password iniziale, cellulare come recapito.
    """

    first_name = forms.CharField(
        max_length=100,
        required=True,
        label="Nome",
        widget=forms.TextInput(attrs={
            'class': 'freg-input',
            'autocomplete': 'given-name',
            'placeholder': 'Il tuo nome',
        }),
    )
    last_name = forms.CharField(
        max_length=100,
        required=True,
        label="Cognome",
        widget=forms.TextInput(attrs={
            'class': 'freg-input',
            'autocomplete': 'family-name',
            'placeholder': 'Il tuo cognome',
        }),
    )
    email = forms.EmailField(
        required=True,
        label="Indirizzo e-mail",
        widget=forms.EmailInput(attrs={
            'class': 'freg-input',
            'autocomplete': 'email',
            'placeholder': 'nome@esempio.it',
        }),
        help_text="Riceverai un link di verifica su questo indirizzo.",
    )
    email_conferma = forms.EmailField(
        required=True,
        label="Conferma e-mail",
        widget=forms.EmailInput(attrs={
            'class': 'freg-input',
            'autocomplete': 'off',
            'placeholder': 'Ripeti l\'indirizzo e-mail',
        }),
    )
    codice_fiscale = forms.CharField(
        label='Codice fiscale (password iniziale)',
        min_length=16,
        max_length=16,
        strip=True,
        widget=forms.TextInput(attrs={
            'class': 'freg-input text-uppercase',
            'autocomplete': 'off',
            'maxlength': '16',
            'placeholder': 'es. RSSMRA85M01H501Z',
            'id': 'id_codice_fiscale',
        }),
        help_text='16 caratteri: sarà la tua password di accesso fino al primo cambio.',
    )
    telefono = forms.CharField(
        label='Cellulare (Italia)',
        max_length=20,
        required=True,
        widget=forms.TextInput(attrs={
            'class': 'freg-input',
            'autocomplete': 'tel',
            'placeholder': 'es. 333 1234567',
            'inputmode': 'numeric',
        }),
        help_text='Numero di contatto per l’azienda; il codice di registrazione viene inviato all’e-mail.',
    )

    # Honeypot anti-bot (campo nascosto: se compilato la registrazione è rifiutata)
    website = forms.CharField(
        required=False,
        widget=forms.TextInput(attrs={
            'style': 'display:none !important; visibility:hidden; position:absolute; left:-9999px;',
            'tabindex': '-1',
            'autocomplete': 'off',
        }),
        label="",
    )

    # ── Consensi GDPR (D.Lgs. 101/2018 - GDPR art. 7) ──────────
    consenso_trattamento = forms.BooleanField(
        required=True,
        label=(
            "Ho letto l'Informativa sul trattamento dei dati personali "
            "e presto il mio consenso al trattamento dei dati per "
            "finalità di selezione del personale. "
            "<strong>(obbligatorio)</strong>"
        ),
        widget=forms.CheckboxInput(attrs={'class': 'freg-check'}),
        error_messages={'required': "Devi accettare il trattamento dei dati per procedere."},
    )
    consenso_conservazione = forms.BooleanField(
        required=False,
        label=(
            "Acconsento alla conservazione del mio profilo nel database "
            "per un periodo massimo di 12 mesi dalla registrazione, "
            "al fine di essere valutato per future selezioni. "
            "<em>(facoltativo)</em>"
        ),
        widget=forms.CheckboxInput(attrs={'class': 'freg-check'}),
    )
    consenso_comunicazione = forms.BooleanField(
        required=False,
        label=(
            "Acconsento alla comunicazione dei miei dati a società "
            "collegate o partner per finalità di selezione del personale. "
            "<em>(facoltativo)</em>"
        ),
        widget=forms.CheckboxInput(attrs={'class': 'freg-check'}),
    )

    def clean_website(self):
        """Honeypot: se compilato → bot rilevato."""
        value = self.cleaned_data.get('website', '')
        if value:
            raise forms.ValidationError("Registrazione non valida.")
        return value

    def clean_email(self):
        email = self.cleaned_data.get('email', '').lower().strip()
        if User.objects.filter(email__iexact=email).exists():
            raise forms.ValidationError(
                "Esiste già un account registrato con questo indirizzo e-mail."
            )
        return email

    def clean_codice_fiscale(self):
        import re

        cf = (self.cleaned_data.get('codice_fiscale') or '').strip().upper()
        try:
            CodiceFiscalePasswordValidator().validate(cf)
        except DjangoValidationError as e:
            raise forms.ValidationError(e.messages)
        if re.fullmatch(r'[A-Z]{6}[0-9]{2}[A-EHLMPRST][0-9]{2}[A-Z][0-9]{3}[A-Z]', cf):
            if not _valida_cf(cf):
                raise forms.ValidationError('Codice fiscale non valido (carattere di controllo errato).')
        if ProfiloCandidato.objects.filter(codice_fiscale__iexact=cf).exists():
            raise forms.ValidationError('Questo codice fiscale risulta già registrato.')
        return cf

    def clean(self):
        cleaned = super().clean()
        email = (cleaned.get('email') or '').lower().strip()
        email_conferma = (cleaned.get('email_conferma') or '').lower().strip()
        if email and email_conferma and email != email_conferma:
            self.add_error('email_conferma', "Gli indirizzi e-mail non coincidono.")
        return cleaned


class CandidatoRegistrazioneOtpConfermaForm(forms.Form):
    """Passo 2 — codice OTP ricevuto via e-mail."""

    otp = forms.CharField(
        label='Codice e-mail',
        max_length=6,
        min_length=6,
        strip=True,
        widget=forms.TextInput(attrs={
            'class': 'freg-input',
            'inputmode': 'numeric',
            'autocomplete': 'one-time-code',
            'placeholder': '6 cifre',
        }),
    )

    def clean_otp(self):
        o = (self.cleaned_data.get('otp') or '').strip()
        if not o.isdigit() or len(o) != 6:
            raise forms.ValidationError('Inserisci il codice a 6 cifre.')
        return o


# ── Completamento profilo candidato ─────────────────────────────
class ProfiloCandidatoForm(forms.ModelForm):
    """Seconda fase: il candidato inserisce i dati anagrafici completi."""
    regione_nascita = forms.ChoiceField(required=False, choices=(), label='Regione nascita')
    provincia_nascita = forms.ChoiceField(required=False, choices=(), label='Provincia nascita')
    comune_nascita = forms.ChoiceField(required=False, choices=(), label='Comune nascita')
    comune_nascita_estero = forms.CharField(
        required=False,
        label='Citta estera di nascita',
        widget=forms.TextInput(attrs={'class': 'form-control form-control-sm text-uppercase'}),
    )
    citta_residenza_estero = forms.CharField(
        required=False,
        label='Citta estera di residenza',
        widget=forms.TextInput(attrs={'class': 'form-control form-control-sm text-uppercase'}),
    )

    class Meta:
        model = ProfiloCandidato
        fields = [
            # Anagrafici (flusso allineato a Dipendente: nascita → recapito)
            'codice_fiscale', 'data_nascita',
            'nazionalita',
            'regione_nascita', 'provincia_nascita', 'comune_nascita', 'luogo_nascita',
            'sesso', 'telefono',
            'regione_residenza', 'provincia', 'citta', 'cap', 'indirizzo',
            # Documento
            'tipo_documento', 'numero_documento',
            'data_emissione_documento', 'scadenza_documento',
            'file_documento', 'file_codice_fiscale',
            # Dati bancari
            'iban',
            # Familiari
            'num_familiari_a_carico', 'dettaglio_familiari',
            # Dichiarazione penale
            'dichiarazione_no_condanne',
            # Competenze
            'mansione_aspirata', 'competenze',
            # Disponibilità
            'data_disponibilita', 'tipo_rapporto_preferito',
            'ore_settimanali_preferite', 'livello_aspirato',
            'note_candidatura', 'paga_giornaliera_attesa',
        ]
        widgets = {
            'codice_fiscale': forms.TextInput(attrs={
                'class': 'form-control form-control-sm text-uppercase',
                'maxlength': 16,
                'placeholder': 'es. RSSMRA85M01H501Z',
            }),
            'data_nascita': forms.DateInput(format='%Y-%m-%d', attrs={
                'class': 'form-control form-control-sm',
                'type': 'date',
            }),
            'luogo_nascita': forms.HiddenInput(),
            'sesso': forms.Select(attrs={'class': 'form-select form-select-sm'}),
            'nazionalita': forms.Select(attrs={'class': 'form-select form-select-sm'}),
            'indirizzo': forms.TextInput(attrs={
                'class': 'form-control form-control-sm text-uppercase',
                'placeholder': 'VIA/PIAZZA, N°',
            }),
            'cap': forms.TextInput(attrs={
                'class': 'form-control form-control-sm',
                'maxlength': 10,
                'placeholder': '00000 / estero',
            }),
            'citta': forms.TextInput(attrs={
                'class': 'form-control form-control-sm text-uppercase',
            }),
            'provincia': forms.TextInput(attrs={
                'class': 'form-control form-control-sm text-uppercase',
                'maxlength': 2,
                'placeholder': 'RM',
            }),
            'regione_residenza': forms.TextInput(attrs={
                'class': 'form-control form-control-sm',
                'placeholder': 'es. Sicilia',
            }),
            'telefono': forms.TextInput(attrs={
                'class': 'form-control form-control-sm',
                'placeholder': '+39 ...',
            }),
            'tipo_documento': forms.Select(attrs={'class': 'form-select form-select-sm'}),
            'numero_documento': forms.TextInput(attrs={
                'class': 'form-control form-control-sm text-uppercase',
            }),
            'data_emissione_documento': forms.DateInput(format='%Y-%m-%d', attrs={
                'class': 'form-control form-control-sm',
                'type': 'date',
            }),
            'scadenza_documento': forms.DateInput(format='%Y-%m-%d', attrs={
                'class': 'form-control form-control-sm',
                'type': 'date',
            }),
            'file_documento': forms.ClearableFileInput(attrs={
                'class': 'form-control form-control-sm',
                'accept': '.pdf,.jpg,.jpeg,.png',
            }),
            'file_codice_fiscale': forms.ClearableFileInput(attrs={
                'class': 'form-control form-control-sm',
                'accept': '.pdf,.jpg,.jpeg,.png',
            }),
            'iban': forms.TextInput(attrs={
                'class': 'form-control form-control-sm text-uppercase',
                'placeholder': 'IT60X0542811101000000123456',
                'maxlength': 34,
            }),
            'num_familiari_a_carico': forms.NumberInput(attrs={
                'class': 'form-control form-control-sm',
                'min': 0, 'max': 20,
            }),
            'dettaglio_familiari': forms.Textarea(attrs={
                'class': 'form-control form-control-sm',
                'rows': 2,
                'placeholder': 'Es. coniuge, 2 figli minori...',
            }),
            'dichiarazione_no_condanne': forms.CheckboxInput(attrs={
                'class': 'form-check-input',
            }),
            'mansione_aspirata': forms.TextInput(attrs={
                'class': 'form-control form-control-sm',
                'placeholder': 'Es. Cuoco, Cameriere, Barista...',
            }),
            'competenze': forms.Textarea(attrs={
                'class': 'form-control form-control-sm',
                'rows': 3,
                'placeholder': 'Lingue parlate, patenti, corsi professionali, certificazioni...',
            }),
            'data_disponibilita': forms.DateInput(format='%Y-%m-%d', attrs={
                'class': 'form-control form-control-sm',
                'type': 'date',
            }),
            'tipo_rapporto_preferito': forms.Select(attrs={
                'class': 'form-select form-select-sm',
            }),
            'ore_settimanali_preferite': forms.NumberInput(attrs={
                'class': 'form-control form-control-sm',
                'min': 10, 'max': 40, 'step': 1,
            }),
            'livello_aspirato': forms.TextInput(attrs={
                'class': 'form-control form-control-sm',
                'placeholder': 'es. 5, 4S, 3...',
            }),
            'note_candidatura': forms.Textarea(attrs={
                'class': 'form-control form-control-sm',
                'rows': 4,
                'placeholder': 'Presentati brevemente: esperienze, motivazioni, disponibilità...',
            }),
            'paga_giornaliera_attesa': forms.NumberInput(attrs={
                'class': 'form-control form-control-sm',
                'min': 0, 'max': 500, 'step': '0.01',
                'placeholder': 'es. 70.00',
            }),
        }
        labels = {
            'codice_fiscale': 'Codice Fiscale',
            'data_nascita': 'Data di nascita',
            'luogo_nascita': 'Luogo di nascita',
            'sesso': 'Sesso',
            'nazionalita': 'Nazionalità',
            'indirizzo': 'Indirizzo di residenza',
            'cap': 'CAP',
            'citta': 'Città',
            'provincia': 'Provincia',
            'telefono': 'Telefono / cellulare',
            'tipo_documento': 'Tipo documento identità',
            'numero_documento': 'Numero documento',
            'data_emissione_documento': 'Data emissione',
            'scadenza_documento': 'Data scadenza',
            'file_documento': 'Allega copia documento identità',
            'file_codice_fiscale': 'Allega copia tessera sanitaria / CF',
            'iban': 'IBAN (codice bancario)',
            'num_familiari_a_carico': 'N° familiari a carico',
            'dettaglio_familiari': 'Dettaglio familiari',
            'dichiarazione_no_condanne': (
                'Dichiaro di non aver riportato condanne penali e di non essere '
                'interessato da procedimenti giudiziari in corso.'
            ),
            'mansione_aspirata': 'Mansione aspirata',
            'competenze': 'Competenze professionali',
            'data_disponibilita': 'Disponibile dal',
            'tipo_rapporto_preferito': 'Tipo rapporto preferito',
            'ore_settimanali_preferite': 'Ore settimanali preferite',
            'livello_aspirato': 'Livello CCNL aspirato',
            'note_candidatura': 'Lettera di presentazione / note',
            'paga_giornaliera_attesa': 'Paga giornaliera netta attesa (€)',
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        def _set_choices(field_name, choices):
            self.fields[field_name].choices = choices
            if hasattr(self.fields[field_name].widget, 'choices'):
                self.fields[field_name].widget.choices = choices

        regioni = [('ESTERO', 'ESTERO')] + [(r, r.title()) for r in regioni_it()]
        paesi_list = paesi_istat()

        def _label_paese(p):
            nome = (p.get('nome') or '').strip()
            at = (p.get('codice_at') or '').strip()
            if not nome:
                return nome
            return f"{nome.title()} ({at})" if at else nome.title()

        paesi = []
        seen_it = False
        for p in paesi_list:
            nome = (p.get('nome') or '').strip()
            if nome.upper() == 'ITALIA':
                seen_it = True
            paesi.append((nome, _label_paese(p)))
        if not seen_it:
            paesi.insert(0, ('ITALIA', 'Italia'))

        self.fields['regione_nascita'].widget.attrs.update({'class': 'form-select form-select-sm'})
        self.fields['provincia_nascita'].widget.attrs.update({'class': 'form-select form-select-sm'})
        self.fields['comune_nascita'].widget.attrs.update({'class': 'form-select form-select-sm'})
        self.fields['regione_residenza'].widget = forms.Select(attrs={'class': 'form-select form-select-sm'})
        self.fields['citta'].widget = forms.Select(attrs={'class': 'form-select form-select-sm'})
        self.fields['provincia'].widget = forms.Select(attrs={'class': 'form-select form-select-sm'})

        _set_choices('regione_nascita', [('', '— Seleziona —')] + regioni)
        _set_choices('regione_residenza', [('', '— Seleziona —')] + regioni)
        _set_choices('nazionalita', paesi)
        self.fields['nazionalita'].help_text = (
            'Elenco ufficiale ISTAT unità territoriali estere (allineato codici AT / Agenzia Entrate).'
        )

        _set_choices('provincia_nascita', [('', '— Seleziona regione —')])
        _set_choices('comune_nascita', [('', '— Seleziona provincia —')])
        _set_choices('provincia', [('', '— Seleziona regione —')])
        _set_choices('citta', [('', '— Seleziona provincia —')])

        if not self.is_bound:
            inst = getattr(self, 'instance', None)
            no_birth_geo = (not inst.pk) or not (inst.luogo_nascita or '').strip()
            if no_birth_geo:
                self.initial.setdefault('regione_nascita', 'SICILIA')
                self.initial.setdefault('provincia_nascita', 'PA')
                self.initial.setdefault('comune_nascita', 'PALERMO')

        self._populate_geo_initial_choices()

    def _populate_geo_initial_choices(self):
        def _set_choices(field_name, choices):
            self.fields[field_name].choices = choices
            if hasattr(self.fields[field_name].widget, 'choices'):
                self.fields[field_name].widget.choices = choices

        data = self.data if self.is_bound else None
        instance = self.instance if getattr(self, 'instance', None) and self.instance.pk else None

        reg_n = (data.get('regione_nascita') or '').strip() if data else ''
        prov_n = (data.get('provincia_nascita') or '').strip() if data else ''
        com_n = (data.get('comune_nascita') or '').strip() if data else ''
        if not reg_n and instance and instance.luogo_nascita:
            luogo_raw = (instance.luogo_nascita or '').strip().upper()
            luogo = luogo_raw.split('(')[0].strip()
            if 'ESTERO' in luogo_raw:
                reg_n = 'ESTERO'
            else:
                for r in regioni_it():
                    provs = province_per_regione(r)
                    for p in provs:
                        comuni = comuni_per_regione_provincia(r, p.get('sigla') or p.get('nome'))
                        hit = next(
                            (x for x in comuni if (x.get('nome') or '').strip().upper() == luogo),
                            None,
                        )
                        if hit:
                            reg_n = r
                            prov_n = p.get('sigla') or p.get('nome')
                            com_n = (hit.get('nome') or '').strip().upper()
                            break
                    if reg_n:
                        break
        if not reg_n and not data:
            reg_n = (self.initial.get('regione_nascita') or '').strip()
            prov_n = prov_n or (self.initial.get('provincia_nascita') or '').strip()
            com_n = com_n or (self.initial.get('comune_nascita') or '').strip()
        if reg_n and reg_n != 'ESTERO':
            provs = province_per_regione(reg_n)
            _set_choices('provincia_nascita', [('', '— Seleziona —')] + [
                (p.get('sigla') or p.get('nome'), f"{p.get('nome')} ({p.get('sigla')})" if p.get('sigla') else p.get('nome'))
                for p in provs
            ])
            if prov_n:
                comuni = comuni_per_regione_provincia(reg_n, prov_n)
                _set_choices('comune_nascita', [('', '— Seleziona —')] + [
                    (
                        c['nome'],
                        f"{(c.get('nome') or '').strip().title()} ({c['codice_catastale']})"
                        if (c.get('codice_catastale') or '').strip()
                        else (c.get('nome') or '').strip().title(),
                    )
                    for c in comuni
                ])
        elif reg_n == 'ESTERO':
            _set_choices('provincia_nascita', [('', '— Non prevista per estero —')])
            _set_choices('comune_nascita', [('', '— Inserisci citta estera —')])
        if reg_n:
            self.initial['regione_nascita'] = reg_n
        if prov_n:
            self.initial['provincia_nascita'] = prov_n
        if com_n:
            self.initial['comune_nascita'] = com_n

        reg_r = ((data.get('regione_residenza') or '') if data else getattr(instance, 'regione_residenza', '')).strip().upper() if (data or instance) else ''
        prov_r = ((data.get('provincia') or '') if data else getattr(instance, 'provincia', '')).strip().upper() if (data or instance) else ''
        cit_r = ((data.get('citta') or '') if data else getattr(instance, 'citta', '')).strip().upper() if (data or instance) else ''
        if reg_r and reg_r != 'ESTERO':
            provs = province_per_regione(reg_r)
            _set_choices('provincia', [('', '— Seleziona —')] + [
                (p.get('sigla') or p.get('nome'), f"{p.get('nome')} ({p.get('sigla')})" if p.get('sigla') else p.get('nome'))
                for p in provs
            ])
            if prov_r:
                comuni = comuni_per_regione_provincia(reg_r, prov_r)
                _set_choices('citta', [('', '— Seleziona —')] + [
                    (
                        c['nome'],
                        f"{(c.get('nome') or '').strip().title()} ({c['codice_catastale']})"
                        if (c.get('codice_catastale') or '').strip()
                        else (c.get('nome') or '').strip().title(),
                    )
                    for c in comuni
                ])
        elif reg_r == 'ESTERO':
            _set_choices('provincia', [('', '— Non prevista per estero —')])
            _set_choices('citta', [('', '— Inserisci citta estera —')])
        if cit_r:
            self.initial['citta'] = cit_r

    # ── Uppercase: campi testo libero ────────────────────────────────────────

    def clean_luogo_nascita(self):
        return (self.cleaned_data.get('luogo_nascita') or '').upper().strip()

    def clean_nazionalita(self):
        return (self.cleaned_data.get('nazionalita') or '').upper().strip()

    def clean_indirizzo(self):
        return self.cleaned_data.get('indirizzo', '').upper().strip()

    def clean_citta(self):
        return (self.cleaned_data.get('citta') or '').upper().strip()

    def clean_provincia(self):
        prov = (self.cleaned_data.get('provincia') or '').upper().strip()
        regione = (
            (self.cleaned_data.get('regione_residenza') or '')
            or (self.data.get('regione_residenza') if getattr(self, 'data', None) else '')
        ).upper().strip()
        if regione == 'ESTERO':
            return ''
        if prov and len(prov) != 2:
            raise forms.ValidationError("La provincia deve essere la sigla di 2 lettere (es. PA, MI, RM).")
        return prov

    def clean_numero_documento(self):
        return self.cleaned_data.get('numero_documento', '').upper().strip()

    # ── Uppercase + validazione CF ───────────────────────────────────────────

    def clean_codice_fiscale(self):
        cf = self.cleaned_data.get('codice_fiscale', '').upper().strip()
        if not cf:
            return cf
        if len(cf) != 16:
            raise forms.ValidationError("Il Codice Fiscale deve essere di 16 caratteri.")
        if not _valida_cf(cf):
            raise forms.ValidationError(
                "Codice Fiscale non valido (formato o carattere di controllo errato)."
            )
        return cf

    # ── Validazione CAP ──────────────────────────────────────────────────────

    def clean_cap(self):
        cap = (self.cleaned_data.get('cap') or '').strip()
        regione = (
            (self.cleaned_data.get('regione_residenza') or '')
            or (self.data.get('regione_residenza') if getattr(self, 'data', None) else '')
        ).upper().strip()
        if regione == 'ESTERO':
            return cap.upper()
        if cap and (not cap.isdigit() or len(cap) != 5):
            raise forms.ValidationError("Il CAP deve essere composto da 5 cifre numeriche.")
        return cap

    # ── Validazione regione ──────────────────────────────────────────────────

    def clean_regione_residenza(self):
        regione = (self.cleaned_data.get('regione_residenza') or '').strip()
        if not regione:
            return regione
        regione_up = regione.upper()
        if regione_up == 'ESTERO':
            return 'ESTERO'
        regioni_upper = {r.upper() for r in regioni_it()}
        if regione_up not in regioni_upper:
            raise forms.ValidationError(
                "Regione non riconosciuta. Inserire una regione italiana (es. Sicilia, Lombardia)."
            )
        return regione_up

    # ── Validazione telefono ─────────────────────────────────────────────────

    def clean_telefono(self):
        tel = self.cleaned_data.get('telefono', '').strip()
        if not tel:
            return tel
        # Accetta: cifre, spazi, +, -, ( )  — min 6 cifre reali
        import re
        cifre = re.sub(r'[^\d]', '', tel)
        if len(cifre) < 6:
            raise forms.ValidationError("Numero di telefono non valido (troppo corto).")
        if len(cifre) > 15:
            raise forms.ValidationError("Numero di telefono non valido (troppo lungo).")
        return tel

    # ── Validazione IBAN ─────────────────────────────────────────────────────

    def clean_iban(self):
        iban = self.cleaned_data.get('iban', '').replace(' ', '').upper()
        if not iban:
            return iban
        if len(iban) < 15 or len(iban) > 34:
            raise forms.ValidationError("IBAN non valido (lunghezza errata).")
        if not iban[:2].isalpha() or not iban[2:4].isdigit():
            raise forms.ValidationError(
                "IBAN non valido (deve iniziare con il codice paese a 2 lettere e 2 cifre di controllo, es. IT60...)."
            )
        # Verifica checksum IBAN (MOD 97)
        rearranged = iban[4:] + iban[:4]
        numeric = ''.join(str(ord(c) - 55) if c.isalpha() else c for c in rearranged)
        if int(numeric) % 97 != 1:
            raise forms.ValidationError("IBAN non valido (checksum errato).")
        return iban

    # ── Validazione data nascita ─────────────────────────────────────────────

    def clean_data_nascita(self):
        data = self.cleaned_data.get('data_nascita')
        if not data:
            return data
        oggi = timezone.localdate()
        if data >= oggi:
            raise forms.ValidationError("La data di nascita non può essere futura.")
        eta = (oggi - data).days // 365
        if eta < 16:
            raise forms.ValidationError("Il candidato deve avere almeno 16 anni.")
        if eta > 100:
            raise forms.ValidationError("Data di nascita non plausibile.")
        return data

    # ── Validazione scadenza documento ───────────────────────────────────────

    def clean_scadenza_documento(self):
        scadenza = self.cleaned_data.get('scadenza_documento')
        if not scadenza:
            return scadenza
        oggi = timezone.localdate()
        if scadenza <= oggi:
            raise forms.ValidationError(
                "Il documento è scaduto. Inserire un documento in corso di validità."
            )
        return scadenza

    # ── Validazione cross-campo ───────────────────────────────────────────────

    def clean(self):
        cleaned = super().clean()
        from anagrafiche.codice_fiscale_it import merge_profilo_candidato_da_codice_fiscale

        merge_profilo_candidato_da_codice_fiscale(cleaned)
        dn = cleaned.get('data_nascita')
        if dn:
            oggi = timezone.localdate()
            if dn >= oggi:
                self.add_error('data_nascita', 'La data di nascita non può essere futura.')
            else:
                eta = (oggi - dn).days // 365
                if eta < 16:
                    self.add_error('data_nascita', 'Il candidato deve avere almeno 16 anni.')
                elif eta > 100:
                    self.add_error('data_nascita', 'Data di nascita non plausibile.')

        reg_n = (cleaned.get('regione_nascita') or '').strip().upper()
        prov_n = (cleaned.get('provincia_nascita') or '').strip().upper()
        com_n = (cleaned.get('comune_nascita') or '').strip().upper()
        com_estero = (cleaned.get('comune_nascita_estero') or '').strip().upper()
        if reg_n == 'ESTERO':
            if not com_estero:
                self.add_error('comune_nascita_estero', 'Inserisci la citta estera di nascita.')
            cleaned['luogo_nascita'] = com_estero
        elif reg_n:
            if not prov_n:
                self.add_error('provincia_nascita', 'Seleziona la provincia di nascita.')
            if not com_n:
                self.add_error('comune_nascita', 'Seleziona il comune di nascita.')
            cleaned['luogo_nascita'] = com_n

        reg_r = (cleaned.get('regione_residenza') or '').strip().upper()
        citta_estero = (cleaned.get('citta_residenza_estero') or '').strip().upper()
        if reg_r == 'ESTERO':
            if not citta_estero:
                self.add_error('citta_residenza_estero', 'Inserisci la citta estera di residenza.')
            cleaned['provincia'] = ''
            cleaned['citta'] = citta_estero
        elif reg_r:
            if not (cleaned.get('provincia') or '').strip():
                self.add_error('provincia', 'Seleziona la provincia di residenza.')
            if not (cleaned.get('citta') or '').strip():
                self.add_error('citta', 'Seleziona il comune di residenza.')
            prov_rr = (cleaned.get('provincia') or '').strip().upper()
            com_rr = (cleaned.get('citta') or '').strip().upper()
            if prov_rr and com_rr and not (cleaned.get('cap') or '').strip():
                for item in comuni_per_regione_provincia(reg_r, prov_rr):
                    if (item.get('nome') or '').strip().upper() == com_rr and item.get('cap'):
                        cleaned['cap'] = item['cap']
                        break

        emissione = cleaned.get('data_emissione_documento')
        scadenza  = cleaned.get('scadenza_documento')
        if emissione and scadenza and emissione >= scadenza:
            self.add_error(
                'scadenza_documento',
                "La data di scadenza deve essere successiva alla data di emissione."
            )

        data_nascita    = cleaned.get('data_nascita')
        data_disponib   = cleaned.get('data_disponibilita')
        if data_nascita and data_disponib and data_disponib < data_nascita:
            self.add_error(
                'data_disponibilita',
                "La data di disponibilità non può essere precedente alla data di nascita."
            )

        return cleaned
