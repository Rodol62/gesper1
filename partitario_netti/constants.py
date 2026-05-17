"""Costanti UI condivise (evita import circolari con altre app)."""

MESI_NOMI = (
    "",
    "Gennaio",
    "Febbraio",
    "Marzo",
    "Aprile",
    "Maggio",
    "Giugno",
    "Luglio",
    "Agosto",
    "Settembre",
    "Ottobre",
    "Novembre",
    "Dicembre",
)

# Coppie (numero, nome) per select «mese di competenza» nel form pagamenti.
MESI_SCELTA: tuple[tuple[int, str], ...] = tuple((i, MESI_NOMI[i]) for i in range(1, 13))
