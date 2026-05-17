from django.contrib import admin

from .models import MovimentoPartitarioNettoDipendente


@admin.register(MovimentoPartitarioNettoDipendente)
class MovimentoPartitarioNettoDipendenteAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "azienda",
        "dipendente",
        "tipo_movimento",
        "lato",
        "anno",
        "mese",
        "importo",
        "data_contabile",
        "creato_il",
    )
    list_filter = ("tipo_movimento", "lato", "anno", "azienda")
    search_fields = ("dipendente__cognome", "dipendente__nome", "causale")
    readonly_fields = ("creato_il", "aggiornato_il", "lato")
    raw_id_fields = ("dipendente", "cedolino_motore_v4", "documento_busta", "documento_ricevuta")
