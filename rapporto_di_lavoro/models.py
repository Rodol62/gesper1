from datetime import date
from typing import Optional
from django.db import models
from django.contrib.auth import get_user_model
from django.utils import timezone


def _anno_corrente():
    """Callable serializzabile per default= anno corrente (usato in ParametroVoceRetributiva)."""
    return timezone.localdate().year
from anagrafiche.models import Dipendente, Azienda
from .utils_calcoli import calcola_netto_dipendente, calcola_costo_azienda, calcola_completo

User = get_user_model()
LUOGO_FIRMA_DEFAULT = 'Palermo'

# Superminimo nel motore busta/simulatore: mensilità di riferimento **a tempo pieno** (Sm_ref); il tipo contratto applica la % part-time.
HELP_SUPERMINIMO_MENSILE_RIF_FT = (
	'Misura fissa concordata di riferimento a tempo pieno (€/mese, imponibile). '
	'È la fonte dati per busta e simulatore: il coefficiente part-time del tipo contratto determina '
	'l’importo mensile in cedolino (× % PT) e la quota €/h sul divisore CCNL. '
	'Compilare sul contratto e sulla proposta quando previsto. '
	'Concorre a 13ª/14ª, TFR, INPS, ferie/permessi e trattamento economico; non è bonus una tantum.'
)


class TipoContratto(models.Model):
	TIPO_CHOICES = [
		# ── Tempo indeterminato ──────────────────────────────────────
		('ind_full',        'Indeterminato full-time'),
		('ind_pt_50',       'Indeterminato part-time 50%'),
		('ind_pt_60',       'Indeterminato part-time 60%'),
		('ind_pt_75',       'Indeterminato part-time 75%'),
		('ind_pt_80',       'Indeterminato part-time 80%'),
		('ind_pt_90',       'Indeterminato part-time 90%'),
		('ind_pt_83',       'Indeterminato part-time 83%'),
		# ── Tempo determinato ────────────────────────────────────────
		('det_full',        'Determinato full-time'),
		('det_pt_50',       'Determinato part-time 50%'),
		('det_pt_75',       'Determinato part-time 75%'),
		# ── Stagionale ──────────────────────────────────────────────
		('stag_full',       'Stagionale full-time'),
		('stag_pt',         'Stagionale part-time'),
		# ── Apprendistato ────────────────────────────────────────────
		('apprendistato',   'Apprendistato professionalizzante'),
		# ── Lavoro intermittente ─────────────────────────────────────
		('intermittente',   'Lavoro intermittente / a chiamata'),
		# ── Somministrazione ─────────────────────────────────────────
		('somministrazione','Somministrazione'),
	]
	
	nome = models.CharField(max_length=100)
	ccnl = models.CharField(max_length=100)
	tipo = models.CharField(max_length=20, choices=TIPO_CHOICES, default='full_time')
	coefficiente_ore = models.DecimalField(max_digits=3, decimal_places=2, default=1.00, help_text='Moltiplicatore per le ore (1.00=full-time, 0.50=50% part-time, ecc.)')
	durata_giorni = models.IntegerField(null=True, blank=True)
	prova_giorni = models.IntegerField(default=30)
	prorogabile = models.BooleanField(default=True)
	rinnovabile = models.BooleanField(default=False)
	descrizione = models.TextField(blank=True)
	attivo = models.BooleanField(default=True)
	data_creazione = models.DateTimeField(auto_now_add=True)

	class Meta:
		verbose_name = 'Tipo Contratto'
		verbose_name_plural = 'Tipi Contratto'
		ordering = ['nome']

	def __str__(self):
		return f"{self.nome} ({self.ccnl})"


class RapportoDiLavoro(models.Model):
	mansionario_file = models.FileField(
		upload_to='contratti/mansionari/',
		null=True,
		blank=True,
		verbose_name='Mansionario allegato',
		help_text='Allega il mansionario specifico per la mansione (PDF, facoltativo)'
	)
	STATO_CHOICES = [
		('proposta', 'Proposta'),
		('sottoscritto', 'Sottoscritto'),
		('sospeso', 'Sospeso'),
		('cessato', 'Cessato'),
	]
	TURNO_CHOICES = [
		('diurno', 'Diurno'),
		('notturno', 'Notturno'),
		('turnista', 'Turnista'),
		('flessibile', 'Flessibile'),
	]

	azienda = models.ForeignKey(Azienda, on_delete=models.CASCADE, related_name='rapporti_di_lavoro')
	dipendente = models.ForeignKey(Dipendente, on_delete=models.CASCADE, related_name='rapporti_di_lavoro')

	numero_contratto = models.CharField(max_length=50, unique=True)
	tipo_contratto = models.ForeignKey(TipoContratto, on_delete=models.PROTECT)
	data_sottoscrizione = models.DateField(null=True, blank=True)
	data_ora_sottoscrizione = models.DateTimeField(null=True, blank=True)
	luogo_sottoscrizione = models.CharField(max_length=120, blank=True, default=LUOGO_FIRMA_DEFAULT)
	data_inizio_rapporto = models.DateField()
	data_fine_rapporto = models.DateField(null=True, blank=True)

	posizione = models.CharField(max_length=100)
	livello_ccnl = models.CharField(max_length=50)
	qualifica = models.CharField(max_length=100)

	stipendio_lordo_mensile = models.DecimalField(max_digits=10, decimal_places=2)
	paga_base_mensile = models.DecimalField(max_digits=10, decimal_places=2, default=0)
	contingenza_mensile = models.DecimalField(max_digits=10, decimal_places=2, default=0)
	edr_mensile = models.DecimalField(max_digits=10, decimal_places=2, default=0)
	superminimo_mensile = models.DecimalField(
		max_digits=10,
		decimal_places=2,
		default=0,
		verbose_name='Superminimo (rif. tempo pieno, €/mese)',
		help_text=HELP_SUPERMINIMO_MENSILE_RIF_FT,
	)
	tredicesima = models.BooleanField(default=True)
	quattordicesima = models.BooleanField(default=False)
	tredicesima_rateo_mensile_in_imponibile = models.BooleanField(
		default=False,
		verbose_name='13ª: quota mensile in busta (base INPS/IRPEF/INAIL)',
		help_text=(
			'Se attivo, la quota 1/12 erogata ogni mese in cedolino concorre alla base contributiva e fiscale mensile. '
			'Se disattivo, il rateo resta solo accantonamento / riferimento (es. pagamento a dicembre).'
		),
	)
	quattordicesima_rateo_mensile_in_imponibile = models.BooleanField(
		default=False,
		verbose_name='14ª: quota mensile in busta (base INPS/IRPEF/INAIL)',
		help_text='Come per la 13ª: solo se la quota mensile è effettivamente in busta e imponibile mensilmente.',
	)
	premio_obiettivi = models.DecimalField(max_digits=10, decimal_places=2, default=0)

	ore_settimanali = models.DecimalField(max_digits=5, decimal_places=2, default=40)
	ore_straordinario_diurno_maggiorazione = models.DecimalField(max_digits=5, decimal_places=2, default=15)
	ore_straordinario_notturno_maggiorazione = models.DecimalField(max_digits=5, decimal_places=2, default=30)
	ore_straordinario_festivo_maggiorazione = models.DecimalField(max_digits=5, decimal_places=2, default=30)
	riposi_compensativi_regola = models.TextField(blank=True)
	turno_tipo = models.CharField(max_length=50, choices=TURNO_CHOICES, default='diurno')
	decorrenza_validita_da = models.DateField(null=True, blank=True)
	decorrenza_validita_a = models.DateField(null=True, blank=True)
	scatto_periodicita_mesi = models.IntegerField(default=24)
	scatto_importo = models.DecimalField(max_digits=10, decimal_places=2, default=0)
	numero_scatti_massimi = models.IntegerField(default=10)

	giorni_ferie_annuali = models.IntegerField(default=20)
	giorni_permesso_annuali = models.IntegerField(default=3)
	giorni_malattia_retribuiti = models.IntegerField(default=0)

	aliquota_tfr = models.DecimalField(max_digits=5, decimal_places=2, default=6.5)
	fondo_pensione = models.CharField(max_length=100, null=True, blank=True)

	file_contratto_pdf = models.FileField(upload_to='contratti/pdf/', null=True, blank=True)
	file_proposta = models.FileField(upload_to='contratti/proposte/', null=True, blank=True)

	stato = models.CharField(max_length=20, choices=STATO_CHOICES, default='proposta')

	data_creazione = models.DateTimeField(auto_now_add=True)
	data_modifica = models.DateTimeField(auto_now=True)

	creato_da = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, related_name='rapporti_creati')
	modificato_da = models.ForeignKey(
		User,
		on_delete=models.SET_NULL,
		null=True,
		blank=True,
		related_name='rapporti_modificati'
	)

	class Meta:
		verbose_name = 'Rapporto di Lavoro'
		verbose_name_plural = 'Rapporti di Lavoro'
		ordering = ['-data_creazione']

	def __str__(self):
		return f"{self.numero_contratto} - {self.dipendente.cognome}"

	@property
	def data_sottoscrizione_effettiva(self):
		return self.data_ora_sottoscrizione or self.data_sottoscrizione

	@property
	def luogo_firma_datore_effettivo(self):
		proposta = getattr(self, 'proposta_origine', None)
		return self.luogo_sottoscrizione or getattr(proposta, 'luogo_firma_datore', '') or LUOGO_FIRMA_DEFAULT

	@property
	def data_firma_datore_effettiva(self):
		proposta = getattr(self, 'proposta_origine', None)
		return self.data_ora_sottoscrizione or getattr(proposta, 'data_firma_datore', None)

	@property
	def luogo_firma_lavoratore_effettivo(self):
		proposta = getattr(self, 'proposta_origine', None)
		return getattr(proposta, 'luogo_firma_candidato', '') or self.luogo_sottoscrizione or LUOGO_FIRMA_DEFAULT

	@property
	def data_firma_lavoratore_effettiva(self):
		proposta = getattr(self, 'proposta_origine', None)
		return getattr(proposta, 'data_firma_candidato', None) or self.data_ora_sottoscrizione


class AddendumContrattuale(models.Model):
	"""Storico addendum / variazioni su contratto definitivo senza sostituire il RapportoDiLavoro."""

	TIPO_CHOICES = [
		('rinnovo_td', 'Rinnovo o proroga a tempo determinato'),
		('trasformazione', 'Trasformazione (es. TD → TI)'),
		('variazione_retribuzione', 'Variazione retribuzione o livello CCNL'),
		('variazione_orario', 'Variazione orario o part-time'),
		('altro', 'Altro (specificare nelle note)'),
	]

	rapporto = models.ForeignKey(
		'RapportoDiLavoro',
		on_delete=models.CASCADE,
		related_name='addenda',
		verbose_name='Contratto di riferimento',
	)
	tipo = models.CharField(max_length=40, choices=TIPO_CHOICES, default='altro')
	data_decorrenza = models.DateField(
		verbose_name='Decorrenza',
		help_text='Data efficacia dell\'atto rispetto al contratto originario.',
	)
	data_fine_rapporto_aggiornata = models.DateField(
		null=True,
		blank=True,
		verbose_name='Nuova data fine rapporto',
		help_text='Es. nuova scadenza dopo rinnovo TD; lasciare vuoto se invariata.',
	)
	stipendio_lordo_mensile = models.DecimalField(
		max_digits=10, decimal_places=2, null=True, blank=True,
		verbose_name='RAL mensile lorda (riferimento)',
	)
	paga_base_mensile = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
	contingenza_mensile = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
	edr_mensile = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
	ore_settimanali = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)
	tipo_contratto = models.ForeignKey(
		'TipoContratto',
		null=True,
		blank=True,
		on_delete=models.SET_NULL,
		verbose_name='Tipo contratto',
	)
	parametro_ccnl = models.ForeignKey(
		'ParametroCCNLTurismo',
		null=True,
		blank=True,
		on_delete=models.SET_NULL,
		verbose_name='Parametro CCNL (riferimento tabellare)',
	)
	livello_ccnl = models.CharField(max_length=50, blank=True, verbose_name='Livello CCNL')
	qualifica = models.CharField(max_length=100, blank=True)
	riferimento_atto = models.CharField(
		max_length=120,
		blank=True,
		verbose_name='Riferimento atto / protocollo',
	)
	note = models.TextField(blank=True)
	file_allegato = models.FileField(
		upload_to='contratti/addenda/',
		null=True,
		blank=True,
		verbose_name='Allegato (PDF atto, lettera, ecc.)',
	)
	creato_da = models.ForeignKey(
		User,
		null=True,
		on_delete=models.SET_NULL,
		related_name='addenda_contrattuali_creati',
	)
	data_creazione = models.DateTimeField(auto_now_add=True)

	class Meta:
		ordering = ['-data_decorrenza', '-data_creazione', '-id']
		verbose_name = 'Addendum contrattuale'
		verbose_name_plural = 'Addendum contrattuali'

	def __str__(self):
		return f'Addendum {self.get_tipo_display()} — {self.rapporto.numero_contratto} ({self.data_decorrenza})'


class ModuloContrattuale(models.Model):
	"""Classificazione dei moduli usati nel processo proposta/contratto."""
	CATEGORIA_CHOICES = [
		('proposta_assunzione', 'Proposta assunzione'),
		('contratto_assunzione', 'Contratto di assunzione (proposta + contratto diretto)'),
		('integrazione_dati', 'Integrazione dati dipendente'),
		('consenso_privacy', 'Consenso privacy'),
		('allegati_obbligatori', 'Allegati obbligatori'),
		('altro', 'Altro'),
	]

	nome = models.CharField(max_length=120, unique=True)
	categoria = models.CharField(max_length=50, choices=CATEGORIA_CHOICES, default='proposta_assunzione')
	descrizione = models.TextField(blank=True)
	compilabile_da_dipendente = models.BooleanField(default=True)
	attivo = models.BooleanField(default=True)
	data_creazione = models.DateTimeField(auto_now_add=True)

	class Meta:
		verbose_name = 'Modulo Contrattuale'
		verbose_name_plural = 'Moduli Contrattuali'
		ordering = ['nome']

	def __str__(self):
		return f"{self.nome} ({self.get_categoria_display()})"


class Mansione(models.Model):
	"""Catalogo mansioni operative (es. HORECA) collegabili alla proposta di assunzione."""
	nome = models.CharField(max_length=80, unique=True)
	ordinamento = models.PositiveSmallIntegerField(default=0)
	attivo = models.BooleanField(default=True)

	class Meta:
		ordering = ['ordinamento', 'nome']
		verbose_name = 'Mansione'
		verbose_name_plural = 'Mansioni'

	def __str__(self):
		return self.nome


class MansioneLivelloCCNL(models.Model):
	"""Mappatura esplicita tra mansione operativa e livello/qualifica tabellare CCNL."""
	FONTE_CHOICES = [
		('standard', 'Standard da tabelle CCNL'),
		('custom_admin', 'Personalizzazione admin'),
	]
	mansione = models.ForeignKey(Mansione, on_delete=models.CASCADE, related_name='mappature_ccnl')
	livello = models.CharField(max_length=20)
	qualifica_tabellare = models.CharField(max_length=120, blank=True, default='')
	ccnl = models.CharField(max_length=150, blank=True, default='')
	versione = models.CharField(max_length=50, blank=True, default='')
	sezione = models.CharField(max_length=50, blank=True, default='')
	fonte = models.CharField(max_length=20, choices=FONTE_CHOICES, default='standard')
	priorita = models.PositiveSmallIntegerField(default=50, help_text='Valore più alto = precedenza maggiore')
	valida_da = models.DateField(null=True, blank=True)
	valida_a = models.DateField(null=True, blank=True)
	note = models.TextField(blank=True, default='')
	attivo = models.BooleanField(default=True)
	data_creazione = models.DateTimeField(auto_now_add=True)
	data_modifica = models.DateTimeField(auto_now=True)

	class Meta:
		verbose_name = 'Mappatura Mansione-Livello CCNL'
		verbose_name_plural = 'Mappature Mansione-Livello CCNL'
		ordering = ['-priorita', 'mansione__ordinamento', 'mansione__nome', 'livello', 'qualifica_tabellare']
		unique_together = ('mansione', 'livello', 'ccnl', 'versione', 'sezione')

	def __str__(self):
		ql = f" · {self.qualifica_tabellare}" if self.qualifica_tabellare else ''
		return f"{self.mansione.nome} -> L{self.livello}{ql}"


class ParametroCCNLTurismo(models.Model):
	"""Parametri retributivi/organizzativi per proposta e contratto di assunzione.

	Le proprietà ``netto_dipendente``, ``costo_azienda_*`` e il metodo ``calcolo_completo`` usano
	``utils_calcoli`` (stime semplificate su ``importo_lordo_mensile``). Non sostituiscono
	``calcola_busta_paga_mese`` per buste/simulazioni ufficiali (vedi ``motori_canonici``).
	"""
	SEZIONE_CHOICES = [
		('ristoranti_pizzerie', 'Ristoranti/Pizzerie con cucina'),
		('somministrazione_tavoli', 'Somministrazione e servizio ai tavoli'),
	]

	ccnl = models.CharField(max_length=150, default='Turismo Confcommercio')
	versione = models.CharField(max_length=50, default='2024-2026')
	sezione = models.CharField(max_length=50, choices=SEZIONE_CHOICES, default='ristoranti_pizzerie')
	livello = models.CharField(max_length=20)
	qualifica = models.CharField(max_length=120)
	tipo_contratto_nazionale = models.CharField(max_length=120)
	decorrenza_validita_da = models.DateField(null=True, blank=True)
	decorrenza_validita_a = models.DateField(null=True, blank=True)
	# Campi strutturali tabella retributiva (fonte PDF/accordi)
	livello_ordinamento = models.IntegerField(null=True, blank=True, help_text='Ordine di visualizzazione livello in tabella retributiva')
	minimo_tabellare = models.DecimalField(max_digits=10, decimal_places=2, default=0, help_text='Valore minimo tabellare da tabella retributiva')
	totale_tabellare = models.DecimalField(max_digits=10, decimal_places=2, default=0, help_text='Totale tabellare lordo (minimo + contingenza + eventuali voci)')
	fonte_tabella = models.CharField(max_length=120, blank=True, default='', help_text='Fonte dati tabellari (es. tabelle retributive.pdf)')
	data_rilevazione_tabella = models.DateField(null=True, blank=True, help_text='Data della rilevazione/accordo tabellare')
	importo_lordo_mensile = models.DecimalField(max_digits=10, decimal_places=2)
	paga_base_mensile = models.DecimalField(max_digits=10, decimal_places=2, default=0)
	contingenza_mensile = models.DecimalField(max_digits=10, decimal_places=2, default=0)
	edr_mensile = models.DecimalField(max_digits=10, decimal_places=2, default=0)
	elemento_distinto_sanita = models.DecimalField(max_digits=10, decimal_places=5, default=0, help_text='EDS - Elemento Distinto Sanità (€/ora)')
	elemento_distinto_bilateralita = models.DecimalField(max_digits=10, decimal_places=5, default=0, help_text='EDB - Elemento Distinto Bilateralità (€/ora)')
	indennita_mensile = models.DecimalField(max_digits=10, decimal_places=2, default=0)
	ore_settimanali = models.DecimalField(max_digits=5, decimal_places=2, default=40)
	ore_mensili = models.DecimalField(max_digits=6, decimal_places=2)
	ore_giornaliere = models.DecimalField(max_digits=5, decimal_places=2)
	scatto_periodicita_mesi = models.IntegerField(default=24)
	scatto_importo = models.DecimalField(max_digits=10, decimal_places=2, default=0)
	numero_scatti_massimi = models.IntegerField(default=10)
	straordinario_diurno_maggiorazione = models.DecimalField(max_digits=5, decimal_places=2, default=15)
	straordinario_notturno_maggiorazione = models.DecimalField(max_digits=5, decimal_places=2, default=30)
	straordinario_festivo_maggiorazione = models.DecimalField(max_digits=5, decimal_places=2, default=30)
	riposi_compensativi_regola = models.TextField(blank=True)
	note = models.TextField(blank=True)
	attivo = models.BooleanField(default=True)
	data_creazione = models.DateTimeField(auto_now_add=True)
	data_modifica = models.DateTimeField(auto_now=True)

	class Meta:
		verbose_name = 'Parametro CCNL Turismo'
		verbose_name_plural = 'Parametri CCNL Turismo'
		ordering = ['sezione', 'livello', 'qualifica']
		unique_together = ('ccnl', 'versione', 'sezione', 'livello', 'qualifica')

	def __str__(self):
		return f"{self.ccnl} {self.versione} - {self.sezione} L{self.livello} {self.qualifica}"

	@property
	def netto_dipendente(self):
		"""Stima netto mensile da lordo tabellare (``utils_calcoli``); non è il netto del motore busta."""
		calcolo = calcola_netto_dipendente(self.importo_lordo_mensile)
		return calcolo['netto']

	@property
	def costo_azienda_mensile(self):
		"""Stima costo mensile da lordo tabellare (``utils_calcoli`` legacy); non il costo del motore busta."""
		calcolo = calcola_costo_azienda(self.importo_lordo_mensile)
		return calcolo['costo_totale_mensile']

	@property
	def costo_azienda_annuo(self):
		"""Stima costo annuo da lordo tabellare (``utils_calcoli`` legacy)."""
		calcolo = calcola_costo_azienda(self.importo_lordo_mensile)
		return calcolo['costo_totale_annuo']

	def calcolo_completo(self):
		"""
		Dettaglio netto+costo da ``utils_calcoli.calcola_completo`` (stima su tabellare).

		Non usare per output contrattuale ufficiale: per quello il motore è ``calcola_busta_paga_mese``.
		"""
		return calcola_completo(self.importo_lordo_mensile)


class RegolaNormativaCCNL(models.Model):
	"""Regole normative per livello/versione CCNL (orario, ferie, permessi, scatti)."""
	ccnl = models.CharField(max_length=150)
	versione = models.CharField(max_length=50)
	sezione = models.CharField(max_length=50, choices=ParametroCCNLTurismo.SEZIONE_CHOICES, default='ristoranti_pizzerie')
	livello = models.CharField(max_length=20)
	decorrenza_validita_da = models.DateField(null=True, blank=True)
	decorrenza_validita_a = models.DateField(null=True, blank=True)

	ore_settimanali = models.DecimalField(max_digits=5, decimal_places=2, default=40)
	ore_mensili = models.DecimalField(max_digits=6, decimal_places=2, default=173.33)
	ore_giornaliere = models.DecimalField(max_digits=5, decimal_places=2, default=8)
	ferie_annue_giorni = models.DecimalField(max_digits=5, decimal_places=2, default=26)
	permessi_annui_ore = models.DecimalField(max_digits=6, decimal_places=2, default=72)

	scatto_periodicita_mesi = models.IntegerField(default=36)
	scatto_importo = models.DecimalField(max_digits=10, decimal_places=2, default=0)
	numero_scatti_massimi = models.IntegerField(default=6)

	note = models.TextField(blank=True)
	attivo = models.BooleanField(default=True)
	data_creazione = models.DateTimeField(auto_now_add=True)
	data_modifica = models.DateTimeField(auto_now=True)

	class Meta:
		verbose_name = 'Regola normativa CCNL'
		verbose_name_plural = 'Regole normative CCNL'
		ordering = ['ccnl', 'versione', 'sezione', 'livello', '-decorrenza_validita_da']
		unique_together = ('ccnl', 'versione', 'sezione', 'livello', 'decorrenza_validita_da')

	def __str__(self):
		return f"{self.ccnl} {self.versione} - {self.sezione} L{self.livello}"


class PropostaAssunzione(models.Model):
	"""Workflow proposta assunzione: bozza → inviata → firmata candidato → contratto attivo."""
	# Stati canonici (per nuovi flussi)
	STATI_CANONICI = (
		'bozza',
		'inviata_candidato',
		'firmata_candidato',
		'contratto_attivo',
		'rifiutata_candidato',
		'rifiutata_admin',
	)
	# Mappa legacy -> stato canonico equivalente (sola lettura/compat)
	LEGACY_TO_CANONICO = {
		'inviata_al_dipendente': 'inviata_candidato',
		'accettata_dipendente': 'firmata_candidato',
		'convertita_in_contratto': 'contratto_attivo',
		'rifiutata_dipendente': 'rifiutata_candidato',
		'in_revisione_admin': 'firmata_candidato',
		'approvata_admin': 'firmata_candidato',
	}
	STATO_CHOICES = [
		# ── Stati attivi ──────────────────────────────────────────────
		('bozza',              'Bozza'),
		('inviata_candidato',  'Inviata al candidato'),
		('firmata_candidato',  'Firmata dal candidato'),
		('contratto_attivo',   'Contratto attivo'),
		# ── Rifiuti ───────────────────────────────────────────────────
		('rifiutata_candidato', 'Rifiutata dal candidato'),
		('rifiutata_admin',    'Rifiutata dall\'amministrazione'),
		# ── Compatibilità retroattiva (stati legacy, non più usati) ──
		('inviata_al_dipendente',   'Inviata al dipendente (legacy)'),
		('accettata_dipendente',    'Accettata dal dipendente (legacy)'),
		('rifiutata_dipendente',    'Rifiutata dal dipendente (legacy)'),
		('in_revisione_admin',      'In revisione admin (legacy)'),
		('approvata_admin',         'Approvata admin (legacy)'),
		('convertita_in_contratto', 'Convertita in contratto (legacy)'),
	]

	azienda = models.ForeignKey(Azienda, on_delete=models.CASCADE, related_name='proposte_assunzione')
	dipendente = models.ForeignKey(Dipendente, on_delete=models.CASCADE, related_name='proposte_assunzione')
	modulo = models.ForeignKey(ModuloContrattuale, on_delete=models.PROTECT, related_name='proposte')
	mansione = models.ForeignKey(
		'Mansione',
		on_delete=models.SET_NULL,
		null=True,
		blank=True,
		related_name='proposte',
		verbose_name='Mansione',
		help_text='Mansione operativa (es. cuoco, cameriere) indipendente dalla voce tabellare CCNL.',
	)
	parametro_ccnl = models.ForeignKey(
		ParametroCCNLTurismo,
		on_delete=models.PROTECT,
		null=True,
		blank=True,
		related_name='proposte'
	)

	numero_proposta = models.CharField(max_length=50, unique=True)
	titolo = models.CharField(max_length=150)
	note = models.TextField(blank=True)
	riferimenti_normativi = models.TextField(
		default=(
			"La presente proposta è formulata nel rispetto della normativa italiana vigente in materia di lavoro, "
			"con particolare riferimento a: Costituzione della Repubblica Italiana (artt. 1, 4, 35, 36, 37, 38); "
			"Codice Civile, Libro V; D.Lgs. 81/2015 (disciplina organica dei contratti di lavoro); "
			"D.Lgs. 66/2003 (orario di lavoro); D.Lgs. 152/1997 e D.Lgs. 104/2022 (informazioni sul rapporto di lavoro); "
			"D.Lgs. 151/2001 (tutela maternità/paternità); D.Lgs. 198/2006 (pari opportunità); "
			"D.Lgs. 81/2008 (salute e sicurezza sul lavoro); normativa previdenziale e assicurativa INPS/INAIL; "
			"CCNL applicato indicato nella proposta."
		),
		help_text='Riferimenti normativi italiani applicati alla proposta.'
	)
	dichiarazione_conformita_legale = models.BooleanField(
		default=True,
		help_text='Conferma che la proposta è stata redatta in conformità al diritto del lavoro italiano e al CCNL applicato.'
	)

	tipo_contratto = models.ForeignKey(TipoContratto, on_delete=models.PROTECT)
	data_inizio_rapporto = models.DateField()
	data_fine_rapporto = models.DateField(null=True, blank=True)
	posizione = models.CharField(max_length=100)
	livello_ccnl = models.CharField(max_length=50)
	qualifica = models.CharField(max_length=100)
	stipendio_lordo_mensile = models.DecimalField(max_digits=10, decimal_places=2)
	paga_base_mensile = models.DecimalField(max_digits=10, decimal_places=2, default=0)
	contingenza_mensile = models.DecimalField(max_digits=10, decimal_places=2, default=0)
	edr_mensile = models.DecimalField(max_digits=10, decimal_places=2, default=0)
	superminimo_mensile = models.DecimalField(
		max_digits=10,
		decimal_places=2,
		default=0,
		verbose_name='Superminimo (rif. tempo pieno, €/mese)',
		help_text=HELP_SUPERMINIMO_MENSILE_RIF_FT,
	)
	indennita_mensile = models.DecimalField(max_digits=10, decimal_places=2, default=0)
	tredicesima = models.BooleanField(default=True, help_text='Mensilità aggiuntiva pagata a dicembre (CCNL FIPE)')
	quattordicesima = models.BooleanField(default=False, help_text='Mensilità aggiuntiva pagata a luglio (CCNL FIPE turismo)')
	tredicesima_rateo_mensile_in_imponibile = models.BooleanField(
		default=False,
		verbose_name='13ª: quota mensile in busta (imponibile mensile)',
		help_text='Se vero, la quota 1/12 mensile è in cedolino e concorre a INPS/IRPEF/INAIL del mese; altrimenti è solo accantonamento teorico.',
	)
	quattordicesima_rateo_mensile_in_imponibile = models.BooleanField(
		default=False,
		verbose_name='14ª: quota mensile in busta (imponibile mensile)',
		help_text='Come per la 13ª: attivare solo se la 14ª è rateizzata in busta ogni mese.',
	)
	giorni_ferie_annuali = models.IntegerField(default=26, help_text='Giorni di ferie annue spettanti (art. 7 D.Lgs. 66/2003)')
	giorni_permesso_annuali = models.IntegerField(default=3, help_text='Giorni di permesso retribuito annui (CCNL)')
	ore_settimanali = models.DecimalField(max_digits=5, decimal_places=2, default=40)
	ore_mensili = models.DecimalField(max_digits=6, decimal_places=2, default=173.33)
	ore_giornaliere = models.DecimalField(max_digits=5, decimal_places=2, default=8)
	decorrenza_validita_da = models.DateField(null=True, blank=True)
	decorrenza_validita_a = models.DateField(null=True, blank=True)
	scatto_periodicita_mesi = models.IntegerField(default=24)
	scatto_importo = models.DecimalField(max_digits=10, decimal_places=2, default=0)
	numero_scatti_massimi = models.IntegerField(default=10)
	straordinario_diurno_maggiorazione = models.DecimalField(max_digits=5, decimal_places=2, default=15)
	straordinario_notturno_maggiorazione = models.DecimalField(max_digits=5, decimal_places=2, default=30)
	straordinario_festivo_maggiorazione = models.DecimalField(max_digits=5, decimal_places=2, default=30)
	riposi_compensativi_regola = models.TextField(blank=True)

	accettata_dipendente = models.BooleanField(default=False)
	data_accettazione_dipendente = models.DateTimeField(null=True, blank=True)
	note_dipendente = models.TextField(blank=True)

	approvata_admin = models.BooleanField(default=False)
	data_approvazione_admin = models.DateTimeField(null=True, blank=True)
	note_admin = models.TextField(blank=True)

	# ── Allegato mansionario ──────────────────────────────────────────
	mansionario_file = models.FileField(
		upload_to='contratti/mansionari/',
		null=True,
		blank=True,
		verbose_name='Mansionario allegato',
		help_text='Allega il mansionario specifico per la mansione (PDF, facoltativo)'
	)
	# ── Firma digitale candidato ───────────────────────────────────────
	data_firma_candidato = models.DateTimeField(
		null=True, blank=True,
		help_text='Timestamp della firma digitale (spunta) del candidato.'
	)
	luogo_firma_candidato = models.CharField(
		max_length=120, blank=True, default=LUOGO_FIRMA_DEFAULT,
		help_text='Luogo di accettazione/firma del candidato.'
	)
	ip_firma_candidato = models.CharField(
		max_length=45, blank=True,
		help_text='Indirizzo IP del candidato al momento della firma.'
	)
	# ── Firma definitiva datore di lavoro ──────────────────────────────
	data_firma_datore = models.DateTimeField(
		null=True, blank=True,
		help_text='Timestamp della firma definitiva del datore di lavoro.'
	)
	luogo_firma_datore = models.CharField(
		max_length=120, blank=True, default=LUOGO_FIRMA_DEFAULT,
		help_text='Luogo di firma definitiva del datore di lavoro.'
	)

	stato = models.CharField(max_length=30, choices=STATO_CHOICES, default='bozza')
	contratto_generato = models.OneToOneField(
		RapportoDiLavoro,
		on_delete=models.SET_NULL,
		null=True,
		blank=True,
		related_name='proposta_origine'
	)

	creato_da = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, related_name='proposte_assunzione_create')
	modificato_da = models.ForeignKey(
		User,
		on_delete=models.SET_NULL,
		null=True,
		blank=True,
		related_name='proposte_assunzione_modifica'
	)
	data_creazione = models.DateTimeField(auto_now_add=True)
	data_modifica = models.DateTimeField(auto_now=True)

	class Meta:
		verbose_name = 'Proposta Assunzione'
		verbose_name_plural = 'Proposte Assunzione'
		ordering = ['-data_creazione']

	def __str__(self):
		return f"{self.numero_proposta} - {self.dipendente}"

	@classmethod
	def stati_equivalenti(cls, stato_canonico):
		"""Ritorna stati (canonico + legacy) equivalenti al target canonico."""
		equivalenti = {stato_canonico}
		for legacy, canonico in cls.LEGACY_TO_CANONICO.items():
			if canonico == stato_canonico:
				equivalenti.add(legacy)
		return tuple(equivalenti)

	@property
	def stato_canonico(self):
		"""Normalizza stato proposta per i flussi applicativi correnti."""
		return self.LEGACY_TO_CANONICO.get(self.stato, self.stato)

	def is_inviata_al_candidato(self):
		return self.stato in self.stati_equivalenti('inviata_candidato')

	def is_firmata_da_candidato(self):
		return self.stato in self.stati_equivalenti('firmata_candidato')

	@property
	def parametro_ccnl_risolto(self):
		"""
		Parametro CCNL usato per la simulazione economica: FK diretto se presente,
		altrimenti stesso fallback della bozza contratto (livello + data inizio).
		"""
		if self.parametro_ccnl_id:
			return self.parametro_ccnl
		livello = (self.livello_ccnl or '').strip()
		if not livello:
			return None
		di = self.data_inizio_rapporto or date.today()
		return ParametroCCNLTurismo.objects.filter(
			livello=livello,
			attivo=True,
			decorrenza_validita_da__lte=di,
		).order_by('-decorrenza_validita_da').first()

	def save(self, *args, **kwargs):
		"""
		Governance stati: in scrittura normalizza eventuali stati legacy al canonico.
		I record storici legacy restano leggibili, ma nuovi aggiornamenti convergono.
		"""
		self.stato = self.LEGACY_TO_CANONICO.get(self.stato, self.stato)
		super().save(*args, **kwargs)

	@property
	def luogo_firma_candidato_effettivo(self):
		return self.luogo_firma_candidato or LUOGO_FIRMA_DEFAULT

	@property
	def luogo_firma_datore_effettivo(self):
		return self.luogo_firma_datore or LUOGO_FIRMA_DEFAULT

	def compila_riferimenti_normativi(self):
		ccnl_label = self.parametro_ccnl.ccnl if self.parametro_ccnl_id else 'CCNL applicato in azienda'
		self.riferimenti_normativi = (
			"La presente proposta è formulata nel rispetto della normativa italiana vigente in materia di lavoro, "
			"con particolare riferimento a: Costituzione della Repubblica Italiana (artt. 1, 4, 35, 36, 37, 38); "
			"Codice Civile, Libro V; D.Lgs. 81/2015 (disciplina organica dei contratti di lavoro); "
			"D.Lgs. 66/2003 (orario di lavoro); D.Lgs. 152/1997 e D.Lgs. 104/2022 (trasparenza delle condizioni di lavoro); "
			"D.Lgs. 151/2001 (tutele genitorialità); D.Lgs. 198/2006 (pari opportunità); "
			"D.Lgs. 81/2008 (salute e sicurezza); normativa previdenziale e assicurativa INPS/INAIL; "
			f"{ccnl_label}."
		)

	def puo_essere_convertita(self):
		"""Legacy: compatibilità con template/views esistenti."""
		return self.is_firmata_da_candidato() and self.contratto_generato is None

	def motivi_blocco_conversione(self):
		"""Restituisce i motivi che impediscono la firma definitiva admin."""
		motivi = []
		if self.contratto_generato_id:
			motivi.append('Il contratto è già stato generato da questa proposta.')
			return motivi
		if not self.is_firmata_da_candidato():
			motivi.append('Il candidato non ha ancora firmato la proposta.')
		return motivi

	def puo_firma_definitiva_admin(self):
		"""True se la proposta è pronta per la firma definitiva del datore."""
		return self.is_firmata_da_candidato() and self.contratto_generato is None

	def firma_definitiva_admin(self, utente):
		"""
		Firma definitiva del datore di lavoro: crea il RapportoDiLavoro già sottoscritto,
		promuove il candidato a dipendente e imposta lo stato a contratto_attivo.

		Definizioni di dominio (candidato, dipendente, TI/TD, variazioni): vedi il modulo
		``rapporto_di_lavoro.concetti_dominio``.
		"""
		if not self.puo_firma_definitiva_admin():
			raise ValueError(' — '.join(self.motivi_blocco_conversione()))

		firma_ts = timezone.now()
		self.data_firma_datore = firma_ts
		self.luogo_firma_datore = self.luogo_firma_datore or LUOGO_FIRMA_DEFAULT
		contratto = self._crea_rapporto_di_lavoro(
			utente,
			stato_finale='sottoscritto',
			data_ora_sottoscrizione=firma_ts,
			luogo_sottoscrizione=self.luogo_firma_datore,
		)
		self.contratto_generato = contratto
		self.stato = 'contratto_attivo'
		self.modificato_da = utente
		self.save(update_fields=[
			'data_firma_datore', 'luogo_firma_datore', 'contratto_generato', 'stato', 'modificato_da', 'data_modifica'
		])

		# Promozione candidato → dipendente
		dip = self.dipendente
		utente_candidato = getattr(dip, 'utente', None)
		if utente_candidato and utente_candidato.has_ruolo('candidato'):
			# ruolo assegnato via M2M sotto
			utente_candidato.azienda = self.azienda
			utente_candidato.save(update_fields=['azienda'])
			from accounts.models import Ruolo as _Ruolo
			_r, _ = _Ruolo.objects.get_or_create(codice='dipendente', defaults={'nome': 'Dipendente'})
			utente_candidato.ruoli.add(_r)
		update_dip = []
		if dip.stato == 'candidato':
			dip.stato = 'attivo'
			update_dip.append('stato')
		if update_dip:
			dip.save(update_fields=update_dip)
		# data_assunzione / data_cessazione: allineate dal contratto appena creato (post_save → signals)

		from accounts.contratto_utente_definitivo import (
			ribalta_utente_candidato_su_dipendente_se_contratto_definitivo,
		)

		ribalta_utente_candidato_su_dipendente_se_contratto_definitivo(
			dip, contratto, motivo="PropostaAssunzione.firma_definitiva_admin"
		)

		return contratto

	def _crea_rapporto_di_lavoro(self, utente, stato_finale='proposta', data_ora_sottoscrizione=None, luogo_sottoscrizione=''):
		"""Crea il RapportoDiLavoro dalla proposta. stato_finale: 'proposta' o 'sottoscritto'."""
		aliquota_tfr_default = 6.91
		if self.parametro_ccnl_id:
			try:
				_ccnl_obj = CCNL.objects.filter(sigla='FIPE').first()
				if _ccnl_obj:
					_rateo = ParametroRatei.objects.filter(
						ccnl=_ccnl_obj, tipo_rateo='tfr', attivo=True
					).order_by('-anno').first()
					if _rateo:
						aliquota_tfr_default = float(_rateo.coefficiente)
			except Exception:
				pass

		numero_contratto = f"CTR-{self.numero_proposta}-{timezone.now().strftime('%Y%m%d%H%M%S')}"
		data_sott = data_ora_sottoscrizione.date() if (stato_finale == 'sottoscritto' and data_ora_sottoscrizione) else None
		contratto = RapportoDiLavoro.objects.create(
			azienda=self.azienda,
			dipendente=self.dipendente,
			numero_contratto=numero_contratto,
			tipo_contratto=self.tipo_contratto,
			data_sottoscrizione=data_sott,
			data_ora_sottoscrizione=data_ora_sottoscrizione,
			luogo_sottoscrizione=luogo_sottoscrizione or self.luogo_firma_datore or LUOGO_FIRMA_DEFAULT,
			data_inizio_rapporto=self.data_inizio_rapporto,
			data_fine_rapporto=self.data_fine_rapporto,
			posizione=self.posizione,
			livello_ccnl=self.livello_ccnl,
			qualifica=self.qualifica,
			stipendio_lordo_mensile=self.stipendio_lordo_mensile,
			paga_base_mensile=self.paga_base_mensile,
			contingenza_mensile=self.contingenza_mensile,
			edr_mensile=self.edr_mensile,
			superminimo_mensile=self.superminimo_mensile,
			ore_settimanali=self.ore_settimanali,
			tredicesima=self.tredicesima,
			quattordicesima=self.quattordicesima,
			tredicesima_rateo_mensile_in_imponibile=self.tredicesima_rateo_mensile_in_imponibile,
			quattordicesima_rateo_mensile_in_imponibile=self.quattordicesima_rateo_mensile_in_imponibile,
			giorni_ferie_annuali=self.giorni_ferie_annuali,
			giorni_permesso_annuali=self.giorni_permesso_annuali,
			ore_straordinario_diurno_maggiorazione=self.straordinario_diurno_maggiorazione,
			ore_straordinario_notturno_maggiorazione=self.straordinario_notturno_maggiorazione,
			ore_straordinario_festivo_maggiorazione=self.straordinario_festivo_maggiorazione,
			riposi_compensativi_regola=self.riposi_compensativi_regola,
			decorrenza_validita_da=self.decorrenza_validita_da,
			decorrenza_validita_a=self.decorrenza_validita_a,
			scatto_periodicita_mesi=self.scatto_periodicita_mesi,
			scatto_importo=self.scatto_importo,
			numero_scatti_massimi=self.numero_scatti_massimi,
			aliquota_tfr=aliquota_tfr_default,
			stato=stato_finale,
			creato_da=utente,
		)
		# Copia l'allegato mansionario se presente
		if self.mansionario_file:
			contratto.mansionario_file.save(self.mansionario_file.name, self.mansionario_file.file, save=True)
		return contratto

	def converti_in_contratto(self, utente):
		"""Legacy: usa firma_definitiva_admin se la proposta è già firmata dal candidato."""
		if self.stato in self.stati_equivalenti('firmata_candidato'):
			return self.firma_definitiva_admin(utente)
		# Vecchio percorso per record legacy (accettata_dipendente + approvata_admin)
		motivi = self.motivi_blocco_conversione()
		if motivi:
			raise ValueError(' — '.join(motivi))

		contratto = self._crea_rapporto_di_lavoro(utente, stato_finale='proposta')
		self.contratto_generato = contratto
		self.stato = 'contratto_attivo'
		self.modificato_da = utente
		self.save(update_fields=['contratto_generato', 'stato', 'modificato_da', 'data_modifica'])
		return contratto


class SimulazioneOrganico(models.Model):
	"""Storico cronologico delle simulazioni organico."""
	azienda = models.ForeignKey(Azienda, on_delete=models.CASCADE, related_name='simulazioni_organico')
	utente = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='simulazioni_organico')
	mese_riferimento = models.CharField(
		max_length=32,
		help_text='Es. YYYY-MM (simulatore tabella) oppure etichetta annua (es. 2026-annuale).',
	)
	parametri_json = models.JSONField(default=dict, blank=True)
	risultato_json = models.JSONField(default=dict, blank=True)
	querystring = models.TextField(blank=True)
	data_creazione = models.DateTimeField(auto_now_add=True)

	class Meta:
		verbose_name = 'Simulazione organico'
		verbose_name_plural = 'Simulazioni organico'
		ordering = ['-data_creazione']

	def __str__(self):
		azienda_nome = self.azienda.nome if self.azienda_id else 'N/A'
		return f"Simulazione {self.mese_riferimento} - {azienda_nome} - {self.data_creazione:%d/%m/%Y %H:%M}"


class SimulazioneVoceRetributivaOre(models.Model):
	"""Valori per singola voce retributiva della struttura Excel (per ruolo/dipendente simulato)."""
	VOCE_CHOICES = [
		('minimo_tabellare', 'Minimo tabellare'),
		('contingenza', 'Contingenza'),
		('el_dis_san', 'EL.DIS.SAN'),
		('scatto_anzianita', 'Scatto anzianità'),
		('superminimo', 'Superminimo'),
		('el_dis_bil', 'EL.DIS.BIL'),
	]

	simulazione = models.ForeignKey(
		SimulazioneOrganico,
		on_delete=models.CASCADE,
		related_name='voci_retributive_ore',
	)
	azienda = models.ForeignKey(Azienda, on_delete=models.CASCADE, related_name='voci_retributive_ore')
	mese_riferimento = models.CharField(max_length=7, help_text='Formato YYYY-MM')

	ruolo_id = models.CharField(max_length=80, help_text='Chiave ruolo simulato (es. cuoco, cameriere1)')
	ruolo_label = models.CharField(max_length=120, blank=True)
	dipendente_nome = models.CharField(max_length=150, blank=True)

	voce = models.CharField(max_length=30, choices=VOCE_CHOICES)
	presente = models.BooleanField(default=False)
	importo_lordo = models.DecimalField(max_digits=10, decimal_places=2, default=0)

	data_creazione = models.DateTimeField(auto_now_add=True)
	data_modifica = models.DateTimeField(auto_now=True)

	class Meta:
		verbose_name = 'Simulazione voce retributiva (ore)'
		verbose_name_plural = 'Simulazione voci retributive (ore)'
		ordering = ['-data_modifica', 'ruolo_id', 'voce']
		unique_together = ('simulazione', 'ruolo_id', 'voce')

	def __str__(self):
		return f"{self.mese_riferimento} · {self.ruolo_id} · {self.voce}"


class FestivitaCalendario(models.Model):
	"""Calendario festività (nazionali/locali) usato per calcolo maggiorazioni."""
	LIVELLO_CHOICES = [
		('nazionale', 'Nazionale'),
		('regionale', 'Regionale'),
		('provinciale', 'Provinciale'),
		('comunale', 'Comunale'),
		('aziendale', 'Aziendale'),
	]

	data = models.DateField()
	nome = models.CharField(max_length=120)
	livello = models.CharField(max_length=20, choices=LIVELLO_CHOICES, default='nazionale')
	regione = models.CharField(max_length=80, blank=True)
	provincia = models.CharField(max_length=10, blank=True)
	comune = models.CharField(max_length=80, blank=True)
	# Per festività aziendali (patrono locale, ecc.): opzionale
	azienda = models.ForeignKey(
		Azienda,
		on_delete=models.CASCADE,
		null=True, blank=True,
		related_name='festivita_aziendali',
	)
	attivo = models.BooleanField(default=True)
	data_creazione = models.DateTimeField(auto_now_add=True)

	class Meta:
		verbose_name = 'Festività calendario'
		verbose_name_plural = 'Festività calendario'
		ordering = ['data', 'livello', 'nome']
		unique_together = ('data', 'nome', 'livello', 'regione', 'provincia', 'comune', 'azienda')

	def __str__(self):
		return f"{self.data:%d/%m/%Y} - {self.nome} ({self.livello})"


class ChiusuraAziendale(models.Model):
	"""Periodo di chiusura aziendale per gestire maggiorazioni/ferie/riposi."""
	TRATTAMENTO_CHOICES = [
		('ferie', 'Ferie'),
		('riposo_compensativo', 'Riposo compensativo'),
		('lavoro_normale', 'Lavoro normale per esigenze aziendali'),
		('chiusura_non_retribuita', 'Chiusura non retribuita'),
	]

	azienda = models.ForeignKey(Azienda, on_delete=models.CASCADE, related_name='chiusure_aziendali')
	data_inizio = models.DateField()
	data_fine = models.DateField()
	trattamento = models.CharField(max_length=30, choices=TRATTAMENTO_CHOICES, default='ferie')
	descrizione = models.CharField(max_length=200, blank=True)
	attivo = models.BooleanField(default=True)
	data_creazione = models.DateTimeField(auto_now_add=True)

	class Meta:
		verbose_name = 'Chiusura aziendale'
		verbose_name_plural = 'Chiusure aziendali'
		ordering = ['-data_inizio']

	def __str__(self):
		return f"{self.azienda.nome} {self.data_inizio:%d/%m/%Y}-{self.data_fine:%d/%m/%Y} ({self.trattamento})"


class CalendarioPresenzeDipendente(models.Model):
	"""Calendario mensile presenze/assenze/straordinari per dipendente o ruolo simulato."""
	azienda = models.ForeignKey(Azienda, on_delete=models.CASCADE, related_name='calendari_presenze')
	dipendente = models.ForeignKey(
		Dipendente,
		on_delete=models.CASCADE,
		related_name='calendari_presenze',
		null=True,
		blank=True,
	)
	mese_riferimento = models.CharField(max_length=7, help_text='Formato YYYY-MM')
	ruolo_riferimento = models.CharField(max_length=80, blank=True, help_text='Chiave ruolo simulato (es. ruolo_1)')

	giorni_presenza = models.DecimalField(max_digits=6, decimal_places=2, default=0)
	giorni_assenza = models.DecimalField(max_digits=6, decimal_places=2, default=0)
	giorni_ferie = models.DecimalField(max_digits=6, decimal_places=2, default=0)
	giorni_riposo_compensativo = models.DecimalField(max_digits=6, decimal_places=2, default=0)

	ore_straordinario_diurno = models.DecimalField(max_digits=8, decimal_places=2, default=0)
	ore_straordinario_notturno = models.DecimalField(max_digits=8, decimal_places=2, default=0)
	ore_straordinario_festivo = models.DecimalField(max_digits=8, decimal_places=2, default=0)
	ore_lavoro_domenicale = models.DecimalField(max_digits=8, decimal_places=2, default=0)
	ore_lavoro_festivo = models.DecimalField(max_digits=8, decimal_places=2, default=0)

	applica_chiusure_aziendali = models.BooleanField(default=True)
	includi_ratei_nel_netto = models.BooleanField(default=False)
	giorni_json = models.JSONField(default=list, blank=True, help_text='Dettaglio giornaliero opzionale per integrazioni future')
	note = models.TextField(blank=True)
	data_creazione = models.DateTimeField(auto_now_add=True)
	data_modifica = models.DateTimeField(auto_now=True)

	class Meta:
		verbose_name = 'Calendario presenze dipendente'
		verbose_name_plural = 'Calendari presenze dipendenti'
		ordering = ['-mese_riferimento', 'azienda', 'dipendente__cognome', 'ruolo_riferimento']
		constraints = [
			models.UniqueConstraint(
				fields=['azienda', 'dipendente', 'mese_riferimento'],
				name='uniq_cal_presenze_azienda_dip_mese',
			),
			models.UniqueConstraint(
				fields=['azienda', 'ruolo_riferimento', 'mese_riferimento'],
				condition=~models.Q(ruolo_riferimento=''),
				name='uniq_cal_presenze_azienda_ruolo_mese',
			),
		]

	def __str__(self):
		target = self.dipendente and str(self.dipendente) or self.ruolo_riferimento or 'N/D'
		return f"{self.azienda.nome} - {self.mese_riferimento} - {target}"


class CalendarioLavoroMensile(models.Model):
	"""
	Configurazione giorni lavorativi per mese, per azienda e anno.

	Definisce quali giorni della settimana sono di chiusura (riposo settimanale)
	per ogni singolo mese — può variare mese per mese (es. estate vs inverno).

	Usato da: simulazione 2026, gestione presenze.

	chiusura_settimanale: lista di interi 0=Lun, 1=Mar, ..., 6=Dom
	  es. [6] = solo domenica chiusa
	      [5, 6] = sabato e domenica chiusi
	"""
	MESE_CHOICES = [
		(1, 'Gennaio'), (2, 'Febbraio'), (3, 'Marzo'),
		(4, 'Aprile'), (5, 'Maggio'), (6, 'Giugno'),
		(7, 'Luglio'), (8, 'Agosto'), (9, 'Settembre'),
		(10, 'Ottobre'), (11, 'Novembre'), (12, 'Dicembre'),
	]

	azienda = models.ForeignKey(
		Azienda,
		on_delete=models.CASCADE,
		related_name='calendario_lavoro_mensile',
	)
	anno = models.PositiveIntegerField()
	mese = models.PositiveSmallIntegerField(choices=MESE_CHOICES)
	# Giorni di chiusura settimanale: [0=Lun, 1=Mar, ..., 6=Dom]
	chiusura_settimanale = models.JSONField(
		default=list,
		help_text='Lista di interi: 0=Lun, 1=Mar, 2=Mer, 3=Gio, 4=Ven, 5=Sab, 6=Dom',
	)
	note = models.CharField(max_length=200, blank=True)
	data_modifica = models.DateTimeField(auto_now=True)

	class Meta:
		verbose_name = 'Calendario lavoro mensile'
		verbose_name_plural = 'Calendari lavoro mensili'
		unique_together = ('azienda', 'anno', 'mese')
		ordering = ['anno', 'mese']

	def __str__(self):
		mese_nome = dict(self.MESE_CHOICES).get(self.mese, str(self.mese))
		return f"{self.azienda.nome} — {mese_nome} {self.anno}"

	def giorni_chiusura_nomi(self):
		"""Nomi leggibili dei giorni di chiusura."""
		nomi = ['Lun', 'Mar', 'Mer', 'Gio', 'Ven', 'Sab', 'Dom']
		return [nomi[d] for d in (self.chiusura_settimanale or []) if 0 <= d <= 6]


# ============================================================
# PARAMETRI CCNL PARAMETRIZZATI (DATABASE)
# Struttura generica per supportare FIPE e future estensioni
# ============================================================

class CCNL(models.Model):
	"""Definizione metadati CCNL: nome, orario standard, mensilità, validità temporale."""
	SIGLA_CCNL = [
		('FIPE', 'Turismo - FIPE (Ristoranti, Hotel, Bar)'),
		('COMMERCIO', 'Commercio'),
		('INDUSTRIA', 'Industria Metalmeccanica'),
		('EDILIZIA', 'Edilizia'),
		('TRASPORTI', 'Trasporti'),
		('AGRICOLTURA', 'Agricoltura'),
		('SANITA_PRIVATA', 'Sanità Privata'),
		('CHIMICI', 'Chimici'),
		('TESSILE', 'Tessile'),
		('ALIMENTARE', 'Alimentare'),
	]
	
	nome = models.CharField(max_length=200, unique=True)
	sigla = models.CharField(max_length=50, choices=SIGLA_CCNL, unique=True)
	anno_inizio_validita = models.IntegerField()
	anno_fine_validita = models.IntegerField(null=True, blank=True)
	orario_standard_settimanale = models.DecimalField(max_digits=5, decimal_places=2, help_text='Es: 36, 40, 37.5')
	mensilita = models.IntegerField(default=12, help_text='12, 13 o 14 mensilità')
	giorni_ferie_base = models.IntegerField(default=20, help_text='Giorni ferie base annui')
	giorni_rol_base = models.IntegerField(default=0, help_text='Giorni ROL annui')
	descrizione = models.TextField(blank=True)
	attivo = models.BooleanField(default=True)
	data_creazione = models.DateTimeField(auto_now_add=True)
	data_modifica = models.DateTimeField(auto_now=True)
	
	class Meta:
		verbose_name = 'CCNL'
		verbose_name_plural = 'CCNL'
		ordering = ['sigla', '-anno_inizio_validita']
	
	def __str__(self):
		return f"{self.sigla} ({self.anno_inizio_validita}-{self.anno_fine_validita or 'Attuale'})"
	
	def is_current(self):
		"""Verifica se il CCNL è attualmente valido."""
		from datetime import datetime
		anno_corrente = datetime.now().year
		end_year = self.anno_fine_validita or 9999
		return self.anno_inizio_validita <= anno_corrente <= end_year

	def get_minimo_tabellare(self, livello: str, data: Optional[date] = None):
		"""
		Restituisce il minimo tabellare valido per il livello e la data.
		"""
		if data is None:
			data = date.today()
		qs = self.livelli_ccnl.filter(
			livello__iexact=livello,
			attivo=True,
			data_inizio__lte=data,
		).filter(models.Q(data_fine__gte=data) | models.Q(data_fine__isnull=True))
		minimo = qs.order_by('-data_inizio').first()
		if minimo:
			return minimo.minimo_tabellare
		fallback = self.livelli_ccnl.filter(livello__iexact=livello, attivo=True).order_by('-data_inizio').first()
		return getattr(fallback, 'minimo_tabellare', None)


class LivelloCCNL(models.Model):
	"""Minimo tabellare e validità storica per ogni livello di CCNL."""
	ccnl = models.ForeignKey(CCNL, on_delete=models.CASCADE, related_name='livelli_ccnl')
	livello = models.CharField(max_length=50)
	minimo_tabellare = models.DecimalField(max_digits=10, decimal_places=2)
	data_inizio = models.DateField(default=date.today)
	data_fine = models.DateField(null=True, blank=True)
	attivo = models.BooleanField(default=True)
	note = models.TextField(blank=True)
	data_creazione = models.DateTimeField(auto_now_add=True)
	data_modifica = models.DateTimeField(auto_now=True)

	class Meta:
		verbose_name = 'Livello CCNL'
		verbose_name_plural = 'Livelli CCNL'
		ordering = ['ccnl', 'livello', '-data_inizio']
		unique_together = ('ccnl', 'livello', 'data_inizio')

	def __str__(self):
		return f"{self.ccnl.sigla} {self.livello} [{self.data_inizio:%Y-%m-%d}]"


class EventoContrattuale(models.Model):
	"""Evento contrattuale legato a un rapporto di lavoro."""
	TIPO_SCELTE = [
		('promozione', 'Promozione'),
		('aspettativa', 'Aspettativa'),
		('licenziamento', 'Licenziamento'),
		('dimissioni', 'Dimissioni'),
	]

	rapporto = models.ForeignKey(
		'RapportoDiLavoro',
		on_delete=models.CASCADE,
		related_name='eventi_contrattuali',
	)
	tipo = models.CharField(max_length=30, choices=TIPO_SCELTE)
	data_evento = models.DateField()
	nuovo_livello = models.CharField(max_length=50, blank=True)
	nuovo_stipendio_lordo_mensile = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
	giorni_ferie_non_godute = models.DecimalField(max_digits=6, decimal_places=2, default=0)
	giorni_permessi_non_goduti = models.DecimalField(max_digits=6, decimal_places=2, default=0)
	giorni_preavviso = models.IntegerField(null=True, blank=True)
	valore_tfr = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
	parametri_specifici = models.JSONField(default=dict, blank=True)
	note = models.TextField(blank=True)
	data_creazione = models.DateTimeField(auto_now_add=True)
	data_modifica = models.DateTimeField(auto_now=True)

	class Meta:
		verbose_name = 'Evento contrattuale'
		verbose_name_plural = 'Eventi contrattuali'
		ordering = ['-data_evento', 'rapporto']

	def __str__(self):
		return f"{self.get_tipo_display()} {self.data_evento:%d/%m/%Y} - {self.rapporto}"


class Transizione(models.Model):
	"""Transizione contrattuale del rapporto: livello, retribuzione e validità."""
	rapporto = models.ForeignKey(
		'RapportoDiLavoro',
		on_delete=models.CASCADE,
		related_name='transizioni',
	)
	data_inizio = models.DateField()
	data_fine = models.DateField(null=True, blank=True)
	nuovo_livello = models.CharField(max_length=50)
	nuovo_stipendio_lordo_mensile = models.DecimalField(max_digits=10, decimal_places=2)
	nuovo_ore_settimanali = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)
	note = models.TextField(blank=True)
	data_creazione = models.DateTimeField(auto_now_add=True)
	data_modifica = models.DateTimeField(auto_now=True)

	class Meta:
		verbose_name = 'Transizione contrattuale'
		verbose_name_plural = 'Transizioni contrattuali'
		ordering = ['-data_inizio', 'rapporto']
		unique_together = ('rapporto', 'data_inizio')

	def __str__(self):
		return f"{self.rapporto} → {self.nuovo_livello} dal {self.data_inizio:%d/%m/%Y}"


class ParametroOrario(models.Model):
	"""Parametri orario per CCNL: limiti giornalieri, settimanali, mensili."""
	TIPO_CATEGORIA = [
		('giornaliero', 'Limite giornaliero'),
		('settimanale', 'Limite settimanale'),
		('mensile', 'Limite mensile'),
	]
	
	ccnl = models.ForeignKey(CCNL, on_delete=models.CASCADE, related_name='parametri_orario')
	tipo_categoria = models.CharField(max_length=50, choices=TIPO_CATEGORIA)
	tipo_contratto = models.CharField(max_length=50, help_text='Es: full_time, part_time')
	valore_minimo = models.DecimalField(max_digits=6, decimal_places=2)
	valore_massimo = models.DecimalField(max_digits=6, decimal_places=2)
	anno = models.IntegerField()
	data_validita_da = models.DateField()
	data_validita_a = models.DateField(null=True, blank=True)
	descrizione = models.TextField(blank=True)
	attivo = models.BooleanField(default=True)
	data_creazione = models.DateTimeField(auto_now_add=True)
	
	class Meta:
		verbose_name = 'Parametro orario'
		verbose_name_plural = 'Parametri orario'
		ordering = ['ccnl', '-anno', 'tipo_categoria', 'tipo_contratto']
		unique_together = ('ccnl', 'anno', 'tipo_categoria', 'tipo_contratto', 'data_validita_da')
	
	def __str__(self):
		return f"{self.ccnl.sigla} {self.anno} - {self.tipo_categoria} ({self.valore_minimo}-{self.valore_massimo}h)"


class ParametroMaggiorazione(models.Model):
	"""Maggiorazioni retributive per CCNL: festivo, notturno, straordinario, domenicale."""
	TIPO_MAGGIORAZIONE = [
		('straordinario_feriale', 'Straordinario feriale'),
		('straordinario_festivo', 'Straordinario festivo'),
		('straordinario_domenicale', 'Straordinario domenicale'),
		('straordinario_notturno', 'Straordinario notturno'),
		('straordinario_notturno_festivo', 'Straordinario notturno festivo'),
		('lavoro_festivo', 'Lavoro festivo'),
		('lavoro_domenicale', 'Lavoro domenicale'),
		('lavoro_notturno', 'Lavoro notturno'),
		('lavoro_supplementare_part_time', 'Lavoro supplementare part-time'),
	]
	
	ccnl = models.ForeignKey(CCNL, on_delete=models.CASCADE, related_name='parametri_maggiorazioni')
	tipo_maggiorazione = models.CharField(max_length=50, choices=TIPO_MAGGIORAZIONE)
	percentuale = models.DecimalField(max_digits=5, decimal_places=2, help_text='Es: 15.00 per 15%')
	anno = models.IntegerField()
	data_validita_da = models.DateField()
	data_validita_a = models.DateField(null=True, blank=True)
	descrizione = models.TextField(blank=True)
	attivo = models.BooleanField(default=True)
	data_creazione = models.DateTimeField(auto_now_add=True)
	
	class Meta:
		verbose_name = 'Parametro maggiorazione'
		verbose_name_plural = 'Parametri maggiorazioni'
		ordering = ['ccnl', '-anno', 'tipo_maggiorazione']
		unique_together = ('ccnl', 'anno', 'tipo_maggiorazione', 'data_validita_da')
	
	def __str__(self):
		return f"{self.ccnl.sigla} {self.anno} - {self.tipo_maggiorazione} {self.percentuale}%"


class ParametroScattiAnnuali(models.Model):
	"""Scatti di anzianità per livello/CCNL."""
	ccnl = models.ForeignKey(CCNL, on_delete=models.CASCADE, related_name='parametri_scatti')
	livello = models.CharField(max_length=100, help_text='Es: L1, L2, L3 o nome livello')
	anni_anzianita = models.IntegerField(help_text='Anni di anzianità per cui si applica')
	importo_scatto = models.DecimalField(max_digits=10, decimal_places=2, help_text='Importo mensile dello scatto')
	anno = models.IntegerField()
	data_validita_da = models.DateField()
	data_validita_a = models.DateField(null=True, blank=True)
	descrizione = models.TextField(blank=True)
	attivo = models.BooleanField(default=True)
	data_creazione = models.DateTimeField(auto_now_add=True)
	
	class Meta:
		verbose_name = 'Parametro scatto annuale'
		verbose_name_plural = 'Parametri scatti annuali'
		ordering = ['ccnl', '-anno', 'livello', 'anni_anzianita']
		unique_together = ('ccnl', 'anno', 'livello', 'anni_anzianita', 'data_validita_da')
	
	def __str__(self):
		return f"{self.ccnl.sigla} {self.anno} - L{self.livello} Anzianità {self.anni_anzianita}a: €{self.importo_scatto}"


class ParametroContributi(models.Model):
	"""Aliquote INPS/INAIL per CCNL e categoria azienda."""
	TIPO_CONTRIBUTO = [
		('inps', 'INPS'),
		('inail', 'INAIL'),
	]
	
	ccnl = models.ForeignKey(CCNL, on_delete=models.CASCADE, related_name='parametri_contributi')
	tipo_contributo = models.CharField(max_length=20, choices=TIPO_CONTRIBUTO)
	categoria = models.CharField(max_length=100, help_text='Es: piccola_ristorazione, media, grande')
	aliquota_azienda = models.DecimalField(max_digits=5, decimal_places=2, help_text='Aliquota azienda %')
	aliquota_dipendente = models.DecimalField(max_digits=5, decimal_places=2, default=0, help_text='Aliquota dipendente % (se applicabile)')
	anno = models.IntegerField()
	data_validita_da = models.DateField()
	data_validita_a = models.DateField(null=True, blank=True)
	descrizione = models.TextField(blank=True)
	attivo = models.BooleanField(default=True)
	data_creazione = models.DateTimeField(auto_now_add=True)
	
	class Meta:
		verbose_name = 'Parametro contributo'
		verbose_name_plural = 'Parametri contributi'
		ordering = ['ccnl', '-anno', 'tipo_contributo', 'categoria']
		unique_together = ('ccnl', 'anno', 'tipo_contributo', 'categoria', 'data_validita_da')
	
	def __str__(self):
		return f"{self.ccnl.sigla} {self.anno} - {self.tipo_contributo} {self.categoria}: Az {self.aliquota_azienda}% Dip {self.aliquota_dipendente}%"


class ParametroRatei(models.Model):
	"""Coefficienti ratei: TFR, 13ª, 14ª, indennità e altre quote parasalariali."""
	TIPO_RATEO = [
		('tfr', 'TFR (Trattamento Fine Rapporto)'),
		('tredicesima', 'Tredicesima mensilità'),
		('quattordicesima', 'Quattordicesima mensilità'),
		('ferie', 'Rateo ferie'),
		('permessi', 'Rateo permessi / ROL'),
		('indennita_ferie', 'Indennità ferie non godute'),
		('indennita_licenziamento', 'Indennità licenziamento'),
		('preavviso', 'Preavviso'),
	]
	
	ccnl = models.ForeignKey(CCNL, on_delete=models.CASCADE, related_name='parametri_ratei')
	tipo_rateo = models.CharField(max_length=50, choices=TIPO_RATEO)
	coefficiente = models.DecimalField(max_digits=7, decimal_places=4, help_text='Coefficiente calcolo (Es: 6.41 per TFR)')
	anno = models.IntegerField()
	data_validita_da = models.DateField()
	data_validita_a = models.DateField(null=True, blank=True)
	note = models.TextField(blank=True)
	attivo = models.BooleanField(default=True)
	data_creazione = models.DateTimeField(auto_now_add=True)
	
	class Meta:
		verbose_name = 'Parametro rateo'
		verbose_name_plural = 'Parametri ratei'
		ordering = ['ccnl', '-anno', 'tipo_rateo']
		unique_together = ('ccnl', 'anno', 'tipo_rateo', 'data_validita_da')
	
	def __str__(self):
		return f"{self.ccnl.sigla} {self.anno} - {self.get_tipo_rateo_display()}: {self.coefficiente}"


class ValidazioneOrario(models.Model):
	"""Validazioni limiti orario per CCNL/categoria: assicurano conformità normativa."""
	TIPO_CATEGORIA = [
		('full_time', 'Full-time'),
		('part_time', 'Part-time'),
		('stagionale', 'Stagionale'),
		('apprendista', 'Apprendista'),
	]
	
	ccnl = models.ForeignKey(CCNL, on_delete=models.CASCADE, related_name='validazioni_orario')
	tipo_categoria = models.CharField(max_length=50, choices=TIPO_CATEGORIA)
	min_ore_giornaliere = models.DecimalField(max_digits=5, decimal_places=2)
	max_ore_giornaliere = models.DecimalField(max_digits=5, decimal_places=2)
	min_ore_settimanali = models.DecimalField(max_digits=6, decimal_places=2)
	max_ore_settimanali = models.DecimalField(max_digits=6, decimal_places=2)
	min_ore_mensili = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True)
	max_ore_mensili = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True)
	anno = models.IntegerField()
	data_validita_da = models.DateField()
	data_validita_a = models.DateField(null=True, blank=True)
	note = models.TextField(blank=True)
	attivo = models.BooleanField(default=True)
	data_creazione = models.DateTimeField(auto_now_add=True)
	
	class Meta:
		verbose_name = 'Validazione orario'
		verbose_name_plural = 'Validazioni orario'
		ordering = ['ccnl', '-anno', 'tipo_categoria']
		unique_together = ('ccnl', 'anno', 'tipo_categoria', 'data_validita_da')
	
	def __str__(self):
		return f"{self.ccnl.sigla} {self.anno} - {self.tipo_categoria}"


class TipoAssenza(models.Model):
	"""Tipologie assenze e relative regole di trattamento per CCNL."""
	TIPO_ASSENZA = [
		('malattia', 'Malattia'),
		('infortunio', 'Infortunio'),
		('maternita', 'Maternità'),
		('paternita', 'Paternità'),
		('congedo_parentale', 'Congedo parentale'),
		('ferie', 'Ferie'),
		('permesso_retribuito', 'Permesso retribuito'),
		('permesso_non_retribuito', 'Permesso non retribuito'),
		('assenza_ingiustificata', 'Assenza ingiustificata'),
		('104', 'Legge 104'),
		('rol', 'ROL (Riposi compensativi)'),
	]
	
	ccnl = models.ForeignKey(CCNL, on_delete=models.CASCADE, related_name='tipi_assenza')
	tipo_assenza = models.CharField(max_length=50, choices=TIPO_ASSENZA)
	carica_inps = models.BooleanField(default=False, help_text='Conteggiata come contribuzione INPS')
	retribuzione_percentuale = models.DecimalField(max_digits=5, decimal_places=2, default=100, help_text='Percentuale retribuzione (100=intero stipendio)')
	giorni_max_anno = models.IntegerField(null=True, blank=True, help_text='Limite giorni anno civile (null=illimitato)')
	anno = models.IntegerField()
	data_validita_da = models.DateField()
	data_validita_a = models.DateField(null=True, blank=True)
	descrizione = models.TextField(blank=True)
	attivo = models.BooleanField(default=True)
	data_creazione = models.DateTimeField(auto_now_add=True)
	
	class Meta:
		verbose_name = 'Tipo assenza'
		verbose_name_plural = 'Tipi assenza'
		ordering = ['ccnl', '-anno', 'tipo_assenza']
		unique_together = ('ccnl', 'anno', 'tipo_assenza', 'data_validita_da')
	
	def __str__(self):
		return f"{self.ccnl.sigla} {self.anno} - {self.get_tipo_assenza_display()}"


class DecontribuzioneParametro(models.Model):
	"""Parametri decontribuzione: incentivi regionali, sotto36, donne, etc."""
	TIPO_INCENTIVO = [
		('giovanile_under35', 'Incentivo assunzione under 35'),
		('giovanile_under36', 'Incentivo assunzione under 36'),
		('donne_svantaggiate', 'Incentivo assunzione donne'),
		('soggetti_fragili', 'Incentivo assunzione soggetti fragili'),
		('disoccupati_lunga_durata', 'Incentivo disoccupati lunga durata'),
		('naspi', 'Percettore NASpI'),
		('reinserimento_lavorativo', 'Reinserimento lavorativo'),
		('territoriale', 'Decontribuzione territoriale'),
		('nessuno', 'Nessun incentivo'),
	]
	
	ccnl = models.ForeignKey(CCNL, on_delete=models.CASCADE, related_name='parametri_decontribuzione')
	tipo_incentivo = models.CharField(max_length=50, choices=TIPO_INCENTIVO)
	regione = models.CharField(max_length=100, null=True, blank=True, help_text='Regione interessata (null=nazionale)')
	provincia = models.CharField(max_length=100, null=True, blank=True)
	categoria = models.CharField(max_length=100, null=True, blank=True)
	tipo_contratto = models.CharField(max_length=100, null=True, blank=True, help_text='Es: full_time, part_time')
	eta_minima = models.IntegerField(null=True, blank=True)
	eta_massima = models.IntegerField(null=True, blank=True)
	percentuale_sconto = models.DecimalField(max_digits=5, decimal_places=2, help_text='Percentuale sconto INPS (Es: 50 per 50%)')
	priorita = models.IntegerField(default=0, help_text='Priorità selezione (più alto = prioritario)')
	anno = models.IntegerField()
	data_validita_da = models.DateField()
	data_validita_a = models.DateField(null=True, blank=True)
	descrizione = models.TextField(blank=True)
	attivo = models.BooleanField(default=True)
	data_creazione = models.DateTimeField(auto_now_add=True)
	
	class Meta:
		verbose_name = 'Parametro decontribuzione'
		verbose_name_plural = 'Parametri decontribuzione'
		ordering = ['ccnl', '-anno', '-priorita', 'tipo_incentivo']
	
	def __str__(self):
		regione_str = f" - {self.regione}" if self.regione else " - Nazionale"
		return f"{self.ccnl.sigla} {self.anno} - {self.get_tipo_incentivo_display()}{regione_str} ({self.percentuale_sconto}%)"


class FringeBenefitSoglia(models.Model):
	"""Soglie fringe benefit per CCNL: limiti esclusi da IRPEF."""
	ccnl = models.ForeignKey(CCNL, on_delete=models.CASCADE, related_name='fringe_benefit_soglie')
	anno = models.IntegerField()
	soglia_importo = models.DecimalField(max_digits=10, decimal_places=2, help_text='Soglia esclusa da IRPEF (Es: 3000.00)')
	tipo_benefit = models.CharField(max_length=100, help_text='Es: mensa, trasporto, buoni, carburante, etc.')
	data_validita_da = models.DateField()
	data_validita_a = models.DateField(null=True, blank=True)
	descrizione = models.TextField(blank=True)
	attivo = models.BooleanField(default=True)
	data_creazione = models.DateTimeField(auto_now_add=True)
	
	class Meta:
		verbose_name = 'Fringe benefit soglia'
		verbose_name_plural = 'Fringe benefit soglie'
		ordering = ['ccnl', '-anno', 'tipo_benefit']
		unique_together = ('ccnl', 'anno', 'tipo_benefit', 'data_validita_da')
	
	def __str__(self):
		return f"{self.ccnl.sigla} {self.anno} - {self.tipo_benefit}: €{self.soglia_importo}"


class ScaglioneIRPEF(models.Model):
	"""Scaglioni IRPEF per calcolo imposta sul reddito."""
	anno = models.IntegerField(help_text='Anno fiscale di riferimento')
	scaglione_numero = models.IntegerField(help_text='Numero progressivo scaglione (1, 2, 3, 4)')
	reddito_da = models.DecimalField(max_digits=12, decimal_places=2, help_text='Limite inferiore reddito imponibile')
	reddito_a = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True, help_text='Limite superiore reddito imponibile (null = infinito)')
	aliquota = models.DecimalField(max_digits=5, decimal_places=2, help_text='Aliquota % (es: 23.00 per 23%)')
	detrazione_base_annua = models.DecimalField(max_digits=10, decimal_places=2, default=0, help_text='Detrazione base annua per lavoro dipendente')
	data_validita_da = models.DateField()
	data_validita_a = models.DateField(null=True, blank=True)
	attivo = models.BooleanField(default=True)
	data_creazione = models.DateTimeField(auto_now_add=True)
	
	class Meta:
		verbose_name = 'Scaglione IRPEF'
		verbose_name_plural = 'Scaglioni IRPEF'
		ordering = ['anno', 'scaglione_numero']
		unique_together = ('anno', 'scaglione_numero')
	
	def __str__(self):
		from accounts.formatting import euro_it_str
		limite_sup = f'€ {euro_it_str(self.reddito_a)}' if self.reddito_a else '∞'
		return f'IRPEF {self.anno} - Scaglione {self.scaglione_numero}: € {euro_it_str(self.reddito_da)} - {limite_sup} ({self.aliquota}%)'


class DetrazioneLavoroDipendente(models.Model):
	"""Parametri detrazioni lavoro dipendente (art. 13 TUIR) per anno fiscale."""
	anno = models.IntegerField(help_text='Anno fiscale di riferimento')
	reddito_da = models.DecimalField(max_digits=12, decimal_places=2)
	reddito_a = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
	importo_base_annuo = models.DecimalField(max_digits=10, decimal_places=2, default=0)
	coefficiente_variabile_annuo = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
	reddito_riferimento = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
	divisore_fascia = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
	data_validita_da = models.DateField()
	data_validita_a = models.DateField(null=True, blank=True)
	attivo = models.BooleanField(default=True)
	data_creazione = models.DateTimeField(auto_now_add=True)

	class Meta:
		verbose_name = 'Detrazione lavoro dipendente'
		verbose_name_plural = 'Detrazioni lavoro dipendente'
		ordering = ['anno', 'reddito_da']
		unique_together = ('anno', 'reddito_da', 'data_validita_da')

	def __str__(self):
		from accounts.formatting import euro_it_str
		limite_sup = f'€ {euro_it_str(self.reddito_a)}' if self.reddito_a else '∞'
		return f'Detrazioni {self.anno}: € {euro_it_str(self.reddito_da)} - {limite_sup}'


class AddizionaleRegionale(models.Model):
	"""Addizionale regionale IRPEF."""
	regione = models.CharField(max_length=100, help_text='Nome regione (es: Lombardia, Lazio)')
	anno = models.IntegerField()
	aliquota = models.DecimalField(max_digits=5, decimal_places=3, help_text='Aliquota % addizionale regionale')
	soglia_esenzione = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True, help_text='Soglia reddito sotto cui non si applica')
	data_validita_da = models.DateField()
	data_validita_a = models.DateField(null=True, blank=True)
	attivo = models.BooleanField(default=True)
	data_creazione = models.DateTimeField(auto_now_add=True)
	
	class Meta:
		verbose_name = 'Addizionale regionale IRPEF'
		verbose_name_plural = 'Addizionali regionali IRPEF'
		ordering = ['anno', 'regione']
		unique_together = ('anno', 'regione')
	
	def __str__(self):
		return f"Add. Reg. {self.anno} - {self.regione}: {self.aliquota}%"


class AddizionaleComunale(models.Model):
	"""Addizionale comunale IRPEF."""
	comune = models.CharField(max_length=100, help_text='Nome comune')
	provincia = models.CharField(max_length=2, help_text='Sigla provincia')
	anno = models.IntegerField()
	aliquota = models.DecimalField(max_digits=5, decimal_places=3, help_text='Aliquota % addizionale comunale')
	soglia_esenzione = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True, help_text='Soglia reddito sotto cui non si applica')
	data_validita_da = models.DateField()
	data_validita_a = models.DateField(null=True, blank=True)
	attivo = models.BooleanField(default=True)
	data_creazione = models.DateTimeField(auto_now_add=True)
	
	class Meta:
		verbose_name = 'Addizionale comunale IRPEF'
		verbose_name_plural = 'Addizionali comunali IRPEF'
		ordering = ['anno', 'provincia', 'comune']
		unique_together = ('anno', 'comune', 'provincia')
	
	def __str__(self):
		return f"Add. Com. {self.anno} - {self.comune} ({self.provincia}): {self.aliquota}%"


class BonusFiscale(models.Model):
	"""Bonus fiscali: trattamento integrativo DL 3/2020, bonus Renzi, L.207/2024, ecc."""
	TIPO_BONUS = [
		('trattamento_integrativo', 'Trattamento Integrativo DL 3/2020'),
		('bonus_renzi', 'Bonus Renzi/Cuneo fiscale'),
		('bonus_200_2022', 'Bonus 200€ (L.197/2022)'),
		('bonus_150_2022', 'Bonus 150€ (DL 176/2022)'),
		('bonus_art1_l207', 'Bonus Art.1 L.207/2024'),
		('altro', 'Altro bonus fiscale'),
	]
	
	codice = models.CharField(max_length=50, help_text='Codice identificativo (es: TI_DL3_20, BONUS_207_24)')
	nome = models.CharField(max_length=200, help_text='Nome completo bonus')
	tipo = models.CharField(max_length=50, choices=TIPO_BONUS)
	anno = models.IntegerField()
	importo_mensile = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True, help_text='Importo mensile fisso (se applicabile)')
	importo_annuale = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True, help_text='Importo annuale fisso (se applicabile)')
	soglia_reddito_min = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True, help_text='Soglia minima reddito per applicabilità')
	soglia_reddito_max = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True, help_text='Soglia massima reddito per applicabilità')
	formula_calcolo = models.TextField(blank=True, help_text='Formula Python per calcolo dinamico (opzionale)')
	contribuisce_imponibile = models.BooleanField(default=False, help_text='Se True, concorre a formare imponibile contributivo')
	contribuisce_irpef = models.BooleanField(default=False, help_text='Se True, concorre a formare imponibile fiscale')
	data_validita_da = models.DateField()
	data_validita_a = models.DateField(null=True, blank=True)
	descrizione = models.TextField(blank=True)
	attivo = models.BooleanField(default=True)
	data_creazione = models.DateTimeField(auto_now_add=True)
	
	class Meta:
		verbose_name = 'Bonus fiscale'
		verbose_name_plural = 'Bonus fiscali'
		ordering = ['-anno', 'tipo', 'nome']
		unique_together = ('codice', 'anno')
	
	def __str__(self):
		return f"{self.nome} ({self.anno}) - {self.codice}"
	
	def calcola_importo(self, reddito_annuo):
		"""
		Calcola l'importo del bonus in base al reddito annuo.
		Restituisce Decimal o 0 se non applicabile.
		"""
		from decimal import Decimal
		
		# Verifica soglie reddito
		if self.soglia_reddito_min and reddito_annuo < self.soglia_reddito_min:
			return Decimal('0')
		if self.soglia_reddito_max and reddito_annuo > self.soglia_reddito_max:
			return Decimal('0')
		
		# Se c'è formula personalizzata, eseguila
		if self.formula_calcolo:
			try:
				# Contesto sicuro per eval
				context = {
					'reddito': float(reddito_annuo),
					'Decimal': Decimal,
				}
				risultato = eval(self.formula_calcolo, {"__builtins__": {}}, context)
				return Decimal(str(risultato))
			except Exception:
				# Fallback a importo fisso se formula non valida
				pass
		
		# Importo fisso
		if self.importo_mensile:
			return self.importo_mensile
		elif self.importo_annuale:
			return self.importo_annuale / Decimal('12')
		
		return Decimal('0')



# =============================================================================
# MODELLI PER MOTORI DI CALCOLO — aggiunto Marzo 2026
# =============================================================================

class VoceRetributiva(models.Model):
	"""
	Classificazione di ogni voce retributiva con i relativi flag di imponibilità.

	Ogni voce indica se concorre alla base INPS, INAIL e/o IRPEF, oppure se è
	esente in tutto o in parte (voci a trattamento speciale con franchigia).

	Riferimento normativo: art. 12 L. 153/1969 (oggi art. 51 TUIR) e circolari INPS.
	"""
	CATEGORIA_CHOICES = [
		('minimo_tabellare', 'Paga base / Minimo tabellare'),
		('contingenza', 'Contingenza / EDR'),
		('scatto_anzianita', 'Scatto di anzianità'),
		('superminimo', 'Superminimo'),
		('indennita_funzione', 'Indennità di funzione/ruolo'),
		('tredicesima', 'Tredicesima mensilità'),
		('quattordicesima', 'Quattordicesima mensilità'),
		('premio_risultato', 'Premio di risultato (tassazione ordinaria)'),
		('premio_agevolato', 'Premio di risultato (tassazione agevolata 5%)'),
		('straordinario', 'Straordinario / Maggiorazioni'),
		('indennita_turno', 'Indennità di turno'),
		('ferie_monetizzate', 'Ferie/permessi monetizzati'),
		('preavviso', 'Indennità mancato preavviso'),
		('trasferta', 'Trasferta (parzialmente esente)'),
		('fringe_benefit', 'Fringe benefit (parzialmente esente)'),
		('auto_aziendale', 'Auto aziendale uso promiscuo'),
		('ticket_restaurant', 'Ticket restaurant / Buoni pasto'),
		('rimborso_km', 'Rimborso km ACI (entro tabella)'),
		('rimborso_documentato', 'Rimborso spese piè di lista'),
		('bonus_fiscale', 'Bonus fiscale non imponibile (TI/L207)'),
		('previdenza_complementare', 'Contributo fondo pensione azienda'),
		('incentivo_esodo', 'Incentivo all\'esodo (tass. separata)'),
		('altro', 'Altra voce'),
	]

	codice = models.CharField(
		max_length=50, unique=True,
		help_text='Codice univoco (es: PAGA_BASE, SCATTO_ANZ, STRAORD_DIURNO)'
	)
	nome = models.CharField(max_length=200, help_text='Denominazione completa della voce')
	categoria = models.CharField(max_length=50, choices=CATEGORIA_CHOICES, default='altro')

	# ── Flag imponibilità ─────────────────────────────────────────────────────
	imponibile_inps = models.BooleanField(
		default=True,
		help_text='Se True, concorre alla base imponibile INPS'
	)
	imponibile_inail = models.BooleanField(
		default=True,
		help_text='Se True, concorre alla base imponibile INAIL'
	)
	imponibile_irpef = models.BooleanField(
		default=True,
		help_text='Se True, concorre alla base imponibile IRPEF'
	)

	# ── Trattamento speciale (franchigia) ─────────────────────────────────────
	imponibile_parziale = models.BooleanField(
		default=False,
		help_text=(
			'Se True, la voce è imponibile solo per la parte eccedente la franchigia. '
			'Collegare il campo codice_franchigia al relativo FranchigiaSogliaVoce.'
		)
	)
	codice_franchigia = models.CharField(
		max_length=50, blank=True,
		help_text='Codice FranchigiaSogliaVoce da applicare (se imponibile_parziale=True)'
	)

	# ── Tassazione agevolata (premi di risultato) ─────────────────────────────
	aliquota_agevolata = models.DecimalField(
		max_digits=5, decimal_places=2, null=True, blank=True,
		help_text='Aliquota sostitutiva % IRPEF (es: 5.00 per premi risultato agevolati)'
	)
	importo_massimo_agevolato_annuo = models.DecimalField(
		max_digits=12, decimal_places=2, null=True, blank=True,
		help_text='Importo massimo annuo soggetto all\'aliquota agevolata (es: 3000 €)'
	)

	# ── Metadati ──────────────────────────────────────────────────────────────
	descrizione = models.TextField(blank=True)
	riferimento_normativo = models.CharField(
		max_length=300, blank=True,
		help_text='Es: art. 51 TUIR c. 3 — Fringe benefit'
	)
	attivo = models.BooleanField(default=True)
	data_creazione = models.DateTimeField(auto_now_add=True)
	data_modifica = models.DateTimeField(auto_now=True)

	class Meta:
		verbose_name = 'Voce retributiva'
		verbose_name_plural = 'Voci retributive'
		ordering = ['categoria', 'codice']

	def __str__(self):
		stato = '' if self.attivo else ' [disattivata]'
		return f"{self.codice} — {self.nome}{stato}"

	@property
	def è_completamente_esente(self):
		"""True se la voce non concorre a nessuna base imponibile."""
		return not self.imponibile_inps and not self.imponibile_inail and not self.imponibile_irpef

	@property
	def descrizione_trattamento(self):
		"""Stringa descrittiva del trattamento fiscale/contributivo."""
		parti = []
		if self.imponibile_inps:
			parti.append('INPS')
		if self.imponibile_inail:
			parti.append('INAIL')
		if self.imponibile_irpef:
			parti.append('IRPEF')
		if not parti:
			return 'Non imponibile (esente da tutto)'
		base = f"Imponibile: {' + '.join(parti)}"
		if self.imponibile_parziale:
			base += ' (solo eccedenza franchigia)'
		if self.aliquota_agevolata is not None:
			base += f' — aliquota agevolata {self.aliquota_agevolata}%'
		return base


class MappaturaVoceMotore(models.Model):
	"""
	Tabella di collegamento certa: codice voce usato dal motore busta paga mensile
	↔ trattamento INPS / INAIL / IRPEF / maturazione 13ª·14ª / TFR.

	Usata per riconciliazione con buste e cedolini e per estendere il motore con nuove voci
	senza deploy di codice (righe attive in admin).
	"""
	codice_voce = models.CharField(
		max_length=50,
		unique=True,
		db_index=True,
		help_text='Codice univoco allineato al motore (es. MINIMO_TABELLARE, MAGG_DOM_FEST, STRAORD_DIURNO).',
	)
	voce_retributiva = models.ForeignKey(
		'VoceRetributiva',
		null=True,
		blank=True,
		on_delete=models.SET_NULL,
		related_name='mappature_motore',
		verbose_name='Voce retributiva (anagrafica)',
		help_text='Opzionale: collega alla scheda VoceRetributiva omonima o correlata.',
	)
	ordine_calcolo = models.PositiveSmallIntegerField(
		default=99,
		help_text='Ordine di presentazione / schema (1 = paga base oraria, …).',
	)
	imponibile_inps = models.BooleanField(default=True)
	imponibile_inail = models.BooleanField(default=True)
	imponibile_irpef = models.BooleanField(default=True)
	matura_tredicesima = models.BooleanField(default=True)
	matura_quattordicesima = models.BooleanField(default=True)
	concorre_tfr = models.BooleanField(default=True)
	etichetta_riconciliazione = models.CharField(
		max_length=200,
		blank=True,
		verbose_name='Etichetta riconciliazione',
		help_text='Es. etichetta busta paga / cedolino TeamSystem per controllo incrociato.',
	)
	note_riconciliazione = models.TextField(
		blank=True,
		verbose_name='Note per riconciliazione',
		help_text='Riferimenti normativi o regole aziendali applicate alla voce.',
	)
	attivo = models.BooleanField(default=True)
	data_creazione = models.DateTimeField(auto_now_add=True)
	data_modifica = models.DateTimeField(auto_now=True)

	class Meta:
		verbose_name = 'Mappatura voce motore paga'
		verbose_name_plural = 'Mappature voci motore paga'
		ordering = ['ordine_calcolo', 'codice_voce']

	def __str__(self):
		st = '' if self.attivo else ' [off]'
		return f'{self.codice_voce} (ord.{self.ordine_calcolo}){st}'


class ParametroVoceRetributiva(models.Model):
	"""
	Valorizzazione economica delle voci retributive per contesto contrattuale.

	Questa tabella contiene gli importi "dati" usati dalla simulazione:
	- importo mensile (es. minimo tabellare, contingenza, superminimo, scatto)
	- importo orario (es. EL.DIS.SAN, EL.DIS.BIL)
	"""
	voce = models.ForeignKey(VoceRetributiva, on_delete=models.PROTECT, related_name='parametri_valore')
	ccnl = models.CharField(max_length=150, default='Turismo Confcommercio')
	versione = models.CharField(max_length=50, default='2024-2026')
	sezione = models.CharField(max_length=50, choices=ParametroCCNLTurismo.SEZIONE_CHOICES, default='ristoranti_pizzerie')
	livello = models.CharField(max_length=20)
	anno = models.IntegerField(default=_anno_corrente)

	importo_mensile = models.DecimalField(max_digits=12, decimal_places=2, default=0)
	importo_orario = models.DecimalField(max_digits=12, decimal_places=5, default=0)

	data_validita_da = models.DateField(null=True, blank=True)
	data_validita_a = models.DateField(null=True, blank=True)
	note = models.TextField(blank=True)
	attivo = models.BooleanField(default=True)
	data_creazione = models.DateTimeField(auto_now_add=True)
	data_modifica = models.DateTimeField(auto_now=True)

	class Meta:
		verbose_name = 'Parametro voce retributiva'
		verbose_name_plural = 'Parametri voci retributive'
		ordering = ['ccnl', 'versione', 'sezione', 'livello', 'voce__codice', '-anno']
		unique_together = ('voce', 'ccnl', 'versione', 'sezione', 'livello', 'anno')

	def __str__(self):
		return f"{self.ccnl} {self.versione} {self.sezione} L{self.livello} · {self.voce.codice}"


class FranchigiaSogliaVoce(models.Model):
	"""
	Soglie di franchigia per voci retributive parzialmente imponibili.

	Tipologie gestite:
	  - Trasferte Italia/Estero (art. 51 c. 5 TUIR)
	  - Fringe benefit generali e con figli a carico (art. 51 c. 3 TUIR)
	  - Ticket restaurant cartacei ed elettronici (art. 51 c. 2 lett. c TUIR)
	  - Rimborso km ACI (entro tabelle)
	"""
	TIPO_CHOICES = [
		('trasferta_italia', 'Trasferta Italia — diaria giornaliera'),
		('trasferta_estero', 'Trasferta Estero — diaria giornaliera'),
		('fringe_benefit_generale', 'Fringe benefit — soglia annua (senza figli)'),
		('fringe_benefit_con_figli', 'Fringe benefit — soglia annua (con figli a carico)'),
		('ticket_cartaceo', 'Ticket restaurant cartaceo — giornaliero'),
		('ticket_elettronico', 'Ticket restaurant elettronico — giornaliero'),
		('rimborso_km_aci', 'Rimborso km ACI — per km (entro tabella)'),
		('altro', 'Altra franchigia'),
	]
	UNITA_CHOICES = [
		('giorno', 'Per giorno'),
		('anno', 'Per anno'),
		('km', 'Per km'),
		('pasto', 'Per pasto'),
		('mese', 'Per mese'),
	]

	codice = models.CharField(
		max_length=50, unique=True,
		help_text='Codice univoco (es: TRASFERTA_ITA_2025, FB_FIGLI_2025)'
	)
	tipo = models.CharField(max_length=50, choices=TIPO_CHOICES)
	anno = models.IntegerField(help_text='Anno fiscale di riferimento')
	importo = models.DecimalField(
		max_digits=12, decimal_places=2,
		help_text='Importo soglia esente per unità di misura indicata'
	)
	unita_misura = models.CharField(
		max_length=20, choices=UNITA_CHOICES, default='giorno',
		help_text='Unità di misura della soglia (giorno, anno, km, pasto)'
	)
	note = models.TextField(blank=True)
	riferimento_normativo = models.CharField(
		max_length=300, blank=True,
		help_text='Es: art. 51 c. 5 TUIR — L. 213/2023 art. 1 c. 16'
	)
	data_validita_da = models.DateField()
	data_validita_a = models.DateField(null=True, blank=True)
	attivo = models.BooleanField(default=True)
	data_creazione = models.DateTimeField(auto_now_add=True)

	class Meta:
		verbose_name = 'Franchigia / Soglia voce retributiva'
		verbose_name_plural = 'Franchigie / Soglie voci retributive'
		ordering = ['-anno', 'tipo']
		unique_together = ('tipo', 'anno')

	def __str__(self):
		return (
			f"{self.get_tipo_display()} {self.anno}: "
			f"€{self.importo}/{self.get_unita_misura_display()}"
		)


class InailParametro(models.Model):
	"""
	Massimali e minimali INAIL per il calcolo della base dei premi assicurativi.

	L'INAIL prevede (art. 30 DPR 1124/1965 + circolari INAIL annuali):
	  - Minimale giornaliero: se la retribuzione giornaliera effettiva è inferiore
	    al minimale, si usa il minimale per il calcolo del premio.
	  - Massimale giornaliero: limite superiore alla retribuzione su cui si calcola
	    il premio (evita l'applicazione su retribuzioni molto alte).
	  - Retribuzione convenzionale: per alcune categorie (es. lavoratori con orario
	    ridotto) può essere prevista una retribuzione convenzionale ai fini INAIL.

	Il premio INAIL è a totale carico dell'azienda (non impatta il netto del dip.).
	"""

	ccnl = models.ForeignKey(
		CCNL, on_delete=models.CASCADE, related_name='parametri_inail',
		help_text='CCNL di riferimento'
	)
	anno = models.IntegerField(help_text='Anno di riferimento')

	# Minimale giornaliero
	retribuzione_giornaliera_minima = models.DecimalField(
		max_digits=10, decimal_places=2,
		help_text=(
			'Minimale retribuzione giornaliera INAIL: se la retribuzione effettiva '
			'giornaliera è inferiore a questo importo, si usa questo valore per il calcolo '
			'del premio (circ. INAIL annuale)'
		)
	)

	# Massimale giornaliero (opzionale)
	retribuzione_giornaliera_massima = models.DecimalField(
		max_digits=10, decimal_places=2, null=True, blank=True,
		help_text='Massimale retribuzione giornaliera INAIL (opzionale)'
	)

	# Massimale annuo (opzionale — art. 116 DPR 1124/1965)
	retribuzione_annua_massima = models.DecimalField(
		max_digits=12, decimal_places=2, null=True, blank=True,
		help_text='Massimale annuo retribuzione imponibile INAIL (opzionale)'
	)

	# Retribuzione convenzionale (per categorie speciali)
	retribuzione_convenzionale_giornaliera = models.DecimalField(
		max_digits=10, decimal_places=2, null=True, blank=True,
		help_text=(
			'Retribuzione convenzionale giornaliera per categorie speciali '
			'(es. lavoratori con part-time molto ridotto)'
		)
	)

	# Validità
	data_validita_da = models.DateField()
	data_validita_a = models.DateField(null=True, blank=True)
	descrizione = models.TextField(blank=True)
	attivo = models.BooleanField(default=True)
	data_creazione = models.DateTimeField(auto_now_add=True)

	class Meta:
		verbose_name = 'Parametro INAIL massimale/minimale'
		verbose_name_plural = 'Parametri INAIL massimali/minimali'
		ordering = ['ccnl', '-anno']
		unique_together = ('ccnl', 'anno', 'data_validita_da')

	def __str__(self):
		return (
			f"{self.ccnl.sigla} {self.anno} — "
			f"Min. giorn.: €{self.retribuzione_giornaliera_minima}"
		)


class RiepilogoMensileDipendente(models.Model):
	"""
	Tabella riepilogo generale mensile per singolo dipendente.

	Obiettivo: avere in un'unica riga tutte le informazioni utili per capire:
	  - quanto pagare al dipendente (netto),
	  - quanto versare a INPS, INAIL ed Erario,
	  - quali accantonamenti/ratei lordi e netti maturano nel mese,
	  - impatti di decontribuzioni e crediti.
	"""
	azienda = models.ForeignKey(
		Azienda,
		on_delete=models.CASCADE,
		related_name='riepiloghi_mensili_dipendenti',
	)
	dipendente = models.ForeignKey(
		Dipendente,
		on_delete=models.CASCADE,
		related_name='riepiloghi_mensili',
	)

	anno = models.IntegerField()
	mese = models.IntegerField(help_text='1=gennaio ... 12=dicembre')
	data_competenza = models.DateField(help_text='Primo giorno del mese di competenza')

	# Imponibili
	imponibile_inps = models.DecimalField(max_digits=12, decimal_places=2, default=0)
	imponibile_inail = models.DecimalField(max_digits=12, decimal_places=2, default=0)
	imponibile_irpef = models.DecimalField(max_digits=12, decimal_places=2, default=0)

	# Imposte/contributi dipendente
	inps_dipendente = models.DecimalField(max_digits=12, decimal_places=2, default=0)
	irpef_lorda = models.DecimalField(max_digits=12, decimal_places=2, default=0)
	detrazioni = models.DecimalField(max_digits=12, decimal_places=2, default=0)
	irpef_netta = models.DecimalField(max_digits=12, decimal_places=2, default=0)
	addizionale_regionale = models.DecimalField(max_digits=12, decimal_places=2, default=0)
	addizionale_comunale = models.DecimalField(max_digits=12, decimal_places=2, default=0)

	# Oneri azienda
	inps_azienda = models.DecimalField(max_digits=12, decimal_places=2, default=0)
	inail = models.DecimalField(max_digits=12, decimal_places=2, default=0)

	# Netto e bonus
	trattamento_integrativo = models.DecimalField(max_digits=12, decimal_places=2, default=0)
	bonus_l207_2024 = models.DecimalField(max_digits=12, decimal_places=2, default=0)
	netto_da_pagare_dipendente = models.DecimalField(max_digits=12, decimal_places=2, default=0)

	# Versamenti enti (dopo crediti/sgravi)
	decontribuzione_inps = models.DecimalField(max_digits=12, decimal_places=2, default=0)
	crediti_inps = models.DecimalField(max_digits=12, decimal_places=2, default=0)
	crediti_inail = models.DecimalField(max_digits=12, decimal_places=2, default=0)
	crediti_erario = models.DecimalField(max_digits=12, decimal_places=2, default=0)

	versamento_inps_lordo = models.DecimalField(max_digits=12, decimal_places=2, default=0)
	versamento_inps_netto = models.DecimalField(max_digits=12, decimal_places=2, default=0)
	versamento_inail_netto = models.DecimalField(max_digits=12, decimal_places=2, default=0)
	versamento_erario_netto = models.DecimalField(max_digits=12, decimal_places=2, default=0)

	# Ratei / accantonamenti
	rateo_tredicesima = models.DecimalField(max_digits=12, decimal_places=2, default=0)
	rateo_quattordicesima = models.DecimalField(max_digits=12, decimal_places=2, default=0)
	tfr_mensile = models.DecimalField(max_digits=12, decimal_places=2, default=0)

	ratei_concorsi_in_imponibile = models.BooleanField(default=False)
	imposte_aggiuntive_ratei_dipendente = models.DecimalField(max_digits=12, decimal_places=2, default=0)
	imposte_aggiuntive_ratei_azienda = models.DecimalField(max_digits=12, decimal_places=2, default=0)
	imposte_aggiuntive_ratei_erario = models.DecimalField(max_digits=12, decimal_places=2, default=0)

	accantonamenti_lordi_mese = models.DecimalField(max_digits=12, decimal_places=2, default=0)
	accantonamenti_netti_stimati_mese = models.DecimalField(max_digits=12, decimal_places=2, default=0)

	# Totali finali controllo
	esborso_totale_azienda_mese = models.DecimalField(max_digits=12, decimal_places=2, default=0)
	note = models.TextField(blank=True)

	creato_il = models.DateTimeField(auto_now_add=True)
	aggiornato_il = models.DateTimeField(auto_now=True)

	class Meta:
		verbose_name = 'Riepilogo mensile dipendente'
		verbose_name_plural = 'Riepiloghi mensili dipendenti'
		ordering = ['-anno', '-mese', 'dipendente__cognome', 'dipendente__nome']
		unique_together = ('dipendente', 'anno', 'mese')

	def __str__(self):
		return f"{self.dipendente} — {self.anno}-{self.mese:02d}"


class RuoloOrganico2026(models.Model):
	"""Ruoli configurati per la Simulazione Organico 2026.
	Una riga per ogni ruolo per azienda. Viene aggiornata ad ogni salvataggio
	della simulazione, consentendo di riprendere e modificare i dati nelle sessioni successive.
	"""
	azienda = models.ForeignKey(
		Azienda,
		on_delete=models.CASCADE,
		related_name='ruoli_organico_2026',
	)
	ordinamento = models.PositiveSmallIntegerField(default=0)

	# Identificazione ruolo
	dipendente = models.ForeignKey(
		Dipendente,
		on_delete=models.SET_NULL,
		null=True,
		blank=True,
		related_name='ruoli_organico_2026',
	)
	stato_soggetto = models.CharField(max_length=20, blank=True, default='')
	mansione_label = models.CharField(max_length=120, blank=True, default='')
	nome = models.CharField(max_length=120, blank=True)
	quantita = models.PositiveSmallIntegerField(default=1)
	livello = models.CharField(max_length=20)
	tipo_contratto_id = models.CharField(max_length=20, blank=True)
	tipo_rapporto = models.CharField(
		max_length=30,
		default='indeterminato',
		choices=[
			('indeterminato', 'Indeterminato'),
			('determinato', 'Determinato'),
			('apprendistato', 'Apprendistato'),
		],
	)
	data_inizio = models.DateField(default=date(2026, 1, 1))
	data_fine = models.DateField(default=date(2026, 12, 31))

	# Agevolazioni / decontribuzioni
	regione = models.CharField(max_length=80, default='sicilia')
	eta = models.PositiveSmallIntegerField(null=True, blank=True)
	categoria = models.CharField(max_length=120, blank=True, null=True)
	percettore_naspi = models.BooleanField(null=True, blank=True)
	tipo_incentivo = models.CharField(max_length=120, blank=True, null=True)

	# Voci retributive variabili
	anni_anzianita = models.PositiveSmallIntegerField(default=0)
	superminimo = models.DecimalField(max_digits=10, decimal_places=2, default=0)
	indennita_turno = models.DecimalField(max_digits=10, decimal_places=2, default=0)
	premio_risultato_annuo = models.DecimalField(max_digits=10, decimal_places=2, default=0)

	# Straordinari/maggiorazioni mensili: {1: {ore_straord_diurno, ...}, ..., 12: {...}}
	calendario_mensile = models.JSONField(default=dict, blank=True)
	# Provenienza dati usati per precompilare il ruolo (attivi/candidati + contratti/proposte)
	origine_dati = models.CharField(
		max_length=20,
		default='manuale',
		choices=[
			('manuale', 'Manuale'),
			('auto_profilo', 'Autocompilato da profili'),
			('misto', 'Misto (auto + modifiche manuali)'),
		],
	)
	nominativi_riferimento = models.TextField(blank=True, default='')
	soggetti_riferimento = models.JSONField(default=list, blank=True)

	data_modifica = models.DateTimeField(auto_now=True)
	modificato_da = models.ForeignKey(
		User,
		on_delete=models.SET_NULL,
		null=True,
		blank=True,
		related_name='ruoli_organico_2026_modificati',
	)

	class Meta:
		verbose_name = 'Ruolo Organico 2026'
		verbose_name_plural = 'Ruoli Organico 2026'
		ordering = ['azienda', 'ordinamento', 'id']

	def __str__(self):
		return f"{self.nome or 'Ruolo'} L{self.livello} x{self.quantita} — {self.azienda}"


# ─────────────────────────────────────────────────────────────────────────────
# TABELLA TEMPORANEA DI TEST — verrà rimossa dopo la validazione del motore
# Scopo: sandbox per verificare e ottimizzare il motore di calcolo paga mensile
# unificato (azienda + dipendente) prima di agganciarlo alle viste in produzione.
# ─────────────────────────────────────────────────────────────────────────────
class TestMotorePaga(models.Model):
    """Sandbox per testare il motore di calcolo busta paga mensile.

    Permette di configurare tutti i parametri (CCNL, tipo contratto, divisore,
    giorni lavorativi, voci retributive, date) e verificare il risultato completo
    di imponibili, imposte, bonus e costi azienda — identico al motore della
    simulazione 2026 — prima di agganciarlo al lato dipendente.

    TABELLA TEMPORANEA: eliminare dopo la validazione del motore unificato.
    """

    DIVISORE_CHOICES = [
        (26, '26 — CCNL FIPE (riferimento mensile convenzionale)'),
        (30, '30 — Mese civile (30 giorni fissi)'),
    ]
    GIORNI_SETTIMANA_CHOICES = [
        (5, '5 giorni — Lun–Ven'),
        (6, '6 giorni — Lun–Sab (CCNL FIPE ristorazione)'),
        (7, '7 giorni — ciclo continuo'),
    ]
    STATO_CHOICES = [
        ('bozza', 'Bozza — parametri da compilare'),
        ('calcolato', 'Calcolato — risultati disponibili'),
    ]

    # ── Identificazione ──────────────────────────────────────────────────────
    nome_test = models.CharField(
        max_length=150, verbose_name='Nome scenario di test',
        help_text='Es. "Cameriere L4 full-time marzo 2026 — verifica TI e bonus L207"',
    )
    stato = models.CharField(max_length=20, choices=STATO_CHOICES, default='bozza')
    note = models.TextField(blank=True, verbose_name='Note e osservazioni')
    data_creazione = models.DateTimeField(auto_now_add=True)
    data_modifica = models.DateTimeField(auto_now=True)

    # ── Parametri: periodo e dipendente ─────────────────────────────────────
    mese_riferimento = models.CharField(
        max_length=7, verbose_name='Mese di riferimento (YYYY-MM)',
        help_text='Formato YYYY-MM — es. 2026-03',
    )
    data_inizio_rapporto = models.DateField(
        null=True, blank=True,
        verbose_name='Data inizio rapporto',
        help_text='Se nel mese di riferimento → calcola il pro-rata (regola CCNL: ≥15 gg = mese intero)',
    )

    # ── Parametri: CCNL e contratto ──────────────────────────────────────────
    parametro_ccnl = models.ForeignKey(
        'ParametroCCNLTurismo',
        on_delete=models.PROTECT,
        verbose_name='Livello CCNL',
        help_text='Seleziona il livello di riferimento — tutte le voci vengono lette da questa tabella',
    )
    tipo_contratto = models.ForeignKey(
        'TipoContratto',
        on_delete=models.PROTECT,
        verbose_name='Tipo di contratto / orario',
        help_text='Full-time (100%) o Part-time con coefficiente orario ridotto',
    )

    # ── Parametri: divisore e giorni ─────────────────────────────────────────
    divisore = models.IntegerField(
        choices=DIVISORE_CHOICES, default=26,
        verbose_name='Divisore convenzionale',
        help_text='Usato per calcolare la paga giornaliera (lordo ÷ divisore)',
    )
    giorni_lavorativi_settimana = models.IntegerField(
        choices=GIORNI_SETTIMANA_CHOICES, default=6,
        verbose_name='Giorni lavorativi/settimana',
    )
    giorni_chiusura_mese = models.IntegerField(
        default=0, verbose_name='Giorni di chiusura aziendale nel mese',
        help_text='Giorni di chiusura (non lavorativi non retribuiti) da escludere',
    )

    # ── Parametri: voci retributive aggiuntive ───────────────────────────────
    superminimo = models.DecimalField(
        max_digits=8, decimal_places=2, default=0,
        verbose_name='Superminimo individuale (€/mese)',
        help_text='Voce aggiuntiva concordata individualmente — imponibile INPS e IRPEF',
    )
    indennita_turno = models.DecimalField(
        max_digits=8, decimal_places=2, default=0,
        verbose_name='Indennità di turno/reperibilità (€/mese)',
        help_text='Voce aggiuntiva — imponibile INPS e IRPEF',
    )
    altre_voci_json = models.JSONField(
        default=dict, blank=True,
        verbose_name='Altre voci retributive (JSON)',
        help_text='Dizionario {"nome_voce": importo, ...} per voci extra da testare',
    )

    # ── Risultati: lordo e giorni ─────────────────────────────────────────────
    r_lordo_pieno = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True, verbose_name='Lordo mensile pieno (€)')
    r_lordo_mensile = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True, verbose_name='Lordo mensile pro-rata (€)')
    r_giorni_lavorati = models.IntegerField(null=True, blank=True, verbose_name='Giorni lavorati nel mese')
    r_giorni_mese = models.IntegerField(null=True, blank=True, verbose_name='Giorni totali nel mese')
    r_paga_giornaliera = models.DecimalField(max_digits=8, decimal_places=4, null=True, blank=True, verbose_name='Paga giornaliera (lordo ÷ divisore)')
    r_paga_oraria = models.DecimalField(max_digits=8, decimal_places=4, null=True, blank=True, verbose_name='Paga oraria (€/h)')

    # ── Risultati: dettaglio voci retributive ───────────────────────────────
    r_paga_base = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True, verbose_name='Paga base (€)')
    r_contingenza = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True, verbose_name='Contingenza (€)')
    r_edr = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True, verbose_name='EDR (€)')
    r_indennita = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True, verbose_name='Indennità mensile CCNL (€)')
    r_superminimo = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True, verbose_name='Superminimo (€)')
    r_indennita_turno = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True, verbose_name='Indennità turno (€)')
    r_altre_voci = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True, verbose_name='Altre voci (€)')

    # ── Risultati: INPS ───────────────────────────────────────────────────────
    r_inps_dip_perc = models.DecimalField(max_digits=6, decimal_places=4, null=True, blank=True, verbose_name='Aliquota INPS dipendente (%)')
    r_inps_az_perc = models.DecimalField(max_digits=6, decimal_places=4, null=True, blank=True, verbose_name='Aliquota INPS azienda (%)')
    r_inps_dipendente = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True, verbose_name='INPS c/dipendente (€)')
    r_inps_azienda = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True, verbose_name='INPS c/azienda (€)')
    r_inail_azienda = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True, verbose_name='INAIL c/azienda (€)')

    # ── Risultati: IRPEF ─────────────────────────────────────────────────────
    r_imponibile_mensile = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True, verbose_name='Imponibile IRPEF mensile (€)')
    r_imponibile_annuo = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True, verbose_name='Imponibile IRPEF annuo stimato (€)')
    r_irpef_lorda = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True, verbose_name='IRPEF lorda mensile (€)')
    r_detrazioni = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True, verbose_name='Detrazioni lavoro dipendente (€)')
    r_irpef_netta = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True, verbose_name='IRPEF netta mensile (€)')

    # ── Risultati: netto ─────────────────────────────────────────────────────
    r_netto_base = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True, verbose_name='Netto base (lordo − INPS − IRPEF)')
    r_trattamento_integrativo = models.DecimalField(max_digits=8, decimal_places=2, null=True, blank=True, verbose_name='Trattamento Integrativo DL 3/2020 (€)')
    r_bonus_l207 = models.DecimalField(max_digits=8, decimal_places=2, null=True, blank=True, verbose_name='Bonus L. 207/2024 (€)')
    r_netto_totale = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True, verbose_name='NETTO MENSILE IN BUSTA (€)')

    # ── Risultati: addizionali ───────────────────────────────────────────────
    r_addiz_reg_mensile = models.DecimalField(max_digits=8, decimal_places=2, null=True, blank=True, verbose_name='Add. regionale mensile stimata (€)')
    r_addiz_com_mensile = models.DecimalField(max_digits=8, decimal_places=2, null=True, blank=True, verbose_name='Add. comunale mensile stimata (€)')
    r_addiz_totale_annuo = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True, verbose_name='Addizionali totali annue stimate (€)')

    # ── Risultati: ratei e accantonamenti ────────────────────────────────────
    r_tfr_mensile = models.DecimalField(max_digits=8, decimal_places=2, null=True, blank=True, verbose_name='TFR accantonato mensile (€)')
    r_rateo_13_mensile = models.DecimalField(max_digits=8, decimal_places=2, null=True, blank=True, verbose_name='Rateo 13ª mensile (€)')
    r_rateo_14_mensile = models.DecimalField(max_digits=8, decimal_places=2, null=True, blank=True, verbose_name='Rateo 14ª mensile (€)')
    r_rateo_ferie_mensile = models.DecimalField(max_digits=8, decimal_places=2, null=True, blank=True, verbose_name='Rateo ferie mensile (€)')

    # ── Risultati: F24 e costo azienda ───────────────────────────────────────
    r_f24_inps = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True, verbose_name='F24 INPS totale (az + dip, €)')
    r_f24_erario = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True, verbose_name='F24 Erario (IRPEF − TI − L207, €)')
    r_costo_totale_azienda = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True, verbose_name='Costo totale azienda mensile (€)')
    r_costo_annuo_stimato = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True, verbose_name='Costo annuo stimato (€)')

    # ── Risultato completo (JSON per analisi e debug) ────────────────────────
    risultato_json = models.JSONField(default=dict, blank=True, verbose_name='Risultato completo (JSON)')

    class Meta:
        verbose_name = 'Test Motore Paga'
        verbose_name_plural = 'Test Motore Paga — Sandbox'
        ordering = ['-data_modifica']

    def __str__(self):
        stato_label = '✓' if self.stato == 'calcolato' else '…'
        return f"[{stato_label}] {self.nome_test} ({self.mese_riferimento})"


class SimulazionePagaSalvata(models.Model):
    """
    Salvataggio persistente di uno scenario del Simulatore Paga Mensile.
    Conserva i parametri di input (per ricaricamento) e i valori chiave
    del risultato (per visualizzazione rapida nella lista scenari).
    """
    nome          = models.CharField(max_length=200, verbose_name='Nome scenario')
    utente        = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='simulazioni_paga', verbose_name='Utente'
    )
    data_creazione = models.DateTimeField(auto_now_add=True, verbose_name='Data creazione')
    data_modifica  = models.DateTimeField(auto_now=True,     verbose_name='Ultima modifica')

    # Metadati per ricerca e visualizzazione rapida
    anno  = models.IntegerField(verbose_name='Anno')
    mese  = models.IntegerField(verbose_name='Mese')
    ccnl_livello         = models.CharField(max_length=50,  blank=True, verbose_name='Livello CCNL')
    ccnl_qualifica       = models.CharField(max_length=200, blank=True, verbose_name='Qualifica CCNL')
    tipo_contratto_nome  = models.CharField(max_length=100, blank=True, verbose_name='Tipo contratto')

    # Cifre chiave (denormalizzate per tabella riepilogativa)
    lordo_mensile = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True, verbose_name='Lordo mensile (€)')
    netto_totale  = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True, verbose_name='Netto in busta (€)')
    costo_mensile = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True, verbose_name='Costo azienda mensile (€)')

    # Parametri form completi (per ricaricamento scenario)
    form_data = models.JSONField(default=dict, blank=True, verbose_name='Parametri form')

    class Meta:
        verbose_name          = 'Simulazione Paga Salvata'
        verbose_name_plural   = 'Simulazioni Paga Salvate'
        ordering              = ['-data_modifica']

    def __str__(self):
        from .utils_motore_paga import Q2  # evita import circolare
        mesi = ['', 'Gen', 'Feb', 'Mar', 'Apr', 'Mag', 'Giu',
                'Lug', 'Ago', 'Set', 'Ott', 'Nov', 'Dic']
        m = mesi[self.mese] if 1 <= self.mese <= 12 else str(self.mese)
        return f"{self.nome} — {m} {self.anno}"

    @property
    def mese_nome(self):
        nomi = ['', 'Gennaio', 'Febbraio', 'Marzo', 'Aprile', 'Maggio', 'Giugno',
                'Luglio', 'Agosto', 'Settembre', 'Ottobre', 'Novembre', 'Dicembre']
        return nomi[self.mese] if 1 <= self.mese <= 12 else str(self.mese)
