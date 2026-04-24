from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0023_alter_configurazione_url_pubblica_base_help'),
    ]

    operations = [
        migrations.AddField(
            model_name='user',
            name='email_stepup_login',
            field=models.BooleanField(
                default=False,
                help_text='Se attivo, dopo username/password viene inviato un codice monouso via e-mail (SMTP di sistema). Richiede e-mail valorizzata sul profilo.',
                verbose_name='Verifica e-mail al login',
            ),
        ),
    ]
