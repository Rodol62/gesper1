from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('rapporto_di_lavoro', '0041_align_ratei_voce_mappatura_imponibili'),
    ]

    operations = [
        migrations.AlterField(
            model_name='simulazioneorganico',
            name='mese_riferimento',
            field=models.CharField(
                help_text='Es. YYYY-MM (simulatore tabella) oppure etichetta annua (es. 2026-annuale).',
                max_length=32,
            ),
        ),
    ]
