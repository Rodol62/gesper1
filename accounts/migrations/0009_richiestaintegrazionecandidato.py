from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0008_profilocandidato_regione_residenza'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='RichiestaIntegrazioneCandidato',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('titolo', models.CharField(default='Richiesta integrazione profilo', max_length=150, verbose_name='Titolo')),
                ('messaggio', models.TextField(blank=True, help_text='Richiesta dettagliata visibile al candidato nel profilo.', verbose_name='Istruzioni HR')),
                ('ruolo_richiesto', models.CharField(blank=True, help_text='Esempio: Cuoco, Cameriere, Fattorino.', max_length=100, verbose_name='Ruolo/mansione richiesta')),
                ('richiedi_documento_identita', models.BooleanField(default=False, verbose_name='Richiedi documento identità')),
                ('richiedi_codice_fiscale', models.BooleanField(default=False, verbose_name='Richiedi tessera sanitaria / CF')),
                ('richiedi_curriculum', models.BooleanField(default=False, verbose_name='Richiedi curriculum')),
                ('richiedi_mansione', models.BooleanField(default=False, verbose_name='Richiedi mansione aspirata')),
                ('richiedi_disponibilita', models.BooleanField(default=False, verbose_name='Richiedi disponibilità lavorativa')),
                ('stato', models.CharField(choices=[('inviata', 'Inviata al candidato'), ('completata_candidato', 'Completata dal candidato'), ('approvata_hr', 'Approvata da HR')], default='inviata', max_length=30, verbose_name='Stato')),
                ('note_candidato', models.TextField(blank=True, verbose_name='Note del candidato')),
                ('note_hr', models.TextField(blank=True, verbose_name='Note HR finali')),
                ('conferma_candidato', models.BooleanField(default=False, verbose_name='Conferma candidato')),
                ('data_invio', models.DateTimeField(auto_now_add=True, verbose_name='Data invio')),
                ('data_completamento_candidato', models.DateTimeField(blank=True, null=True, verbose_name='Data completamento candidato')),
                ('data_approvazione_hr', models.DateTimeField(blank=True, null=True, verbose_name='Data approvazione HR')),
                ('candidato', models.ForeignKey(limit_choices_to={'ruolo': 'candidato'}, on_delete=django.db.models.deletion.CASCADE, related_name='richieste_integrazione', to=settings.AUTH_USER_MODEL, verbose_name='Candidato')),
                ('richiesta_da', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='richieste_integrazione_inviate', to=settings.AUTH_USER_MODEL, verbose_name='Richiesta da')),
            ],
            options={
                'verbose_name': 'Richiesta integrazione candidato',
                'verbose_name_plural': 'Richieste integrazione candidati',
                'ordering': ['-data_invio'],
            },
        ),
    ]
