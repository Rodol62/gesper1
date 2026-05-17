"""
Partitario semplificato «netti da pagare» vs «pagamenti effettuati» per dipendente.

I movimenti in **DARE** nascono dalla sincronizzazione con :class:`documenti.models.CedolinoMotoreV4`
(netto busta). I movimenti in **AVERE** sono pagamenti registrati manualmente dall'amministratore.

**Referenzialità (FK e ciclo di vita)**

- ``azienda`` / ``dipendente``: ``on_delete=CASCADE`` — con l’azienda o il dipendente spariscono
  tutte le righe partitario collegate (allineamento multiaziendale / anagrafica).
- ``cedolino_motore_v4``: ``SET_NULL`` a livello DB (non blocca cancellazioni ordinate); alla
  **cancellazione** del cedolino v4 il segnale in ``partitario_netti.signals`` elimina i DARE che
  lo referenziavano, così non restano netti «da busta» senza estrazione sottostante.
- ``documento_busta`` / ``documento_ricevuta``: ``SET_NULL`` — se il PDF viene rimosso, la riga
  partitario resta per storico saldi; per le ricevute dei pagamenti si può ancora eliminare il
  movimento AVERE dall’interfaccia admin dedicata.
"""

from __future__ import annotations

import calendar

from django.conf import settings
from django.db import models

from anagrafiche.models import Azienda, Dipendente


class MovimentoPartitarioNettoDipendente(models.Model):
    """
    Singola riga di partitario: netto da busta (DARE) oppure pagamento al dipendente (AVERE).
    """

    class TipoMovimento(models.TextChoices):
        BUSTA_NETTO = "busta_netto", "Netto da busta paga (DARE)"
        PAGAMENTO = "pagamento", "Pagamento al dipendente (AVERE)"

    class Lato(models.TextChoices):
        DARE = "DARE", "Dare"
        AVERE = "AVERE", "Avere"

    class MetodoPagamento(models.TextChoices):
        CONTANTI = "contanti", "Contanti"
        BONIFICO = "bonifico", "Bonifico bancario"

    class Meta:
        verbose_name = "Movimento partitario netto dipendente"
        verbose_name_plural = "Movimenti partitario netti dipendenti"
        ordering = ("-anno", "-mese", "dipendente__cognome", "dipendente__nome", "-data_contabile", "-id")
        indexes = [
            models.Index(fields=("azienda", "anno", "mese")),
            models.Index(fields=("azienda", "dipendente", "anno", "mese")),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=("azienda", "dipendente", "anno", "mese", "natura_busta"),
                name="uniq_partitario_netto_busta_periodo",
                condition=models.Q(tipo_movimento="busta_netto"),
            ),
        ]

    azienda = models.ForeignKey(
        Azienda,
        on_delete=models.CASCADE,
        related_name="movimenti_partitario_netto",
        verbose_name="Azienda",
    )
    dipendente = models.ForeignKey(
        Dipendente,
        on_delete=models.CASCADE,
        related_name="movimenti_partitario_netto",
        verbose_name="Dipendente",
    )
    tipo_movimento = models.CharField(
        max_length=20,
        choices=TipoMovimento.choices,
        verbose_name="Tipo movimento",
    )
    lato = models.CharField(
        max_length=5,
        choices=Lato.choices,
        verbose_name="Dare / Avere",
    )
    anno = models.PositiveSmallIntegerField(verbose_name="Anno di competenza")
    mese = models.PositiveSmallIntegerField(verbose_name="Mese (1–12)")
    data_contabile = models.DateField(
        verbose_name="Data contabile",
        help_text="Ultimo giorno del mese per il netto busta; data effettiva del pagamento per l'AVERE.",
    )
    importo = models.DecimalField(max_digits=14, decimal_places=2, verbose_name="Importo (€)")
    natura_busta = models.CharField(
        max_length=20,
        default="ORDINARIA",
        verbose_name="Natura busta (riferimento)",
        help_text="Ordinaria / tredicesima / quattordicesima; per i pagamenti resta «ordinaria» se non diversamente indicato.",
    )

    cedolino_motore_v4 = models.ForeignKey(
        "documenti.CedolinoMotoreV4",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="movimenti_partitario_netto",
        verbose_name="Cedolino motore v4",
    )
    documento_busta = models.ForeignKey(
        "documenti.Documento",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="movimenti_partitario_da_busta",
        verbose_name="Documento busta collegato",
    )

    metodo_pagamento = models.CharField(
        max_length=20,
        choices=MetodoPagamento.choices,
        blank=True,
        default="",
        verbose_name="Modalità pagamento",
    )
    documento_ricevuta = models.ForeignKey(
        "documenti.Documento",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="movimenti_partitario_ricevuta",
        verbose_name="Allegato ricevuta / bonifico",
    )
    documento_ricevuta_firma = models.ForeignKey(
        "documenti.Documento",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="movimenti_partitario_ricevuta_firma",
        verbose_name="Ricevuta contanti in area dipendente (firma)",
        help_text="PDF generato reso visibile al dipendente in «I miei documenti»; revocabile o rimosso automaticamente se cambiano i dati del pagamento.",
    )
    causale = models.TextField(
        blank=True,
        default="",
        verbose_name="Causale",
        help_text="Motivazione contabile del pagamento (es. saldo netto competenza, acconto, storno, …).",
    )

    inserito_da = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="movimenti_partitario_netto_inseriti",
        verbose_name="Inserito da",
    )
    creato_il = models.DateTimeField(auto_now_add=True, verbose_name="Creato il")
    aggiornato_il = models.DateTimeField(auto_now=True, verbose_name="Aggiornato il")

    def __str__(self) -> str:
        return f"{self.dipendente_id} {self.anno}-{self.mese:02d} {self.tipo_movimento} {self.importo} €"

    def save(self, *args, **kwargs) -> None:
        if self.tipo_movimento == self.TipoMovimento.BUSTA_NETTO:
            self.lato = self.Lato.DARE
            if not self.metodo_pagamento:
                self.metodo_pagamento = ""
        else:
            self.lato = self.Lato.AVERE
        super().save(*args, **kwargs)

    @staticmethod
    def ultimo_giorno_mese(anno: int, mese: int) -> "date":
        from datetime import date

        ult = calendar.monthrange(anno, mese)[1]
        return date(anno, mese, ult)
