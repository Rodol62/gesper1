from django.contrib import admin

from .forms import VoceGuidaForm
from .models import VoceGuida


@admin.register(VoceGuida)
class VoceGuidaAdmin(admin.ModelAdmin):
    form = VoceGuidaForm
    list_display = ('codice_modulo', 'codice_campo', 'titolo', 'attiva', 'ordine', 'aggiornata_il')
    list_filter = ('codice_modulo', 'attiva')
    search_fields = ('titolo', 'testo', 'codice_campo')
    ordering = ('codice_modulo', 'ordine', 'codice_campo')
    fieldsets = (
        (None, {
            'fields': ('codice_modulo', 'codice_campo', 'titolo', 'testo', 'attiva', 'ordine'),
            'description': (
                'Codici modulo dalla lista (allineati a <code>guida/registry.py</code> e '
                '<code>docs/MODULI_E_TRACCIAMENTO.md</code>). '
                '<code>codice_campo</code> vuoto = guida generale del modulo.'
            ),
        }),
    )
