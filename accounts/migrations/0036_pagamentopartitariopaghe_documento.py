# Generated manually — collegamento ricevuta PDF al pagamento partitario

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("documenti", "0015_documento_tipo_pagamento_dipendente"),
        ("accounts", "0035_pagamentopartitariopaghe"),
    ]

    operations = [
        migrations.AddField(
            model_name="pagamentopartitariopaghe",
            name="documento",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="pagamento_partitario_collegato",
                to="documenti.documento",
                verbose_name="Documento ricevuta (opz.)",
            ),
        ),
    ]
