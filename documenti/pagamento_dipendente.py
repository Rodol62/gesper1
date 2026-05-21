"""
Documenti archivio per pagamenti al dipendente (bonifici, contanti, ricevute PDF).
"""
from __future__ import annotations

import re
from datetime import date
from decimal import Decimal

from documenti.models import Documento

TIPO_DOCUMENTO_PAGAMENTO_DIPENDENTE = "pagamento_dipendente"
ETICHETTA_TIPO_PAGAMENTO_DIPENDENTE = "Pagamento dipendente"

MESI_ITA_NOME = (
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


def formato_importo_descrizione(importo: Decimal) -> str:
    """Importo in forma leggibile per descrizione documento (es. 1.500,00)."""
    q = importo.quantize(Decimal("0.01"))
    neg = q < 0
    q = abs(q)
    parti = f"{q:.2f}".split(".")
    intero = int(parti[0])
    gruppi: list[str] = []
    s = str(intero)
    while len(s) > 3:
        gruppi.insert(0, s[-3:])
        s = s[:-3]
    if s:
        gruppi.insert(0, s)
    testo = ".".join(gruppi) + "," + parti[1]
    return ("-" if neg else "") + testo


def periodo_competenza_da_mese_anno(mese: int | None, anno: int | None) -> str:
    if mese and anno and 1 <= int(mese) <= 12:
        return f"{MESI_ITA_NOME[int(mese)]} {int(anno)}"
    return ""


def descrizione_documento_pagamento_dipendente(
    *,
    data_pagamento: date,
    importo: Decimal,
    metodo: str,
    causale: str = "",
    periodo_competenza: str = "",
) -> str:
    """
    Descrizione canonica per ``Documento`` tipo pagamento dipendente.

    Esempio: ``Pagamento dipendente 20/04/2026 — Bonifico — € 100,00 — Acconto aprile 2026 — competenza Aprile 2026``
    """
    met = (metodo or "Pagamento").strip().capitalize()
    if met not in ("Bonifico", "Contanti"):
        met = "Bonifico" if "contant" in met.lower() else met
    imp = formato_importo_descrizione(importo)
    parti = [
        f"Pagamento dipendente {data_pagamento:%d/%m/%Y}",
        met,
        f"€ {imp}",
    ]
    caus = (causale or "").strip()
    if caus:
        parti.append(caus)
    comp = (periodo_competenza or "").strip()
    if comp:
        parti.append(f"competenza {comp}")
    return " — ".join(parti)[:200]


def descrizione_movimento_partitario(
    *,
    metodo: str,
    causale: str = "",
) -> str:
    """Descrizione riga Dare in partitario (max 220)."""
    met = (metodo or "Bonifico").strip().capitalize()
    caus = (causale or "").strip()
    if caus:
        return f"{met} — {causale}"[:220]
    return met[:220]


def normalizza_descrizione_legacy_pagamento(descrizione: str) -> tuple[str, str, Decimal | None]:
    """
    Ricava metodo, causale e importo da descrizioni storiche ``ricevuta_pagamento_netto``.
    """
    desc = (descrizione or "").strip()
    metodo = "Bonifico"
    if "contant" in desc.lower():
        metodo = "Contanti"
    m_imp = re.search(r"€\s*([\d.,]+)", desc)
    importo = None
    if m_imp:
        try:
            importo = Decimal(m_imp.group(1).replace(".", "").replace(",", "."))
        except Exception:
            importo = None
    causale = desc
    for pref in (
        "Ricevuta pagamento netto ",
        "Ricevuta acconto retribuzione (contanti) ",
        "Ricevuta acconto retribuzione ",
    ):
        if causale.startswith(pref):
            causale = causale[len(pref) :]
    causale = re.sub(r"—\s*€\s*[\d.,]+\s*—\s*da firmare\s*$", "", causale, flags=re.I).strip(" —")
    return metodo, causale, importo


def crea_documento_pagamento_dipendente(
    *,
    azienda,
    dipendente,
    data_pagamento: date,
    importo: Decimal,
    metodo: str,
    causale: str = "",
    periodo_competenza: str = "",
    file_obj,
    utente=None,
) -> Documento:
    """Crea ``Documento`` tipo Pagamento dipendente con allegato PDF e descrizione standard."""
    if file_obj is None:
        raise ValueError("file_obj obbligatorio per Documento pagamento dipendente")
    descr = descrizione_documento_pagamento_dipendente(
        data_pagamento=data_pagamento,
        importo=importo,
        metodo=metodo,
        causale=causale,
        periodo_competenza=periodo_competenza,
    )
    doc = Documento(
        azienda=azienda,
        dipendente=dipendente,
        tipo=TIPO_DOCUMENTO_PAGAMENTO_DIPENDENTE,
        descrizione=descr,
        caricato_da=utente,
        caricato_dal_dipendente=False,
        visibile_al_dipendente=True,
    )
    doc.file.save(file_obj.name, file_obj, save=False)
    doc.save()
    return doc
