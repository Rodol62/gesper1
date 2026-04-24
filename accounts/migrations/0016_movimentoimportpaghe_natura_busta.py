from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0015_rename_accounts_mov_azienda__c45c2c_idx_accounts_mo_azienda_d7359e_idx_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='movimentoimportpaghe',
            name='natura_busta',
            field=models.CharField(
                choices=[
                    ('ORDINARIA', 'Ordinaria'),
                    ('TREDICESIMA', 'Tredicesima'),
                    ('QUATTORDICESIMA', 'Quattordicesima'),
                ],
                default='ORDINARIA',
                help_text='Valido per tipo=BUSTA; per F24 resta ORDINARIA',
                max_length=20,
                verbose_name='Natura busta',
            ),
        ),
        migrations.AddIndex(
            model_name='movimentoimportpaghe',
            index=models.Index(
                fields=['azienda', 'dipendente', 'anno', 'mese', 'natura_busta'],
                name='accounts_mo_azienda_69f00f_idx',
            ),
        ),
    ]
