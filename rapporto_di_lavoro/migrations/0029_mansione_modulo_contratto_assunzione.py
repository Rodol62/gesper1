# Generated manually for Mansione + modulo Contratto di Assunzione

from django.db import migrations, models
import django.db.models.deletion


def seed_mansioni_e_modulo(apps, schema_editor):
	Mansione = apps.get_model('rapporto_di_lavoro', 'Mansione')
	ModuloContrattuale = apps.get_model('rapporto_di_lavoro', 'ModuloContrattuale')
	for nome, ordine in (
		('Pizzaiolo/a', 10),
		('Cuoco/a', 20),
		('Cameriere/a', 30),
		('Fattorino', 40),
		('Lavapiatti', 50),
		('Barman', 60),
		('Responsabile', 70),
		('Amministrativo', 80),
	):
		Mansione.objects.get_or_create(
			nome=nome,
			defaults={'ordinamento': ordine, 'attivo': True},
		)
	ModuloContrattuale.objects.get_or_create(
		nome='Contratto di Assunzione',
		defaults={
			'categoria': 'contratto_assunzione',
			'descrizione': (
				'Proposta di assunzione con passaggio diretto al contratto '
				'(stesso flusso documentale, modulo dedicato).'
			),
			'compilabile_da_dipendente': True,
			'attivo': True,
		},
	)


def noop_reverse(apps, schema_editor):
	pass


class Migration(migrations.Migration):

	dependencies = [
		('rapporto_di_lavoro', '0028_propostaassunzione_mansionario_file_and_more'),
	]

	operations = [
		migrations.CreateModel(
			name='Mansione',
			fields=[
				('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
				('nome', models.CharField(max_length=80, unique=True)),
				('ordinamento', models.PositiveSmallIntegerField(default=0)),
				('attivo', models.BooleanField(default=True)),
			],
			options={
				'verbose_name': 'Mansione',
				'verbose_name_plural': 'Mansioni',
				'ordering': ['ordinamento', 'nome'],
			},
		),
		migrations.AlterField(
			model_name='modulocontrattuale',
			name='categoria',
			field=models.CharField(
				choices=[
					('proposta_assunzione', 'Proposta assunzione'),
					('contratto_assunzione', 'Contratto di assunzione (proposta + contratto diretto)'),
					('integrazione_dati', 'Integrazione dati dipendente'),
					('consenso_privacy', 'Consenso privacy'),
					('allegati_obbligatori', 'Allegati obbligatori'),
					('altro', 'Altro'),
				],
				default='proposta_assunzione',
				max_length=50,
			),
		),
		migrations.AddField(
			model_name='propostaassunzione',
			name='mansione',
			field=models.ForeignKey(
				blank=True,
				help_text='Mansione operativa (es. cuoco, cameriere) indipendente dalla voce tabellare CCNL.',
				null=True,
				on_delete=django.db.models.deletion.SET_NULL,
				related_name='proposte',
				to='rapporto_di_lavoro.mansione',
				verbose_name='Mansione',
			),
		),
		migrations.RunPython(seed_mansioni_e_modulo, noop_reverse),
	]
