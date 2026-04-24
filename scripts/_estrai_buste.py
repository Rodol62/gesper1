#!/usr/bin/env python
"""
Estrazione dati da buste paga PDF locali.
Usa approccio posizionale (coordinate x/y) per trovare TOTALE LORDO e NETTO BUSTA.
"""
import os
import re
import sys

# Aggiungi il path del progetto Django
project_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, project_dir)
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'settings')

try:
    import pdfplumber
except ImportError:
    print("pdfplumber non installato. Esegui: pip install pdfplumber")
    sys.exit(1)

import django

django.setup()
from accounts.formatting import euro_it_str

AMOUNT_RE = re.compile(r'^-?\d{1,3}(?:[.,\u00A0]\d{3})*[.,]\d{2}$')


def normalize_amount(s):
    """Converte '1.433,89' o '1,433.89' in float."""
    if s is None:
        return None
    s = s.replace('\u00A0', '').replace(' ', '')
    # formato italiano: punto come separatore migliaia, virgola decimale
    if ',' in s and '.' in s:
        # es: 1.433,89 -> 1433.89
        s = s.replace('.', '').replace(',', '.')
    elif ',' in s:
        # es: 726,95 -> 726.95
        s = s.replace(',', '.')
    try:
        return float(s)
    except ValueError:
        return None


def find_value_below_label(page, label_text, y_gap_max=60, x_tolerance=5, y_min_gap=0):
    """
    Cerca il valore numerico SOTTO l'etichetta nella stessa colonna.

    In questi cedolini STeamSystem il layout è:
    - riga etichetta  (es. "TOTALE LORDO") in cima alla cella
    - riga valore     (es. "726,95") nella stessa cella, qualche pt sotto

    Strategia:
    1. Localizza le parole dell'etichetta (x0_min, x1_max, bottom)
    2. Cerca numeri sotto (y > label_bottom + y_min_gap,
                           y < label_bottom + y_gap_max)
       con x che si sovrappone alla colonna dell'etichetta (con tolleranza)
    3. Restituisce il numero più vicino in y, poi per x al centro della label.

    y_min_gap: gap minimo per saltare righe intermedie (es. 20 per NETTO BUSTA
               che ha una riga intermedia prima dei valori reali di riga 31).
    """
    words = page.extract_words(keep_blank_chars=False)
    label_parts = label_text.upper().split()

    for i, w in enumerate(words):
        if w['text'].upper() != label_parts[0]:
            continue
        # verifica sequenza completa
        match = True
        for j, part in enumerate(label_parts[1:], 1):
            if i + j >= len(words) or words[i + j]['text'].upper() != part:
                match = False
                break
        if not match:
            continue

        # coordinate bounding-box dell'intera etichetta (prima→ultima parola)
        first_w = words[i]
        last_w = words[i + len(label_parts) - 1]
        label_x0 = first_w['x0']
        label_x1 = last_w['x1']
        label_bottom = max(first_w['bottom'], last_w['bottom'])

        # calcola il centro-x della colonna etichetta
        label_cx = (label_x0 + label_x1) / 2

        # cerca numeri sotto la label, nella stessa colonna
        candidates = []
        for cw in words:
            if not AMOUNT_RE.match(cw['text']):
                continue
            gap = cw['top'] - label_bottom
            if gap < y_min_gap:
                continue
            if gap > y_gap_max:
                continue
            # il numero deve sovrapporsi alla colonna etichetta (con tolleranza)
            in_col = (cw['x0'] < label_x1 + x_tolerance and
                      cw['x1'] > label_x0 - x_tolerance)
            if in_col:
                candidates.append(cw)

        if candidates:
            # il più vicino in y, poi in x
            best = min(candidates, key=lambda c: (c['top'], abs((c['x0']+c['x1'])/2 - label_cx)))
            return best['text']

    return None


def extract_from_pdf(pdf_path):
    """Estrae dipendente, periodo, TOTALE LORDO, NETTO BUSTA da una busta PDF."""
    result = {
        'file': os.path.basename(pdf_path),
        'dipendente': None,
        'periodo': None,
        'lordo': None,
        'netto': None,
        'raw_lordo': None,
        'raw_netto': None,
    }

    mese_map = {
        'GENNAIO': 1, 'FEBBRAIO': 2, 'MARZO': 3, 'APRILE': 4,
        'MAGGIO': 5, 'GIUGNO': 6, 'LUGLIO': 7, 'AGOSTO': 8,
        'SETTEMBRE': 9, 'OTTOBRE': 10, 'NOVEMBRE': 11, 'DICEMBRE': 12,
    }

    try:
        with pdfplumber.open(pdf_path) as pdf:
            page = pdf.pages[0]

            # --- Approccio posizionale: cerca valori SOTTO le etichette ---
            raw_lordo = find_value_below_label(page, 'TOTALE LORDO', y_min_gap=0)
            raw_netto = find_value_below_label(page, 'NETTO BUSTA', y_min_gap=0)
            result['raw_lordo'] = raw_lordo
            result['raw_netto'] = raw_netto
            result['lordo'] = normalize_amount(raw_lordo)
            result['netto'] = normalize_amount(raw_netto)

            # --- Estrai testo per dipendente e periodo ---
            text = page.extract_text() or ''
            lines = text.split('\n')

            # Cerca "MESE ANNO COGNOME NOME" pattern tipo "GENNAIO 2024 ... GOMES FERREIRA..."
            for line in lines:
                for mese_nome, mese_num in mese_map.items():
                    m = re.search(
                        rf'\b{mese_nome}\b\s+(\d{{4}})\b',
                        line.upper()
                    )
                    if m:
                        result['periodo'] = f"{mese_num:02d}/{m.group(1)}"
                        # il nome è spesso sulla stessa riga
                        # tenta di estrarre il cognome/nome dopo i codici numerici
                        nome_m = re.search(
                            r'(?:\d+\s+){3,}([A-Z][A-Z\s\']+?)\s+\d{2}/\d{2}',
                            line
                        )
                        if nome_m:
                            result['dipendente'] = nome_m.group(1).strip()
                        break
                if result['periodo']:
                    break

    except Exception as e:
        result['error'] = str(e)

    return result


def main():
    project_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    pdf_dir = os.path.join(project_dir, 'documenti')

    # Trova tutti i PDF buste
    pdf_files = sorted([
        os.path.join(pdf_dir, f)
        for f in os.listdir(pdf_dir)
        if f.endswith('.pdf') and 'busta' in f.lower()
    ])

    # Aggiungi anche i PDF alla radice progetto
    for f in os.listdir(project_dir):
        if f.endswith('.pdf') and ('busta' in f.lower() or 'gennaio' in f.lower() or
                                    'febbraio' in f.lower() or 'paga' in f.lower()):
            pdf_files.append(os.path.join(project_dir, f))

    if not pdf_files:
        print("Nessun PDF trovato.")
        return

    print(f"PDF trovati: {len(pdf_files)}")
    print()
    # Header
    print(f"{'FILE':<42} {'PERIODO':>8} {'DIPENDENTE':<35} {'LORDO':>14} {'NETTO':>14}")
    print("-" * 112)

    totals = {'lordo': 0.0, 'netto': 0.0, 'count': 0, 'ok': 0}

    for path in pdf_files:
        r = extract_from_pdf(path)
        lordo_str = euro_it_str(r['lordo']).rjust(14) if r['lordo'] is not None else f"{'?':>14}"
        netto_str = euro_it_str(r['netto']).rjust(14) if r['netto'] is not None else f"{'?':>14}"
        dip = (r['dipendente'] or '-')[:35]
        per = r['periodo'] or '?'
        print(f"{r['file']:<42} {per:>8} {dip:<35} {lordo_str} {netto_str}")

        totals['count'] += 1
        if r['lordo']:
            totals['lordo'] += r['lordo']
            totals['ok'] += 1
        if r['netto']:
            totals['netto'] += r['netto']

    print("-" * 112)
    tot_l = euro_it_str(totals['lordo']).rjust(14)
    tot_n = euro_it_str(totals['netto']).rjust(14)
    print(f"{'TOTALE (' + str(totals['count']) + ' buste, ' + str(totals['ok']) + ' con lordo)':<51} "
          f"{tot_l} {tot_n}")
    print()

    # Debug: mostra raw values per i primi 2 file
    print("=== DEBUG raw values (primi 2 PDF) ===")
    for path in pdf_files[:2]:
        r = extract_from_pdf(path)
        print(f"File: {r['file']}")
        print(f"  raw_lordo = {r.get('raw_lordo')!r}")
        print(f"  raw_netto = {r.get('raw_netto')!r}")
        if r.get('error'):
            print(f"  ERROR: {r['error']}")


if __name__ == '__main__':
    main()
