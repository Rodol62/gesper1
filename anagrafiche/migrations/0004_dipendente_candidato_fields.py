from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('anagrafiche', '0003_azienda_configurazione_contrattuale'),
    ]

    operations = [
        migrations.AlterField(
            model_name='dipendente',
            name='codice_fiscale',
            field=models.CharField(
                max_length=16,
                unique=True,
                null=True,
                blank=True,
                verbose_name='Codice Fiscale',
            ),
        ),
        migrations.AlterField(
            model_name='dipendente',
            name='data_nascita',
            field=models.DateField(
                null=True,
                blank=True,
                verbose_name='Data di nascita',
            ),
        ),
        migrations.AlterField(
            model_name='dipendente',
            name='data_assunzione',
            field=models.DateField(
                null=True,
                blank=True,
                verbose_name='Data assunzione',
            ),
        ),
        migrations.AlterField(
            model_name='dipendente',
            name='indirizzo',
            field=models.CharField(
                max_length=255,
                blank=True,
                verbose_name='Indirizzo',
            ),
        ),
        migrations.AlterField(
            model_name='dipendente',
            name='email',
            field=models.EmailField(
                blank=True,
                verbose_name='Email',
            ),
        ),
        migrations.AlterField(
            model_name='dipendente',
            name='stato',
            field=models.CharField(
                max_length=20,
                default='attivo',
                choices=[
                    ('attivo', 'Attivo'),
                    ('cessato', 'Cessato'),
                    ('candidato', 'Candidato'),
                ],
                verbose_name='Stato',
            ),
        ),
    ]
