"""
Campione automatico: confronto CedolinoMotoreV4 (busta acquisita / consulente) vs motore busta GESPER.

Usa ``documenti.cedolino_conciliazione_motore_paga.confronto_cedolino_motore_paga`` (stesse
tolleranze di ``documenti.cedolini_tolleranze``). Solo buste ORDINARIE (vincolo del motore in conciliazione).

Esempi::

    python manage.py concilia_busta_motore_campione --limite=20
    python manage.py concilia_busta_motore_campione --azienda-id=1 --anno=2025 --mese=6
    python manage.py concilia_busta_motore_campione --solo-ko --limite=100
    python manage.py concilia_busta_motore_campione --json --limite=30
"""

from __future__ import annotations

import json
from typing import Any

from django.core.management.base import BaseCommand

from documenti.cedolino_conciliazione_motore_paga import confronto_cedolino_motore_paga
from documenti.models import CedolinoMotoreV4


def _qs_campione(
    *,
    limite: int,
    azienda_id: int | None,
    anno: int | None,
    mese: int | None,
) -> Any:
    qs = (
        CedolinoMotoreV4.objects.filter(natura_busta="ORDINARIA")
        .select_related("dipendente")
        .order_by("-anno", "-mese", "-id")
    )
    if azienda_id is not None:
        qs = qs.filter(dipendente__azienda_id=azienda_id)
    if anno is not None:
        qs = qs.filter(anno=anno)
    if mese is not None:
        qs = qs.filter(mese=mese)
    return qs[: max(1, min(limite, 5000))]


def _analizza_v4(v4: CedolinoMotoreV4) -> dict[str, Any]:
    """Esegue il confronto e restituisce un dict serializzabile (Decimal → str)."""
    raw = confronto_cedolino_motore_paga(v4)
    if not raw.get("ok"):
        return {
            "cedolino_id": v4.pk,
            "dipendente_id": v4.dipendente_id,
            "mese": v4.mese,
            "anno": v4.anno,
            "ok": False,
            "errore": raw.get("errore"),
            "lordo_ok": None,
            "netto_ok": None,
            "n_voci_ko": None,
            "n_voci": None,
        }
    tot = raw.get("totali") or {}
    righe = raw.get("righe_voci") or []
    n_ko = sum(1 for r in righe if not r.get("ok"))
    return {
        "cedolino_id": v4.pk,
        "dipendente_id": v4.dipendente_id,
        "mese": v4.mese,
        "anno": v4.anno,
        "ok": True,
        "errore": None,
        "lordo_ok": bool(tot.get("lordo_ok")),
        "netto_ok": bool(tot.get("netto_ok")),
        "lordo_delta": str(tot.get("lordo_delta")),
        "netto_delta": str(tot.get("netto_delta")),
        "n_voci_ko": n_ko,
        "n_voci": len(righe),
        "n_non_mappate": len(raw.get("voci_non_mappate") or []),
        "modalita_griglia": (raw.get("meta") or {}).get("modalita_griglia_ruolo"),
    }


def _esito_finale(row: dict[str, Any]) -> str:
    if not row.get("ok"):
        return "ERRORE"
    if row.get("lordo_ok") and row.get("netto_ok") and row.get("n_voci_ko", 0) == 0:
        return "OK"
    if row.get("lordo_ok") and row.get("netto_ok"):
        return "VOCI"
    return "TOTALI"


class Command(BaseCommand):
    help = (
        "Esegue confronto motore busta vs CedolinoMotoreV4 su un campione (buste ORDINARIE). "
        "Solo report stdout; non modifica il database."
    )

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "--limite",
            type=int,
            default=50,
            help="Numero massimo di cedolini da analizzare (default 50, max 5000).",
        )
        parser.add_argument(
            "--azienda-id",
            type=int,
            default=None,
            help="Filtra per dipendente.azienda_id.",
        )
        parser.add_argument("--anno", type=int, default=None, help="Filtra per anno competenza.")
        parser.add_argument("--mese", type=int, default=None, help="Filtra per mese competenza (1-12).")
        parser.add_argument(
            "--solo-ko",
            action="store_true",
            help="Stampa solo righe con esito diverso da OK totale+voci.",
        )
        parser.add_argument(
            "--json",
            action="store_true",
            help="Solo output JSON (array di risultati) su stdout.",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        limite: int = options["limite"]
        azienda_id: int | None = options.get("azienda_id")
        anno: int | None = options.get("anno")
        mese: int | None = options.get("mese")
        solo_ko: bool = bool(options.get("solo_ko"))
        as_json: bool = bool(options.get("json"))

        rows_out: list[dict[str, Any]] = []
        conteggi = {"OK": 0, "VOCI": 0, "TOTALI": 0, "ERRORE": 0}

        for v4 in _qs_campione(
            limite=limite,
            azienda_id=azienda_id,
            anno=anno,
            mese=mese,
        ):
            row = _analizza_v4(v4)
            rows_out.append(row)
            conteggi[_esito_finale(row)] += 1

        if as_json:
            self.stdout.write(json.dumps(rows_out, ensure_ascii=False, indent=2))
            return

        self.stdout.write(
            self.style.NOTICE(
                f"Campione conciliazione busta vs motore: limite={limite}, "
                f"azienda_id={azienda_id}, anno={anno}, mese={mese} (solo ORDINARIA)\n"
            )
        )
        hdr = f"{'id':>6} {'dip':>6} {'mm/aaaa':>8} {'esito':>8} {'LΔ':>10} {'NΔ':>10} {'vociKO':>7} {'nm':>4} {'griglia':>18}"
        self.stdout.write(hdr)
        self.stdout.write("-" * len(hdr))
        for row in rows_out:
            esito = _esito_finale(row)
            if solo_ko and esito == "OK":
                continue
            if not row.get("ok"):
                self.stdout.write(
                    f"{row['cedolino_id']:>6} {row['dipendente_id']:>6} "
                    f"{row['mese']:02d}/{row['anno']:04d} {esito:>8} "
                    f"{'—':>10} {'—':>10} {'—':>7} {'—':>4} "
                    f"{str(row.get('errore', ''))[:40]}"
                )
                continue
            self.stdout.write(
                f"{row['cedolino_id']:>6} {row['dipendente_id']:>6} "
                f"{row['mese']:02d}/{row['anno']:04d} {esito:>8} "
                f"{row.get('lordo_delta', '0'):>10} {row.get('netto_delta', '0'):>10} "
                f"{row.get('n_voci_ko', 0):>7} {row.get('n_non_mappate', 0):>4} "
                f"{str(row.get('modalita_griglia') or '—'):>18}"
            )

        self.stdout.write("")
        self.stdout.write(
            self.style.SUCCESS(
                f"Riepilogo: OK={conteggi['OK']}  voci_da_verificare={conteggi['VOCI']}  "
                f"totali_fuori_tolleranza={conteggi['TOTALI']}  errore_contesto={conteggi['ERRORE']}"
            )
        )
