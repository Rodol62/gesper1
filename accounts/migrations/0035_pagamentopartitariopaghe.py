# Pagamenti/bonifici dipendenti nel partitario paghe (colonna Dare).

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0034_pianoallocazionebonificiquad'),
        ('anagrafiche', '0001_initial'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='PagamentoPartitarioPaghe',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('data_pagamento', models.DateField(verbose_name='Data pagamento')),
                ('descrizione', models.CharField(blank=True, default='', max_length=220, verbose_name='Descrizione / causale')),
                ('importo', models.DecimalField(decimal_places=2, max_digits=12, verbose_name='Importo pagato (dare)')),
                ('riferimento_bancario', models.CharField(blank=True, default='', max_length=160, verbose_name='Riferimento bancario (CRO/TRN)')),
                ('creato_il', models.DateTimeField(auto_now_add=True, verbose_name='Creato il')),
                ('azienda', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='pagamenti_partitario_paghe', to='anagrafiche.azienda', verbose_name='Azienda')),
                ('dipendente', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='pagamenti_partitario_paghe', to='anagrafiche.dipendente', verbose_name='Dipendente')),
                ('movimento_busta', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='pagamenti_partitario', to='accounts.movimentoimportpaghe', verbose_name='Busta collegata (opz.)')),
                ('registrato_da', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='pagamenti_partitario_paghe_registrati', to=settings.AUTH_USER_MODEL, verbose_name='Registrato da')),
            ],
            options={
                'verbose_name': 'Pagamento partitario paghe',
                'verbose_name_plural': 'Pagamenti partitario paghe',
                'ordering': ['dipendente_id', 'data_pagamento', 'id'],
            },
        ),
        migrations.AddIndex(
            model_name='pagamentopartitariopaghe',
            index=models.Index(fields=['azienda', 'dipendente', 'data_pagamento'], name='acc_ppp_az_dip_dt_idx'),
        ),
    ]
