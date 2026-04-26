from django.db import migrations, models
import django.db.models.deletion


def purge_old_ruoli(apps, schema_editor):
    RuoloOrganico2026 = apps.get_model('rapporto_di_lavoro', 'RuoloOrganico2026')
    RuoloOrganico2026.objects.all().delete()


class Migration(migrations.Migration):

    dependencies = [
        ('rapporto_di_lavoro', '0035_ruoloorganico2026_origine_dati_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='ruoloorganico2026',
            name='mansione_label',
            field=models.CharField(blank=True, default='', max_length=120),
        ),
        migrations.AddField(
            model_name='ruoloorganico2026',
            name='stato_soggetto',
            field=models.CharField(blank=True, default='', max_length=20),
        ),
        migrations.AddField(
            model_name='ruoloorganico2026',
            name='dipendente',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='ruoli_organico_2026',
                to='anagrafiche.dipendente',
            ),
        ),
        migrations.RunPython(purge_old_ruoli, migrations.RunPython.noop),
    ]
