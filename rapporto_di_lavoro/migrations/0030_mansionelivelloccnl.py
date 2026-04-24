from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('rapporto_di_lavoro', '0029_mansione_modulo_contratto_assunzione'),
    ]

    operations = [
        migrations.CreateModel(
            name='MansioneLivelloCCNL',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('livello', models.CharField(max_length=20)),
                ('qualifica_tabellare', models.CharField(blank=True, default='', max_length=120)),
                ('ccnl', models.CharField(blank=True, default='', max_length=150)),
                ('versione', models.CharField(blank=True, default='', max_length=50)),
                ('sezione', models.CharField(blank=True, default='', max_length=50)),
                ('attivo', models.BooleanField(default=True)),
                ('data_creazione', models.DateTimeField(auto_now_add=True)),
                ('data_modifica', models.DateTimeField(auto_now=True)),
                ('mansione', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='mappature_ccnl', to='rapporto_di_lavoro.mansione')),
            ],
            options={
                'verbose_name': 'Mappatura Mansione-Livello CCNL',
                'verbose_name_plural': 'Mappature Mansione-Livello CCNL',
                'ordering': ['mansione__ordinamento', 'mansione__nome', 'livello', 'qualifica_tabellare'],
                'unique_together': {('mansione', 'livello', 'ccnl', 'versione', 'sezione')},
            },
        ),
    ]
