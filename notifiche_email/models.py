from django.db import models
from django.contrib.auth import get_user_model
from anagrafiche.models import Azienda

User = get_user_model()


class TipoNotifica(models.Model):
	nome = models.CharField(max_length=100)
	evento_trigger = models.CharField(max_length=100, unique=True)
	template_subject = models.CharField(max_length=255)
	template_body = models.TextField()
	invia_email = models.BooleanField(default=True)
	destinatario = models.CharField(
		max_length=50,
		choices=[
			('dipendente', 'Al dipendente'),
			('manager', 'Al manager'),
			('hr', 'All HR'),
			('admin', 'All admin'),
		],
		default='dipendente'
	)
	attivo = models.BooleanField(default=True)

	class Meta:
		verbose_name = 'Tipo Notifica'
		verbose_name_plural = 'Tipi Notifica'
		ordering = ['nome']

	def __str__(self):
		return self.nome


class Notifica(models.Model):
	STATO_CHOICES = [
		('pending', 'In coda'),
		('sent', 'Inviata'),
		('failed', 'Errore'),
	]

	tipo = models.ForeignKey(TipoNotifica, on_delete=models.CASCADE)
	azienda = models.ForeignKey(Azienda, on_delete=models.CASCADE)
	destinatario = models.ForeignKey(User, on_delete=models.CASCADE)
	email_destinatario = models.EmailField()

	subject = models.CharField(max_length=255)
	body_html = models.TextField()

	data_creazione = models.DateTimeField(auto_now_add=True)
	data_invio = models.DateTimeField(null=True, blank=True)
	stato = models.CharField(max_length=20, choices=STATO_CHOICES, default='pending')

	class Meta:
		verbose_name = 'Notifica'
		verbose_name_plural = 'Notifiche'
		ordering = ['-data_creazione']
