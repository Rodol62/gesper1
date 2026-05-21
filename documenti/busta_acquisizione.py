"""
Pipeline unica di acquisizione busta paga da PDF.

Tutti i punti d’ingresso (lettura cedolino, upload, movimenti import, ZIP, memorizzazione v4)
devono basarsi su questa catena per avere gli stessi netto/lordo/report tra UI, DB e conciliazione.

Ordine: (1) motore posizionale v4 (``try_busta_v4_bundle``); (2) legacy testo + merge analizza/Claude.

Rimemorizzazione massiva dopo deploy: ``python manage.py ricalcola_buste_acquisizione``.

Allineamento motori: stringhe ``motore`` / costanti descritte in
``rapporto_di_lavoro.motori_canonici`` (cedolino v4 vs legacy testo).
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import TYPE_CHECKING, Any

from documenti.buste_pdf_passwords import passwords_for_busta_pdf_read
from documenti.cedolino_bridge_v4 import BustaV4Bundle, try_busta_v4_bundle

if TYPE_CHECKING:
    from documenti.models import Documento
    from documenti.motore_cedolino_v4 import Cedolino


def _leggi_bytes_documento(doc: Documento) -> bytes | None:
    """Legge i byte del PDF; prova path alternativi sotto MEDIA_ROOT (come la UI archivio)."""
    if not getattr(doc, "file", None):
        return None
    name = getattr(doc.file, "name", None) or ""
    if not name:
        return None
    try:
        storage = doc.file.storage
        from documenti.file_path_resolution import first_existing_relpath_for_stored_name

        resolved = first_existing_relpath_for_stored_name(storage, name)
        with storage.open(resolved or name, "rb") as fh:
            return fh.read()
    except Exception:
        return None


def leggi_pdf_busta_documento(doc: "Documento") -> bytes | None:
    """Byte del PDF collegato al ``Documento`` busta (stesso I/O usato dall’acquisizione canonica)."""
    return _leggi_bytes_documento(doc)


def _netto_lordo_da_report(report: dict[str, Any] | None) -> tuple[Decimal | None, Decimal | None]:
    """Import lazy: ``cedolino_confronto_import`` importa ``buste_cedolino_batch`` (no ciclo a livello modulo)."""
    from documenti.cedolino_confronto_import import netto_lordo_da_report

    return netto_lordo_da_report(report)


def _legacy_report_da_testo(raw: bytes, *, password: str) -> dict[str, Any]:
    """Evita import circolare a livello di modulo: chiama la pipeline testo solo qui."""
    from documenti.leggi_busta_paga_claude import estrai_testo_prima_pagina, report_cedolino_da_testo

    t = estrai_testo_prima_pagina(raw, password=password)
    rep = report_cedolino_da_testo(t)
    if rep and not (rep.get("motore") or "").strip():
        rep = dict(rep)
        rep["motore"] = "legacy_testo"
    return rep


@dataclass
class BustaAcquisizioneResult:
    """Esito canonico di una lettura PDF busta (stesso significato ovunque)."""

    report: dict[str, Any]
    motore: str
    errore: str | None = None
    netto: Decimal | None = None
    lordo: Decimal | None = None
    cedolino_v4: Cedolino | None = None
    calc_v4: dict[str, Any] | None = None
    checks_v4: list[Any] | None = None
    password_usata: str = ""
    bundle_v4: BustaV4Bundle | None = None


def acquisisci_busta_pdf_bytes(
    raw: bytes,
    *,
    password: str = "",
    file_label: str = "",
) -> BustaAcquisizioneResult:
    """
    Acquisizione da buffer PDF (una password alla volta; il chiamante può ciclare le password studio).
    """
    # Stesso criterio di ``estrai_report_per_documento`` in ``buste_cedolino_batch``:
    # primi 5 byte ``%PDF-``. (``raw[:5] != b"%PDF"`` era errato: ``b"%PDF"`` è lungo 4 byte.)
    if not raw or len(raw) < 5 or raw[:5] != b"%PDF-":
        return BustaAcquisizioneResult(
            report={},
            motore="",
            errore="File non PDF o buffer vuoto",
        )
    try:
        bundle = try_busta_v4_bundle(
            raw, password=password or "", file_label=file_label or "(buffer)"
        )
        if bundle is not None:
            n, l = _netto_lordo_da_report(bundle.report)
            # Totali riga A/D: pdfplumber sotto etichetta è più fedele del v4 su alcuni layout.
            try:
                from documenti.busta_importi_pdfplumber import estrai_lordo_netto_pdfplumber_monopagina

                n_pl, l_pl = estrai_lordo_netto_pdfplumber_monopagina(raw)
                if n_pl is not None:
                    n = n_pl
                if l_pl is not None:
                    l = l_pl
            except Exception:
                pass
            return BustaAcquisizioneResult(
                report=bundle.report,
                motore="posizionale_v4",
                netto=n,
                lordo=l,
                cedolino_v4=bundle.c,
                calc_v4=bundle.calc,
                checks_v4=bundle.checks,
                password_usata=password or "",
                bundle_v4=bundle,
            )
        report = _legacy_report_da_testo(raw, password=password or "")
        n, l = _netto_lordo_da_report(report)
        mot = (report.get("motore") or "").strip() or "legacy_testo"
        return BustaAcquisizioneResult(
            report=report,
            motore=mot,
            netto=n,
            lordo=l,
            password_usata=password or "",
        )
    except Exception as ex:
        return BustaAcquisizioneResult(
            report={},
            motore="",
            errore=str(ex).strip() or type(ex).__name__,
        )


def acquisisci_busta_da_documento(
    doc: "Documento",
    *,
    raw_pdf: bytes | None = None,
) -> BustaAcquisizioneResult:
    """
    Legge il file del documento e prova le password configurate (studio + settings + vuota).
    Se ``raw_pdf`` è passato, non riapre lo storage (evita doppia I/O quando il chiamante ha già i byte).
    """
    raw = raw_pdf if raw_pdf is not None else _leggi_bytes_documento(doc)
    if raw is None:
        return BustaAcquisizioneResult(
            report={},
            motore="",
            errore="File PDF mancante o non leggibile",
        )
    name = (doc.nome_file() or getattr(doc.file, "name", None) or "") or ""
    last_err: str | None = None
    for pw in passwords_for_busta_pdf_read():
        res = acquisisci_busta_pdf_bytes(raw, password=pw, file_label=name)
        if res.errore is None:
            return res
        last_err = res.errore
    return BustaAcquisizioneResult(
        report={},
        motore="",
        errore=last_err or "Lettura PDF non riuscita con le password configurate",
    )


def report_cedolino_da_sorgente_pdf(
    source: str | bytes | bytearray | Path,
    *,
    password: str = "",
    file_label: str = "",
) -> dict[str, Any]:
    """
    API compatibile con i chiamanti che usano path o buffer: ritorna solo il dict report.
    Solleva ``RuntimeError`` se l’acquisizione fallisce (comportamento atteso da ``report_cedolino_senza_azienda``).
    """
    if isinstance(source, (bytes, bytearray)):
        res = acquisisci_busta_pdf_bytes(
            bytes(source), password=password or "", file_label=file_label or "(buffer)"
        )
    else:
        p = Path(str(source)).expanduser()
        with open(p, "rb") as f:
            res = acquisisci_busta_pdf_bytes(
                f.read(), password=password or "", file_label=file_label or p.name
            )
    if res.errore:
        raise RuntimeError(res.errore)
    return res.report
