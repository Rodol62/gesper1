"""
Partitario paghe dipendenti — scheda contabile classica.

- Avere: buste paga (netto da pagare, da MovimentoImportPaghe)
- Dare: bonifici/pagamenti registrati (PagamentoPartitarioPaghe)
- Saldo progressivo: cumulo (avere − dare), residuo da pagare al dipendente
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any

from django.db.models import QuerySet, Sum

from accounts.models import MovimentoImportPaghe, PagamentoPartitarioPaghe
from anagrafiche.models import Azienda, Dipendente


def importo_netto_busta(mov: MovimentoImportPaghe) -> Decimal:
    if mov.importo_netto is not None:
        return mov.importo_netto
    if mov.importo is not None:
        return mov.importo
    return Decimal('0')


@dataclass
class RigaLibroPartitario:
    """Riga partitario contabile (dare / avere / saldo)."""

    data_ord: date
    data_label: str
    desc_dare: str
    dare: Decimal
    desc_avere: str
    avere: Decimal
    saldo: Decimal = field(default_factory=lambda: Decimal('0'))
    documento_id: int | None = None
    pagamento_id: int | None = None
    busta_movimento_id: int | None = None


@dataclass
class BloccoAnnoPartitario:
    anno: int
    righe: list[RigaLibroPartitario]
    tot_avere: Decimal
    tot_dare: Decimal
    saldo_finale: Decimal


@dataclass
class SchedaDipendentePartitario:
    dipendente: Dipendente
    blocchi_anno: list[BloccoAnnoPartitario]
    tot_avere: Decimal
    tot_dare: Decimal
    saldo_finale: Decimal


def qs_buste(
    azienda: Azienda,
    *,
    dipendente_id: int | None = None,
    anno: int | None = None,
    mese: int | None = None,
) -> QuerySet[MovimentoImportPaghe]:
    qs = (
        MovimentoImportPaghe.objects.filter(azienda=azienda, tipo='BUSTA')
        .select_related('dipendente', 'documento')
        .order_by('anno', 'mese', 'id')
    )
    if dipendente_id:
        qs = qs.filter(dipendente_id=dipendente_id)
    if anno:
        qs = qs.filter(anno=anno)
    if mese:
        qs = qs.filter(mese=mese)
    return qs


def qs_pagamenti(
    azienda: Azienda,
    *,
    dipendente_id: int | None = None,
    anno: int | None = None,
) -> QuerySet[PagamentoPartitarioPaghe]:
    qs = PagamentoPartitarioPaghe.objects.filter(azienda=azienda).select_related(
        'dipendente', 'movimento_busta'
    )
    if dipendente_id:
        qs = qs.filter(dipendente_id=dipendente_id)
    if anno:
        qs = qs.filter(data_pagamento__year=anno)
    return qs.order_by('data_pagamento', 'id')


def _data_busta(mov: MovimentoImportPaghe) -> date:
    if mov.anno and mov.mese:
        try:
            return date(int(mov.anno), int(mov.mese), 1)
        except (TypeError, ValueError):
            pass
    return date(int(mov.anno or date.today().year), 1, 1)


def _periodo_busta(mov: MovimentoImportPaghe) -> str:
    if mov.periodo_label:
        return mov.periodo_label
    if mov.mese and mov.anno:
        return f'{int(mov.mese):02d}/{int(mov.anno)}'
    return '—'


def righe_libro_partitario(
    dipendente: Dipendente,
    *,
    anno: int | None = None,
) -> list[RigaLibroPartitario]:
    """Righe ordinate cronologicamente con saldo progressivo."""
    azienda = dipendente.azienda
    righe_raw: list[RigaLibroPartitario] = []

    for mov in qs_buste(azienda, dipendente_id=dipendente.pk, anno=anno):
        periodo = _periodo_busta(mov)
        netto = importo_netto_busta(mov)
        righe_raw.append(
            RigaLibroPartitario(
                data_ord=_data_busta(mov),
                data_label=periodo,
                desc_dare='',
                dare=Decimal('0'),
                desc_avere=f'Busta paga {periodo}',
                avere=netto,
                documento_id=mov.documento_id,
                busta_movimento_id=mov.pk,
            )
        )

    for pag in qs_pagamenti(azienda, dipendente_id=dipendente.pk, anno=anno):
        desc = (pag.descrizione or '').strip()
        if pag.riferimento_bancario:
            rif = pag.riferimento_bancario.strip()
            desc = f'{desc} — {rif}' if desc else rif
        if not desc:
            desc = 'Bonifico stipendio'
        righe_raw.append(
            RigaLibroPartitario(
                data_ord=pag.data_pagamento,
                data_label=pag.data_pagamento.strftime('%d/%m/%Y'),
                desc_dare=desc,
                dare=pag.importo or Decimal('0'),
                desc_avere='',
                avere=Decimal('0'),
                pagamento_id=pag.pk,
                busta_movimento_id=pag.movimento_busta_id,
            )
        )

    righe_raw.sort(key=lambda r: (r.data_ord, 0 if r.avere > 0 else 1, r.pagamento_id or 0))

    saldo = Decimal('0')
    for r in righe_raw:
        saldo = (saldo + r.avere - r.dare).quantize(Decimal('0.01'))
        r.saldo = saldo
    return righe_raw


def raggruppa_righe_per_anno(righe: list[RigaLibroPartitario]) -> list[BloccoAnnoPartitario]:
    per_anno: dict[int, list[RigaLibroPartitario]] = {}
    for r in righe:
        per_anno.setdefault(r.data_ord.year, []).append(r)
    blocchi: list[BloccoAnnoPartitario] = []
    for anno in sorted(per_anno.keys(), reverse=True):
        rs = per_anno[anno]
        tot_avere = sum((x.avere for x in rs), start=Decimal('0'))
        tot_dare = sum((x.dare for x in rs), start=Decimal('0'))
        saldo_finale = rs[-1].saldo if rs else Decimal('0')
        blocchi.append(
            BloccoAnnoPartitario(
                anno=anno,
                righe=rs,
                tot_avere=tot_avere,
                tot_dare=tot_dare,
                saldo_finale=saldo_finale,
            )
        )
    return blocchi


def scheda_dipendente_partitario(
    dipendente: Dipendente,
    *,
    anno: int | None = None,
) -> SchedaDipendentePartitario:
    righe = righe_libro_partitario(dipendente, anno=anno)
    blocchi = raggruppa_righe_per_anno(righe)
    tot_avere = sum((b.tot_avere for b in blocchi), start=Decimal('0'))
    tot_dare = sum((b.tot_dare for b in blocchi), start=Decimal('0'))
    saldo_finale = righe[-1].saldo if righe else Decimal('0')
    return SchedaDipendentePartitario(
        dipendente=dipendente,
        blocchi_anno=blocchi,
        tot_avere=tot_avere,
        tot_dare=tot_dare,
        saldo_finale=saldo_finale,
    )


def schede_azienda_partitario(
    azienda: Azienda,
    *,
    dipendente_id: int | None = None,
    anno: int | None = None,
) -> list[SchedaDipendentePartitario]:
    dip_qs = Dipendente.objects.filter(azienda=azienda, stato='attivo').order_by('cognome', 'nome')
    if dipendente_id:
        dip_qs = dip_qs.filter(pk=dipendente_id)
    return [scheda_dipendente_partitario(d, anno=anno) for d in dip_qs]


def anni_disponibili_partitario(azienda: Azienda) -> list[int]:
    anni_b = set(
        MovimentoImportPaghe.objects.filter(azienda=azienda, tipo='BUSTA').values_list('anno', flat=True)
    )
    anni_p = set(
        PagamentoPartitarioPaghe.objects.filter(azienda=azienda).values_list(
            'data_pagamento__year', flat=True
        )
    )
    anni = sorted({a for a in anni_b | anni_p if a}, reverse=True)
    return anni or [date.today().year]


def buste_scelta_collegamento(
    dipendente: Dipendente,
    *,
    anno: int | None = None,
) -> list[MovimentoImportPaghe]:
    """Buste del dipendente per collegare un pagamento (select opzionale)."""
    return list(qs_buste(dipendente.azienda, dipendente_id=dipendente.pk, anno=anno))


def riepilogo_generale_hub(schede: list[SchedaDipendentePartitario]) -> dict[str, Any]:
    tot_avere = sum((s.tot_avere for s in schede), start=Decimal('0'))
    tot_dare = sum((s.tot_dare for s in schede), start=Decimal('0'))
    return {
        'tot_avere': tot_avere,
        'tot_dare': tot_dare,
        'saldo_complessivo': sum((s.saldo_finale for s in schede), start=Decimal('0')),
        'n_dipendenti': len(schede),
    }


def registra_pagamento_partitario(
    *,
    azienda: Azienda,
    dipendente: Dipendente,
    data_pagamento: date,
    importo: Decimal,
    descrizione: str,
    riferimento_bancario: str,
    movimento_busta_id: int | None,
    utente,
) -> PagamentoPartitarioPaghe:
    movimento_busta = None
    if movimento_busta_id:
        movimento_busta = MovimentoImportPaghe.objects.filter(
            pk=movimento_busta_id,
            azienda=azienda,
            dipendente=dipendente,
            tipo='BUSTA',
        ).first()
    return PagamentoPartitarioPaghe.objects.create(
        azienda=azienda,
        dipendente=dipendente,
        data_pagamento=data_pagamento,
        descrizione=(descrizione or 'Bonifico stipendio')[:220],
        importo=importo.quantize(Decimal('0.01')),
        riferimento_bancario=(riferimento_bancario or '')[:160],
        movimento_busta=movimento_busta,
        registrato_da=utente,
    )


def elimina_pagamento_partitario(pagamento_id: int, azienda: Azienda) -> bool:
    deleted, _ = PagamentoPartitarioPaghe.objects.filter(pk=pagamento_id, azienda=azienda).delete()
    return deleted > 0
