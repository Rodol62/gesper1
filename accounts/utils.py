"""
Utility per il modulo accounts.
"""
from django.utils import timezone


# ── Definizione campi obbligatori / consigliati per la proposta ──────────────

# Blocca la creazione della proposta se mancanti
CAMPI_OBBLIGATORI_PROPOSTA = [
    ('codice_fiscale',        'Codice Fiscale'),
    ('data_nascita',          'Data di nascita'),
    ('luogo_nascita',         'Luogo di nascita'),
    ('sesso',                 'Sesso'),
    ('nazionalita',           'Nazionalità'),
    ('indirizzo',             'Indirizzo di residenza'),
    ('cap',                   'CAP'),
    ('citta',                 'Città'),
    ('provincia',             'Provincia'),
    ('telefono',              'Telefono / cellulare'),
    ('tipo_documento',        'Tipo documento identità'),
    ('numero_documento',      'Numero documento'),
    ('scadenza_documento',    'Scadenza documento'),
    ('data_disponibilita',    'Disponibile dal'),
]

# Consentita la proposta, ma mostrati come avvisi
CAMPI_CONSIGLIATI_PROPOSTA = [
    ('iban',                     'IBAN (necessario per il pagamento)'),
    ('regione_residenza',        'Regione di residenza (addizionale IRPEF)'),
    ('dichiarazione_no_condanne', 'Dichiarazione assenza precedenti penali'),
]

# Allegati documentali (FileField sul profilo)
CAMPI_ALLEGATI_PROPOSTA = [
    ('file_documento', 'Documento di identità (PDF/JPG)'),
    ('file_codice_fiscale', 'Codice fiscale / tessera sanitaria (PDF/JPG)'),
]


def controlla_completezza_profilo(profilo):
    """
    Verifica la completezza del ProfiloCandidato rispetto ai dati
    necessari per generare una proposta di assunzione.

    Restituisce un dict:
        {
          'completo':       bool,          # True se tutti i campi obbligatori presenti
          'mancanti':       [(campo, label), ...],  # campi obbligatori mancanti
          'consigliati':    [(campo, label), ...],  # campi consigliati mancanti
          'percentuale':    int,           # % di completamento (obbligatori + consigliati)
          'doc_scaduto':    bool,          # True se il documento è già scaduto
        }
    """
    if not profilo:
        return {
            'completo': False,
            'mancanti': CAMPI_OBBLIGATORI_PROPOSTA[:],
            'consigliati': CAMPI_CONSIGLIATI_PROPOSTA[:] + CAMPI_ALLEGATI_PROPOSTA[:],
            'percentuale': 0,
            'doc_scaduto': False,
        }

    mancanti = []
    for campo, label in CAMPI_OBBLIGATORI_PROPOSTA:
        valore = getattr(profilo, campo, None)
        if not valore and valore != 0:
            mancanti.append((campo, label))

    consigliati_mancanti = []
    for campo, label in CAMPI_CONSIGLIATI_PROPOSTA:
        valore = getattr(profilo, campo, None)
        # dichiarazione_no_condanne è booleano: False è mancante
        if campo == 'dichiarazione_no_condanne':
            if not valore:
                consigliati_mancanti.append((campo, label))
        elif not valore and valore != 0:
            consigliati_mancanti.append((campo, label))

    for campo, label in CAMPI_ALLEGATI_PROPOSTA:
        if not getattr(profilo, campo, None):
            consigliati_mancanti.append((campo, label))

    totale = (
        len(CAMPI_OBBLIGATORI_PROPOSTA)
        + len(CAMPI_CONSIGLIATI_PROPOSTA)
        + len(CAMPI_ALLEGATI_PROPOSTA)
    )
    presenti = totale - len(mancanti) - len(consigliati_mancanti)
    percentuale = round(presenti / totale * 100) if totale else 100

    doc_scaduto = False
    scadenza = getattr(profilo, 'scadenza_documento', None)
    if scadenza and scadenza <= timezone.localdate():
        doc_scaduto = True

    return {
        'completo': len(mancanti) == 0,
        'mancanti': mancanti,
        'consigliati': consigliati_mancanti,
        'percentuale': percentuale,
        'doc_scaduto': doc_scaduto,
    }


def get_richiesta_integrazione_attiva(candidato):
    from .models import RichiestaIntegrazioneCandidato

    return (
        RichiestaIntegrazioneCandidato.objects
        .filter(candidato=candidato, stato__in=['inviata', 'completata_candidato'])
        .order_by('-data_invio')
        .first()
    )


def get_ultima_richiesta_integrazione(candidato):
    from .models import RichiestaIntegrazioneCandidato

    return (
        RichiestaIntegrazioneCandidato.objects
        .filter(candidato=candidato)
        .order_by('-data_invio')
        .first()
    )


def checklist_richiesta_integrazione(richiesta, profilo):
    """Restituisce checklist e mancanze rispetto alla richiesta HR."""
    items = []
    mancanti = []

    if not richiesta:
        return {
            'items': items,
            'mancanti': mancanti,
            'completa': True,
        }

    def _append(label, ok):
        voce = {'label': label, 'ok': bool(ok)}
        items.append(voce)
        if not ok:
            mancanti.append(label)

    if richiesta.richiedi_documento_identita:
        _append('Documento di identità allegato', bool(profilo and profilo.file_documento))

    if richiesta.richiedi_codice_fiscale:
        _append('Tessera sanitaria / codice fiscale allegata', bool(profilo and profilo.file_codice_fiscale))

    if richiesta.richiedi_mansione:
        _append('Mansione aspirata compilata', bool(profilo and (profilo.mansione_aspirata or '').strip()))

    if richiesta.ruolo_richiesto:
        _append(f'Ruolo richiesto indicato: {richiesta.ruolo_richiesto}', bool(profilo and (profilo.mansione_aspirata or '').strip()))

    if richiesta.richiedi_disponibilita:
        _append('Disponibilità lavorativa compilata', bool(profilo and profilo.data_disponibilita and profilo.ore_settimanali_preferite))

    if richiesta.richiedi_curriculum:
        from documenti.models import Documento

        dipendente = getattr(profilo, 'dipendente', None) if profilo else None
        ha_curriculum = bool(
            dipendente and Documento.objects.filter(
                dipendente=dipendente,
                tipo='curriculum',
                visibile_al_dipendente=True,
            ).exists()
        )
        _append('Curriculum vitae caricato', ha_curriculum)

    return {
        'items': items,
        'mancanti': mancanti,
        'completa': len(mancanti) == 0,
    }
