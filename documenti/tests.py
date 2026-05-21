from django.contrib.auth import get_user_model
from django.http import HttpResponseForbidden
from django.test import Client, RequestFactory, TestCase

from anagrafiche.models import Azienda, Dipendente
from documenti.models import Documento
from documenti.views import _assert_documento_accesso

User = get_user_model()


class DocumentoTenantAccessTests(TestCase):
	def setUp(self):
		self.factory = RequestFactory()
		self.a1 = Azienda.objects.create(
			nome='Alpha Srl', partita_iva='11111111111', indirizzo='Via A 1', email='a@alpha.it',
		)
		self.a2 = Azienda.objects.create(
			nome='Beta Srl', partita_iva='22222222222', indirizzo='Via B 1', email='b@beta.it',
		)
		self.hr_a1 = User.objects.create_user(
			username='hr_a1', password='x', azienda=self.a1, convalidato=True, privacy_accettata=True,
		)
		from accounts.models import Ruolo
		hr_ruolo, _ = Ruolo.objects.get_or_create(codice='hr', defaults={'nome': 'HR'})
		self.hr_a1.ruoli.add(hr_ruolo)
		self.dip = Dipendente.objects.create(
			azienda=self.a1, nome='D', cognome='Test', codice_fiscale='TSTDTT80A01H501U',
			data_nascita='1980-01-01', indirizzo='x', email='d@test.it', telefono='1',
			data_assunzione='2020-01-01',
		)
		import io
		from django.core.files.uploadedfile import SimpleUploadedFile
		fake = SimpleUploadedFile('x.txt', b'x', content_type='text/plain')
		self.doc_a2 = Documento.objects.create(
			azienda=self.a2,
			dipendente=None,
			tipo='contratto',
			descrizione='t',
			file=fake,
			caricato_da=self.hr_a1,
		)

	def test_hr_non_accede_documento_altra_azienda(self):
		request = self.factory.get('/documenti/')
		request.user = self.hr_a1
		request.session = {}
		resp = _assert_documento_accesso(request, self.doc_a2)
		self.assertIsInstance(resp, HttpResponseForbidden)

	def test_lista_documenti_tipo_contratto_include_legacy_classificati_in_cartella(self):
		from django.core.files.uploadedfile import SimpleUploadedFile

		# Documento "pulito" tipo contratto
		d1 = Documento.objects.create(
			azienda=self.a1,
			dipendente=self.dip,
			tipo='contratto',
			descrizione='Contratto standard',
			file=SimpleUploadedFile('contratto_std.pdf', b'pdf', content_type='application/pdf'),
			caricato_da=self.hr_a1,
		)
		# Documento legacy classificato in cartella contratti ma con tipo non allineato
		d2 = Documento.objects.create(
			azienda=self.a1,
			dipendente=self.dip,
			tipo='altro',
			descrizione='Contratto legacy classificato',
			file=SimpleUploadedFile('legacy.pdf', b'pdf', content_type='application/pdf'),
			caricato_da=self.hr_a1,
		)
		Documento.objects.filter(pk=d2.pk).update(file='contratti/legacy_contratto.pdf')

		c = Client()
		c.force_login(self.hr_a1)
		r = c.get('/documenti/?tipo=contratto&anno=&dipendente=')
		self.assertEqual(r.status_code, 200)
		ids = {x.id for x in r.context['documenti']}
		self.assertIn(d1.id, ids)
		self.assertIn(d2.id, ids)

	def test_lista_documenti_tipo_contratto_filtro_anno_da_descrizione(self):
		from datetime import datetime
		from django.core.files.uploadedfile import SimpleUploadedFile
		from django.utils import timezone

		d = Documento.objects.create(
			azienda=self.a1,
			dipendente=self.dip,
			tipo='contratto',
			descrizione='Contratto definitivo C-77/2026',
			file=SimpleUploadedFile('contratto_legacy.pdf', b'pdf', content_type='application/pdf'),
			caricato_da=self.hr_a1,
		)
		# Simula upload avvenuto in anno diverso dal periodo contratto
		Documento.objects.filter(pk=d.pk).update(
			data_caricamento=timezone.make_aware(datetime(2025, 12, 31, 12, 0, 0)),
		)

		c = Client()
		c.force_login(self.hr_a1)
		r = c.get('/documenti/?tipo=contratto&anno=2026&dipendente=')
		self.assertEqual(r.status_code, 200)
		ids = {x.id for x in r.context['documenti']}
		self.assertIn(d.id, ids)

	def test_lista_documenti_tipo_contratto_filtro_anno_non_esclude_legacy_senza_anno(self):
		from datetime import datetime
		from django.core.files.uploadedfile import SimpleUploadedFile
		from django.utils import timezone

		d = Documento.objects.create(
			azienda=self.a1,
			dipendente=self.dip,
			tipo='contratto',
			descrizione='Contratto definitivo N. 55',
			file=SimpleUploadedFile('contratto_definitivo.pdf', b'pdf', content_type='application/pdf'),
			caricato_da=self.hr_a1,
		)
		Documento.objects.filter(pk=d.pk).update(
			data_caricamento=timezone.make_aware(datetime(2025, 6, 15, 10, 0, 0)),
		)

		c = Client()
		c.force_login(self.hr_a1)
		r = c.get('/documenti/?tipo=contratto&anno=2026&dipendente=')
		self.assertEqual(r.status_code, 200)
		ids = {x.id for x in r.context['documenti']}
		self.assertIn(d.id, ids)
		self.assertEqual(r.context['contratti_senza_anno_esplicito_inclusi'], 1)

	def test_lista_documenti_select_dipendente_restera_popolata_anche_se_filtri_vuoti(self):
		c = Client()
		c.force_login(self.hr_a1)
		r = c.get('/documenti/?tipo=contratto&anno=2026&dipendente=')
		self.assertEqual(r.status_code, 200)
		dip_ids = {d.id for d in r.context['dipendenti_filtri']}
		self.assertIn(self.dip.id, dip_ids)

	def test_upload_forza_cartella_coerente_col_tipo(self):
		from django.core.files.uploadedfile import SimpleUploadedFile

		doc = Documento.objects.create(
			azienda=self.a1,
			dipendente=self.dip,
			tipo='contratto',
			descrizione='Upload con path sporco',
			file=SimpleUploadedFile('f24/../../legacy.pdf', b'pdf', content_type='application/pdf'),
			caricato_da=self.hr_a1,
		)
		self.assertTrue(doc.file.name.startswith('contratti/'))


class CedolinoRofProbeTests(TestCase):
	def test_ha_rof_da_retr_oraria(self):
		from decimal import Decimal
		from types import SimpleNamespace

		from documenti.cedolino_conciliazione_motore_paga import cedolino_motore_v4_ha_rof_per_conciliazione

		v4 = SimpleNamespace(retr_oraria_att=Decimal("12.5"), retrib_di_fatto=None)
		self.assertTrue(cedolino_motore_v4_ha_rof_per_conciliazione(v4))

	def test_no_rof(self):
		from types import SimpleNamespace

		from documenti.cedolino_conciliazione_motore_paga import cedolino_motore_v4_ha_rof_per_conciliazione

		v4 = SimpleNamespace(retr_oraria_att=None, retrib_di_fatto=None)
		self.assertFalse(cedolino_motore_v4_ha_rof_per_conciliazione(v4))


class ConciliazioneChecksTests(TestCase):
	def test_solo_f5_conta_come_formula_bloccante_tipica(self):
		from types import SimpleNamespace

		from documenti.cedolino_conciliazione_checks import formula_ko_bloccanti_da_checks

		ch_f8 = SimpleNamespace(ok=False, campo="F8 · Tot. Trattenute")
		ch_f3 = SimpleNamespace(ok=False, campo="F3 · Totale Lordo")
		ch_f4 = SimpleNamespace(ok=False, campo="F4 · Imponibile Contributivo (voci vs PDF)")
		ch_f9 = SimpleNamespace(ok=False, campo="F9 · Netto Busta")
		ch_f5 = SimpleNamespace(ok=False, campo="F5 · Contributi Sociali INPS")
		self.assertEqual(formula_ko_bloccanti_da_checks([ch_f8]), 0)
		self.assertEqual(formula_ko_bloccanti_da_checks([ch_f8, ch_f3]), 0)
		self.assertEqual(formula_ko_bloccanti_da_checks([ch_f3, ch_f4, ch_f9]), 0)
		self.assertEqual(formula_ko_bloccanti_da_checks([ch_f5]), 1)
		self.assertEqual(formula_ko_bloccanti_da_checks([ch_f3, ch_f5]), 1)


class ConciliazioneMappingTsTests(TestCase):
	"""Alias codici TeamSystem → chiavi motore per confronto voci."""

	def test_codice_ts8020_8030_e_addiz_abbrev(self):
		from types import SimpleNamespace

		from documenti.cedolino_conciliazione_motore_paga import _codice_motore_per_voce_cedolino

		self.assertEqual(
			_codice_motore_per_voce_cedolino(SimpleNamespace(codice="8020", descrizione="Festivo"), []),
			"MAGG_DOM_FEST",
		)
		self.assertEqual(
			_codice_motore_per_voce_cedolino(SimpleNamespace(codice="8030", descrizione="Notturno"), []),
			"STRAORD_NOTTURNO",
		)
		self.assertEqual(
			_codice_motore_per_voce_cedolino(SimpleNamespace(codice="800", descrizione="Add reg"), []),
			"CED_ADDIZ_REGIONALE",
		)
		self.assertEqual(
			_codice_motore_per_voce_cedolino(SimpleNamespace(codice="1812", descrizione="Acconto add com"), []),
			"CED_ADDIZ_COMUNALE",
		)

	def test_somma_8001_include_nc_e_somma_duplicate(self):
		from decimal import Decimal
		from types import SimpleNamespace

		from documenti.cedolino_conciliazione_motore_paga import _somma_importi_voci_codici

		voci = [
			SimpleNamespace(codice="8001", tipo="COMPETENZA", importo=Decimal("100.00")),
			SimpleNamespace(codice="8001", tipo="N/C", importo=Decimal("50.00")),
			SimpleNamespace(codice="8001", tipo="TRATTENUTA", importo=Decimal("999.00")),
			SimpleNamespace(codice="8002", tipo="COMPETENZA", importo=Decimal("1.00")),
		]
		self.assertEqual(_somma_importi_voci_codici(voci, frozenset({"8001"})), Decimal("150.00"))

	def test_allinea_8020_ricava_ore_festivi(self):
		from decimal import Decimal
		from types import SimpleNamespace

		from documenti.cedolino_conciliazione_motore_paga import allinea_kwargs_calcolo_a_dati_cedolino_v4

		v4 = SimpleNamespace(
			pk=None,
			retr_oraria_att=Decimal("10.00"),
			retrib_di_fatto=None,
			imp_irpef_mese=None,
		)
		voci = [SimpleNamespace(codice="8020", tipo="COMPETENZA", importo=Decimal("120.00"), descrizione="")]
		kw_in = {"ore_festivi": Decimal("0"), "modalita_ore_effettive": False}
		kw_out, meta = allinea_kwargs_calcolo_a_dati_cedolino_v4(v4, kw_in, voci_prefetched=voci)
		self.assertTrue(meta.get("attivo"))
		self.assertEqual(kw_out.get("festivo_compenso_completo"), True)
		self.assertEqual(kw_out.get("ore_festivi"), Decimal("10.00"))


class ConciliazioneCalendarioRuoloTests(TestCase):
	"""Soglia 01/03/2026 per uso calendario ruolo organico in conciliazione motore paga."""

	def test_marzo_2026_usa_calendario(self):
		from documenti.cedolino_conciliazione_motore_paga import usa_calendario_ruolo_organico_in_conciliazione

		self.assertTrue(usa_calendario_ruolo_organico_in_conciliazione(2026, 3))

	def test_febbraio_2026_non_usa(self):
		from documenti.cedolino_conciliazione_motore_paga import usa_calendario_ruolo_organico_in_conciliazione

		self.assertFalse(usa_calendario_ruolo_organico_in_conciliazione(2026, 2))

	def test_dicembre_2025_non_usa(self):
		from documenti.cedolino_conciliazione_motore_paga import usa_calendario_ruolo_organico_in_conciliazione

		self.assertFalse(usa_calendario_ruolo_organico_in_conciliazione(2025, 12))


class InferNaturaBustaTests(TestCase):
	"""Doppia busta stesso mese (ordinaria + tredicesima): chiave ``natura_busta`` distinta."""

	def test_infer_tredicesima_da_voce_report(self):
		from documenti.natura_busta_utils import infer_natura_busta_per_busta

		rep = {
			"tipo_cedolino": "Ordinario",
			"voci_retributive": [
				{"descrizione": "LIQUIDAZIONE TREDICESIMA ANNO 2025", "codice": "9999", "tipo": "Competenza"},
			],
		}
		self.assertEqual(
			infer_natura_busta_per_busta(documento=None, report=rep, tipo_cedolino_motore="ORDINARIO"),
			"TREDICESIMA",
		)

	def test_infer_ordinaria_se_solo_acconto_in_voce(self):
		from documenti.natura_busta_utils import infer_natura_busta_per_busta

		rep = {
			"tipo_cedolino": "Ordinario",
			"voci_retributive": [
				{"descrizione": "ACCONTO 13ª SU ORDINARIO", "codice": "8001", "tipo": "Competenza"},
			],
		}
		self.assertEqual(
			infer_natura_busta_per_busta(documento=None, report=rep, tipo_cedolino_motore="ORDINARIO"),
			"ORDINARIA",
		)

	def test_infer_quattordicesima_da_voce_report(self):
		from documenti.natura_busta_utils import infer_natura_busta_per_busta

		rep = {
			"tipo_cedolino": "Ordinario",
			"voci_retributive": [
				{"descrizione": "LIQUIDAZIONE QUATTORDICESIMA 2024-2025", "codice": "9998", "tipo": "Competenza"},
			],
		}
		self.assertEqual(
			infer_natura_busta_per_busta(documento=None, report=rep, tipo_cedolino_motore="ORDINARIO"),
			"QUATTORDICESIMA",
		)

	def test_infer_quattordicesima_prima_di_tredicesima_se_entrambe_in_blob(self):
		from documenti.natura_busta_utils import infer_natura_busta_per_busta

		rep = {
			"tipo_cedolino": "Ordinario",
			"dati_dipendente": {"Nota": "Riferimento TREDICESIMA maturata"},
			"voci_retributive": [
				{"descrizione": "SALDO QUATTORDICESIMA", "codice": "X", "tipo": "Competenza"},
			],
		}
		self.assertEqual(
			infer_natura_busta_per_busta(documento=None, report=rep, tipo_cedolino_motore="ORDINARIO"),
			"QUATTORDICESIMA",
		)


class EstrazionePeriodoCedolinoTests(TestCase):
	"""Periodo retributivo da testo cedolino (MESE RETRIBUITO TeamSystem)."""

	def test_mese_retribuito_aprile_riga_successiva(self):
		from documenti.busta_periodo_da_pdf import estrai_mese_anno_da_testo_cedolino

		testo = """
		Autorizzazione numerazione automatica n.N. 37938 del 27/01/2009
		MESE RETRIBUITO                      COD. AZI
		APRILE                   2026              136
		CARDELLA MASSIMO
		"""
		mese, anno = estrai_mese_anno_da_testo_cedolino(testo)
		self.assertEqual((mese, anno), (4, 2026))

	def test_non_confonde_data_autorizzazione_con_periodo(self):
		from documenti.busta_periodo_da_pdf import estrai_mese_anno_da_testo_cedolino

		testo = "Autorizzazione del 27/01/2009\nMESE RETRIBUITO\nMARZO 2025\n"
		mese, anno = estrai_mese_anno_da_testo_cedolino(testo)
		self.assertEqual((mese, anno), (3, 2025))


class EstrazioneImportiBustaPdfplumberTests(TestCase):
	"""Lordo/netto sotto etichetta (mock words layout TeamSystem)."""

	def test_find_below_label_lordo_netto(self):
		from documenti.busta_importi_pdfplumber import _find_below_label
		from decimal import Decimal

		words = [
			{"text": "TOTALE", "x0": 40, "x1": 80, "top": 500, "bottom": 510},
			{"text": "LORDO", "x0": 82, "x1": 110, "top": 500, "bottom": 510},
			{"text": "1.745,16", "x0": 42, "x1": 70, "top": 518, "bottom": 526},
			{"text": "NETTO", "x0": 360, "x1": 390, "top": 620, "bottom": 628},
			{"text": "BUSTA", "x0": 392, "x1": 420, "top": 620, "bottom": 628},
			{"text": "0,50", "x0": 330, "x1": 350, "top": 638, "bottom": 646},
			{"text": "1.339,00", "x0": 372, "x1": 400, "top": 638, "bottom": 646},
		]
		lordo = _find_below_label(words, "TOTALE LORDO", (30, 95), gap_max=18, min_val=Decimal("100"))
		netto = _find_below_label(words, "NETTO BUSTA", (348, 425), gap_max=25, min_val=Decimal("100"))
		self.assertEqual(lordo, Decimal("1745.16"))
		self.assertEqual(netto, Decimal("1339.00"))
