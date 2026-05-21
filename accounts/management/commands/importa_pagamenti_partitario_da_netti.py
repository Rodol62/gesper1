"""
Importa pagamenti dipendente dal partitario netti legacy
(``partitario_netti_movimentopartitarionettodipendente``) verso ``PagamentoPartitarioPaghe``.

I documenti ``ricevuta_pagamento_netto`` restano in archivio; il partitario paghe nuovo
mostra le righe in Dare (bonifici/contanti).

  python manage.py importa_pagamenti_partitario_da_netti --azienda-id 1
  python manage.py importa_pagamenti_partitario_da_netti --azienda-id 1 --dipendente-id 15
  python manage.py importa_pagamenti_partitario_da_netti --azienda-id 1 --dry-run
"""

from __future__ import annotations

from decimal import Decimal

from django.core.management.base import BaseCommand, CommandError
from django.db import connection, transaction

from accounts.models import MovimentoImportPaghe, PagamentoPartitarioPaghe
from anagrafiche.models import Azienda, Dipendente


def _tabella_netti_disponibile() -> bool:
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT name FROM sqlite_master
            WHERE type='table' AND name='partitario_netti_movimentopartitarionettodipendente'
            """
        )
        return cursor.fetchone() is not None


def _leggi_pagamenti_netti_legacy(
    azienda_id: int,
    *,
    dipendente_id: int | None = None,
) -> list[dict]:
    aid = int(azienda_id)
    sql = f"""
        SELECT id, dipendente_id, anno, mese, data_contabile, importo,
               metodo_pagamento, causale, documento_ricevuta_id
        FROM partitario_netti_movimentopartitarionettodipendente
        WHERE azienda_id = {aid} AND tipo_movimento = 'pagamento'
    """
    if dipendente_id:
        sql += f" AND dipendente_id = {int(dipendente_id)}"
    sql += " ORDER BY data_contabile, id"
    with connection.cursor() as cursor:
        cursor.execute(sql)
        cols = [c[0] for c in cursor.description]
        return [dict(zip(cols, row)) for row in cursor.fetchall()]


def _movimento_busta_per_periodo(
    azienda_id: int,
    dipendente_id: int,
    anno: int,
    mese: int,
) -> MovimentoImportPaghe | None:
    return (
        MovimentoImportPaghe.objects.filter(
            azienda_id=azienda_id,
            dipendente_id=dipendente_id,
            tipo="BUSTA",
            anno=anno,
            mese=mese,
            natura_busta="ORDINARIA",
        )
        .order_by("-id")
        .first()
    )


class Command(BaseCommand):
    help = (
        "Copia i pagamenti (bonifici/contanti) dal partitario netti legacy "
        "nel modello PagamentoPartitarioPaghe del partitario paghe."
    )

    def add_arguments(self, parser):
        parser.add_argument("--azienda-id", type=int, required=True)
        parser.add_argument("--dipendente-id", type=int, default=0)
        parser.add_argument("--dry-run", action="store_true")

    @transaction.atomic
    def handle(self, *args, **options):
        if not _tabella_netti_disponibile():
            raise CommandError(
                "Tabella partitario_netti_movimentopartitarionettodipendente assente "
                "(nessun dato legacy da importare)."
            )

        aid = int(options["azienda_id"])
        az = Azienda.objects.filter(pk=aid).first()
        if not az:
            raise CommandError(f"Azienda id={aid} non trovata.")

        dip_f = int(options["dipendente_id"] or 0) or None
        dry = bool(options["dry_run"])

        righe = _leggi_pagamenti_netti_legacy(aid, dipendente_id=dip_f)
        if not righe:
            self.stdout.write(self.style.WARNING("Nessun pagamento legacy trovato."))
            return

        creati = 0
        saltati = 0

        for r in righe:
            dip = Dipendente.objects.filter(pk=r["dipendente_id"], azienda=az).first()
            if not dip:
                saltati += 1
                continue

            data_pag = r["data_contabile"]
            importo = Decimal(str(r["importo"])).quantize(Decimal("0.01"))
            metodo = (r["metodo_pagamento"] or "").strip().capitalize() or "Pagamento"
            causale = (r["causale"] or "").strip()
            desc = f"{metodo} — {causale}" if causale else metodo
            desc = desc[:220]

            gia = PagamentoPartitarioPaghe.objects.filter(
                azienda=az,
                dipendente=dip,
                data_pagamento=data_pag,
                importo=importo,
                descrizione=desc,
            ).exists()
            if gia:
                saltati += 1
                self.stdout.write(f"SKIP legacy id={r['id']} già presente")
                continue

            mov_busta = _movimento_busta_per_periodo(
                az.pk,
                dip.pk,
                int(r["anno"]),
                int(r["mese"]),
            )

            self.stdout.write(
                f"{'[DRY] ' if dry else ''}legacy {r['id']} -> {dip.cognome} "
                f"{data_pag:%d/%m/%Y} € {importo} busta={mov_busta.pk if mov_busta else '-'}"
            )
            if not dry:
                PagamentoPartitarioPaghe.objects.create(
                    azienda=az,
                    dipendente=dip,
                    data_pagamento=data_pag,
                    descrizione=desc,
                    importo=importo,
                    riferimento_bancario="",
                    movimento_busta=mov_busta,
                    registrato_da=None,
                )
            creati += 1

        self.stdout.write(
            self.style.SUCCESS(
                f"Creati: {creati} | saltati: {saltati}" + (" (DRY-RUN)" if dry else "")
            )
        )
