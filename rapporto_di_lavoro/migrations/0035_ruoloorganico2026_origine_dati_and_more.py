from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('rapporto_di_lavoro', '0034_alter_parametromaggiorazione_tipo_maggiorazione'),
    ]

    operations = [
        migrations.AddField(
            model_name='ruoloorganico2026',
            name='nominativi_riferimento',
            field=models.TextField(blank=True, default=''),
        ),
        migrations.AddField(
            model_name='ruoloorganico2026',
            name='origine_dati',
            field=models.CharField(
                choices=[
                    ('manuale', 'Manuale'),
                    ('auto_profilo', 'Autocompilato da profili'),
                    ('misto', 'Misto (auto + modifiche manuali)'),
                ],
                default='manuale',
                max_length=20,
            ),
        ),
        migrations.AddField(
            model_name='ruoloorganico2026',
            name='soggetti_riferimento',
            field=models.JSONField(blank=True, default=list),
        ),
    ]
