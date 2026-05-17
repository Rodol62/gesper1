
from django.db import models
from django.conf import settings
from anagrafiche.models import Dipendente, Azienda

class Documento(models.Model):
    class Meta:
        verbose_name = 'Documento'
        verbose_name_plural = 'Documenti'
        ordering = ['-data_caricamento']

    TIPO_CHOICES = [
        # Documenti aziendali
        ('contratto', 'Contratto di lavoro'),
        ('privacy', 'Autorizzazione privacy'),
        ('busta_paga', 'Busta paga'),
        ('certificato', 'CUD / Certificato fiscale'),
        ('carichi_famiglia', 'Comunicazione carichi di famiglia'),
        # Documenti identità (caricabili dal dipendente)
        ('documento_identita', 'Documento di identità'),
        ('permesso_soggiorno', 'Permesso di soggiorno'),
        ('codice_fiscale_doc', 'Tessera sanitaria / Codice fiscale'),
        # Documenti professionali
        ('curriculum', 'Curriculum vitae'),
        ('attestato', 'Attestato professionale'),
        ('abilitazione', 'Abilitazione tecnica'),
        ('titolo_studio', 'Titolo di studio'),
        ('certificazione', 'Certificazione / Titolo di studio'),
        ('altro', 'F24'),
    ]

    # Gruppi per visualizzazione
    TIPI_AZIENDALI = {'contratto', 'privacy', 'busta_paga', 'certificato', 'carichi_famiglia'}
    TIPI_PERSONALI = {
        'documento_identita', 'permesso_soggiorno', 'codice_fiscale_doc',
        'curriculum', 'attestato', 'abilitazione', 'titolo_studio',
        'certificazione', 'altro',
    }

    azienda = models.ForeignKey(Azienda, on_delete=models.CASCADE, verbose_name='Azienda')
    dipendente = models.ForeignKey(Dipendente, on_delete=models.CASCADE, null=True, blank=True, verbose_name='Dipendente')
    tipo = models.CharField(max_length=30, choices=TIPO_CHOICES, verbose_name='Tipo documento')
    descrizione = models.CharField(max_length=200, blank=True, verbose_name='Descrizione / Titolo')
    file = models.FileField(upload_to='documenti/', verbose_name='File')
    data_caricamento = models.DateTimeField(auto_now_add=True, verbose_name='Data caricamento')
    caricato_da = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, verbose_name='Caricato da')
    caricato_dal_dipendente = models.BooleanField(default=False, verbose_name='Caricato dal dipendente')
    visibile_al_dipendente = models.BooleanField(default=True, verbose_name='Visibile al dipendente')
    visualizzato_da_azienda = models.BooleanField(default=False, verbose_name='Visualizzato dall\'azienda',
                                                   help_text='Se True il documento caricato dal dipendente è stato acquisito dall\'azienda e non può più essere eliminato dal dipendente.')

    def nome_file(self):
        """Restituisce solo il nome del file senza path."""
        import os
        return os.path.basename(self.file.name) if self.file else ''
