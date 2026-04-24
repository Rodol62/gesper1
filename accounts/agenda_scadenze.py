"""
Scadenze operative per dashboard admin: contratti TD e promemoria F24 (regola semplificata).

La data F24 è un promemoria orientativo (16 del mese successivo al periodo di competenza,
regola frequente per ritenute da lavoro dipendente); verificare sempre normativa e casistica.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from django.urls import reverse
from django.utils import timezone

from accounts.models import MovimentoImportPaghe


def _add_month(year: int, month: int, delta: int) -> tuple[int, int]:
    m = month + delta
    y = year
    while m > 12:
        m -= 12
        y += 1
    while m < 1:
        m += 12
        y -= 1
    return y, m


def scadenza_f24_ritenute_sintetica(anno_periodo: int, mese_periodo: int) -> date:
    """16 del mese successivo al mese di competenza del periodo."""
    if mese_periodo >= 12:
        return date(anno_periodo + 1, 1, 16)
    return date(anno_periodo, mese_periodo + 1, 16)


def _priorita_f24(scadenza: date, oggi: date) -> int:
    if scadenza < oggi:
        return 2
    giorni = (scadenza - oggi).days
    if giorni <= 7:
        return 3
    if giorni <= 21:
        return 4
    return 5


def _priorita_label(p: int) -> str:
    return {1: "Critica", 2: "Alta", 3: "Media", 4: "Normale", 5: "Bassa"}.get(p, "—")


def build_agenda_scadenze(azienda: Any | None, oggi: date | None = None) -> list[dict[str, Any]]:
    """
    Elenco eventi con chiavi: titolo, descrizione, data, priorita, priorita_label, categoria, url, extra.
    """
    oggi = oggi or timezone.localdate()
    items: list[dict[str, Any]] = []

    if azienda:
        from rapporto_di_lavoro.services_contratti import (
            contratti_td_in_scadenza,
            contratti_td_scaduti_non_chiusi,
        )

        for r in contratti_td_scaduti_non_chiusi(azienda, oggi=oggi):
            dip = r.dipendente
            label = f"{dip.cognome} {dip.nome}".strip() if dip else "—"
            items.append(
                {
                    "categoria": "contratto_td_scaduto",
                    "titolo": f"Contratto a termine non allineato: {label}",
                    "descrizione": (
                        f"Data fine {r.data_fine_rapporto.strftime('%d/%m/%Y')} passata; "
                        "verificare rinnovo, addendum o cessazione."
                    ),
                    "data": r.data_fine_rapporto,
                    "priorita": 1,
                    "priorita_label": _priorita_label(1),
                    "url": reverse("lista_contratti_scadenza"),
                    "extra": {"rapporto_id": r.pk},
                }
            )

        for r in contratti_td_in_scadenza(azienda, giorni=30, oggi=oggi):
            if not r.data_fine_rapporto:
                continue
            dip = r.dipendente
            label = f"{dip.cognome} {dip.nome}".strip() if dip else "—"
            giorni = (r.data_fine_rapporto - oggi).days
            pr = 2 if giorni <= 7 else 3
            items.append(
                {
                    "categoria": "contratto_td_prossimo",
                    "titolo": f"Scadenza contratto TD: {label}",
                    "descrizione": f"Termine il {r.data_fine_rapporto.strftime('%d/%m/%Y')} (tra {giorni} giorni).",
                    "data": r.data_fine_rapporto,
                    "priorita": pr,
                    "priorita_label": _priorita_label(pr),
                    "url": reverse("lista_contratti_scadenza"),
                    "extra": {"rapporto_id": r.pk, "giorni": giorni},
                }
            )

    y, m = oggi.year, oggi.month
    for _ in range(2):
        y, m = _add_month(y, m, -1)

    for _ in range(9):
        scad = scadenza_f24_ritenute_sintetica(y, m)
        if scad < oggi - timedelta(days=120) or scad > oggi + timedelta(days=180):
            y, m = _add_month(y, m, 1)
            continue
        pr = _priorita_f24(scad, oggi)
        has_mov = False
        if azienda:
            has_mov = MovimentoImportPaghe.objects.filter(
                azienda=azienda, tipo="F24", anno=y, mese=m
            ).exists()
        note = (
            "Movimento F24 presente in archivio import."
            if has_mov
            else "Nessun F24 importato per questo periodo in archivio (controllo consigliato)."
        )
        items.append(
            {
                "categoria": "f24_promemoria",
                "titolo": f"F24 — competenza {m:02d}/{y}",
                "descrizione": (
                    f"Promemoria versamento (regola sintetica 16/mese succ. al periodo). {note}"
                ),
                "data": scad,
                "priorita": pr,
                "priorita_label": _priorita_label(pr),
                "url": reverse("lista_documenti") + f"?categoria=f24&anno={y}",
                "extra": {"anno_periodo": y, "mese_periodo": m, "ha_import": has_mov},
            }
        )
        y, m = _add_month(y, m, 1)

    items.sort(key=lambda x: (x["priorita"], x["data"], x["titolo"]))
    return items


def agenda_popup_items(items: list[dict[str, Any]], oggi: date | None = None, limit: int = 14) -> list[dict[str, Any]]:
    """Sottoinsieme per modale: priorità stringente o scadenza già passata."""
    oggi = oggi or timezone.localdate()
    out: list[dict[str, Any]] = []
    for it in sorted(items, key=lambda x: (x["priorita"], x["data"])):
        if it["priorita"] <= 3 or it["data"] < oggi:
            out.append(it)
        if len(out) >= limit:
            break
    return out


def items_in_calendar_month(items: list[dict[str, Any]], year: int, month: int) -> list[dict[str, Any]]:
    return [it for it in items if it["data"].year == year and it["data"].month == month]
