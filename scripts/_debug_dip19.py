#!/usr/bin/env python
"""Debug specifico per dip_19."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pdfplumber, re

AMOUNT_RE = re.compile(r'^-?\d{1,3}(?:[.,\u00A0]\d{3})*[.,]\d{2}$')

pdf_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        'documenti', 'busta_01_2024_dip_19_p3.pdf')

with pdfplumber.open(pdf_path) as pdf:
    page = pdf.pages[0]
    words = page.extract_words()

# Cerca TOTALE e LORDO
print("=== Parole chiave e loro coordinate ===")
for i, w in enumerate(words):
    if w['text'].upper() in ('TOTALE','LORDO','NETTO','BUSTA'):
        print(f"i={i:3d} text={w['text']!r:15s} x0={w['x0']:7.1f} top={w['top']:7.1f}")

print()
print("=== Numeri vicino a y=504 (area TOTALE LORDO) ===")
for w in words:
    if AMOUNT_RE.match(w['text']) and 490 < w['top'] < 540:
        print(f"  {w['text']:<15} x0={w['x0']:7.1f} top={w['top']:7.1f}")

print()
print("=== Numeri vicino a y=618 (area NETTO BUSTA) ===")
for w in words:
    if AMOUNT_RE.match(w['text']) and 610 < w['top'] < 670:
        print(f"  {w['text']:<15} x0={w['x0']:7.1f} top={w['top']:7.1f}")

print()
print("=== Testo pagina (righe 100-145) ===")
text = page.extract_text() or ''
for i, line in enumerate(text.split('\n')[100:], start=100):
    print(f"L{i:3d}: {line}")
