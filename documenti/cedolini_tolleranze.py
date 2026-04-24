"""
Tolleranze monetarie condivise per confronti sui cedolini (PDF, DB, import paghe, formule F1–F9).

Un solo punto di configurazione evita discrepanze «OK qui / KO lì» per pochi centesimi.
"""

from __future__ import annotations

from decimal import Decimal


# Importi singoli: netto/lordo, snapshot salvato vs lettura odierna, movimento import paghe
TOLLERANZA_CONFRONTO_EURO = Decimal("0.02")

# Formule F1–F9 e somme con propagazione arrotondamenti (motore v4 usa float + ar())
TOLLERANZA_FORMULE_EURO = Decimal("0.05")

# F4: Σ voci contrib. vs imponibile riga A (arrotondamenti totali TS / riga A spesso al centesimo)
TOLLERANZA_IMPONIBILE_VOCI_VS_PDF = Decimal("0.50")

# F5: imponibile × aliquota IVS vs contributi letti (arrotondamenti TS, rateazioni, casi particolari CCNL)
TOLLERANZA_F5_CONTRIBUTI_INPS = Decimal("10.00")

# F8: tot. trattenute vs formula (arr. prec./att., riporti mese precedente, somma subtotali TS)
TOLLERANZA_F8_TRATTENUTE = Decimal("1.00")


def toll_formule_float() -> float:
	return float(TOLLERANZA_FORMULE_EURO)


def _fmt_tol_it(d: Decimal) -> str:
	"""Es. Decimal('0.02') → '0,02'."""
	return f"{d:.2f}".replace(".", ",")


def tolleranze_cedolini_context() -> dict[str, str]:
	"""Chiavi per template buste paga / conciliazione."""
	tol_f_std = _fmt_tol_it(TOLLERANZA_FORMULE_EURO)
	tol_f4 = _fmt_tol_it(TOLLERANZA_IMPONIBILE_VOCI_VS_PDF)
	tol_f5 = _fmt_tol_it(TOLLERANZA_F5_CONTRIBUTI_INPS)
	tol_f8 = _fmt_tol_it(TOLLERANZA_F8_TRATTENUTE)
	tol_mov = _fmt_tol_it(TOLLERANZA_CONFRONTO_EURO)
	return {
		"tol_confronto_label": f"±{tol_mov} €",
		"tol_formule_label": f"±{tol_f_std} €",
		"tol_impon_f4_label": f"±{tol_f4} €",
		"tol_f5_label": f"±{tol_f5} €",
		"tol_f8_label": f"±{tol_f8} €",
		# Non dire «tutte le F1–F9 ±0,05»: F4, F5, F8 hanno soglie proprie nel motore.
		"tol_legenda_breve": (
			f"Motore v4: F1–F3, F6, F7, F9 entro ±{tol_f_std} €; "
			f"F5 (contributi INPS / IVS dip.) ±{tol_f5} €; "
			f"F4 (impon. voci vs riga A) ±{tol_f4} €; "
			f"F8 (tot. trattenute) ±{tol_f8} €. "
			f"Movimenti import / singole celle netto-lordo in anagrafica: ±{tol_mov} €."
		),
	}
