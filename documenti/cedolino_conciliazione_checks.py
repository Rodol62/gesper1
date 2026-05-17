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
# F3/F4/F5/F9: confrontano somme da voci vs campi letti al salvataggio (cella riga A / impon. / netto / IVS).
# La pagina conciliazione ha già righe dedicate «elaborato Σ voci vs PDF odierno» + fallback snapshot.
# Contarli come bloccanti duplica gli stessi scarti e genera falsi Δ su archivi 2024+.
# F5: su molte buste il tot. contributi TS non coincide con il solo imponibile×9,36% (ratei, altre voci INPS):
# la riga «Contributi INPS (F5 · calc. vs PDF)» usa tolleranza ampia e fallback su ``tot_contrib_soc`` memorizzato;
# il check F5 interno di ``calcola()`` resta diagnostico in elenco ma non deve ribaltare il badge OK.
# F8: totale trattenute TS con riporti non ricostruibili.
FORMULA_KO_ESITI_CONCILIAZIONE_IGNORATI = (
    "F1 ·",
    "F2 ·",
    "F3 ·",
    "F4 ·",
    "F5 ·",
    "F7 ·",
    "F8 ·",
    "F9 ·",
)


def formula_ko_totali_da_checks(checks: list[Any]) -> int:
    """Conteggio di tutti i check con ``ok`` falso (reportistica / KPI pagina)."""
    return sum(1 for ch in checks if not getattr(ch, "ok", True))


def formula_ko_bloccanti_da_checks(checks: list[Any]) -> int:
    """
    Solo i check che devono far passare la busta in «differenze» se falliscono
    (oltre alle righe esplicite DB↔PDF in ``conciliazione_oggi_vs_cedolino_motore_v4``).

    In pratica nessuna formula F interna resta bloccante: lordo/netto/impon./contributi sono già nelle
    righe «elaborato vs PDF» con tolleranze dedicate; i check F in elenco restano diagnostici.
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
