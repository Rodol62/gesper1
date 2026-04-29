# Nomi indice compatibili con models.E034 (max 30 caratteri su Meta.indexes).

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0032_import_estratto_conto_studio'),
    ]

    operations = [
        migrations.RenameIndex(
            model_name='movimentoregistrostudioconsulente',
            new_name='acc_mrsc_az_tpdt_idx',
            old_name='accounts_mo_azienda_tipo_data_idx',
        ),
        migrations.RenameIndex(
            model_name='rigaestrattocontostudio',
            new_name='acc_rgest_im_es_idx',
            old_name='accounts_ri_importa_0f90b8_idx',
        ),
    ]
