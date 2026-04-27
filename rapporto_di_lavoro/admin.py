from django.contrib import admin
from django.contrib import messages
from django.http import HttpResponseRedirect
from django.urls import path, reverse
from django.utils.translation import gettext_lazy as _


class AnnoListFilter(admin.SimpleListFilter):
    """Filtro anno che mostra valori senza separatore delle migliaia (fix locale IT)."""
    title = _('Anno')
    parameter_name = 'anno'

    def lookups(self, request, model_admin):
        anni = (
            model_admin.get_queryset(request)
            .order_by('-anno')
            .values_list('anno', flat=True)
            .distinct()
        )
        return [(str(a), str(a)) for a in anni]

    def queryset(self, request, queryset):
        if self.value():
            return queryset.filter(anno=self.value())
        return queryset

from accounts.formatting import euro_it_str

from .forms import PropostaAssunzioneForm
from .models import (
	TestMotorePaga,
	TipoContratto,
	RapportoDiLavoro,
	AddendumContrattuale,
	ModuloContrattuale,
	Mansione,
	MansioneLivelloCCNL,
	ParametroCCNLTurismo,
	RegolaNormativaCCNL,
	PropostaAssunzione,
	SimulazioneOrganico,
	SimulazioneVoceRetributivaOre,
	VoceRetributiva,
	MappaturaVoceMotore,
	ParametroVoceRetributiva,
	# Nuovi modelli parametrici CCNL
	CCNL,
	ParametroOrario,
	ParametroMaggiorazione,
	ParametroScattiAnnuali,
	ParametroContributi,
	ParametroRatei,
	ValidazioneOrario,
	TipoAssenza,
	DecontribuzioneParametro,
	FringeBenefitSoglia,
	# Modelli fiscali e bonus
	ScaglioneIRPEF,
	DetrazioneLavoroDipendente,
	AddizionaleRegionale,
	AddizionaleComunale,
	BonusFiscale,
	RiepilogoMensileDipendente,
	FestivitaCalendario,
	ChiusuraAziendale,
	CalendarioPresenzeDipendente,
	SimulazionePagaSalvata,
)


@admin.register(TipoContratto)
class TipoContrattoAdmin(admin.ModelAdmin):
	list_display = ('nome', 'ccnl', 'durata_giorni', 'attivo')
	list_filter = ('attivo', 'ccnl')
	search_fields = ('nome', 'ccnl')


@admin.register(RapportoDiLavoro)
class RapportoDiLavoroAdmin(admin.ModelAdmin):
	list_display = ('numero_contratto', 'dipendente', 'tipo_contratto', 'stato')
	list_filter = ('stato', 'tipo_contratto')
	search_fields = ('numero_contratto', 'dipendente__nome', 'dipendente__cognome')


@admin.register(AddendumContrattuale)
class AddendumContrattualeAdmin(admin.ModelAdmin):
	list_display = ('rapporto', 'tipo', 'data_decorrenza', 'riferimento_atto', 'data_creazione')
	list_filter = ('tipo', 'data_decorrenza')
	search_fields = ('rapporto__numero_contratto', 'riferimento_atto', 'note')
	raw_id_fields = ('rapporto', 'tipo_contratto', 'parametro_ccnl', 'creato_da')
	readonly_fields = ('data_creazione',)


@admin.register(ModuloContrattuale)
class ModuloContrattualeAdmin(admin.ModelAdmin):
	list_display = ('nome', 'categoria', 'compilabile_da_dipendente', 'attivo')
	list_filter = ('categoria', 'compilabile_da_dipendente', 'attivo')
	search_fields = ('nome', 'descrizione')


@admin.register(Mansione)
class MansioneAdmin(admin.ModelAdmin):
	list_display = ('nome', 'ordinamento', 'attivo')
	list_filter = ('attivo',)
	search_fields = ('nome',)
	ordering = ('ordinamento', 'nome')


@admin.register(MansioneLivelloCCNL)
class MansioneLivelloCCNLAdmin(admin.ModelAdmin):
	list_display = (
		'mansione',
		'livello',
		'qualifica_tabellare',
		'fonte',
		'priorita',
		'ccnl',
		'versione',
		'sezione',
		'attivo',
	)
	list_filter = ('attivo', 'fonte', 'livello', 'ccnl', 'versione', 'sezione')
	search_fields = ('mansione__nome', 'livello', 'qualifica_tabellare', 'ccnl', 'versione', 'note')
	ordering = ('-priorita', 'mansione__ordinamento', 'mansione__nome', 'livello')
	list_editable = ('priorita', 'attivo')
	autocomplete_fields = ('mansione',)
	fieldsets = (
		('Collegamento', {
			'fields': ('mansione', 'livello', 'qualifica_tabellare')
		}),
		('Contesto CCNL', {
			'fields': ('ccnl', 'versione', 'sezione')
		}),
		('Personalizzazione', {
			'description': 'Usa fonte=Personalizzazione admin e priorita alta per override di voci non previste.',
			'fields': ('fonte', 'priorita', 'valida_da', 'valida_a', 'attivo', 'note')
		}),
	)


@admin.register(ParametroCCNLTurismo)
class ParametroCCNLTurismoAdmin(admin.ModelAdmin):
	list_display = (
		'livello_ordinamento',
		'ccnl',
		'versione',
		'sezione',
		'livello',
		'qualifica',
		'minimo_tabellare',
		'contingenza_mensile',
		'totale_tabellare',
		'fonte_tabella',
		'data_rilevazione_tabella',
		'attivo',
	)
	list_filter = ('ccnl', 'versione', 'sezione', 'fonte_tabella', 'attivo')
	search_fields = ('qualifica', 'livello', 'tipo_contratto_nazionale')
	ordering = ('ccnl', 'versione', 'livello_ordinamento', 'livello')


@admin.register(RegolaNormativaCCNL)
class RegolaNormativaCCNLAdmin(admin.ModelAdmin):
	list_display = (
		'ccnl',
		'versione',
		'sezione',
		'livello',
		'ore_settimanali',
		'ferie_annue_giorni',
		'permessi_annui_ore',
		'attivo',
	)
	list_filter = ('ccnl', 'versione', 'sezione', 'attivo')
	search_fields = ('ccnl', 'versione', 'livello')


@admin.register(PropostaAssunzione)
class PropostaAssunzioneAdmin(admin.ModelAdmin):
	form = PropostaAssunzioneForm

	list_display = (
		'numero_proposta',
		'dipendente',
		'azienda',
		'mansione',
		'livello_ccnl',
		'stipendio_lordo_mensile',
		'stato',
		'accettata_dipendente',
		'approvata_admin',
		'contratto_generato',
	)
	list_filter = ('stato', 'accettata_dipendente', 'approvata_admin', 'azienda')
	search_fields = ('numero_proposta', 'dipendente__nome', 'dipendente__cognome', 'titolo')

	readonly_fields = (
		'numero_proposta',
		'azienda',
		'livello_ccnl',
		'qualifica',
		'stipendio_lordo_mensile',
		'paga_base_mensile',
		'contingenza_mensile',
		'edr_mensile',
		'creato_da',
		'data_creazione',
		'data_modifica',
		'contratto_generato',
	)

	fieldsets = (
		('Identificazione', {
			'fields': (
				'numero_proposta', 'titolo', 'stato',
				'azienda', 'dipendente',
				'tipo_contratto', 'modulo', 'mansione', 'ccnl_livello_scelta', 'parametro_ccnl',
				'creato_da', 'data_creazione', 'data_modifica',
			)
		}),
		('Posizione e contratto', {
			'fields': (
				'posizione', 'livello_ccnl', 'qualifica',
				'data_inizio_rapporto', 'data_fine_rapporto',
			)
		}),
		('Retribuzione (da CCNL — sola lettura)', {
			'description': 'Valori calcolati dalla simulazione sulla base del parametro CCNL selezionato.',
			'fields': (
				'stipendio_lordo_mensile',
				'paga_base_mensile', 'contingenza_mensile',
				'edr_mensile', 'superminimo_mensile', 'indennita_mensile',
			)
		}),
		('Orario di lavoro', {
			'fields': (
				'ore_settimanali', 'ore_mensili', 'ore_giornaliere',
				'decorrenza_validita_da', 'decorrenza_validita_a',
			)
		}),
		('Scatti di anzianità', {
			'classes': ('collapse',),
			'fields': (
				'scatto_periodicita_mesi', 'scatto_importo', 'numero_scatti_massimi',
			)
		}),
		('Straordinario', {
			'classes': ('collapse',),
			'fields': (
				'straordinario_diurno_maggiorazione',
				'straordinario_notturno_maggiorazione',
				'straordinario_festivo_maggiorazione',
				'riposi_compensativi_regola',
			)
		}),
		('Accettazione e approvazione', {
			'fields': (
				'accettata_dipendente', 'data_accettazione_dipendente', 'note_dipendente',
				'approvata_admin', 'data_approvazione_admin', 'note_admin',
				'contratto_generato',
			)
		}),
		('Note e normativa', {
			'classes': ('collapse',),
			'fields': (
				'note', 'riferimenti_normativi', 'dichiarazione_conformita_legale',
			)
		}),
	)


@admin.register(SimulazioneOrganico)
class SimulazioneOrganicoAdmin(admin.ModelAdmin):
	list_display = (
		'mese_riferimento',
		'azienda',
		'utente',
		'data_creazione',
	)
	list_filter = ('mese_riferimento', 'azienda', 'data_creazione')
	search_fields = ('azienda__nome', 'utente__username', 'mese_riferimento')
	date_hierarchy = 'data_creazione'
	readonly_fields = ('data_creazione',)


@admin.register(SimulazioneVoceRetributivaOre)
class SimulazioneVoceRetributivaOreAdmin(admin.ModelAdmin):
	list_display = (
		'mese_riferimento',
		'azienda',
		'ruolo_id',
		'dipendente_nome',
		'voce',
		'presente',
		'importo_lordo',
		'data_modifica',
	)
	list_filter = ('mese_riferimento', 'azienda', 'voce', 'presente')
	search_fields = ('azienda__nome', 'ruolo_id', 'ruolo_label', 'dipendente_nome')
	readonly_fields = ('data_creazione', 'data_modifica')


@admin.register(ParametroVoceRetributiva)
class ParametroVoceRetributivaAdmin(admin.ModelAdmin):
	list_display = (
		'ccnl', 'versione', 'sezione', 'livello', 'formato_anno', 'voce',
		'importo_mensile', 'importo_orario', 'attivo',
	)
	list_filter = ('ccnl', 'versione', 'sezione', AnnoListFilter, 'attivo', 'voce')
	search_fields = ('ccnl', 'versione', 'livello', 'voce__codice', 'voce__nome')
	readonly_fields = ('data_creazione', 'data_modifica')

	def formato_anno(self, obj):
		return str(int(obj.anno)) if obj.anno else '-'
	formato_anno.short_description = 'Anno'
	formato_anno.admin_order_field = 'anno'


@admin.register(VoceRetributiva)
class VoceRetributivaAdmin(admin.ModelAdmin):
	list_display = (
		'codice',
		'nome',
		'categoria',
		'imponibile_inps',
		'imponibile_inail',
		'imponibile_irpef',
		'imponibile_parziale',
		'attivo',
	)
	list_filter = (
		'categoria',
		'imponibile_inps',
		'imponibile_inail',
		'imponibile_irpef',
		'imponibile_parziale',
		'attivo',
	)
	search_fields = ('codice', 'nome', 'descrizione', 'riferimento_normativo')
	readonly_fields = ('data_creazione', 'data_modifica')


@admin.register(MappaturaVoceMotore)
class MappaturaVoceMotoreAdmin(admin.ModelAdmin):
	list_display = (
		'codice_voce',
		'ordine_calcolo',
		'voce_retributiva',
		'imponibile_inps',
		'imponibile_inail',
		'imponibile_irpef',
		'matura_tredicesima',
		'matura_quattordicesima',
		'concorre_tfr',
		'etichetta_riconciliazione',
		'attivo',
	)
	list_filter = ('attivo', 'imponibile_inps', 'concorre_tfr', 'matura_tredicesima')
	search_fields = ('codice_voce', 'etichetta_riconciliazione', 'note_riconciliazione')
	readonly_fields = ('data_creazione', 'data_modifica')
	autocomplete_fields = ('voce_retributiva',)


@admin.register(FestivitaCalendario)
class FestivitaCalendarioAdmin(admin.ModelAdmin):
	list_display = ('data', 'nome', 'livello', 'regione', 'provincia', 'comune', 'attivo')
	list_filter = ('livello', 'regione', 'provincia', 'comune', 'attivo')
	search_fields = ('nome', 'regione', 'provincia', 'comune')
	date_hierarchy = 'data'
	readonly_fields = ('data_creazione',)


@admin.register(ChiusuraAziendale)
class ChiusuraAziendaleAdmin(admin.ModelAdmin):
	list_display = ('azienda', 'data_inizio', 'data_fine', 'trattamento', 'attivo')
	list_filter = ('azienda', 'trattamento', 'attivo')
	search_fields = ('azienda__nome', 'descrizione')
	date_hierarchy = 'data_inizio'
	readonly_fields = ('data_creazione',)


@admin.register(CalendarioPresenzeDipendente)
class CalendarioPresenzeDipendenteAdmin(admin.ModelAdmin):
	list_display = (
		'azienda',
		'mese_riferimento',
		'dipendente',
		'ruolo_riferimento',
		'giorni_presenza',
		'ore_straordinario_diurno',
		'ore_straordinario_notturno',
		'ore_straordinario_festivo',
	)
	list_filter = ('azienda', 'mese_riferimento', 'applica_chiusure_aziendali', 'includi_ratei_nel_netto')
	search_fields = ('azienda__nome', 'dipendente__nome', 'dipendente__cognome', 'ruolo_riferimento')
	readonly_fields = ('data_creazione', 'data_modifica')


# ============================================================
# ADMIN PER PARAMETRI CCNL PARAMETRIZZATI
# ============================================================

@admin.register(CCNL)
class CCNLAdmin(admin.ModelAdmin):
	list_display = ('sigla', 'nome', 'formato_anno_inizio', 'formato_anno_fine', 'orario_standard_settimanale', 'mensilita', 'attivo')
	list_filter = ('sigla', 'attivo', 'anno_inizio_validita')
	search_fields = ('nome', 'sigla', 'descrizione')
	readonly_fields = ('data_creazione', 'data_modifica')

	def formato_anno_inizio(self, obj):
		"""Visualizza anno inizio come numero intero senza decimali"""
		return str(int(obj.anno_inizio_validita)) if obj.anno_inizio_validita else '-'
	formato_anno_inizio.short_description = 'Anno inizio validità'
	formato_anno_inizio.admin_order_field = 'anno_inizio_validita'

	def formato_anno_fine(self, obj):
		"""Visualizza anno fine come numero intero senza decimali"""
		return str(int(obj.anno_fine_validita)) if obj.anno_fine_validita else '-'
	formato_anno_fine.short_description = 'Anno fine validità'
	formato_anno_fine.admin_order_field = 'anno_fine_validita'


@admin.register(ParametroOrario)
class ParametroOrarioAdmin(admin.ModelAdmin):
	list_display = ('ccnl', 'formato_anno', 'tipo_categoria', 'tipo_contratto', 'valore_minimo', 'valore_massimo', 'attivo')
	list_filter = ('ccnl', AnnoListFilter, 'tipo_categoria', 'tipo_contratto', 'attivo')
	search_fields = ('ccnl__sigla', 'descrizione')
	date_hierarchy = 'data_validita_da'
	readonly_fields = ('data_creazione',)

	def formato_anno(self, obj):
		"""Visualizza anno come numero intero senza decimali"""
		return str(int(obj.anno)) if obj.anno else '-'
	formato_anno.short_description = 'Anno'
	formato_anno.admin_order_field = 'anno'


@admin.register(ParametroMaggiorazione)
class ParametroMaggiorazioneAdmin(admin.ModelAdmin):
	list_display = ('ccnl', 'formato_anno', 'tipo_maggiorazione', 'percentuale', 'attivo')
	list_filter = ('ccnl', AnnoListFilter, 'tipo_maggiorazione', 'attivo')
	search_fields = ('ccnl__sigla', 'tipo_maggiorazione')
	date_hierarchy = 'data_validita_da'
	readonly_fields = ('data_creazione',)

	def formato_anno(self, obj):
		"""Visualizza anno come numero intero senza decimali"""
		return str(int(obj.anno)) if obj.anno else '-'
	formato_anno.short_description = 'Anno'
	formato_anno.admin_order_field = 'anno'


@admin.register(ParametroScattiAnnuali)
class ParametroScattiAnnualiAdmin(admin.ModelAdmin):
	list_display = ('ccnl', 'formato_anno', 'livello', 'anni_anzianita', 'importo_scatto', 'attivo')
	list_filter = ('ccnl', AnnoListFilter, 'livello', 'attivo')
	search_fields = ('ccnl__sigla', 'livello')
	date_hierarchy = 'data_validita_da'
	readonly_fields = ('data_creazione',)

	def formato_anno(self, obj):
		"""Visualizza anno come numero intero senza decimali"""
		return str(int(obj.anno)) if obj.anno else '-'
	formato_anno.short_description = 'Anno'
	formato_anno.admin_order_field = 'anno'


@admin.register(ParametroContributi)
class ParametroContributiAdmin(admin.ModelAdmin):
	list_display = ('ccnl', 'formato_anno', 'tipo_contributo', 'categoria', 'aliquota_azienda', 'aliquota_dipendente', 'attivo')
	list_filter = ('ccnl', AnnoListFilter, 'tipo_contributo', 'categoria', 'attivo')
	search_fields = ('ccnl__sigla', 'categoria')
	date_hierarchy = 'data_validita_da'
	readonly_fields = ('data_creazione',)

	def formato_anno(self, obj):
		"""Visualizza anno come numero intero senza decimali"""
		return str(int(obj.anno)) if obj.anno else '-'
	formato_anno.short_description = 'Anno'
	formato_anno.admin_order_field = 'anno'


@admin.register(ParametroRatei)
class ParametroRateiAdmin(admin.ModelAdmin):
	list_display = ('ccnl', 'formato_anno', 'tipo_rateo', 'coefficiente', 'attivo')
	list_filter = ('ccnl', AnnoListFilter, 'tipo_rateo', 'attivo')
	search_fields = ('ccnl__sigla', 'tipo_rateo')
	date_hierarchy = 'data_validita_da'
	readonly_fields = ('data_creazione',)

	def formato_anno(self, obj):
		"""Visualizza anno come numero intero senza decimali"""
		return str(int(obj.anno)) if obj.anno else '-'
	formato_anno.short_description = 'Anno'
	formato_anno.admin_order_field = 'anno'


@admin.register(ValidazioneOrario)
class ValidazioneOrarioAdmin(admin.ModelAdmin):
	list_display = ('ccnl', 'formato_anno', 'tipo_categoria', 'min_ore_settimanali', 'max_ore_settimanali', 'attivo')
	list_filter = ('ccnl', AnnoListFilter, 'tipo_categoria', 'attivo')
	search_fields = ('ccnl__sigla',)
	date_hierarchy = 'data_validita_da'
	readonly_fields = ('data_creazione',)

	def formato_anno(self, obj):
		"""Visualizza anno come numero intero senza decimali"""
		return str(int(obj.anno)) if obj.anno else '-'
	formato_anno.short_description = 'Anno'
	formato_anno.admin_order_field = 'anno'


@admin.register(TipoAssenza)
class TipoAssenzaAdmin(admin.ModelAdmin):
	list_display = ('ccnl', 'formato_anno', 'tipo_assenza', 'carica_inps', 'retribuzione_percentuale', 'giorni_max_anno', 'attivo')
	list_filter = ('ccnl', AnnoListFilter, 'tipo_assenza', 'carica_inps', 'attivo')
	search_fields = ('ccnl__sigla', 'tipo_assenza')
	date_hierarchy = 'data_validita_da'
	readonly_fields = ('data_creazione',)

	def formato_anno(self, obj):
		"""Visualizza anno come numero intero senza decimali"""
		return str(int(obj.anno)) if obj.anno else '-'
	formato_anno.short_description = 'Anno'
	formato_anno.admin_order_field = 'anno'


@admin.register(DecontribuzioneParametro)
class DecontribuzioneParametroAdmin(admin.ModelAdmin):
	list_display = ('ccnl', 'formato_anno', 'tipo_incentivo', 'regione', 'percentuale_sconto', 'priorita', 'attivo')
	list_filter = ('ccnl', AnnoListFilter, 'tipo_incentivo', 'regione', 'attivo')
	search_fields = ('ccnl__sigla', 'tipo_incentivo', 'regione')
	date_hierarchy = 'data_validita_da'
	readonly_fields = ('data_creazione',)

	def formato_anno(self, obj):
		"""Visualizza anno come numero intero senza decimali"""
		return str(int(obj.anno)) if obj.anno else '-'
	formato_anno.short_description = 'Anno'


@admin.register(FringeBenefitSoglia)
class FringeBenefitSogliaAdmin(admin.ModelAdmin):
	list_display = ('ccnl', 'formato_anno', 'tipo_benefit', 'soglia_importo', 'attivo')
	list_filter = ('ccnl', AnnoListFilter, 'tipo_benefit', 'attivo')
	search_fields = ('ccnl__sigla', 'tipo_benefit')
	date_hierarchy = 'data_validita_da'
	readonly_fields = ('data_creazione',)

	def formato_anno(self, obj):
		"""Visualizza anno come numero intero senza decimali"""
		return str(int(obj.anno)) if obj.anno else '-'
	formato_anno.short_description = 'Anno'
	formato_anno.admin_order_field = 'anno'


# =====================================================
# ADMIN MODELLI FISCALI E BONUS
# =====================================================

@admin.register(ScaglioneIRPEF)
class ScaglioneIRPEFAdmin(admin.ModelAdmin):
	list_display = ('formato_anno', 'scaglione_numero', 'reddito_da_formatted', 'reddito_a_formatted', 'aliquota', 'attivo')
	list_filter = (AnnoListFilter, 'attivo')
	search_fields = ('anno',)
	date_hierarchy = 'data_validita_da'
	readonly_fields = ('data_creazione',)
	ordering = ['-anno', 'scaglione_numero']
	
	fieldsets = (
		('Identificazione', {
			'fields': ('anno', 'scaglione_numero', 'attivo')
		}),
		('Limiti Reddito', {
			'fields': ('reddito_da', 'reddito_a')
		}),
		('Aliquota e Detrazioni', {
			'fields': ('aliquota', 'detrazione_base_annua')
		}),
		('Validità', {
			'fields': ('data_validita_da', 'data_validita_a', 'data_creazione'),
			'classes': ('collapse',)
		}),
	)
	
	def formato_anno(self, obj):
		return str(int(obj.anno)) if obj.anno else '-'
	formato_anno.short_description = 'Anno'
	formato_anno.admin_order_field = 'anno'
	
	def reddito_da_formatted(self, obj):
		return f'€ {euro_it_str(obj.reddito_da)}'
	reddito_da_formatted.short_description = 'Da'
	reddito_da_formatted.admin_order_field = 'reddito_da'
	
	def reddito_a_formatted(self, obj):
		return f'€ {euro_it_str(obj.reddito_a)}' if obj.reddito_a else '∞'
	reddito_a_formatted.short_description = 'A'


@admin.register(DetrazioneLavoroDipendente)
class DetrazioneLavoroDipendenteAdmin(admin.ModelAdmin):
	list_display = ('formato_anno', 'reddito_da', 'reddito_a', 'importo_base_annuo', 'coefficiente_variabile_annuo', 'attivo')
	list_filter = (AnnoListFilter, 'attivo')
	search_fields = ('anno',)
	date_hierarchy = 'data_validita_da'
	readonly_fields = ('data_creazione',)
	ordering = ['-anno', 'reddito_da']

	fieldsets = (
		('Identificazione', {
			'fields': ('anno', 'attivo')
		}),
		('Fascia reddito', {
			'fields': ('reddito_da', 'reddito_a')
		}),
		('Formula', {
			'fields': ('importo_base_annuo', 'coefficiente_variabile_annuo', 'reddito_riferimento', 'divisore_fascia')
		}),
		('Validità', {
			'fields': ('data_validita_da', 'data_validita_a', 'data_creazione'),
			'classes': ('collapse',)
		}),
	)

	def formato_anno(self, obj):
		return str(int(obj.anno)) if obj.anno else '-'
	formato_anno.short_description = 'Anno'
	formato_anno.admin_order_field = 'anno'


@admin.register(AddizionaleRegionale)
class AddizionaleRegionaleAdmin(admin.ModelAdmin):
	list_display = ('regione', 'formato_anno', 'aliquota', 'soglia_esenzione', 'attivo')
	list_filter = (AnnoListFilter, 'regione', 'attivo')
	search_fields = ('regione',)
	date_hierarchy = 'data_validita_da'
	readonly_fields = ('data_creazione',)
	ordering = ['-anno', 'regione']
	
	fieldsets = (
		('Identificazione', {
			'fields': ('regione', 'anno', 'attivo')
		}),
		('Parametri Fiscali', {
			'fields': ('aliquota', 'soglia_esenzione')
		}),
		('Validità', {
			'fields': ('data_validita_da', 'data_validita_a', 'data_creazione'),
			'classes': ('collapse',)
		}),
	)
	
	def formato_anno(self, obj):
		return str(int(obj.anno)) if obj.anno else '-'
	formato_anno.short_description = 'Anno'
	formato_anno.admin_order_field = 'anno'


@admin.register(AddizionaleComunale)
class AddizionaleComunaleAdmin(admin.ModelAdmin):
	list_display = ('comune', 'provincia', 'formato_anno', 'aliquota', 'soglia_esenzione', 'attivo')
	list_filter = (AnnoListFilter, 'provincia', 'attivo')
	search_fields = ('comune', 'provincia')
	date_hierarchy = 'data_validita_da'
	readonly_fields = ('data_creazione',)
	ordering = ['-anno', 'provincia', 'comune']
	
	fieldsets = (
		('Identificazione', {
			'fields': ('comune', 'provincia', 'anno', 'attivo')
		}),
		('Parametri Fiscali', {
			'fields': ('aliquota', 'soglia_esenzione')
		}),
		('Validità', {
			'fields': ('data_validita_da', 'data_validita_a', 'data_creazione'),
			'classes': ('collapse',)
		}),
	)
	
	def formato_anno(self, obj):
		return str(int(obj.anno)) if obj.anno else '-'
	formato_anno.short_description = 'Anno'
	formato_anno.admin_order_field = 'anno'


@admin.register(BonusFiscale)
class BonusFiscaleAdmin(admin.ModelAdmin):
	list_display = ('codice', 'nome', 'tipo', 'formato_anno', 'importo_mensile', 'importo_annuale', 'attivo')
	list_filter = (AnnoListFilter, 'tipo', 'attivo', 'contribuisce_imponibile', 'contribuisce_irpef')
	search_fields = ('codice', 'nome', 'descrizione')
	date_hierarchy = 'data_validita_da'
	readonly_fields = ('data_creazione',)
	ordering = ['-anno', 'tipo', 'codice']
	
	fieldsets = (
		('Identificazione', {
			'fields': ('codice', 'nome', 'tipo', 'anno', 'attivo')
		}),
		('Importi', {
			'fields': ('importo_mensile', 'importo_annuale')
		}),
		('Soglie Reddito', {
			'fields': ('soglia_reddito_min', 'soglia_reddito_max')
		}),
		('Calcolo Dinamico', {
			'fields': ('formula_calcolo',),
			'classes': ('collapse',),
			'description': 'Formula Python opzionale per calcolo dinamico. Variabili disponibili: reddito'
		}),
		('Impatto Fiscale', {
			'fields': ('contribuisce_imponibile', 'contribuisce_irpef', 'descrizione')
		}),
		('Validità', {
			'fields': ('data_validita_da', 'data_validita_a', 'data_creazione'),
			'classes': ('collapse',)
		}),
	)
	
	def formato_anno(self, obj):
		return str(int(obj.anno)) if obj.anno else '-'
	formato_anno.short_description = 'Anno'
	formato_anno.admin_order_field = 'anno'


@admin.register(RiepilogoMensileDipendente)
class RiepilogoMensileDipendenteAdmin(admin.ModelAdmin):
	list_display = (
		'dipendente',
		'azienda',
		'formato_anno',
		'mese',
		'imponibile_inps',
		'imponibile_inail',
		'imponibile_irpef',
		'netto_da_pagare_dipendente',
		'versamento_inps_netto',
		'versamento_inail_netto',
		'versamento_erario_netto',
		'accantonamenti_netti_stimati_mese',
	)
	list_filter = (AnnoListFilter, 'mese', 'azienda', 'ratei_concorsi_in_imponibile')
	search_fields = (
		'dipendente__nome',
		'dipendente__cognome',
		'azienda__ragione_sociale',
	)
	readonly_fields = ('creato_il', 'aggiornato_il')
	date_hierarchy = 'data_competenza'
	ordering = ['-anno', '-mese', 'dipendente__cognome']

	def formato_anno(self, obj):
		return str(int(obj.anno)) if obj.anno else '-'
	formato_anno.short_description = 'Anno'
	formato_anno.admin_order_field = 'anno'

	fieldsets = (
		('Identificazione', {
			'fields': ('azienda', 'dipendente', 'anno', 'mese', 'data_competenza')
		}),
		('Imponibili', {
			'fields': ('imponibile_inps', 'imponibile_inail', 'imponibile_irpef')
		}),
		('Imposte e contributi a carico dipendente', {
			'fields': (
				'inps_dipendente',
				'irpef_lorda',
				'detrazioni',
				'irpef_netta',
				'addizionale_regionale',
				'addizionale_comunale',
			)
		}),
		('Oneri azienda', {
			'fields': ('inps_azienda', 'inail')
		}),
		('Netto dipendente e bonus', {
			'fields': (
				'trattamento_integrativo',
				'bonus_l207_2024',
				'netto_da_pagare_dipendente',
			)
		}),
		('Versamenti enti (al netto crediti/sgravi)', {
			'fields': (
				'decontribuzione_inps',
				'crediti_inps',
				'crediti_inail',
				'crediti_erario',
				'versamento_inps_lordo',
				'versamento_inps_netto',
				'versamento_inail_netto',
				'versamento_erario_netto',
			)
		}),
		('Ratei e accantonamenti', {
			'fields': (
				'rateo_tredicesima',
				'rateo_quattordicesima',
				'tfr_mensile',
				'ratei_concorsi_in_imponibile',
				'imposte_aggiuntive_ratei_dipendente',
				'imposte_aggiuntive_ratei_azienda',
				'imposte_aggiuntive_ratei_erario',
				'accantonamenti_lordi_mese',
				'accantonamenti_netti_stimati_mese',
			)
		}),
		('Riepilogo finale', {
			'fields': ('esborso_totale_azienda_mese', 'note', 'creato_il', 'aggiornato_il')
		}),
	)


# ─────────────────────────────────────────────────────────────────────────────
# MOTORE PAGA MENSILE — Sandbox di test (voce di menu admin)
# ─────────────────────────────────────────────────────────────────────────────
@admin.register(TestMotorePaga)
class TestMotorePagaAdmin(admin.ModelAdmin):
	"""Admin per il motore di calcolo paga mensile — area test e verifica."""

	list_display = (
		'nome_test',
		'mese_riferimento',
		'parametro_ccnl',
		'tipo_contratto',
		'stato',
		'r_lordo_mensile',
		'r_netto_totale',
		'r_costo_totale_azienda',
		'data_modifica',
	)
	list_filter = ('stato', 'tipo_contratto', 'divisore', 'giorni_lavorativi_settimana')
	search_fields = ('nome_test', 'mese_riferimento')
	readonly_fields = (
		'stato', 'data_creazione', 'data_modifica',
		# Risultati lordo e giorni
		'r_lordo_pieno', 'r_lordo_mensile', 'r_giorni_lavorati', 'r_giorni_mese',
		'r_paga_giornaliera', 'r_paga_oraria',
		# Voci retributive calcolate
		'r_paga_base', 'r_contingenza', 'r_edr', 'r_indennita',
		'r_superminimo', 'r_indennita_turno', 'r_altre_voci',
		# INPS
		'r_inps_dip_perc', 'r_inps_az_perc',
		'r_inps_dipendente', 'r_inps_azienda', 'r_inail_azienda',
		# IRPEF
		'r_imponibile_mensile', 'r_imponibile_annuo',
		'r_irpef_lorda', 'r_detrazioni', 'r_irpef_netta',
		# Netto
		'r_netto_base', 'r_trattamento_integrativo', 'r_bonus_l207', 'r_netto_totale',
		# Addizionali
		'r_addiz_reg_mensile', 'r_addiz_com_mensile', 'r_addiz_totale_annuo',
		# Ratei
		'r_tfr_mensile', 'r_rateo_13_mensile', 'r_rateo_14_mensile', 'r_rateo_ferie_mensile',
		# F24 e costo
		'r_f24_inps', 'r_f24_erario', 'r_costo_totale_azienda', 'r_costo_annuo_stimato',
		# JSON
		'risultato_json',
	)

	fieldsets = (
		('Scenario', {
			'fields': ('nome_test', 'stato', 'note', 'data_creazione', 'data_modifica'),
		}),
		('Parametri — Periodo', {
			'description': 'Indica il mese di riferimento e, se il rapporto inizia a metà mese, la data di inizio.',
			'fields': ('mese_riferimento', 'data_inizio_rapporto'),
		}),
		('Parametri — CCNL e Contratto', {
			'description': 'Seleziona il livello retributivo CCNL e il tipo di contratto (full-time / part-time).',
			'fields': ('parametro_ccnl', 'tipo_contratto'),
		}),
		('Parametri — Divisore e Giorni', {
			'description': (
				'Il divisore convenzionale (26 = CCNL FIPE) divide il lordo mensile per ottenere '
				'la retribuzione giornaliera. I giorni di chiusura aziendale vengono sottratti.'
			),
			'fields': ('divisore', 'giorni_lavorativi_settimana', 'giorni_chiusura_mese'),
		}),
		('Parametri — Voci Retributive Aggiuntive', {
			'description': (
				'Queste voci si sommano alle voci base CCNL (paga base, contingenza, EDR, indennità). '
				'Sono tutte imponibili INPS e IRPEF.'
			),
			'fields': ('superminimo', 'indennita_turno', 'altre_voci_json'),
		}),
		('Risultati — Lordo e Voci', {
			'classes': ('collapse',),
			'fields': (
				'r_lordo_pieno', 'r_lordo_mensile', 'r_giorni_lavorati', 'r_giorni_mese',
				'r_paga_giornaliera', 'r_paga_oraria',
				'r_paga_base', 'r_contingenza', 'r_edr', 'r_indennita',
				'r_superminimo', 'r_indennita_turno', 'r_altre_voci',
			),
		}),
		('Risultati — Imponibile INPS', {
			'classes': ('collapse',),
			'description': 'Aliquote lette da ParametroContributi CCNL FIPE per l\'anno di riferimento.',
			'fields': (
				'r_inps_dip_perc', 'r_inps_az_perc',
				'r_inps_dipendente', 'r_inps_azienda', 'r_inail_azienda',
			),
		}),
		('Risultati — Imponibile IRPEF e Detrazioni', {
			'classes': ('collapse',),
			'description': 'IRPEF scaglioni 2024-2026 (art. 11 TUIR, L. 213/2023). Detrazioni art. 13 TUIR.',
			'fields': (
				'r_imponibile_mensile', 'r_imponibile_annuo',
				'r_irpef_lorda', 'r_detrazioni', 'r_irpef_netta',
			),
		}),
		('Risultati — Netto Mensile', {
			'classes': ('collapse',),
			'description': (
				'Netto base = lordo − INPS − IRPEF. '
				'TI e Bonus L207 sono crediti d\'imposta anticipati dall\'azienda. '
				'Netto totale = netto base + TI + Bonus L207.'
			),
			'fields': (
				'r_netto_base', 'r_trattamento_integrativo', 'r_bonus_l207', 'r_netto_totale',
				'r_addiz_reg_mensile', 'r_addiz_com_mensile', 'r_addiz_totale_annuo',
			),
		}),
		('Risultati — Ratei e Accantonamenti', {
			'classes': ('collapse',),
			'description': 'Coefficienti letti da ParametroRatei CCNL FIPE per l\'anno di riferimento.',
			'fields': (
				'r_tfr_mensile', 'r_rateo_13_mensile', 'r_rateo_14_mensile', 'r_rateo_ferie_mensile',
			),
		}),
		('Risultati — F24 e Costo Azienda', {
			'classes': ('collapse',),
			'description': (
				'F24 INPS = INPS dipendente + INPS azienda. '
				'F24 Erario = IRPEF netta − TI − Bonus L207 (recuperati dall\'azienda). '
				'Costo azienda = lordo + INPS az + INAIL + TFR + rateo 13ª + rateo 14ª.'
			),
			'fields': (
				'r_f24_inps', 'r_f24_erario', 'r_costo_totale_azienda', 'r_costo_annuo_stimato',
			),
		}),
		('Risultato completo (JSON — debug)', {
			'classes': ('collapse',),
			'fields': ('risultato_json',),
		}),
	)

	# ── URL personalizzati ────────────────────────────────────────────────────
	def get_urls(self):
		urls = super().get_urls()
		custom = [
			path(
				'simulatore/',
				self.admin_site.admin_view(self._simulatore_view),
				name='rapporto_di_lavoro_testmotorepaga_simulatore',
			),
			path(
				'<int:pk>/calcola/',
				self.admin_site.admin_view(self._calcola_view),
				name='rapporto_di_lavoro_testmotorepaga_calcola',
			),
		]
		return custom + urls

	def changelist_view(self, request, extra_context=None):
		extra_context = extra_context or {}
		extra_context['simulatore_url'] = reverse('admin:rapporto_di_lavoro_testmotorepaga_simulatore')
		return super().changelist_view(request, extra_context)

	def _simulatore_view(self, request):
		"""Simulatore completo paga mensile — calcolo inline senza salvataggio su DB."""
		from django.shortcuts import render
		from .models import ParametroCCNLTurismo, TipoContratto, CCNL, ParametroContributi, ParametroRatei
		from .utils_calcoli import (
			calcola_irpef_lorda, calcola_detrazioni,
			calcola_trattamento_integrativo, calcola_bonus_l207_2024,
			calcola_addizionale_regionale_sicilia, calcola_addizionale_comunale_stima,
		)
		from .utils_calendario import get_giorni_lavorativi_mese, build_griglia_mese
		from anagrafiche.models import Azienda
		from decimal import Decimal
		import calendar
		from datetime import date

		Q2 = Decimal('0.01')
		Q6 = Decimal('0.000001')

		parametri_ccnl = ParametroCCNLTurismo.objects.filter(attivo=True).order_by('ccnl', 'livello_ordinamento')
		tipi_contratto = TipoContratto.objects.filter(attivo=True).order_by('nome')
		aziende = Azienda.objects.all().order_by('nome')

		risultato = None
		errore = None
		form_data = {}
		cal_data = None
		cal_griglia = None

		if request.method == 'POST':
			form_data = request.POST
			try:
				# ── Parametri base ────────────────────────────────────────────
				cp = ParametroCCNLTurismo.objects.get(pk=request.POST['parametro_ccnl'])
				tc = TipoContratto.objects.get(pk=request.POST['tipo_contratto'])
				coeff = Decimal(str(tc.coefficiente_ore or 1))

				mese_str = request.POST.get('mese_riferimento', '')
				try:
					anno, mese = int(mese_str[:4]), int(mese_str[5:7])
				except Exception:
					from django.utils import timezone as _tz
					_d = _tz.localdate()
					anno, mese = _d.year, _d.month
				giorni_nel_mese = calendar.monthrange(anno, mese)[1]

				# ── Calendario lavorativo aziendale ───────────────────────────
				azienda_pk = request.POST.get('azienda', '').strip()
				azienda = None
				if azienda_pk:
					try:
						azienda = Azienda.objects.get(pk=azienda_pk)
					except Azienda.DoesNotExist:
						pass

				cal_data = get_giorni_lavorativi_mese(azienda, anno, mese)
				cal_griglia = build_griglia_mese(anno, mese, azienda)

				# Set dei giorni non lavorativi dal calendario
				_non_lav = (
					set(cal_data['dates_chiusure_sett']) |
					set(cal_data['dates_festivita']) |
					set(cal_data['dates_chiusure_extra'])
				)

				# ── Pro-rata inizio / fine rapporto con calendario ─────────────
				def _to_date(s):
					s = (s or '').strip()
					return date.fromisoformat(s) if s else None

				data_inizio = _to_date(request.POST.get('data_inizio_rapporto'))
				data_fine   = _to_date(request.POST.get('data_fine_rapporto'))

				if data_inizio and data_inizio.year == anno and data_inizio.month == mese:
					# Regola CCNL FIPE: gg calendari dal giorno di inizio a fine mese
					gg_cal = (date(anno, mese, giorni_nel_mese) - data_inizio).days + 1
					if gg_cal >= 15:
						frazione = Decimal('1')
						gg_lav = cal_data['giorni_lavorativi']
					else:
						# Pro-rata: giorni lavorativi dal giorno di inizio a fine mese
						gg_lav = sum(
							1 for g in range(data_inizio.day, giorni_nel_mese + 1)
							if date(anno, mese, g) not in _non_lav
						)
						tot = cal_data['giorni_lavorativi'] or 1
						frazione = (Decimal(str(gg_lav)) / Decimal(str(tot))).quantize(Q6)
				elif data_fine and data_fine.year == anno and data_fine.month == mese:
					gg_cal = data_fine.day
					if gg_cal >= 15:
						frazione = Decimal('1')
						gg_lav = cal_data['giorni_lavorativi']
					else:
						gg_lav = sum(
							1 for g in range(1, data_fine.day + 1)
							if date(anno, mese, g) not in _non_lav
						)
						tot = cal_data['giorni_lavorativi'] or 1
						frazione = (Decimal(str(gg_lav)) / Decimal(str(tot))).quantize(Q6)
				else:
					gg_lav = cal_data['giorni_lavorativi']
					frazione = Decimal('1')

				# ── Ore e divisore ────────────────────────────────────────────
				divisore      = int(request.POST.get('divisore', 26) or 26)
				ore_mensili   = (cp.ore_mensili   * coeff).quantize(Q2)
				ore_giorn     = (cp.ore_giornaliere * coeff).quantize(Q2)

				# ── Voci base CCNL (pro-ratate) ───────────────────────────────
				def _v(val): return (val * coeff * frazione).quantize(Q2)
				paga_base   = _v(cp.paga_base_mensile)
				contingenza = _v(cp.contingenza_mensile)
				_edr_src = Decimal(str(cp.edr_mensile or 0))
				if 'FIPE' in (cp.ccnl or '').upper():
					_edr_src = Decimal('0')
				edr         = _v(_edr_src)
				indennita   = _v(cp.indennita_mensile)
				superminimo     = Decimal(request.POST.get('superminimo',     '0') or '0').quantize(Q2)
				indennita_turno = Decimal(request.POST.get('indennita_turno', '0') or '0').quantize(Q2)
				lordo_base = (paga_base + contingenza + edr + indennita + superminimo + indennita_turno).quantize(Q2)

				# Paga oraria e giornaliera calcolate sul lordo pieno (senza pro-rata)
				lordo_pieno = ((cp.paga_base_mensile + cp.contingenza_mensile + _edr_src + cp.indennita_mensile) * coeff).quantize(Q2)
				paga_oraria      = (lordo_pieno / ore_mensili).quantize(Decimal('0.0001'))  if ore_mensili else Decimal('0')
				paga_giornaliera = (lordo_pieno / Decimal(str(divisore))).quantize(Decimal('0.0001'))

				# ── Straordinari ──────────────────────────────────────────────
				magg_diur  = Decimal(str(cp.straordinario_diurno_maggiorazione   or 15)) / 100
				magg_nott  = Decimal(str(cp.straordinario_notturno_maggiorazione or 30)) / 100
				magg_fest  = Decimal(str(cp.straordinario_festivo_maggiorazione  or 30)) / 100
				magg_nf    = magg_nott + magg_fest   # notturno-festivo cumulativo

				def _ore(key): return Decimal(request.POST.get(key, '0') or '0').quantize(Q2)
				ore_sd = _ore('ore_straord_diurno')
				ore_sn = _ore('ore_straord_notturno')
				ore_sf = _ore('ore_straord_festivo')
				ore_snf= _ore('ore_straord_nott_fest')

				imp_sd  = (ore_sd  * paga_oraria * (1 + magg_diur)).quantize(Q2)
				imp_sn  = (ore_sn  * paga_oraria * (1 + magg_nott)).quantize(Q2)
				imp_sf  = (ore_sf  * paga_oraria * (1 + magg_fest)).quantize(Q2)
				imp_snf = (ore_snf * paga_oraria * (1 + magg_nf  )).quantize(Q2)
				tot_straord = (imp_sd + imp_sn + imp_sf + imp_snf).quantize(Q2)

				# Maggiorazioni isolate (solo la quota di maggiorazione, non la quota ordinaria)
				magg_imp_sd  = (ore_sd  * paga_oraria * magg_diur).quantize(Q2)
				magg_imp_sn  = (ore_sn  * paga_oraria * magg_nott).quantize(Q2)
				magg_imp_sf  = (ore_sf  * paga_oraria * magg_fest).quantize(Q2)
				magg_imp_snf = (ore_snf * paga_oraria * magg_nf  ).quantize(Q2)

				# ── Assenze e utilizzo ferie/permessi ────────────────────────
				def _gg(key): return Decimal(request.POST.get(key, '0') or '0').quantize(Q2)
				gg_assenza = _gg('giorni_assenza_ingiust')
				gg_ferie   = _gg('giorni_ferie_godute')
				ore_perm   = _gg('ore_permessi_goduti')

				decurt_assenze  = (gg_assenza * paga_giornaliera).quantize(Q2)
				# ferie e permessi goduti: già compresi nel lordo mensile, non decurtano
				# li mostriamo informativamente

				# ── Lordo competenze mensili (+ quote 13ª/14ª nella base INPS, come motore canonico)
				lordo_mensile = (lordo_base + tot_straord - decurt_assenze).quantize(Q2)

				_ccnl_obj = CCNL.objects.filter(sigla__icontains='FIPE').first()
				c_tfr  = Decimal('0.0691')
				c_13   = (Decimal('1') / Decimal('12')).quantize(Q6)
				c_14   = Decimal('0')
				c_fer  = Decimal('0.1154')
				if _ccnl_obj:
					for tipo_r, transform, attr in [
						('tfr',             lambda r: r.coefficiente / 100, 'c_tfr'),
						('tredicesima',     lambda r: r.coefficiente / 12,  'c_13'),
						('quattordicesima', lambda r: r.coefficiente / 12,  'c_14'),
						('ferie',           lambda r: r.coefficiente / 100, 'c_fer'),
					]:
						pr = ParametroRatei.objects.filter(ccnl=_ccnl_obj, anno=anno, tipo_rateo=tipo_r, attivo=True).first()
						if pr:
							val = transform(pr).quantize(Q6)
							if   attr == 'c_tfr': c_tfr = val
							elif attr == 'c_13':  c_13  = val
							elif attr == 'c_14':  c_14  = val
							elif attr == 'c_fer': c_fer = val

				rat13_m    = (lordo_base * c_13).quantize(Q2)
				rat14_m    = (lordo_base * c_14).quantize(Q2)
				lordo_imponibile_inps_m = (lordo_mensile + rat13_m + rat14_m).quantize(Q2)

				# ── Aliquote contributive ─────────────────────────────────────
				inps_dip_p = Decimal('0.0936')
				inps_az_p  = Decimal('0.2931')
				inail_p    = Decimal('0.0074')

				if _ccnl_obj:
					pc = ParametroContributi.objects.filter(ccnl=_ccnl_obj, anno=anno, tipo_contributo='inps', attivo=True).first()
					if pc:
						inps_dip_p = (pc.aliquota_dipendente / 100).quantize(Decimal('0.0001'))
						inps_az_p  = (pc.aliquota_azienda    / 100).quantize(Decimal('0.0001'))
					pc2 = ParametroContributi.objects.filter(ccnl=_ccnl_obj, anno=anno, tipo_contributo='inail', attivo=True).first()
					if pc2:
						inail_p = (pc2.aliquota_azienda / 100).quantize(Decimal('0.0001'))

				inps_dip  = (lordo_imponibile_inps_m * inps_dip_p).quantize(Q2)
				inps_az   = (lordo_imponibile_inps_m * inps_az_p ).quantize(Q2)
				inail_az  = (lordo_imponibile_inps_m * inail_p   ).quantize(Q2)
				tot_contrib_dip = inps_dip
				tot_contrib_az  = (inps_az + inail_az).quantize(Q2)

				# ── IRPEF ─────────────────────────────────────────────────────
				imponibile_m    = (lordo_imponibile_inps_m - inps_dip).quantize(Q2)
				imponibile_ann  = float(imponibile_m) * 12
				irpef_lorda_m   = Decimal(str(calcola_irpef_lorda(float(imponibile_m), anno=anno))).quantize(Q2)
				detrazioni_m    = Decimal(str(calcola_detrazioni(float(imponibile_m), anno=anno))).quantize(Q2)
				irpef_netta_m   = max(irpef_lorda_m - detrazioni_m, Decimal('0')).quantize(Q2)
				netto_base      = (lordo_imponibile_inps_m - inps_dip - irpef_netta_m).quantize(Q2)

				# ── Bonus fiscali (crediti d'imposta anticipati dall'azienda) ─
				ti   = Decimal(str(calcola_trattamento_integrativo(imponibile_ann, anno))).quantize(Q2)
				l207 = Decimal(str(calcola_bonus_l207_2024(imponibile_ann, anno))).quantize(Q2)
				crediti_imposta = (ti + l207).quantize(Q2)
				netto_totale    = (netto_base + ti + l207).quantize(Q2)

				# ── Addizionali (stima — versate anno successivo) ─────────────
				add_reg_ann = Decimal(str(calcola_addizionale_regionale_sicilia(imponibile_ann, anno=anno))).quantize(Q2)
				add_com_ann = Decimal(str(calcola_addizionale_comunale_stima(imponibile_ann, anno=anno))).quantize(Q2)
				add_reg_m   = (add_reg_ann / 12).quantize(Q2)
				add_com_m   = (add_com_ann / 12).quantize(Q2)
				add_tot_ann = (add_reg_ann + add_com_ann).quantize(Q2)

				tfr_m      = (lordo_mensile * c_tfr).quantize(Q2)
				rat_fer_m  = (lordo_mensile * c_fer).quantize(Q2)
				tot_ratei_lordi = (tfr_m + rat13_m + rat14_m + rat_fer_m).quantize(Q2)

				# Ratei netti (tassazione proporzionale, senza TI/L207 che non si applica a 13ª/TFR)
				ratio = (netto_base / lordo_imponibile_inps_m).quantize(Q6) if lordo_imponibile_inps_m else Decimal('0')
				tfr_n     = (tfr_m     * ratio).quantize(Q2)
				rat13_n   = (rat13_m   * ratio).quantize(Q2)
				rat14_n   = (rat14_m   * ratio).quantize(Q2)
				rat_fer_n = (rat_fer_m * ratio).quantize(Q2)
				tot_ratei_netti = (tfr_n + rat13_n + rat14_n + rat_fer_n).quantize(Q2)

				# Ratei su base oraria e giornaliera
				giorni_m_teorici = (ore_mensili / ore_giorn).quantize(Q2) if ore_giorn else Decimal('26')
				tfr_ora  = (tfr_m  / ore_mensili).quantize(Decimal('0.0001'))   if ore_mensili   else Decimal('0')
				tfr_gg   = (tfr_m  / giorni_m_teorici).quantize(Q2) if giorni_m_teorici else Decimal('0')
				rat13_ora = (rat13_m / ore_mensili).quantize(Decimal('0.0001'))  if ore_mensili   else Decimal('0')
				rat13_gg  = (rat13_m / giorni_m_teorici).quantize(Q2) if giorni_m_teorici else Decimal('0')

				# ── Riepilogo busta paga ──────────────────────────────────────
				lordo_con_ratei = (lordo_mensile + tot_ratei_lordi).quantize(Q2)
				netto_con_ratei = (netto_totale  + tot_ratei_netti).quantize(Q2)

				# ── F24 ───────────────────────────────────────────────────────
				f24_inps        = (inps_dip + inps_az).quantize(Q2)
				f24_erario_lord = irpef_netta_m
				f24_erario      = max(irpef_netta_m - crediti_imposta, Decimal('0')).quantize(Q2)
				f24_totale      = (f24_inps + f24_erario).quantize(Q2)

				# ── Costo azienda ─────────────────────────────────────────────
				costo_corrente  = (lordo_imponibile_inps_m + inps_az + inail_az).quantize(Q2)
				costo_differito = tot_ratei_lordi  # TFR + 13ª + 14ª + ferie accantonati
				costo_mensile   = (costo_corrente + costo_differito).quantize(Q2)
				costo_annuo     = (costo_mensile * 12).quantize(Q2)

				risultato = {
					# Periodo
					'anno': anno, 'mese': mese, 'mese_str': mese_str,
					'giorni_nel_mese': giorni_nel_mese, 'giorni_lavorati': gg_lav,
					'frazione': frazione, 'prorata': frazione < Decimal('1'),
					# Calendario lavorativo
					'azienda_nome': azienda.nome if azienda else '(nessuna — festività nazionali + dom.)',
					'cal_giorni_lavorativi':    cal_data['giorni_lavorativi'],
					'cal_chiusure_settimanali': cal_data['chiusure_settimanali'],
					'cal_festivi':              cal_data['festivi'],
					'cal_chiusure_extra':       cal_data['chiusure_extra'],
					'cal_giorni_conv_26':       cal_data['giorni_conv_26'],
					'cal_festivita':            cal_data['dates_festivita'],
					'cal_chiusure_sett':        cal_data['dates_chiusure_sett'],
					'cal_chiusure_extra_dates': cal_data['dates_chiusure_extra'],
					'cal_griglia':              cal_griglia,
					# Anagrafica
					'ccnl_nome': cp.ccnl, 'ccnl_livello': cp.livello, 'ccnl_qualifica': cp.qualifica,
					'tipo_contratto': tc.nome, 'coeff_ore': coeff,
					# Ore
					'ore_mensili': ore_mensili, 'ore_giornaliere': ore_giorn,
					'divisore': divisore,
					'paga_oraria': paga_oraria, 'paga_giornaliera': paga_giornaliera,
					# Voci base (tabella con flag imponibilità)
					'voci': (
						[
							{'nome': 'Paga base CCNL',     'importo': paga_base,       'inps': True,  'irpef': True,  'note': 'Art. 74 CCNL FIPE'},
							{'nome': 'Contingenza',         'importo': contingenza,     'inps': True,  'irpef': True,  'note': 'Indennità di contingenza'},
						]
						+ ([{'nome': 'EDR', 'importo': edr, 'inps': True, 'irpef': True, 'note': 'Elemento Distorsivo Retrib.'}] if edr > 0 else [])
						+ [
						{'nome': 'Indennità CCNL',      'importo': indennita,       'inps': True,  'irpef': True,  'note': 'Se prevista dal livello'},
						{'nome': 'Superminimo',         'importo': superminimo,     'inps': True,  'irpef': True,  'note': 'Individuale/aziendale'},
						{'nome': 'Indennità turno',     'importo': indennita_turno, 'inps': True,  'irpef': True,  'note': 'Turni notturni/speciali'},
						{'nome': 'Straord. diurno',     'importo': imp_sd,  'ore': ore_sd,  'magg': int(magg_diur*100),  'inps': True, 'irpef': True, 'note': f'+{int(magg_diur*100)}%'},
						{'nome': 'Straord. notturno',   'importo': imp_sn,  'ore': ore_sn,  'magg': int(magg_nott*100),  'inps': True, 'irpef': True, 'note': f'+{int(magg_nott*100)}%'},
						{'nome': 'Straord. festivo',    'importo': imp_sf,  'ore': ore_sf,  'magg': int(magg_fest*100),  'inps': True, 'irpef': True, 'note': f'+{int(magg_fest*100)}%'},
						{'nome': 'Straord. nott-fest',  'importo': imp_snf, 'ore': ore_snf, 'magg': int(magg_nf*100),    'inps': True, 'irpef': True, 'note': f'+{int(magg_nf*100)}%'},
						{'nome': 'Assenze ingiustif.',  'importo': -decurt_assenze, 'gg': gg_assenza, 'inps': True, 'irpef': True, 'note': 'Decurtazione lordo', 'negativo': True},
					]),
					'lordo_base': lordo_base,
					'tot_straord': tot_straord,
					'decurt_assenze': decurt_assenze,
					'lordo_mensile': lordo_mensile,
					'lordo_imponibile_inps_m': lordo_imponibile_inps_m,
					# Info assenze/ferie (informative)
					'gg_ferie_godute': gg_ferie,
					'ore_perm_goduti': ore_perm,
					# INPS
					'inps_dip_perc': (inps_dip_p * 100).quantize(Q2),
					'inps_az_perc':  (inps_az_p  * 100).quantize(Q2),
					'inail_perc':    (inail_p    * 100).quantize(Q2),
					'inps_dip':  inps_dip,
					'inps_az':   inps_az,
					'inail_az':  inail_az,
					'tot_contrib_dip': tot_contrib_dip,
					'tot_contrib_az':  tot_contrib_az,
					# IRPEF
					'imponibile_m':   imponibile_m,
					'imponibile_ann': Decimal(str(round(imponibile_ann, 2))),
					'irpef_lorda':    irpef_lorda_m,
					'detrazioni':     detrazioni_m,
					'irpef_netta':    irpef_netta_m,
					# Addizionali
					'add_reg_m': add_reg_m, 'add_com_m': add_com_m, 'add_tot_ann': add_tot_ann,
					# Bonus
					'ti': ti, 'l207': l207, 'crediti_imposta': crediti_imposta,
					# Netto
					'netto_base': netto_base, 'netto_totale': netto_totale,
					# Ratei lordi
					'c_tfr': c_tfr, 'c_13': c_13, 'c_14': c_14, 'c_fer': c_fer,
					'tfr_m': tfr_m, 'rat13_m': rat13_m, 'rat14_m': rat14_m, 'rat_fer_m': rat_fer_m,
					'tot_ratei_lordi': tot_ratei_lordi,
					'tfr_ora': tfr_ora, 'tfr_gg': tfr_gg,
					'rat13_ora': rat13_ora, 'rat13_gg': rat13_gg,
					# Ratei netti
					'ratio': ratio,
					'tfr_n': tfr_n, 'rat13_n': rat13_n, 'rat14_n': rat14_n, 'rat_fer_n': rat_fer_n,
					'tot_ratei_netti': tot_ratei_netti,
					# Riepilogo busta
					'lordo_con_ratei': lordo_con_ratei,
					'netto_con_ratei': netto_con_ratei,
					# F24
					'f24_inps': f24_inps,
					'f24_erario_lord': f24_erario_lord,
					'crediti_imposta': crediti_imposta,
					'f24_erario': f24_erario,
					'f24_totale': f24_totale,
					# Costo azienda
					'costo_corrente': costo_corrente,
					'costo_differito': costo_differito,
					'costo_mensile': costo_mensile,
					'costo_annuo': costo_annuo,
					# Maggiorazioni straord. percentuali
					'magg_diur_pct': int(magg_diur*100), 'magg_nott_pct': int(magg_nott*100),
					'magg_fest_pct': int(magg_fest*100), 'magg_nf_pct': int(magg_nf*100),
				}

			except Exception as exc:
				import traceback
				errore = f"{exc}<br><pre style='font-size:.75rem'>{traceback.format_exc()}</pre>"

		context = {
			**self.admin_site.each_context(request),
			'title': 'Simulatore Motore Paga',
			'parametri_ccnl': parametri_ccnl,
			'tipi_contratto': tipi_contratto,
			'aziende': aziende,
			'risultato': risultato,
			'errore': errore,
			'form_data': form_data,
			'cal_data': cal_data,
			'cal_griglia': cal_griglia,
			'opts': self.model._meta,
		}
		return render(request, 'admin/rapporto_di_lavoro/testmotorepaga/simulatore.html', context)

	def _calcola_view(self, request, pk):
		"""Esegue il calcolo del motore paga e salva i risultati nel record."""
		from django.shortcuts import get_object_or_404
		test = get_object_or_404(TestMotorePaga, pk=pk)
		try:
			from datetime import date
			from decimal import Decimal
			from django.utils import timezone as _tz
			from .utils_motore_paga import calcola_busta_paga_mese

			# Parsing periodo YYYY-MM
			try:
				anno, mese = int(test.mese_riferimento[:4]), int(test.mese_riferimento[5:7])
			except (ValueError, TypeError, AttributeError):
				oggi = _tz.localdate()
				anno, mese = oggi.year, oggi.month

			# Somma voci extra (compatibilità con sandbox legacy)
			altre_voci = Decimal('0.00')
			if test.altre_voci_json and isinstance(test.altre_voci_json, dict):
				for v in test.altre_voci_json.values():
					try:
						altre_voci += Decimal(str(v))
					except Exception:
						pass
			altre_voci = altre_voci.quantize(Decimal('0.01'))

			ccnl_obj = CCNL.objects.filter(sigla__icontains='FIPE').first()
			r = calcola_busta_paga_mese(
				parametro_ccnl=test.parametro_ccnl,
				tipo_contratto=test.tipo_contratto,
				anno=anno,
				mese=mese,
				azienda=None,
				data_inizio_rapporto=test.data_inizio_rapporto,
				data_fine_rapporto=None,
				divisore_str=str(test.divisore or 26),
				superminimo=Decimal(str(test.superminimo or 0)),
				indennita_turno=Decimal(str(test.indennita_turno or 0)),
				indennita_extra=altre_voci,
				ccnl_obj=ccnl_obj,
				rateo_13_mensile_in_imponibile=False,
				rateo_14_mensile_in_imponibile=False,
			)

			# Mappatura chiavi legacy (compatibilità con campi TestMotorePaga)
			frazione = Decimal(str(r.get('frazione', '1') or '1'))
			r['lordo_pieno'] = (Decimal(str(r['lordo_base'])) / frazione).quantize(Decimal('0.01')) if frazione else Decimal(str(r['lordo_base']))
			r['altre_voci'] = altre_voci
			r['inps_dipendente'] = r['inps_dip']
			r['inps_azienda'] = r['inps_az']
			r['inail_azienda'] = r['inail_az']
			r['imponibile_mensile'] = r['imponibile_m']
			r['imponibile_annuo'] = r['imponibile_ann']
			r['trattamento_integrativo'] = r['ti']
			r['bonus_l207'] = r['l207']
			r['addiz_reg_mensile'] = r['add_reg_m']
			r['addiz_com_mensile'] = r['add_com_m']
			r['addiz_totale_annuo'] = ((Decimal(str(r['add_reg_m'])) + Decimal(str(r['add_com_m']))) * Decimal('12')).quantize(Decimal('0.01'))
			r['tfr_mensile'] = r['tfr_m']
			r['rateo_13_mensile'] = r['rat13_m']
			r['rateo_14_mensile'] = r['rat14_m']
			r['rateo_ferie_mensile'] = r['rat_fer_m']
			r['costo_totale_azienda'] = r['costo_mensile']
			r['costo_annuo_stimato'] = r['costo_annuo']

			# Salva tutti i campi risultato
			test.r_lordo_pieno              = r['lordo_pieno']
			test.r_lordo_mensile            = r['lordo_mensile']
			test.r_giorni_lavorati          = r['giorni_lavorati']
			test.r_giorni_mese              = r['giorni_nel_mese']
			test.r_paga_giornaliera         = r['paga_giornaliera']
			test.r_paga_oraria              = r['paga_oraria']
			test.r_paga_base                = r['paga_base']
			test.r_contingenza              = r['contingenza']
			test.r_edr                      = r['edr']
			test.r_indennita                = r['indennita']
			test.r_superminimo              = r['superminimo']
			test.r_indennita_turno          = r['indennita_turno']
			test.r_altre_voci               = r['altre_voci']
			test.r_inps_dip_perc            = r['inps_dip_perc']
			test.r_inps_az_perc             = r['inps_az_perc']
			test.r_inps_dipendente          = r['inps_dipendente']
			test.r_inps_azienda             = r['inps_azienda']
			test.r_inail_azienda            = r['inail_azienda']
			test.r_imponibile_mensile       = r['imponibile_mensile']
			test.r_imponibile_annuo         = r['imponibile_annuo']
			test.r_irpef_lorda              = r['irpef_lorda']
			test.r_detrazioni               = r['detrazioni']
			test.r_irpef_netta              = r['irpef_netta']
			test.r_netto_base               = r['netto_base']
			test.r_trattamento_integrativo  = r['trattamento_integrativo']
			test.r_bonus_l207               = r['bonus_l207']
			test.r_netto_totale             = r['netto_totale']
			test.r_addiz_reg_mensile        = r['addiz_reg_mensile']
			test.r_addiz_com_mensile        = r['addiz_com_mensile']
			test.r_addiz_totale_annuo       = r['addiz_totale_annuo']
			test.r_tfr_mensile              = r['tfr_mensile']
			test.r_rateo_13_mensile         = r['rateo_13_mensile']
			test.r_rateo_14_mensile         = r['rateo_14_mensile']
			test.r_rateo_ferie_mensile      = r['rateo_ferie_mensile']
			test.r_f24_inps                 = r['f24_inps']
			test.r_f24_erario               = r['f24_erario']
			test.r_costo_totale_azienda     = r['costo_totale_azienda']
			test.r_costo_annuo_stimato      = r['costo_annuo_stimato']
			# JSON serializzabile
			test.risultato_json = {
				k: str(v) for k, v in r.items()
			}
			test.stato = 'calcolato'
			test.save()
			messages.success(
				request,
				f'✓ Calcolo completato — Netto: € {r["netto_totale"]} | '
				f'Lordo: € {r["lordo_mensile"]} | '
				f'Costo az.: € {r["costo_totale_azienda"]}'
			)
		except Exception as exc:
			messages.error(request, f'Errore nel calcolo: {exc}')

		return HttpResponseRedirect(
			reverse('admin:rapporto_di_lavoro_testmotorepaga_change', args=[pk])
		)

	def change_view(self, request, object_id, form_url='', extra_context=None):
		extra_context = extra_context or {}
		if object_id:
			calcola_url = reverse(
				'admin:rapporto_di_lavoro_testmotorepaga_calcola', args=[object_id]
			)
			extra_context['calcola_url'] = calcola_url
		return super().change_view(request, object_id, form_url, extra_context)


@admin.register(SimulazionePagaSalvata)
class SimulazionePagaSalvataAdmin(admin.ModelAdmin):
	list_display = ('nome', 'utente', 'anno', 'mese_nome', 'ccnl_livello', 'ccnl_qualifica', 'tipo_contratto_nome', 'lordo_mensile', 'netto_totale', 'costo_mensile', 'data_modifica')
	list_filter = ('anno', 'utente')
	search_fields = ('nome', 'ccnl_livello', 'ccnl_qualifica')
	readonly_fields = ('data_creazione', 'data_modifica')
	ordering = ('-data_modifica',)
