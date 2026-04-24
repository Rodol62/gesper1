from django.test import TestCase

from accounts.models import ProfiloCandidato, User
from accounts.sync_anagrafica import (
    diagnostica_anagrafica_candidato,
    sincronizza_dipendente_da_profilo,
)
from anagrafiche.models import Azienda


class SyncAnagraficaTests(TestCase):
    def setUp(self):
        self.azienda = Azienda.objects.create(
            nome="Azienda Test",
            partita_iva="12345678901",
            indirizzo="Via Test 1",
            email="azienda@test.local",
            telefono="0000000000",
        )

    def test_diagnostica_segnala_profilo_mancante(self):
        user = User.objects.create_user(
            username="diag_user_1",
            password="x",
            email="diag1@test.local",
        )
        anomalie = diagnostica_anagrafica_candidato(user, None)
        self.assertTrue(anomalie)
        self.assertIn("Profilo candidato mancante", anomalie[0]["titolo"])

    def test_sync_blocca_aggancio_cf_conflitto(self):
        user1 = User.objects.create_user(username="sync_u1", password="x", email="u1@test.local")
        prof1 = ProfiloCandidato.objects.create(
            user=user1,
            azienda_interesse=self.azienda,
            codice_fiscale="RSSMRA80A01H501Z",
        )
        dip1 = sincronizza_dipendente_da_profilo(user1, prof1, create_if_missing=True)
        self.assertIsNotNone(dip1)
        prof1.save(update_fields=["dipendente"])

        user2 = User.objects.create_user(username="sync_u2", password="x", email="u2@test.local")
        prof2 = ProfiloCandidato.objects.create(
            user=user2,
            azienda_interesse=self.azienda,
            codice_fiscale="RSSMRA80A01H501Z",
        )
        dip2 = sincronizza_dipendente_da_profilo(user2, prof2, create_if_missing=True)
        self.assertIsNone(dip2)
        self.assertIsNone(prof2.dipendente_id)

