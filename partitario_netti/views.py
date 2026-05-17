"""
Viste partitario netti dipendenti: solo utenti con ruolo ``admin`` o superuser.
"""

from __future__ import annotations

import logging
from decimal import Decimal
from smtplib import SMTPException
from typing import Any
from urllib.parse import parse_qsl, urlencode

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.core.mail import EmailMessage, get_connection
from django.db import DatabaseError, IntegrityError, transaction
from django.http import HttpResponse, HttpResponseBadRequest, HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.text import slugify
from django.views.decorators.http import require_POST

from accounts.models import ConfigurazioneSistema
from accounts.tenant import get_azienda_operativa
from anagrafiche.models import Dipendente
from documenti.models import Documento

from .estratto_contabile import (
    arricchisci_pannelli_riepilogo_anni,
    calcola_riporto_e_saldo_fine_mese,
    costruisci_pannelli_estratto,
    q_competenza_fino_a,
    q_competenza_in_periodo,
)
from .forms import PagamentoNettoDipendenteForm
from .models import MovimentoPartitarioNettoDipendente
from .ricevuta_contanti_pdf import genera_pdf_ricevuta_acconto_contanti
from .ricevuta_firma_dipendente import (
    movimento_richiede_revoca_ricevuta_firma,
    pubblica_ricevuta_firma_dipendente,
    revoca_ricevuta_firma_dipendente,
)
from .services_sync import sincronizza_netto_buste_da_cedolini

logger = logging.getLogger(__name__)


def _descrizione_documento_ricevuta_pagamento(form: PagamentoNettoDipendenteForm) -> str:
    """Testo descrizione documento allegato al pagamento (con causale se presente)."""
    c = (form.cleaned_data.get("causale") or "").strip()
    dp = form.cleaned_data["data_pagamento"]
    base = f"Ricevuta pagamento netto {dp:%d/%m/%Y}"
    if c:
        return f"{base} — {c[:180]}"
    return base


def _anno_intero_da_stringa(raw: str | None, *, default: int) -> int:
    """
    Interpreta l'anno da query string / input utente senza separatori delle migliaia.

    Con ``USE_THOUSAND_SEPARATOR`` (es. IT con ``'.'``) il valore può arrivare come ``2.026``;
    per il filtro anno serve sempre un intero 2000–2100.
    """
    if raw is None:
        return default
    t = str(raw).strip().replace("\xa0", "").replace(" ", "")
    for ch in (".", ",", "'", "\u2019"):
        t = t.replace(ch, "")
    if not t or not t.isdigit():
        return default
    try:
        y = int(t)
    except ValueError:
        return default
    if 2000 <= y <= 2100:
        return y
    return default


def _mese_anno_da_stringa(raw: str | None, default: tuple[int, int]) -> tuple[int, int]:
    """Interpreta ``YYYY-MM`` (es. da ``<input type="month">``)."""
    if raw is None:
        return default
    s = str(raw).strip()[:7]
    if len(s) == 7 and s[4] == "-":
        parts = s.split("-")
        if len(parts) == 2 and parts[0].isdigit() and parts[1].isdigit():
            y, mo = int(parts[0]), int(parts[1])
            if 2000 <= y <= 2100 and 1 <= mo <= 12:
                return (y, mo)
    return default


def _etichetta_periodo(da: tuple[int, int], a: tuple[int, int]) -> str:
    """Testo breve competenza (mm/aaaa — mm/aaaa)."""
    d0, d1 = f"{da[1]:02d}/{da[0]}", f"{a[1]:02d}/{a[0]}"
    return f"{d0} — {d1}" if da != a else d0


def _redirect_situazione_dopo_pagamento(mov: MovimentoPartitarioNettoDipendente) -> str:
    """Query string coerente con filtro anno, periodo annuale e dipendente del movimento."""
    y = int(mov.anno)
    q = urlencode(
        {
            "anno": str(y),
            "periodo_da": f"{y:04d}-01",
            "periodo_a": f"{y:04d}-12",
            "dipendente": str(mov.dipendente_id),
        }
    )
    return f"{reverse('partitario_netti_situazione')}?{q}"


def _next_query_partitario(get_data: Any, *, anno_normalizzato: int) -> str:
    """Serializza filtri partitario per il redirect post-sincronizzazione."""
    pairs: list[tuple[str, str]] = [("anno", str(anno_normalizzato))]
    for key in ("dipendente", "periodo_da", "periodo_a"):
        val = get_data.get(key)
        if val is not None and str(val).strip() != "":
            pairs.append((key, str(val).strip()))
    return urlencode(pairs)


def _select_movimenti_partitario(
    *,
    azienda_id: int,
    dipendente_id: int | None,
    periodo_da: tuple[int, int],
    periodo_a: tuple[int, int],
) -> Any:
    """Queryset movimenti nel periodo di competenza (filtro OR sui mesi)."""
    qs = MovimentoPartitarioNettoDipendente.objects.filter(
        azienda_id=azienda_id,
    ).filter(q_competenza_in_periodo(periodo_da, periodo_a))
    if dipendente_id is not None:
        qs = qs.filter(dipendente_id=dipendente_id)
    return qs.select_related(
        "dipendente",
        "cedolino_motore_v4",
        "cedolino_motore_v4__documento",
        "documento_busta",
        "documento_ricevuta",
        "documento_ricevuta_firma",
        "inserito_da",
    )


def _select_movimenti_fino_a_fine_periodo(
    *,
    azienda_id: int,
    dipendente_ids: list[int],
    periodo_a: tuple[int, int],
) -> Any:
    """Movimenti con competenza fino a fine periodo (per riporto e saldi progressivi)."""
    if not dipendente_ids:
        return MovimentoPartitarioNettoDipendente.objects.none()
    return (
        MovimentoPartitarioNettoDipendente.objects.filter(azienda_id=azienda_id, dipendente_id__in=dipendente_ids)
        .filter(q_competenza_fino_a(periodo_a))
        .select_related(
            "dipendente",
            "cedolino_motore_v4",
            "cedolino_motore_v4__documento",
            "documento_busta",
            "documento_ricevuta",
            "documento_ricevuta_firma",
            "inserito_da",
        )
    )


def _movimento_pagamento_tenant(movimento_id: int, azienda) -> MovimentoPartitarioNettoDipendente:
    """Movimento di pagamento (partitario) vincolato all'azienda operativa."""
    return get_object_or_404(
        MovimentoPartitarioNettoDipendente.objects.select_related(
            "dipendente",
            "azienda",
            "documento_ricevuta_firma",
        ),
        pk=movimento_id,
        azienda=azienda,
        tipo_movimento=MovimentoPartitarioNettoDipendente.TipoMovimento.PAGAMENTO,
    )


def _solo_admin(request) -> bool:
    u = request.user
    if not u.is_authenticated:
        return False
    if u.is_superuser:
        return True
    has = getattr(u, "has_ruolo", None)
    return callable(has) and has("admin")


@login_required
def situazione_contabile_netti(request):
    if not _solo_admin(request):
        return HttpResponseForbidden("Accesso riservato agli amministratori.")

    azienda = get_azienda_operativa(request.user, request.session)
    if not azienda:
        messages.warning(request, "Selezionare un'azienda operativa.")
        return redirect("profile")

    anno_default = timezone.now().year
    anno = _anno_intero_da_stringa(request.GET.get("anno"), default=anno_default)
    periodo_da = _mese_anno_da_stringa(request.GET.get("periodo_da"), (anno, 1))
    periodo_a = _mese_anno_da_stringa(request.GET.get("periodo_a"), (anno, 12))
    if periodo_da > periodo_a:
        periodo_da, periodo_a = periodo_a, periodo_da

    next_query_sync = _next_query_partitario(request.GET, anno_normalizzato=anno)

    dip_filter = (request.GET.get("dipendente") or "").strip()
    dip_id = int(dip_filter) if dip_filter.isdigit() else None

    movs = list(
        _select_movimenti_partitario(
            azienda_id=azienda.id,
            dipendente_id=dip_id,
            periodo_da=periodo_da,
            periodo_a=periodo_a,
        ).order_by("-data_contabile", "-id")
    )

    if dip_id is not None:
        dip_ids_for_cum = [dip_id]
    elif movs:
        dip_ids_for_cum = sorted({m.dipendente_id for m in movs})
    else:
        dip_ids_for_cum = []

    riporto_per_dip: dict[int, Decimal] = {}
    saldo_fine: dict[tuple[int, int, int], Decimal] = {}
    cum_list: list[MovimentoPartitarioNettoDipendente] = []
    if dip_ids_for_cum:
        cum_list = list(
            _select_movimenti_fino_a_fine_periodo(
                azienda_id=azienda.id,
                dipendente_ids=dip_ids_for_cum,
                periodo_a=periodo_a,
            )
        )
        riporto_per_dip, saldo_fine = calcola_riporto_e_saldo_fine_mese(cum_list, periodo_da, periodo_a)

    pannelli_estratto = costruisci_pannelli_estratto(
        movs,
        periodo_da=periodo_da,
        periodo_a=periodo_a,
        saldo_fine_mese=saldo_fine,
    )
    for p in pannelli_estratto:
        p["riporto_inizio_periodo"] = riporto_per_dip.get(p["dipendente"].pk, Decimal("0"))
    arricchisci_pannelli_riepilogo_anni(pannelli_estratto, cum_list, periodo_a)

    movimenti_avere = [x for x in movs if x.tipo_movimento == MovimentoPartitarioNettoDipendente.TipoMovimento.PAGAMENTO]

    dipendenti_scelta = list(
        Dipendente.objects.filter(azienda=azienda).order_by("cognome", "nome")
    )

    periodo_da_html = f"{periodo_da[0]:04d}-{periodo_da[1]:02d}"
    periodo_a_html = f"{periodo_a[0]:04d}-{periodo_a[1]:02d}"

    return render(
        request,
        "partitario_netti/situazione_contabile.html",
        {
            "azienda": azienda,
            "anno": anno,
            "periodo_da": periodo_da,
            "periodo_a": periodo_a,
            "periodo_da_html": periodo_da_html,
            "periodo_a_html": periodo_a_html,
            "periodo_etichetta": _etichetta_periodo(periodo_da, periodo_a),
            "dipendente_filter": dip_id or "",
            "movimenti_avere": movimenti_avere,
            "pannelli_estratto": pannelli_estratto,
            "dipendenti_scelta": dipendenti_scelta,
            "next_query_sync": next_query_sync,
        },
    )


@login_required
def estratt_conto_stampa(request):
    """
    Anteprima e stampa A4 dell'estratto conto per un singolo dipendente e periodo di competenza.
    """
    if not _solo_admin(request):
        return HttpResponseForbidden("Accesso riservato agli amministratori.")

    azienda = get_azienda_operativa(request.user, request.session)
    if not azienda:
        messages.warning(request, "Selezionare un'azienda operativa.")
        return redirect("profile")

    anno_default = timezone.now().year
    anno = _anno_intero_da_stringa(request.GET.get("anno"), default=anno_default)
    dip_raw = (request.GET.get("dipendente") or "").strip()
    if not dip_raw.isdigit():
        messages.error(request, "Per l'estratto in stampa è obbligatorio selezionare un dipendente.")
        return redirect(reverse("partitario_netti_situazione"))

    dip_pk = int(dip_raw)
    dip = get_object_or_404(Dipendente, pk=dip_pk, azienda=azienda)

    periodo_da = _mese_anno_da_stringa(request.GET.get("periodo_da"), (anno, 1))
    periodo_a = _mese_anno_da_stringa(request.GET.get("periodo_a"), (anno, 12))
    if periodo_da > periodo_a:
        periodo_da, periodo_a = periodo_a, periodo_da

    movs = list(
        _select_movimenti_partitario(
            azienda_id=azienda.id,
            dipendente_id=dip_pk,
            periodo_da=periodo_da,
            periodo_a=periodo_a,
        ).order_by("-data_contabile", "-id")
    )
    cum_list = list(
        _select_movimenti_fino_a_fine_periodo(
            azienda_id=azienda.id,
            dipendente_ids=[dip_pk],
            periodo_a=periodo_a,
        )
    )
    riporto_per_dip, saldo_fine = calcola_riporto_e_saldo_fine_mese(cum_list, periodo_da, periodo_a)
    pannelli_estratto = costruisci_pannelli_estratto(
        movs,
        periodo_da=periodo_da,
        periodo_a=periodo_a,
        saldo_fine_mese=saldo_fine,
    )
    for p in pannelli_estratto:
        p["riporto_inizio_periodo"] = riporto_per_dip.get(p["dipendente"].pk, Decimal("0"))

    if not pannelli_estratto:
        pannelli_estratto = [
            {
                "dipendente": dip,
                "anni": [],
                "tot_dare": Decimal("0"),
                "tot_avere": Decimal("0"),
                "saldo": Decimal("0"),
                "riporto_inizio_periodo": riporto_per_dip.get(dip.pk, Decimal("0")),
            }
        ]
    arricchisci_pannelli_riepilogo_anni(pannelli_estratto, cum_list, periodo_a)

    return render(
        request,
        "partitario_netti/estratt_conto_stampa.html",
        {
            "azienda": azienda,
            "dipendente": dip,
            "periodo_da": periodo_da,
            "periodo_a": periodo_a,
            "periodo_etichetta": _etichetta_periodo(periodo_da, periodo_a),
            "pannelli_estratto": pannelli_estratto,
            "ora_stampa": timezone.now(),
        },
    )


@login_required
def situazione_contabile_sincronizza(request):
    if not _solo_admin(request):
        return HttpResponseForbidden("Accesso riservato agli amministratori.")
    if request.method != "POST":
        return redirect(reverse("partitario_netti_situazione"))

    azienda = get_azienda_operativa(request.user, request.session)
    if not azienda:
        messages.error(request, "Nessuna azienda operativa.")
        return redirect(reverse("partitario_netti_situazione"))

    try:
        stats = sincronizza_netto_buste_da_cedolini(azienda, utente=request.user)
    except (DatabaseError, IntegrityError, ValidationError, TypeError, ValueError) as exc:
        logger.exception("Sincronizzazione partitario netti fallita")
        messages.error(request, f"Sincronizzazione non riuscita: {exc}")
        return redirect(reverse("partitario_netti_situazione"))

    messages.success(
        request,
        f"Sincronizzazione completata: {stats['creati']} nuovi, {stats['aggiornati']} aggiornati, "
        f"{stats['saltati']} saltati (senza netto o non coerenti).",
    )
    q = (request.POST.get("next_query") or "").strip()
    if q:
        anno_def = timezone.now().year
        data = dict(parse_qsl(q, keep_blank_values=True))
        anno_q = _anno_intero_da_stringa(data.get("anno"), default=anno_def)
        q_clean = _next_query_partitario(data, anno_normalizzato=anno_q)
        return redirect(f"{reverse('partitario_netti_situazione')}?{q_clean}")
    return redirect(reverse("partitario_netti_situazione"))


@login_required
def pagamento_netto_nuovo(request):
    if not _solo_admin(request):
        return HttpResponseForbidden("Accesso riservato agli amministratori.")

    azienda = get_azienda_operativa(request.user, request.session)
    if not azienda:
        messages.warning(request, "Selezionare un'azienda operativa.")
        return redirect("profile")

    anno_default = timezone.now().year
    if request.method == "POST":
        form = PagamentoNettoDipendenteForm(request.POST, request.FILES, azienda_id=azienda.id)
        if form.is_valid():
            dip = form.cleaned_data["dipendente"]
            if dip.azienda_id != azienda.id:
                messages.error(request, "Dipendente non valido per l'azienda corrente.")
            else:
                try:
                    with transaction.atomic():
                        doc_ric = None
                        f = form.cleaned_data.get("allegato_pdf")
                        if f:
                            doc_ric = Documento.objects.create(
                                azienda=azienda,
                                dipendente=dip,
                                tipo="ricevuta_pagamento_netto",
                                descrizione=_descrizione_documento_ricevuta_pagamento(form),
                                file=f,
                                caricato_da=request.user,
                                caricato_dal_dipendente=False,
                                visibile_al_dipendente=False,
                            )
                        data_p = form.cleaned_data["data_pagamento"]
                        MovimentoPartitarioNettoDipendente.objects.create(
                            azienda=azienda,
                            dipendente=dip,
                            tipo_movimento=MovimentoPartitarioNettoDipendente.TipoMovimento.PAGAMENTO,
                            lato=MovimentoPartitarioNettoDipendente.Lato.AVERE,
                            anno=form.cleaned_data["anno_competenza"],
                            mese=form.cleaned_data["mese_competenza"],
                            data_contabile=data_p,
                            importo=form.cleaned_data["importo"],
                            natura_busta="ORDINARIA",
                            metodo_pagamento=form.cleaned_data["metodo_pagamento"],
                            documento_ricevuta=doc_ric,
                            causale=(form.cleaned_data.get("causale") or "").strip(),
                            inserito_da=request.user,
                        )
                    messages.success(request, "Pagamento registrato nel partitario (DARE in estratto conto).")
                    y = int(form.cleaned_data["anno_competenza"])
                    return redirect(
                        reverse("partitario_netti_situazione")
                        + "?"
                        + urlencode(
                            {
                                "anno": str(y),
                                "periodo_da": f"{y:04d}-01",
                                "periodo_a": f"{y:04d}-12",
                                "dipendente": str(dip.pk),
                            }
                        )
                    )
                except (DatabaseError, IntegrityError, ValidationError, TypeError, ValueError, OSError) as exc:
                    logger.exception("Salvataggio pagamento partitario")
                    messages.error(request, f"Errore salvataggio: {exc}")
    else:
        form = PagamentoNettoDipendenteForm(
            azienda_id=azienda.id,
            initial={
                "anno_competenza": anno_default,
                "mese_competenza": timezone.now().month,
            },
        )

    return render(
        request,
        "partitario_netti/pagamento_nuovo.html",
        {"form": form, "azienda": azienda},
    )


@login_required
def pagamento_netto_modifica(request, movimento_id: int):
    """Aggiorna un movimento di pagamento (DARE in estratto) già registrato."""
    if not _solo_admin(request):
        return HttpResponseForbidden("Accesso riservato agli amministratori.")

    azienda = get_azienda_operativa(request.user, request.session)
    if not azienda:
        messages.warning(request, "Selezionare un'azienda operativa.")
        return redirect("profile")

    mov = get_object_or_404(
        MovimentoPartitarioNettoDipendente.objects.select_related("documento_ricevuta_firma"),
        pk=movimento_id,
        azienda=azienda,
        tipo_movimento=MovimentoPartitarioNettoDipendente.TipoMovimento.PAGAMENTO,
    )

    if request.method == "POST":
        form = PagamentoNettoDipendenteForm(
            request.POST,
            request.FILES,
            azienda_id=azienda.id,
            movimento=mov,
        )
        if form.is_valid():
            dip = form.cleaned_data["dipendente"]
            if dip.azienda_id != azienda.id:
                messages.error(request, "Dipendente non valido per l'azienda corrente.")
            else:
                try:
                    revocata_firma = False
                    with transaction.atomic():
                        if movimento_richiede_revoca_ricevuta_firma(
                            mov,
                            nuovo_importo=form.cleaned_data["importo"],
                            nuova_data_contabile=form.cleaned_data["data_pagamento"],
                            nuovo_anno=int(form.cleaned_data["anno_competenza"]),
                            nuovo_mese=int(form.cleaned_data["mese_competenza"]),
                            nuovo_dipendente_id=int(dip.pk),
                            nuovo_metodo=str(form.cleaned_data["metodo_pagamento"]),
                        ):
                            revoca_ricevuta_firma_dipendente(mov)
                            revocata_firma = True
                        f = form.cleaned_data.get("allegato_pdf")
                        doc_old = mov.documento_ricevuta if f else None
                        if f:
                            mov.documento_ricevuta = Documento.objects.create(
                                azienda=azienda,
                                dipendente=dip,
                                tipo="ricevuta_pagamento_netto",
                                descrizione=_descrizione_documento_ricevuta_pagamento(form),
                                file=f,
                                caricato_da=request.user,
                                caricato_dal_dipendente=False,
                                visibile_al_dipendente=False,
                            )
                        mov.dipendente = dip
                        mov.data_contabile = form.cleaned_data["data_pagamento"]
                        mov.importo = form.cleaned_data["importo"]
                        mov.metodo_pagamento = form.cleaned_data["metodo_pagamento"]
                        mov.anno = form.cleaned_data["anno_competenza"]
                        mov.mese = form.cleaned_data["mese_competenza"]
                        mov.causale = (form.cleaned_data.get("causale") or "").strip()
                        mov.save()
                        if doc_old:
                            doc_old.delete()
                    messages.success(request, "Pagamento aggiornato.")
                    if revocata_firma:
                        messages.warning(
                            request,
                            "La ricevuta resa disponibile al dipendente per la firma è stata rimossa "
                            "perché risultano modificati importo, competenza, data pagamento, dipendente "
                            "o modalità di pagamento. Ripubblica la ricevuta dall'elenco pagamenti se necessario.",
                        )
                    return redirect(_redirect_situazione_dopo_pagamento(mov))
                except (DatabaseError, IntegrityError, ValidationError, TypeError, ValueError, OSError) as exc:
                    logger.exception("Aggiornamento pagamento partitario id=%s", movimento_id)
                    messages.error(request, f"Errore salvataggio: {exc}")
    else:
        form = PagamentoNettoDipendenteForm(azienda_id=azienda.id, movimento=mov)

    return render(
        request,
        "partitario_netti/pagamento_modifica.html",
        {"form": form, "azienda": azienda, "movimento": mov},
    )


@login_required
def pagamento_netto_elimina(request, movimento_id: int):
    if not _solo_admin(request):
        return HttpResponseForbidden("Accesso riservato agli amministratori.")

    azienda = get_azienda_operativa(request.user, request.session)
    if not azienda:
        return HttpResponseForbidden("Azienda non selezionata.")

    mov = get_object_or_404(
        MovimentoPartitarioNettoDipendente,
        pk=movimento_id,
        azienda=azienda,
        tipo_movimento=MovimentoPartitarioNettoDipendente.TipoMovimento.PAGAMENTO,
    )

    if request.method == "POST":
        redir = _redirect_situazione_dopo_pagamento(mov)
        doc = mov.documento_ricevuta
        doc_firma = mov.documento_ricevuta_firma
        with transaction.atomic():
            mov.delete()
            if doc:
                doc.delete()
            if doc_firma:
                doc_firma.delete()
        messages.success(request, "Movimento di pagamento eliminato.")
        return redirect(redir)

    return render(
        request,
        "partitario_netti/pagamento_elimina_confirm.html",
        {"movimento": mov, "azienda": azienda},
    )


@login_required
@require_POST
def ricevuta_contanti_pubblica_dipendente(request, movimento_id: int):
    """Genera il PDF e lo pubblica in «I miei documenti» del dipendente (firma digitale / procedura interna)."""
    if not _solo_admin(request):
        return HttpResponseForbidden("Accesso riservato agli amministratori.")

    azienda = get_azienda_operativa(request.user, request.session)
    if not azienda:
        messages.warning(request, "Selezionare un'azienda operativa.")
        return redirect("profile")

    mov = _movimento_pagamento_tenant(movimento_id, azienda)
    if mov.metodo_pagamento != MovimentoPartitarioNettoDipendente.MetodoPagamento.CONTANTI:
        messages.error(request, "La pubblicazione in area dipendente è prevista solo per pagamenti in contanti.")
        return redirect(_redirect_situazione_dopo_pagamento(mov))

    try:
        pdf_bytes = genera_pdf_ricevuta_acconto_contanti(mov)
    except ValueError as exc:
        messages.error(request, str(exc))
        return redirect(_redirect_situazione_dopo_pagamento(mov))
    except RuntimeError as exc:
        logger.error("Generazione ricevuta contanti non disponibile")
        messages.error(request, str(exc))
        return redirect(_redirect_situazione_dopo_pagamento(mov))

    try:
        pubblica_ricevuta_firma_dipendente(mov, azienda, request.user, pdf_bytes)
    except (DatabaseError, IntegrityError, OSError, ValidationError, TypeError, ValueError) as exc:
        logger.exception("Pubblicazione ricevuta firma dipendente movimento id=%s", movimento_id)
        messages.error(request, f"Pubblicazione non riuscita: {exc}")
        return redirect(_redirect_situazione_dopo_pagamento(mov))

    messages.success(
        request,
        "Ricevuta pubblicata nell'area documenti del dipendente (scheda «Altri» / download). "
        "Puoi revocarla o aggiornarla ripubblicando se cambiano i dati.",
    )
    return redirect(_redirect_situazione_dopo_pagamento(mov))


@login_required
@require_POST
def ricevuta_contanti_revoca_dipendente(request, movimento_id: int):
    """Rimuove la ricevuta generata dall'area dipendente (senza eliminare il movimento di pagamento)."""
    if not _solo_admin(request):
        return HttpResponseForbidden("Accesso riservato agli amministratori.")

    azienda = get_azienda_operativa(request.user, request.session)
    if not azienda:
        messages.warning(request, "Selezionare un'azienda operativa.")
        return redirect("profile")

    mov = _movimento_pagamento_tenant(movimento_id, azienda)
    if revoca_ricevuta_firma_dipendente(mov):
        messages.success(request, "Ricevuta rimossa dall'area dipendente.")
    else:
        messages.info(request, "Non risulta alcuna ricevuta pubblicata da revocare.")
    return redirect(_redirect_situazione_dopo_pagamento(mov))


@login_required
def ricevuta_contanti_pdf(request, movimento_id: int):
    """Scarica il PDF della ricevuta di acconto in contanti (solo pagamenti con metodo contanti)."""
    if not _solo_admin(request):
        return HttpResponseForbidden("Accesso riservato agli amministratori.")

    azienda = get_azienda_operativa(request.user, request.session)
    if not azienda:
        messages.warning(request, "Selezionare un'azienda operativa.")
        return redirect("profile")

    mov = _movimento_pagamento_tenant(movimento_id, azienda)
    try:
        pdf_bytes = genera_pdf_ricevuta_acconto_contanti(mov)
    except ValueError as exc:
        return HttpResponseBadRequest(str(exc))
    except RuntimeError as exc:
        logger.error("Generazione ricevuta contanti non disponibile")
        messages.error(request, str(exc))
        return redirect(_redirect_situazione_dopo_pagamento(mov))

    slug = slugify(f"{mov.dipendente.cognome or 'dip'}_{mov.pk}") or str(mov.pk)
    filename = f"ricevuta_acconto_{slug}.pdf"
    resp = HttpResponse(pdf_bytes, content_type="application/pdf")
    resp["Content-Disposition"] = f'attachment; filename="{filename}"'
    return resp


@login_required
@require_POST
def ricevuta_contanti_invia(request, movimento_id: int):
    """Invia per email al dipendente il PDF della ricevuta (pagamenti in contanti)."""
    if not _solo_admin(request):
        return HttpResponseForbidden("Accesso riservato agli amministratori.")

    azienda = get_azienda_operativa(request.user, request.session)
    if not azienda:
        messages.warning(request, "Selezionare un'azienda operativa.")
        return redirect("profile")

    mov = _movimento_pagamento_tenant(movimento_id, azienda)
    dip = mov.dipendente
    dest = (dip.email or "").strip()
    if not dest:
        messages.warning(request, "Impossibile inviare: email del dipendente non impostata in anagrafica.")
        return redirect(_redirect_situazione_dopo_pagamento(mov))

    try:
        pdf_bytes = genera_pdf_ricevuta_acconto_contanti(mov)
    except ValueError as exc:
        messages.error(request, str(exc))
        return redirect(_redirect_situazione_dopo_pagamento(mov))
    except RuntimeError as exc:
        logger.error("Generazione ricevuta contanti non disponibile")
        messages.error(request, str(exc))
        return redirect(_redirect_situazione_dopo_pagamento(mov))

    config = ConfigurazioneSistema.get()
    nome_sito = (config.nome_sito or "GESPER").strip() or "GESPER"
    oggetto = f"[{nome_sito}] Ricevuta acconto retribuzione in contanti"
    corpo = (
        f"Gentile {dip.nome or ''} {dip.cognome or ''},\n\n"
        "in allegato trovi una copia in PDF della ricevuta di acconto in contanti. "
        "La consegna ordinaria avviene dall'area «I miei documenti» del portale dipendente "
        "dopo la pubblicazione da parte dell'amministratore; questa email è solo un invio "
        "opzionale di cortesia.\n\n"
        "Per dubbi contatta l'ufficio del personale.\n\n"
        f"— {nome_sito}"
    )
    slug = slugify(f"{dip.cognome or 'dip'}_{mov.pk}") or str(mov.pk)
    attach_name = f"ricevuta_acconto_{slug}.pdf"

    try:
        if config.smtp_user and config.smtp_password:
            conn = get_connection(
                backend="accounts.email_backend.ConfigurazioneSistemaEmailBackend",
                host=config.smtp_host,
                port=config.smtp_port,
                username=config.smtp_user,
                password=config.smtp_password,
                use_tls=config.smtp_use_tls and not config.smtp_use_ssl,
                use_ssl=config.smtp_use_ssl,
                fail_silently=False,
            )
            msg = EmailMessage(
                subject=oggetto,
                body=corpo,
                from_email=config.from_email(),
                to=[dest],
                connection=conn,
            )
        else:
            msg = EmailMessage(
                subject=oggetto,
                body=corpo,
                from_email=getattr(settings, "DEFAULT_FROM_EMAIL", "noreply@gesper.it"),
                to=[dest],
            )
        msg.attach(attach_name, pdf_bytes, "application/pdf")
        msg.send()
    except (SMTPException, OSError) as exc:
        logger.exception("Invio email ricevuta contanti fallito (movimento id=%s)", movimento_id)
        messages.error(request, f"Invio non riuscito: {exc}")
        return redirect(_redirect_situazione_dopo_pagamento(mov))

    messages.success(request, "Ricevuta inviata all'indirizzo email del dipendente.")
    return redirect(_redirect_situazione_dopo_pagamento(mov))
