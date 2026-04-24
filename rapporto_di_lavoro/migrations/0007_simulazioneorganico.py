from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('anagrafiche', '0002_alter_dipendente_utente'),
        ('rapporto_di_lavoro', '0006_regolanormativaccnl'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='SimulazioneOrganico',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('mese_riferimento', models.CharField(max_length=7)),
                ('parametri_json', models.JSONField(blank=True, default=dict)),
                ('risultato_json', models.JSONField(blank=True, default=dict)),
                ('querystring', models.TextField(blank=True)),
                ('data_creazione', models.DateTimeField(auto_now_add=True)),
                ('azienda', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='simulazioni_organico', to='anagrafiche.azienda')),
                ('utente', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='simulazioni_organico', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'verbose_name': 'Simulazione organico',
                'verbose_name_plural': 'Simulazioni organico',
                'ordering': ['-data_creazione'],
            },
        ),
    ]
