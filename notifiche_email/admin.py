from django.contrib import admin
from .models import TipoNotifica, Notifica


@admin.register(TipoNotifica)
class TipoNotificaAdmin(admin.ModelAdmin):
	list_display = ('nome', 'evento_trigger', 'attivo')
	list_filter = ('attivo',)


@admin.register(Notifica)
class NotificaAdmin(admin.ModelAdmin):
	list_display = ('tipo', 'destinatario', 'stato', 'data_creazione')
	list_filter = ('stato', 'data_creazione')
	readonly_fields = ('data_creazione', 'data_invio')
