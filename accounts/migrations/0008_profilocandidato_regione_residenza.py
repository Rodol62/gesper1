from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0007_profilocandidato_paga_giornaliera_attesa'),
    ]

    operations = [
        migrations.AddField(
            model_name='profilocandidato',
            name='regione_residenza',
            field=models.CharField(
                blank=True,
                help_text='Es. Sicilia, Lombardia — usato per calcolo addizionale regionale IRPEF.',
                max_length=50,
                verbose_name='Regione di residenza',
            ),
        ),
    ]
