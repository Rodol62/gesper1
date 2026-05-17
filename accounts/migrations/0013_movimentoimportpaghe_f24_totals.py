from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0012_movimentoimportpaghe_importi_lordo_netto'),
    ]

    operations = [
        migrations.AddField(
            model_name='movimentoimportpaghe',
            name='f24_tot_credito',
            field=models.DecimalField(blank=True, decimal_places=2, default=None, max_digits=10, null=True, verbose_name='F24 totale crediti/compensazioni'),
        ),
        migrations.AddField(
            model_name='movimentoimportpaghe',
            name='f24_tot_debito',
            field=models.DecimalField(blank=True, decimal_places=2, default=None, max_digits=10, null=True, verbose_name='F24 totale debiti'),
        ),
        migrations.AddField(
            model_name='movimentoimportpaghe',
            name='f24_saldo_finale',
            field=models.DecimalField(blank=True, decimal_places=2, default=None, max_digits=10, null=True, verbose_name='F24 saldo finale'),
        ),
    ]
