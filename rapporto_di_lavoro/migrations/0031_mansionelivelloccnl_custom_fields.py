from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('rapporto_di_lavoro', '0030_mansionelivelloccnl'),
    ]

    operations = [
        migrations.AddField(
            model_name='mansionelivelloccnl',
            name='fonte',
            field=models.CharField(
                choices=[('standard', 'Standard da tabelle CCNL'), ('custom_admin', 'Personalizzazione admin')],
                default='standard',
                max_length=20,
            ),
        ),
        migrations.AddField(
            model_name='mansionelivelloccnl',
            name='note',
            field=models.TextField(blank=True, default=''),
        ),
        migrations.AddField(
            model_name='mansionelivelloccnl',
            name='priorita',
            field=models.PositiveSmallIntegerField(default=50, help_text='Valore più alto = precedenza maggiore'),
        ),
        migrations.AddField(
            model_name='mansionelivelloccnl',
            name='valida_a',
            field=models.DateField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='mansionelivelloccnl',
            name='valida_da',
            field=models.DateField(blank=True, null=True),
        ),
        migrations.AlterModelOptions(
            name='mansionelivelloccnl',
            options={
                'ordering': ['-priorita', 'mansione__ordinamento', 'mansione__nome', 'livello', 'qualifica_tabellare'],
                'verbose_name': 'Mappatura Mansione-Livello CCNL',
                'verbose_name_plural': 'Mappature Mansione-Livello CCNL',
            },
        ),
    ]
