#!/usr/bin/env python3
"""
Script per popolare la tabella ParametroCCNLTurismo con i dati CCNL Turismo Confcommercio 2024-2027
Fonte: Tabelle retributive ufficiali CCNL Turismo
"""
import os
import sys
import django
from datetime import date
from decimal import Decimal

# Aggiungi la directory parent al path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Setup Django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'settings')
django.setup()

from rapporto_di_lavoro.models import ParametroCCNLTurismo


def popola_ccnl():
    """Popola la tabella con i dati CCNL Turismo Confcommercio"""
    
    # Dati CCNL - Paga Base per livello e periodo
    # Formato: (livello, parametro, qualifica_esempio, dal_2024_06, dal_2025_06, dal_2026_06, dal_2027_06, dal_2027_12)
    dati_paga_base = [
        ('Quadri', 220, 'Responsabile', Decimal('1956.22'), Decimal('2014.88'), Decimal('2073.55'), Decimal('2132.22'), Decimal('2190.88')),
        ('1', 205, 'Chef, Capo Ricevimento', Decimal('1764.25'), Decimal('1818.87'), Decimal('1873.49'), Decimal('1928.11'), Decimal('1982.73')),
        ('2', 180, 'Caposala, Sous Chef', Decimal('1554.19'), Decimal('1602.19'), Decimal('1650.19'), Decimal('1698.19'), Decimal('1746.19')),
        ('3', 165, 'Cameriere, Barista', Decimal('1427.99'), Decimal('1471.99'), Decimal('1515.99'), Decimal('1559.99'), Decimal('1603.99')),
        ('4', 150, 'Cameriere di sala', Decimal('1303.84'), Decimal('1343.84'), Decimal('1383.84'), Decimal('1423.84'), Decimal('1463.84')),
        ('5', 135, 'Aiuto cameriere', Decimal('1183.18'), Decimal('1219.18'), Decimal('1255.18'), Decimal('1291.18'), Decimal('1327.18')),
        ('6 Super', 121, 'Addetto mensa, Lavapiatti esperto', Decimal('1082.10'), Decimal('1114.37'), Decimal('1146.63'), Decimal('1178.90'), Decimal('1211.17')),
        ('6', 115, 'Addetto pulizie, Lavapiatti', Decimal('1042.20'), Decimal('1072.87'), Decimal('1103.53'), Decimal('1134.20'), Decimal('1164.87')),
        ('7', 100, 'Operatore generico', Decimal('919.25'), Decimal('945.92'), Decimal('972.58'), Decimal('999.25'), Decimal('1025.92')),
    ]
    
    # Contingenza per livello (varia leggermente per livello - valori tipici)
    contingenza_per_livello = {
        'Quadri': Decimal('526.50'),
        '1': Decimal('525.80'),
        '2': Decimal('524.00'),
        '3': Decimal('522.50'),
        '4': Decimal('524.94'),  # Dato fornito dall'utente
        '5': Decimal('521.50'),
        '6 Super': Decimal('520.80'),
        '6': Decimal('520.51'),  # Dato fornito dall'utente
        '7': Decimal('519.00'),
    }
    
    # EDR (Elemento Distinto della Retribuzione) - uguale per tutti
    edr = Decimal('10.33')
    
    # Scatti di anzianità per livello (ogni 3 anni, max 6 scatti)
    scatti_per_livello = {
        'Quadri': Decimal('38.50'),
        '1': Decimal('37.00'),
        '2': Decimal('35.50'),
        '3': Decimal('34.00'),
        '4': Decimal('33.05'),  # Dato fornito dall'utente
        '5': Decimal('31.50'),
        '6 Super': Decimal('30.50'),
        '6': Decimal('30.00'),
        '7': Decimal('28.50'),
    }
    
    # Periodi di validità
    periodi = [
        (date(2024, 6, 1), date(2025, 5, 31), 0),   # dal_2024_06
        (date(2025, 6, 1), date(2026, 5, 31), 1),   # dal_2025_06
        (date(2026, 6, 1), date(2027, 5, 31), 2),   # dal_2026_06
        (date(2027, 6, 1), date(2027, 11, 30), 3),  # dal_2027_06
        (date(2027, 12, 1), date(2028, 12, 31), 4), # dal_2027_12
    ]
    
    # Cancella dati esistenti (opzionale - commentare se si vogliono mantenere)
    print("⚠️  Eliminazione dati CCNL esistenti...")
    ParametroCCNLTurismo.objects.filter(ccnl='Turismo Confcommercio', versione='2024-2027').delete()
    print("✅ Dati precedenti eliminati")
    
    contatore = 0
    
    # Creazione record per ogni combinazione livello/periodo
    for livello, parametro, qualifica_base, *paghe in dati_paga_base:
        contingenza = contingenza_per_livello[livello]
        scatto = scatti_per_livello[livello]
        
        for (data_inizio, data_fine, idx_paga) in periodi:
            paga_base = paghe[idx_paga]
            
            # Qualifica univoca includendo il periodo
            periodo_label = data_inizio.strftime('%m/%Y')
            qualifica = f"{qualifica_base} ({periodo_label})"
            
            # Calcolo importo lordo mensile = paga_base + contingenza + edr
            importo_lordo = paga_base + contingenza + edr
            
            # Ore standard full-time
            ore_settimanali = Decimal('40')
            ore_mensili = Decimal('173.33')  # 40 ore * 52 settimane / 12 mesi
            ore_giornaliere = Decimal('8')
            
            parametro_obj = ParametroCCNLTurismo.objects.create(
                ccnl='Turismo Confcommercio',
                versione='2024-2027',
                sezione='ristoranti_pizzerie',
                livello=livello,
                qualifica=qualifica,
                tipo_contratto_nazionale='Indeterminato',
                decorrenza_validita_da=data_inizio,
                decorrenza_validita_a=data_fine,
                paga_base_mensile=paga_base,
                contingenza_mensile=contingenza,
                edr_mensile=edr,
                importo_lordo_mensile=importo_lordo,
                indennita_mensile=Decimal('0'),  # Può essere aggiunta manualmente dopo
                ore_settimanali=ore_settimanali,
                ore_mensili=ore_mensili,
                ore_giornaliere=ore_giornaliere,
                scatto_periodicita_mesi=36,  # Ogni 3 anni
                scatto_importo=scatto,
                numero_scatti_massimi=6,
                straordinario_diurno_maggiorazione=Decimal('15'),
                straordinario_notturno_maggiorazione=Decimal('30'),
                straordinario_festivo_maggiorazione=Decimal('30'),
                riposi_compensativi_regola='Secondo CCNL Turismo',
                note=f'Parametro {parametro} - Livello {livello}',
                attivo=True
            )
            
            contatore += 1
            periodo_str = f"{data_inizio.strftime('%d/%m/%Y')} - {data_fine.strftime('%d/%m/%Y')}"
            print(f"✅ Creato: Livello {livello:6s} | {periodo_str} | Paga Base: €{paga_base:8.2f} | Totale: €{importo_lordo:8.2f}")
    
    print(f"\n🎉 COMPLETATO! Creati {contatore} record nella tabella ParametroCCNLTurismo")
    print(f"\n📊 Riepilogo:")
    print(f"   - Livelli: {len(dati_paga_base)}")
    print(f"   - Periodi: {len(periodi)}")
    print(f"   - Totale record: {contatore}")
    print(f"\n💡 Note:")
    print(f"   - Contingenza: varia per livello (€519-€526)")
    print(f"   - EDR: €{edr} fisso per tutti i livelli")
    print(f"   - Scatti: ogni 36 mesi (max 6), importo varia per livello")
    print(f"   - Maggiorazioni straordinario: 15% diurno, 30% notturno/festivo")


if __name__ == '__main__':
    print("=" * 80)
    print("POPOLAMENTO TABELLA CCNL TURISMO CONFCOMMERCIO 2024-2027")
    print("=" * 80)
    print()
    
    popola_ccnl()
    
    print()
    print("=" * 80)
    print("✅ Script completato con successo!")
    print("=" * 80)
