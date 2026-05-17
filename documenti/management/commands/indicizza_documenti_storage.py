from __future__ import annotations

import re
import unicodedata
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from anagrafiche.models import Azienda, Dipendente
from documenti.models import Documento
from documenti.upload_paths import subdir_for_documento_tipo


SUPPORTED_EXTENSIONS = {".pdf", ".png", ".jpg", ".jpeg", ".webp"}
CF_RE = re.compile(r"\b([A-Z]{6}[0-9]{2}[A-Z][0-9]{2}[A-Z][0-9]{3}[A-Z])\b", re.IGNORECASE)


def _norm_text(value: str) -> str:
    txt = (value or "").strip().upper()
    txt = unicodedata.normalize("NFKD", txt).encode("ascii", "ignore").decode("ascii")
    txt = re.sub(r"[^A-Z0-9]+", " ", txt)
    return re.sub(r"\s+", " ", txt).strip()


class Command(BaseCommand):
    help = (
        "Indicizza file sotto MEDIA_ROOT nelle cartelle tipo (buste_paghe, cud, …) creando record Documento "
        "e agganciando il dipendente quando rilevabile da CF o nome/cognome filename."
    )

    def add_arguments(self, parser):
        parser.add_argument("--azienda-id", type=int, required=True, help="ID azienda target")
        parser.add_argument("--applica", action="store_true", help="Applica su DB (default: dry-run)")
        parser.add_argument("--solo-tipo", type=str, default="", help="Indicizza solo un tipo documento")
        parser.add_argument(
            "--includi-estensioni",
            type=str,
            default="pdf,png,jpg,jpeg,webp",
            help="Lista estensioni separate da virgola",
        )

    def handle(self, *args, **options):
        try:
            azienda = Azienda.objects.get(id=options["azienda_id"])
        except Azienda.DoesNotExist as exc:
            raise CommandError(f"Azienda non trovata: id={options['azienda_id']}") from exc

        media_root = Path(settings.MEDIA_ROOT)
        if not media_root.exists():
            raise CommandError(f"MEDIA_ROOT non esiste: {media_root}")

        apply_mode = bool(options.get("applica"))
        only_tipo = (options.get("solo_tipo") or "").strip()
        ext_csv = (options.get("includi_estensioni") or "").strip()
        allowed_ext = {
            ("." + x.strip().lower().lstrip(".")) for x in ext_csv.split(",") if x.strip()
        } or set(SUPPORTED_EXTENSIONS)

        tipo_choices = {code for code, _ in Documento.TIPO_CHOICES}
        if only_tipo and only_tipo not in tipo_choices:
            raise CommandError(f"Tipo non valido: {only_tipo}")

        tipo_to_subdir = {}
        for tipo in tipo_choices:
            if only_tipo and tipo != only_tipo:
                continue
            tipo_to_subdir[tipo] = subdir_for_documento_tipo(tipo)

        subdir_to_tipo = {v.strip("/"): k for k, v in tipo_to_subdir.items()}

        dip_by_cf = {
            (d.codice_fiscale or "").strip().upper(): d
            for d in Dipendente.objects.filter(azienda=azienda).exclude(codice_fiscale__isnull=True)
            if (d.codice_fiscale or "").strip()
        }
        dip_by_name = {}
        for d in Dipendente.objects.filter(azienda=azienda):
            key = _norm_text(f"{d.cognome} {d.nome}")
            if key and key not in dip_by_name:
                dip_by_name[key] = d

        scanned = 0
        skipped_existing = 0
        created = 0
        unmatched = 0
        unknown_paths = 0
        invalid_ext = 0

        for subdir, tipo in subdir_to_tipo.items():
            abs_dir = media_root / subdir
            if not abs_dir.exists() or not abs_dir.is_dir():
                continue

            for f in abs_dir.rglob("*"):
                if not f.is_file():
                    continue
                scanned += 1

                ext = f.suffix.lower()
                if ext not in allowed_ext:
                    invalid_ext += 1
                    continue

                try:
                    rel = f.relative_to(media_root).as_posix()
                except ValueError:
                    unknown_paths += 1
                    continue

                if Documento.objects.filter(azienda=azienda, file=rel).exists():
                    skipped_existing += 1
                    continue

                filename_norm = _norm_text(f.stem)
                cf_match = CF_RE.search(f.name or "")
                dip = None
                if cf_match:
                    dip = dip_by_cf.get(cf_match.group(1).upper())

                if dip is None and filename_norm:
                    for key, candidate in dip_by_name.items():
                        if key and key in filename_norm:
                            dip = candidate
                            break

                if dip is None and tipo in {"busta_paga", "unilav", "riepilogo_mensile", "contratto"}:
                    unmatched += 1

                if apply_mode:
                    doc = Documento(
                        azienda=azienda,
                        dipendente=dip,
                        tipo=tipo,
                        descrizione=f.stem[:200],
                        file=rel,
                        caricato_da=None,
                        caricato_dal_dipendente=False,
                        visibile_al_dipendente=(dip is not None),
                    )
                    doc.save()
                    created += 1
                else:
                    self.stdout.write(
                        f"[DRY] tipo={tipo} file={rel} dip={dip.id if dip else '-'}"
                    )

        mode = "APPLY" if apply_mode else "DRY-RUN"
        self.stdout.write(
            self.style.SUCCESS(
                f"{mode} completato: scansionati={scanned}, creati={created}, "
                f"esistenti={skipped_existing}, senza_match={unmatched}, "
                f"estensione_non_ammessa={invalid_ext}, path_invalidi={unknown_paths}"
            )
        )

