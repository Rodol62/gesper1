from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('rapporto_di_lavoro', '0020_tipocontratto_fipe_choices'),
    ]

    operations = [
        migrations.DeleteModel(
            name='VariazioneContratto',
        ),
    ]
