"""Eliminazione comunicazione recesso in prova: solo admin/superuser."""
from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.urls import reverse

from accounts.models import Ruolo
from anagrafiche.models import Azienda, ComunicazioneRecessoProva, Dipendente
from rapporto_di_lavoro.models import RapportoDiLavoro, TipoContratto

User = get_user_model()


class EliminaComunicazioneRecessoProvaTests(TestCase):
    def setUp(self):
        self.az = Azienda.objects.create(
            nome='Az Recesso',
            partita_iva='IT12345678901',
            indirizzo='Via R 1',
            email='r@az.it',
        )
        admin_r, _ = Ruolo.objects.get_or_create(codice='admin', defaults={'nome': 'Admin'})
        cons_r, _ = Ruolo.objects.get_or_create(codice='consulente', defaults={'nome': 'Consulente'})
        self.admin_u = User.objects.create_user(
            username='adm_rec',
            password='pw',
            azienda=self.az,
            convalidato=True,
            privacy_accettata=True,
        )
        self.admin_u.ruoli.add(admin_r)
        self.cons_u = User.objects.create_user(
            username='cons_rec',
            password='pw',
            azienda=self.az,
            convalidato=True,
            privacy_accettata=True,
        )
        self.cons_u.ruoli.add(cons_r)
        self.dip = Dipendente.objects.create(
            azienda=self.az,
            nome='Nome',
            cognome='Cognome',
            codice_fiscale='CGNNME80A01H501Z',
            data_nascita=date(1980, 1, 1),
            indirizzo='x',
            email='d@az.it',
            telefono='1',
            data_assunzione=date(2026, 1, 1),
        )
        self.tc = TipoContratto.objects.create(nome='Tipo T', ccnl='TEST', tipo='det_full')
        self.rap = RapportoDiLavoro.objects.create(
            azienda=self.az,
            dipendente=self.dip,
            numero_contratto='CTR-REC-TEST-001',
            tipo_contratto=self.tc,
            data_inizio_rapporto=date(2026, 1, 10),
            posizione='Impiegato',
            livello_ccnl='1',
            qualifica='q',
            stipendio_lordo_mensile=Decimal('1500.00'),
        )
        self.com = ComunicazioneRecessoProva.objects.create(
            azienda=self.az,
            dipendente=self.dip,
            rapporto=self.rap,
            stato='in_verifica_consulente',
            testo_bozza='Testo',
        )

    def _post_elimina(self, user):
        c = Client()
        c.force_login(user)
        s = c.session
        s['azienda_id'] = self.az.pk
        s.save()
        url = reverse(
            'elimina_comunicazione_recesso_prova',
            kwargs={'pk': self.dip.pk, 'rapporto_id': self.rap.pk},
        )
        return c.post(url, {})

    def test_consulente_ottiene_redirect_login_o_forbidden(self):
        r = self._post_elimina(self.cons_u)
        self.assertIn(r.status_code, (302, 403))

    def test_admin_elimina_e_redirect_centro(self):
        r = self._post_elimina(self.admin_u)
        self.assertEqual(r.status_code, 302)
        self.assertIn('/rapporti/centro/', r.url)
        self.assertIn('recesso-prova-periodo', r.url)
        self.assertFalse(ComunicazioneRecessoProva.objects.filter(pk=self.com.pk).exists())
