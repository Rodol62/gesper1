from decimal import Decimal
from django.db import migrations, models


def backfill_tabella_retributiva(apps, schema_editor):
    ParametroCCNLTurismo = apps.get_model('rapporto_di_lavoro', 'ParametroCCNLTurismo')
    for rec in ParametroCCNLTurismo.objects.all():
        rec.minimo_tabellare = rec.paga_base_mensile or Decimal('0.00')
        rec.totale_tabellare = rec.importo_lordo_mensile or Decimal('0.00')
        if not rec.fonte_tabella:
            rec.fonte_tabella = 'legacy'
        rec.save(update_fields=['minimo_tabellare', 'totale_tabellare', 'fonte_tabella'])


class Migration(migrations.Migration):

    dependencies = [
        ('rapporto_di_lavoro', '0008_ccnl_decontribuzioneparametro_fringebenefitsoglia_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='parametroccnlturismo',
            name='data_rilevazione_tabella',
            field=models.DateField(blank=True, help_text='Data della rilevazione/accordo tabellare', null=True),
        ),
        migrations.AddField(
            model_name='parametroccnlturismo',
            name='fonte_tabella',
            field=models.CharField(blank=True, default='', help_text='Fonte dati tabellari (es. tabelle retributive.pdf)', max_length=120),
        ),
        migrations.AddField(
            model_name='parametroccnlturismo',
            name='livello_ordinamento',
            field=models.IntegerField(blank=True, help_text='Ordine di visualizzazione livello in tabella retributiva', null=True),
        ),
        migrations.AddField(
            model_name='parametroccnlturismo',
            name='minimo_tabellare',
            field=models.DecimalField(decimal_places=2, default=Decimal('0.00'), help_text='Valore minimo tabellare da tabella retributiva', max_digits=10),
        ),
        migrations.AddField(
            model_name='parametroccnlturismo',
            name='totale_tabellare',
            field=models.DecimalField(decimal_places=2, default=Decimal('0.00'), help_text='Totale tabellare lordo (minimo + contingenza + eventuali voci)', max_digits=10),
        ),
        migrations.RunPython(backfill_tabella_retributiva, migrations.RunPython.noop),
    ]
