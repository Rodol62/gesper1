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
