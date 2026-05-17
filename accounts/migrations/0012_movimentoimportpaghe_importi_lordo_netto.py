from django.db import migrations, models


def copy_legacy_importo_to_netto(apps, schema_editor):
    MovimentoImportPaghe = apps.get_model('accounts', 'MovimentoImportPaghe')
    MovimentoImportPaghe.objects.filter(importo_netto__isnull=True).update(importo_netto=models.F('importo'))


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0011_movimentoimportpaghe'),
    ]

    operations = [
        migrations.AddField(
            model_name='movimentoimportpaghe',
            name='importo_lordo',
            field=models.DecimalField(blank=True, decimal_places=2, default=None, max_digits=10, null=True, verbose_name='Importo lordo'),
        ),
        migrations.AddField(
            model_name='movimentoimportpaghe',
            name='importo_netto',
            field=models.DecimalField(blank=True, decimal_places=2, default=None, max_digits=10, null=True, verbose_name='Importo netto'),
        ),
        migrations.AlterField(
            model_name='movimentoimportpaghe',
            name='importo',
            field=models.DecimalField(blank=True, decimal_places=2, default='0.00', max_digits=10, null=True, verbose_name='Importo (legacy)'),
        ),
        migrations.RunPython(copy_legacy_importo_to_netto, migrations.RunPython.noop),
    ]
