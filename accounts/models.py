import secrets
from decimal import Decimal
from datetime import timedelta
from django.contrib.auth.models import AbstractUser, Group, Permission
from django.db import models
from django.utils import timezone
from anagrafiche.models import Azienda


class Ruolo(models.Model):
    codice = models.CharField(max_length=50, unique=True)
    nome = models.CharField(max_length=100)

    class Meta:
        verbose_name = 'Ruolo'
        verbose_name_plural = 'Ruoli'

    def __str__(self):
        return self.nome


class User(AbstractUser):
    class Meta(AbstractUser.Meta):
        verbose_name = 'Utente'
        verbose_name_plural = 'Utenti'
        # Permessi “funzionali” per il portale dipendente (scheda Django → gruppi / permessi utente).
        permissions = [
            (
                'portale_documenti_personali',
                'Portale dipendente: documenti personali',
            ),
            (
                'portale_buste_paga_cud',
                'Portale dipendente: buste paga e CUD',
            ),
            (
                'portale_presenze_calendario',
                'Portale dipendente: presenze e calendario orari',
            ),
            (
                'portale_richieste',
                'Portale dipendente: richieste (ferie, permessi, …)',
            ),
        ]

    RUOLO_CHOICES = [
        ('admin', 'Amministratore'),
        ('hr', 'Risorse Umane'),
        ('dipendente', 'Dipendente'),
        ('consulente', 'Consulente'),
        ('candidato', 'Candidato'),
    ]

    azienda = models.ForeignKey(
        Azienda,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='accounts_users',
        verbose_name='Azienda',
    )
    ruoli = models.ManyToManyField(
        'accounts.Ruolo',
        blank=True,
        related_name='utenti',
        verbose_name='Ruoli',
        help_text='Ruoli associati all’utente (admin, hr, ecc.)',
    )
    groups = models.ManyToManyField(
        Group,
        related_name='accounts_users',
        blank=True,
        help_text="Gruppi a cui appartiene l'utente.",
        verbose_name='Gruppi',
    )
    user_permissions = models.ManyToManyField(
        Permission,
        related_name='accounts_users_permissions',
        blank=True,
        help_text='Permessi specifici per questo utente.',
        verbose_name='Permessi utente',
    )

    convalidato = models.BooleanField(
        default=False,
        help_text='Utente abilitato dal supervisore',
        verbose_name='Convalidato',
    )
    privacy_accettata = models.BooleanField(
        default=False,
        help_text='Consenso privacy GDPR',
        verbose_name='Privacy accettata',
    )
    privacy_data = models.DateTimeField(
        null=True, blank=True,
        help_text='Data accettazione privacy',
        verbose_name='Data consenso privacy',
    )

    # ── Verifica e-mail ──────────────────────────────────────────
    email_verificata = models.BooleanField(
        default=False,
        verbose_name='E-mail verificata',
    )
    email_token = models.CharField(
        max_length=64, blank=True, default='',
        verbose_name='Token verifica e-mail',
    )
    email_token_scadenza = models.DateTimeField(
        null=True, blank=True,
        verbose_name='Scadenza token verifica',
    )

    # ── Verifica in due passaggi (TOTP) ──────────────────────────
    totp_secret = models.CharField(
        max_length=64, blank=True, default='',
        verbose_name='Segreto TOTP',
    )
    totp_enabled = models.BooleanField(
        default=False,
        verbose_name='2FA app (TOTP)',
        help_text='Se attivo, dopo la password serve il codice dall’app authenticator (stesso flusso API e login web).',
    )
    email_stepup_login = models.BooleanField(
        default=False,
        verbose_name='Verifica e-mail al login',
        help_text='Se attivo, dopo username/password viene inviato un codice monouso via e-mail (SMTP di sistema). Richiede e-mail valorizzata sul profilo.',
    )

    def genera_token_verifica(self):
        """Genera un nuovo token di verifica e-mail (valido 48 ore)."""
        self.email_token = secrets.token_urlsafe(40)
        self.email_token_scadenza = timezone.now() + timedelta(hours=48)
        self.save(update_fields=['email_token', 'email_token_scadenza'])
        return self.email_token

    def token_valido(self, token):
        return (
            self.email_token
            and self.email_token == token
            and self.email_token_scadenza
            and timezone.now() <= self.email_token_scadenza
        )

    def __str__(self):
        ruoli = ', '.join([str(r) for r in self.ruoli.all()])
        return f"{self.username} ({ruoli})"

    def has_ruolo(self, ruolo_codice):
        """Restituisce True se l’utente ha il ruolo specificato (es. 'admin', 'hr')."""
        return self.ruoli.filter(codice=ruolo_codice).exists()

    def is_candidato_portale(self):
        """True se è un candidato gestibile dal portale: ruolo candidato e/o profilo candidato."""
        if self.has_ruolo('candidato'):
            return True
        return type(self).objects.filter(pk=self.pk, profilo_candidato__isnull=False).exists()


# ── Profilo Candidato ────────────────────────────────────────────
class ProfiloCandidato(models.Model):
    """Dati anagrafici e di candidatura dell'utente con ruolo 'candidato'."""

    SESSO_CHOICES = [
        ('M', 'Maschile'),
        ('F', 'Femminile'),
        ('A', 'Preferisco non specificare'),
    ]
    TIPO_DOCUMENTO_CHOICES = [
        ('ci', "Carta d'identità"),
        ('passaporto', 'Passaporto'),
        ('patente', 'Patente di guida'),
        ('ps', 'Permesso di soggiorno'),
    ]
    TIPO_RAPPORTO_CHOICES = [
        ('indeterminato', 'Tempo indeterminato'),
        ('determinato', 'Tempo determinato'),
        ('entrambi', 'Nessuna preferenza'),
    ]

    class Meta:
        verbose_name = 'Profilo candidato'
        verbose_name_plural = 'Profili candidati'

    user = models.OneToOneField(
        User,
        on_delete=models.CASCADE,
        related_name='profilo_candidato',
        verbose_name='Utente',
    )
    azienda_interesse = models.ForeignKey(
        Azienda,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='candidati',
        verbose_name='Azienda di interesse',
    )
    # Collegamento al Dipendente creato al completamento profilo
    dipendente = models.OneToOneField(
        'anagrafiche.Dipendente',
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='profilo_candidato',
        verbose_name='Dipendente collegato',
    )

    # ── Dati anagrafici ──────────────────────────────────────────
    codice_fiscale = models.CharField(
        max_length=16, blank=True, verbose_name='Codice Fiscale',
    )
    data_nascita = models.DateField(
        null=True, blank=True, verbose_name='Data di nascita',
    )
    luogo_nascita = models.CharField(
        max_length=100, blank=True, verbose_name='Luogo di nascita',
    )
    sesso = models.CharField(
        max_length=1, choices=SESSO_CHOICES, blank=True, verbose_name='Sesso',
    )
    nazionalita = models.CharField(
        max_length=60, blank=True, default='Italiana', verbose_name='Nazionalità',
    )
    indirizzo = models.CharField(
        max_length=255, blank=True, verbose_name='Indirizzo di residenza',
    )
    cap = models.CharField(
        max_length=10, blank=True, verbose_name='CAP',
    )
    citta = models.CharField(
        max_length=100, blank=True, verbose_name='Città',
    )
    provincia = models.CharField(
        max_length=2, blank=True, verbose_name='Provincia (sigla)',
    )
    regione_residenza = models.CharField(
        max_length=50, blank=True, verbose_name='Regione di residenza',
        help_text='Es. Sicilia, Lombardia — usato per calcolo addizionale regionale IRPEF.',
    )
    telefono = models.CharField(
        max_length=30, blank=True, verbose_name='Telefono',
    )

    # ── Documento di identità ────────────────────────────────────
    tipo_documento = models.CharField(
        max_length=20, choices=TIPO_DOCUMENTO_CHOICES,
        blank=True, verbose_name='Tipo documento',
    )
    numero_documento = models.CharField(
        max_length=50, blank=True, verbose_name='Numero documento',
    )
    data_emissione_documento = models.DateField(
        null=True, blank=True, verbose_name='Data emissione documento',
    )
    scadenza_documento = models.DateField(
        null=True, blank=True, verbose_name='Scadenza documento',
    )
    file_documento = models.FileField(
        upload_to='candidati/documenti/',
        null=True, blank=True,
        verbose_name='Copia documento identità (PDF/JPG)',
    )
    file_codice_fiscale = models.FileField(
        upload_to='candidati/cf/',
        null=True, blank=True,
        verbose_name='Copia tessera sanitaria / Codice fiscale (PDF/JPG)',
    )

    # ── Dati bancari ─────────────────────────────────────────────
    iban = models.CharField(
        max_length=34, blank=True, verbose_name='IBAN',
        help_text='Es. IT60X0542811101000000123456',
    )

    # ── Carichi di famiglia ───────────────────────────────────────
    num_familiari_a_carico = models.PositiveSmallIntegerField(
        default=0, verbose_name='Numero familiari a carico',
    )
    dettaglio_familiari = models.TextField(
        blank=True,
        verbose_name='Dettaglio familiari a carico',
        help_text='Es. coniuge, 2 figli minori, ecc.',
    )

    # ── Dichiarazione penale ──────────────────────────────────────
    dichiarazione_no_condanne = models.BooleanField(
        default=False,
        verbose_name='Dichiarazione assenza condanne penali',
        help_text=(
            'Dichiaro di non aver riportato condanne penali e di non essere '
            'interessato da procedimenti giudiziari in corso.'
        ),
    )

    # ── Competenze e mansione ─────────────────────────────────────
    mansione_aspirata = models.CharField(
        max_length=100, blank=True, verbose_name='Mansione aspirata',
        help_text='Es. Cuoco, Cameriere, Barista, Receptionist...',
    )
    competenze = models.TextField(
        blank=True, verbose_name='Competenze',
        help_text='Elenca le competenze professionali certificate e non (lingue, patenti, corsi, ecc.)',
    )

    # ── Disponibilità e preferenze lavorative ────────────────────
    data_disponibilita = models.DateField(
        null=True, blank=True, verbose_name='Disponibile dal',
    )
    tipo_rapporto_preferito = models.CharField(
        max_length=20, choices=TIPO_RAPPORTO_CHOICES,
        blank=True, default='entrambi', verbose_name='Tipo rapporto preferito',
    )
    ore_settimanali_preferite = models.DecimalField(
        max_digits=4, decimal_places=1,
        null=True, blank=True, verbose_name='Ore settimanali preferite',
    )
    livello_aspirato = models.CharField(
        max_length=10, blank=True, verbose_name='Livello CCNL aspirato',
    )
    note_candidatura = models.TextField(
        blank=True, verbose_name='Note / lettera di presentazione',
    )
    paga_giornaliera_attesa = models.DecimalField(
        max_digits=7, decimal_places=2,
        null=True, blank=True,
        verbose_name='Paga giornaliera netta attesa (€)',
        help_text=(
            'Indicare la paga giornaliera netta che si desidera percepire. '
            'Verrà usata per confrontare l\'offerta contrattuale con le proprie aspettative.'
        ),
    )

    # ── Consensi aggiuntivi (GDPR) ───────────────────────────────
    consenso_conservazione = models.BooleanField(
        default=False,
        verbose_name='Consenso conservazione profilo (12 mesi)',
        help_text=(
            'Autorizza la conservazione del profilo per future selezioni '
            'per un periodo massimo di 12 mesi dalla data di iscrizione.'
        ),
    )
    consenso_comunicazione = models.BooleanField(
        default=False,
        verbose_name='Consenso comunicazione a terzi',
        help_text=(
            'Autorizza la comunicazione dei dati a società del gruppo o '
            'partner per finalità di selezione del personale.'
        ),
    )
    data_consensi = models.DateTimeField(
        null=True, blank=True, verbose_name='Data registrazione consensi',
    )

    # ── Stato del profilo ────────────────────────────────────────
    profilo_completato = models.BooleanField(
        default=False, verbose_name='Profilo completato',
    )
    data_completamento = models.DateTimeField(
        null=True, blank=True, verbose_name='Data completamento profilo',
    )
    data_creazione = models.DateTimeField(
        auto_now_add=True, verbose_name='Data registrazione',
    )

    def __str__(self):
        return (
            f"{self.user.first_name} {self.user.last_name} "
            f"({self.user.email})"
        )


class RichiestaIntegrazioneCandidato(models.Model):
    """Richiesta HR di integrazione dati/documenti prima della convalida."""

    STATO_CHOICES = [
        ('inviata', 'Inviata al candidato'),
        ('completata_candidato', 'Completata dal candidato'),
        ('approvata_hr', 'Approvata da HR'),
    ]

    class Meta:
        verbose_name = 'Richiesta integrazione candidato'
        verbose_name_plural = 'Richieste integrazione candidati'
        ordering = ['-data_invio']

    candidato = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name='richieste_integrazione',
        verbose_name='Candidato',
        limit_choices_to={'ruoli__codice': 'candidato'},
    )
    richiesta_da = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='richieste_integrazione_inviate',
        verbose_name='Richiesta da',
    )
    titolo = models.CharField(
        max_length=150,
        default='Richiesta integrazione profilo',
        verbose_name='Titolo',
    )
    messaggio = models.TextField(
        blank=True,
        verbose_name='Istruzioni HR',
        help_text='Richiesta dettagliata visibile al candidato nel profilo.',
    )
    ruolo_richiesto = models.CharField(
        max_length=100,
        blank=True,
        verbose_name='Ruolo/mansione richiesta',
        help_text='Esempio: Cuoco, Cameriere, Fattorino.',
    )
    richiedi_documento_identita = models.BooleanField(
        default=False,
        verbose_name='Richiedi documento identità',
    )
    richiedi_codice_fiscale = models.BooleanField(
        default=False,
        verbose_name='Richiedi tessera sanitaria / CF',
    )
    richiedi_curriculum = models.BooleanField(
        default=False,
        verbose_name='Richiedi curriculum',
    )
    richiedi_mansione = models.BooleanField(
        default=False,
        verbose_name='Richiedi mansione aspirata',
    )
    richiedi_disponibilita = models.BooleanField(
        default=False,
        verbose_name='Richiedi disponibilità lavorativa',
    )
    stato = models.CharField(
        max_length=30,
        choices=STATO_CHOICES,
        default='inviata',
        verbose_name='Stato',
    )
    note_candidato = models.TextField(
        blank=True,
        verbose_name='Note del candidato',
    )
    note_hr = models.TextField(
        blank=True,
        verbose_name='Note HR finali',
    )
    conferma_candidato = models.BooleanField(
        default=False,
        verbose_name='Conferma candidato',
    )
    data_invio = models.DateTimeField(
        auto_now_add=True,
        verbose_name='Data invio',
    )
    data_completamento_candidato = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name='Data completamento candidato',
    )
    data_approvazione_hr = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name='Data approvazione HR',
    )

    def __str__(self):
        stato_label = dict(self.STATO_CHOICES).get(self.stato, self.stato)
        return f"{self.candidato} — {stato_label}"


# ── Configurazione di sistema (singleton) ────────────────────────
class ConfigurazioneSistema(models.Model):
    """
    Parametri globali del sito: SMTP, aspetto UI, info sito.
    Singleton: esiste sempre e solo una riga (pk=1).
    """

    FONT_CHOICES = [
        ('system', 'Sistema (default)'),
        ('inter', 'Inter'),
        ('roboto', 'Roboto'),
        ('open-sans', 'Open Sans'),
        ('lato', 'Lato'),
        ('source-sans', 'Source Sans 3'),
    ]
    FONT_SIZE_CHOICES = [
        ('12', '12px — Compatto'),
        ('13', '13px — Piccolo'),
        ('14', '14px — Standard'),
        ('15', '15px — Leggibile'),
        ('16', '16px — Grande'),
    ]

    class Meta:
        verbose_name = 'Configurazione di sistema'
        verbose_name_plural = 'Configurazione di sistema'

    # ── Informazioni sito ────────────────────────────────────────
    nome_sito = models.CharField(
        max_length=100, default='GESPER',
        verbose_name='Nome del sito',
    )
    nome_azienda = models.CharField(
        max_length=200, blank=True, default='',
        verbose_name='Nome azienda (intestazione documenti)',
    )
    indirizzo_sede = models.CharField(
        max_length=255, blank=True, default='',
        verbose_name='Indirizzo sede legale',
    )
    partita_iva = models.CharField(
        max_length=20, blank=True, default='',
        verbose_name='Partita IVA',
    )
    firmatario_amministratore_nome = models.CharField(
        max_length=150,
        blank=True,
        default='',
        verbose_name='Nome firmatario amministratore',
        help_text='Nome e cognome del titolare/legale rappresentante da usare nei documenti firmati.',
    )
    firmatario_amministratore_ruolo = models.CharField(
        max_length=150,
        blank=True,
        default='',
        verbose_name='Ruolo firmatario amministratore',
        help_text='Es. Amministratore unico, Legale rappresentante.',
    )

    # ── E-mail / SMTP ────────────────────────────────────────────
    smtp_host = models.CharField(
        max_length=200, default='smtp.gmail.com',
        verbose_name='Host SMTP',
    )
    smtp_port = models.PositiveIntegerField(
        default=587,
        verbose_name='Porta SMTP',
    )
    smtp_use_tls = models.BooleanField(
        default=True,
        verbose_name='Usa TLS',
    )
    smtp_user = models.CharField(
        max_length=200, blank=True, default='',
        verbose_name='Utente SMTP (indirizzo e-mail mittente)',
        help_text='Es. noreply@tuodominio.com oppure account Gmail',
    )
    smtp_password = models.CharField(
        max_length=200, blank=True, default='',
        verbose_name='Password SMTP',
        help_text='Per Gmail: usa una "Password per le app" (16 caratteri)',
    )
    email_mittente_nome = models.CharField(
        max_length=100, blank=True, default='',
        verbose_name='Nome mittente e-mail',
        help_text='Es. "GESPER - Gestione Personale". Lascia vuoto per usare solo l\'indirizzo.',
    )
    email_notifiche_hr = models.CharField(
        max_length=200, blank=True, default='',
        verbose_name='E-mail notifiche HR',
        help_text='Indirizzo che riceve le notifiche interne (nuovi candidati, ecc.)',
    )
    url_pubblica_base = models.CharField(
        max_length=300,
        blank=True,
        default='',
        verbose_name='URL pubblica base',
        help_text=(
            'Solo scheme + host, senza path e senza slash finale '
            '(es. https://www.plazapretoria.it oppure https://gesper.plazapretoria.it). '
            'Il prefisso applicazione (es. /gesper/) lo aggiunge sempre Django con reverse(). '
            'Usata per link in e-mail quando la richiesta arriva da host di sviluppo '
            '(localhost, [::1], *.local, …).'
        ),
    )

    # ── SMS (OTP / notifiche) ────────────────────────────────────
    sms_abilitato = models.BooleanField(
        default=False,
        verbose_name='Abilita invio SMS',
    )
    sms_provider = models.CharField(
        max_length=20,
        blank=True,
        default='',
        choices=[
            ('', 'Disattivato'),
            ('twilio', 'Twilio'),
            ('http', 'HTTP POST (JSON)'),
        ],
        verbose_name='Provider SMS',
    )
    sms_prefisso_default = models.CharField(
        max_length=8,
        blank=True,
        default='+39',
        verbose_name='Prefisso internazionale default',
        help_text='Usato se il numero non inizia con + (es. +39).',
    )
    sms_twilio_account_sid = models.CharField(
        max_length=120,
        blank=True,
        default='',
        verbose_name='Twilio Account SID',
    )
    sms_twilio_auth_token = models.CharField(
        max_length=120,
        blank=True,
        default='',
        verbose_name='Twilio Auth Token',
    )
    sms_twilio_from = models.CharField(
        max_length=40,
        blank=True,
        default='',
        verbose_name='Twilio mittente (From)',
        help_text='Numero o ID alfanumerico Twilio approvato.',
    )
    sms_http_url = models.CharField(
        max_length=400,
        blank=True,
        default='',
        verbose_name='URL endpoint SMS (HTTP)',
    )
    sms_http_authorization = models.CharField(
        max_length=400,
        blank=True,
        default='',
        verbose_name='Header Authorization (opzionale)',
        help_text='Es. Bearer … oppure Basic …',
    )
    sms_http_json_template = models.TextField(
        blank=True,
        default='',
        verbose_name='Template corpo JSON (HTTP)',
        help_text='JSON con placeholder {telefono} e {testo}. Vuoto = {"to":"{telefono}","text":"{testo}"}',
    )

    # ── Log test e-mail ──────────────────────────────────────────
    ultimo_test_email_data = models.DateTimeField(
        null=True, blank=True,
        verbose_name='Data ultimo test e-mail',
    )
    ultimo_test_email_esito = models.CharField(
        max_length=10, blank=True, default='',
        choices=[('', 'Mai testato'), ('ok', 'Successo'), ('errore', 'Errore')],
        verbose_name='Esito ultimo test',
    )
    ultimo_test_email_messaggio = models.TextField(
        blank=True, default='',
        verbose_name='Dettaglio ultimo test',
    )
    ultimo_test_email_destinatario = models.CharField(
        max_length=200, blank=True, default='',
        verbose_name='Destinatario ultimo test',
    )

    # ── Aspetto UI ───────────────────────────────────────────────
    colore_primario = models.CharField(
        max_length=7, default='#1b3a5f',
        verbose_name='Colore primario (navbar, titoli)',
        help_text='Formato esadecimale, es. #1b3a5f',
    )
    colore_accent = models.CharField(
        max_length=7, default='#2d6199',
        verbose_name='Colore accent (bottoni, link)',
        help_text='Formato esadecimale, es. #2d6199',
    )
    colore_sfondo = models.CharField(
        max_length=7, default='#f1f4f8',
        verbose_name='Colore sfondo pagina',
        help_text='Formato esadecimale, es. #f1f4f8',
    )
    font_famiglia = models.CharField(
        max_length=30, choices=FONT_CHOICES, default='system',
        verbose_name='Famiglia font',
    )
    font_dimensione_base = models.CharField(
        max_length=4, choices=FONT_SIZE_CHOICES, default='14',
        verbose_name='Dimensione font base',
    )

    # ── Geolocalizzazione presenze (default globale) ────────────
    presenze_geo_enabled = models.BooleanField(
        default=True,
        verbose_name='Abilita timbratura geolocalizzata',
    )
    presenze_geo_require_gps = models.BooleanField(
        default=True,
        verbose_name='Richiedi GPS per timbratura',
    )
    presenze_geo_enforce_geofence = models.BooleanField(
        default=True,
        verbose_name='Blocca timbratura fuori perimetro',
    )
    presenze_geo_center_lat = models.DecimalField(
        max_digits=9,
        decimal_places=6,
        null=True,
        blank=True,
        verbose_name='Latitudine sede (default)',
        help_text='Usata come default globale se l’azienda non ha coordinate proprie.',
    )
    presenze_geo_center_lon = models.DecimalField(
        max_digits=9,
        decimal_places=6,
        null=True,
        blank=True,
        verbose_name='Longitudine sede (default)',
        help_text='Usata come default globale se l’azienda non ha coordinate proprie.',
    )
    presenze_geo_radius_m = models.PositiveIntegerField(
        default=180,
        verbose_name='Raggio geofence default (metri)',
    )
    presenze_geo_enforce_for_test = models.BooleanField(
        default=False,
        verbose_name='Applica geofence anche agli utenti test',
        help_text='Se attivo, anche gli utenti in whitelist test devono rispettare GPS/perimetro.',
    )

    @classmethod
    def get(cls):
        """Restituisce l'unica istanza, creandola se non esiste."""
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj

    @property
    def smtp_use_ssl(self):
        """SSL implicito per porta 465 (Aruba, ecc.). Mutuamente esclusivo con TLS."""
        return self.smtp_port == 465

    def from_email(self):
        """Restituisce il mittente formattato per EmailMessage."""
        if self.email_mittente_nome and self.smtp_user:
            return f"{self.email_mittente_nome} <{self.smtp_user}>"
        return self.smtp_user or 'noreply@gesper.it'

    def __str__(self):
        return 'Configurazione di sistema'


class MovimentoImportPaghe(models.Model):
    """Movimenti economici estratti da import PDF unico (buste/F24)."""

    TIPO_CHOICES = [
        ('BUSTA', 'Busta paga'),
        ('F24', 'Modello F24'),
    ]

    NATURA_BUSTA_CHOICES = [
        ('ORDINARIA', 'Ordinaria'),
        ('TREDICESIMA', 'Tredicesima'),
        ('QUATTORDICESIMA', 'Quattordicesima'),
    ]

    azienda = models.ForeignKey(
        Azienda,
        on_delete=models.CASCADE,
        related_name='movimenti_import_paghe',
        verbose_name='Azienda',
    )
    dipendente = models.ForeignKey(
        'anagrafiche.Dipendente',
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name='movimenti_import_paghe',
        verbose_name='Dipendente',
    )
    documento = models.ForeignKey(
        'documenti.Documento',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='movimenti_import_paghe',
        verbose_name='Documento collegato',
    )
    tipo = models.CharField(max_length=10, choices=TIPO_CHOICES, verbose_name='Tipo movimento')
    natura_busta = models.CharField(
        max_length=20,
        choices=NATURA_BUSTA_CHOICES,
        default='ORDINARIA',
        verbose_name='Natura busta',
        help_text='Valido per tipo=BUSTA; per F24 resta ORDINARIA',
    )
    anno = models.PositiveIntegerField(verbose_name='Anno')
    mese = models.PositiveSmallIntegerField(verbose_name='Mese')
    importo = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True,
        default=Decimal('0.00'),
        verbose_name='Importo (legacy)',
    )
    importo_lordo = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True,
        default=None,
        verbose_name='Importo lordo',
    )
    importo_netto = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True,
        default=None,
        verbose_name='Importo netto',
    )
    f24_tot_debito = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True,
        default=None,
        verbose_name='F24 totale debiti',
    )
    f24_tot_credito = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True,
        default=None,
        verbose_name='F24 totale crediti/compensazioni',
    )
    f24_saldo_finale = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True,
        default=None,
        verbose_name='F24 saldo finale',
    )
    cf_estratto = models.CharField(max_length=16, blank=True, default='', verbose_name='CF estratto')
    nominativo_estratto = models.CharField(max_length=160, blank=True, default='', verbose_name='Nominativo estratto')
    periodo_label = models.CharField(max_length=7, blank=True, default='', verbose_name='Periodo MM/YYYY')
    source_pdf = models.CharField(max_length=260, blank=True, default='', verbose_name='PDF sorgente')
    page_number = models.PositiveIntegerField(null=True, blank=True, verbose_name='Pagina PDF')
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='Creato il')
    updated_at = models.DateTimeField(auto_now=True, verbose_name='Aggiornato il')

    class Meta:
        verbose_name = 'Movimento import paghe'
        verbose_name_plural = 'Movimenti import paghe'
        ordering = ['-anno', '-mese', 'tipo', 'dipendente__cognome', 'dipendente__nome']
        indexes = [
            models.Index(fields=['azienda', 'tipo', 'anno', 'mese']),
            models.Index(fields=['azienda', 'dipendente', 'anno', 'mese']),
            models.Index(fields=['azienda', 'dipendente', 'anno', 'mese', 'natura_busta']),
        ]

    def __str__(self):
        soggetto = f"{self.dipendente}" if self.dipendente else 'Azienda'
        tipo_label = dict(self.TIPO_CHOICES).get(self.tipo, self.tipo)
        return f"{tipo_label} {self.periodo_label or f'{self.mese:02d}/{self.anno}'} - {soggetto}"


class MovimentoImportPagheF24Dettaglio(models.Model):
    """Dettaglio righe imposte F24 estratte dal PDF (per sezione/codice tributo)."""

    SEZIONE_CHOICES = [
        ('ERARIO', 'Sezione Erario'),
        ('INPS', 'Sezione INPS'),
        ('REGIONI', 'Sezione Regioni'),
        ('IMU', 'Sezione IMU e altri tributi locali'),
        ('ALTRI_ENTI', 'Sezione altri enti previdenziali/assicurativi'),
        ('ALTRO', 'Altro'),
    ]

    movimento = models.ForeignKey(
        MovimentoImportPaghe,
        on_delete=models.CASCADE,
        related_name='f24_dettagli',
        verbose_name='Movimento F24',
    )
    documento = models.ForeignKey(
        'documenti.Documento',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='f24_dettagli',
        verbose_name='Documento F24 allegato',
    )
    sezione = models.CharField(max_length=20, choices=SEZIONE_CHOICES, default='ALTRO', verbose_name='Sezione F24')
    codice_tributo = models.CharField(max_length=12, blank=True, default='', verbose_name='Codice tributo')
    anno_riferimento = models.PositiveIntegerField(null=True, blank=True, verbose_name='Anno riferimento')
    periodo_riferimento = models.CharField(max_length=16, blank=True, default='', verbose_name='Periodo riferimento')
    importo_debito = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True, default=None, verbose_name='Importo a debito versato')
    importo_credito = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True, default=None, verbose_name='Importo a credito compensato')
    ordine = models.PositiveIntegerField(default=0, verbose_name='Ordine riga nel PDF')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Dettaglio F24 import paghe'
        verbose_name_plural = 'Dettagli F24 import paghe'
        ordering = ['movimento_id', 'ordine', 'codice_tributo']
        indexes = [
            models.Index(fields=['movimento', 'sezione']),
            models.Index(fields=['codice_tributo', 'anno_riferimento']),
        ]

    def __str__(self):
        movimento_pk = getattr(self.movimento, 'pk', None)
        return f"{movimento_pk or '-'} {self.sezione} {self.codice_tributo} {self.importo_debito or 0}/{self.importo_credito or 0}"
