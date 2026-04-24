from decimal import Decimal

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('anagrafiche', '0005_delete_user'),
        ('documenti', '0005_documento_tipo_nuovi_choices'),
        ('accounts', '0010_ruolo_remove_user_ruolo_user_ruoli'),
    ]

    operations = [
        migrations.CreateModel(
            name='MovimentoImportPaghe',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('tipo', models.CharField(choices=[('BUSTA', 'Busta paga'), ('F24', 'Modello F24')], max_length=10, verbose_name='Tipo movimento')),
                ('anno', models.PositiveIntegerField(verbose_name='Anno')),
                ('mese', models.PositiveSmallIntegerField(verbose_name='Mese')),
                ('importo', models.DecimalField(blank=True, decimal_places=2, default=Decimal('0.00'), max_digits=10, null=True, verbose_name='Importo')),
                ('cf_estratto', models.CharField(blank=True, default='', max_length=16, verbose_name='CF estratto')),
                ('nominativo_estratto', models.CharField(blank=True, default='', max_length=160, verbose_name='Nominativo estratto')),
                ('periodo_label', models.CharField(blank=True, default='', max_length=7, verbose_name='Periodo MM/YYYY')),
                ('source_pdf', models.CharField(blank=True, default='', max_length=260, verbose_name='PDF sorgente')),
                ('page_number', models.PositiveIntegerField(blank=True, null=True, verbose_name='Pagina PDF')),
                ('created_at', models.DateTimeField(auto_now_add=True, verbose_name='Creato il')),
                ('updated_at', models.DateTimeField(auto_now=True, verbose_name='Aggiornato il')),
                ('azienda', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='movimenti_import_paghe', to='anagrafiche.azienda', verbose_name='Azienda')),
                ('dipendente', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name='movimenti_import_paghe', to='anagrafiche.dipendente', verbose_name='Dipendente')),
                ('documento', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='movimenti_import_paghe', to='documenti.documento', verbose_name='Documento collegato')),
            ],
            options={
                'verbose_name': 'Movimento import paghe',
                'verbose_name_plural': 'Movimenti import paghe',
                'ordering': ['-anno', '-mese', 'tipo', 'dipendente__cognome', 'dipendente__nome'],
            },
        ),
        migrations.AddIndex(
            model_name='movimentoimportpaghe',
            index=models.Index(fields=['azienda', 'tipo', 'anno', 'mese'], name='accounts_mov_azienda__c45c2c_idx'),
        ),
        migrations.AddIndex(
            model_name='movimentoimportpaghe',
            index=models.Index(fields=['azienda', 'dipendente', 'anno', 'mese'], name='accounts_mov_azienda__1fd687_idx'),
        ),
    ]
