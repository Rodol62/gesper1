"""
Disattiva l'invio SMS in configurazione: il canale non è più supportato.

Così anche istanze con codice precedente non tentano più l'SMS in registrazione candidato
quando ``sms_abilitato`` era stato attivato per errore.
"""
from django.db import migrations


def disable_sms_config(apps, schema_editor):
    ConfigurazioneSistema = apps.get_model('accounts', 'ConfigurazioneSistema')
    ConfigurazioneSistema.objects.filter(sms_abilitato=True).update(sms_abilitato=False)


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0025_alter_user_totp_labels'),
    ]

    operations = [
        migrations.RunPython(disable_sms_config, migrations.RunPython.noop),
    ]
