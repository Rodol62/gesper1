"""
Management command: seed_voci_retributive_2025
================================================
Popola le tabelle:
  - VoceRetributiva    → classificazione completa voci paga con flag imponibilità
  - FranchigiaSogliaVoce → soglie esenti per trasferte, ticket, fringe benefit
  - InailParametro     → minimali/massimali INAIL per CCNL Turismo Confcommercio

Uso:
    python manage.py seed_voci_retributive_2025 [--anno 2025] [--forza]

Opzioni:
    --anno    Anno di riferimento (default: 2025)
    --forza   Sovrascrive i record esistenti per lo stesso anno
"""

from decimal import Decimal

from django.core.management.base import BaseCommand
from django.utils.dateparse import parse_date


class Command(BaseCommand):
    help = 'Popola VoceRetributiva, FranchigiaSogliaVoce e InailParametro per il 2025'

    def add_arguments(self, parser):
        parser.add_argument('--anno', type=int, default=2025)
        parser.add_argument('--forza', action='store_true', default=False)

    def handle(self, *args, **options):
        anno = options['anno']
        forza = options['forza']

        self.stdout.write(self.style.MIGRATE_HEADING(
            f'\n══ Seed voci retributive e franchigie {anno} ══\n'
        ))

        self._seed_franchigie(anno, forza)
        self._seed_voci_retributive(forza)
        self._seed_inail_parametri(anno, forza)

        self.stdout.write(self.style.SUCCESS('\n✅ Seed completato con successo.\n'))

    # ─────────────────────────────────────────────────────────────────────────
    # FRANCHIGIE
    # ─────────────────────────────────────────────────────────────────────────
    def _seed_franchigie(self, anno, forza):
        from rapporto_di_lavoro.models import FranchigiaSogliaVoce

        dati = [
            # ── Trasferte (art. 51 c. 5 TUIR) ────────────────────────────────
            {
                'codice': f'TRASFERTA_ITA_{anno}',
                'tipo': 'trasferta_italia',
                'anno': anno,
                'importo': Decimal('46.48'),
                'unita_misura': 'giorno',
                'note': (
                    'Diaria intera giornaliera. '
                    'Dimezzata a €23,24 se rimborsato vitto O alloggio. '
                    'Azzerata se rimborsati vitto E alloggio.'
                ),
                'riferimento_normativo': 'Art. 51 c. 5 TUIR',
                'data_validita_da': parse_date(f'{anno}-01-01'),
                'data_validita_a': parse_date(f'{anno}-12-31'),
            },
            {
                'codice': f'TRASFERTA_EST_{anno}',
                'tipo': 'trasferta_estero',
                'anno': anno,
                'importo': Decimal('77.47'),
                'unita_misura': 'giorno',
                'note': (
                    'Diaria intera giornaliera per trasferte all\'estero. '
                    'Dimezzata a €38,74 se rimborsato vitto O alloggio. '
                    'Azzerata se rimborsati vitto E alloggio.'
                ),
                'riferimento_normativo': 'Art. 51 c. 5 TUIR',
                'data_validita_da': parse_date(f'{anno}-01-01'),
                'data_validita_a': parse_date(f'{anno}-12-31'),
            },

            # ── Fringe benefit (art. 51 c. 3 TUIR + L. 207/2024) ─────────────
            {
                'codice': f'FB_GENERALE_{anno}',
                'tipo': 'fringe_benefit_generale',
                'anno': anno,
                'importo': Decimal('258.23'),
                'unita_misura': 'anno',
                'note': (
                    'Soglia annua esente per dipendenti senza figli fiscalmente a carico. '
                    'Include: auto aziendale (quota privata), prestiti, alloggio, '
                    'assicurazioni, buoni spesa. L\'eccedenza è interamente imponibile '
                    'INPS/INAIL/IRPEF.'
                ),
                'riferimento_normativo': 'Art. 51 c. 3 TUIR',
                'data_validita_da': parse_date(f'{anno}-01-01'),
                'data_validita_a': parse_date(f'{anno}-12-31'),
            },
            {
                'codice': f'FB_CON_FIGLI_{anno}',
                'tipo': 'fringe_benefit_con_figli',
                'anno': anno,
                'importo': Decimal('1000.00'),
                'unita_misura': 'anno',
                'note': (
                    'Soglia annua esente per dipendenti con almeno 1 figlio '
                    'fiscalmente a carico. Prorogata da L. 207/2024 art. 1 c. 390. '
                    'Il dipendente deve dichiarare la condizione in forma scritta all\'azienda. '
                    'Include anche rimborso utenze domestiche (acqua, luce, gas).'
                ),
                'riferimento_normativo': 'Art. 51 c. 3 TUIR — L. 207/2024 art. 1 c. 390',
                'data_validita_da': parse_date(f'{anno}-01-01'),
                'data_validita_a': parse_date(f'{anno}-12-31'),
            },

            # ── Ticket restaurant (art. 51 c. 2 lett. c TUIR — L. 213/2023) ──
            {
                'codice': f'TICKET_CARTA_{anno}',
                'tipo': 'ticket_cartaceo',
                'anno': anno,
                'importo': Decimal('4.00'),
                'unita_misura': 'pasto',
                'note': (
                    'Ticket restaurant cartaceo: esente fino a €4,00 per giorno/pasto. '
                    'L\'eccedenza è imponibile IRPEF (ma non INPS per prassi consolidata).'
                ),
                'riferimento_normativo': 'Art. 51 c. 2 lett. c TUIR — L. 213/2023 art. 1 c. 16',
                'data_validita_da': parse_date(f'{anno}-01-01'),
                'data_validita_a': parse_date(f'{anno}-12-31'),
            },
            {
                'codice': f'TICKET_ELETT_{anno}',
                'tipo': 'ticket_elettronico',
                'anno': anno,
                'importo': Decimal('8.00'),
                'unita_misura': 'pasto',
                'note': (
                    'Ticket restaurant elettronico: esente fino a €8,00 per giorno/pasto. '
                    'L\'eccedenza è imponibile IRPEF. Aggiornato da L. 213/2023.'
                ),
                'riferimento_normativo': 'Art. 51 c. 2 lett. c TUIR — L. 213/2023 art. 1 c. 16',
                'data_validita_da': parse_date(f'{anno}-01-01'),
                'data_validita_a': parse_date(f'{anno}-12-31'),
            },
        ]

        creati = aggiornati = 0
        for d in dati:
            obj, created = FranchigiaSogliaVoce.objects.update_or_create(
                codice=d['codice'],
                defaults=d if forza else {k: v for k, v in d.items() if k != 'codice'},
            ) if forza else FranchigiaSogliaVoce.objects.get_or_create(
                codice=d['codice'], defaults={k: v for k, v in d.items() if k != 'codice'}
            )
            if created:
                creati += 1
            else:
                aggiornati += 1

        self.stdout.write(
            f'  Franchigie: {creati} create, {aggiornati} già presenti'
        )

    # ─────────────────────────────────────────────────────────────────────────
    # VOCI RETRIBUTIVE
    # ─────────────────────────────────────────────────────────────────────────
    def _seed_voci_retributive(self, forza):
        from rapporto_di_lavoro.models import VoceRetributiva

        voci = [
            # ── GRUPPO 1: Voci 100% imponibili (INPS + INAIL + IRPEF) ─────────
            {
                'codice': 'PAGA_BASE',
                'nome': 'Paga base / Minimo tabellare CCNL',
                'categoria': 'minimo_tabellare',
                'imponibile_inps': True, 'imponibile_inail': True, 'imponibile_irpef': True,
                'descrizione': 'Importo minimo retributivo fissato dal CCNL per livello e qualifica.',
                'riferimento_normativo': 'Art. 36 Cost. — CCNL Turismo Confcommercio',
            },
            {
                'codice': 'CONTINGENZA',
                'nome': 'Indennità di contingenza / EDR',
                'categoria': 'contingenza',
                'imponibile_inps': True, 'imponibile_inail': True, 'imponibile_irpef': True,
                'descrizione': (
                    'Elemento aggiuntivo della retribuzione (contingenza + EDR €10,33). '
                    'Congelata dal luglio 1992 (Protocollo Ciampi).'
                ),
                'riferimento_normativo': 'Accordo Tripartito 31/07/1992',
            },
            {
                'codice': 'SCATTO_ANZ',
                'nome': 'Scatto di anzianità',
                'categoria': 'scatto_anzianita',
                'imponibile_inps': True, 'imponibile_inail': True, 'imponibile_irpef': True,
                'descrizione': 'Aumento periodico in base agli anni di permanenza in azienda.',
                'riferimento_normativo': 'CCNL Turismo — art. scatti di anzianità',
            },
            {
                'codice': 'SUPERMINIMO',
                'nome': 'Superminimo individuale',
                'categoria': 'superminimo',
                'imponibile_inps': True, 'imponibile_inail': True, 'imponibile_irpef': True,
                'descrizione': (
                    'Importo individuale aggiuntivo rispetto al minimo tabellare. '
                    'Può essere assorbibile o non assorbibile dagli scatti/aumenti.'
                ),
                'riferimento_normativo': 'Art. 2099 c.c.',
            },
            {
                'codice': 'IND_FUNZIONE',
                'nome': 'Indennità di funzione/ruolo',
                'categoria': 'indennita_funzione',
                'imponibile_inps': True, 'imponibile_inail': True, 'imponibile_irpef': True,
                'descrizione': 'Indennità per quadri, dirigenti, responsabili di settore.',
                'riferimento_normativo': 'CCNL — accordi aziendali',
            },
            {
                'codice': 'TREDICESIMA',
                'nome': 'Tredicesima mensilità (rateo o liquidazione)',
                'categoria': 'tredicesima',
                'imponibile_inps': False, 'imponibile_inail': False, 'imponibile_irpef': False,
                'descrizione': (
                    'Rateo o liquidazione 13ª. Imponibile INPS/IRPEF/INAIL del mese solo se la quota '
                    '1/12 è effettivamente in busta (flag contratto); altrimenti solo accantonamento.'
                ),
                'riferimento_normativo': 'CCNL Turismo',
            },
            {
                'codice': 'QUATTORDICESIMA',
                'nome': 'Quattordicesima mensilità (rateo o liquidazione)',
                'categoria': 'quattordicesima',
                'imponibile_inps': False, 'imponibile_inail': False, 'imponibile_irpef': False,
                'descrizione': (
                    'Come la 13ª: imponibile mensile solo se rateo in cedolino; altrimenti accantonamento.'
                ),
                'riferimento_normativo': 'CCNL Turismo Confcommercio — 14ª mensilità',
            },
            {
                'codice': 'PREMIO_ORDINARIO',
                'nome': 'Premio di risultato (tassazione ordinaria)',
                'categoria': 'premio_risultato',
                'imponibile_inps': True, 'imponibile_inail': True, 'imponibile_irpef': True,
                'descrizione': (
                    'Premio non agevolato: soggetto a INPS, INAIL e IRPEF ordinaria. '
                    'Usare PREMIO_AGEVOLATO se rispetta i requisiti di legge.'
                ),
            },
            {
                'codice': 'PREMIO_AGEVOLATO',
                'nome': 'Premio di risultato (aliquota IRPEF agevolata 5%)',
                'categoria': 'premio_agevolato',
                'imponibile_inps': True, 'imponibile_inail': True, 'imponibile_irpef': True,
                'aliquota_agevolata': Decimal('5.00'),
                'importo_massimo_agevolato_annuo': Decimal('3000.00'),
                'descrizione': (
                    'Premio di risultato con tassazione sostitutiva IRPEF al 5% '
                    '(art. 1 c. 182-190 L. 208/2015 — confermato L. 207/2024). '
                    'Max €3.000/anno. Imponibile INPS e INAIL normalmente.'
                ),
                'riferimento_normativo': 'Art. 1 c. 182-190 L. 208/2015 — L. 207/2024',
            },
            {
                'codice': 'STRAORD_DIURNO',
                'nome': 'Straordinario diurno',
                'categoria': 'straordinario',
                'imponibile_inps': True, 'imponibile_inail': True, 'imponibile_irpef': True,
                'descrizione': 'Ore di lavoro oltre l\'orario contrattuale in fascia diurna con relativa maggiorazione.',
            },
            {
                'codice': 'STRAORD_NOTTURNO',
                'nome': 'Straordinario notturno / Maggiorazione notturna',
                'categoria': 'straordinario',
                'imponibile_inps': True, 'imponibile_inail': True, 'imponibile_irpef': True,
                'descrizione': 'Ore notturne con maggiorazione CCNL (tipicamente 30% o più).',
            },
            {
                'codice': 'STRAORD_FESTIVO',
                'nome': 'Straordinario festivo / Maggiorazione festiva',
                'categoria': 'straordinario',
                'imponibile_inps': True, 'imponibile_inail': True, 'imponibile_irpef': True,
                'descrizione': 'Ore lavorate nei giorni festivi con relativa maggiorazione.',
            },
            {
                'codice': 'MAGG_DOM_FEST',
                'nome': 'Maggiorazioni lavoro domenicale e festivo (non straordinario)',
                'categoria': 'straordinario',
                'imponibile_inps': True, 'imponibile_inail': True, 'imponibile_irpef': True,
                'descrizione': (
                    'Maggiorazioni CCNL su ore ordinarie in domenica/festivo (non quota straordinario). '
                    'Matura 13ª/14ª e concorre al TFR secondo schema motore Gesper.'
                ),
            },
            {
                'codice': 'IND_TURNO',
                'nome': 'Indennità di turno',
                'categoria': 'indennita_turno',
                'imponibile_inps': True, 'imponibile_inail': True, 'imponibile_irpef': True,
                'descrizione': 'Indennità per lavoro organizzato in turni rotativi.',
            },
            {
                'codice': 'FERIE_MONETIZZ',
                'nome': 'Ferie e permessi monetizzati',
                'categoria': 'ferie_monetizzate',
                'imponibile_inps': True, 'imponibile_inail': True, 'imponibile_irpef': True,
                'descrizione': (
                    'Importo liquidato per ferie o ROL non goduti (solo alla cessazione '
                    'o in casi eccezionali — vedi CCNL). Interamente imponibile.'
                ),
                'riferimento_normativo': 'Art. 2109 c.c. — D.Lgs. 66/2003 art. 10',
            },
            {
                'codice': '8108',
                'nome': 'Festività non goduta (ore) — TeamSystem',
                'categoria': 'ferie_monetizzate',
                'imponibile_inps': True, 'imponibile_inail': True, 'imponibile_irpef': True,
                'descrizione': (
                    'Voce cedolino TeamSystem: festività non goduta liquidata a ore. '
                    'Classificata come competenza imponibile per conciliazione con cedolino consulente.'
                ),
                'riferimento_normativo': 'Art. 2109 c.c. — D.Lgs. 66/2003 art. 10',
            },
            {
                'codice': '8109',
                'nome': 'Festività goduta (ore) — TeamSystem',
                'categoria': 'straordinario',
                'imponibile_inps': True, 'imponibile_inail': True, 'imponibile_irpef': True,
                'descrizione': (
                    'Voce cedolino TeamSystem: festività goduta retribuita a ore '
                    '(competenza imponibile).'
                ),
                'riferimento_normativo': 'CCNL Turismo — festività',
            },
            {
                'codice': 'PREAVVISO_MANCATO',
                'nome': 'Indennità mancato preavviso (in luogo del preavviso lavorato)',
                'categoria': 'preavviso',
                'imponibile_inps': True, 'imponibile_inail': True, 'imponibile_irpef': True,
                'descrizione': (
                    'Somma corrisposta in caso di licenziamento o dimissioni senza '
                    'preavviso. Imponibile al 100% per INPS, INAIL e IRPEF ordinaria '
                    '(non è "indennità sostitutiva del preavviso" in cessazione).'
                ),
            },

            # ── GRUPPO 2: Voci con trattamento speciale (imponibili parziali) ──
            {
                'codice': 'TRASFERTA_ITA',
                'nome': 'Trasferta Italia (diaria forfettaria)',
                'categoria': 'trasferta',
                'imponibile_inps': True, 'imponibile_inail': True, 'imponibile_irpef': True,
                'imponibile_parziale': True,
                'codice_franchigia': 'trasferta_italia',
                'descrizione': (
                    'Diaria trasferta Italia: esente fino a €46,48/giorno. '
                    'La parte eccedente è imponibile INPS + INAIL + IRPEF. '
                    'La soglia si dimezza se rimborsato anche vitto o alloggio.'
                ),
                'riferimento_normativo': 'Art. 51 c. 5 TUIR',
            },
            {
                'codice': 'TRASFERTA_EST',
                'nome': 'Trasferta Estero (diaria forfettaria)',
                'categoria': 'trasferta',
                'imponibile_inps': True, 'imponibile_inail': True, 'imponibile_irpef': True,
                'imponibile_parziale': True,
                'codice_franchigia': 'trasferta_estero',
                'descrizione': (
                    'Diaria trasferta Estero: esente fino a €77,47/giorno. '
                    'La parte eccedente è imponibile INPS + INAIL + IRPEF.'
                ),
                'riferimento_normativo': 'Art. 51 c. 5 TUIR',
            },
            {
                'codice': 'FRINGE_BENEFIT',
                'nome': 'Fringe benefit (generico)',
                'categoria': 'fringe_benefit',
                'imponibile_inps': True, 'imponibile_inail': True, 'imponibile_irpef': True,
                'imponibile_parziale': True,
                'codice_franchigia': 'fringe_benefit_generale',
                'descrizione': (
                    'Benefit aziendali in natura (prestiti a tasso agevolato, '
                    'alloggio, assicurazioni vita, ecc.): esenti fino a €258,23/anno '
                    '(senza figli a carico) o €1.000/anno (con figli). '
                    'L\'eccedenza è interamente imponibile.'
                ),
                'riferimento_normativo': 'Art. 51 c. 3 TUIR — L. 207/2024 art. 1 c. 390',
            },
            {
                'codice': 'AUTO_AZIENDALE',
                'nome': 'Auto aziendale uso promiscuo (fringe benefit ACI)',
                'categoria': 'auto_aziendale',
                'imponibile_inps': True, 'imponibile_inail': True, 'imponibile_irpef': True,
                'imponibile_parziale': False,
                'descrizione': (
                    'Benefit calcolato su tabelle ACI: costo_km × 15.000 km × % CO₂. '
                    'Percentuali CO₂: ≤60 g/km=25%, 61-160=30%, 161-190=50%, >190=60%. '
                    'Il valore è interamente imponibile (non ha franchigia separata, '
                    'ma rientra nel plafond fringe benefit annuo).'
                ),
                'riferimento_normativo': (
                    'Art. 51 c. 4 lett. a TUIR — Circ. AE 48/E/1998 — L. 160/2019 art. 1 c. 632'
                ),
            },

            # ── GRUPPO 3: Voci imponibili SOLO IRPEF (non INPS/INAIL) ──────────
            {
                'codice': 'IND_SOST_PREAVVISO',
                'nome': 'Indennità sostitutiva del preavviso (cessazione rapporto)',
                'categoria': 'preavviso',
                'imponibile_inps': False, 'imponibile_inail': False, 'imponibile_irpef': True,
                'descrizione': (
                    'Somma erogata in sostituzione del preavviso lavorato alla cessazione del '
                    'rapporto. NON imponibile INPS/INAIL (circ. INPS 263/1994). '
                    'Imponibile IRPEF con tassazione separata (art. 17 TUIR).'
                ),
                'riferimento_normativo': (
                    'Art. 17 c. 1 lett. a TUIR — Circ. INPS 263/1994 — Cass. 14/06/2012 n.9790'
                ),
            },
            {
                'codice': 'INCENTIVO_ESODO',
                'nome': 'Incentivo all\'esodo / Accordo risolutivo consensuale',
                'categoria': 'incentivo_esodo',
                'imponibile_inps': False, 'imponibile_inail': False, 'imponibile_irpef': True,
                'descrizione': (
                    'Somme corrisposte per incentivare la risoluzione consensuale del rapporto. '
                    'NON imponibili INPS (art. 12 c. 4 lett. b L. 153/1969). '
                    'Soggette a tassazione separata IRPEF (art. 17 c. 1 lett. a TUIR). '
                    'Attenzione: sono distinte dalla normale buonuscita/TFR.'
                ),
                'riferimento_normativo': (
                    'Art. 17 c. 1 lett. a TUIR — Art. 12 c. 4 L. 153/1969'
                ),
            },

            # ── GRUPPO 4: Voci NON imponibili (esenti da tutto) ──────────────
            {
                'codice': 'TICKET_CARTACEO',
                'nome': 'Ticket restaurant cartaceo (entro soglia €4,00/giorno)',
                'categoria': 'ticket_restaurant',
                'imponibile_inps': False, 'imponibile_inail': False, 'imponibile_irpef': False,
                'imponibile_parziale': True,
                'codice_franchigia': 'ticket_cartaceo',
                'descrizione': (
                    'Buoni pasto cartacei: esenti fino a €4,00 per pasto. '
                    'L\'eccedenza (giornaliera) diventa imponibile IRPEF. '
                    'Per prassi INPS: non imponibili anche sull\'eccedenza (interpretazione '
                    'prevalente — verificare circolare di riferimento).'
                ),
                'riferimento_normativo': 'Art. 51 c. 2 lett. c TUIR — L. 213/2023',
            },
            {
                'codice': 'TICKET_ELETTRONICO',
                'nome': 'Ticket restaurant elettronico (entro soglia €8,00/giorno)',
                'categoria': 'ticket_restaurant',
                'imponibile_inps': False, 'imponibile_inail': False, 'imponibile_irpef': False,
                'imponibile_parziale': True,
                'codice_franchigia': 'ticket_elettronico',
                'descrizione': (
                    'Buoni pasto elettronici: esenti fino a €8,00 per pasto. '
                    'Limite raddoppiato rispetto ai cartacei (L. 213/2023).'
                ),
                'riferimento_normativo': 'Art. 51 c. 2 lett. c TUIR — L. 213/2023',
            },
            {
                'codice': 'RIMBORSO_KM',
                'nome': 'Rimborso km ACI per uso auto propria (entro tabella)',
                'categoria': 'rimborso_km',
                'imponibile_inps': False, 'imponibile_inail': False, 'imponibile_irpef': False,
                'descrizione': (
                    'Rimborso per uso dell\'auto propria del dipendente per trasferte di lavoro, '
                    'calcolato entro i limiti delle tabelle ACI aggiornate annualmente. '
                    'Esente da INPS, INAIL e IRPEF (circ. AE 326/E/1997). '
                    'La parte eccedente le tariffe ACI diventa imponibile IRPEF.'
                ),
                'riferimento_normativo': 'Art. 51 c. 5 TUIR — Circ. AE 326/E/1997',
            },
            {
                'codice': 'RIMBORSO_SPESE',
                'nome': 'Rimborso spese documentato (piè di lista)',
                'categoria': 'rimborso_documentato',
                'imponibile_inps': False, 'imponibile_inail': False, 'imponibile_irpef': False,
                'descrizione': (
                    'Rimborso esatto di spese documentate da fatture/scontrini intestati '
                    'all\'azienda. Completamente esente da qualsiasi imposizione. '
                    'Richiede documentazione analitica.'
                ),
                'riferimento_normativo': 'Art. 51 c. 5 TUIR',
            },
            {
                'codice': 'TI_DL3_2020',
                'nome': 'Trattamento Integrativo DL 3/2020 (ex Bonus Renzi)',
                'categoria': 'bonus_fiscale',
                'imponibile_inps': False, 'imponibile_inail': False, 'imponibile_irpef': False,
                'descrizione': (
                    'Bonus €100/mese per redditi ≤ €15.000, decrescente fino a €28.000. '
                    'Completamente esente: non concorre a INPS, INAIL, IRPEF, 13ª, 14ª, TFR. '
                    'L\'azienda lo anticipa e recupera in F24 (nessun costo netto per l\'azienda).'
                ),
                'riferimento_normativo': 'Art. 1 L. 21/2020 — DL 3/2020',
            },
            {
                'codice': 'BONUS_L207_2024',
                'nome': 'Bonus Art. 1 c. 4 L. 207/2024 (indennità aggiuntiva)',
                'categoria': 'bonus_fiscale',
                'imponibile_inps': False, 'imponibile_inail': False, 'imponibile_irpef': False,
                'descrizione': (
                    'Indennità aggiuntiva per redditi ≤ €20.000 (~€70,82/mese). '
                    'Completamente esente: non concorre a INPS, INAIL, IRPEF, 13ª, 14ª, TFR. '
                    'L\'azienda lo anticipa e recupera in F24.'
                ),
                'riferimento_normativo': 'Art. 1 c. 4 L. 207/2024',
            },
            {
                'codice': 'PREV_COMPL_AZ',
                'nome': 'Contributo fondo pensione complementare (quota azienda)',
                'categoria': 'previdenza_complementare',
                'imponibile_inps': True, 'imponibile_inail': False, 'imponibile_irpef': False,
                'descrizione': (
                    'Contributo versato dall\'azienda a fondi di previdenza complementare. '
                    'INPS: imponibile oltre la soglia CCNL / oltre i limiti art. 8 D.Lgs. 252/2005. '
                    'IRPEF: NON imponibile fino a €5.164,57/anno (art. 10 c. 1 lett. e-bis TUIR); '
                    'la quota eccedente diventa reddito del dipendente. '
                    'INAIL: non imponibile.'
                ),
                'riferimento_normativo': 'Art. 10 c. 1 lett. e-bis TUIR — D.Lgs. 252/2005 art. 8',
            },
        ]

        creati = aggiornati = 0
        for v in voci:
            codice = v.pop('codice')
            obj, created = VoceRetributiva.objects.get_or_create(
                codice=codice, defaults=v
            )
            if not created and forza:
                for k, val in v.items():
                    setattr(obj, k, val)
                obj.save()
                aggiornati += 1
            elif created:
                creati += 1
            else:
                aggiornati += 1

        self.stdout.write(
            f'  Voci retributive: {creati} create, {aggiornati} già presenti'
        )

    # ─────────────────────────────────────────────────────────────────────────
    # MINIMALI/MASSIMALI INAIL
    # ─────────────────────────────────────────────────────────────────────────
    def _seed_inail_parametri(self, anno, forza):
        from rapporto_di_lavoro.models import InailParametro, CCNL

        # Cerca il CCNL Turismo/FIPE nel DB
        ccnl_obj = (
            CCNL.objects.filter(attivo=True)
            .filter(nome__icontains='turismo')
            .order_by('-anno_inizio_validita')
            .first()
        )
        if not ccnl_obj:
            ccnl_obj = CCNL.objects.filter(attivo=True).order_by('-anno_inizio_validita').first()

        if not ccnl_obj:
            self.stdout.write(self.style.WARNING(
                '  ⚠️  Nessun CCNL attivo trovato. Skip seed InailParametro.'
            ))
            return

        # Valori 2025 (circ. INAIL aggiornata annualmente)
        # Minimale giornaliero = minimale annuo / 300 (convenzionali)
        # Minimale annuo INAIL 2025: ~€15.746 → giornaliero ≈ €52,49
        dati = {
            'ccnl': ccnl_obj,
            'anno': anno,
            'retribuzione_giornaliera_minima': Decimal('52.48'),   # circ. INAIL 2025
            'retribuzione_giornaliera_massima': None,              # nessun massimale giornaliero per ristorazione
            'retribuzione_annua_massima': None,
            'retribuzione_convenzionale_giornaliera': None,
            'data_validita_da': parse_date(f'{anno}-01-01'),
            'data_validita_a': parse_date(f'{anno}-12-31'),
            'descrizione': (
                f'Parametri INAIL {anno} per CCNL Turismo/FIPE. '
                'Minimale giornaliero da circolare INAIL annuale. '
                'Se la retribuzione giornaliera effettiva è inferiore al minimale, '
                'il premio INAIL si calcola sul minimale.'
            ),
        }

        obj, created = InailParametro.objects.get_or_create(
            ccnl=ccnl_obj,
            anno=anno,
            data_validita_da=parse_date(f'{anno}-01-01'),
            defaults=dati,
        )
        if not created and forza:
            for k, v in dati.items():
                setattr(obj, k, v)
            obj.save()
            self.stdout.write(f'  Parametro INAIL {anno}: aggiornato')
        elif created:
            self.stdout.write(f'  Parametro INAIL {anno}: creato per {ccnl_obj}')
        else:
            self.stdout.write(f'  Parametro INAIL {anno}: già presente per {ccnl_obj}')
