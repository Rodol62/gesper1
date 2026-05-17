from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('documenti', '0003_alter_documento_options_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='documento',
            name='visualizzato_da_azienda',
            field=models.BooleanField(
                default=False,
                verbose_name="Visualizzato dall'azienda",
                help_text="Se True il documento caricato dal dipendente è stato acquisito dall'azienda e non può più essere eliminato dal dipendente.",
            ),
        ),
    ]
