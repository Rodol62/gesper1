from django.db import models
from django.contrib.auth import get_user_model
from richieste.models import Richiesta

User = get_user_model()


class RichiestaWorkflow(models.Model):
	TIPO_CHOICES = [
		('ferie', 'Ferie'),
		('permesso', 'Permesso'),
		('malattia', 'Malattia'),
	]

	nome = models.CharField(max_length=100)
	tipo_richiesta = models.CharField(max_length=50, choices=TIPO_CHOICES)
	numero_step = models.IntegerField(default=1)
	richiede_documenti = models.BooleanField(default=False)
	timeout_giorni = models.IntegerField(default=3)
	notifica_rifiuto = models.BooleanField(default=True)
	attivo = models.BooleanField(default=True)
	data_creazione = models.DateTimeField(auto_now_add=True)

	class Meta:
		verbose_name = 'Workflow Richiesta'
		verbose_name_plural = 'Workflow Richieste'
		unique_together = [('tipo_richiesta', 'nome')]

	def __str__(self):
		return f"{self.nome} ({self.tipo_richiesta})"


class StepApprovazione(models.Model):
	RUOLO_CHOICES = [
		('manager', 'Manager diretto'),
		('hr', 'HR'),
		('admin', 'Administrator'),
	]

	workflow = models.ForeignKey(RichiestaWorkflow, on_delete=models.CASCADE, related_name='steps')
	numero_step = models.IntegerField()
	titolo = models.CharField(max_length=100)
	ruolo_approvatore = models.CharField(max_length=50, choices=RUOLO_CHOICES)
	puo_approvare = models.BooleanField(default=True)
	puo_rifiutare = models.BooleanField(default=True)
	order = models.IntegerField()

	class Meta:
		verbose_name = 'Step Approvazione'
		verbose_name_plural = 'Step Approvazioni'
		ordering = ['workflow', 'numero_step']
		unique_together = [('workflow', 'numero_step')]

	def __str__(self):
		return f"{self.workflow.nome} - Step {self.numero_step}"


class RichiestaApprovazione(models.Model):
	STATO_CHOICES = [
		('in_attesa', 'In attesa'),
		('approvato', 'Approvato'),
		('rifiutato', 'Rifiutato'),
	]

	richiesta = models.ForeignKey(
		Richiesta,
		on_delete=models.CASCADE,
		related_name='approvazioni_workflow'
	)
	step = models.ForeignKey(StepApprovazione, on_delete=models.CASCADE)
	approvatore = models.ForeignKey(
		User,
		on_delete=models.CASCADE,
		null=True,
		blank=True,
		related_name='da_approvare'
	)

	stato = models.CharField(max_length=20, choices=STATO_CHOICES, default='in_attesa')
	data_assegnazione = models.DateTimeField(auto_now_add=True)
	data_azione = models.DateTimeField(null=True, blank=True)
	commento = models.TextField(blank=True)
	notifica_inviata = models.BooleanField(default=False)

	class Meta:
		verbose_name = 'Richiesta Approvazione'
		verbose_name_plural = 'Richieste Approvazione'
		ordering = ['data_assegnazione']

	def __str__(self):
		return f"{self.richiesta} - Step {self.step.numero_step}: {self.stato}"
