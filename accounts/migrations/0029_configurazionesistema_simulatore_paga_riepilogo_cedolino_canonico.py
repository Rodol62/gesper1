from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0028_alter_profilocandidato_cap'),
    ]

    operations = [
        migrations.AddField(
            model_name='configurazionesistema',
            name='simulatore_paga_riepilogo_cedolino_canonico',
            field=models.BooleanField(
                default=True,
                help_text=(
                    'Se attivo, nella colonna risultati del simulatore mensile viene mostrato un blocco '
                    'allineato al layout canonico busta paga (intestazione, totali ore, rubrica voci, INPS/IRPEF/INAIL, netto). '
                    'Disattivalo per tornare solo alle schede numerate tradizionali.'
                ),
                verbose_name='Simulatore paga: riepilogo come schema cedolino',
            ),
        ),
    ]
