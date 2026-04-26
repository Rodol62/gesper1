# Allinea anagrafica e mappatura: ratei 13ª/14ª non imponibili INPS/IRPEF/INAIL
# se solo accantonamento (il simulatore/contratto attivano l'imponibile con i flag dedicati).

from django.db import migrations


def forwards(apps, schema_editor):
    VoceRetributiva = apps.get_model('rapporto_di_lavoro', 'VoceRetributiva')
    MappaturaVoceMotore = apps.get_model('rapporto_di_lavoro', 'MappaturaVoceMotore')
    for cod in ('TREDICESIMA', 'QUATTORDICESIMA'):
        VoceRetributiva.objects.filter(codice=cod).update(
            imponibile_inps=False,
            imponibile_inail=False,
            imponibile_irpef=False,
        )
        MappaturaVoceMotore.objects.filter(codice_voce=cod).update(
            imponibile_inps=False,
            imponibile_inail=False,
            imponibile_irpef=False,
            note_riconciliazione=(
                'Imponibile INPS/IRPEF/INAIL del mese solo se quota 1/12 erogata in cedolino '
                '(flag contratto / simulatore); altrimenti accantonamento.'
            ),
        )


def backwards(apps, schema_editor):
    VoceRetributiva = apps.get_model('rapporto_di_lavoro', 'VoceRetributiva')
    MappaturaVoceMotore = apps.get_model('rapporto_di_lavoro', 'MappaturaVoceMotore')
    for cod in ('TREDICESIMA', 'QUATTORDICESIMA'):
        VoceRetributiva.objects.filter(codice=cod).update(
            imponibile_inps=True,
            imponibile_inail=True,
            imponibile_irpef=True,
        )
        MappaturaVoceMotore.objects.filter(codice_voce=cod).update(
            imponibile_inps=True,
            imponibile_inail=True,
            imponibile_irpef=True,
        )


class Migration(migrations.Migration):

    dependencies = [
        ('rapporto_di_lavoro', '0040_reset_ratei_imponibile_flags_default'),
    ]

    operations = [
        migrations.RunPython(forwards, backwards),
    ]
