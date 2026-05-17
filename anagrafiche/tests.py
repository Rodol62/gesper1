from django.contrib.auth import get_user_model
from django.test import TestCase

from .models import Azienda, Dipendente

User = get_user_model()

class UserAuthTest(TestCase):
    def setUp(self):
        self.azienda = Azienda.objects.create(nome='Test Srl', partita_iva='12345678901', indirizzo='Via Roma 1', email='info@test.it')
        self.admin = User.objects.create_user(
            username='admin', password='admin123', azienda=self.azienda,
            convalidato=True, privacy_accettata=True,
        )
        self.hr = User.objects.create_user(
            username='hr', password='hr123', azienda=self.azienda,
            convalidato=True, privacy_accettata=True,
        )
        self.dip = User.objects.create_user(
            username='dip', password='dip123', azienda=self.azienda,
            convalidato=True, privacy_accettata=True,
        )
        # Assegna ruoli dopo la creazione: self.admin.ruoli.add(Ruolo.objects.get(codice='admin')) ecc.

    def test_admin_login(self):
        login = self.client.login(username='admin', password='admin123')
        self.assertTrue(login)

    def test_hr_login(self):
        login = self.client.login(username='hr', password='hr123')
        self.assertTrue(login)

    def test_dipendente_login(self):
        login = self.client.login(username='dip', password='dip123')
        self.assertTrue(login)

class DipendenteTest(TestCase):
    def setUp(self):
        self.azienda = Azienda.objects.create(nome='Test Srl', partita_iva='12345678901', indirizzo='Via Roma 1', email='info@test.it')
        self.user = User.objects.create_user(
            username='dip', password='dip123', azienda=self.azienda,
            convalidato=True, privacy_accettata=True,
        )
        # Assegna ruolo dopo la creazione: self.user.ruoli.add(Ruolo.objects.get(codice='dipendente'))
        self.dipendente = Dipendente.objects.create(
            azienda=self.azienda,
            utente=self.user,
            nome='Mario',
            cognome='Rossi',
            codice_fiscale='RSSMRA80A01H501U',
            data_nascita='1980-01-01',
            indirizzo='Via Roma 2',
            email='mario.rossi@test.it',
            telefono='3331234567',
            data_assunzione='2020-01-01',
            ruolo='Impiegato',
        )

    def test_dipendente_str(self):
        s = str(self.dipendente)
        self.assertIn('mario', s.lower())
        self.assertIn('rossi', s.lower())
