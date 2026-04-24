from datetime import date

from django.test import TestCase

from anagrafiche.models import Azienda, Dipendente
from accounts.models import User
from notifiche_email.models import Notifica, TipoNotifica
from richieste.models import Richiesta

from workflow.models import RichiestaApprovazione, RichiestaWorkflow, StepApprovazione
from workflow.services import processa_azione_approvazione


class WorkflowAutomaticoTestCase(TestCase):
	def setUp(self):
		self.azienda = Azienda.objects.create(
			nome='Azienda Test',
			partita_iva='12345678901',
			indirizzo='Via Roma 1',
			email='azienda@test.local',
			telefono='0100000000',
		)
		self.hr = User.objects.create_user(
			username='hr_user',
			password='x',
			azienda=self.azienda,
		)
		# self.hr.ruoli.add(Ruolo.objects.get(codice='hr'))
		self.dip_user = User.objects.create_user(
			username='dip_user',
			password='x',
			azienda=self.azienda,
		)
		# self.dip_user.ruoli.add(Ruolo.objects.get(codice='dipendente'))
		self.dipendente = Dipendente.objects.create(
			azienda=self.azienda,
			nome='Mario',
			cognome='Rossi',
			codice_fiscale='RSSMRA80A01H501X',
			data_nascita=date(1980, 1, 1),
			indirizzo='Via Milano 2',
			email='mario.rossi@test.local',
			telefono='3331234567',
			data_assunzione=date(2024, 1, 1),
			ruolo='Impiegato',
			livello='3',
			stato='attivo',
		)

		self.workflow = RichiestaWorkflow.objects.create(
			nome='Ferie Standard',
			tipo_richiesta='ferie',
			numero_step=2,
			attivo=True,
		)
		self.step1 = StepApprovazione.objects.create(
			workflow=self.workflow,
			numero_step=1,
			titolo='Approvazione manager',
			ruolo_approvatore='manager',
			order=1,
		)
		self.step2 = StepApprovazione.objects.create(
			workflow=self.workflow,
			numero_step=2,
			titolo='Controfirma HR',
			ruolo_approvatore='hr',
			order=2,
		)

		TipoNotifica.objects.create(
			nome='Richiesta Approvata',
			evento_trigger='richiesta_approvata',
			template_subject='Richiesta approvata',
			template_body='ok {dipendente_nome}',
			attivo=True,
		)
		TipoNotifica.objects.create(
			nome='Richiesta Rifiutata',
			evento_trigger='richiesta_rifiutata',
			template_subject='Richiesta rifiutata',
			template_body='ko {dipendente_nome}',
			attivo=True,
		)
		TipoNotifica.objects.create(
			nome='Richiesta da approvare',
			evento_trigger='richiesta_da_approvare',
			template_subject='Da approvare',
			template_body='todo {dipendente_nome}',
			attivo=True,
		)

	def test_creazione_richiesta_avvia_step1(self):
		richiesta = Richiesta.objects.create(
			dipendente=self.dipendente,
			azienda=self.azienda,
			tipo='ferie',
			data_inizio=date(2026, 3, 10),
			data_fine=date(2026, 3, 12),
			stato='inviata',
			richiesta_da=self.dip_user,
		)

		approvazioni = RichiestaApprovazione.objects.filter(richiesta=richiesta)
		self.assertEqual(approvazioni.count(), 1)
		self.assertEqual(approvazioni.first().step.numero_step, 1)

	def test_approvazione_step1_genera_step2(self):
		richiesta = Richiesta.objects.create(
			dipendente=self.dipendente,
			azienda=self.azienda,
			tipo='ferie',
			data_inizio=date(2026, 3, 10),
			data_fine=date(2026, 3, 12),
			stato='inviata',
			richiesta_da=self.dip_user,
		)
		step1_instanza = RichiestaApprovazione.objects.get(richiesta=richiesta, step=self.step1)

		processa_azione_approvazione(step1_instanza, self.hr, 'approvato', 'ok')

		self.assertTrue(RichiestaApprovazione.objects.filter(richiesta=richiesta, step=self.step2).exists())
		richiesta.refresh_from_db()
		self.assertEqual(richiesta.stato, 'inviata')

	def test_approvazione_step_finale_chiude_richiesta(self):
		richiesta = Richiesta.objects.create(
			dipendente=self.dipendente,
			azienda=self.azienda,
			tipo='ferie',
			data_inizio=date(2026, 3, 10),
			data_fine=date(2026, 3, 12),
			stato='inviata',
			richiesta_da=self.dip_user,
		)
		step1_instanza = RichiestaApprovazione.objects.get(richiesta=richiesta, step=self.step1)
		processa_azione_approvazione(step1_instanza, self.hr, 'approvato', 'ok step1')

		step2_instanza = RichiestaApprovazione.objects.get(richiesta=richiesta, step=self.step2)
		processa_azione_approvazione(step2_instanza, self.hr, 'approvato', 'ok finale')

		richiesta.refresh_from_db()
		self.assertEqual(richiesta.stato, 'approvata')
		self.assertTrue(Notifica.objects.filter(tipo__evento_trigger='richiesta_approvata').exists())

	def test_rifiuto_step_chiude_richiesta_e_crea_notifica(self):
		richiesta = Richiesta.objects.create(
			dipendente=self.dipendente,
			azienda=self.azienda,
			tipo='ferie',
			data_inizio=date(2026, 3, 10),
			data_fine=date(2026, 3, 12),
			stato='inviata',
			richiesta_da=self.dip_user,
		)
		step1_instanza = RichiestaApprovazione.objects.get(richiesta=richiesta, step=self.step1)
		processa_azione_approvazione(step1_instanza, self.hr, 'rifiutato', 'manca copertura')

		richiesta.refresh_from_db()
		self.assertEqual(richiesta.stato, 'rifiutata')
		self.assertTrue(Notifica.objects.filter(tipo__evento_trigger='richiesta_rifiutata').exists())
