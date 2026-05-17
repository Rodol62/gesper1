"""
Costruzione strutture dati per la vista «estratto conto» partitario netti.

Convenzione visualizzata: **DARE** = pagamenti; **AVERE** = netti busta da riconoscere; **saldo** = DARE − AVERE.
"""

from __future__ import annotations

from collections import defaultdict
from decimal import Decimal
from typing import Any

from django.db.models import Q

from .constants import MESI_NOMI
from .models import MovimentoPartitarioNettoDipendente


def _dip_sort_key(dip: Any) -> tuple[str, str, int]:
    return (dip.cognome or "").lower(), (dip.nome or "").lower(), dip.pk


def ord_movimento(m: MovimentoPartitarioNettoDipendente) -> tuple[int, int, int, int, int]:
    """Ordine cronologico di competenza: anno, mese, buste prima dei pagamenti, data contabile, id."""
    tipo_pri = (
        0
        if m.tipo_movimento == MovimentoPartitarioNettoDipendente.TipoMovimento.BUSTA_NETTO
        else 1
    )
    return (m.anno, m.mese, tipo_pri, m.data_contabile.toordinal(), m.pk)


def mesi_inclusi(da: tuple[int, int], a: tuple[int, int]) -> list[tuple[int, int]]:
    """Elenco (anno, mese) da ``da`` a ``a`` inclusi."""
    out: list[tuple[int, int]] = []
    y, mo = da
    while (y, mo) <= a:
        out.append((y, mo))
        mo += 1
        if mo > 12:
            mo = 1
            y += 1
    return out


def q_competenza_in_periodo(periodo_da: tuple[int, int], periodo_a: tuple[int, int]) -> Q:
    """``Q`` ORM: movimenti con competenza (anno, mese) nel periodo incluso."""
    parts = [Q(anno=y, mese=m) for y, m in mesi_inclusi(periodo_da, periodo_a)]
    if not parts:
        return Q(pk__in=[])
    q: Q = parts[0]
    for p in parts[1:]:
        q |= p
    return q


def q_competenza_fino_a(periodo_a: tuple[int, int]) -> Q:
    """Movimenti con (anno, mese) <= ``periodo_a`` (per calcolo cumulati e riporto)."""
    y_end, m_end = periodo_a
    return Q(anno__lt=y_end) | Q(anno=y_end, mese__lte=m_end)


def calcola_riporto_e_saldo_fine_mese(
    movs_fino_a_fine_periodo: list[MovimentoPartitarioNettoDipendente],
    periodo_da: tuple[int, int],
    periodo_a: tuple[int, int],
) -> tuple[dict[int, Decimal], dict[tuple[int, int, int], Decimal]]:
    """
    Calcola il saldo all'inizio del periodo (riporto) e il saldo progressivo a fine mese per ogni
    competenza nel periodo.

    ``movs_fino_a_fine_periodo`` deve contenere tutti i movimenti dei dipendenti di interesse con
    competenza (anno, mese) <= fine periodo (inclusi anni precedenti), ordinabili per ``ord_movimento``.

    Returns:
        riporto_per_dip: saldo **DARE − AVERE** (DARE = pagamenti, AVERE = netti busta da riconoscere)
            cumulato subito **prima** del primo movimento nel periodo.
        saldo_fine_mese: chiave ``(dipendente_id, anno, mese)`` → saldo dopo l'ultimo movimento del mese.
    """
    by_dip: dict[int, list[MovimentoPartitarioNettoDipendente]] = defaultdict(list)
    for m in movs_fino_a_fine_periodo:
        by_dip[m.dipendente_id].append(m)

    riporto_per_dip: dict[int, Decimal] = {}
    saldo_fine: dict[tuple[int, int, int], Decimal] = {}

    for dip_id, lista in by_dip.items():
        lista.sort(key=ord_movimento)
        cum = Decimal("0")
        riporto_impostato = False

        for m in lista:
            ym = (m.anno, m.mese)
            if ym > periodo_a:
                break
            if ym >= periodo_da and not riporto_impostato:
                riporto_per_dip[dip_id] = cum
                riporto_impostato = True
            # Saldo progressivo = somma(DARE) − somma(AVERE) con DARE=pagamenti, AVERE=netti busta.
            if m.tipo_movimento == MovimentoPartitarioNettoDipendente.TipoMovimento.BUSTA_NETTO:
                cum -= m.importo
            else:
                cum += m.importo
            if periodo_da <= ym <= periodo_a:
                saldo_fine[(dip_id, m.anno, m.mese)] = cum

        if not riporto_impostato:
            riporto_per_dip[dip_id] = cum

    return riporto_per_dip, saldo_fine


def costruisci_pannelli_estratto(
    movs: list[MovimentoPartitarioNettoDipendente],
    *,
    periodo_da: tuple[int, int] | None = None,
    periodo_a: tuple[int, int] | None = None,
    saldo_fine_mese: dict[tuple[int, int, int], Decimal] | None = None,
) -> list[dict[str, Any]]:
    """
    Un pannello per dipendente: totali per anno, dettaglio mensile.

    **Convenzione estratto:** DARE = pagamenti al dipendente; AVERE = importi da riconoscere (netti
    busta); saldo mese = DARE − AVERE. Opzionale saldo progressivo a fine mese se ``saldo_fine_mese``.

    Se ``periodo_da`` / ``periodo_a`` sono impostati, filtra le righe per competenza nel periodo.
    """
    by_dip: dict[int, list[MovimentoPartitarioNettoDipendente]] = defaultdict(list)
    for m in movs:
        by_dip[m.dipendente_id].append(m)

    pannelli: list[dict[str, Any]] = []
    for dip_id in sorted(by_dip.keys(), key=lambda did: _dip_sort_key(by_dip[did][0].dipendente)):
        righe_d = by_dip[dip_id]
        if periodo_da and periodo_a:
            righe_d = [m for m in righe_d if periodo_da <= (m.anno, m.mese) <= periodo_a]
        if not righe_d:
            continue
        dip = righe_d[0].dipendente

        anni_set = sorted({m.anno for m in righe_d}, reverse=True)
        anni_out: list[dict[str, Any]] = []
        tot_dare_dip = Decimal("0")
        tot_avere_dip = Decimal("0")

        for y in anni_set:
            in_y = [m for m in righe_d if m.anno == y]
            tot_dare_y = sum(
                (m.importo for m in in_y if m.tipo_movimento == MovimentoPartitarioNettoDipendente.TipoMovimento.PAGAMENTO),
                Decimal("0"),
            )
            tot_avere_y = sum(
                (m.importo for m in in_y if m.tipo_movimento == MovimentoPartitarioNettoDipendente.TipoMovimento.BUSTA_NETTO),
                Decimal("0"),
            )
            tot_dare_dip += tot_dare_y
            tot_avere_dip += tot_avere_y

            mesi_nums = sorted({m.mese for m in in_y}, reverse=True)
            mesi_out: list[dict[str, Any]] = []
            for mes in mesi_nums:
                in_m = [m for m in in_y if m.mese == mes]
                dare_m = sum(
                    (
                        m.importo
                        for m in in_m
                        if m.tipo_movimento == MovimentoPartitarioNettoDipendente.TipoMovimento.PAGAMENTO
                    ),
                    Decimal("0"),
                )
                avere_m = sum(
                    (
                        m.importo
                        for m in in_m
                        if m.tipo_movimento == MovimentoPartitarioNettoDipendente.TipoMovimento.BUSTA_NETTO
                    ),
                    Decimal("0"),
                )
                buste = sorted(
                    [m for m in in_m if m.tipo_movimento == MovimentoPartitarioNettoDipendente.TipoMovimento.BUSTA_NETTO],
                    key=lambda x: (x.natura_busta or "", -x.pk),
                )
                pagamenti = sorted(
                    [m for m in in_m if m.tipo_movimento == MovimentoPartitarioNettoDipendente.TipoMovimento.PAGAMENTO],
                    key=lambda x: (-x.data_contabile.toordinal(), -x.pk),
                )
                sk = (dip_id, y, mes)
                saldo_prog = saldo_fine_mese.get(sk) if saldo_fine_mese else None
                mesi_out.append(
                    {
                        "mese": mes,
                        "mese_nome": MESI_NOMI[mes] if 1 <= mes <= 12 else str(mes),
                        "dare": dare_m,
                        "avere": avere_m,
                        "saldo": dare_m - avere_m,
                        "saldo_progressivo_fine": saldo_prog,
                        "buste": buste,
                        "pagamenti": pagamenti,
                    }
                )

            anni_out.append(
                {
                    "anno": y,
                    "tot_dare": tot_dare_y,
                    "tot_avere": tot_avere_y,
                    "saldo": tot_dare_y - tot_avere_y,
                    "mesi": mesi_out,
                }
            )

        pannelli.append(
            {
                "dipendente": dip,
                "anni": anni_out,
                "tot_dare": tot_dare_dip,
                "tot_avere": tot_avere_dip,
                "saldo": tot_dare_dip - tot_avere_dip,
            }
        )

    return pannelli


def arricchisci_pannelli_riepilogo_anni(
    pannelli: list[dict[str, Any]],
    movs_fino_a_fine_periodo: list[MovimentoPartitarioNettoDipendente],
    periodo_a: tuple[int, int],
) -> None:
    """
    Per ogni dipendente e anno presente nei pannelli, aggiunge:

    - ``riporto_inizio_anno``: saldo DARE − AVERE cumulato **prima** del primo movimento di quell'anno
      (riporto da anni precedenti e, se applicabile, mesi dell'anno precedenti al primo movimento);
    - ``saldo_progressivo_fine_anno``: saldo dopo l'ultimo movimento dell'anno (competenza ≤ ``periodo_a``).
    """
    by_dip: dict[int, list[MovimentoPartitarioNettoDipendente]] = defaultdict(list)
    for m in movs_fino_a_fine_periodo:
        by_dip[m.dipendente_id].append(m)

    for p in pannelli:
        dip_id = p["dipendente"].pk
        lista = list(by_dip.get(dip_id, []))
        lista.sort(key=ord_movimento)
        if not lista:
            for a in p.get("anni", []):
                a["riporto_inizio_anno"] = Decimal("0")
                a["saldo_progressivo_fine_anno"] = Decimal("0")
            continue

        cum = Decimal("0")
        riporto_inizio_anno: dict[int, Decimal] = {}
        saldo_fine_anno: dict[int, Decimal] = {}

        for m in lista:
            if (m.anno, m.mese) > periodo_a:
                break
            y = int(m.anno)
            if y not in riporto_inizio_anno:
                riporto_inizio_anno[y] = cum
            if m.tipo_movimento == MovimentoPartitarioNettoDipendente.TipoMovimento.BUSTA_NETTO:
                cum -= m.importo
            else:
                cum += m.importo
            saldo_fine_anno[y] = cum

        for a in p.get("anni", []):
            y = int(a["anno"])
            a["riporto_inizio_anno"] = riporto_inizio_anno.get(y, Decimal("0"))
            a["saldo_progressivo_fine_anno"] = saldo_fine_anno.get(y, a["riporto_inizio_anno"])


def costruisci_saldi_progressivi(
    movs: list[MovimentoPartitarioNettoDipendente],
) -> list[dict[str, Any]]:
    """
    Per ogni dipendente, movimenti in ordine cronologico (anno, mese, tipo, data, id),
    saldo progressivo dopo ciascun movimento; elenco mostrato dal più recente al meno recente.
    """
    by_dip: dict[int, list[MovimentoPartitarioNettoDipendente]] = defaultdict(list)
    for m in movs:
        by_dip[m.dipendente_id].append(m)

    sezioni: list[dict[str, Any]] = []
    for dip_id in sorted(by_dip.keys(), key=lambda did: _dip_sort_key(by_dip[did][0].dipendente)):
        righe_d = by_dip[dip_id]
        dip = righe_d[0].dipendente

        def _ord(m: MovimentoPartitarioNettoDipendente) -> tuple[int, int, int, int, int]:
            tipo_pri = 0 if m.tipo_movimento == MovimentoPartitarioNettoDipendente.TipoMovimento.BUSTA_NETTO else 1
            return (m.anno, m.mese, tipo_pri, m.data_contabile.toordinal(), m.pk)

        chrono = sorted(righe_d, key=_ord)
        cum = Decimal("0")
        linee: list[dict[str, Any]] = []
        for m in chrono:
            if m.tipo_movimento == MovimentoPartitarioNettoDipendente.TipoMovimento.BUSTA_NETTO:
                dare_v, avere_v = Decimal("0"), m.importo
                cum -= m.importo
                descr = f"Netto in busta ({m.natura_busta or 'ORDINARIA'})"
            else:
                dare_v, avere_v = m.importo, Decimal("0")
                cum += m.importo
                caus = (m.causale or "").strip()
                descr = f"Pagamento ({m.get_metodo_pagamento_display()})" + (f" — {caus}" if caus else "")

            linee.append(
                {
                    "periodo": f"{m.mese:02d}/{m.anno}",
                    "data_contabile": m.data_contabile,
                    "descrizione": descr,
                    "dare": dare_v,
                    "avere": avere_v,
                    "saldo_progressivo": cum,
                    "movimento": m,
                }
            )

        linee.reverse()
        sezioni.append({"dipendente": dip, "linee": linee})

    return sezioni
