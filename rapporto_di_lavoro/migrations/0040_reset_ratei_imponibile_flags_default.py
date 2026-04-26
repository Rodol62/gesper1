# I ratei 13ª/14ª entrano in imponibile INPS solo se esplicitamente attivati su proposta/contratto
# (revoca il popolamento automatico della 0039).

from django.db import migrations


def forwards_reset_flags(apps, schema_editor):
    RapportoDiLavoro = apps.get_model('rapporto_di_lavoro', 'RapportoDiLavoro')
    PropostaAssunzione = apps.get_model('rapporto_di_lavoro', 'PropostaAssunzione')
    RapportoDiLavoro.objects.update(
        tredicesima_rateo_mensile_in_imponibile=False,
        quattordicesima_rateo_mensile_in_imponibile=False,
    )
    PropostaAssunzione.objects.update(
        tredicesima_rateo_mensile_in_imponibile=False,
        quattordicesima_rateo_mensile_in_imponibile=False,
    )


def backwards_noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('rapporto_di_lavoro', '0039_ratei_mensili_imponibile_flags'),
    ]

    operations = [
        migrations.RunPython(forwards_reset_flags, backwards_noop),
    ]
