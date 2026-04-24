from django.db import migrations, models


class Migration(migrations.Migration):

	dependencies = [
		('rapporto_di_lavoro', '0026_proposta_firma_digitale'),
	]

	operations = [
		migrations.AddField(
			model_name='propostaassunzione',
			name='luogo_firma_candidato',
			field=models.CharField(blank=True, default='Palermo', help_text='Luogo di accettazione/firma del candidato.', max_length=120),
		),
		migrations.AddField(
			model_name='propostaassunzione',
			name='luogo_firma_datore',
			field=models.CharField(blank=True, default='Palermo', help_text='Luogo di firma definitiva del datore di lavoro.', max_length=120),
		),
		migrations.AddField(
			model_name='rapportodilavoro',
			name='data_ora_sottoscrizione',
			field=models.DateTimeField(blank=True, null=True),
		),
		migrations.AddField(
			model_name='rapportodilavoro',
			name='luogo_sottoscrizione',
			field=models.CharField(blank=True, default='Palermo', max_length=120),
		),
	]