from __future__ import annotations

import json

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from documenti.file_path_resolution import first_existing_relpath_for_stored_name, stored_relpath_equivalent
from documenti.models import Documento


class Command(BaseCommand):
    help = (
        "Confronta path Documento.file nel DB con file su storage (MEDIA). "
        "Solo lettura: non modifica record. Stessa logica del riallineamento in UI."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--azienda-id",
            type=int,
            default=None,
            help="Limita a un'azienda (default: tutte).",
        )
        parser.add_argument(
            "--tipo",
            type=str,
            default="",
            help="Filtro Documento.tipo (es. busta_paga, certificato).",
        )
        parser.add_argument(
            "--limite",
            type=int,
            default=0,
            help="Max righe mostrate o in JSON (0 = illimitate; in JSON sconsigliato se molti).",
        )
        parser.add_argument(
            "--solo-mancanti",
            action="store_true",
            help="Evidenzia solo assenti in ogni path noto.",
        )
        parser.add_argument(
            "--solo-da-riallineare",
            action="store_true",
            help="Solo con file su disco sotto altro relpath (come riallineerebbe la UI).",
        )
        parser.add_argument(
            "--json",
            action="store_true",
            help="Riepilogo strutturato in JSON (include righe dettaglio).",
        )

    def handle(self, *args, **options):
        azienda_id = options.get("azienda_id")
        tipo = (options.get("tipo") or "").strip()
        limite = int(options.get("limite") or 0)
        solo_mancanti = bool(options.get("solo_mancanti"))
        solo_da_riallineare = bool(options.get("solo_da_riallineare"))
        as_json = bool(options.get("json"))

        if solo_mancanti and solo_da_riallineare:
            raise CommandError("Usa solo una tra --solo-mancanti e --solo-da-riallineare.")

        qs = Documento.objects.all().order_by("id")
        if azienda_id is not None:
            qs = qs.filter(azienda_id=azienda_id)
        if tipo:
            tipo_choices = {code for code, _ in Documento.TIPO_CHOICES}
            if tipo not in tipo_choices:
                raise CommandError(f"Tipo non valido: {tipo!r} (scegli tra {sorted(tipo_choices)})")
            qs = qs.filter(tipo=tipo)

        media_root = getattr(settings, "MEDIA_ROOT", None)
        if not as_json:
            self.stdout.write(f"MEDIA_ROOT: {media_root!s}\n")

        stats = {
            "documenti": 0,
            "ok_db": 0,
            "sul_disco_altrove": 0,
            "mancanti": 0,
        }
        rows_out: list[dict] = []
        n_shown = 0
        n_eleg = 0

        for doc in qs.iterator(chunk_size=500):
            stats["documenti"] += 1
            name = getattr(doc.file, "name", None) or ""
            try:
                storage = doc.file.storage
            except Exception:
                storage = None
            resolved: str | None = None
            if not name or not storage:
                stats["mancanti"] += 1
                stato = "mancante"
            else:
                found = first_existing_relpath_for_stored_name(storage, name)
                if not found:
                    stats["mancanti"] += 1
                    stato = "mancante"
                elif stored_relpath_equivalent(found, name):
                    stats["ok_db"] += 1
                    stato = "ok_db"
                else:
                    stats["sul_disco_altrove"] += 1
                    resolved = found
                    stato = "altrove"

            if stato == "ok_db":
                continue
            if solo_mancanti and stato != "mancante":
                continue
            if solo_da_riallineare and stato != "altrove":
                continue

            n_eleg += 1
            row = {
                "id": doc.id,
                "azienda_id": doc.azienda_id,
                "dipendente_id": doc.dipendente_id,
                "tipo": doc.tipo,
                "db_path": name,
                "resolved": resolved,
                "stato": stato,
            }
            if as_json:
                if not limite or len(rows_out) < limite:
                    rows_out.append(row)
            else:
                if not limite or n_shown < limite:
                    self.stdout.write(
                        f"id={row['id']} tipo={row['tipo']} azienda={row['azienda_id']} "
                        f"db={row['db_path']!r} -> risolto={row['resolved']!r} [{row['stato']}]"
                    )
                    n_shown += 1

        if as_json:
            out = {
                **stats,
                "rows": rows_out,
                "righe_eleggibili": n_eleg,
                "limite_righe": limite,
                "troncato": bool(limite and n_eleg > len(rows_out)),
            }
            self.stdout.write(json.dumps(out, ensure_ascii=False, indent=2))
        else:
            self.stdout.write(
                f"\nRiepilogo: totale={stats['documenti']} ok_path_DB={stats['ok_db']} "
                f"file_altrove_riallineabile={stats['sul_disco_altrove']} mancanti={stats['mancanti']}\n"
            )
            if limite and n_shown >= limite and n_eleg > n_shown:
                self.stdout.write(
                    f"(dettagli troncati a {limite} righe; {n_eleg} problemi, usa --limite 0 per l'elenco completo)\n"
                )
