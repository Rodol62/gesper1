from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('rapporto_di_lavoro', '0016_parametrovoceretributiva'),
        ('anagrafiche', '__first__'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='ConfigurazioneOrganico2026',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('ruoli_querystring', models.TextField(blank=True, help_text='Querystring GET con tutti i parametri dei ruoli configurati.')),
                ('data_modifica', models.DateTimeField(auto_now=True)),
                ('azienda', models.OneToOneField(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='configurazione_organico_2026',
                    to='anagrafiche.azienda',
                )),
                ('modificato_da', models.ForeignKey(
                    blank=True,
                    null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name='configurazioni_organico_2026',
                    to=settings.AUTH_USER_MODEL,
                )),
            ],
            options={
                'verbose_name': 'Configurazione Organico 2026',
                'verbose_name_plural': 'Configurazioni Organico 2026',
            },
        ),
    ]
