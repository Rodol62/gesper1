# Import Excel estratto conto + righe agganciate al partitario studio.

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('accounts', '0031_movimentoregistrostudioconsulente_partitario'),
    ]

    operations = [
        migrations.CreateModel(
            name='ImportEstrattoContoStudio',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('nome_file', models.CharField(max_length=280, verbose_name='Nome file')),
                (
                    'file',
                    models.FileField(
                        blank=True,
                        null=True,
                        upload_to='registro_studio_estratto/%Y/%m/',
                        verbose_name='File Excel',
                    ),
                ),
                ('importato_il', models.DateTimeField(auto_now_add=True, verbose_name='Importato il')),
                ('righe_lette', models.PositiveIntegerField(default=0, verbose_name='Righe lette')),
                ('righe_agganciate', models.PositiveIntegerField(default=0, verbose_name='Righe agganciate')),
                (
                    'azienda',
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name='import_estratti_studio',
                        to='anagrafiche.azienda',
                        verbose_name='Azienda',
                    ),
                ),
                (
                    'importato_da',
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name='import_estratti_studio',
                        to=settings.AUTH_USER_MODEL,
                        verbose_name='Importato da',
                    ),
                ),
            ],
            options={
                'verbose_name': 'Import estratto conto studio',
                'verbose_name_plural': 'Import estratti conto studio',
                'ordering': ['-importato_il'],
            },
        ),
        migrations.CreateModel(
            name='RigaEstrattoContoStudio',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('indice_riga', models.PositiveIntegerField(verbose_name='Riga foglio (1-based)')),
                ('descrizione', models.CharField(blank=True, default='', max_length=600, verbose_name='Descrizione')),
                (
                    'importo_excel',
                    models.DecimalField(
                        blank=True,
                        decimal_places=2,
                        max_digits=14,
                        null=True,
                        verbose_name='Importo (Excel)',
                    ),
                ),
                ('data_excel', models.DateField(blank=True, null=True, verbose_name='Data (Excel)')),
                (
                    'riferimento_excel',
                    models.CharField(blank=True, default='', max_length=200, verbose_name='Riferimento / CRO'),
                ),
                ('celle_raw', models.JSONField(blank=True, default=dict, verbose_name='Valori colonne (debug)')),
                (
                    'esito_match',
                    models.CharField(
                        choices=[
                            ('agganciato', 'Agganciato'),
                            ('non_trovato', 'Non trovato'),
                            ('saltato', 'Saltato (vuoto)'),
                        ],
                        default='non_trovato',
                        max_length=20,
                        verbose_name='Esito',
                    ),
                ),
                (
                    'importazione',
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name='righe',
                        to='accounts.importestrattocontostudio',
                        verbose_name='Import',
                    ),
                ),
                (
                    'movimento',
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name='righe_estratto_importate',
                        to='accounts.movimentoregistrostudioconsulente',
                        verbose_name='Movimento agganciato',
                    ),
                ),
            ],
            options={
                'verbose_name': 'Riga estratto conto importata',
                'verbose_name_plural': 'Righe estratto conto importate',
                'ordering': ['importazione_id', 'indice_riga'],
            },
        ),
        migrations.AddIndex(
            model_name='rigaestrattocontostudio',
            index=models.Index(fields=['importazione', 'esito_match'], name='accounts_ri_importa_0f90b8_idx'),
        ),
    ]
