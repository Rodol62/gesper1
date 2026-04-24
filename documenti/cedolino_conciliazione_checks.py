"""
Regole uniche per quali controlli del motore ``calcola()`` contano per l’**esito sintetico**
conciliazione (badge OK / Δ).

I check **F1** (retribuzione di fatto), **F2** (importo riga voce) e **F7** (IRPEF lorda annua
su progressivi) dipendono da dettaglio posizionale o da progressivi annui spesso incompleti
nello snapshot ``CedolinoMotoreV4``, pur avendo **totali mensili** già verificati altrove
(righe elaborato F3–F5, F9 vs PDF).

Non duplicare questa logica in altri moduli: importare da qui.
"""

from __future__ import annotations

from typing import Any

# Prefissi ``EsitoCheck.campo`` del motore v4 (stesso testo di ``motore_cedolino_v4.chk``).
FORMULA_KO_ESITI_CONCILIAZIONE_IGNORATI = (
    "F1 ·",
    "F2 ·",
    "F7 ·",
)


def formula_ko_totali_da_checks(checks: list[Any]) -> int:
    """Conteggio di tutti i check con ``ok`` falso (reportistica / KPI pagina)."""
    return sum(1 for ch in checks if not getattr(ch, "ok", True))


def formula_ko_bloccanti_da_checks(checks: list[Any]) -> int:
    """
    Solo i check che devono far passare la busta in «differenze» se falliscono
    (oltre alle righe esplicite DB↔PDF in ``conciliazione_oggi_vs_cedolino_motore_v4``).
    """
    n = 0
    for ch in checks:
        if getattr(ch, "ok", True):
            continue
        campo = (getattr(ch, "campo", None) or "").strip()
        if any(campo.startswith(p) for p in FORMULA_KO_ESITI_CONCILIAZIONE_IGNORATI):
            continue
        n += 1
    return n
