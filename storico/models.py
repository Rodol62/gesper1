from django.db import models
from anagrafiche.models import Dipendente, Azienda
from documenti.models import Documento
from presenze.models import Presenza
from django.contrib.auth import get_user_model

class EventoStorico(models.Model):
    TIPO_EVENTO = [
        ('assunzione', 'Assunzione'),
        ('variazione', 'Variazione'),
        ('documento', 'Documento'),
        ('presenza', 'Presenza'),
        ('richiesta', 'Richiesta'),
        ('cessazione', 'Cessazione'),
        ('altro', 'Altro'),
    ]
    dipendente = models.ForeignKey(Dipendente, on_delete=models.CASCADE)
    azienda = models.ForeignKey(Azienda, on_delete=models.CASCADE)
    tipo = models.CharField(max_length=20, choices=TIPO_EVENTO)
    data_evento = models.DateTimeField()
    descrizione = models.TextField(blank=True)
    documento = models.ForeignKey(Documento, on_delete=models.SET_NULL, null=True, blank=True)
    presenza = models.ForeignKey(Presenza, on_delete=models.SET_NULL, null=True, blank=True)

# Modello Libro Paga mensile
class LibroPaga(models.Model):
    dipendente = models.ForeignKey(Dipendente, on_delete=models.CASCADE)
    azienda = models.ForeignKey(Azienda, on_delete=models.CASCADE)
    mese = models.IntegerField()
    anno = models.IntegerField()
    stipendio = models.DecimalField(max_digits=10, decimal_places=2)
    contributi = models.DecimalField(max_digits=10, decimal_places=2)
    trattenute = models.DecimalField(max_digits=10, decimal_places=2)
    netto = models.DecimalField(max_digits=10, decimal_places=2)
    data_creazione = models.DateTimeField(auto_now_add=True)

# Registro Unico accessi e attività
class RegistroUnico(models.Model):
    utente = models.ForeignKey(get_user_model(), on_delete=models.CASCADE)
    azienda = models.ForeignKey(Azienda, on_delete=models.CASCADE, null=True, blank=True)
    data_accesso = models.DateTimeField(auto_now_add=True)
    descrizione = models.TextField()

# Storico dettagliato accessi/attività
class StoricoAccessi(models.Model):
    utente = models.ForeignKey(get_user_model(), on_delete=models.CASCADE)
    data_azione = models.DateTimeField(auto_now_add=True)
    azione = models.CharField(max_length=100)
    descrizione = models.TextField(blank=True)

# Modello LibroPaga storico — conforme Libro Unico del Lavoro (L. 133/2008, art. 39)
class LibroPagaStorico(models.Model):
    FONTE_CHOICES = [
        ('simulazione', 'Simulazione paga'),
        ('documento', 'Documento busta paga'),
        ('manuale', 'Inserimento manuale'),
        ('importazione', 'Importazione dati storici'),
    ]

    # ── Identificazione rapporto ──────────────────────────────────────────────
    dipendente = models.ForeignKey(Dipendente, on_delete=models.CASCADE, related_name='libri_paga_storici')
    azienda = models.ForeignKey(Azienda, on_delete=models.CASCADE)
    data_inizio_rapporto = models.DateField(verbose_name='Inizio rapporto')
    data_fine_rapporto = models.DateField(null=True, blank=True, verbose_name='Fine rapporto')
    livello_ccnl = models.CharField(max_length=50, blank=True, verbose_name='Livello CCNL')
    qualifica = models.CharField(max_length=200, blank=True, verbose_name='Qualifica')
    tipo_contratto = models.CharField(max_length=100, blank=True, verbose_name='Tipo contratto')

    # ── Periodo di paga ───────────────────────────────────────────────────────
    periodo_riferimento = models.CharField(max_length=7, help_text='Formato MM/YYYY', verbose_name='Periodo')
    data_pagamento = models.DateField(verbose_name='Data pagamento')
    ordinamento = models.PositiveIntegerField(default=0, help_text='Riordino manuale cronologico')

    # ── Ore lavorate (art. 39 c.1 L.133/2008) ────────────────────────────────
    ore_ordinarie = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True, verbose_name='Ore ordinarie')
    ore_straordinario = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True, verbose_name='Ore straordinario')
    ore_assenza = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True, verbose_name='Ore assenza/ferie/ROL')

    # ── Competenze (lordo) ────────────────────────────────────────────────────
    retribuzione_base = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True, verbose_name='Retribuzione base (€)')
    indennita_accessorie = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True, verbose_name='Indennità e accessori (€)')
    lordo_mensile = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True, verbose_name='Lordo imponibile (€)')

    # ── Trattenute a carico dipendente ────────────────────────────────────────
    inps_dipendente = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True, verbose_name='INPS c/dipendente (€)')
    irpef = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True, verbose_name='IRPEF trattenuta (€)')
    addizionali = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True, verbose_name='Addizionali regionali/comunali (€)')
    altre_trattenute = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True, verbose_name='Altre trattenute (€)')
    trattamento_integrativo = models.DecimalField(max_digits=8, decimal_places=2, null=True, blank=True, verbose_name='Trattamento integrativo/Bonus (€)')

    # ── Netto erogato ─────────────────────────────────────────────────────────
    importo = models.DecimalField(max_digits=10, decimal_places=2, verbose_name='Netto erogato (€)')

    # ── Oneri a carico azienda ────────────────────────────────────────────────
    inps_azienda = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True, verbose_name='INPS c/azienda (€)')
    inail_azienda = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True, verbose_name='INAIL c/azienda (€)')
    costo_azienda = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True, verbose_name='Costo totale azienda (€)')

    # ── Accantonamenti ────────────────────────────────────────────────────────
    tfr_mensile = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True, verbose_name='TFR accantonato (€)')
    rateo_13 = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True, verbose_name='Rateo 13ª (€)')
    rateo_14 = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True, verbose_name='Rateo 14ª (€)')

    # ── Metadati ──────────────────────────────────────────────────────────────
    fonte_dati = models.CharField(max_length=20, choices=FONTE_CHOICES, default='manuale', verbose_name='Fonte dati')
    note = models.TextField(blank=True, null=True, verbose_name='Note')
    creato_il = models.DateTimeField(auto_now_add=True)
    modificato_il = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['dipendente', 'ordinamento', 'data_pagamento']
        verbose_name = 'Voce libro paga'
        verbose_name_plural = 'Libro Unico del Lavoro'

    def __str__(self):
        return f"{self.dipendente} — {self.periodo_riferimento} — netto {self.importo} €"
