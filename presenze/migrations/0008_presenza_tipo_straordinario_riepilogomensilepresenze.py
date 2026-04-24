from django.conf import settings
from django.db import migrations, models
import django.core.validators
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('presenze', '0007_remove_turno_lavorativo'),
        ('anagrafiche', '__first__'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        # 1. Nuovo campo tipo_straordinario su Presenza
        migrations.AddField(
            model_name='presenza',
            name='tipo_straordinario',
            field=models.CharField(
                blank=True,
                choices=[
                    ('diurno', 'Straordinario diurno'),
                    ('notturno', 'Straordinario notturno (dopo 22:00)'),
                    ('festivo', 'Straordinario festivo/domenicale'),
                    ('nott_fest', 'Straordinario notturno festivo'),
                ],
                max_length=10,
                null=True,
                verbose_name='Tipo straordinario',
            ),
        ),

        # 2. Nuovo modello RiepilogoMensilePresenze
        migrations.CreateModel(
            name='RiepilogoMensilePresenze',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('anno', models.PositiveSmallIntegerField(verbose_name='Anno')),
                ('mese', models.PositiveSmallIntegerField(
                    validators=[django.core.validators.MinValueValidator(1)],
                    verbose_name='Mese',
                )),
                ('stato', models.CharField(
                    choices=[
                        ('bozza', 'Bozza (generata automaticamente)'),
                        ('revisione', 'In revisione HR'),
                        ('approvata', 'Approvata'),
                        ('elaborata', 'Elaborata nel cedolino'),
                    ],
                    default='bozza',
                    max_length=12,
                    verbose_name='Stato',
                )),
                ('ore_ordinarie', models.DecimalField(decimal_places=2, default=0, max_digits=6, verbose_name='Ore ordinarie lavorate')),
                ('ore_domenicali', models.DecimalField(decimal_places=2, default=0, max_digits=6, verbose_name='Ore domenicali (within orario contrattuale)')),
                ('ore_festivi', models.DecimalField(decimal_places=2, default=0, max_digits=6, verbose_name='Ore festività nazionali lavorate (within orario)')),
                ('ore_straord_diurno', models.DecimalField(decimal_places=2, default=0, max_digits=6, verbose_name='Ore straordinario diurno')),
                ('ore_straord_notturno', models.DecimalField(decimal_places=2, default=0, max_digits=6, verbose_name='Ore straordinario notturno')),
                ('ore_straord_festivo', models.DecimalField(decimal_places=2, default=0, max_digits=6, verbose_name='Ore straordinario festivo/domenicale')),
                ('ore_straord_nott_fest', models.DecimalField(decimal_places=2, default=0, max_digits=6, verbose_name='Ore straordinario notturno festivo')),
                ('giorni_ferie_godute', models.DecimalField(decimal_places=2, default=0, max_digits=5, verbose_name='Giorni ferie godute')),
                ('ore_permessi_goduti', models.DecimalField(decimal_places=2, default=0, max_digits=6, verbose_name='Ore permessi goduti (ROL)')),
                ('giorni_malattia', models.PositiveSmallIntegerField(default=0, verbose_name='Giorni malattia')),
                ('giorni_assenza_ingiust', models.PositiveSmallIntegerField(default=0, verbose_name='Giorni assenza ingiustificata')),
                ('giorni_cig', models.PositiveSmallIntegerField(default=0, verbose_name='Giorni CIG / sospensione')),
                ('data_generazione', models.DateTimeField(auto_now_add=True)),
                ('data_modifica', models.DateTimeField(auto_now=True)),
                ('note', models.TextField(blank=True, verbose_name='Note')),
                ('dipendente', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='riepiloghi_presenze',
                    to='anagrafiche.dipendente',
                    verbose_name='Dipendente',
                )),
                ('azienda', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    to='anagrafiche.azienda',
                    verbose_name='Azienda',
                )),
                ('generata_da', models.ForeignKey(
                    blank=True,
                    null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name='riepiloghi_generati',
                    to=settings.AUTH_USER_MODEL,
                    verbose_name='Generata da',
                )),
                ('approvata_da', models.ForeignKey(
                    blank=True,
                    null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name='riepiloghi_approvati',
                    to=settings.AUTH_USER_MODEL,
                    verbose_name='Approvata da',
                )),
            ],
            options={
                'verbose_name': 'Riepilogo mensile presenze',
                'verbose_name_plural': 'Riepiloghi mensili presenze',
                'ordering': ['-anno', '-mese', 'dipendente'],
                'unique_together': {('dipendente', 'anno', 'mese')},
            },
        ),
    ]
