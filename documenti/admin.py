from django.contrib import admin

from .models import (
    CedolinoMotoreV4,
    Documento,
    ValidazioneCedolinoMotoreV4,
    VoceCedolinoMotoreV4,
)


@admin.register(Documento)
class DocumentoAdmin(admin.ModelAdmin):
    """Tutti i file caricati nel modulo documenti (anche quelli non esposti altrove in admin)."""

    list_display = (
        "id",
        "tipo",
        "azienda",
        "dipendente",
        "descrizione",
        "nome_file_breve",
        "data_caricamento",
    )
    list_filter = ("tipo", "azienda", "caricato_dal_dipendente", "visibile_al_dipendente")
    search_fields = (
        "descrizione",
        "dipendente__cognome",
        "dipendente__nome",
        "dipendente__codice_fiscale",
        "file",
    )
    raw_id_fields = ("azienda", "dipendente", "caricato_da")
    readonly_fields = ("data_caricamento",)
    date_hierarchy = "data_caricamento"
    list_per_page = 100
    list_max_show_all = 2000
    list_select_related = ("azienda", "dipendente")

    @admin.display(description="File")
    def nome_file_breve(self, obj: Documento) -> str:
        try:
            return obj.nome_file() or "-"
        except Exception:
            n = getattr(obj.file, "name", None) if obj.file else None
            return (n[-48:] if n else "-")

    def has_module_permission(self, request):
        return request.user.is_staff


class VoceCedolinoMotoreV4Inline(admin.TabularInline):
    model = VoceCedolinoMotoreV4
    extra = 0
    fields = ("codice", "descrizione", "tipo", "importo", "esito_check")
    readonly_fields = fields


class ValidazioneCedolinoMotoreV4Inline(admin.TabularInline):
    model = ValidazioneCedolinoMotoreV4
    extra = 0
    fields = ("descrizione", "valore_calc", "valore_letto", "delta", "esito")
    readonly_fields = fields


@admin.register(CedolinoMotoreV4)
class CedolinoMotoreV4Admin(admin.ModelAdmin):
    """Estrazione motore v4 (schema cedolini) legata ad anagrafica dipendente."""

    list_display = (
        "dipendente",
        "mese",
        "anno",
        "natura_busta",
        "tipo_cedolino",
        "verifica_stato",
        "verifica_il",
        "totale_lordo",
        "netto_busta",
        "importato_il",
    )
    list_filter = ("anno", "natura_busta", "tipo_cedolino", "verifica_stato", "estrazione_motore")
    search_fields = (
        "dipendente__cognome",
        "dipendente__nome",
        "dipendente__codice_fiscale",
        "file_pdf",
        "pdf_bytes_sha256",
    )
    raw_id_fields = ("dipendente", "documento")
    readonly_fields = (
        "importato_il",
        "pdf_bytes_sha256",
        "estrazione_motore",
        "verifica_stato",
        "verifica_il",
        "verifica_n_diff",
        "verifica_n_checks_formula_ko",
        "verifica_n_checks_formula_ko_bloccanti",
        "confronto_motore_paga_canonico",
    )

    @admin.display(
        description=(
            "Riconciliazione motore paga canonico (calcola_busta_paga_mese vs voci cedolino, "
            "tramite MappaturaVoceMotore)"
        )
    )
    def confronto_motore_paga_canonico(self, obj: CedolinoMotoreV4):
        from documenti.cedolino_conciliazione_motore_paga import confronto_cedolino_motore_paga_html

        return confronto_cedolino_motore_paga_html(obj)

    def has_module_permission(self, request):
        return request.user.is_staff
