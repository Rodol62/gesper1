"""
Riclassifica documenti pagamento (legacy ``ricevuta_pagamento_netto``) come
``pagamento_dipendente`` con descrizione canonica e collega ``PagamentoPartitarioPaghe``.

  python manage.py classifica_documenti_pagamento_dipendente --azienda-id 1
  python manage.py classifica_documenti_pagamento_dipendente --azienda-id 1 --dipendente-id 15
  python manage.py classifica_documenti_pagamento_dipendente --azienda-id 1 --dry-run
"""

from __future__ import annotations

import re
from datetime import datetime
from decimal import Decimal

from django.core.management.base import BaseCommand, CommandError
from django.db import connection, transaction
from django.db.models import Q

from accounts.models import PagamentoPartitarioPaghe
from anagrafiche.models import Azienda, Dipendente
from documenti.models import Documento
from documenti.pagamento_dipendente import (
    TIPO_DOCUMENTO_PAGAMENTO_DIPENDENTE,
    descrizione_documento_pagamento_dipendente,
    normalizza_descrizione_legacy_pagamento,
)


def _data_da_descrizione(desc: str):
    m = re.search(r"(\d{2}/\d{2}/\d{4})", desc or "")
    if m:
        try:
            return datetime.strptime(m.group(1), "%d/%m/%Y").date()
        except ValueError:
            return None
    return None


def _periodo_da_descrizione(desc: str) -> str:
    desc_u = (desc or "").upper()
    m = re.search(r"COMPETENZA\s+([A-ZÀ-Ú]+)\s+(\d{4})", desc_u)
    if m:
        return f"{m.group(1).capitalize()} {m.group(2)}"
    m2 = re.search(r"(\d{2})/(\d{4})", desc or "")
    if m2:
        mesi = (
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
        mi = int(m2.group(1))
        if 1 <= mi <= 12:
            return f"{mesi[mi]} {m2.group(2)}"
    return ""


class Command(BaseCommand):
    help = "Imposta tipo Pagamento dipendente e descrizione standard sui documenti ricevuta legacy."

    def add_arguments(self, parser):
        parser.add_argument("--azienda-id", type=int, required=True)
        parser.add_argument("--dipendente-id", type=int, default=0)
        parser.add_argument("--dry-run", action="store_true")

    @transaction.atomic
    def handle(self, *args, **options):
        aid = int(options["azienda_id"])
        az = Azienda.objects.filter(pk=aid).first()
        if not az:
            raise CommandError(f"Azienda id={aid} non trovata.")

        dip_f = int(options["dipendente_id"] or 0) or None
        dry = bool(options["dry_run"])

        qs = Documento.objects.filter(azienda=az).filter(
            Q(tipo="ricevuta_pagamento_netto")
            | Q(tipo=TIPO_DOCUMENTO_PAGAMENTO_DIPENDENTE, descrizione__icontains="Ricevuta ")
        )
        if dip_f:
            qs = qs.filter(dipendente_id=dip_f)

        aggiornati = 0
        collegati = 0

        for doc in qs.select_related("dipendente").order_by("id"):
            if not doc.dipendente_id:
                continue
            metodo, causale, imp_desc = normalizza_descrizione_legacy_pagamento(doc.descrizione)
            data_pag = _data_da_descrizione(doc.descrizione) or (
                doc.data_caricamento.date() if doc.data_caricamento else None
            )
            if not data_pag:
                self.stdout.write(self.style.WARNING(f"doc {doc.id}: data non ricavata, skip"))
                continue

            importo = imp_desc
            if importo is None:
                # Prova legacy partitario_netti (documento_ricevuta_id)
                with connection.cursor() as cursor:
                    cursor.execute(
                        f"""
                        SELECT importo FROM partitario_netti_movimentopartitarionettodipendente
                        WHERE documento_ricevuta_id = {int(doc.id)} LIMIT 1
                        """
                    )
                    row = cursor.fetchone()
                    if row:
                        importo = Decimal(str(row[0]))
            if importo is None:
                pag = PagamentoPartitarioPaghe.objects.filter(
                    azienda=az,
                    dipendente_id=doc.dipendente_id,
                    data_pagamento=data_pag,
                ).order_by("-id").first()
                if pag:
                    importo = pag.importo
            if importo is None:
                self.stdout.write(self.style.WARNING(f"doc {doc.id}: importo non ricavato, skip"))
                continue

            periodo = _periodo_da_descrizione(doc.descrizione)
            nuova_desc = descrizione_documento_pagamento_dipendente(
                data_pagamento=data_pag,
                importo=importo,
                metodo=metodo,
                causale=causale or "Pagamento stipendio",
                periodo_competenza=periodo,
            )

            self.stdout.write(
                f"{'[DRY] ' if dry else ''}doc {doc.id} {doc.dipendente.cognome}: "
                f"{doc.tipo} -> {TIPO_DOCUMENTO_PAGAMENTO_DIPENDENTE}"
            )
            self.stdout.write(f"  {nuova_desc}")

            if not dry:
                doc.tipo = TIPO_DOCUMENTO_PAGAMENTO_DIPENDENTE
                doc.descrizione = nuova_desc
                doc.save(update_fields=["tipo", "descrizione"])

                pag = PagamentoPartitarioPaghe.objects.filter(
                    azienda=az,
                    dipendente_id=doc.dipendente_id,
                    data_pagamento=data_pag,
                    importo=importo,
                ).first()
                if pag is None:
                    pag = PagamentoPartitarioPaghe.objects.filter(
                        azienda=az,
                        dipendente_id=doc.dipendente_id,
                        importo=importo,
                    ).order_by("data_pagamento", "id").first()
                if pag and pag.documento_id is None:
                    pag.documento = doc
                    pag.save(update_fields=["documento"])
                    collegati += 1

            aggiornati += 1

        self.stdout.write(
            self.style.SUCCESS(
                f"Documenti aggiornati: {aggiornati} | pagamenti collegati: {collegati}"
                + (" (DRY-RUN)" if dry else "")
            )
        )
