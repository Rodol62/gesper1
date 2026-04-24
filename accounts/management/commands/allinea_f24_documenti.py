from __future__ import annotations

import re
from collections import defaultdict

from django.core.management.base import BaseCommand
from django.db import transaction

from accounts.models import MovimentoImportPaghe
from documenti.models import Documento


def _periodo_from_text(value: str) -> tuple[int | None, int | None]:
    txt = (value or "").upper()
    m = re.search(r"\b(0?[1-9]|1[0-2])\s*/\s*(20\d{2})\b", txt)
    if not m:
        return None, None
    return int(m.group(1)), int(m.group(2))


class Command(BaseCommand):
    help = "Allinea i movimenti F24 senza documento ai Documento F24 esistenti."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Mostra le associazioni candidate senza salvare.",
        )

    @transaction.atomic
    def handle(self, *args, **options):
        dry_run = bool(options.get("dry_run"))

        docs = (
            Documento.objects.filter(tipo="altro")
            .filter(descrizione__icontains="F24")
            .select_related("azienda")
            .order_by("-data_caricamento", "-id")
        )

        docs_by_key: dict[tuple[int, int, int], list[Documento]] = defaultdict(list)
        for doc in docs:
            mese, anno = _periodo_from_text(doc.descrizione or "")
            if mese and anno and doc.azienda_id:
                docs_by_key[(doc.azienda_id, anno, mese)].append(doc)

        mov_qs = (
            MovimentoImportPaghe.objects.filter(tipo="F24", documento__isnull=True)
            .select_related("azienda")
            .order_by("azienda_id", "anno", "mese", "id")
        )

        tot = mov_qs.count()
        linked = 0
        unmatched = 0
        ambiguous = 0

        for mov in mov_qs:
            key = (mov.azienda_id, mov.anno, mov.mese)
            candidates = docs_by_key.get(key, [])
            if not candidates:
                unmatched += 1
                continue
            if len(candidates) > 1:
                # Ambiguo: se ci sono piu doc nello stesso periodo, non forzare.
                ambiguous += 1
                continue

            doc = candidates[0]
            self.stdout.write(
                f"{'[DRY] ' if dry_run else ''}LINK mov={mov.id} {mov.mese:02d}/{mov.anno} -> doc={doc.id}"
            )
            linked += 1
            if not dry_run:
                mov.documento = doc
                mov.save(update_fields=["documento"])

        if dry_run:
            transaction.set_rollback(True)

        self.stdout.write(
            self.style.SUCCESS(
                f"Allineamento F24 completato: movimenti={tot}, collegati={linked}, "
                f"senza_match={unmatched}, ambigui={ambiguous}, dry_run={dry_run}"
            )
        )

