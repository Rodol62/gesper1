import datetime
from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('rapporto_di_lavoro', '0017_configurazionerorganico2026'),
        ('anagrafiche', '__first__'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        # Rimuove la tabella provvisoria basata su querystring
        migrations.DeleteModel(
            name='ConfigurazioneOrganico2026',
        ),
        # Crea la nuova tabella con colonne proprie per ogni ruolo
        migrations.CreateModel(
            name='RuoloOrganico2026',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('ordinamento', models.PositiveSmallIntegerField(default=0)),
                ('nome', models.CharField(blank=True, max_length=120)),
                ('quantita', models.PositiveSmallIntegerField(default=1)),
                ('livello', models.CharField(max_length=20)),
                ('tipo_contratto_id', models.CharField(blank=True, max_length=20)),
                ('tipo_rapporto', models.CharField(
                    choices=[
                        ('indeterminato', 'Indeterminato'),
                        ('determinato', 'Determinato'),
                        ('apprendistato', 'Apprendistato'),
                    ],
                    default='indeterminato',
                    max_length=30,
                )),
                ('data_inizio', models.DateField(default=datetime.date(2026, 1, 1))),
                ('data_fine', models.DateField(default=datetime.date(2026, 12, 31))),
                ('regione', models.CharField(default='sicilia', max_length=80)),
                ('eta', models.PositiveSmallIntegerField(blank=True, null=True)),
                ('categoria', models.CharField(blank=True, max_length=120, null=True)),
                ('percettore_naspi', models.BooleanField(blank=True, null=True)),
                ('tipo_incentivo', models.CharField(blank=True, max_length=120, null=True)),
                ('anni_anzianita', models.PositiveSmallIntegerField(default=0)),
                ('superminimo', models.DecimalField(decimal_places=2, default=0, max_digits=10)),
                ('indennita_turno', models.DecimalField(decimal_places=2, default=0, max_digits=10)),
                ('premio_risultato_annuo', models.DecimalField(decimal_places=2, default=0, max_digits=10)),
                ('calendario_mensile', models.JSONField(blank=True, default=dict)),
                ('data_modifica', models.DateTimeField(auto_now=True)),
                ('azienda', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='ruoli_organico_2026',
                    to='anagrafiche.azienda',
                )),
                ('modificato_da', models.ForeignKey(
                    blank=True,
                    null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name='ruoli_organico_2026_modificati',
                    to=settings.AUTH_USER_MODEL,
                )),
            ],
            options={
                'verbose_name': 'Ruolo Organico 2026',
                'verbose_name_plural': 'Ruoli Organico 2026',
                'ordering': ['azienda', 'ordinamento', 'id'],
            },
        ),
    ]
