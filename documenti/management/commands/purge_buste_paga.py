"""
Pulizia dell'ecosistema «buste paga» in Gesper: documenti PDF, movimenti import paghe,
estrazioni motore v4, opzionalmente F24 da stesso flusso import e Libro Unico storico.

Tabelle / modelli tipicamente coinvolti (nessuna DROP di schema Django):
  - ``documenti.Documento`` (tipo busta_paga; opz. tipo altro = PDF F24 da import)
  - ``accounts.MovimentoImportPaghe`` (tipo BUSTA; opz. F24) e ``MovimentoImportPagheF24Dettaglio``
  - ``documenti.CedolinoMotoreV4`` (+ voci/validazioni CASCADE)
  - ``storico.LibroPagaStorico`` (opzionale: registro LUL popolato da buste / popolalibropaga)

Non tocca: dipendenti, ``SimulazionePagaSalvata``, altri tipi di ``Documento`` (salvo opzione ``--include-cud``).

Eliminazione singola di un ``Documento`` con ``tipo=busta_paga`` (lista documenti, archivio,
gestione DB): rimuove anche le righe ``CedolinoMotoreV4`` collegate allo stesso PDF
(segnale ``pre_delete``), tranne durante questo comando con ``--keep-cedolini-v4``.

Esempi::

    python manage.py purge_buste_paga --dry-run
    python manage.py purge_buste_paga --dry-run --azienda-id=1
    python manage.py purge_buste_paga
    python manage.py purge_buste_paga --azienda-id=1 --include-f24 --libro-storico
    python manage.py purge_buste_paga --azienda-id=1 --include-f24 --include-cud   # reset completo import paghe
    python manage.py purge_buste_paga --keep-movimenti --keep-cedolini-v4   # solo file busta_paga
"""

from __future__ import annotations

from django.core.files.storage import default_storage
from django.core.management.base import BaseCommand
from django.db import transaction
from django.db.models import Q

from accounts.models import MovimentoImportPaghe
from documenti.models import CedolinoMotoreV4, Documento
from documenti.signals import busta_paga_delete_cascade_motore_v4


class Command(BaseCommand):
    help = (
        "Rimuove documenti busta_paga, movimenti import BUSTA (default), cedolini v4; "
        "opz. F24 import, CUD (certificato) e LibroPagaStorico."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Mostra solo i conteggi senza eliminare nulla.",
        )
        parser.add_argument(
            "--azienda-id",
            type=int,
            default=None,
            help="Limita l'operazione a un'azienda (documenti, movimenti, cedolini v4, libro).",
        )
        parser.add_argument(
            "--keep-cedolini-v4",
            action="store_true",
            help=(
                "Non cancellare CedolinoMotoreV4 in blocco né alla rimozione dei PDF busta_paga "
                "(le estrazioni restano; il collegamento documento va in NULL)."
            ),
        )
        parser.add_argument(
            "--keep-movimenti",
            action="store_true",
            help="Non cancellare MovimentoImportPaghe (BUSTA / F24 se --include-f24).",
        )
        parser.add_argument(
            "--include-f24",
            action="store_true",
            help="Cancella anche movimenti tipo F24, dettagli F24 e PDF Documento tipo=altro da flusso import (F24).",
        )
        parser.add_argument(
            "--include-cud",
            action="store_true",
            help="Cancella anche tutti i Documento tipo=certificato (CUD) dell'azienda filtrata.",
        )
        parser.add_argument(
            "--libro-storico",
            action="store_true",
            help="Cancella le righe storico.LibroPagaStorico (registro LUL non strettamente necessario al solo import buste).",
        )
        parser.add_argument(
            "--legacy-libro-paga",
            action="store_true",
            help="Cancella anche storico.LibroPaga (modello mensile semplice legacy), filtrato per azienda se indicata.",
        )

    def handle(self, *args, **options):
        dry = options["dry_run"]
        azienda_id = options.get("azienda_id")
        keep_v4 = options["keep_cedolini_v4"]
        keep_mov = options["keep_movimenti"]
        include_f24 = options["include_f24"]
        include_cud = options["include_cud"]
        libro_storico = options["libro_storico"]
        legacy_lp = options["legacy_libro_paga"]

        buste_qs = Documento.objects.filter(tipo="busta_paga")
        if azienda_id is not None:
            buste_qs = buste_qs.filter(azienda_id=azienda_id)
        n_buste = buste_qs.count()

        ced_qs = CedolinoMotoreV4.objects.all()
        if azienda_id is not None:
            ced_qs = ced_qs.filter(dipendente__azienda_id=azienda_id)
        n_v4 = ced_qs.count()

        mov_qs = MovimentoImportPaghe.objects.none()
        if not keep_mov:
            mov_qs = MovimentoImportPaghe.objects.all()
            if azienda_id is not None:
                mov_qs = mov_qs.filter(azienda_id=azienda_id)
            if include_f24:
                mov_qs = mov_qs.filter(tipo__in=("BUSTA", "F24"))
            else:
                mov_qs = mov_qs.filter(tipo="BUSTA")
        n_mov = mov_qs.count()

        f24_docs_qs = Documento.objects.none()
        if include_f24 and not keep_mov:
            # PDF F24 da import massivo: di solito dipendente nullo; descrizione o path sotto cartella f24.
            f24_docs_qs = Documento.objects.filter(
                tipo="altro",
                dipendente__isnull=True,
            ).filter(
                Q(descrizione__icontains="F24")
                | Q(descrizione__icontains="Modello F24")
                | Q(file__icontains="/f24/")
            )
            if azienda_id is not None:
                f24_docs_qs = f24_docs_qs.filter(azienda_id=azienda_id)
        n_f24_docs = f24_docs_qs.count()

        cud_docs_qs = Documento.objects.none()
        if include_cud and not keep_mov:
            cud_docs_qs = Documento.objects.filter(tipo="certificato")
            if azienda_id is not None:
                cud_docs_qs = cud_docs_qs.filter(azienda_id=azienda_id)
        n_cud_docs = cud_docs_qs.count()

        n_libro = n_legacy = 0
        if libro_storico:
            from storico.models import LibroPagaStorico

            lqs = LibroPagaStorico.objects.all()
            if azienda_id is not None:
                lqs = lqs.filter(azienda_id=azienda_id)
            n_libro = lqs.count()
        if legacy_lp:
            from storico.models import LibroPaga

            l2 = LibroPaga.objects.all()
            if azienda_id is not None:
                l2 = l2.filter(azienda_id=azienda_id)
            n_legacy = l2.count()

        if dry:
            self.stdout.write(
                self.style.WARNING(
                    "DRY RUN — conteggi:\n"
                    f"  Documento busta_paga: {n_buste}\n"
                    f"  CedolinoMotoreV4: {n_v4} ({'mantenuti' if keep_v4 else 'verrebbero eliminati'})\n"
                    f"  MovimentoImportPaghe: {n_mov} ({'nessuno (keep-movimenti)' if keep_mov else 'verrebbero eliminati'})\n"
                    f"  Documento F24 import (altro): {n_f24_docs if include_f24 and not keep_mov else 0}\n"
                    f"  Documento CUD (certificato): {n_cud_docs if include_cud and not keep_mov else 0}\n"
                    f"  LibroPagaStorico: {n_libro if libro_storico else 0}\n"
                    f"  LibroPaga legacy: {n_legacy if legacy_lp else 0}"
                )
            )
            return

        paths_to_delete: list[str] = []

        with transaction.atomic():
            if not keep_v4 and n_v4:
                del_v4, det = ced_qs.delete()
                self.stdout.write(
                    self.style.SUCCESS(
                        f"Eliminati {del_v4} oggetti legati a CedolinoMotoreV4: {det}"
                    )
                )

            if not keep_mov and n_mov:
                del_m, det_m = mov_qs.delete()
                self.stdout.write(
                    self.style.SUCCESS(
                        f"Eliminati {del_m} oggetti MovimentoImportPaghe (e dettagli F24 CASCADE): {det_m}"
                    )
                )

            if include_f24 and not keep_mov and n_f24_docs:
                for doc in f24_docs_qs.iterator(chunk_size=200):
                    if doc.file and doc.file.name:
                        paths_to_delete.append(doc.file.name)
                del_f, det_f = f24_docs_qs.delete()
                self.stdout.write(
                    self.style.SUCCESS(
                        f"Eliminati {del_f} documenti F24 import: {det_f}"
                    )
                )

            if include_cud and not keep_mov and n_cud_docs:
                for doc in cud_docs_qs.iterator(chunk_size=200):
                    if doc.file and doc.file.name:
                        paths_to_delete.append(doc.file.name)
                del_c, det_c = cud_docs_qs.delete()
                self.stdout.write(
                    self.style.SUCCESS(
                        f"Eliminati {del_c} documenti CUD (certificato): {det_c}"
                    )
                )

            if libro_storico and n_libro:
                from storico.models import LibroPagaStorico

                lqs = LibroPagaStorico.objects.all()
                if azienda_id is not None:
                    lqs = lqs.filter(azienda_id=azienda_id)
                dl, dtl = lqs.delete()
                self.stdout.write(
                    self.style.SUCCESS(f"Eliminati {dl} LibroPagaStorico: {dtl}")
                )

            if legacy_lp and n_legacy:
                from storico.models import LibroPaga

                l2 = LibroPaga.objects.all()
                if azienda_id is not None:
                    l2 = l2.filter(azienda_id=azienda_id)
                d2, dt2 = l2.delete()
                self.stdout.write(
                    self.style.SUCCESS(f"Eliminati {d2} LibroPaga legacy: {dt2}")
                )

            buste_list = list(buste_qs)
            for doc in buste_list:
                if doc.file and doc.file.name:
                    paths_to_delete.append(doc.file.name)

            ids = [d.pk for d in buste_list]
            if ids:
                tok = busta_paga_delete_cascade_motore_v4.set(not keep_v4)
                try:
                    Documento.objects.filter(pk__in=ids).delete()
                finally:
                    busta_paga_delete_cascade_motore_v4.reset(tok)

        for path in dict.fromkeys(paths_to_delete):
            try:
                if default_storage.exists(path):
                    default_storage.delete(path)
            except Exception as exc:
                self.stderr.write(
                    self.style.WARNING(f"File non rimosso {path!r}: {exc}")
                )

        self.stdout.write(
            self.style.SUCCESS(
                f"Completato: rimossi {n_buste} Documento (busta_paga) e file su storage ove noti."
            )
        )
        self.stdout.write(
            "Nuove buste: cartella in settings DOCUMENTO_TIPO_MEDIA_SUBDIRS "
            "(default buste_paghe/). "
            "Per F24 da import usare --include-f24; per CUD --include-cud; per LUL anche --libro-storico."
        )
