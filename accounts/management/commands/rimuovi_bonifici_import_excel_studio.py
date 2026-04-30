"""Elimina bonifici creati da import Excel e vecchie tracce ImportEstrattoContoStudio."""

from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError
from django.db.models import Q

from anagrafiche.models import Azienda


class Command(BaseCommand):
    help = (
        "Rimuove per un'azienda: (1) movimenti bonifico da import Excel "
        "(metodo excel_riepilogo / excel_estratto_conto o nome_file xlsx-bon/ / xlsx-estratto/); "
        "(2) eventuali ImportEstrattoContoStudio (import «estratto» deprecato in UI). "
        "Con --solo-parcella-proforma-sintetici elimina solo i bonifici «finti» da riepilogo PROFORMA "
        "(riferimento tipo PARCELLA n|data|… o PF-/PAR-), lasciando i bonifici bancari reali. "
        "Senza --execute mostra solo il conteggio (anteprima)."
    )

    def add_arguments(self, parser):
        parser.add_argument("--azienda-id", type=int, required=True, help="ID anagrafiche.Azienda")
        parser.add_argument(
            "--execute",
            action="store_true",
            help="Esegui eliminazione. Senza questo flag: solo anteprima.",
        )
        parser.add_argument(
            "--solo-parcella-proforma-sintetici",
            action="store_true",
            help=(
                "Solo bonifici excel_riepilogo con riferimento sintetico da colonna Documento "
                "proforma/parcella (es. PARCELLA 182|2021-06-16|130.00). Non elimina bonifici tipo "
                "«BONIFICO BANCA|…» né tocca ImportEstrattoContoStudio."
            ),
        )

    def handle(self, *args, **options):
        from accounts.consulente_registro_studio import (
            bonifico_excel_con_riferimento_sintetico_parcella_o_proforma,
            ricalcola_saldi_progressivi,
        )
        from accounts.models import ImportEstrattoContoStudio, MovimentoRegistroStudioConsulente

        azienda_id = options["azienda_id"]
        execute = options["execute"]
        solo_sint = options["solo_parcella_proforma_sintetici"]

        try:
            Azienda.objects.get(pk=azienda_id)
        except Azienda.DoesNotExist as exc:
            raise CommandError(f"Azienda inesistente: id={azienda_id}") from exc

        filtro_excel = (
            Q(metodo_estrazione="excel_riepilogo")
            | Q(metodo_estrazione="excel_estratto_conto")
            | Q(nome_file__startswith="xlsx-bon/")
            | Q(nome_file__startswith="xlsx-estratto/")
        )
        qs_bon_base = MovimentoRegistroStudioConsulente.objects.filter(
            azienda_id=azienda_id,
            tipo_riga="bonifico",
        ).filter(filtro_excel)

        if solo_sint:
            pks = [m.pk for m in qs_bon_base.iterator() if bonifico_excel_con_riferimento_sintetico_parcella_o_proforma(m)]
            qs_bon = MovimentoRegistroStudioConsulente.objects.filter(pk__in=pks)
            qs_imp = ImportEstrattoContoStudio.objects.none()
        else:
            qs_bon = qs_bon_base
            qs_imp = ImportEstrattoContoStudio.objects.filter(azienda_id=azienda_id)

        n_bon = qs_bon.count()
        n_imp = qs_imp.count()

        self.stdout.write(f"Movimenti bonifico da eliminare: {n_bon}")
        if solo_sint:
            self.stdout.write("(modalità solo-parcella-proforma-sintetici: import estratto conto non toccato)")
        else:
            self.stdout.write(f"Import estratto conto (modello storico) da eliminare: {n_imp}")

        if not execute:
            self.stdout.write(self.style.WARNING("Anteprima sola. Aggiungi --execute per eliminare."))
            return

        for m in qs_bon.iterator():
            m.delete()

        if not solo_sint:
            for imp in qs_imp.iterator():
                imp.delete()

        ricalcola_saldi_progressivi(azienda_id)
        msg = f"Eliminati {n_bon} bonifici."
        if not solo_sint:
            msg += f" Eliminati {n_imp} import estratto."
        msg += " Saldi ricalcolati."
        self.stdout.write(self.style.SUCCESS(msg))
