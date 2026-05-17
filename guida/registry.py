"""
Registry canonico dei moduli funzionali GESPER.

Usare sempre questi `codice` in:
- VoceGuida.codice_modulo / codice_campo
- template tag {% guida_link "codice" %}
- riferimenti incrociati con docs/MODULI_E_TRACCIAMENTO.md

Campo `evoluzione` (per modulo):
- consolidato — punto di riferimento attuale; eventuale legacy collegato si può rimuovere quando soddisfa i criteri
- ibrido — funziona ma convive con vecchie schermate/flussi da accorpare o sostituire
- nuovo — target di progetto; implementazione ancora da completare o assente

LEGACY_AREE — inventario delle “vecchie” porzioni (menu, URL, duplicazioni) da smantellare
a favore del modulo indicato in modulo_sostitutivo. Aggiornare stato in sync con il team.
"""

from __future__ import annotations

from typing import Literal, TypedDict

EvoluzioneModulo = Literal['nuovo', 'ibrido', 'consolidato']
StatoLegacy = Literal['convivenza', 'da_smantellare', 'rimosso']


class ModuloInfo(TypedDict):
    codice: str
    titolo: str
    descrizione: str
    app_django: str
    evoluzione: EvoluzioneModulo


class LegacyArea(TypedDict):
    """Area o flusso legacy da tracciare per decisione di rimozione."""

    id: str
    titolo: str
    ubicazione: str
    modulo_sostitutivo: str
    stato: StatoLegacy
    criterio_rimozione: str


MODULI: tuple[ModuloInfo, ...] = (
    {
        'codice': 'reg-dipendente',
        'titolo': 'Registrazione e identità dipendente',
        'descrizione': (
            'Self-registration o creazione da Admin, login, reset password, '
            'convalida, matricola, uniformità dati (maiuscolo).'
        ),
        'app_django': 'accounts, anagrafiche',
        'evoluzione': 'nuovo',
    },
    {
        'codice': 'portale-dipendente',
        'titolo': 'Portale dipendente',
        'descrizione': (
            'Profilo, documenti, buste paga, CUD, richieste ferie/permessi/malattia, '
            'presenze in lettura, TFR/ferie ove esposti.'
        ),
        'app_django': 'accounts, documenti, richieste, presenze',
        'evoluzione': 'ibrido',
    },
    {
        'codice': 'modulo-admin',
        'titolo': 'Operazioni amministrative HR',
        'descrizione': (
            'Anagrafica, contratti, proposte e simulazioni, trasformazioni rapporto, '
            'controlli paghe, F24, calendari e chiusura mensile, pagamenti dare/avere.'
        ),
        'app_django': 'anagrafiche, rapporto_di_lavoro, documenti, presenze, storico',
        'evoluzione': 'ibrido',
    },
    {
        'codice': 'modulo-consulente',
        'titolo': 'Profilo consulente del lavoro',
        'descrizione': (
            'Carico PDF (cedolini, CUD, F24), comunicazioni INAIL/INPS/UniEmens, '
            'livelli di visibilità documenti.'
        ),
        'app_django': 'documenti, accounts (ruoli), workflow',
        'evoluzione': 'ibrido',
    },
    {
        'codice': 'presenze-turni',
        'titolo': 'Presenze, turni e calendario',
        'descrizione': (
            'Orari sede, turni dipendenti, calendario mensile, conferme e chiusura '
            'verso consulente.'
        ),
        'app_django': 'presenze',
        'evoluzione': 'ibrido',
    },
    {
        'codice': 'documenti-compliance',
        'titolo': 'Documenti e consensi',
        'descrizione': (
            'Upload classificati, privacy, geolocalizzazione lavoro, firma digitale '
            'contratti (conformità normativa italiana).'
        ),
        'app_django': 'documenti, rapporto_di_lavoro, accounts',
        'evoluzione': 'nuovo',
    },
    {
        'codice': 'paghe-controlli',
        'titolo': 'Paghe, libro paga e riconciliazioni',
        'descrizione': (
            'Import buste, confronto con contratti e profilo dipendente, prospetti '
            'consulente, evidenze lordo/netto/F24.'
        ),
        'app_django': 'documenti, storico, rapporto_di_lavoro',
        'evoluzione': 'ibrido',
    },
    {
        'codice': 'multi-azienda',
        'titolo': 'Contesto multi-aziendale',
        'descrizione': (
            'Selezione azienda operativa, filtri dati, permessi per sede/ruolo.'
        ),
        'app_django': 'anagrafiche, accounts',
        'evoluzione': 'consolidato',
    },
)


# Aree legacy: quando lo stato passa a `rimosso`, aggiornare anche le note in MODULI_E_TRACCIAMENTO.md
LEGACY_AREE: tuple[LegacyArea, ...] = (
    {
        'id': 'legacy-portale-candidato-dipendente-stesso-menu',
        'titolo': 'Menu portale candidato/dipendente con voci sovrapposte',
        'ubicazione': 'templates/base.html — sezioni candidato e dipendente',
        'modulo_sostitutivo': 'portale-dipendente',
        'stato': 'convivenza',
        'criterio_rimozione': (
            'Unificare navigazione dipendente vs candidato con wizard e ruoli espliciti; '
            'rimuovere duplicazioni quando ogni ruolo ha un solo percorso chiaro.'
        ),
    },
    {
        'id': 'legacy-documenti-multi-entry',
        'titolo': 'Accesso documenti da più punti (navbar, sotto-menu, URL diretti)',
        'ubicazione': 'Documenti, CUD, buste, F24, libro paga in menu HR e consulente',
        'modulo_sostitutivo': 'modulo-admin',
        'stato': 'convivenza',
        'criterio_rimozione': (
            'Introdurre hub documenti per ruolo con stessa tassonomia; tenere un solo entry point per profilo.'
        ),
    },
    {
        'id': 'legacy-simulazioni-menu-admin',
        'titolo': 'Simulatori e simulazione annua sparsi nel menu Admin',
        'ubicazione': 'base.html — dropdown Admin (simulatore paga, simulazione annua, …)',
        'modulo_sostitutivo': 'modulo-admin',
        'stato': 'convivenza',
        'criterio_rimozione': (
            'Raggruppare in un unico modulo “Contratti e simulazioni” con sotto-pagine; '
            'poi rimuovere voci ridondanti dal menu.'
        ),
    },
    {
        'id': 'legacy-django-admin-crud-parallelo',
        'titolo': 'CRUD dati sensibili sia in app custom sia in Django Admin',
        'ubicazione': '/admin/ vs viste HR interne',
        'modulo_sostitutivo': 'modulo-admin',
        'stato': 'convivenza',
        'criterio_rimozione': (
            'Definire quali modelli restano solo in Django Admin (tecnici) e quali solo in HR; '
            'evitare doppia modifica sugli stessi campi business.'
        ),
    },
    {
        'id': 'legacy-consulente-upload-singoli',
        'titolo': 'Più schermate di upload separate (buste, CUD, …)',
        'ubicazione': 'URL consulente upload buste / CUD / …',
        'modulo_sostitutivo': 'modulo-consulente',
        'stato': 'convivenza',
        'criterio_rimozione': (
            'Flusso unico “Carica documento paghe” con tipo documento; mantenere redirect '
            'compatibilità fino a migrazione utenti.'
        ),
    },
    {
        'id': 'legacy-registrazione-solo-candidato',
        'titolo': 'Registrazione self-service legata al percorso candidato',
        'ubicazione': '/candidato/registrati/, ProfiloCandidato',
        'modulo_sostitutivo': 'reg-dipendente',
        'stato': 'convivenza',
        'criterio_rimozione': (
            'Introdurre invito/registrazione dipendente con convalida admin e matricola; '
            'poi valutare se tenere solo candidatura separata o unificare form base.'
        ),
    },
)


def elenco_moduli():
    return MODULI


def get_modulo(codice: str) -> ModuloInfo | None:
    for m in MODULI:
        if m['codice'] == codice:
            return m
    return None


def elenco_legacy():
    return LEGACY_AREE


def legacy_per_modulo(codice_modulo: str) -> tuple[LegacyArea, ...]:
    return tuple(a for a in LEGACY_AREE if a['modulo_sostitutivo'] == codice_modulo)
