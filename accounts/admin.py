

from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as DjangoUserAdmin
from django.urls import reverse
from django.utils.html import format_html
from django.utils.safestring import mark_safe
from django.utils.translation import gettext_lazy as _

from .models import User, ConfigurazioneSistema, ProfiloCandidato
from .portale_dipendente_defaults import (
    GRUPPO_PORTALE_DIPENDENTE,
    sync_gruppo_portale_se_ruolo_portale,
)
from anagrafiche.models import Azienda, Dipendente

# Documento e Presenza non sono registrati qui: liste e operazioni sono nell'app
# (documenti, presenze, consulente) per evitare duplicazione con Django admin.


@admin.register(User)
class UserAdmin(DjangoUserAdmin):
	"""Admin utenti: convalida supervisore e privacy sono su User, non su Dipendente."""

	# In indice admin: sezione «ACCOUNTS» → «Utenti» (/admin/accounts/user/)
	ordering = ('username',)
	readonly_fields = DjangoUserAdmin.readonly_fields + ('riepilogo_collegamenti_hr',)
	list_display = (
		'username',
		'email',
		'first_name',
		'last_name',
		'azienda',
		'is_staff',
		'convalidato',
		'privacy_accettata',
		'email_verificata',
		'is_active',
	)
	list_filter = (
		'is_staff',
		'is_superuser',
		'is_active',
		'convalidato',
		'privacy_accettata',
		'email_verificata',
		'totp_enabled',
		'email_stepup_login',
		'azienda',
	)
	search_fields = ('username', 'first_name', 'last_name', 'email')
	filter_horizontal = ('groups', 'user_permissions', 'ruoli')

	fieldsets = (
		(None, {'fields': ('username', 'password')}),
		(
			_('Collegamenti HR / portale'),
			{
				'fields': ('riepilogo_collegamenti_hr',),
				'description': _(
					'L’identità di login è sempre questa scheda «Utente». '
					'L’anagrafica aziendale «Dipendente» è il record usato da contratti, documenti e presenze. '
					'Il «Profilo portale» è il questionario compilato dal candidato/dipendente in autonomia (può contenere '
					'dati più ricchi o aggiornati prima del passaggio in anagrafica).'
				),
			},
		),
		(_('Info personali'), {'fields': ('first_name', 'last_name', 'email')}),
		(_('Azienda e ruoli'), {'fields': ('azienda', 'ruoli')}),
		(
			_('Convalida accesso (supervisore / HR)'),
			{
				'fields': ('convalidato', 'privacy_accettata', 'privacy_data', 'email_verificata'),
				'description': _(
					'Se «Convalidato» è disattivato, dopo il login l’utente vede il messaggio '
					'«account non convalidato dal supervisore» e non accede all’app (salvo admin/consulente/candidato). '
					'Per i dipendenti collega anche l’anagrafica: Dipendente → campo «Utente».'
				),
			},
		),
		(
			_('Permessi Django'),
			{
				'fields': ('is_active', 'is_staff', 'is_superuser', 'groups', 'user_permissions'),
				'description': _(
					'Per l’accesso al portale personale (documenti, buste paga, CUD, presenze, richieste) '
					'è previsto il gruppo «%(gruppo)s», assegnato automaticamente se l’utente ha ruolo '
					'«Candidato» o «Dipendente» oppure è collegato da anagrafica «Dipendente».'
				)
				% {'gruppo': GRUPPO_PORTALE_DIPENDENTE},
			},
		),
		(_('Date importanti'), {'fields': ('last_login', 'date_joined')}),
		(
			_('Autenticazione avanzata'),
			{
				'fields': (
					'totp_enabled',
					'totp_secret',
					'email_stepup_login',
					'email_token',
					'email_token_scadenza',
				),
				'description': _(
					'«2FA app (TOTP)»: dopo la password il portale web e le API richiedono il codice a tempo '
					'(Google Authenticator, Microsoft Authenticator, Aruba Key e altre app TOTP standard). '
					'Attivazione da app mobile (endpoint API 2FA) o da strumenti interni. '
					'«Verifica e-mail al login»: codice inviato via SMTP se il TOTP non è attivo oltre alla e-mail. '
					'Token «e-mail verifica» sulla scheda è distinto (verifica indirizzo una tantum).'
				),
				'classes': ('collapse',),
			},
		),
	)
	add_fieldsets = (
		(
			None,
			{
				'classes': ('wide',),
				'fields': (
					'username',
					'password1',
					'password2',
					'email',
					'azienda',
					'convalidato',
					'privacy_accettata',
					'is_staff',
				),
			},
		),
	)

	def save_related(self, request, form, formsets, change):
		super().save_related(request, form, formsets, change)
		sync_gruppo_portale_se_ruolo_portale(form.instance)

	@admin.display(description=_('Anagrafica dipendente e profilo portale'))
	def riepilogo_collegamenti_hr(self, obj):
		if not obj or not obj.pk:
			return '—'
		dip = Dipendente.objects.filter(utente_id=obj.pk).select_related('azienda').first()
		prof = ProfiloCandidato.objects.filter(user_id=obj.pk).first()
		rows = []
		if dip:
			url = reverse('admin:anagrafiche_dipendente_change', args=[dip.pk])
			rows.append(
				format_html(
					'<tr><td style="padding:4px 10px 4px 0;vertical-align:top"><strong>Dipendente</strong></td>'
					'<td>{} — {} <span class="quiet">({})</span><br><a class="button" href="{}">Apri anagrafica</a></td></tr>',
					dip.cognome,
					dip.nome,
					dip.get_stato_display(),
					url,
				)
			)
		else:
			rows.append(
				mark_safe(
					'<tr><td style="padding:4px 10px 4px 0;vertical-align:top"><strong>Dipendente</strong></td>'
					'<td><em>Nessuna anagrafica collegata</em> (campo «Utente» vuoto in Dipendenti).</td></tr>'
				)
			)
		if prof:
			url_p = reverse('admin:accounts_profilocandidato_change', args=[prof.pk])
			rows.append(
				format_html(
					'<tr><td style="padding:4px 10px 4px 0;vertical-align:top"><strong>Profilo portale</strong></td>'
					'<td>Questionario candidatura / area personale<br>'
					'<a class="button" href="{}">Apri profilo portale</a>'
					' &nbsp; <span class="quiet">completato: {}</span></td></tr>',
					url_p,
					'Sì' if prof.profilo_completato else 'No',
				)
			)
		else:
			rows.append(
				mark_safe(
					'<tr><td style="padding:4px 10px 4px 0;vertical-align:top"><strong>Profilo portale</strong></td>'
					'<td><em>Nessun profilo</em> (utente creato solo da back-office o senza registrazione candidato).</td></tr>'
				)
			)
		return mark_safe('<table style="margin:0;border-collapse:collapse">{}</table>'.format(''.join(rows)))


@admin.register(ProfiloCandidato)
class ProfiloCandidatoAdmin(admin.ModelAdmin):
	"""Visibilità in admin: stessi dati del portale self-service, collegati 1:1 all'utente."""

	list_display = (
		'user',
		'dipendente',
		'profilo_completato',
		'codice_fiscale',
		'azienda_interesse',
	)
	list_filter = ('profilo_completato', 'azienda_interesse')
	search_fields = (
		'user__username',
		'user__email',
		'user__first_name',
		'user__last_name',
		'codice_fiscale',
	)
	raw_id_fields = ('user', 'dipendente')
	autocomplete_fields = ('azienda_interesse',)


@admin.register(Azienda)
class AziendaAdmin(admin.ModelAdmin):
	verbose_name = 'Azienda'
	verbose_name_plural = 'Aziende'
	search_fields = ('nome', 'partita_iva', 'email')


@admin.register(Dipendente)
class DipendenteAdmin(admin.ModelAdmin):
	verbose_name = 'Dipendente'
	verbose_name_plural = 'Dipendenti'

	# In indice admin: «ANAGRAFICHE» → «Dipendenti» (/admin/anagrafiche/dipendente/)
	readonly_fields = ('accesso_account_riepilogo',)

	list_display = (
		'matricola',
		'cognome',
		'nome',
		'azienda',
		'stato',
		'mansione_breve',
		'email',
		'utente_username',
		'utente_convalidato_col',
		'utente_privacy_col',
	)
	list_filter = ('azienda', 'stato', 'mansione')
	search_fields = ('nome', 'cognome', 'email', 'codice_fiscale', 'utente__username')
	autocomplete_fields = ('utente', 'azienda')

	@admin.display(description='Mansione', ordering='mansione')
	def mansione_breve(self, obj):
		return obj.get_mansione_display() if obj.mansione else '—'

	@admin.display(description='Username app')
	def utente_username(self, obj):
		if obj.utente_id:
			return obj.utente.username
		return '—'

	@admin.display(description='Convalidato', boolean=True)
	def utente_convalidato_col(self, obj):
		if not obj.utente_id:
			return None
		return obj.utente.convalidato

	@admin.display(description='Privacy', boolean=True)
	def utente_privacy_col(self, obj):
		if not obj.utente_id:
			return None
		return obj.utente.privacy_accettata

	def get_queryset(self, request):
		return super().get_queryset(request).select_related('utente', 'azienda')

	def get_fieldsets(self, request, obj=None):
		fields = list(self.get_fields(request, obj))
		if 'accesso_account_riepilogo' in fields:
			fields.remove('accesso_account_riepilogo')
		return (
			(
				'Account app — convalida supervisore',
				{
					'fields': ('accesso_account_riepilogo',),
					'description': (
						'Il flag «Convalidato» è sul modello Utente (non sul dipendente). '
						'Elenco utenti: Admin → sezione ACCOUNTS → Utenti, oppure usa il pulsante qui sotto. '
						'In salvataggio, se imposti «Utente», vengono applicati automaticamente il gruppo Django '
						f'«{GRUPPO_PORTALE_DIPENDENTE}» (permessi portale) e il ruolo applicativo coerente '
						'con lo stato anagrafico (attivo → Dipendente, candidato → Candidato); '
						'se l’utente non ha azienda, viene impostata l’azienda di questa anagrafica.'
					),
				},
			),
			(None, {'fields': fields}),
		)

	@admin.display(description='Account app — convalida supervisore')
	def accesso_account_riepilogo(self, obj):
		if not obj or not obj.pk:
			return mark_safe(
				'<span class="help">Salva il dipendente, poi associa il campo «Utente» e usa il link sotto per convalidare.</span>'
			)
		if not obj.utente_id:
			return mark_safe(
				'<p style="margin:0"><strong style="color:#842029">Nessun utente collegato.</strong> '
				'Imposta il campo «Utente» in questa scheda (ricerca per username, es. massimo.cardella).</p>'
				'<p class="help" style="margin:.5em 0 0">La convalida del supervisore non è sul dipendente ma sull’<em>Utente</em>.</p>'
			)
		u = obj.utente
		ch_url = reverse('admin:accounts_user_change', args=[u.pk])
		ok = '#198754'
		ko = '#b02a37'
		return format_html(
			'<table style="margin:0"><tr><td style="padding:2px 12px 2px 0">Username</td><td><strong>{}</strong></td></tr>'
			'<tr><td>Convalidato (supervisore)</td><td style="font-weight:700;color:{}">{}</td></tr>'
			'<tr><td>Privacy accettata</td><td style="font-weight:700;color:{}">{}</td></tr></table>'
			'<p style="margin:.6em 0 0"><a class="button" href="{}">Apri scheda utente (modifica Convalidato / Privacy)</a></p>',
			u.username,
			ok if u.convalidato else ko,
			'Sì' if u.convalidato else 'No',
			ok if u.privacy_accettata else ko,
			'Sì' if u.privacy_accettata else 'No',
			ch_url,
		)


@admin.register(ConfigurazioneSistema)
class ConfigurazioneSistemaAdmin(admin.ModelAdmin):
	fieldsets = (
		('Informazioni sito', {
			'fields': ('nome_sito', 'nome_azienda', 'indirizzo_sede', 'partita_iva'),
		}),
		('Configurazione SMTP', {
			'fields': ('smtp_host', 'smtp_port', 'smtp_use_tls', 'smtp_user', 'smtp_password', 'email_mittente_nome', 'email_notifiche_hr'),
		}),
		('URL pubblici (link nelle e-mail)', {
			'fields': ('url_pubblica_base',),
			'description': 'Base URL per link assoluti quando si opera da localhost (es. certificazione firma, reset password).',
		}),
		('Log test e-mail', {
			'fields': ('ultimo_test_email_data', 'ultimo_test_email_esito', 'ultimo_test_email_messaggio', 'ultimo_test_email_destinatario'),
			'classes': ('collapse',),
		}),
		('Aspetto UI', {
			'fields': ('colore_primario', 'colore_accent', 'colore_sfondo', 'font_famiglia', 'font_dimensione_base'),
			'classes': ('collapse',),
		}),
	)

	def has_add_permission(self, request):
		# Singleton: non permettere di aggiungere una seconda riga
		return not ConfigurazioneSistema.objects.exists()

	def has_delete_permission(self, request, obj=None):
		return False
