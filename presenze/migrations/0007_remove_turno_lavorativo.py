from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('presenze', '0006_terzo_turno'),
    ]

    operations = [
        migrations.DeleteModel(
            name='TurnoLavorativo',
        ),
    ]
