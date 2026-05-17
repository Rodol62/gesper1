"""
Management command: fix_db_foundation_2026
==========================================
Corregge e integra tutti i dati DB necessari per il corretto funzionamento
del flusso simulazione → proposta → contratto.

Interventi:
  1. ParametroCCNLTurismo  — imposta decorrenza_validita_a su tutti i 40 record
  2. BonusFiscale          — aggiunge record 2026 (TI DL3/2020 + Bonus L207)
  3. INAILParametro        — aggiunge record 2026
  4. RegolaNormativaCCNL   — aggiunge livelli Qa e Qb mancanti
  5. VoceRetributiva       — disattiva i duplicati PAGA_BASE e SCATTO_ANZ
  6. ScaglioneIRPEF        — aggiorna detrazione base 2025 al valore corretto €1.955
  7. AddizionaleRegionale  — verifica/aggiunge Sicilia 2026 se assente

Esecuzione:
  python manage.py fix_db_foundation_2026
  python manage.py fix_db_foundation_2026 --dry-run   # solo anteprima
"""

from datetime import date
from decimal import Decimal

from django.core.management.base import BaseCommand
from django.db import transaction


class Command(BaseCommand):
    help = 'Corregge e integra i dati DB fondazione 2026 per simulazioni e proposte'

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Mostra cosa verrebbe fatto senza modificare il DB',
        )

    def handle(self, *args, **options):
        self.dry = options['dry_run']
        self.ok = 0
        self.skip = 0
        self.warn = 0

        self._header('FIX DB FOUNDATION 2026')
        if self.dry:
            self.stdout.write(self.style.WARNING('  *** DRY-RUN — nessuna modifica verrà salvata ***\n'))

        try:
            with transaction.atomic():
                self._fix_ccnl_date_fine()
                self._fix_bonus_fiscale_2026()
                self._fix_inail_2026()
                self._fix_regole_normative_qa_qb()
                self._fix_voce_retributiva_duplicati()
                self._fix_scaglioni_irpef_detrazione()
                self._fix_addizionale_regionale_2026()

                if self.dry:
                    transaction.set_rollback(True)
        except Exception as exc:
            self.stdout.write(self.style.ERROR(f'\nErrore durante l\'esecuzione: {exc}'))
            raise

        self._footer()

    # ─────────────────────────────────────────────────────────────────────────
    # 1. ParametroCCNLTurismo — date fine validità
    # ─────────────────────────────────────────────────────────────────────────
    def _fix_ccnl_date_fine(self):
        self._section('1. ParametroCCNLTurismo — decorrenza_validita_a')
        from rapporto_di_lavoro.models import ParametroCCNLTurismo

        # Mappa versione → data fine (la successiva versione inizia il giorno dopo)
        # Struttura CCNL FIPE 2024-2026 con aumenti tabulari:
        #   2025-06-01 → in vigore fino al 31/05/2026
        #   2026-06-01 → in vigore fino al 30/11/2027
        #   2027-06-01 → in vigore fino al 30/11/2027
        #   2027-12-01 → ancora aperta (ultimo aumento previsto, nessuna data fine)
        mappa_date = {
            '2025-06': date(2026, 5, 31),
            '2026-06': date(2027, 5, 31),
            '2027-06': date(2027, 11, 30),
            '2027-12': None,   # data aperta — aumento finale del contratto
        }

        for versione, data_fine in mappa_date.items():
            qs = ParametroCCNLTurismo.objects.filter(versione=versione)
            count = qs.count()
            if count == 0:
                self._warn(f'Versione {versione}: nessun record trovato')
                continue

            attuali_errati = qs.exclude(decorrenza_validita_a=data_fine).count()
            if attuali_errati == 0:
                self._skip(f'Versione {versione}: {count} record già corretti')
                continue

            label_fine = data_fine.isoformat() if data_fine else 'NULL (aperta)'
            self._log(f'Versione {versione}: imposto decorrenza_validita_a = {label_fine} su {count} record')
            if not self.dry:
                qs.update(decorrenza_validita_a=data_fine)
            self.ok += count

    # ─────────────────────────────────────────────────────────────────────────
    # 2. BonusFiscale — aggiunge record 2026
    # ─────────────────────────────────────────────────────────────────────────
    def _fix_bonus_fiscale_2026(self):
        self._section('2. BonusFiscale — estendi validità al 2026')
        from rapporto_di_lavoro.models import BonusFiscale

        # Il campo codice ha unique=True globale: non si possono creare nuovi record
        # con lo stesso codice. I valori 2025 sono invariati per il 2026, quindi
        # si estende data_validita_a al 31/12/2026.
        bonus_da_aggiornare = [
            {
                'codice': 'TI_DL3_2020',
                'nuova_data_fine': date(2026, 12, 31),
                'note': '€101.92/mese — valori invariati per il 2026',
            },
            {
                'codice': 'BONUS_L207_2024',
                'nuova_data_fine': date(2026, 12, 31),
                'note': '€70.82/mese — valori invariati per il 2026',
            },
        ]

        for dati in bonus_da_aggiornare:
            try:
                bonus = BonusFiscale.objects.get(codice=dati['codice'])
            except BonusFiscale.DoesNotExist:
                self._warn(f"BonusFiscale {dati['codice']}: non trovato in DB")
                continue

            if bonus.data_validita_a == dati['nuova_data_fine']:
                self._skip(f"BonusFiscale {dati['codice']}: data_validita_a già = {dati['nuova_data_fine']}")
                continue

            self._log(
                f"BonusFiscale {dati['codice']}: "
                f"data_validita_a {bonus.data_validita_a} → {dati['nuova_data_fine']}  [{dati['note']}]"
            )
            if not self.dry:
                bonus.data_validita_a = dati['nuova_data_fine']
                bonus.save(update_fields=['data_validita_a'])
            self.ok += 1

    # ─────────────────────────────────────────────────────────────────────────
    # 3. INAILParametro — aggiunge record 2026
    # ─────────────────────────────────────────────────────────────────────────
    def _fix_inail_2026(self):
        self._section('3. INAILParametro — record 2026')
        from rapporto_di_lavoro.models import InailParametro as INAILParametro, CCNL

        try:
            ccnl_fipe = CCNL.objects.get(sigla='FIPE')
        except CCNL.DoesNotExist:
            self._warn('CCNL FIPE non trovato — salto INAILParametro 2026')
            return

        esiste = INAILParametro.objects.filter(ccnl=ccnl_fipe, anno=2026).exists()
        if esiste:
            self._skip('INAILParametro 2026: già presente')
            return

        self._log(
            'Creo INAILParametro 2026 — minimale giornaliero €52.48 '
            '(confermato per 2026, aggiornare se INAIL pubblica nuova circolare)'
        )
        if not self.dry:
            INAILParametro.objects.create(
                ccnl=ccnl_fipe,
                anno=2026,
                retribuzione_giornaliera_minima=Decimal('52.48'),
                retribuzione_giornaliera_massima=None,
                retribuzione_annua_massima=None,
                retribuzione_convenzionale_giornaliera=None,
                data_validita_da=date(2026, 1, 1),
                data_validita_a=date(2026, 12, 31),
                descrizione=(
                    'Parametri INAIL 2026 per CCNL Turismo/FIPE. '
                    'Minimale giornaliero da circolare INAIL annuale. '
                    'Se la retribuzione giornaliera effettiva è inferiore '
                    'al minimale, il premio INAIL si calcola sul minimale.'
                ),
                attivo=True,
            )
        self.ok += 1

    # ─────────────────────────────────────────────────────────────────────────
    # 4. RegolaNormativaCCNL — aggiunge Qa e Qb
    # ─────────────────────────────────────────────────────────────────────────
    def _fix_regole_normative_qa_qb(self):
        self._section('4. RegolaNormativaCCNL — livelli Qa e Qb')
        from rapporto_di_lavoro.models import RegolaNormativaCCNL

        # Qa e Qb (quadri) hanno lo stesso orario dei livelli normali
        # ma ferie a 30 giorni (quadri hanno ferie aggiuntive per CCNL Turismo)
        livelli_quadro = [
            {
                'livello': 'Qa',
                'note': 'Quadro tipo A — dirigenziale/responsabile di area',
                'ferie_annue_giorni': Decimal('30'),
                'permessi_annui_ore': Decimal('88'),
                'scatto_periodicita_mesi': 36,
                'numero_scatti_massimi': 6,
            },
            {
                'livello': 'Qb',
                'note': 'Quadro tipo B — responsabile di settore',
                'ferie_annue_giorni': Decimal('28'),
                'permessi_annui_ore': Decimal('80'),
                'scatto_periodicita_mesi': 36,
                'numero_scatti_massimi': 6,
            },
        ]

        for dati_livello in livelli_quadro:
            livello = dati_livello['livello']
            esiste = RegolaNormativaCCNL.objects.filter(
                ccnl='Turismo Confcommercio',
                versione='2024-2026',
                livello=livello,
            ).exists()
            if esiste:
                self._skip(f'RegolaNormativaCCNL livello {livello}: già presente')
                continue

            self._log(f'Creo RegolaNormativaCCNL livello {livello}')
            if not self.dry:
                RegolaNormativaCCNL.objects.create(
                    ccnl='Turismo Confcommercio',
                    versione='2024-2026',
                    sezione='ristoranti_pizzerie',
                    livello=livello,
                    decorrenza_validita_da=date(2024, 1, 1),
                    decorrenza_validita_a=None,
                    ore_settimanali=Decimal('40'),
                    ore_mensili=Decimal('173.33'),
                    ore_giornaliere=Decimal('8'),
                    ferie_annue_giorni=dati_livello['ferie_annue_giorni'],
                    permessi_annui_ore=dati_livello['permessi_annui_ore'],
                    scatto_periodicita_mesi=dati_livello['scatto_periodicita_mesi'],
                    scatto_importo=Decimal('0'),
                    numero_scatti_massimi=dati_livello['numero_scatti_massimi'],
                    note=dati_livello['note'],
                    attivo=True,
                )
            self.ok += 1

    # ─────────────────────────────────────────────────────────────────────────
    # 5. VoceRetributiva — disattiva duplicati
    # ─────────────────────────────────────────────────────────────────────────
    def _fix_voce_retributiva_duplicati(self):
        self._section('5. VoceRetributiva — disattivazione duplicati')
        from rapporto_di_lavoro.models import VoceRetributiva

        # PAGA_BASE (id legacy) → sostituita da MINIMO_TABELLARE (usata in ParametroVoceRetributiva)
        # SCATTO_ANZ (id legacy) → sostituita da SCATTO_ANZIANITA
        codici_da_disattivare = {
            'PAGA_BASE': 'Sostituita da MINIMO_TABELLARE (voce usata in ParametroVoceRetributiva)',
            'SCATTO_ANZ': 'Sostituita da SCATTO_ANZIANITA (voce usata in ParametroVoceRetributiva)',
        }

        for codice, motivo in codici_da_disattivare.items():
            try:
                voce = VoceRetributiva.objects.get(codice=codice)
            except VoceRetributiva.DoesNotExist:
                self._skip(f'VoceRetributiva {codice}: non trovata')
                continue

            if not voce.attivo:
                self._skip(f'VoceRetributiva {codice}: già inattiva')
                continue

            self._log(f'Disattivo VoceRetributiva {codice} — {motivo}')
            if not self.dry:
                voce.attivo = False
                voce.save(update_fields=['attivo', 'data_modifica'])
            self.ok += 1

    # ─────────────────────────────────────────────────────────────────────────
    # 6. ScaglioneIRPEF — aggiorna detrazione base al valore 2025 corretto
    # ─────────────────────────────────────────────────────────────────────────
    def _fix_scaglioni_irpef_detrazione(self):
        self._section('6. ScaglioneIRPEF — detrazione base 2025')
        from rapporto_di_lavoro.models import ScaglioneIRPEF

        # L.207/2024 porta la detrazione base (redditi ≤ 15.000€) a €1.955
        # Il DB ha il primo scaglione (0-28.000, 23%) con detrazione_base_annua=1955 — già corretto
        # Il secondo scaglione (28.000-50.000, 35%) ha detrazione=1910 — valore base progressiva, corretto
        # Verifica coerenza e aggiorna se necessario
        correzioni = [
            # (scaglione_numero, aliquota_attesa, detrazione_corretta, descrizione)
            (1, Decimal('23'), Decimal('1955'), '€1.955 — valore aggiornato L.207/2024 per redditi ≤15.000€'),
            (2, Decimal('35'), Decimal('1910'), '€1.910 — base formula progressiva 15.001-28.000€'),
            (3, Decimal('43'), Decimal('0'),    '€0 — nessuna detrazione oltre 50.000€'),
        ]

        for num, aliquota, detrazione_corretta, desc in correzioni:
            try:
                scaglione = ScaglioneIRPEF.objects.get(
                    anno=2025, scaglione_numero=num
                )
            except ScaglioneIRPEF.DoesNotExist:
                self._warn(f'ScaglioneIRPEF 2025 n.{num}: non trovato')
                continue

            if scaglione.detrazione_base_annua == detrazione_corretta:
                self._skip(f'ScaglioneIRPEF 2025 n.{num} ({aliquota}%): detrazione già corretta ({detrazione_corretta}€)')
                continue

            self._log(
                f'ScaglioneIRPEF 2025 n.{num} ({aliquota}%): '
                f'detrazione {scaglione.detrazione_base_annua} → {detrazione_corretta}€  [{desc}]'
            )
            if not self.dry:
                scaglione.detrazione_base_annua = detrazione_corretta
                scaglione.save(update_fields=['detrazione_base_annua'])
            self.ok += 1

    # ─────────────────────────────────────────────────────────────────────────
    # 7. AddizionaleRegionale — verifica Sicilia 2026
    # ─────────────────────────────────────────────────────────────────────────
    def _fix_addizionale_regionale_2026(self):
        self._section('7. AddizionaleRegionale — Sicilia 2026')
        from rapporto_di_lavoro.models import AddizionaleRegionale

        esiste = AddizionaleRegionale.objects.filter(
            regione__iexact='Sicilia', anno=2026
        ).exists()
        if esiste:
            self._skip('AddizionaleRegionale Sicilia 2026: già presente')
            return

        self._log('Creo AddizionaleRegionale Sicilia 2026 — aliquota 1,23% (invariata)')
        if not self.dry:
            AddizionaleRegionale.objects.create(
                regione='Sicilia',
                anno=2026,
                aliquota=Decimal('1.23'),
                soglia_esenzione=None,
                data_validita_da=date(2026, 1, 1),
                data_validita_a=date(2026, 12, 31),
                attivo=True,
            )
        self.ok += 1

    # ─────────────────────────────────────────────────────────────────────────
    # Utility output
    # ─────────────────────────────────────────────────────────────────────────
    def _header(self, titolo):
        self.stdout.write(self.style.SUCCESS('\n' + '=' * 62))
        self.stdout.write(self.style.SUCCESS(f'  {titolo}'))
        self.stdout.write(self.style.SUCCESS('=' * 62 + '\n'))

    def _section(self, titolo):
        self.stdout.write(self.style.HTTP_INFO(f'\n▶ {titolo}'))

    def _log(self, msg):
        prefisso = '  [DRY] ' if self.dry else '  [OK]  '
        self.stdout.write(self.style.SUCCESS(f'{prefisso}{msg}'))

    def _skip(self, msg):
        self.stdout.write(f'  [--]  {msg}')
        self.skip += 1

    def _warn(self, msg):
        self.stdout.write(self.style.WARNING(f'  [!!]  {msg}'))
        self.warn += 1

    def _footer(self):
        self.stdout.write(self.style.SUCCESS('\n' + '=' * 62))
        stato = 'ANTEPRIMA (nessuna modifica salvata)' if self.dry else 'COMPLETATO'
        self.stdout.write(self.style.SUCCESS(f'  {stato}'))
        self.stdout.write(f'  Operazioni eseguite : {self.ok}')
        self.stdout.write(f'  Già corretti (skip) : {self.skip}')
        if self.warn:
            self.stdout.write(self.style.WARNING(f'  Avvisi              : {self.warn}'))
        self.stdout.write(self.style.SUCCESS('=' * 62 + '\n'))
