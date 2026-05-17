from pathlib import Path
from urllib.request import urlopen
from zipfile import ZipFile
from io import BytesIO

from django.core.management.base import BaseCommand

from anagrafiche.territorio_it import URL_ISTAT_COMUNI, URL_ISTAT_STATI


class Command(BaseCommand):
    help = "Aggiorna dataset ISTAT (comuni italiani + stati esteri) per anagrafica."

    def handle(self, *args, **options):
        base = Path('anagrafiche/data')
        base.mkdir(parents=True, exist_ok=True)

        comuni_out = base / 'istat_comuni_italiani.csv'
        self.stdout.write(f"Download comuni ISTAT: {URL_ISTAT_COMUNI}")
        with urlopen(URL_ISTAT_COMUNI, timeout=30) as resp:
            comuni_out.write_bytes(resp.read())
        self.stdout.write(self.style.SUCCESS(f"OK comuni: {comuni_out}"))

        self.stdout.write(f"Download stati esteri ISTAT: {URL_ISTAT_STATI}")
        with urlopen(URL_ISTAT_STATI, timeout=30) as resp:
            payload = resp.read()
        zip_bytes = BytesIO(payload)
        dest = base / 'istat_stati_esteri'
        dest.mkdir(parents=True, exist_ok=True)
        with ZipFile(zip_bytes) as zf:
            zf.extractall(dest)
        self.stdout.write(self.style.SUCCESS(f"OK stati esteri: {dest}"))

        self.stdout.write(self.style.SUCCESS("Aggiornamento dataset ISTAT completato."))
