from django.contrib.auth import get_user_model
from django.http import HttpResponseForbidden
from django.test import RequestFactory, TestCase

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
