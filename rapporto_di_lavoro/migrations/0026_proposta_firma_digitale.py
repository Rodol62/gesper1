from django.db import migrations, models


def migra_stati_legacy(apps, schema_editor):
    """Converte i vecchi stati proposta nei nuovi stati semplificati."""
    PropostaAssunzione = apps.get_model('rapporto_di_lavoro', 'PropostaAssunzione')

    mapping = {
        'inviata_al_dipendente':  'inviata_candidato',
        'accettata_dipendente':   'firmata_candidato',
        'in_revisione_admin':     'firmata_candidato',
        'approvata_admin':        'firmata_candidato',
        'rifiutata_dipendente':   'rifiutata_candidato',
        'convertita_in_contratto': 'contratto_attivo',
    }

    for vecchio, nuovo in mapping.items():
        PropostaAssunzione.objects.filter(stato=vecchio).update(stato=nuovo)


def reversa_stati(apps, schema_editor):
    """Reversal best-effort: rimappa i nuovi stati ai vecchi più vicini."""
    PropostaAssunzione = apps.get_model('rapporto_di_lavoro', 'PropostaAssunzione')
    PropostaAssunzione.objects.filter(stato='inviata_candidato').update(stato='inviata_al_dipendente')
    PropostaAssunzione.objects.filter(stato='firmata_candidato').update(stato='accettata_dipendente')
    PropostaAssunzione.objects.filter(stato='rifiutata_candidato').update(stato='rifiutata_dipendente')
    PropostaAssunzione.objects.filter(stato='contratto_attivo').update(stato='convertita_in_contratto')


class Migration(migrations.Migration):

    dependencies = [
        ('rapporto_di_lavoro', '0025_detrazione_lavoro_dipendente_bonus_multianno'),
    ]

    operations = [
        # 1. Aggiungi i campi firma digitale
        migrations.AddField(
            model_name='propostaassunzione',
            name='data_firma_candidato',
            field=models.DateTimeField(
                blank=True, null=True,
                help_text='Timestamp della firma digitale (spunta) del candidato.'
            ),
        ),
        migrations.AddField(
            model_name='propostaassunzione',
            name='ip_firma_candidato',
            field=models.CharField(
                blank=True, max_length=45,
                help_text='Indirizzo IP del candidato al momento della firma.'
            ),
        ),
        migrations.AddField(
            model_name='propostaassunzione',
            name='data_firma_datore',
            field=models.DateTimeField(
                blank=True, null=True,
                help_text='Timestamp della firma definitiva del datore di lavoro.'
            ),
        ),
        # 2. Data migration: converte gli stati vecchi nei nuovi
        migrations.RunPython(migra_stati_legacy, reversa_stati),
        # 3. Aggiorna le choices nel campo stato (solo metadata Django, nessun DDL)
        migrations.AlterField(
            model_name='propostaassunzione',
            name='stato',
            field=models.CharField(
                choices=[
                    ('bozza', 'Bozza'),
                    ('inviata_candidato', 'Inviata al candidato'),
                    ('firmata_candidato', 'Firmata dal candidato'),
                    ('contratto_attivo', 'Contratto attivo'),
                    ('rifiutata_candidato', 'Rifiutata dal candidato'),
                    ('rifiutata_admin', "Rifiutata dall'amministrazione"),
                    ('inviata_al_dipendente', 'Inviata al dipendente (legacy)'),
                    ('accettata_dipendente', 'Accettata dal dipendente (legacy)'),
                    ('rifiutata_dipendente', 'Rifiutata dal dipendente (legacy)'),
                    ('in_revisione_admin', 'In revisione admin (legacy)'),
                    ('approvata_admin', 'Approvata admin (legacy)'),
                    ('convertita_in_contratto', 'Convertita in contratto (legacy)'),
                ],
                default='bozza',
                max_length=30,
            ),
        ),
    ]
