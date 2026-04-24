# Generated manually for CedolinoMotoreV4 verifica + drop LetturaBustaPagaMemorizzata

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("documenti", "0008_cedolino_motore_v4_estrazione"),
    ]

    operations = [
        migrations.DeleteModel(
            name="LetturaBustaPagaMemorizzata",
        ),
        migrations.AddField(
            model_name="cedolinomotorev4",
            name="estrazione_motore",
            field=models.CharField(
                blank=True,
                default="posizionale_v4",
                max_length=32,
                verbose_name="Motore estrazione",
            ),
        ),
        migrations.AddField(
            model_name="cedolinomotorev4",
            name="pdf_bytes_sha256",
            field=models.CharField(
                blank=True,
                db_index=True,
                default="",
                max_length=64,
                verbose_name="SHA-256 PDF (ultima estrazione)",
            ),
        ),
        migrations.AddField(
            model_name="cedolinomotorev4",
            name="verifica_il",
            field=models.DateTimeField(
                blank=True, null=True, verbose_name="Verifica eseguita il"
            ),
        ),
        migrations.AddField(
            model_name="cedolinomotorev4",
            name="verifica_n_checks_formula_ko",
            field=models.PositiveSmallIntegerField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="cedolinomotorev4",
            name="verifica_n_diff",
            field=models.PositiveSmallIntegerField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="cedolinomotorev4",
            name="verifica_stato",
            field=models.CharField(
                choices=[
                    ("pending", "Da verificare"),
                    ("ok", "Verificato OK"),
                    ("divergenze", "Divergenze"),
                    ("senza_report", "PDF non letto"),
                    ("errore", "Errore ricalcolo da DB"),
                ],
                default="pending",
                max_length=20,
                verbose_name="Stato verifica vs PDF",
            ),
        ),
    ]
