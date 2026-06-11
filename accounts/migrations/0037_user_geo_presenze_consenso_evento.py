# Generated manually for consenso geolocalizzazione presenze

from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


def _colonne_user_geo_presenti(schema_editor) -> set[str]:
    with schema_editor.connection.cursor() as cursor:
        cursor.execute('PRAGMA table_info(accounts_user)')
        return {row[1] for row in cursor.fetchall()}


def applica_schema_geo_presenze(apps, schema_editor):
    """Idempotente: su produzione le colonne/tabella possono esistere già senza riga in django_migrations."""
    connection = schema_editor.connection
    colonne = _colonne_user_geo_presenti(schema_editor)

    alterazioni = []
    if 'geo_presenze_consenso' not in colonne:
        alterazioni.append(
            'ALTER TABLE accounts_user ADD COLUMN geo_presenze_consenso bool NOT NULL DEFAULT 0'
        )
    if 'geo_presenze_consenso_at' not in colonne:
        alterazioni.append(
            'ALTER TABLE accounts_user ADD COLUMN geo_presenze_consenso_at datetime NULL'
        )
    if 'geo_presenze_consenso_revocato_at' not in colonne:
        alterazioni.append(
            'ALTER TABLE accounts_user ADD COLUMN geo_presenze_consenso_revocato_at datetime NULL'
        )
    if 'geo_presenze_consenso_version' not in colonne:
        alterazioni.append(
            "ALTER TABLE accounts_user ADD COLUMN geo_presenze_consenso_version varchar(32) NOT NULL DEFAULT ''"
        )

    with connection.cursor() as cursor:
        for sql in alterazioni:
            cursor.execute(sql)

    tabelle = set(connection.introspection.table_names())
    if 'accounts_consensogeolocalizzazioneevento' not in tabelle:
        modello = apps.get_model('accounts', 'ConsensoGeolocalizzazioneEvento')
        schema_editor.create_model(modello)


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0036_pagamentopartitariopaghe_documento'),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[
                migrations.RunPython(applica_schema_geo_presenze, migrations.RunPython.noop),
            ],
            state_operations=[
                migrations.AddField(
                    model_name='user',
                    name='geo_presenze_consenso',
                    field=models.BooleanField(
                        default=False,
                        help_text='Consenso esplicito all’uso del GPS solo per timbratura entrata/uscita.',
                        verbose_name='Consenso geolocalizzazione presenze',
                    ),
                ),
                migrations.AddField(
                    model_name='user',
                    name='geo_presenze_consenso_at',
                    field=models.DateTimeField(
                        blank=True,
                        null=True,
                        verbose_name='Data/ora consenso geo presenze',
                    ),
                ),
                migrations.AddField(
                    model_name='user',
                    name='geo_presenze_consenso_revocato_at',
                    field=models.DateTimeField(
                        blank=True,
                        null=True,
                        verbose_name='Data/ora revoca consenso geo presenze',
                    ),
                ),
                migrations.AddField(
                    model_name='user',
                    name='geo_presenze_consenso_version',
                    field=models.CharField(
                        blank=True,
                        default='',
                        max_length=32,
                        verbose_name='Versione testo informativa geo',
                    ),
                ),
                migrations.CreateModel(
                    name='ConsensoGeolocalizzazioneEvento',
                    fields=[
                        ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                        ('azione', models.CharField(choices=[('concesso', 'Consenso concesso'), ('revocato', 'Consenso revocato')], max_length=16)),
                        ('fonte', models.CharField(choices=[('registrazione', 'Registrazione candidato'), ('profilo_pwa', 'Profilo app PWA'), ('profilo_web', 'Portale web'), ('sistema', 'Sistema')], default='profilo_pwa', max_length=24)),
                        ('versione_testo', models.CharField(blank=True, default='', max_length=32)),
                        ('indirizzo_ip', models.GenericIPAddressField(blank=True, null=True)),
                        ('user_agent', models.CharField(blank=True, default='', max_length=500)),
                        ('note', models.CharField(blank=True, default='', max_length=255)),
                        ('created_at', models.DateTimeField(auto_now_add=True, verbose_name='Data evento')),
                        ('user', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='eventi_consenso_geo_presenze', to=settings.AUTH_USER_MODEL, verbose_name='Utente')),
                    ],
                    options={
                        'verbose_name': 'Evento consenso geolocalizzazione presenze',
                        'verbose_name_plural': 'Eventi consenso geolocalizzazione presenze',
                        'ordering': ['-created_at'],
                    },
                ),
            ],
        ),
    ]
