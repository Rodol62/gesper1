from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0006_configurazionesistema_email_test_log'),
    ]

    operations = [
        migrations.AddField(
            model_name='profilocandidato',
            name='paga_giornaliera_attesa',
            field=models.DecimalField(
                blank=True,
                decimal_places=2,
                help_text=(
                    "Indicare la paga giornaliera netta che si desidera percepire. "
                    "Verrà usata per confrontare l'offerta contrattuale con le proprie aspettative."
                ),
                max_digits=7,
                null=True,
                verbose_name='Paga giornaliera netta attesa (€)',
            ),
        ),
    ]
