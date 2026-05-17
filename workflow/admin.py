from django.contrib import admin
from .models import RichiestaWorkflow, StepApprovazione, RichiestaApprovazione


class StepApprovazioneInline(admin.TabularInline):
	model = StepApprovazione
	extra = 1


@admin.register(RichiestaWorkflow)
class RichiestaWorkflowAdmin(admin.ModelAdmin):
	list_display = ('nome', 'tipo_richiesta', 'numero_step', 'attivo')
	list_filter = ('tipo_richiesta', 'attivo')
	inlines = [StepApprovazioneInline]


@admin.register(StepApprovazione)
class StepApprovazioneAdmin(admin.ModelAdmin):
	list_display = ('workflow', 'numero_step', 'titolo', 'ruolo_approvatore')


@admin.register(RichiestaApprovazione)
class RichiestaApprovazioneAdmin(admin.ModelAdmin):
	list_display = ('richiesta', 'step', 'approvatore', 'stato')
	list_filter = ('stato',)
