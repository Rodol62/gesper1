"""
Anonimizzazione dati personali nel database **sandbox** (e file documentali in area demo).

Usato dal comando ``gesper_sandbox_anonymize``. Non importare logica payroll sensibile nei log.
"""

from __future__ import annotations

import hashlib
import logging
from io import BytesIO
from pathlib import Path
from typing import Callable

from django.conf import settings
from django.core.files.base import ContentFile

from sandbox_dimostrativo.state import set_sandbox_routing

logger = logging.getLogger(__name__)

SANDBOX = "sandbox"

_FANTASIA_NOMI: tuple[str, ...] = (
    "ALESSANDRO",
    "MARCO",
    "LUCA",
    "GIUSEPPE",
    "ANDREA",
    "MATTEO",
    "PAOLO",
    "FRANCESCA",
    "GIULIA",
    "SARA",
    "ELENA",
    "VALENTINA",
    "CHIARA",
    "FEDERICA",
    "ROBERTO",
    "SIMONE",
)
_FANTASIA_COGNOMI: tuple[str, ...] = (
    "ROSSI",
    "BIANCHI",
    "VERDI",
    "NERI",
    "MARINO",
    "GRECO",
    "FONTANA",
    "CARUSO",
    "ROMANO",
    "RICCI",
    "MARINI",
    "GALLO",
    "FERRARI",
    "CONTI",
    "ESPOSITO",
    "LEONE",
)


def fake_codice_fiscale(pk: int) -> str:
    """Codice fiscale fittizio 16 caratteri (non valido MEF), univoco per ``pk``."""
    raw = hashlib.sha256(f"GESPER_DEMO_CF_{pk}".encode()).hexdigest().upper()
    out: list[str] = []
    for c in raw:
        if len(out) >= 16:
            break
        if c.isalnum():
            out.append(c)
    while len(out) < 16:
        out.append("X")
    return "".join(out[:16])


def fantasia_nome_cognome(pk: int) -> tuple[str, str]:
    nome = _FANTASIA_NOMI[pk % len(_FANTASIA_NOMI)]
    cognome = _FANTASIA_COGNOMI[(pk * 17) % len(_FANTASIA_COGNOMI)]
    return nome, cognome


def _placeholder_pdf_bytes(titolo_riga: str) -> bytes:
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas

    buf = BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    c.setTitle("Documento dimostrativo")
    c.drawString(72, 800, "Documento dimostrativo — contenuto anonimizzato")
    c.drawString(72, 780, (titolo_riga or "Documento")[:95])
    c.showPage()
    c.save()
    return buf.getvalue()


def _unlink_sandbox_media_relative(relative_name: str) -> None:
    root = getattr(settings, "GESPER_SANDBOX_MEDIA_ROOT", None)
    if not root or not relative_name:
        return
    p = Path(root).expanduser().resolve() / relative_name
    try:
        if p.is_file():
            p.unlink()
    except OSError as exc:
        logger.warning("Rimozione file sandbox %s: %s", p, exc)


def anonymize_dipendenti(*, progress: Callable[[str], None] | None = None) -> int:
    from anagrafiche.models import Dipendente

    n = 0
    qs = Dipendente.objects.using(SANDBOX).order_by("pk")
    total = qs.count()
    for dip in qs.iterator(chunk_size=200):
        nome, cognome = fantasia_nome_cognome(dip.pk)
        dip.nome = nome
        dip.cognome = cognome
        dip.codice_fiscale = fake_codice_fiscale(dip.pk)
        dip.email = f"dip.{dip.pk}@demo.invalid.local"
        dip.telefono = f"333{(dip.pk * 7919) % 10_000_000:07d}"[:30]
        dip.indirizzo = f"VIA DIMOSTRATIVA {dip.pk}"
        dip.cap = "00100"
        dip.citta = "ROMA"
        dip.provincia = "RM"
        dip.paese_residenza = "ITALIA"
        dip.regione_residenza = "LAZIO"
        dip.luogo_nascita = "ROMA"
        dip.paese_nascita = "ITALIA"
        dip.regione_nascita = "LAZIO"
        dip.provincia_nascita = "RM"
        dip.comune_nascita = "ROMA"
        dip.domicilio_indirizzo = dip.indirizzo
        dip.domicilio_cap = dip.cap
        dip.domicilio_comune = dip.citta
        dip.domicilio_provincia = dip.provincia
        dip.paese_domicilio = "ITALIA"
        dip.domicilio_regione = dip.regione_residenza
        dip.save(using=SANDBOX)
        n += 1
        if progress and n % 500 == 0:
            progress(f"Dipendenti anonimizzati: {n}/{total}")
    if progress:
        progress(f"Dipendenti anonimizzati: {n}")
    return n


def anonymize_users_demo(*, progress: Callable[[str], None] | None = None) -> int:
    from accounts.models import User

    skip = {x.strip().lower() for x in getattr(settings, "GESPER_SANDBOX_USERNAMES", frozenset())}
    n = 0
    for u in User.objects.using(SANDBOX).order_by("pk").iterator(chunk_size=200):
        if (u.username or "").strip().lower() in skip:
            continue
        nome, cognome = fantasia_nome_cognome(u.pk + 10_000)
        u.first_name = nome.title()
        u.last_name = cognome.title()
        u.email = f"user.{u.pk}@demo.invalid.local"
        u.save(
            using=SANDBOX,
            update_fields=["first_name", "last_name", "email"],
        )
        n += 1
    if progress:
        progress(f"Utenti anonimizzati (esclusi dimostrativi): {n}")
    return n


def anonymize_profili_candidato(*, progress: Callable[[str], None] | None = None) -> int:
    from accounts.models import ProfiloCandidato

    n = 0
    for p in ProfiloCandidato.objects.using(SANDBOX).order_by("pk").iterator(chunk_size=100):
        p.codice_fiscale = fake_codice_fiscale(p.pk + 50_000)
        p.luogo_nascita = "ROMA"
        p.indirizzo = f"VIA CANDIDATO {p.pk}"
        p.cap = "00100"
        p.citta = "ROMA"
        p.provincia = "RM"
        p.regione_residenza = "LAZIO"
        p.telefono = f"340{(p.pk * 4999) % 10_000_000:07d}"[:30]
        p.numero_documento = f"DEMO-{p.pk}"
        p.iban = ""
        p.dettaglio_familiari = ""
        p.competenze = "Competenze dimostrative (anonimizzate)."
        p.note_candidatura = "Lettera dimostrativa anonimizzata."
        rel_doc = (p.file_documento.name or "").strip() if p.file_documento else ""
        rel_cf = (p.file_codice_fiscale.name or "").strip() if p.file_codice_fiscale else ""
        p.file_documento = None
        p.file_codice_fiscale = None
        p.save(
            using=SANDBOX,
            update_fields=[
                "codice_fiscale",
                "luogo_nascita",
                "indirizzo",
                "cap",
                "citta",
                "provincia",
                "regione_residenza",
                "telefono",
                "numero_documento",
                "iban",
                "dettaglio_familiari",
                "competenze",
                "note_candidatura",
                "file_documento",
                "file_codice_fiscale",
            ],
        )
        if rel_doc:
            _unlink_sandbox_media_relative(rel_doc)
        if rel_cf:
            _unlink_sandbox_media_relative(rel_cf)
        n += 1
    if progress:
        progress(f"Profili candidato anonimizzati: {n}")
    return n


def anonymize_richieste_e_inbox(*, progress: Callable[[str], None] | None = None) -> None:
    from richieste.models import InboxEmailDipendenteAzione, Richiesta

    Richiesta.objects.using(SANDBOX).update(
        motivo="Richiesta dimostrativa (testo anonimizzato)",
        testo_richiesta="Contenuto anonimizzato per ambiente demo.",
        note_risposta="",
    )
    InboxEmailDipendenteAzione.objects.using(SANDBOX).update(
        mittente_email="mittente@demo.invalid.local",
        oggetto="Oggetto dimostrativo",
        risposta_testo="",
    )
    if progress:
        progress("Richieste e inbox e-mail dipendente anonimizzate (testi generici).")


def anonymize_comunicazioni_recesso(*, progress: Callable[[str], None] | None = None) -> None:
    from anagrafiche.models import ComunicazioneRecessoProva

    ComunicazioneRecessoProva.objects.using(SANDBOX).update(
        testo_bozza="Testo dimostrativo anonimizzato.",
        note_consulente="",
        firmatario_nome="Amministratore Dimostrativo",
        firmatario_ruolo="Legale rappresentante (demo)",
    )
    if progress:
        progress("Comunicazioni recesso prova anonimizzate.")


def anonymize_simulazioni_voci(*, progress: Callable[[str], None] | None = None) -> int:
    from rapporto_di_lavoro.models import SimulazioneVoceRetributivaOre

    n = SimulazioneVoceRetributivaOre.objects.using(SANDBOX).update(
        dipendente_nome="Ruolo simulato (demo)",
    )
    if progress:
        progress(f"Voci simulazione retributiva aggiornate (righe): {n}")
    return int(n)


def anonymize_rapporti_allegati(*, progress: Callable[[str], None] | None = None) -> int:
    """Rimuove PDF contratto/proposta/mansionario (file sandbox + campi null)."""
    from rapporto_di_lavoro.models import RapportoDiLavoro

    n = 0
    for r in RapportoDiLavoro.objects.using(SANDBOX).iterator(chunk_size=200):
        paths: list[str] = []
        for attr in ("file_contratto_pdf", "file_proposta", "mansionario_file"):
            f = getattr(r, attr, None)
            if f and getattr(f, "name", None):
                nm = (f.name or "").strip()
                if nm:
                    paths.append(nm)
        if not paths:
            continue
        RapportoDiLavoro.objects.using(SANDBOX).filter(pk=r.pk).update(
            file_contratto_pdf=None,
            file_proposta=None,
            mansionario_file=None,
        )
        for name in paths:
            _unlink_sandbox_media_relative(name)
        n += 1
    if progress:
        progress(f"Allegati contrattuali rimossi (record con file): {n}")
    return n


def anonymize_documenti(*, progress: Callable[[str], None] | None = None) -> int:
    from documenti.models import Documento

    set_sandbox_routing(True)
    try:
        n = 0
        for doc in Documento.objects.using(SANDBOX).order_by("pk").iterator(chunk_size=50):
            old = (doc.file.name or "").strip() if doc.file else ""
            label = f"{doc.get_tipo_display()} #{doc.pk}"
            pdf = _placeholder_pdf_bytes(label)
            doc.descrizione = f"[Demo] {doc.get_tipo_display()} n.{doc.pk}"[:200]
            doc.file.save(
                f"demo_anon_{doc.pk}_{doc.tipo}.pdf",
                ContentFile(pdf),
                save=False,
            )
            doc.save(using=SANDBOX, update_fields=["file", "descrizione"])
            new_name = (doc.file.name or "").strip() if doc.file else ""
            if old and old != new_name:
                _unlink_sandbox_media_relative(old)
            n += 1
            if progress and n % 200 == 0:
                progress(f"Documenti sostituiti con PDF dimostrativo: {n}")
    finally:
        set_sandbox_routing(False)
    if progress:
        progress(f"Documenti anonimizzati (PDF placeholder): {n}")
    return n
