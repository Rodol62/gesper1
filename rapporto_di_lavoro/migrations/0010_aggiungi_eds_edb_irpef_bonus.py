# Generated migration for adding EDS, EDB, IRPEF and Bonus Fiscali

from django.db import migrations, models
import django.db.models.deletion
from decimal import Decimal


class Migration(migrations.Migration):

    dependencies = [
        ('rapporto_di_lavoro', '0009_parametroccnlturismo_struttura_tabella_retributiva'),
    ]

    operations = [
        # 1. Aggiungi EDS e EDB a ParametroCCNLTurismo
        migrations.AddField(
            model_name='parametroccnlturismo',
            name='elemento_distinto_sanita',
            field=models.DecimalField(
                decimal_places=5,
                default=Decimal('0.09302'),
                help_text='EDS - Elemento Distinto Sanità (€/ora o mensile)',
                max_digits=10
            ),
        ),
        migrations.AddField(
            model_name='parametroccnlturismo',
            name='elemento_distinto_bilateralita',
            field=models.DecimalField(
                decimal_places=5,
                default=Decimal('0.05386'),
                help_text='EDB - Elemento Distinto Bilateralità (€/ora o mensile)',
                max_digits=10
            ),
        ),
        migrations.AddField(
            model_name='parametroccnlturismo',
            name='eds_edb_su_base_oraria',
            field=models.BooleanField(
                default=True,
                help_text='True se EDS/EDB sono valori orari, False se mensili'
            ),
        ),
        
        # 2. Crea modello ScaglioneIRPEF per sistema fiscale
        migrations.CreateModel(
            name='ScaglioneIRPEF',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('anno', models.IntegerField(help_text='Anno fiscale di riferimento')),
                ('scaglione_numero', models.IntegerField(help_text='Numero progressivo scaglione (1, 2, 3, 4)')),
                ('reddito_da', models.DecimalField(decimal_places=2, help_text='Limite inferiore reddito imponibile', max_digits=12)),
                ('reddito_a', models.DecimalField(blank=True, decimal_places=2, help_text='Limite superiore reddito imponibile (null = infinito)', max_digits=12, null=True)),
                ('aliquota', models.DecimalField(decimal_places=2, help_text='Aliquota % (es: 23.00 per 23%)', max_digits=5)),
                ('detrazione_base_annua', models.DecimalField(decimal_places=2, default=Decimal('0'), help_text='Detrazione base annua per lavoro dipendente', max_digits=10)),
                ('data_validita_da', models.DateField()),
                ('data_validita_a', models.DateField(blank=True, null=True)),
                ('attivo', models.BooleanField(default=True)),
                ('data_creazione', models.DateTimeField(auto_now_add=True)),
            ],
            options={
                'verbose_name': 'Scaglione IRPEF',
                'verbose_name_plural': 'Scaglioni IRPEF',
                'ordering': ['anno', 'scaglione_numero'],
                'unique_together': {('anno', 'scaglione_numero')},
            },
        ),
        
        # 3. Crea modello AddizionaleRegionale
        migrations.CreateModel(
            name='AddizionaleRegionale',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('regione', models.CharField(max_length=100, help_text='Nome regione (es: Lombardia, Lazio)')),
                ('anno', models.IntegerField()),
                ('aliquota', models.DecimalField(decimal_places=3, help_text='Aliquota % addizionale regionale', max_digits=5)),
                ('soglia_esenzione', models.DecimalField(blank=True, decimal_places=2, help_text='Soglia reddito sotto cui non si applica', max_digits=12, null=True)),
                ('data_validita_da', models.DateField()),
                ('data_validita_a', models.DateField(blank=True, null=True)),
                ('attivo', models.BooleanField(default=True)),
                ('data_creazione', models.DateTimeField(auto_now_add=True)),
            ],
            options={
                'verbose_name': 'Addizionale regionale IRPEF',
                'verbose_name_plural': 'Addizionali regionali IRPEF',
                'ordering': ['anno', 'regione'],
                'unique_together': {('anno', 'regione')},
            },
        ),
        
        # 4. Crea modello AddizionaleComunale
        migrations.CreateModel(
            name='AddizionaleComunale',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('comune', models.CharField(max_length=100, help_text='Nome comune')),
                ('provincia', models.CharField(max_length=2, help_text='Sigla provincia')),
                ('anno', models.IntegerField()),
                ('aliquota', models.DecimalField(decimal_places=3, help_text='Aliquota % addizionale comunale', max_digits=5)),
                ('soglia_esenzione', models.DecimalField(blank=True, decimal_places=2, help_text='Soglia reddito sotto cui non si applica', max_digits=12, null=True)),
                ('data_validita_da', models.DateField()),
                ('data_validita_a', models.DateField(blank=True, null=True)),
                ('attivo', models.BooleanField(default=True)),
                ('data_creazione', models.DateTimeField(auto_now_add=True)),
            ],
            options={
                'verbose_name': 'Addizionale comunale IRPEF',
                'verbose_name_plural': 'Addizionali comunali IRPEF',
                'ordering': ['anno', 'provincia', 'comune'],
                'unique_together': {('anno', 'comune', 'provincia')},
            },
        ),
        
        # 5. Crea modello BonusFiscale per trattamenti integrativi e altri bonus
        migrations.CreateModel(
            name='BonusFiscale',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('codice', models.CharField(max_length=50, unique=True, help_text='Codice identificativo (es: TI_DL3_20, BONUS_207_24)')),
                ('nome', models.CharField(max_length=200, help_text='Nome completo bonus')),
                ('tipo', models.CharField(
                    choices=[
                        ('trattamento_integrativo', 'Trattamento Integrativo DL 3/2020'),
                        ('bonus_renzi', 'Bonus Renzi/Cuneo fiscale'),
                        ('bonus_200_2022', 'Bonus 200€ (L.197/2022)'),
                        ('bonus_150_2022', 'Bonus 150€ (DL 176/2022)'),
                        ('bonus_art1_l207', 'Bonus Art.1 L.207/2024'),
                        ('altro', 'Altro bonus fiscale'),
                    ],
                    max_length=50
                )),
                ('anno', models.IntegerField()),
                ('importo_mensile', models.DecimalField(blank=True, decimal_places=2, help_text='Importo mensile fisso (se applicabile)', max_digits=10, null=True)),
                ('importo_annuale', models.DecimalField(blank=True, decimal_places=2, help_text='Importo annuale fisso (se applicabile)', max_digits=10, null=True)),
                ('soglia_reddito_min', models.DecimalField(blank=True, decimal_places=2, help_text='Soglia minima reddito per applicabilità', max_digits=12, null=True)),
                ('soglia_reddito_max', models.DecimalField(blank=True, decimal_places=2, help_text='Soglia massima reddito per applicabilità', max_digits=12, null=True)),
                ('formula_calcolo', models.TextField(blank=True, help_text='Formula Python per calcolo dinamico (opzionale)')),
                ('contribuisce_imponibile', models.BooleanField(default=False, help_text='Se True, concorre a formare imponibile contributivo')),
                ('contribuisce_irpef', models.BooleanField(default=False, help_text='Se True, concorre a formare imponibile fiscale')),
                ('data_validita_da', models.DateField()),
                ('data_validita_a', models.DateField(blank=True, null=True)),
                ('descrizione', models.TextField(blank=True)),
                ('attivo', models.BooleanField(default=True)),
                ('data_creazione', models.DateTimeField(auto_now_add=True)),
            ],
            options={
                'verbose_name': 'Bonus fiscale',
                'verbose_name_plural': 'Bonus fiscali',
                'ordering': ['-anno', 'tipo', 'nome'],
                'unique_together': {('codice', 'anno')},
            },
        ),
    ]
