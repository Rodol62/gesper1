# Generated manually for PianoAllocazioneBonificiQuad

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0033_rename_regstudio_indexes_short_names'),
        ('anagrafiche', '0001_initial'),
    ]

    operations = [
        migrations.CreateModel(
            name='PianoAllocazioneBonificiQuad',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('righe', models.JSONField(blank=True, default=list, help_text='Lista di oggetti {documento_id, bonifico_id, quota (stringa decimale)}.', verbose_name='Righe allocazione')),
                ('aggiornato_il', models.DateTimeField(auto_now=True, verbose_name='Aggiornato il')),
                (
                    'aggiornato_da',
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name='piani_allocazione_bonifici_quad',
                        to=settings.AUTH_USER_MODEL,
                        verbose_name='Aggiornato da',
                    ),
                ),
                (
                    'azienda',
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name='piano_allocazione_bonifici_quad',
                        to='anagrafiche.azienda',
                        verbose_name='Azienda',
                    ),
                ),
            ],
            options={
                'verbose_name': 'Piano allocazione bonifici (quadratura)',
                'verbose_name_plural': 'Piani allocazione bonifici (quadratura)',
            },
        ),
    ]
