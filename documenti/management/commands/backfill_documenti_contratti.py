from __future__ import annotations

from pathlib import Path

from django.core.files.base import ContentFile
from django.core.management.base import BaseCommand, CommandError

from documenti.models import Documento
from rapporto_di_lavoro.models import RapportoDiLavoro


class Command(BaseCommand):
    help = (
        "Backfill archivio Documenti per i contratti: crea Documento(tipo=contratto) "
        "da RapportoDiLavoro.file_contratto_pdf; se il file manca su storage puo rigenerarlo."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--azienda-id",
            type=int,
            default=None,
            help="Limita il backfill a una specifica azienda.",
        )
        parser.add_argument(
            "--anno",
            type=int,
            default=None,
            help="Limita ai rapporti con data_inizio_rapporto nell'anno indicato.",
        )
        parser.add_argument(
            "--force-rigenera",
            action="store_true",
            help="Rigenera sempre il PDF dal rapporto anche se il file esiste.",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Simula senza scrivere su DB/storage.",
        )

    def handle(self, *args, **options):
        azienda_id = options.get("azienda_id")
        anno = options.get("anno")
        force_rigenera = bool(options.get("force_rigenera"))
        dry_run = bool(options.get("dry_run"))

        qs = RapportoDiLavoro.objects.select_related("azienda", "dipendente").all().order_by("id")
        if azienda_id is not None:
            qs = qs.filter(azienda_id=azienda_id)
        if anno is not None:
            if anno < 1900 or anno > 2500:
                raise CommandError(f"Anno non valido: {anno}")
            qs = qs.filter(data_inizio_rapporto__year=anno)

        # Import locale: evita side effects all'avvio comando.
        from rapporto_di_lavoro.views import _genera_contratto_pdf_bytes

        counters = {
            "rapporti_scansionati": 0,
            "pdf_rigenerati": 0,
            "documenti_creati": 0,
            "documenti_gia_presenti": 0,
            "rapporti_senza_dipendente_o_azienda": 0,
            "errori": 0,
        }

        for rapporto in qs.iterator(chunk_size=200):
            counters["rapporti_scansionati"] += 1

            if not rapporto.dipendente_id or not rapporto.azienda_id:
                counters["rapporti_senza_dipendente_o_azienda"] += 1
                continue

            numero = (rapporto.numero_contratto or "").strip() or f"RAP-{rapporto.id}"
            descrizione_doc = f"Contratto definitivo {numero}"

            esistente = Documento.objects.filter(
                azienda_id=rapporto.azienda_id,
                dipendente_id=rapporto.dipendente_id,
                tipo="contratto",
                descrizione=descrizione_doc,
            ).first()
            if esistente:
                counters["documenti_gia_presenti"] += 1
                continue

            filefield = rapporto.file_contratto_pdf
            file_exists = False
            if filefield and filefield.name:
                try:
                    file_exists = bool(filefield.storage.exists(filefield.name))
                except Exception:
                    file_exists = False

            pdf_bytes = None
            try:
                if force_rigenera or not file_exists:
                    pdf_bytes = _genera_contratto_pdf_bytes(rapporto)
                    if not dry_run:
                        suggested = (
                            f"CONTRATTO_{(rapporto.dipendente.cognome or '').strip()}_"
                            f"{(rapporto.dipendente.nome or '').strip()}_{rapporto.id}.pdf"
                        ).replace(" ", "_")
                        rapporto.file_contratto_pdf.save(suggested, ContentFile(pdf_bytes), save=True)
                    counters["pdf_rigenerati"] += 1
                else:
                    if not dry_run:
                        filefield.open("rb")
                        try:
                            pdf_bytes = filefield.read()
                        finally:
                            filefield.close()
            except Exception as exc:
                counters["errori"] += 1
                self.stderr.write(f"[ERRORE PDF] rapporto={rapporto.id} n={numero}: {exc}")
                continue

            if dry_run:
                counters["documenti_creati"] += 1
                continue

            try:
                if pdf_bytes is None:
                    filefield = rapporto.file_contratto_pdf
                    filefield.open("rb")
                    try:
                        pdf_bytes = filefield.read()
                    finally:
                        filefield.close()
                basename = Path(rapporto.file_contratto_pdf.name or f"contratto_{rapporto.id}.pdf").name
                Documento.objects.create(
                    azienda_id=rapporto.azienda_id,
                    dipendente_id=rapporto.dipendente_id,
                    tipo="contratto",
                    descrizione=descrizione_doc,
                    file=ContentFile(pdf_bytes, name=basename),
                    caricato_da=getattr(rapporto, "utente_firma_admin", None),
                    caricato_dal_dipendente=False,
                    visibile_al_dipendente=True,
                )
                counters["documenti_creati"] += 1
            except Exception as exc:
                counters["errori"] += 1
                self.stderr.write(f"[ERRORE DOC] rapporto={rapporto.id} n={numero}: {exc}")

        mode = "DRY RUN" if dry_run else "APPLY"
        self.stdout.write(self.style.SUCCESS(f"{mode} completato backfill_documenti_contratti"))
        for k, v in counters.items():
            self.stdout.write(f" - {k}: {v}")
