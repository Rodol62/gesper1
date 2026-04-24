import traceback as tb_module
from django.db import models
from django.conf import settings
from anagrafiche.models import Azienda


class LogAttivita(models.Model):
    TIPO_OPERAZIONE = [
        # Autenticazione
        ('login',              'Accesso'),
        ('logout',             'Uscita'),
        ('login_fallito',      'Accesso fallito'),
        # Candidati
        ('registrazione',      'Registrazione candidato'),
        ('verifica_email',     'Verifica e-mail'),
        ('completa_profilo',   'Completamento profilo'),
        ('candidatura',        'Espressione interesse'),
        ('convalida',          'Convalida candidato'),
        ('revoca_convalida',   'Revoca convalida'),
        # Documenti e file
        ('upload_doc',         'Caricamento documento'),
        ('download_doc',       'Download documento'),
        # Anagrafiche / HR
        ('modifica_anagrafica','Modifica anagrafica'),
        ('crea_proposta',      'Creazione proposta'),
        ('modifica_proposta',  'Modifica proposta'),
        ('assegna_proposta',   'Assegnazione proposta'),
        # Presenze / richieste
        ('richiesta',          'Richiesta ferie/permesso/malattia'),
        ('presenza',           'Registrazione presenza'),
        # Sistema
        ('impostazioni',       'Modifica impostazioni'),
        ('altro',              'Altro'),
    ]

    utente      = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)
    azienda     = models.ForeignKey(Azienda, on_delete=models.SET_NULL, null=True, blank=True)
    operazione  = models.CharField(max_length=30, choices=TIPO_OPERAZIONE)
    descrizione = models.TextField(blank=True)
    data_ora    = models.DateTimeField(auto_now_add=True)
    oggetto_id  = models.CharField(max_length=100, blank=True)
    ip_address  = models.GenericIPAddressField(null=True, blank=True)

    class Meta:
        verbose_name = 'Log attività'
        verbose_name_plural = 'Log attività'
        ordering = ['-data_ora']
        indexes = [
            models.Index(fields=['data_ora']),
            models.Index(fields=['utente', 'data_ora']),
            models.Index(fields=['operazione']),
        ]

    def __str__(self):
        utente = self.utente.username if self.utente else 'Anonimo'
        return f"{self.get_operazione_display()} — {utente} — {self.data_ora:%d/%m/%Y %H:%M}"


class LogErrore(models.Model):
    LIVELLO_CHOICES = [
        ('warning',  'Warning'),
        ('error',    'Errore'),
        ('critical', 'Critico'),
    ]

    data_ora    = models.DateTimeField(auto_now_add=True)
    livello     = models.CharField(max_length=10, choices=LIVELLO_CHOICES, default='error')
    messaggio   = models.TextField()
    traceback   = models.TextField(blank=True)
    url         = models.CharField(max_length=500, blank=True)
    metodo_http = models.CharField(max_length=10, blank=True)
    utente      = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
                                    null=True, blank=True, related_name='log_errori')
    ip_address  = models.GenericIPAddressField(null=True, blank=True)
    risolto     = models.BooleanField(default=False)

    class Meta:
        verbose_name = 'Log errore'
        verbose_name_plural = 'Log errori'
        ordering = ['-data_ora']
        indexes = [
            models.Index(fields=['data_ora']),
            models.Index(fields=['livello', 'risolto']),
        ]

    def __str__(self):
        return f"[{self.livello.upper()}] {self.messaggio[:80]} — {self.data_ora:%d/%m/%Y %H:%M}"

    @classmethod
    def registra(cls, messaggio, exc=None, request=None, livello='error'):
        """Crea un LogErrore. Può essere chiamato da qualunque punto dell'app."""
        traceback_str = ''
        if exc:
            traceback_str = ''.join(
                tb_module.format_exception(type(exc), exc, exc.__traceback__)
            )
        url = metodo = ''
        utente = ip = None
        if request:
            url = request.get_full_path()
            metodo = request.method
            if hasattr(request, 'user') and request.user.is_authenticated:
                utente = request.user
            ip = _get_ip(request)
        cls.objects.create(
            livello=livello,
            messaggio=str(messaggio),
            traceback=traceback_str,
            url=url,
            metodo_http=metodo,
            utente=utente,
            ip_address=ip,
        )


def _get_ip(request):
    x_forwarded = request.META.get('HTTP_X_FORWARDED_FOR')
    if x_forwarded:
        return x_forwarded.split(',')[0].strip()
    return request.META.get('REMOTE_ADDR')
