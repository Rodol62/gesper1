"""
Riesegue l’acquisizione canonica (:mod:`documenti.busta_acquisizione`) su tutte le buste
paga di un anno e, per i PDF elaborabili con motore v4, aggiorna ``CedolinoMotoreV4``
senza un secondo ``parse_bytes`` (bundle precalcolato).

  python manage.py ricalcola_buste_acquisizione --anno 2024 --azienda-id 1
  python manage.py ricalcola_buste_acquisizione --anno 2024 --azienda-id 1 --dry-run
  python manage.py ricalcola_buste_acquisizione --anno 2024 --azienda-id 1 --limit 5 -v 2

Criterio anno e queryset: come ``documenti.buste_cedolino_batch.queryset_buste_anno``.
"""

from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError

from anagrafiche.models import Azienda

from documenti.busta_acquisizione import acquisisci_busta_da_documento, leggi_pdf_busta_documento
from documenti.buste_cedolino_batch import queryset_buste_anno
from documenti.cedolino_estrazione_v4_store import tenta_persistenza_cedolino_v4_dopo_lettura


class Command(BaseCommand):
    help = (
        "Ricalcola acquisizione buste (pipeline unica) e rimemorizza CedolinoMotoreV4 dove possibile."
    )

    def add_arguments(self, parser):
        parser.add_argument("--anno", type=int, required=True, help="Anno (es. 2024)")
        parser.add_argument(
            "--azienda-id",
            type=int,
            required=True,
            help="ID azienda (anagrafiche.Azienda)",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Solo statistiche in stdout, nessun salvataggio su DB",
        )
        parser.add_argument(
            "--limit",
            type=int,
            default=0,
            metavar="N",
            help="Elabora al massimo N documenti (0 = tutti)",
        )
        parser.add_argument(
            "--no-persist",
            action="store_true",
            help="Non chiamare la persistenza v4 (solo lettura / conteggi)",
        )

    def handle(self, *args, **options):
        anno = int(options["anno"])
        aid = int(options["azienda_id"])
        dry = bool(options["dry_run"])
        no_persist = bool(options["no_persist"])
        limit = int(options["limit"] or 0)

        azienda = Azienda.objects.filter(pk=aid).first()
        if not azienda:
            raise CommandError(f"Azienda id={aid} non trovata.")

        qs = queryset_buste_anno(azienda, anno)
        total_qs = qs.count()
        if total_qs == 0:
            raise CommandError(
                f"Nessun documento busta per anno {anno} e azienda {azienda.nome}."
            )

        self.stdout.write(
            f"Documenti in elenco: {total_qs} (anno {anno}, {azienda.nome})"
            + (" [DRY-RUN]" if dry else "")
            + (" [senza persistenza]" if no_persist and not dry else "")
        )

        n_no_file = 0
        n_lettura_err = 0
        n_legacy = 0
        n_v4 = 0
        n_persist_ok = 0
        n_persist_skip = 0
        n_processed = 0
        failures: list[str] = []

        for doc in qs.iterator(chunk_size=50):
            if limit and n_processed >= limit:
                break
            n_processed += 1

            raw = leggi_pdf_busta_documento(doc)
            if not raw or len(raw) < 5 or raw[:5] != b"%PDF":
                n_no_file += 1
                if options["verbosity"] >= 2:
                    failures.append(f"doc {doc.pk}: file PDF mancante o non valido")
                continue

            res = acquisisci_busta_da_documento(doc, raw_pdf=raw)
            if res.errore:
                n_lettura_err += 1
                failures.append(f"doc {doc.pk}: {res.errore}")
                continue

            if (res.motore or "").strip() == "posizionale_v4":
                n_v4 += 1
            else:
                n_legacy += 1

            if dry or no_persist:
                continue

            if (res.motore or "").strip() != "posizionale_v4":
                continue

            ok = tenta_persistenza_cedolino_v4_dopo_lettura(
                doc,
                raw,
                res.report,
                password=res.password_usata or "",
                c_precalcolato=res.cedolino_v4,
                calc_precalcolato=res.calc_v4,
                checks_precalcolato=res.checks_v4,
            )
            if ok:
                n_persist_ok += 1
            else:
                n_persist_skip += 1
                if options["verbosity"] >= 2:
                    failures.append(
                        f"doc {doc.pk}: motore v4 ma persistenza non riuscita "
                        f"(dipendente assente o mese/anno mancanti?)"
                    )

        self.stdout.write(
            f"Elaborati: {n_processed}"
            f" | senza PDF: {n_no_file}"
            f" | errore lettura: {n_lettura_err}"
            f" | motore v4: {n_v4}"
            f" | legacy: {n_legacy}"
        )
        if dry:
            self.stdout.write(
                self.style.WARNING("Dry-run: nessun dato scritto sul database.")
            )
        elif no_persist:
            self.stdout.write(
                self.style.WARNING(
                    "--no-persist: lettura eseguita ma nessun aggiornamento CedolinoMotoreV4."
                )
            )
        else:
            self.stdout.write(
                self.style.SUCCESS(
                    f"CedolinoMotoreV4 aggiornati: {n_persist_ok} | tentativi v4 non salvati: {n_persist_skip}"
                )
            )

        if options["verbosity"] >= 2 and failures:
            self.stdout.write(self.style.WARNING("Dettaglio problemi:"))
            for line in failures[:200]:
                self.stdout.write(f"  - {line}")
            if len(failures) > 200:
                self.stdout.write(f"  … altre {len(failures) - 200} righe")
