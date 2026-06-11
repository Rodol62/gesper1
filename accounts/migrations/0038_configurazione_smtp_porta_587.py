"""
Allinea la porta SMTP a 587 (STARTTLS) quando era 465.

Dal server VPS la 465 verso Aruba (SSL implicito) è spesso bloccata o in timeout;
587 + TLS è il profilo corretto per smtps.aruba.it.
"""
from django.db import migrations


def porta_465_a_587(apps, schema_editor):
    ConfigurazioneSistema = apps.get_model('accounts', 'ConfigurazioneSistema')
    ConfigurazioneSistema.objects.filter(smtp_port=465).update(
        smtp_port=587,
        smtp_use_tls=True,
    )


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0037_user_geo_presenze_consenso_evento'),
    ]

    operations = [
        migrations.RunPython(porta_465_a_587, migrations.RunPython.noop),
    ]
