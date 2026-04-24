from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("documenti", "0013_cedolinomotorev4_natura_busta"),
    ]

    operations = [
        migrations.AlterField(
            model_name="documento",
            name="tipo",
            field=models.CharField(
                choices=[
                    ("contratto", "Contratto di lavoro"),
                    ("privacy", "Autorizzazione privacy"),
                    ("busta_paga", "Busta paga"),
                    ("certificato", "CUD / Certificato fiscale"),
                    ("unilav", "UniLav"),
                    ("riepilogo_mensile", "Riepilogo mensile"),
                    ("carichi_famiglia", "Comunicazione carichi di famiglia"),
                    ("documento_identita", "Documento di identità"),
                    ("permesso_soggiorno", "Permesso di soggiorno"),
                    ("codice_fiscale_doc", "Tessera sanitaria / Codice fiscale"),
                    ("curriculum", "Curriculum vitae"),
                    ("attestato", "Attestato professionale"),
                    ("abilitazione", "Abilitazione tecnica"),
                    ("titolo_studio", "Titolo di studio"),
                    ("certificazione", "Certificazione / Titolo di studio"),
                    ("altro", "F24"),
                ],
                max_length=30,
                verbose_name="Tipo documento",
            ),
        ),
    ]

