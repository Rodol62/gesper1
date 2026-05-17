from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('anagrafiche', '0013_azienda_sede_legale_strutturata_amministratore'),
        ('richieste', '0002_alter_richiesta_options_richiesta_testo_richiesta_and_more'),
    ]

    operations = [
        migrations.CreateModel(
            name='InboxEmailDipendenteAzione',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('mailbox', models.CharField(default='INBOX', max_length=120)),
                ('uid_email', models.CharField(max_length=120)),
                ('mittente_email', models.CharField(blank=True, default='', max_length=255)),
                ('oggetto', models.CharField(blank=True, default='', max_length=255)),
                ('nascosta', models.BooleanField(default=False)),
                ('risposta_inviata', models.BooleanField(default=False)),
                ('data_risposta', models.DateTimeField(blank=True, null=True)),
                ('risposta_testo', models.TextField(blank=True, default='')),
                ('aggiornata_il', models.DateTimeField(auto_now=True)),
                ('creata_il', models.DateTimeField(auto_now_add=True)),
                ('azienda', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='inbox_email_dipendenti_azioni', to='anagrafiche.azienda')),
            ],
            options={
                'verbose_name': 'Inbox email dipendente (azione)',
                'verbose_name_plural': 'Inbox email dipendenti (azioni)',
                'ordering': ['-aggiornata_il', '-id'],
                'unique_together': {('azienda', 'mailbox', 'uid_email')},
            },
        ),
    ]
