from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0013_movimentoimportpaghe_f24_totals'),
    ]

    operations = [
        migrations.CreateModel(
            name='MovimentoImportPagheF24Dettaglio',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('sezione', models.CharField(choices=[('ERARIO', 'Sezione Erario'), ('INPS', 'Sezione INPS'), ('REGIONI', 'Sezione Regioni'), ('IMU', 'Sezione IMU e altri tributi locali'), ('ALTRI_ENTI', 'Sezione altri enti previdenziali/assicurativi'), ('ALTRO', 'Altro')], default='ALTRO', max_length=20, verbose_name='Sezione F24')),
                ('codice_tributo', models.CharField(blank=True, default='', max_length=12, verbose_name='Codice tributo')),
                ('anno_riferimento', models.PositiveIntegerField(blank=True, null=True, verbose_name='Anno riferimento')),
                ('periodo_riferimento', models.CharField(blank=True, default='', max_length=16, verbose_name='Periodo riferimento')),
                ('importo_debito', models.DecimalField(blank=True, decimal_places=2, default=None, max_digits=10, null=True, verbose_name='Importo a debito versato')),
                ('importo_credito', models.DecimalField(blank=True, decimal_places=2, default=None, max_digits=10, null=True, verbose_name='Importo a credito compensato')),
                ('ordine', models.PositiveIntegerField(default=0, verbose_name='Ordine riga nel PDF')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('movimento', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='f24_dettagli', to='accounts.movimentoimportpaghe', verbose_name='Movimento F24')),
            ],
            options={
                'verbose_name': 'Dettaglio F24 import paghe',
                'verbose_name_plural': 'Dettagli F24 import paghe',
                'ordering': ['movimento_id', 'ordine', 'codice_tributo'],
            },
        ),
        migrations.AddIndex(
            model_name='movimentoimportpaghef24dettaglio',
            index=models.Index(fields=['movimento', 'sezione'], name='accounts_mov_movimen_f4f4fb_idx'),
        ),
        migrations.AddIndex(
            model_name='movimentoimportpaghef24dettaglio',
            index=models.Index(fields=['codice_tributo', 'anno_riferimento'], name='accounts_mov_codice__7f3248_idx'),
        ),
    ]
