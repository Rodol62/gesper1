# Generated manually for LetturaBustaPagaMemorizzata

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("documenti", "0006_alter_documento_tipo"),
    ]

    operations = [
        migrations.CreateModel(
            name="LetturaBustaPagaMemorizzata",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                (
                    "netto_memo",
                    models.DecimalField(
                        blank=True,
                        decimal_places=2,
                        max_digits=14,
                        null=True,
                        verbose_name="Netto memorizzato",
                    ),
                ),
                (
                    "lordo_memo",
                    models.DecimalField(
                        blank=True,
                        decimal_places=2,
                        max_digits=14,
                        null=True,
                        verbose_name="Lordo memorizzato",
                    ),
                ),
                (
                    "n_voci_memo",
                    models.PositiveIntegerField(
                        default=0, verbose_name="Numero righe voce"
                    ),
                ),
                (
                    "periodo_mese",
                    models.PositiveSmallIntegerField(blank=True, null=True),
                ),
                (
                    "periodo_anno",
                    models.PositiveSmallIntegerField(blank=True, null=True),
                ),
                (
                    "cf_memo",
                    models.CharField(
                        blank=True, max_length=16, verbose_name="CF (snapshot)"
                    ),
                ),
                (
                    "cognome_nome_memo",
                    models.CharField(
                        blank=True,
                        max_length=200,
                        verbose_name="Cognome e nome (snapshot)",
                    ),
                ),
                (
                    "formule_snapshot",
                    models.JSONField(
                        blank=True,
                        default=dict,
                        verbose_name="Formule cedolino (snapshot)",
                    ),
                ),
                ("aggiornata_il", models.DateTimeField(auto_now=True)),
                (
                    "documento",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="lettura_busta_memorizzata",
                        to="documenti.documento",
                        verbose_name="Documento busta",
                    ),
                ),
            ],
            options={
                "verbose_name": "Lettura busta paga memorizzata",
                "verbose_name_plural": "Letture buste paga memorizzate",
            },
        ),
    ]
