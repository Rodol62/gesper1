"""Import bonifici da Excel riepilogo (stessa logica della pagina Pagamenti)."""

from __future__ import annotations

from pathlib import Path

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

from anagrafiche.models import Azienda


class Command(BaseCommand):
    help = (
        "Importa righe bonifico da un file Excel (colonne Data, Documento, Descrizione, Importo). "
        "Stessa logica di Posizione contabile → Pagamenti → «Import bonifici da Excel». "
        "Righe solo proforma/parcella in colonna Documento (senza segnali SEPA) non vengono importate come bonifici. "
        "Pulizia storica bonifici «PARCELLA n|…» errati: rimuovi_bonifici_import_excel_studio --solo-parcella-proforma-sintetici."
    )

    def add_arguments(self, parser):
        parser.add_argument("path", type=str, help="Percorso file .xlsx (es. riepilogo generale)")
        parser.add_argument("--username", required=True, help="Utente da registrare come importatore")
        parser.add_argument("--azienda-id", type=int, required=True, help="ID azienda (anagrafiche.Azienda)")

    def handle(self, *args, **options):
        from accounts.consulente_registro_studio import import_riepilogo_bonifici_da_excel

        path = Path(options["path"]).expanduser().resolve()
        if not path.is_file():
            raise CommandError(f"File non trovato: {path}")
        if path.suffix.lower() not in (".xlsx", ".xlsm"):
            raise CommandError("Estensione non supportata: usare .xlsx o .xlsm")

        User = get_user_model()
        try:
            user = User.objects.get(username=options["username"])
        except User.DoesNotExist as exc:
            raise CommandError(f"Utente inesistente: {options['username']}") from exc

        try:
            azienda = Azienda.objects.get(pk=options["azienda_id"])
        except Azienda.DoesNotExist as exc:
            raise CommandError(f"Azienda inesistente: id={options['azienda_id']}") from exc

        with path.open("rb") as f:
            msgs = import_riepilogo_bonifici_da_excel(f, path.name, azienda, user)

        for m in msgs:
            self.stdout.write(m)
