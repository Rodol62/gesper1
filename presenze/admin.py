from django.contrib import admin

from .models import CausaleAssenzaNonMaturativa, MovimentoMonte, SaldoMonteDipendente


@admin.register(CausaleAssenzaNonMaturativa)
class CausaleAssenzaNonMaturativaAdmin(admin.ModelAdmin):
    list_display = ('ordine', 'codice_causale', 'etichetta', 'modalita', 'attiva', 'azienda')
    list_filter = ('attiva', 'modalita', 'azienda')
    search_fields = ('codice_causale', 'etichetta')
    ordering = ('ordine', 'id')


def _all_field_names(model):
    return [f.name for f in model._meta.fields]


@admin.register(SaldoMonteDipendente)
class SaldoMonteDipendenteAdmin(admin.ModelAdmin):
    list_display = (
        'dipendente',
        'azienda',
        'tipo_monte',
        'anno_competenza',
        'saldo_iniziale',
        'data_modifica',
    )
    list_filter = ('tipo_monte', 'anno_competenza', 'azienda')
    search_fields = ('dipendente__cognome', 'dipendente__nome', 'dipendente__codice_fiscale')
    readonly_fields = _all_field_names(SaldoMonteDipendente)

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(MovimentoMonte)
class MovimentoMonteAdmin(admin.ModelAdmin):
    list_display = (
        'saldo_monte',
        'data_movimento',
        'tipo_movimento',
        'quantita',
        'unita',
        'origine',
        'data_creazione',
    )
    list_filter = ('tipo_movimento', 'origine', 'unita')
    search_fields = ('note', 'idempotency_key', 'saldo_monte__dipendente__cognome')
    readonly_fields = _all_field_names(MovimentoMonte)
    raw_id_fields = ('saldo_monte', 'presenza', 'riepilogo_mensile', 'registrato_da')

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
