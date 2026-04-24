"""
Management command per popolare TUTTI i Parametri CCNL Turismo dal PDF 'tabelle retributive.pdf'
Include tutte e 4 le versioni temporali: giu-25, giu-26, giu-27, dic-27
"""
from django.core.management.base import BaseCommand
from rapporto_di_lavoro.models import ParametroCCNLTurismo
from datetime import date
from decimal import Decimal


class Command(BaseCommand):
    help = 'Popola i parametri CCNL FIPE Pubblici Esercizi con tutte le tabelle retributive dal PDF'

    def handle(self, *args, **options):
        # Dati estratti dal PDF "tabelle retributive.pdf"
        
        # GIUGNO 2025
        tabella_giu_25 = [
            {'livello': 'Qa', 'contingenza': '542.70', 'paga_base': '1854.49', 'paga_ridotta': '1848.81', 'totale': '2391.51', 'riduzione': '5.68'},
            {'livello': 'Qb', 'contingenza': '537.59', 'paga_base': '1674.63', 'paga_ridotta': '1669.47', 'totale': '2207.06', 'riduzione': '5.16'},
            {'livello': '1', 'contingenza': '536.71', 'paga_base': '1517.16', 'paga_ridotta': '1512.00', 'totale': '2048.71', 'riduzione': '5.16'},
            {'livello': '2', 'contingenza': '531.59', 'paga_base': '1337.33', 'paga_ridotta': '1332.94', 'totale': '1864.53', 'riduzione': '4.39'},
            {'livello': '3', 'contingenza': '528.26', 'paga_base': '1228.88', 'paga_ridotta': '1225.01', 'totale': '1753.27', 'riduzione': '3.87'},
            {'livello': '4', 'contingenza': '524.94', 'paga_base': '1127.75', 'paga_ridotta': '1124.39', 'totale': '1649.33', 'riduzione': '3.36'},
            {'livello': '5', 'contingenza': '522.37', 'paga_base': '1021.49', 'paga_ridotta': '1018.39', 'totale': '1540.76', 'riduzione': '3.10'},
            {'livello': '6S', 'contingenza': '520.64', 'paga_base': '960.13', 'paga_ridotta': '957.29', 'totale': '1477.93', 'riduzione': '2.84'},
            {'livello': '6', 'contingenza': '520.51', 'paga_base': '937.80', 'paga_ridotta': '934.96', 'totale': '1455.47', 'riduzione': '2.84'},
            {'livello': '7', 'contingenza': '518.45', 'paga_base': '841.89', 'paga_ridotta': '839.31', 'totale': '1357.76', 'riduzione': '2.58'},
        ]
        
        # GIUGNO 2026
        tabella_giu_26 = [
            {'livello': 'Qa', 'contingenza': '542.70', 'paga_base': '1920.26', 'paga_ridotta': '1914.58', 'totale': '2457.28', 'riduzione': '5.68'},
            {'livello': 'Qb', 'contingenza': '537.59', 'paga_base': '1734.02', 'paga_ridotta': '1728.86', 'totale': '2266.45', 'riduzione': '5.16'},
            {'livello': '1', 'contingenza': '536.71', 'paga_base': '1570.97', 'paga_ridotta': '1565.81', 'totale': '2102.52', 'riduzione': '5.16'},
            {'livello': '2', 'contingenza': '531.59', 'paga_base': '1384.76', 'paga_ridotta': '1380.37', 'totale': '1911.96', 'riduzione': '4.39'},
            {'livello': '3', 'contingenza': '528.26', 'paga_base': '1272.47', 'paga_ridotta': '1268.60', 'totale': '1796.86', 'riduzione': '3.87'},
            {'livello': '4', 'contingenza': '524.94', 'paga_base': '1167.75', 'paga_ridotta': '1164.39', 'totale': '1689.33', 'riduzione': '3.36'},
            {'livello': '5', 'contingenza': '522.37', 'paga_base': '1057.72', 'paga_ridotta': '1054.62', 'totale': '1576.99', 'riduzione': '3.10'},
            {'livello': '6S', 'contingenza': '520.64', 'paga_base': '994.19', 'paga_ridotta': '991.35', 'totale': '1511.99', 'riduzione': '2.84'},
            {'livello': '6', 'contingenza': '520.51', 'paga_base': '971.06', 'paga_ridotta': '968.22', 'totale': '1488.73', 'riduzione': '2.84'},
            {'livello': '7', 'contingenza': '518.45', 'paga_base': '871.75', 'paga_ridotta': '869.17', 'totale': '1387.62', 'riduzione': '2.58'},
        ]
        
        # GIUGNO 2027
        tabella_giu_27 = [
            {'livello': 'Qa', 'contingenza': '542.70', 'paga_base': '1969.60', 'paga_ridotta': '1963.92', 'totale': '2506.62', 'riduzione': '5.68'},
            {'livello': 'Qb', 'contingenza': '537.59', 'paga_base': '1778.57', 'paga_ridotta': '1773.41', 'totale': '2311.00', 'riduzione': '5.16'},
            {'livello': '1', 'contingenza': '536.71', 'paga_base': '1611.33', 'paga_ridotta': '1606.17', 'totale': '2142.88', 'riduzione': '5.16'},
            {'livello': '2', 'contingenza': '531.59', 'paga_base': '1420.33', 'paga_ridotta': '1415.94', 'totale': '1947.53', 'riduzione': '4.39'},
            {'livello': '3', 'contingenza': '528.26', 'paga_base': '1305.16', 'paga_ridotta': '1301.29', 'totale': '1829.55', 'riduzione': '3.87'},
            {'livello': '4', 'contingenza': '524.94', 'paga_base': '1197.75', 'paga_ridotta': '1194.39', 'totale': '1719.33', 'riduzione': '3.36'},
            {'livello': '5', 'contingenza': '522.37', 'paga_base': '1084.89', 'paga_ridotta': '1081.79', 'totale': '1604.16', 'riduzione': '3.10'},
            {'livello': '6S', 'contingenza': '520.64', 'paga_base': '1019.73', 'paga_ridotta': '1016.89', 'totale': '1537.53', 'riduzione': '2.84'},
            {'livello': '6', 'contingenza': '520.51', 'paga_base': '996.01', 'paga_ridotta': '993.17', 'totale': '1513.68', 'riduzione': '2.84'},
            {'livello': '7', 'contingenza': '518.45', 'paga_base': '894.14', 'paga_ridotta': '891.56', 'totale': '1410.01', 'riduzione': '2.58'},
        ]
        
        # DICEMBRE 2027
        tabella_dic_27 = [
            {'livello': 'Qa', 'contingenza': '542.70', 'paga_base': '2035.37', 'paga_ridotta': '2029.69', 'totale': '2572.39', 'riduzione': '5.68'},
            {'livello': 'Qb', 'contingenza': '537.59', 'paga_base': '1837.97', 'paga_ridotta': '1832.81', 'totale': '2370.40', 'riduzione': '5.16'},
            {'livello': '1', 'contingenza': '536.71', 'paga_base': '1665.14', 'paga_ridotta': '1659.98', 'totale': '2196.69', 'riduzione': '5.16'},
            {'livello': '2', 'contingenza': '531.59', 'paga_base': '1467.77', 'paga_ridotta': '1463.38', 'totale': '1994.97', 'riduzione': '4.39'},
            {'livello': '3', 'contingenza': '528.26', 'paga_base': '1348.74', 'paga_ridotta': '1344.87', 'totale': '1873.13', 'riduzione': '3.87'},
            {'livello': '4', 'contingenza': '524.94', 'paga_base': '1237.75', 'paga_ridotta': '1234.39', 'totale': '1759.33', 'riduzione': '3.36'},
            {'livello': '5', 'contingenza': '522.37', 'paga_base': '1121.13', 'paga_ridotta': '1118.03', 'totale': '1640.40', 'riduzione': '3.10'},
            {'livello': '6S', 'contingenza': '520.64', 'paga_base': '1053.78', 'paga_ridotta': '1050.94', 'totale': '1571.58', 'riduzione': '2.84'},
            {'livello': '6', 'contingenza': '520.51', 'paga_base': '1029.27', 'paga_ridotta': '1026.43', 'totale': '1546.94', 'riduzione': '2.84'},
            {'livello': '7', 'contingenza': '518.45', 'paga_base': '924.00', 'paga_ridotta': '921.42', 'totale': '1439.87', 'riduzione': '2.58'},
        ]
        
        # Dizionario con tutte le versioni
        versioni = {
            '2025-06': {'data': date(2025, 6, 1), 'tabella': tabella_giu_25},
            '2026-06': {'data': date(2026, 6, 1), 'tabella': tabella_giu_26},
            '2027-06': {'data': date(2027, 6, 1), 'tabella': tabella_giu_27},
            '2027-12': {'data': date(2027, 12, 1), 'tabella': tabella_dic_27},
        }
        
        totale_creati = 0
        totale_aggiornati = 0
        
        for versione_key, versione_data in versioni.items():
            tabella = versione_data['tabella']
            data_rilevazione = versione_data['data']
            
            self.stdout.write(self.style.WARNING(f'\n📋 Importazione versione {versione_key}...'))
            
            for idx, riga in enumerate(tabella, start=1):
                livello = riga['livello']
                # I valori sono già in formato corretto: 1517.16 = 1517,16 euro
                minimo = Decimal(riga['paga_base'])
                contingenza = Decimal(riga['contingenza'])
                totale = Decimal(riga['totale'])
                
                # Dati di base
                defaults = {
                    'sezione': 'Pubblici Esercizi',
                    'qualifica': f'Livello {livello}',
                    'tipo_contratto_nazionale': 'CCNL FIPE',
                    'paga_base_mensile': minimo,
                    'contingenza_mensile': contingenza,
                    'importo_lordo_mensile': totale,
                    'indennita_mensile': Decimal('0.00'),
                    'ore_settimanali': Decimal('40.00'),
                    'ore_mensili': Decimal('173.33'),
                    'ore_giornaliere': Decimal('8.00'),
                    'edr_mensile': Decimal('10.33'),
                    'note': f'Dati da tabelle retributive.pdf - versione {versione_key}',
                    'attivo': True,
                    'decorrenza_validita_da': data_rilevazione,
                    'decorrenza_validita_a': None,
                    # Nuovi campi struttura tabella retributiva
                    'livello_ordinamento': idx,
                    'minimo_tabellare': minimo,
                    'totale_tabellare': totale,
                    'fonte_tabella': 'tabelle retributive.pdf',
                    'data_rilevazione_tabella': data_rilevazione,
                }
                
                obj, created = ParametroCCNLTurismo.objects.update_or_create(
                    ccnl='FIPE Pubblici Esercizi',
                    versione=versione_key,
                    livello=livello,
                    defaults=defaults
                )
                
                if created:
                    totale_creati += 1
                    self.stdout.write(f'  ✓ Creato livello {livello}')
                else:
                    totale_aggiornati += 1
                    self.stdout.write(f'  ↻ Aggiornato livello {livello}')
        
        self.stdout.write(
            self.style.SUCCESS(
                f'\n✓ Import completato: {totale_creati} record creati, '
                f'{totale_aggiornati} aggiornati, '
                f'totale livelli: {totale_creati + totale_aggiornati}'
            )
        )
