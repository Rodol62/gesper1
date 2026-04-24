from django.db import models


class VoceGuida(models.Model):
    """
    Testo di aiuto per un modulo (e opzionalmente per un campo/form specifico).
    I codici devono combaciare con guida.registry.MODULI e con la documentazione.
    """

    class Meta:
        verbose_name = 'Voce guida'
        verbose_name_plural = 'Voci guida'
        ordering = ['codice_modulo', 'ordine', 'codice_campo', 'titolo']
        constraints = [
            models.UniqueConstraint(
                fields=['codice_modulo', 'codice_campo'],
                name='guida_voce_unica_modulo_campo',
            ),
        ]
        indexes = [
            models.Index(fields=['codice_modulo', 'attiva']),
        ]

    codice_modulo = models.SlugField(
        max_length=64,
        verbose_name='Codice modulo',
        help_text='Es. reg-dipendente (vedi guida/registry.py).',
    )
    codice_campo = models.SlugField(
        max_length=64,
        blank=True,
        verbose_name='Codice campo',
        help_text='Vuoto = testo introduttivo del modulo; altrimenti chiave campo/form.',
    )
    titolo = models.CharField(max_length=200, verbose_name='Titolo')
    testo = models.TextField(
        verbose_name='Testo',
        help_text='Testo esplicativo. Righe vuote = paragrafi in visualizzazione.',
    )
    attiva = models.BooleanField(default=True, verbose_name='Attiva')
    ordine = models.PositiveSmallIntegerField(
        default=0,
        verbose_name='Ordine',
        help_text='Ordine di visualizzazione nella pagina modulo (0 = primo).',
    )
    aggiornata_il = models.DateTimeField(auto_now=True, verbose_name='Ultimo aggiornamento')

    def __str__(self):
        if self.codice_campo:
            return f'{self.codice_modulo}/{self.codice_campo}: {self.titolo}'
        return f'{self.codice_modulo}: {self.titolo}'
