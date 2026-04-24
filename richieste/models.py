from django.db import models
from django.conf import settings
from anagrafiche.models import Dipendente, Azienda

class Richiesta(models.Model):
    TIPO_CHOICES = [
        ('ferie', 'Ferie'),
        ('permesso', 'Permesso'),
        ('malattia', 'Malattia'),
        ('generica', 'Richiesta generica / Chiarimento'),
        ('altro', 'Altro'),
    ]
    STATO_CHOICES = [
        ('inviata', 'Inviata'),
        ('approvata', 'Approvata'),
        ('rifiutata', 'Rifiutata'),
        ('chiusa', 'Chiusa'),
    ]

    dipendente = models.ForeignKey(Dipendente, on_delete=models.CASCADE, related_name='richieste')
    azienda = models.ForeignKey(Azienda, on_delete=models.CASCADE)
    tipo = models.CharField(max_length=20, choices=TIPO_CHOICES)
    # Per ferie/permesso/malattia; nullable per richieste generiche
    data_inizio = models.DateField(null=True, blank=True)
    data_fine = models.DateField(null=True, blank=True)
    motivo = models.CharField(max_length=255, blank=True)
    # Testo libero per richieste generiche o chiarimenti
    testo_richiesta = models.TextField(blank=True, verbose_name='Testo richiesta')
    stato = models.CharField(max_length=20, choices=STATO_CHOICES, default='inviata')
    richiesta_da = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True)
    data_richiesta = models.DateTimeField(auto_now_add=True)
    data_risposta = models.DateTimeField(null=True, blank=True)
    risposta_da = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='risposte_richieste')
    note_risposta = models.TextField(blank=True, verbose_name='Risposta / Note')

    class Meta:
        ordering = ['-data_richiesta']
        verbose_name = 'Richiesta'
        verbose_name_plural = 'Richieste'

    def __str__(self):
        return f"{self.get_tipo_display()} - {self.dipendente} ({self.get_stato_display()})"
