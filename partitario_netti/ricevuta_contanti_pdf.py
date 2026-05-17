"""
Generazione PDF «ricevuta di pagamento in acconto retribuzione» per pagamenti in contanti.
"""

from __future__ import annotations

import logging
import re
from datetime import date
from decimal import Decimal
from io import BytesIO
from typing import Any

from .constants import MESI_NOMI
from .models import MovimentoPartitarioNettoDipendente

logger = logging.getLogger(__name__)


def _xml_esc(s: str) -> str:
    return (s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _format_euro_it(value: Decimal) -> str:
    """Importo in formato italiano (migliaia con punto, decimali con virgola)."""
    q = value.quantize(Decimal("0.01"))
    s = f"{q:,.2f}"
    return s.replace(",", "X").replace(".", ",").replace("X", ".")


def _data_it(d: date | None) -> str:
    """Data in gg/mm/aaaa."""
    if d is None:
        return "—"
    return f"{d.day:02d}/{d.month:02d}/{d.year}"


def _euro_cent_parts(value: Decimal) -> tuple[str, int]:
    """Parte intera (stringa IT senza decimali) e centesimi 0–99."""
    q = value.quantize(Decimal("0.01"))
    intero = int(q)
    cent = int((q * 100) % 100)
    intero_it = _format_euro_it(Decimal(intero)).split(",")[0]
    return intero_it, cent


def _dedupe_comma_segments(text: str) -> str:
    """Rimuove segmenti ripetuti dopo split su virgola (indirizzi incollati male)."""
    raw = [p.strip() for p in (text or "").split(",") if p.strip()]
    seen: set[str] = set()
    out: list[str] = []
    for p in raw:
        key = re.sub(r"\s+", " ", p.lower())
        if key in seen:
            continue
        seen.add(key)
        out.append(p)
    return ", ".join(out)


def _indirizzo_datore(az: Any) -> str:
    """
    Una o due righe leggibili: preferisce via/CAP/comune/provincia strutturati,
    altrimenti il campo indirizzo ripulito da duplicati.
    """
    via = (getattr(az, "sede_legale_via", None) or "").strip()
    cap = (getattr(az, "sede_legale_cap", None) or "").strip()
    com = (getattr(az, "sede_legale_comune", None) or "").strip()
    prv = (getattr(az, "sede_legale_provincia", None) or "").strip().upper()

    righe: list[str] = []
    if via:
        righe.append(via)
    cap_com = " ".join(x for x in (cap, com) if x).strip()
    if cap_com:
        if prv and len(prv) <= 3:
            com_upper = com.upper()
            if prv not in com_upper and f"({prv})" not in com_upper:
                cap_com = f"{cap_com} ({prv})"
        righe.append(cap_com)
    if righe:
        return "\n".join(righe)
    ind = (getattr(az, "indirizzo", None) or "").strip()
    if ind:
        return _dedupe_comma_segments(ind)
    return ""


def _is_segnaposto_luogo(s: str) -> bool:
    """True se il valore in anagrafica è vuoto o solo segnaposto (underscore, trattini, ecc.)."""
    t = (s or "").strip()
    if not t:
        return True
    if re.fullmatch(r"[_\-\s\.]+", t):
        return True
    core = re.sub(r"[\s_\-\.]+", "", t)
    if not core:
        return True
    if t.upper() in ("N/D", "ND", "N.D.", "—", "-"):
        return True
    return False


def _luogo_nascita_dipendente(dip: Any) -> str:
    """
    Luogo di nascita per la ricevuta: campi anagrafici, altrimenti decodifica dal codice fiscale (Belfiore).
    """
    from anagrafiche.codice_fiscale_it import decodifica_codice_fiscale

    ln = (getattr(dip, "luogo_nascita", None) or "").strip()
    if ln and not _is_segnaposto_luogo(ln):
        return ln

    cn = (getattr(dip, "comune_nascita", None) or "").strip()
    pn_raw = (getattr(dip, "provincia_nascita", None) or "").strip().upper()
    if cn and not _is_segnaposto_luogo(cn):
        sigla = pn_raw[:2] if len(pn_raw) == 2 and pn_raw.isalpha() else ""
        if sigla and sigla not in cn.upper() and f"({sigla})" not in cn.upper():
            return f"{cn} ({sigla})"
        return cn

    pa = (getattr(dip, "paese_nascita", None) or "").strip()
    if pa and pa.upper() not in ("ITALIA", "IT", "") and not _is_segnaposto_luogo(pa):
        return pa

    cf = (getattr(dip, "codice_fiscale", None) or "").strip().upper()
    if cf:
        try:
            dec = decodifica_codice_fiscale(cf)
        except (ValueError, TypeError, KeyError):
            dec = None
        if dec:
            if dec.nascita_italiana and (dec.comune_nome or "").strip():
                nome = dec.comune_nome.strip()
                prov = (dec.provincia_sigla or "").strip().upper()[:2]
                if prov and prov not in nome.upper() and f"({prov})" not in nome.upper():
                    return f"{nome} ({prov})"
                return nome
            if (dec.stato_estero_nome or "").strip():
                return dec.stato_estero_nome.strip()
    return ""


def genera_pdf_ricevuta_acconto_contanti(mov: MovimentoPartitarioNettoDipendente) -> bytes:
    """
    Costruisce il PDF della ricevuta (A4) con i dati del movimento e del dipendente.

    Richiede ``mov.metodo_pagamento == contanti`` e tipo pagamento; solleva ``ValueError`` altrimenti.
    """
    if mov.tipo_movimento != MovimentoPartitarioNettoDipendente.TipoMovimento.PAGAMENTO:
        raise ValueError("Solo movimenti di pagamento.")
    if mov.metodo_pagamento != MovimentoPartitarioNettoDipendente.MetodoPagamento.CONTANTI:
        raise ValueError("La ricevuta è prevista solo per pagamenti in contanti.")

    try:
        from reportlab.lib import colors
        from reportlab.lib.enums import TA_CENTER, TA_JUSTIFY
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
        from reportlab.lib.units import cm
        from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
    except ImportError as err:
        logger.error("reportlab non disponibile: %s", err)
        raise RuntimeError("Libreria PDF non disponibile.") from err

    dip = mov.dipendente
    az = mov.azienda

    nome_c = f"{(dip.nome or '').strip()} {(dip.cognome or '').strip()}".strip() or "—"
    luogo_n = _luogo_nascita_dipendente(dip)
    dn = _data_it(dip.data_nascita)
    cf = (dip.codice_fiscale or "").strip() or "—"
    matr = getattr(dip, "matricola", None)
    matr_txt = f"Matricola n. {matr}" if matr else ""

    data_ricev = _data_it(mov.data_contabile)
    mese_nome = MESI_NOMI[mov.mese] if 1 <= mov.mese <= 12 else str(mov.mese)
    anno_c = mov.anno

    ditta = (az.nome or "").strip() or "—"
    piva = (getattr(az, "partita_iva", None) or "").strip()
    ind_datore = _indirizzo_datore(az)

    imp_it = _format_euro_it(mov.importo)
    intero_it, cent = _euro_cent_parts(mov.importo)
    importo_parentesi = f"{intero_it} euro e {cent:02d}/100"

    comune_firma = (az.sede_legale_comune or "").strip() or "________________"
    data_firma = data_ricev

    sesso = (dip.sesso or "").upper()
    nato_txt = "nato" if sesso == "M" else "nata" if sesso == "F" else "nato/a"

    styles = getSampleStyleSheet()
    w = A4[0] - 4 * cm

    title_style = ParagraphStyle(
        name="RcTitle",
        parent=styles["Normal"],
        fontName="Helvetica-Bold",
        fontSize=14,
        leading=18,
        spaceAfter=4,
        alignment=TA_CENTER,
        textColor=colors.HexColor("#1a2744"),
    )
    subtitle = ParagraphStyle(
        name="RcSub",
        parent=styles["Normal"],
        fontName="Helvetica",
        fontSize=8.5,
        leading=11,
        alignment=TA_CENTER,
        textColor=colors.HexColor("#5c6470"),
        spaceAfter=16,
    )
    section = ParagraphStyle(
        name="RcSection",
        parent=styles["Normal"],
        fontName="Helvetica-Bold",
        fontSize=10,
        leading=13,
        spaceBefore=10,
        spaceAfter=6,
        textColor=colors.HexColor("#1a2744"),
    )
    body = ParagraphStyle(
        name="RcBody",
        parent=styles["Normal"],
        fontName="Helvetica",
        fontSize=10.5,
        leading=15.5,
        alignment=TA_JUSTIFY,
        spaceAfter=4,
    )
    body_small = ParagraphStyle(
        name="RcSmall",
        parent=body,
        fontSize=9,
        leading=12.5,
        textColor=colors.HexColor("#444850"),
    )
    foot_ref = ParagraphStyle(
        name="RcFoot",
        parent=styles["Normal"],
        fontSize=7.5,
        leading=10,
        textColor=colors.grey,
        alignment=TA_CENTER,
    )

    if luogo_n:
        riga_nascita = (
            f"{nato_txt} a <b>{_xml_esc(luogo_n)}</b> il <b>{_xml_esc(dn)}</b>, "
            f"C.F. <b>{_xml_esc(cf)}</b>"
        )
    else:
        riga_nascita = (
            f"{nato_txt} il <b>{_xml_esc(dn)}</b>, C.F. <b>{_xml_esc(cf)}</b>"
            "<br/><font size=9 color='#555'>"
            "(Luogo di nascita non presente in anagrafica: integrare comune o luogo di nascita del dipendente.)"
            "</font>"
        )

    blocco_dip = (
        f"Il/La sottoscritto/a <b>{_xml_esc(nome_c)}</b>"
        + (f", {_xml_esc(matr_txt)}" if matr_txt else "")
        + "<br/>"
        + riga_nascita
    )

    blocco_datore_lines = [
        f"<b>{_xml_esc(ditta)}</b>",
    ]
    if piva:
        blocco_datore_lines.append(f"P. IVA {_xml_esc(piva)}")
    if ind_datore:
        for linea in ind_datore.split("\n"):
            blocco_datore_lines.append(_xml_esc(linea))
    blocco_datore = "<br/>".join(blocco_datore_lines)

    dichiarazione = (
        f"In data <b>{_xml_esc(data_ricev)}</b> dichiara di aver ricevuto dal datore di lavoro "
        f"sopra indicato la somma in <b>contanti</b> di <b>€ {_xml_esc(imp_it)}</b> "
        f"(<b>{_xml_esc(importo_parentesi)}</b>), "
        f"a titolo di <b>acconto</b> sulla retribuzione di competenza del mese di "
        f"<b>{_xml_esc(mese_nome)} {anno_c}</b>."
    )

    nota_decur = (
        "Il suddetto importo verrà decurtato dal bonifico bancario a saldo della busta paga "
        "relativa al medesimo periodo."
    )

    story: list[Any] = []
    story.append(Paragraph(_xml_esc("RICEVUTA DI PAGAMENTO IN ACCONTO RETRIBUZIONE"), title_style))
    story.append(
        Paragraph(
            _xml_esc("Documento redatto ai sensi delle consuetudini di documentazione dei pagamenti in acconto."),
            subtitle,
        )
    )

    # Blocco dipendente (cornice)
    t_dip = Table(
        [[Paragraph(blocco_dip, body)]],
        colWidths=[w],
    )
    t_dip.setStyle(
        TableStyle(
            [
                ("BOX", (0, 0), (-1, -1), 0.8, colors.HexColor("#c5cdd8")),
                ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#f4f7fb")),
                ("LEFTPADDING", (0, 0), (-1, -1), 12),
                ("RIGHTPADDING", (0, 0), (-1, -1), 12),
                ("TOPPADDING", (0, 0), (-1, -1), 10),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 10),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ]
        )
    )
    story.append(Paragraph("<b>1. Dati del dipendente</b>", section))
    story.append(t_dip)

    t_dat = Table(
        [[Paragraph(blocco_datore, body)]],
        colWidths=[w],
    )
    t_dat.setStyle(
        TableStyle(
            [
                ("BOX", (0, 0), (-1, -1), 0.8, colors.HexColor("#c5cdd8")),
                ("BACKGROUND", (0, 0), (-1, -1), colors.white),
                ("LEFTPADDING", (0, 0), (-1, -1), 12),
                ("RIGHTPADDING", (0, 0), (-1, -1), 12),
                ("TOPPADDING", (0, 0), (-1, -1), 10),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 10),
            ]
        )
    )
    story.append(Paragraph("<b>2. Datore di lavoro / Azienda</b>", section))
    story.append(t_dat)

    story.append(Paragraph("<b>3. Dichiarazione di avvenuto pagamento</b>", section))
    story.append(Paragraph(dichiarazione, body))
    story.append(Spacer(1, 0.25 * cm))

    st_imp = ParagraphStyle(
        name="RcImpCell",
        parent=styles["Normal"],
        alignment=TA_CENTER,
        fontSize=11,
        leading=14,
    )
    t_imp = Table(
        [
            [
                Paragraph(
                    f"<font size=11><b>€ {_xml_esc(imp_it)}</b></font><br/>"
                    f"<font size=9 color='#333'>{_xml_esc(importo_parentesi)}</font>",
                    st_imp,
                )
            ]
        ],
        colWidths=[w],
    )
    t_imp.setStyle(
        TableStyle(
            [
                ("BOX", (0, 0), (-1, -1), 1.2, colors.HexColor("#1a2744")),
                ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#eef2f8")),
                ("TOPPADDING", (0, 0), (-1, -1), 12),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 12),
            ]
        )
    )
    story.append(t_imp)
    story.append(Spacer(1, 0.35 * cm))
    story.append(Paragraph(_xml_esc(nota_decur), body_small))

    story.append(Spacer(1, 1.0 * cm))
    story.append(
        Paragraph(
            f"<b>{_xml_esc(comune_firma)}</b>, lì <b>{_xml_esc(data_firma)}</b>",
            ParagraphStyle(
                name="RcLuogoData",
                parent=styles["Normal"],
                fontSize=10.5,
                alignment=TA_CENTER,
                spaceAfter=14,
            ),
        )
    )

    firma_box = Table(
        [
            [
                Paragraph(
                    "<b>Firma del dipendente</b><br/>"
                    "<font size=8 color='#555'>(firma autografa, digitale o grafometrica su copia conforme)</font>",
                    body_small,
                )
            ],
            [Paragraph("<br/><br/><br/>", body)],
        ],
        colWidths=[w],
        rowHeights=[None, 2.0 * cm],
    )
    firma_box.setStyle(
        TableStyle(
            [
                ("BOX", (0, 0), (-1, -1), 0.6, colors.HexColor("#aeb8c4")),
                ("LINEBELOW", (0, 1), (0, 1), 0.55, colors.HexColor("#333333")),
                ("LEFTPADDING", (0, 0), (-1, -1), 12),
                ("RIGHTPADDING", (0, 0), (-1, -1), 12),
                ("TOPPADDING", (0, 0), (-1, 0), 8),
                ("BOTTOMPADDING", (0, 1), (-1, 1), 2),
                ("ALIGN", (0, 0), (-1, -1), "CENTER"),
                ("VALIGN", (0, 1), (0, 1), "BOTTOM"),
            ]
        )
    )
    story.append(firma_box)
    story.append(Spacer(1, 0.5 * cm))
    story.append(
        Paragraph(
            _xml_esc(f"Riferimento interno: movimento partitario n. {mov.pk}."),
            foot_ref,
        )
    )

    buf = BytesIO()
    doc = SimpleDocTemplate(
        buf,
        pagesize=A4,
        leftMargin=2 * cm,
        rightMargin=2 * cm,
        topMargin=1.8 * cm,
        bottomMargin=1.8 * cm,
        title="Ricevuta acconto contanti",
    )
    doc.build(story)
    out = buf.getvalue()
    buf.close()
    return out
