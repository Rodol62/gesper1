"""
Seed dei tipi di contratto secondo il CCNL FIPE
(Federazione Italiana Pubblici Esercizi — Turismo, Ristorazione, Bar).

Uso:
    python manage.py seed_contratti           # crea/aggiorna senza toccare i vecchi
    python manage.py seed_contratti --reset   # disattiva i vecchi non-FIPE e ricrea
"""
from django.core.management.base import BaseCommand
from rapporto_di_lavoro.models import TipoContratto

CCNL = 'CCNL FIPE'

# ── Definizione completa tipi FIPE ──────────────────────────────────────────
# Campi: nome, tipo, coefficiente_ore, durata_giorni, prova_giorni,
#         prorogabile, rinnovabile, descrizione
TIPI_FIPE = [

    # ── TEMPO INDETERMINATO ─────────────────────────────────────────────────
    dict(
        nome='Tempo Indeterminato Full-Time',
        tipo='ind_full',
        coefficiente_ore=1.00,
        durata_giorni=None,
        prova_giorni=30,
        prorogabile=False,
        rinnovabile=False,
        descrizione=(
            'Contratto a tempo indeterminato full-time (40 h/sett. su 6 gg). '
            'Forma comune di assunzione nel settore FIPE. '
            'Periodo di prova: 30 gg lavorativi per i livelli 1-3, '
            '20 gg per il livello 4, 10 gg per il livello 5, 5 gg per il livello 6.'
        ),
    ),
    dict(
        nome='Tempo Indeterminato Part-Time 50%',
        tipo='ind_pt_50',
        coefficiente_ore=0.50,
        durata_giorni=None,
        prova_giorni=30,
        prorogabile=False,
        rinnovabile=False,
        descrizione=(
            'Contratto a tempo indeterminato part-time orizzontale al 50% '
            '(20 h/sett.). Clausole elastiche e flessibili disciplinate '
            "dall'art. 64 CCNL FIPE."
        ),
    ),
    dict(
        nome='Tempo Indeterminato Part-Time 60%',
        tipo='ind_pt_60',
        coefficiente_ore=0.60,
        durata_giorni=None,
        prova_giorni=30,
        prorogabile=False,
        rinnovabile=False,
        descrizione=(
            'Contratto a tempo indeterminato part-time orizzontale al 60% '
            '(24 h/sett.).'
        ),
    ),
    dict(
        nome='Tempo Indeterminato Part-Time 75%',
        tipo='ind_pt_75',
        coefficiente_ore=0.75,
        durata_giorni=None,
        prova_giorni=30,
        prorogabile=False,
        rinnovabile=False,
        descrizione=(
            'Contratto a tempo indeterminato part-time orizzontale al 75% '
            '(30 h/sett.).'
        ),
    ),
    dict(
        nome='Tempo Indeterminato Part-Time 80%',
        tipo='ind_pt_80',
        coefficiente_ore=0.80,
        durata_giorni=None,
        prova_giorni=30,
        prorogabile=False,
        rinnovabile=False,
        descrizione=(
            'Contratto a tempo indeterminato part-time orizzontale al 80% '
            '(32 h/sett.).'
        ),
    ),
    dict(
        nome='Tempo Indeterminato Part-Time 90%',
        tipo='ind_pt_90',
        coefficiente_ore=0.90,
        durata_giorni=None,
        prova_giorni=30,
        prorogabile=False,
        rinnovabile=False,
        descrizione=(
            'Contratto a tempo indeterminato part-time orizzontale al 90% '
            '(36 h/sett.).'
        ),
    ),
    dict(
        nome='Tempo Indeterminato Part-Time 83%',
        tipo='ind_pt_83',
        coefficiente_ore=0.833,
        durata_giorni=None,
        prova_giorni=30,
        prorogabile=False,
        rinnovabile=False,
        descrizione=(
            'Contratto a tempo indeterminato part-time orizzontale al 83,33% '
            '(circa 33 h/sett. — 5 giorni su 6).'
        ),
    ),

    # ── TEMPO DETERMINATO ───────────────────────────────────────────────────
    dict(
        nome='Tempo Determinato Full-Time',
        tipo='det_full',
        coefficiente_ore=1.00,
        durata_giorni=730,   # 24 mesi — limite massimo D.Lgs. 81/2015
        prova_giorni=30,
        prorogabile=True,    # max 4 proroghe entro i 24 mesi
        rinnovabile=False,   # oltre 24 mesi diventa indeterminato
        descrizione=(
            'Contratto a tempo determinato full-time ai sensi del D.Lgs. 81/2015 '
            '(art. 19-29). Durata massima: 24 mesi complessivi. '
            'Prorogabile fino a 4 volte entro il limite di 24 mesi. '
            'Causali obbligatorie oltre i 12 mesi o in caso di rinnovo. '
            'Contributo addizionale NASPI: +0,5% per ogni rinnovo.'
        ),
    ),
    dict(
        nome='Tempo Determinato Part-Time 50%',
        tipo='det_pt_50',
        coefficiente_ore=0.50,
        durata_giorni=730,
        prova_giorni=30,
        prorogabile=True,
        rinnovabile=False,
        descrizione=(
            'Contratto a tempo determinato part-time 50% (20 h/sett.). '
            'Stesse norme del determinato full-time (D.Lgs. 81/2015).'
        ),
    ),
    dict(
        nome='Tempo Determinato Part-Time 75%',
        tipo='det_pt_75',
        coefficiente_ore=0.75,
        durata_giorni=730,
        prova_giorni=30,
        prorogabile=True,
        rinnovabile=False,
        descrizione=(
            'Contratto a tempo determinato part-time 75% (30 h/sett.).'
        ),
    ),

    # ── STAGIONALE ──────────────────────────────────────────────────────────
    dict(
        nome='Contratto Stagionale Full-Time',
        tipo='stag_full',
        coefficiente_ore=1.00,
        durata_giorni=None,  # durata variabile legata alla stagione
        prova_giorni=7,
        prorogabile=True,
        rinnovabile=True,    # nessun limite al numero di rinnovi stagionali
        descrizione=(
            'Contratto stagionale full-time per attività di natura stagionale '
            '(art. 21 co. 2 D.Lgs. 81/2015 e D.P.R. 1525/1963). '
            'Non soggetto al limite dei 24 mesi. Rinnovabile senza limitazioni. '
            'Diritto di precedenza per la stagione successiva (art. 24 D.Lgs. 81/2015). '
            'Indennità di fine stagione prevista dal CCNL FIPE.'
        ),
    ),
    dict(
        nome='Contratto Stagionale Part-Time',
        tipo='stag_pt',
        coefficiente_ore=0.50,   # da adeguare caso per caso
        durata_giorni=None,
        prova_giorni=7,
        prorogabile=True,
        rinnovabile=True,
        descrizione=(
            'Contratto stagionale part-time per attività stagionali. '
            'Il coefficiente ore va aggiornato in base alle ore concordate. '
            'Stesse tutele del stagionale full-time.'
        ),
    ),

    # ── APPRENDISTATO PROFESSIONALIZZANTE ───────────────────────────────────
    dict(
        nome='Apprendistato Professionalizzante',
        tipo='apprendistato',
        coefficiente_ore=1.00,
        durata_giorni=1095,  # 36 mesi (estendibile a 48 per qualifiche specifiche)
        prova_giorni=30,
        prorogabile=False,
        rinnovabile=False,
        descrizione=(
            'Apprendistato professionalizzante ai sensi del D.Lgs. 81/2015 '
            'artt. 41-47 e CCNL FIPE. Durata: 36 mesi (3 anni), estendibile '
            'a 48 mesi per qualifiche di 4° livello. '
            'Retribuzione ridotta: 85% nel 1° anno, 95% nel 2° anno, '
            '100% dal 3° anno. '
            'Contribuzione ridotta a carico azienda per i primi 3 anni. '
            'Formazione interna obbligatoria: min. 120 ore/anno. '
            'Divieto di recesso durante il periodo formativo salvo giusta causa.'
        ),
    ),

    # ── LAVORO INTERMITTENTE ─────────────────────────────────────────────────
    dict(
        nome='Lavoro Intermittente (a Chiamata)',
        tipo='intermittente',
        coefficiente_ore=0.50,   # indicativo — varia in base alle chiamate
        durata_giorni=None,
        prova_giorni=0,
        prorogabile=True,
        rinnovabile=True,
        descrizione=(
            'Contratto di lavoro intermittente (a chiamata) ai sensi degli '
            'artt. 13-18 D.Lgs. 81/2015. Ammesso per i lavoratori con più di '
            '55 anni o under 24, oppure per attività stagionali/periodi di punta. '
            'Limite: 400 giornate in 3 anni (eccetto settori turismo, pubblici '
            'esercizi e spettacolo). Con o senza obbligo di risposta alla chiamata. '
            'Se con disponibilità: indennità giornaliera min. pari al 20% '
            'della retribuzione globale.'
        ),
    ),

    # ── SOMMINISTRAZIONE ─────────────────────────────────────────────────────
    dict(
        nome='Somministrazione di Lavoro',
        tipo='somministrazione',
        coefficiente_ore=1.00,
        durata_giorni=None,
        prova_giorni=0,
        prorogabile=True,
        rinnovabile=True,
        descrizione=(
            'Contratto di somministrazione tramite Agenzia per il Lavoro '
            'autorizzata (APL). Disciplinato dagli artt. 30-40 D.Lgs. 81/2015. '
            'Il lavoratore è dipendente dell\'APL; il CCNL applicato è quello '
            'del settore utilizzatore (FIPE). '
            'Durata complessiva max 24 mesi presso lo stesso utilizzatore. '
            'Contributo addizionale NASPI: 1,30% a carico APL.'
        ),
    ),
]

# Nomi "vecchi" da disattivare se presenti
NOMI_VECCHI = {
    'Indeterminato', 'Determinato 30 gg', 'Determinato 3 mesi',
    'Determinato 6 mesi', 'Determinato 1 anno', 'Part-Time Indeterminato',
    'Apprendistato', 'Tirocinio', 'Somministrazione', 'Telelavoro',
}


class Command(BaseCommand):
    help = 'Crea/aggiorna i tipi contratto CCNL FIPE'

    def add_arguments(self, parser):
        parser.add_argument(
            '--reset',
            action='store_true',
            help='Disattiva i vecchi tipi non-FIPE prima di creare i nuovi',
        )

    def handle(self, *args, **options):
        if options['reset']:
            disattivati = (
                TipoContratto.objects
                .filter(nome__in=NOMI_VECCHI, attivo=True)
                .update(attivo=False)
            )
            if disattivati:
                self.stdout.write(
                    self.style.WARNING(f'  Disattivati {disattivati} tipi vecchi.')
                )

        creati = aggiornati = 0
        for dati in TIPI_FIPE:
            obj, created = TipoContratto.objects.update_or_create(
                nome=dati['nome'],
                defaults={**dati, 'ccnl': CCNL, 'attivo': True},
            )
            if created:
                creati += 1
            else:
                aggiornati += 1

        self.stdout.write(self.style.SUCCESS(
            f'✓ CCNL FIPE — Creati: {creati}, Aggiornati: {aggiornati}'
        ))
