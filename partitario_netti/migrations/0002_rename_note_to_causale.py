# Generated manually for rinominare note → causale (partitario pagamenti).

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("partitario_netti", "0001_initial_movimento_partitario"),
    ]

    operations = [
        migrations.RenameField(
            model_name="movimentopartitarionettodipendente",
            old_name="note",
            new_name="causale",
        ),
    ]
