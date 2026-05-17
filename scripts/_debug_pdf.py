#!/usr/bin/env python
"""Debug delle coordinate spaziali del PDF busta paga."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pdfplumber, re

AMOUNT_RE = re.compile(r'^-?\d{1,3}(?:[.,\u00A0]\d{3})*[.,]\d{2}$')

pdf_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        'documenti', 'busta_01_2024_dip_18_p2.pdf')

with pdfplumber.open(pdf_path) as pdf:
    page = pdf.pages[0]
    words = page.extract_words()

print(f"Totale parole: {len(words)}")
print(f"Page size: {page.width:.1f} x {page.height:.1f}")
print()

# Mostra tutte le parole con coordinate
print(f"{'i':>4} {'text':<25} {'x0':>7} {'x1':>7} {'top':>7} {'bottom':>7}")
print("-" * 65)
for i, w in enumerate(words):
    # evidenzia le parole chiave
    mark = ''
    if w['text'].upper() in ('TOTALE','LORDO','NETTO','BUSTA','IRPEF','ERARIO'):
        mark = '<<<'
    if AMOUNT_RE.match(w['text']):
        mark = '§§§'
    if mark:
        print(f"{i:>4} {w['text']:<25} {w['x0']:>7.1f} {w['x1']:>7.1f} {w['top']:>7.1f} {w['bottom']:>7.1f}  {mark}")
