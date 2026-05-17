"""Test mirati su ProfiloCandidatoForm (territorio, initial, nascosto luogo_nascita)."""

from django.test import TestCase

from accounts.forms import ProfiloCandidatoForm
from accounts.models import ProfiloCandidato, User


class ProfiloCandidatoFormGeoTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username='form_geo_u1',
            password='test-pass-123',
            email='form_geo_u1@test.local',
        )

    def test_unbound_new_instance_prefills_palermo_when_no_luogo(self):
        profilo = ProfiloCandidato(user=self.user)
        form = ProfiloCandidatoForm(instance=profilo)
        self.assertEqual(form.initial.get('regione_nascita'), 'SICILIA')
        self.assertEqual(form.initial.get('provincia_nascita'), 'PA')
        self.assertEqual(form.initial.get('comune_nascita'), 'PALERMO')

    def test_unbound_saved_luogo_inference_no_palermo_overwrite(self):
        profilo = ProfiloCandidato.objects.create(user=self.user, luogo_nascita='CATANIA')
        form = ProfiloCandidatoForm(instance=profilo)
        self.assertEqual(form.initial.get('regione_nascita'), 'SICILIA')
        self.assertEqual(form.initial.get('provincia_nascita'), 'CT')
        self.assertEqual(form.initial.get('comune_nascita'), 'CATANIA')

    def test_unbound_luogo_with_parenthesis(self):
        profilo = ProfiloCandidato.objects.create(user=self.user, luogo_nascita='PALERMO (PA)')
        form = ProfiloCandidatoForm(instance=profilo)
        self.assertEqual(form.initial.get('regione_nascita'), 'SICILIA')
        self.assertEqual(form.initial.get('provincia_nascita'), 'PA')
        self.assertEqual(form.initial.get('comune_nascita'), 'PALERMO')

    def test_luogo_nascita_widget_hidden(self):
        form = ProfiloCandidatoForm()
        self.assertEqual(
            form.fields['luogo_nascita'].widget.__class__.__name__,
            'HiddenInput',
        )
