# Generated manually: F1 orario (retr. paga contr.) — persistenza scatti anz. e superminimo €/h

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("documenti", "0009_cedolino_verifica_remove_lettura_memo"),
    ]

    operations = [
        migrations.AddField(
            model_name="cedolinomotorev4",
            name="scatti_anz_imp",
            field=models.DecimalField(
                "Scatti anz. (€/h)",
                decimal_places=5,
                default=0,
                max_digits=16,
            ),
        ),
        migrations.AddField(
            model_name="cedolinomotorev4",
            name="superminimo_imp",
            field=models.DecimalField(
                "Superminimo (€/h)",
                decimal_places=5,
                default=0,
                max_digits=16,
            ),
        ),
    ]
