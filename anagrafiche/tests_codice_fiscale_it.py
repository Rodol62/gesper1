"""Decodifica e validazione codice fiscale (MEF + Belfiore)."""

from datetime import date

from django.test import TestCase
from django.urls import reverse

from django.contrib.auth import get_user_model

from anagrafiche.codice_fiscale_it import (
    decodifica_codice_fiscale,
    merge_profilo_candidato_da_codice_fiscale,
    valida_cf,
)

User = get_user_model()


class CodiceFiscaleDecodificaTest(TestCase):
    @staticmethod
    def _genera_omocodico_valido(cf_base: str, idx: int, lettera_omo: str) -> str:
        base15 = list(cf_base[:15])
        base15[idx] = lettera_omo
        prefix = ''.join(base15)
        for c in 'ABCDEFGHIJKLMNOPQRSTUVWXYZ':
            tent = prefix + c
            if valida_cf(tent):
                return tent
        raise AssertionError('Impossibile generare CF omocodico valido dal base.')

    def test_valida_cf_rssmra(self):
        self.assertTrue(valida_cf('RSSMRA80A01H501U'))

    def test_valida_cf_omocodico(self):
        cf_omo = self._genera_omocodico_valido('RSSMRA80A01H501U', 14, 'M')
        self.assertTrue(valida_cf(cf_omo))

    def test_decodifica_roma_maschio(self):
        d = decodifica_codice_fiscale('RSSMRA80A01H501U')
        self.assertIsNotNone(d)
        assert d is not None
        self.assertEqual(d.data_nascita, date(1980, 1, 1))
        self.assertEqual(d.sesso, 'M')
        self.assertTrue(d.nascita_italiana)
        self.assertEqual(d.codice_belfiore, 'H501')
        self.assertEqual(d.comune_nome, 'ROMA')
        self.assertEqual(d.provincia_sigla, 'RM')

    def test_decodifica_femmina(self):
        d = decodifica_codice_fiscale('RSSMRA80A41H501Y')
        self.assertIsNotNone(d)
        assert d is not None
        self.assertEqual(d.sesso, 'F')
        self.assertEqual(d.data_nascita, date(1980, 1, 1))

    def test_decodifica_omocodico_stessi_dati(self):
        cf_omo = self._genera_omocodico_valido('RSSMRA80A01H501U', 14, 'M')
        d = decodifica_codice_fiscale(cf_omo)
        self.assertIsNotNone(d)
        assert d is not None
        self.assertEqual(d.data_nascita, date(1980, 1, 1))
        self.assertEqual(d.sesso, 'M')
        self.assertEqual(d.codice_belfiore, 'H501')
        self.assertEqual(d.comune_nome, 'ROMA')

    def test_merge_profilo_sovrascrive_geo_nascita(self):
        cleaned = {
            'codice_fiscale': 'RSSMRA80A01H501U',
            'regione_nascita': 'SICILIA',
            'provincia_nascita': 'PA',
            'comune_nascita': 'PALERMO',
            'luogo_nascita': 'PALERMO',
        }
        merge_profilo_candidato_da_codice_fiscale(cleaned)
        self.assertEqual(cleaned['regione_nascita'], 'LAZIO')
        self.assertEqual(cleaned['provincia_nascita'], 'RM')
        self.assertEqual(cleaned['comune_nascita'], 'ROMA')


class ApiDecodificaCfTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username='cf_api_u',
            password='x',
            email='cf_api_u@test.local',
        )

    def test_api_richiede_login(self):
        url = reverse('api_decodifica_cf')
        r = self.client.get(url, {'cf': 'RSSMRA80A01H501U'})
        self.assertEqual(r.status_code, 302)

    def test_api_decodifica_ok(self):
        self.client.login(username='cf_api_u', password='x')
        url = reverse('api_decodifica_cf')
        r = self.client.get(url, {'cf': 'RSSMRA80A01H501U'})
        self.assertEqual(r.status_code, 200)
        data = r.json()
        self.assertTrue(data.get('ok'))
        self.assertEqual(data.get('data_nascita'), '1980-01-01')
        self.assertEqual(data.get('sesso'), 'M')
        self.assertTrue(data['nascita'].get('italia'))

    def test_api_cf_non_valido(self):
        self.client.login(username='cf_api_u', password='x')
        url = reverse('api_decodifica_cf')
        r = self.client.get(url, {'cf': 'RSSMRA80A01H501X'})
        self.assertEqual(r.status_code, 400)
        self.assertFalse(r.json().get('ok'))
