"""
Percorso fiscale in ordine normativo busta paga (IT): imponibile INPS → contributi dipendente
→ imponibile IRPEF → IRPEF lorda → detrazioni d'imposta → IRPEF netta → addizionali → netto.

Usa i valori estratti dal PDF (totali/IRPEF) quando presenti; integra stime dalle voci
(classificazione codice) come riferimento incrociato. Non sostituisce il software paghe:
ricalcola aliquote ufficiali solo se in futuro si aggiungono tabelle; qui si espone la catena
logica e la coerenza aritmetica rispetto ai totali del cedolino.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from documenti.formule_cedolino_da_busta import (
    TOLLERANZA,
    _fmt_importo_it_da_decimal,
    _parse_importo_it,
    risolvi_contributi_da_totali,
    risolvi_irpef_netta,
    somma_addizionali_da_blocco_irpef,
)

# Codici voce con trattamento agevolato / fuori schema IRPEF ordinario (indicativo)
_CODICI_BONUS_ESENTI = frozenset({"9824"})


def _fmt(d: Decimal | None) -> str:
    return _fmt_importo_it_da_decimal(d) if d is not None else "—"


def _somma_voci_per_categorie(
    voci: list[dict[str, Any]],
    *,
    includi_categorie: frozenset[str] | None = None,
    escludi_categorie: frozenset[str] | None = None,
    escludi_codici: frozenset[str] | None = None,
    solo_competenza_tipo: bool = True,
) -> tuple[Decimal, int]:
    tot = Decimal("0")
    n = 0
    for v in voci or []:
        if solo_competenza_tipo and (v.get("tipo") or "").strip() == "Trattenuta":
            continue
        cat = (v.get("categoria") or "").strip()
        cod = str(v.get("codice") or "").strip()
        if includi_categorie is not None and cat not in includi_categorie:
            continue
        if escludi_categorie and cat in escludi_categorie:
            continue
        if escludi_codici and cod in escludi_codici:
            continue
        imp = _parse_importo_it(v.get("importo"))
        if imp is not None:
            tot += imp
            n += 1
    return tot, n


def _somma_trattenute_voci(voci: list[dict[str, Any]]) -> Decimal:
    s = Decimal("0")
    for v in voci or []:
        if (v.get("tipo") or "").strip() != "Trattenuta":
            continue
        imp = _parse_importo_it(v.get("importo"))
        if imp is not None:
            s += abs(imp)
    return s


def costruisci_percorso_fiscale_normativa_it(
    voci: list[dict[str, Any]],
    tot: dict[str, str],
    irp: dict[str, str],
) -> dict[str, Any]:
    tot = tot or {}
    irp = irp or {}

    lordo = _parse_importo_it(tot.get("Totale Lordo"))
    imp_inps_pdf = _parse_importo_it(tot.get("Imponibile Contr. Soc."))
    oneri_ded = _parse_importo_it(tot.get("Oneri Deducibili"))

    ck, contributi = risolvi_contributi_da_totali(tot)
    _ik, irpef_netta, _src = risolvi_irpef_netta(tot, irp)

    imp_irpef_pdf = _parse_importo_it(tot.get("Imponibile IRPEF (mese)"))
    irpef_lorda = _parse_importo_it(tot.get("IRPEF Lorda"))
    detrazioni = _parse_importo_it(tot.get("Tot. Detrazioni"))
    irpef_netta_tot = _parse_importo_it(tot.get("IRPEF Netta"))
    if irpef_netta_tot is None:
        irpef_netta_tot = _parse_importo_it(irp.get("IRPEF Erario"))

    addizionali = somma_addizionali_da_blocco_irpef(irp)
    netto_pdf = _parse_importo_it(tot.get("Netto in Busta"))
    tot_trattenute_blocco = _parse_importo_it(tot.get("Tot. Trattenute"))

    # Stima elementi retributivi rilevanti per imponibile contributivo (voci in competenza)
    cat_imponibili = frozenset(
        {"COMPETENZA", "ACCESSORIO", "LIQUIDAZIONE", "ALTRO", "N/C"}
    )
    somma_voci_imp, n_v_imp = _somma_voci_per_categorie(
        voci,
        includi_categorie=cat_imponibili,
        escludi_categorie=frozenset({"BONUS_LEGGE"}),
        escludi_codici=_CODICI_BONUS_ESENTI,
    )
    bonus_esenti, n_bonus = _somma_voci_per_categorie(
        voci,
        includi_categorie=frozenset({"BONUS_LEGGE"}),
        solo_competenza_tipo=True,
    )
    s9824 = Decimal("0")
    for v in voci or []:
        if str(v.get("codice") or "").strip() != "9824":
            continue
        imp = _parse_importo_it(v.get("importo"))
        if imp is not None and (v.get("tipo") or "").strip() != "Trattenuta":
            s9824 += imp
    if s9824 > 0:
        bonus_esenti = max(bonus_esenti, s9824)
        n_bonus = max(n_bonus, 1)

    trattenute_voci = _somma_trattenute_voci(voci)

    # Schema semplificato richiesto: imp. IRPEF ≈ imp. INPS − contributo dip. (reale: anche oneri deducibili)
    imp_irpef_teorico: Decimal | None = None
    if imp_inps_pdf is not None and contributi is not None:
        imp_irpef_teorico = imp_inps_pdf - contributi
        if oneri_ded is not None:
            imp_irpef_teorico = imp_irpef_teorico + oneri_ded  # oneri di solito negativi in busta

    passi: list[dict[str, Any]] = []

    passi.append(
        {
            "ordine": 1,
            "fase": "Retribuzione lorda",
            "normativa": (
                "Compenso complessivo prima delle trattenute (totale competenze in busta). "
                "È il punto di partenza del cedolino."
            ),
            "valore": _fmt(lordo),
            "fonte": "PDF: Totale Lordo" if lordo is not None else "—",
            "nota_incrocio": (
                f"Somma stimata voci «competenza» (escl. bonus legge indicativi): {_fmt(somma_voci_imp)} "
                f"({n_v_imp} righe)."
                if n_v_imp
                else ""
            ),
        }
    )

    passi.append(
        {
            "ordine": 2,
            "fase": "Imponibile contributi sociali (INPS / Cassa)",
            "normativa": (
                "Base su cui si calcolano i contributi previdenziali a carico del lavoratore "
                "(e quota datore), secondo aliquote e massimali vigenti."
            ),
            "valore": _fmt(imp_inps_pdf),
            "fonte": "PDF: Imponibile Contr. Soc." if imp_inps_pdf is not None else "—",
            "nota_incrocio": "",
        }
    )

    passi.append(
        {
            "ordine": 3,
            "fase": "Contributi sociali a carico del dipendente",
            "normativa": (
                "Ritenute previdenziali sulla base imponibile INPS (quota dipendente). "
                "Si applicano prima dell’IRPEF."
            ),
            "valore": _fmt(contributi),
            "fonte": f"PDF: {ck}" if ck and contributi is not None else "—",
            "nota_incrocio": (
                f"Σ importi righe «Trattenuta» (previdenza + trattenute in tabella): {_fmt(trattenute_voci)} — "
                "può includere anche IRPEF/addizionali a seconda del layout."
                if trattenute_voci > 0
                else ""
            ),
        }
    )

    passi.append(
        {
            "ordine": 4,
            "fase": "Imponibile IRPEF (reddito di lavoro dipendente)",
            "normativa": (
                "Reddito imponibile per l’imposta sul reddito, dopo contributi e con oneri deducibili "
                "(spese detraibili/deducibili secondo decreti). In busta è spesso già nettizzato dal paghe."
            ),
            "valore": _fmt(imp_irpef_pdf),
            "fonte": "PDF: Imponibile IRPEF (mese)" if imp_irpef_pdf is not None else "—",
            "nota_incrocio": (
                f"Schema sempl.: impon. INPS − contributi dip. (+ oneri ded. se estratti): {_fmt(imp_irpef_teorico)}."
                if imp_irpef_teorico is not None
                else ""
            ),
        }
    )

    passi.append(
        {
            "ordine": 5,
            "fase": "IRPEF lorda",
            "normativa": (
                "Imposta lorda sul reddito (art. 11 e seguenti TUIR), prima delle detrazioni d’imposta."
            ),
            "valore": _fmt(irpef_lorda),
            "fonte": "PDF: IRPEF Lorda" if irpef_lorda is not None else "—",
            "nota_incrocio": "",
        }
    )

    passi.append(
        {
            "ordine": 6,
            "fase": "Detrazioni d’imposta",
            "normativa": (
                "Detrazioni da imposta lorda (lavoro dipendente, coniuge, figli, ecc.) — D.Lgs. 917/86 e successive."
            ),
            "valore": _fmt(detrazioni),
            "fonte": "PDF: Tot. Detrazioni" if detrazioni is not None else "—",
            "nota_incrocio": "",
        }
    )

    passi.append(
        {
            "ordine": 7,
            "fase": "IRPEF netta (a carico del dipendente)",
            "normativa": (
                "Ritenuta erariale da trattenere in busta dopo detrazioni; può coincidere con la voce "
                "«IRPEF Erario» o «IRPEF Netta» a seconda del modello di cedolino."
            ),
            "valore": _fmt(irpef_netta_tot),
            "fonte": "PDF: IRPEF Netta o IRPEF Erario" if irpef_netta_tot is not None else "—",
            "nota_incrocio": "",
        }
    )

    passi.append(
        {
            "ordine": 8,
            "fase": "Addizionali regionali e comunali (e conguagli)",
            "normativa": "Addizionali IRPEF ex D.Lgs. 23/2011 e acconti/conguagli riportati in cedolino.",
            "valore": _fmt(addizionali) if addizionali > 0 else "—",
            "fonte": "PDF: blocco IRPEF / addizionali" if addizionali > 0 else "—",
            "nota_incrocio": "",
        }
    )

    passi.append(
        {
            "ordine": 9,
            "fase": "Elementi con trattamento agevolato (es. bonus in detassazione)",
            "normativa": (
                "Componenti retributivi con regimi fiscali particolari (es. somme non pienamente imponibili "
                "o con detassazione): in molte buste influiscono sul netto senza seguire la sola catena IRPEF ordinaria."
            ),
            "valore": _fmt(bonus_esenti) if bonus_esenti > 0 else "—",
            "fonte": "Stima da voci (categoria bonus / cod. 9824)" if bonus_esenti > 0 else "—",
            "nota_incrocio": "",
        }
    )

    passi.append(
        {
            "ordine": 10,
            "fase": "Netto in busta",
            "normativa": (
                "Importo complessivo dopo contributi, ritenute fiscali, addizionali e altre trattenute autorizzate; "
                "deve coincidere con l’accredito al dipendente indicato in cedolino."
            ),
            "valore": _fmt(netto_pdf),
            "fonte": "PDF: Netto in Busta" if netto_pdf is not None else "—",
            "nota_incrocio": "",
        }
    )

    # Riconciliazione netto: lordo − contributi − IRPEF netta − addizionali (non usa Tot. Trattenute per evitare doppi conteggi)
    netto_ric: Decimal | None = None
    coer_netto: bool | None = None
    if (
        lordo is not None
        and contributi is not None
        and irpef_netta_tot is not None
        and netto_pdf is not None
    ):
        netto_ric = lordo - contributi - irpef_netta_tot - addizionali
        coer_netto = abs(netto_pdf - netto_ric) <= TOLLERANZA

    nota_metodo = (
        "Ordine secondo la logica tipica delle buste paga italiane: prima la base INPS e i contributi del dipendente, "
        "poi l’imponibile IRPEF, l’imposta lorda, le detrazioni d’imposta e l’IRPEF netta, quindi addizionali e infine il netto. "
        "I numeri sono quelli estratti dal PDF; la riga «Schema sempl.» per l’imponibile IRPEF è solo un incrocio aritmetico "
        "(in sede di conguaglio intervengono anche altre voci). La riconciliazione del netto usa: "
        "Lordo − contributi dip. − IRPEF netta − addizionali (se estratte), senza ricalcolo delle aliquote INPS/IRPEF."
    )

    return {
        "titolo": "Percorso fiscale (normativa busta paga — Italia)",
        "nota_metodo": nota_metodo,
        "passi": passi,
        "riconciliazione_netto": {
            "formula": "Netto ≈ Lordo − contributi dipendente − IRPEF netta − Σ addizionali (da blocco IRPEF)",
            "valore_ricalcolato": _fmt(netto_ric),
            "valore_pdf": _fmt(netto_pdf),
            "coerente": coer_netto,
            "tot_trattenute_pdf": _fmt(tot_trattenute_blocco),
            "nota_tot_trattenute": (
                "«Tot. Trattenute» in cedolino aggrega spesso più voci: non è usato nella formula sopra per non sommare due volte IRPEF/addizionali."
                if tot_trattenute_blocco is not None
                else ""
            ),
        },
    }
