# Generated manually for partitario consulente–azienda (bonifici, tipo riga).

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0030_movimento_registro_studio_consulente'),
    ]

    operations = [
        migrations.RemoveConstraint(
            model_name='movimentoregistrostudioconsulente',
            name='uniq_regstudio_azienda_nomefile',
        ),
        migrations.AddField(
            model_name='movimentoregistrostudioconsulente',
            name='tipo_riga',
            field=models.CharField(
                choices=[
                    ('documento', 'Documento (proforma / parcella)'),
                    ('bonifico', 'Bonifico / pagamento'),
                    ('rettifica', 'Rettifica manuale'),
                ],
                default='documento',
                help_text='Documento inviato dal consulente, bonifico registrato dall’azienda/consulente, o rettifica.',
                max_length=20,
                verbose_name='Tipo movimento',
            ),
        ),
        migrations.AddField(
            model_name='movimentoregistrostudioconsulente',
            name='riferimento_pagamento',
            field=models.CharField(
                blank=True,
                default='',
                help_text='Es. CRO, TRN, ordinativo banca, data valuta bonifico.',
                max_length=160,
                verbose_name='Riferimento pagamento',
            ),
        ),
        migrations.AddField(
            model_name='movimentoregistrostudioconsulente',
            name='causale_pagamento',
            field=models.CharField(blank=True, default='', max_length=220, verbose_name='Causale / descrizione pagamento'),
        ),
        migrations.AlterModelOptions(
            name='movimentoregistrostudioconsulente',
            options={
                'ordering': ['data_documento', 'importato_il', 'id'],
                'verbose_name': 'Movimento partitario consulente–azienda',
                'verbose_name_plural': 'Movimenti partitario consulente–azienda',
            },
        ),
        migrations.RemoveIndex(
            model_name='movimentoregistrostudioconsulente',
            name='accounts_mo_azienda_0b4a41_idx',
        ),
        migrations.AddIndex(
            model_name='movimentoregistrostudioconsulente',
            index=models.Index(fields=['azienda', 'tipo_riga', 'data_documento'], name='accounts_mo_azienda_tipo_data_idx'),
        ),
    ]
