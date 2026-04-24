from django.contrib import admin
from django.contrib import messages
from django.utils.html import format_html

from accounts.formatting import euro_it_str

from .models import LibroPagaStorico


def _riordina_cronologico(queryset):
    """Riassegna ordinamento progressivo per data_pagamento, per ogni dipendente."""
    from collections import defaultdict
    per_dip = defaultdict(list)
    for v in queryset.order_by('dipendente', 'data_pagamento', 'periodo_riferimento'):
        per_dip[v.dipendente_id].append(v)
    aggiornati = 0
    for voci in per_dip.values():
        for i, voce in enumerate(voci, start=1):
            if voce.ordinamento != i:
                voce.ordinamento = i
                voce.save(update_fields=['ordinamento'])
                aggiornati += 1
    return aggiornati


@admin.register(LibroPagaStorico)
class LibroPagaStoricoAdmin(admin.ModelAdmin):

    # ── List ─────────────────────────────────────────────────────────────────
    list_display = (
        'dipendente', 'azienda', 'periodo_riferimento',
        'lordo_mensile_fmt', 'importo_fmt', 'costo_azienda_fmt',
        'data_pagamento', 'data_inizio_rapporto', 'data_fine_rapporto',
        'livello_ccnl', 'ordinamento', 'fonte_dati',
    )
    list_display_links = ('dipendente', 'periodo_riferimento')
    list_editable = ('ordinamento',)
    list_filter = ('azienda', 'fonte_dati', 'livello_ccnl')
    search_fields = (
        'dipendente__nome', 'dipendente__cognome',
        'periodo_riferimento', 'qualifica', 'note',
    )
    ordering = ('dipendente', 'ordinamento', 'data_pagamento')
    readonly_fields = ('creato_il', 'modificato_il')
    date_hierarchy = 'data_pagamento'

    # ── Fieldsets ─────────────────────────────────────────────────────────────
    fieldsets = (
        ('Rapporto di lavoro', {
            'fields': (
                ('dipendente', 'azienda'),
                ('data_inizio_rapporto', 'data_fine_rapporto'),
                ('livello_ccnl', 'qualifica', 'tipo_contratto'),
            )
        }),
        ('Periodo di paga', {
            'fields': (
                ('periodo_riferimento', 'data_pagamento', 'ordinamento'),
            )
        }),
        ('Ore lavorate', {
            'fields': (
                ('ore_ordinarie', 'ore_straordinario', 'ore_assenza'),
            ),
            'classes': ('collapse',),
        }),
        ('Competenze (lordo)', {
            'fields': (
                ('retribuzione_base', 'indennita_accessorie', 'lordo_mensile'),
            )
        }),
        ('Trattenute c/dipendente', {
            'fields': (
                ('inps_dipendente', 'irpef', 'addizionali'),
                ('trattamento_integrativo', 'altre_trattenute'),
            )
        }),
        ('Netto erogato', {
            'fields': (
                ('importo',),
            )
        }),
        ('Oneri c/azienda', {
            'fields': (
                ('inps_azienda', 'inail_azienda', 'costo_azienda'),
            ),
            'classes': ('collapse',),
        }),
        ('Accantonamenti', {
            'fields': (
                ('tfr_mensile', 'rateo_13', 'rateo_14'),
            ),
            'classes': ('collapse',),
        }),
        ('Metadati', {
            'fields': ('fonte_dati', 'note', 'creato_il', 'modificato_il'),
            'classes': ('collapse',),
        }),
    )

    # ── Formatted display ─────────────────────────────────────────────────────
    @admin.display(description='Lordo (€)', ordering='lordo_mensile')
    def lordo_mensile_fmt(self, obj):
        if obj.lordo_mensile is not None:
            return format_html('<span style="color:#1a5276">{}</span>', euro_it_str(obj.lordo_mensile))
        return '—'

    @admin.display(description='Netto (€)', ordering='importo')
    def importo_fmt(self, obj):
        return format_html('<strong>{}</strong>', euro_it_str(obj.importo))

    @admin.display(description='Costo az. (€)', ordering='costo_azienda')
    def costo_azienda_fmt(self, obj):
        if obj.costo_azienda is not None:
            return format_html('<span style="color:#922b21">{}</span>', euro_it_str(obj.costo_azienda))
        return '—'

    # ── Admin actions ─────────────────────────────────────────────────────────
    actions = ['riordina_cronologico', 'riordina_tutti']

    @admin.action(description='Riordina cronologicamente (selezione)')
    def riordina_cronologico(self, request, queryset):
        aggiornati = _riordina_cronologico(queryset)
        self.message_user(
            request,
            f'Ordinamento aggiornato per {aggiornati} voci.',
            messages.SUCCESS,
        )

    @admin.action(description='Riordina cronologicamente TUTTI i dipendenti')
    def riordina_tutti(self, request, queryset):
        aggiornati = _riordina_cronologico(LibroPagaStorico.objects.all())
        self.message_user(
            request,
            f'Ordinamento aggiornato per {aggiornati} voci (tutti i dipendenti).',
            messages.SUCCESS,
        )
