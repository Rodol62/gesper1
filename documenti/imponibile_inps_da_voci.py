"""
Primo passo della catena busta paga (IT): imponibile ai fini contributivi (INPS / Cassa).

Dalla lettura delle voci del cedolino si applica una regola esplicita su quali righe
concorrono all'imponibile contributivo; si confronta la somma con il valore estratto
dal PDF («Imponibile Contr. Soc.» o equivalente).
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from documenti.formule_cedolino_da_busta import TOLLERANZA, _parse_importo_it

# Categorie voce la cui competenza concorre di norma all'imponibile contributivo
_CATEGORIE_IMPONIBILE_INPS = frozenset(
    {"COMPETENZA", "ACCESSORIO", "LIQUIDAZIONE", "ALTRO", "N/C"}
)

# Esclusioni: trattamenti con regimi contributivi / fiscali particolari (indicativo)
_CATEGORIE_ESCLUSE = frozenset({"BONUS_LEGGE", "TRATTENUTA", "PREVIDENZA"})
_CODICI_ESCLUSI = frozenset(
    {"8992", "9824", "9746"}
)  # 8992 DL 3/20 (netto, fuori impon. INPS); 9824/9746 L.207/2024 — come motore v4 F4


def _chiavi_imponibile_contributivo_teoriche() -> tuple[str, ...]:
    return (
        "Imponibile Contr. Soc.",
        "Imponibile contributivo",
        "Imponibile INPS",
        "Imp. Contr. Soc.",
    )


def _imponibile_da_totali(tot: dict[str, str]) -> tuple[str | None, Decimal | None, str | None]:
    for k in _chiavi_imponibile_contributivo_teoriche():
        v = _parse_importo_it(tot.get(k))
        if v is not None:
            raw = (tot.get(k) or "").strip()
            return k, v, raw or None
    return None, None, None


def confronto_imponibile_inps_da_lettura_cedolino(
    report: dict[str, Any] | None,
) -> dict[str, Any]:
    """
    Applica la regola sulle voci e confronta con l'imponibile contributivo letto dalla busta.
    Non esegue IRPEF, netto o altre fasi.
    """
    logica = (
        "Regole desunte per l’imponibile contributivo (primo stadio busta paga): "
        "si sommano gli importi delle righe con tipo «Competenza» (quindi non trattenute/previdenza in tabella voci) "
        "e con categoria tra COMPETENZA, ACCESSORIO, LIQUIDAZIONE, ALTRO, N/C. "
        "Si escludono le categorie BONUS_LEGGE, TRATTENUTA, PREVIDENZA e i codici 8992 (tratt. int. DL 3/20), "
        "9824 e 9746 (trattamenti agevolati / esonero IVS), come nel motore posizionale v4. "
        "Il risultato va confrontato con il campo di totali cedolino che riporta l’imponibile contributivo (es. «Imponibile Contr. Soc.»)."
    )

    if not report:
        return {
            "stato": "senza_report",
            "logica_applicata": logica,
            "somma_da_voci_fmt": "—",
            "imponibile_busta_fmt": "—",
            "chiave_busta": None,
            "delta_fmt": "—",
            "coerente": None,
            "righe_incluse": [],
            "nota": "Nessun report disponibile.",
        }

    tot = report.get("totali_mensili") or {}
    voci = report.get("voci_retributive") or []

    somma = Decimal("0")
    righe_incluse: list[dict[str, str]] = []

    for v in voci:
        tipo = (v.get("tipo") or "").strip()
        if tipo == "Trattenuta":
            continue
        cat = (v.get("categoria") or "").strip()
        cod = str(v.get("codice") or "").strip()
        if cat in _CATEGORIE_ESCLUSE:
            continue
        if cat and cat not in _CATEGORIE_IMPONIBILE_INPS:
            continue
        if cod in _CODICI_ESCLUSI:
            continue
        imp = _parse_importo_it(v.get("importo"))
        if imp is None:
            continue
        somma += imp
        righe_incluse.append(
            {
                "codice": cod,
                "descrizione": (v.get("descrizione") or "")[:80],
                "categoria": cat,
                "importo": str(v.get("importo") or "").strip(),
            }
        )

    chiave_busta, imp_busta, _raw = _imponibile_da_totali(tot)

    def _fmt(d: Decimal | None) -> str:
        if d is None:
            return "—"
        return _fmt_decimal_it(d)

    delta: Decimal | None = None
    coerente: bool | None = None
    if imp_busta is not None and righe_incluse:
        delta = somma - imp_busta
        coerente = abs(delta) <= TOLLERANZA
    elif imp_busta is not None and not righe_incluse:
        delta = somma - imp_busta
        coerente = None

    if imp_busta is None and not righe_incluse:
        stato = "dati_insufficienti"
    elif imp_busta is None:
        stato = "senza_imponibile_pdf"
    elif not righe_incluse:
        stato = "senza_voci_utili"
    elif coerente:
        stato = "ok"
    else:
        stato = "differenza"

    return {
        "stato": stato,
        "logica_applicata": logica,
        "somma_da_voci": somma,
        "somma_da_voci_fmt": _fmt(somma),
        "n_righe_incluse": len(righe_incluse),
        "imponibile_busta": imp_busta,
        "imponibile_busta_fmt": _fmt(imp_busta),
        "chiave_busta": chiave_busta,
        "delta": delta,
        "delta_fmt": _fmt(delta) if delta is not None else "—",
        "coerente": coerente,
        "righe_incluse": righe_incluse,
        "nota": _nota_stato(stato, chiave_busta, len(righe_incluse)),
    }


def _fmt_decimal_it(d: Decimal) -> str:
    d = d.quantize(Decimal("0.01"))
    neg = d < 0
    if neg:
        d = -d
    ip = int(d)
    cents = int(((d - Decimal(ip)) * 100).quantize(Decimal("1")))
    s = str(ip)
    parts: list[str] = []
    for i, c in enumerate(reversed(s)):
        if i and i % 3 == 0:
            parts.append(".")
        parts.append(c)
    body = "".join(reversed(parts)) + f",{cents:02d}"
    return "-" + body if neg else body


def _nota_stato(stato: str, chiave: str | None, n_righe: int) -> str:
    if stato == "ok":
        return "La somma delle voci selezionate coincide con l’imponibile contributivo estratto dalla busta (entro tolleranza)."
    if stato == "differenza":
        return "Scostamento tra somma voci e imponibile in busta: verificare codici esclusi, righe fuori tabella o layout PDF."
    if stato == "senza_imponibile_pdf":
        return f"Nessun campo imponibile contributivo trovato nei totali (cercati: {', '.join(_chiavi_imponibile_contributivo_teoriche())})."
    if stato == "senza_voci_utili":
        return "Nessuna riga voce è entrata nella regola (manca tabella voci o tutte escluse)."
    if stato == "dati_insufficienti":
        return "Imponibile da busta e voci utili entrambi assenti o non leggibili."
    return ""
