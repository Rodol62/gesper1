# Generated manually for ricevuta acconto contanti in area dipendente

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("documenti", "0015_documento_tipo_ricevuta_pagamento_netto"),
        ("partitario_netti", "0002_rename_note_to_causale"),
    ]

    operations = [
        migrations.AddField(
            model_name="movimentopartitarionettodipendente",
            name="documento_ricevuta_firma",
            field=models.ForeignKey(
                blank=True,
                help_text="PDF generato reso visibile al dipendente in «I miei documenti»; revocabile o rimosso automaticamente se cambiano i dati del pagamento.",
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="movimenti_partitario_ricevuta_firma",
                to="documenti.documento",
                verbose_name="Ricevuta contanti in area dipendente (firma)",
            ),
        ),
    ]
