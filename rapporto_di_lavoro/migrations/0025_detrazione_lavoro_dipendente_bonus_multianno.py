import datetime
from decimal import Decimal

from django.db import migrations, models


def seed_detrazioni_lavoro_dipendente(apps, schema_editor):
    Detrazione = apps.get_model('rapporto_di_lavoro', 'DetrazioneLavoroDipendente')

    rows = []
    for anno in (2024, 2025, 2026):
        rows.extend([
            {
                'anno': anno,
                'reddito_da': 0,
                'reddito_a': 15000,
                'importo_base_annuo': 1955,
                'coefficiente_variabile_annuo': None,
                'reddito_riferimento': None,
                'divisore_fascia': None,
                'data_validita_da': datetime.date(anno, 1, 1),
                'data_validita_a': datetime.date(anno, 12, 31),
                'attivo': True,
            },
            {
                'anno': anno,
                'reddito_da': 15000.01,
                'reddito_a': 28000,
                'importo_base_annuo': 1910,
                'coefficiente_variabile_annuo': 1190,
                'reddito_riferimento': 28000,
                'divisore_fascia': 13000,
                'data_validita_da': datetime.date(anno, 1, 1),
                'data_validita_a': datetime.date(anno, 12, 31),
                'attivo': True,
            },
            {
                'anno': anno,
                'reddito_da': 28000.01,
                'reddito_a': 50000,
                'importo_base_annuo': 0,
                'coefficiente_variabile_annuo': 1910,
                'reddito_riferimento': 50000,
                'divisore_fascia': 22000,
                'data_validita_da': datetime.date(anno, 1, 1),
                'data_validita_a': datetime.date(anno, 12, 31),
                'attivo': True,
            },
            {
                'anno': anno,
                'reddito_da': 50000.01,
                'reddito_a': None,
                'importo_base_annuo': 0,
                'coefficiente_variabile_annuo': None,
                'reddito_riferimento': None,
                'divisore_fascia': None,
                'data_validita_da': datetime.date(anno, 1, 1),
                'data_validita_a': datetime.date(anno, 12, 31),
                'attivo': True,
            },
        ])

    for row in rows:
        Detrazione.objects.update_or_create(
            anno=row['anno'],
            reddito_da=row['reddito_da'],
            data_validita_da=row['data_validita_da'],
            defaults=row,
        )


def unseed_detrazioni_lavoro_dipendente(apps, schema_editor):
    Detrazione = apps.get_model('rapporto_di_lavoro', 'DetrazioneLavoroDipendente')
    Detrazione.objects.filter(anno__in=[2024, 2025, 2026]).delete()


class Migration(migrations.Migration):

    dependencies = [
        ('rapporto_di_lavoro', '0024_simulazione_paga_salvata'),
    ]

    operations = [
        migrations.CreateModel(
            name='DetrazioneLavoroDipendente',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('anno', models.IntegerField(help_text='Anno fiscale di riferimento')),
                ('reddito_da', models.DecimalField(decimal_places=2, max_digits=12)),
                ('reddito_a', models.DecimalField(blank=True, decimal_places=2, max_digits=12, null=True)),
                ('importo_base_annuo', models.DecimalField(decimal_places=2, default=Decimal('0'), max_digits=10)),
                ('coefficiente_variabile_annuo', models.DecimalField(blank=True, decimal_places=2, max_digits=10, null=True)),
                ('reddito_riferimento', models.DecimalField(blank=True, decimal_places=2, max_digits=12, null=True)),
                ('divisore_fascia', models.DecimalField(blank=True, decimal_places=2, max_digits=12, null=True)),
                ('data_validita_da', models.DateField()),
                ('data_validita_a', models.DateField(blank=True, null=True)),
                ('attivo', models.BooleanField(default=True)),
                ('data_creazione', models.DateTimeField(auto_now_add=True)),
            ],
            options={
                'verbose_name': 'Detrazione lavoro dipendente',
                'verbose_name_plural': 'Detrazioni lavoro dipendente',
                'ordering': ['anno', 'reddito_da'],
                'unique_together': {('anno', 'reddito_da', 'data_validita_da')},
            },
        ),
        migrations.AlterField(
            model_name='bonusfiscale',
            name='codice',
            field=models.CharField(help_text='Codice identificativo (es: TI_DL3_20, BONUS_207_24)', max_length=50),
        ),
        migrations.RunPython(seed_detrazioni_lavoro_dipendente, unseed_detrazioni_lavoro_dipendente),
    ]
