"""
Importa in batch PDF proforma/parcelle nel partitario consulente ↔ azienda (righe in dare, con PDF).

In portale consulente, per righe **già** create (es. pregresso da Excel senza allegato) usare
**Proforma / parcelle → «Aggancia PDF a movimenti esistenti»** (stesso criterio data+numero dal PDF).

Esempio CLI (Mac, path assoluto alla cartella «QUADRATURA CIPRIANO»):

  python manage.py import_proforma_parcelle_studio \\
    --path "/Users/rosario/Documents/.../QUADRATURA CIPRIANO " \\
    --username marco.consulente

Report CSV (UTF-8 con BOM, separatore `;`):

  python manage.py import_proforma_parcelle_studio --path ... --username ... \\
    --report-csv /tmp/report_import_proforma.csv

Esclude i file il cui nome inizia con «riepilogo» (case-insensitive).
Deduplica come il caricamento web: **data documento + numero** (stesso numero in anni diversi = file distinti).
Dopo l'import ricalcola i saldi progressivi per l'azienda del consulente.

  --dry-run   solo anteprima su stdout, nessun salvataggio
"""
from decimal import Decimal
from pathlib import Path

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.core.files import File

from accounts.consulente_registro_studio import (
    _movimento_documento_duplicato_upload,
    elenco_pdf_cartella,
    estrai_testo_da_pdf,
    parse_testo_proforma_parcella,
    render_csv_report_import_proforma_cartella,
    ricalcola_saldi_progressivi,
)
from accounts.models import MovimentoRegistroStudioConsulente


class Command(BaseCommand):
    help = 'Importa PDF proforma/parcelle da cartella (registro studio consulente).'

    def add_arguments(self, parser):
        parser.add_argument('--path', type=str, required=True, help='Cartella radice (ricorsiva, solo PDF)')
        parser.add_argument('--username', type=str, required=True, help='Utente consulente (deve avere ruolo e azienda)')
        parser.add_argument('--dry-run', action='store_true', help='Non scrive sul database')
        parser.add_argument(
            '--report-csv',
            type=str,
            default='',
            help='Percorso file .csv di riepilogo (per PDF: esito, numero/data estratti, messaggio)',
        )

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
        report_csv = (options.get('report_csv') or '').strip()
        files = elenco_pdf_cartella(root)
        self.stdout.write(f'Trovati {len(files)} PDF (esclusi nomi che iniziano con «riepilogo»).')

        report_rows: list[dict[str, str]] = []
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
                msg = 'Già presente (stesso nome file relativo già in libro).'
                self.stdout.write(self.style.WARNING(f'  Salta: {nome_store} — {msg}'))
                report_rows.append(
                    {
                        'file': nome_store,
                        'esito': 'saltato',
                        'movimento_id': '',
                        'numero_pdf': '',
                        'data_pdf': '',
                        'messaggio': msg,
                    }
                )
                n_skip += 1
                continue

            try:
                testo, metodo = estrai_testo_da_pdf(pdf)
                parsed = parse_testo_proforma_parcella(testo, pdf.name)
                num_pdf = (parsed.numero_documento or '').strip()
                data_pdf = parsed.data_documento.strftime('%Y-%m-%d') if parsed.data_documento else ''

                dup = _movimento_documento_duplicato_upload(azienda, parsed)
                if dup is not None:
                    dd = dup.data_documento.isoformat() if dup.data_documento else '—'
                    msg = (
                        f'Già presente in libro {dup.get_tipo_documento_display()} n. «{num_pdf}» '
                        f'del {dd} (file: {dup.nome_file}); ignorato.'
                    )
                    self.stdout.write(self.style.WARNING(f'  {nome_store}: {msg}'))
                    report_rows.append(
                        {
                            'file': nome_store,
                            'esito': 'saltato',
                            'movimento_id': str(dup.pk),
                            'numero_pdf': num_pdf,
                            'data_pdf': data_pdf,
                            'messaggio': msg,
                        }
                    )
                    n_skip += 1
                    continue

                tot = parsed.totale_da_pagare or Decimal('0')
                dare = tot if tot > 0 else Decimal('0')
                note = '; '.join(parsed.avvisi) if parsed.avvisi else ''
                if dry:
                    preview = (
                        f'{parsed.tipo_documento} | n={parsed.numero_documento!r} | data={parsed.data_documento} | '
                        f'totale={parsed.totale_da_pagare} | {metodo}'
                    )
                    self.stdout.write(f'  [dry-run] {nome_store} | {preview}')
                    report_rows.append(
                        {
                            'file': nome_store,
                            'esito': 'dry_run',
                            'movimento_id': '',
                            'numero_pdf': num_pdf,
                            'data_pdf': data_pdf,
                            'messaggio': preview,
                        }
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
                report_rows.append(
                    {
                        'file': nome_store,
                        'esito': 'ok',
                        'movimento_id': str(obj.pk),
                        'numero_pdf': (obj.numero_documento or num_pdf or '').strip(),
                        'data_pdf': obj.data_documento.strftime('%Y-%m-%d') if obj.data_documento else data_pdf,
                        'messaggio': f'Importato id={obj.pk} ({metodo}).',
                    }
                )
                n_ok += 1
            except Exception as exc:
                self.stdout.write(self.style.ERROR(f'  Errore {nome_store}: {exc}'))
                report_rows.append(
                    {
                        'file': nome_store,
                        'esito': 'errore',
                        'movimento_id': '',
                        'numero_pdf': '',
                        'data_pdf': '',
                        'messaggio': str(exc),
                    }
                )
                n_err += 1

        if not dry:
            ricalcola_saldi_progressivi(azienda.id)
            self.stdout.write(self.style.SUCCESS('Saldi progressivi ricalcolati.'))

        self.stdout.write(f'Fine: importati={n_ok}, saltati={n_skip}, errori={n_err}, dry_run={dry}')

        if report_csv:
            out = Path(report_csv).expanduser().resolve()
            try:
                out.parent.mkdir(parents=True, exist_ok=True)
            except OSError as exc:
                raise CommandError(f'Impossibile creare la cartella per il report: {exc}') from exc
            body = '\ufeff' + render_csv_report_import_proforma_cartella(report_rows)
            out.write_text(body, encoding='utf-8')
            self.stdout.write(self.style.SUCCESS(f'Report CSV scritto: {out} ({len(report_rows)} righe).'))
