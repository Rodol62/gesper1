"""
Importa in batch PDF proforma/parcelle nel partitario consulente ↔ azienda (righe in dare, con PDF).

Esempio (Mac, path assoluto alla cartella «QUADRATURA CIPRIANO»):

  python manage.py import_proforma_parcelle_studio \\
    --path "/Users/rosario/Documents/.../QUADRATURA CIPRIANO " \\
    --username marco.consulente

Esclude i file il cui nome inizia con «riepilogo» (case-insensitive).
Dopo l'import ricalcola i saldi progressivi per l'azienda del consulente.

  --dry-run   solo anteprima su stdout, nessun salvataggio
"""
from decimal import Decimal
from pathlib import Path

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.core.files import File

from accounts.consulente_registro_studio import (
    elenco_pdf_cartella,
    estrai_testo_da_pdf,
    parse_testo_proforma_parcella,
    ricalcola_saldi_progressivi,
)
from accounts.models import MovimentoRegistroStudioConsulente


class Command(BaseCommand):
    help = 'Importa PDF proforma/parcelle da cartella (registro studio consulente).'

    def add_arguments(self, parser):
        parser.add_argument('--path', type=str, required=True, help='Cartella radice (ricorsiva, solo PDF)')
        parser.add_argument('--username', type=str, required=True, help='Utente consulente (deve avere ruolo e azienda)')
        parser.add_argument('--dry-run', action='store_true', help='Non scrive sul database')

    def handle(self, *args, **options):
        root = Path(options['path']).expanduser().resolve()
        if not root.is_dir():
            raise CommandError(f'Cartella non valida: {root}')

        User = get_user_model()
        try:
            user = User.objects.get(username=options['username'])
        except User.DoesNotExist as exc:
            raise CommandError(f"Utente inesistente: {options['username']}") from exc

        if not getattr(user, 'has_ruolo', lambda _: False)('consulente'):
            raise CommandError("L'utente non ha ruolo consulente.")
        azienda = getattr(user, 'azienda', None)
        if not azienda:
            raise CommandError('Al consulente non è associata un azienda.')

        dry = options['dry_run']
        files = elenco_pdf_cartella(root)
        self.stdout.write(f'Trovati {len(files)} PDF (esclusi nomi che iniziano con «riepilogo»).')

        n_ok = n_skip = n_err = 0
        for pdf in files:
            try:
                rel = str(pdf.relative_to(root))
            except ValueError:
                rel = pdf.name
            nome_store = rel[:280] if len(rel) > 280 else rel

            if MovimentoRegistroStudioConsulente.objects.filter(
                azienda=azienda, nome_file=nome_store, tipo_riga='documento'
            ).exists():
                self.stdout.write(self.style.WARNING(f'  Salta (già presente): {nome_store}'))
                n_skip += 1
                continue

            try:
                testo, metodo = estrai_testo_da_pdf(pdf)
                parsed = parse_testo_proforma_parcella(testo, pdf.name)
                tot = parsed.totale_da_pagare or Decimal('0')
                dare = tot if tot > 0 else Decimal('0')
                note = '; '.join(parsed.avvisi) if parsed.avvisi else ''
                if dry:
                    self.stdout.write(
                        f'  [dry-run] {nome_store} | {parsed.tipo_documento} | '
                        f'n={parsed.numero_documento!r} | data={parsed.data_documento} | '
                        f'totale={parsed.totale_da_pagare} | {metodo}'
                    )
                    n_ok += 1
                    continue

                obj = MovimentoRegistroStudioConsulente(
                    azienda=azienda,
                    tipo_riga='documento',
                    tipo_documento=parsed.tipo_documento,
                    numero_documento=parsed.numero_documento[:80],
                    data_documento=parsed.data_documento,
                    totale_da_pagare=parsed.totale_da_pagare,
                    dare=dare,
                    avere=Decimal('0'),
                    nome_file=nome_store,
                    testo_estratto=testo[:50000],
                    metodo_estrazione=metodo,
                    note=note[:500],
                    importato_da=user,
                )
                obj.save()
                with pdf.open('rb') as fh:
                    obj.file.save(pdf.name, File(fh), save=True)
                self.stdout.write(self.style.SUCCESS(f'  Importato: {nome_store}'))
                n_ok += 1
            except Exception as exc:
                self.stdout.write(self.style.ERROR(f'  Errore {nome_store}: {exc}'))
                n_err += 1

        if not dry:
            ricalcola_saldi_progressivi(azienda.id)
            self.stdout.write(self.style.SUCCESS('Saldi progressivi ricalcolati.'))

        self.stdout.write(f'Fine: importati={n_ok}, saltati={n_skip}, errori={n_err}, dry_run={dry}')
