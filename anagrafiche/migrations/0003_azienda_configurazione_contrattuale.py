from decimal import Decimal
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('rapporto_di_lavoro', '0008_ccnl_decontribuzioneparametro_fringebenefitsoglia_and_more'),
        ('anagrafiche', '0002_alter_dipendente_utente'),
    ]

    operations = [
        migrations.AddField(
            model_name='azienda',
            name='ccnl_predefinito',
            field=models.ForeignKey(
                blank=True,
                help_text='CCNL di riferimento per precompilazione proposta/contratto.',
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='aziende_predefinite',
                to='rapporto_di_lavoro.ccnl',
                verbose_name='CCNL predefinito',
            ),
        ),
        migrations.AddField(
            model_name='azienda',
            name='data_attivazione_contratto',
            field=models.DateField(
                blank=True,
                help_text='Data di aggancio alle decorrenze delle tabelle retributive.',
                null=True,
                verbose_name='Data attivazione contratto',
            ),
        ),
        migrations.AddField(
            model_name='azienda',
            name='note_contrattuali',
            field=models.TextField(blank=True, verbose_name='Note contrattuali'),
        ),
        migrations.AddField(
            model_name='azienda',
            name='ore_giornaliere_standard',
            field=models.DecimalField(decimal_places=2, default=Decimal('8.00'), max_digits=5, verbose_name='Orario giornaliero standard (ore)'),
        ),
        migrations.AddField(
            model_name='azienda',
            name='ore_settimanali_standard',
            field=models.DecimalField(decimal_places=2, default=Decimal('40.00'), max_digits=5, verbose_name='Orario settimanale standard (ore)'),
        ),
        migrations.AddField(
            model_name='azienda',
            name='tipologia_dimensionale',
            field=models.CharField(
                choices=[('piccola', 'Piccola'), ('media', 'Media'), ('grande', 'Grande')],
                default='piccola',
                help_text='Dimensione aziendale per applicazione regole contributive/retributive.',
                max_length=20,
                verbose_name='Tipologia azienda',
            ),
        ),
        migrations.AddField(
            model_name='azienda',
            name='tipo_contratto_predefinito',
            field=models.ForeignKey(
                blank=True,
                help_text='Tipo di contratto standard da proporre nei flussi HR.',
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='aziende_predefinite',
                to='rapporto_di_lavoro.tipocontratto',
                verbose_name='Tipo contratto predefinito',
            ),
        ),
    ]
