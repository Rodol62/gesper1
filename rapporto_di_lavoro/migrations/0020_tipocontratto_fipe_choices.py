from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('rapporto_di_lavoro', '0019_calendario_lavoro_mensile'),
    ]

    operations = [
        migrations.AlterField(
            model_name='tipocontratto',
            name='tipo',
            field=models.CharField(
                choices=[
                    ('ind_full',         'Indeterminato full-time'),
                    ('ind_pt_50',        'Indeterminato part-time 50%'),
                    ('ind_pt_60',        'Indeterminato part-time 60%'),
                    ('ind_pt_75',        'Indeterminato part-time 75%'),
                    ('ind_pt_80',        'Indeterminato part-time 80%'),
                    ('ind_pt_83',        'Indeterminato part-time 83%'),
                    ('det_full',         'Determinato full-time'),
                    ('det_pt_50',        'Determinato part-time 50%'),
                    ('det_pt_75',        'Determinato part-time 75%'),
                    ('stag_full',        'Stagionale full-time'),
                    ('stag_pt',          'Stagionale part-time'),
                    ('apprendistato',    'Apprendistato professionalizzante'),
                    ('intermittente',    'Lavoro intermittente / a chiamata'),
                    ('somministrazione', 'Somministrazione'),
                ],
                default='ind_full',
                max_length=20,
                verbose_name='Tipo contratto',
            ),
        ),
    ]
