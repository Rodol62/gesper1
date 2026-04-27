
from django.db import models
from django.db.models import Max, Q
from django.conf import settings
from django.contrib.auth.models import AbstractUser

class Azienda(models.Model):
    TIPOLOGIA_DIMENSIONALE_CHOICES = [
        ('piccola', 'Piccola'),
        ('media', 'Media'),
        ('grande', 'Grande'),
    ]

    class Meta:
        verbose_name = 'Azienda'
        verbose_name_plural = 'Aziende'
    nome = models.CharField(max_length=255, verbose_name='Ragione sociale')
    partita_iva = models.CharField(max_length=20, unique=True, verbose_name='Partita IVA')
    indirizzo = models.CharField(
        max_length=255,
        verbose_name='Indirizzo sede legale (riepilogo)',
        help_text='Compilato automaticamente da via, CAP e comune; usato nei report che richiedono una sola riga.',
    )
    sede_legale_regione = models.CharField(
        max_length=100,
        blank=True,
        default='',
        verbose_name='Regione (sede legale)',
    )
    sede_legale_provincia = models.CharField(
        max_length=10,
        blank=True,
        default='',
        verbose_name='Provincia (sigla, sede legale)',
    )
    sede_legale_comune = models.CharField(
        max_length=120,
        blank=True,
        default='',
        verbose_name='Comune (sede legale)',
    )
    sede_legale_cap = models.CharField(
        max_length=10,
        blank=True,
        default='',
        verbose_name='CAP (sede legale)',
    )
    sede_legale_via = models.CharField(
        max_length=255,
        blank=True,
        default='',
        verbose_name='Via e numero civico (sede legale)',
        help_text='Es. Via Roma 12 — da affiancare a comune e CAP.',
    )
    amministratore_pro_tempore_nome = models.CharField(
        max_length=200,
        blank=True,
        default='',
        verbose_name='Amministratore pro tempore (nome e cognome)',
    )
    amministratore_pro_tempore_ruolo = models.CharField(
        max_length=150,
        blank=True,
        default='',
        verbose_name='Amministratore pro tempore (ruolo)',
        help_text='Es. Amministratore delegato, Legale rappresentante.',
    )
    email = models.EmailField(verbose_name='Email')
    telefono = models.CharField(max_length=30, blank=True, verbose_name='Telefono')

    # Tipizzazione contrattuale azienda
    tipologia_dimensionale = models.CharField(
        max_length=20,
        choices=TIPOLOGIA_DIMENSIONALE_CHOICES,
        default='piccola',
        verbose_name='Tipologia azienda',
        help_text='Dimensione aziendale per applicazione regole contributive/retributive.'
    )
    ccnl_predefinito = models.ForeignKey(
        'rapporto_di_lavoro.CCNL',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='aziende_predefinite',
        verbose_name='CCNL predefinito',
        help_text='CCNL di riferimento per precompilazione proposta/contratto.'
    )
    tipo_contratto_predefinito = models.ForeignKey(
        'rapporto_di_lavoro.TipoContratto',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='aziende_predefinite',
        verbose_name='Tipo contratto predefinito',
        help_text='Tipo di contratto standard da proporre nei flussi HR.'
    )
    ore_settimanali_standard = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        default=40,
        verbose_name='Orario settimanale standard (ore)'
    )
    ore_giornaliere_standard = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        default=8,
        verbose_name='Orario giornaliero standard (ore)'
    )
    data_attivazione_contratto = models.DateField(
        null=True,
        blank=True,
        verbose_name='Data attivazione contratto',
        help_text='Data di aggancio alle decorrenze delle tabelle retributive.'
    )
    note_contrattuali = models.TextField(
        blank=True,
        verbose_name='Note contrattuali'
    )

    # Geolocalizzazione sede lavorativa (usata per check-in/check-out geofence)
    sede_lavorativa_indirizzo = models.CharField(
        max_length=255,
        blank=True,
        verbose_name='Indirizzo sede lavorativa',
        help_text='Indirizzo operativo dove avviene la timbratura (può differire dalla sede sociale).'
    )
    sede_lavorativa_lat = models.DecimalField(
        max_digits=9,
        decimal_places=6,
        null=True,
        blank=True,
        verbose_name='Latitudine sede lavorativa',
        help_text='Formato decimale, es. 38.117940'
    )
    sede_lavorativa_lon = models.DecimalField(
        max_digits=9,
        decimal_places=6,
        null=True,
        blank=True,
        verbose_name='Longitudine sede lavorativa',
        help_text='Formato decimale, es. 13.362860'
    )
    sede_lavorativa_raggio_m = models.PositiveIntegerField(
        default=180,
        verbose_name='Raggio geofence sede (metri)',
        help_text='Raggio massimo consentito per timbratura valida.'
    )

    def __str__(self):
        return self.nome


class Dipendente(models.Model):
    MANSIONE_CHOICES = [
        ('', '— Non specificata —'),
        ('cuoco', 'Cuoco/a'),
        ('piazzista', 'Piazziolo/a'),
        ('cameriere', 'Cameriere/a'),
        ('fattorino', 'Fattorino/a'),
        ('amministrativo', 'Amministrativo'),
    ]
    STATO_CHOICES = [
        ('attivo', 'Attivo'),
        ('cessato', 'Cessato'),
        ('candidato', 'Candidato'),
    ]
    SESSO_CHOICES = [
        ('', '— Non specificato —'),
        ('M', 'Maschile'),
        ('F', 'Femminile'),
        ('A', 'Preferisco non specificare'),
    ]

    class Meta:
        verbose_name = 'Dipendente'
        verbose_name_plural = 'Dipendenti'
        constraints = [
            models.UniqueConstraint(
                fields=['azienda', 'matricola'],
                condition=Q(matricola__isnull=False),
                name='uniq_dip_matricola_per_azienda',
            ),
        ]

    azienda = models.ForeignKey(Azienda, on_delete=models.CASCADE, related_name='dipendenti', verbose_name='Azienda')
    utente = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        verbose_name='Utente'
    )
    matricola = models.PositiveIntegerField(
        null=True,
        blank=True,
        verbose_name='Matricola',
        help_text='Numero univoco per azienda. Vuoto in creazione = assegnazione automatica.',
    )
    nome = models.CharField(max_length=100, verbose_name='Nome')
    cognome = models.CharField(max_length=100, verbose_name='Cognome')
    codice_fiscale = models.CharField(max_length=16, unique=True, null=True, blank=True, verbose_name='Codice Fiscale')
    data_nascita = models.DateField(null=True, blank=True, verbose_name='Data di nascita')
    cittadinanza = models.CharField(max_length=100, blank=True, default='ITALIANA', verbose_name='Cittadinanza')
    luogo_nascita = models.CharField(max_length=100, blank=True, verbose_name='Luogo di nascita')
    paese_nascita = models.CharField(max_length=100, blank=True, default='ITALIA', verbose_name='Paese di nascita')
    regione_nascita = models.CharField(max_length=100, blank=True, verbose_name='Regione di nascita')
    provincia_nascita = models.CharField(max_length=100, blank=True, verbose_name='Provincia di nascita')
    comune_nascita = models.CharField(max_length=100, blank=True, verbose_name='Comune di nascita')
    sesso = models.CharField(max_length=1, blank=True, choices=SESSO_CHOICES, default='', verbose_name='Sesso')
    indirizzo = models.CharField(max_length=255, blank=True, verbose_name='Indirizzo')
    cap = models.CharField(max_length=10, blank=True, verbose_name='CAP')
    citta = models.CharField(max_length=100, blank=True, verbose_name='Citta')
    provincia = models.CharField(max_length=2, blank=True, verbose_name='Provincia')
    paese_residenza = models.CharField(max_length=100, blank=True, default='ITALIA', verbose_name='Paese di residenza')
    regione_residenza = models.CharField(max_length=100, blank=True, verbose_name='Regione di residenza')
    domicilio_uguale_residenza = models.BooleanField(default=True, verbose_name='Domicilio uguale a residenza')
    domicilio_indirizzo = models.CharField(max_length=255, blank=True, verbose_name='Domicilio - indirizzo')
    domicilio_cap = models.CharField(max_length=10, blank=True, verbose_name='Domicilio - CAP')
    domicilio_comune = models.CharField(max_length=100, blank=True, verbose_name='Domicilio - Comune')
    domicilio_provincia = models.CharField(max_length=2, blank=True, verbose_name='Domicilio - Provincia')
    paese_domicilio = models.CharField(max_length=100, blank=True, default='ITALIA', verbose_name='Domicilio - Paese')
    domicilio_regione = models.CharField(max_length=100, blank=True, verbose_name='Domicilio - Regione')
    email = models.EmailField(blank=True, verbose_name='Email')
    telefono = models.CharField(max_length=30, blank=True, verbose_name='Telefono')
    data_assunzione = models.DateField(null=True, blank=True, verbose_name='Data assunzione')
    data_cessazione = models.DateField(null=True, blank=True, verbose_name='Data cessazione')
    ruolo = models.CharField(max_length=100, verbose_name='Ruolo')
    livello = models.CharField(max_length=50, blank=True, verbose_name='Livello')
    mansione = models.CharField(
        max_length=32,
        blank=True,
        choices=MANSIONE_CHOICES,
        default='',
        verbose_name='Mansione',
        help_text='Qualifica operativa / reparto (lista standard).',
    )
    stato = models.CharField(max_length=20, default='attivo', choices=STATO_CHOICES, verbose_name='Stato')
    # altri dati lavorativi

    def _normalizza_testi_maiuscolo(self):
        """Uniformità dati: testi anagrafici salvati in maiuscolo (email esclusa)."""
        for attr in (
            'nome', 'cognome', 'cittadinanza', 'luogo_nascita',
            'paese_nascita',
            'regione_nascita', 'provincia_nascita', 'comune_nascita',
            'indirizzo', 'cap', 'citta', 'provincia', 'paese_residenza', 'regione_residenza',
            'domicilio_indirizzo', 'domicilio_cap', 'domicilio_comune', 'domicilio_provincia', 'paese_domicilio', 'domicilio_regione',
            'ruolo', 'livello',
        ):
            v = getattr(self, attr, None)
            if v and isinstance(v, str):
                setattr(self, attr, v.strip().upper())
        if self.codice_fiscale:
            self.codice_fiscale = self.codice_fiscale.strip().upper()

    def save(self, *args, **kwargs):
        self._normalizza_testi_maiuscolo()
        inserting = self.pk is None
        if inserting and self.matricola is None and self.azienda_id:
            agg = (
                Dipendente.objects.filter(azienda_id=self.azienda_id)
                .exclude(matricola__isnull=True)
                .aggregate(m=Max('matricola'))
            )
            self.matricola = (agg['m'] or 0) + 1
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.nome} {self.cognome} ({self.azienda.nome if hasattr(self, 'azienda') and self.azienda else ''})"


class ComunicazioneRecessoProva(models.Model):
    STATO_CHOICES = [
        ('bozza', 'Bozza'),
        ('in_verifica_consulente', 'In verifica consulente'),
        ('verificata_consulente', 'Verificata consulente'),
        ('firmata_admin', 'Firmata amministratore'),
        ('inviata_dipendente', 'Inviata al dipendente'),
    ]

    azienda = models.ForeignKey(Azienda, on_delete=models.CASCADE, related_name='comunicazioni_recesso_prova')
    dipendente = models.ForeignKey(Dipendente, on_delete=models.CASCADE, related_name='comunicazioni_recesso_prova')
    rapporto = models.ForeignKey(
        'rapporto_di_lavoro.RapportoDiLavoro',
        on_delete=models.CASCADE,
        related_name='comunicazioni_recesso_prova',
    )
    stato = models.CharField(max_length=40, choices=STATO_CHOICES, default='bozza')
    testo_bozza = models.TextField(blank=True, default='')
    note_consulente = models.TextField(blank=True, default='')
    consulente_verificatore = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='recessi_prova_verificati',
    )
    data_verifica_consulente = models.DateTimeField(null=True, blank=True)
    firmato_da_admin = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='recessi_prova_firmati',
    )
    firmatario_nome = models.CharField(max_length=150, blank=True, default='')
    firmatario_ruolo = models.CharField(max_length=150, blank=True, default='')
    data_firma_admin = models.DateTimeField(null=True, blank=True)
    documento_pdf = models.ForeignKey(
        'documenti.Documento',
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='comunicazioni_recesso_prova_pdf',
    )
    inviata_email = models.BooleanField(default=False)
    data_invio_email = models.DateTimeField(null=True, blank=True)
    creato_da = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='recessi_prova_creati',
    )
    modificato_da = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='recessi_prova_modificati',
    )
    data_creazione = models.DateTimeField(auto_now_add=True)
    data_modifica = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Comunicazione recesso periodo prova'
        verbose_name_plural = 'Comunicazioni recesso periodo prova'
        ordering = ['-data_creazione']

    def __str__(self):
        return f"Recesso prova {self.rapporto.numero_contratto} - {self.dipendente.cognome}"
