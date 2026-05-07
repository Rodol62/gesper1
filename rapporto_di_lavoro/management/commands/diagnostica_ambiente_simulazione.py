"""
Stampa path database, GESPER_DATA_ROOT e conteggi parametri usati dal motore busta.
Utile per capire perché simulazione locale e produzione differiscono (di solito DB diversi).
"""

import os
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import connection

from rapporto_di_lavoro.models import (
	CCNL,
	ParametroCCNLTurismo,
	ParametroContributi,
	ParametroRatei,
)
from rapporto_di_lavoro.utils_calendario import get_calendario_motore_id


class Command(BaseCommand):
	help = (
		"Diagnosi ambiente simulazione: DB effettivo, GESPER_DATA_ROOT, conteggi parametri CCNL/ratei/contributi."
	)

	def add_arguments(self, parser):
		parser.add_argument(
			"--anno",
			type=int,
			default=None,
			help="Anno per filtrare ParametroContributi (default: anno corrente del server).",
		)

	def handle(self, *args, **options):
		from datetime import date

		anno = options["anno"]
		if anno is None:
			anno = date.today().year

		self.stdout.write(self.style.SUCCESS("=== Diagnostica ambiente simulazione ===\n"))

		djm = os.environ.get("DJANGO_SETTINGS_MODULE", "")
		self.stdout.write(f"DJANGO_SETTINGS_MODULE={djm}")

		data_root = getattr(settings, "GESPER_DATA_ROOT", None)
		if data_root is not None:
			self.stdout.write(f"GESPER_DATA_ROOT={Path(data_root).resolve()}")
		else:
			self.stdout.write("GESPER_DATA_ROOT=(non impostato)")

		db = settings.DATABASES.get("default", {})
		name = db.get("NAME", "")
		if name and not str(name).startswith(":"):
			try:
				name_disp = str(Path(name).resolve())
			except (OSError, ValueError):
				name_disp = str(name)
		else:
			name_disp = str(name)
		self.stdout.write(f"DATABASE ENGINE={db.get('ENGINE')}")
		self.stdout.write(f"DATABASE NAME (effettivo)={name_disp}")

		# Due file possibili in sviluppo: stessa radice dati della VPS vs legacy nella cartella progetto.
		try:
			_cand_data = Path(settings.GESPER_DATA_ROOT) / "db.sqlite3"
			_cand_base = Path(settings.BASE_DIR) / "db.sqlite3"
			self.stdout.write(
				"SQLite candidati: "
				f"GESPER_DATA_ROOT/db={_cand_data} exists={_cand_data.is_file()} | "
				f"BASE_DIR/db={_cand_base} exists={_cand_base.is_file()}"
			)
		except Exception:
			pass

		try:
			with connection.cursor() as cur:
				cur.execute("select sqlite_version()")
				row = cur.fetchone()
				if row:
					self.stdout.write(f"sqlite_version={row[0]}")
		except Exception:
			pass

		self.stdout.write(f"get_calendario_motore_id()={get_calendario_motore_id()!r}")

		n_turismo = ParametroCCNLTurismo.objects.count()
		self.stdout.write(f"ParametroCCNLTurismo (righe totali)={n_turismo}")

		ccnl_fipe = CCNL.objects.filter(sigla="FIPE").first()
		if ccnl_fipe:
			n_ratei = ParametroRatei.objects.filter(ccnl=ccnl_fipe).count()
			n_contrib = ParametroContributi.objects.filter(ccnl=ccnl_fipe, anno=anno).count()
			self.stdout.write(
				f"CCNL FIPE id={ccnl_fipe.pk} | ParametroRatei (tutti gli anni)={n_ratei} | "
				f"ParametroContributi anno {anno}={n_contrib}"
			)
		else:
			self.stdout.write(self.style.WARNING("CCNL FIPE assente: controlla seed/migrazioni."))

		self.stdout.write(
			self.style.SUCCESS(
				"\nSe locale e produzione differiscono, confronta questa uscita tra i due ambienti "
				"(o copia db.sqlite3 da produzione e rilancia qui)."
			)
		)
