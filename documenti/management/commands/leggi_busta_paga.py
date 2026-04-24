"""
Legge il cedolino TeamSystem da PDF (testo, JSON, HTML): dipendente, azienda (se nel testo), voci, totali.

  python manage.py leggi_busta_paga /path/busta.pdf
  python manage.py leggi_busta_paga --json /path/busta.pdf
  python manage.py leggi_busta_paga /path/busta.pdf --html /tmp/cedolino.html --open
"""

import json
import platform
import subprocess
from pathlib import Path

from django.core.management.base import BaseCommand
from django.template.loader import render_to_string

from documenti.buste_pdf_passwords import passwords_for_busta_pdf_read
from documenti.leggi_busta_paga_claude import render_report_testo, report_cedolino_senza_azienda


class Command(BaseCommand):
    help = "Legge busta paga PDF TeamSystem (pdfplumber): cedolino + dati aziendali se rilevabili."

    def add_arguments(self, parser):
        parser.add_argument(
            "pdf_path",
            nargs="?",
            default="/Users/rosario/Downloads/busta_03_2026_dip_19_p2.pdf",
            help="Percorso del PDF",
        )
        parser.add_argument(
            "--json",
            action="store_true",
            help="Stampa solo JSON strutturato",
        )
        parser.add_argument(
            "--html",
            metavar="FILE",
            help="Scrive una pagina HTML apribile nel browser (stesso layout della vista web)",
        )
        parser.add_argument(
            "--open",
            action="store_true",
            help="Dopo --html, apre il file (su macOS: comando open)",
        )

    def handle(self, *args, **options):
        path = Path(options["pdf_path"]).expanduser()
        if not path.is_file():
            self.stderr.write(self.style.ERROR(f"File non trovato: {path}"))
            return

        rep = None
        last_err = None
        for pw in passwords_for_busta_pdf_read():
            try:
                rep = report_cedolino_senza_azienda(str(path), password=pw)
                break
            except Exception as e:
                last_err = e
                rep = None
        if rep is None:
            self.stderr.write(
                self.style.ERROR(f"Impossibile leggere il PDF: {last_err}")
            )
            return
        if options["json"]:
            self.stdout.write(json.dumps(rep, ensure_ascii=False, indent=2))
            return

        html_out = options.get("html")
        if html_out:
            html_path = Path(html_out).expanduser().resolve()
            html_path.parent.mkdir(parents=True, exist_ok=True)
            ctx = {
                "report": rep,
                "extraction_error": None,
                "pdf_label": path.name,
                "pdf_path": str(path),
            }
            html_path.write_text(
                render_to_string("documenti/cedolino_busta_standalone.html", ctx),
                encoding="utf-8",
            )
            self.stdout.write(self.style.SUCCESS(f"HTML scritto: {html_path}"))
            if options.get("open"):
                system = platform.system()
                try:
                    if system == "Darwin":
                        subprocess.run(["open", str(html_path)], check=False)
                    elif system == "Windows":
                        subprocess.run(["cmd", "/c", "start", "", str(html_path)], check=False)
                    else:
                        subprocess.run(["xdg-open", str(html_path)], check=False)
                except Exception as e:
                    self.stderr.write(self.style.WARNING(f"Impossibile aprire il browser: {e}"))
            return

        self.stdout.write(render_report_testo(rep, str(path)))
