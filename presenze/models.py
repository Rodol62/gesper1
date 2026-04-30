from decimal import Decimal

from django.db import models
from django.conf import settings
from django.core.validators import MinValueValidator
from django.core.exceptions import ValidationError
from anagrafiche.models import Dipendente, Azienda


class Presenza(models.Model):

    CAUSALE_CHOICES = [
        ('P',   'Presenza'),
        ('ST',  'Straordinario'),
        ('F',   'Ferie'),
        ('PE',  'Permesso'),
        ('M',   'Malattia'),
        ('INF', 'Infortunio'),
        ('MAT', 'Maternità / Paternità'),
        ('CIG', 'Cassa Integrazione'),
        ('A',   'Assenza ingiustificata'),
        ('FE',  'Festivo lavorato'),
        ('R',   'Riposo / Giorno libero'),
        ('SMART', 'Smart working'),
        ('ALTRO', 'Altro'),
        ('SOSP', 'Sospensione disciplinare'),
    ]

    # Colori per il calendario
    CAUSALE_COLORI = {
        'P':     '#0a6640',   # verde
        'ST':    '#1a4a20',   # verde scuro
        'F':     '#1b3a8f',   # blu
        'PE':    '#b85a00',   # arancione
        'M':     '#c0392b',   # rosso
        'INF':   '#7b1c1c',   # rosso scuro
        'MAT':   '#8e44ad',   # viola
        'CIG':   '#6d4c41',   # marrone
        'A':     '#922b21',   # rosso mattone
        'FE':    '#2d6199',   # azzurro
        'R':     '#888888',   # grigio
        'SMART': '#0e7d91',   # teal
        'ALTRO': '#555555',
        'SOSP': '#5c4033',
    }

    class Meta:
        verbose_name = 'Presenza'
        verbose_name_plural = 'Presenze'
        ordering = ['data']
        unique_together = [('dipendente', 'data')]  # un record per giorno per dipendente

    dipendente = models.ForeignKey(
        Dipendente, on_delete=models.CASCADE,
        related_name='presenze', verbose_name='Dipendente'
    )
    azienda = models.ForeignKey(Azienda, on_delete=models.CASCADE, verbose_name='Azienda')
    data = models.DateField(verbose_name='Data')
    causale = models.CharField(
        max_length=10, choices=CAUSALE_CHOICES, default='P',
        verbose_name='Causale'
    )
    # Orario — non obbligatorio per causali assenza (ferie, malattia, ecc.)
    ora_entrata = models.TimeField(null=True, blank=True, verbose_name='Ora entrata')
    ora_uscita = models.TimeField(null=True, blank=True, verbose_name='Ora uscita')
    # Secondo turno (es. pranzo + cena)
    ora_entrata2 = models.TimeField(null=True, blank=True, verbose_name='Ora entrata 2°')
    ora_uscita2  = models.TimeField(null=True, blank=True, verbose_name='Ora uscita 2°')
    # Terzo turno (es. aperitivo / extra serale)
    ora_entrata3 = models.TimeField(null=True, blank=True, verbose_name='Ora entrata 3°')
    ora_uscita3  = models.TimeField(null=True, blank=True, verbose_name='Ora uscita 3°')
    TIPO_STRAORD_CHOICES = [
        ('diurno',    'Straordinario diurno'),
        ('notturno',  'Straordinario notturno (dopo 22:00)'),
        ('festivo',   'Straordinario festivo/domenicale'),
        ('nott_fest', 'Straordinario notturno festivo'),
    ]

    # Ore straordinario separato
    ore_straordinario = models.DecimalField(
        max_digits=4, decimal_places=2, default=0,
        verbose_name='Ore straordinario'
    )
    tipo_straordinario = models.CharField(
        max_length=10, choices=TIPO_STRAORD_CHOICES,
        null=True, blank=True,
        verbose_name='Tipo straordinario'
    )
    note = models.CharField(max_length=255, blank=True, verbose_name='Note')
    registrata_da = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, verbose_name='Registrata da'
    )
    data_registrazione = models.DateTimeField(auto_now_add=True, verbose_name='Data registrazione')
    data_modifica = models.DateTimeField(auto_now=True, verbose_name='Ultima modifica')

    def ore_lavorate(self):
        """Ore lavorate totali: 1° + 2° + 3° turno."""
        from datetime import datetime, date as ddate
        totale = 0.0
        coppie = [
            (self.ora_entrata,  self.ora_uscita),
            (self.ora_entrata2, self.ora_uscita2),
            (self.ora_entrata3, self.ora_uscita3),
        ]
        intervalli_visti = set()
        for ent, usc in coppie:
            if ent and usc:
                # Evita doppio conteggio quando lo stesso turno viene copiato su piu' colonne.
                key = (ent, usc)
                if key in intervalli_visti:
                    continue
                intervalli_visti.add(key)
                t_in  = datetime.combine(ddate.today(), ent)
                t_out = datetime.combine(ddate.today(), usc)
                # Supporta anche turni che superano la mezzanotte (es. 22:00-02:00).
                diff  = (t_out - t_in).total_seconds() / 3600
                if diff <= 0:
                    diff += 24
                if diff > 0:
                    totale += diff
        return round(totale, 2)

    def ore_totali(self):
        return round(self.ore_lavorate() + float(self.ore_straordinario), 2)

    def colore_causale(self):
        return self.CAUSALE_COLORI.get(self.causale, '#555555')

    def __str__(self):
        return f"{self.dipendente} — {self.data} — {self.get_causale_display()}"


class CausaleAssenzaNonMaturativa(models.Model):
    """
    Causali di assenza che riducono il rateo mensile di maturazione ferie/ROL
    (stessa logica per tutti i dipendenti; configurabile per azienda o globale).
    """

    MODALITA_CHOICES = [
        ('INTERA', 'Giornata intera non maturativa'),
        (
            'MALATTIA_ECCEDENTE',
            'Malattia oltre il periodo di comporto (solo parte eccedente, ordine annuo)',
        ),
    ]

    azienda = models.ForeignKey(
        Azienda,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name='causali_non_maturative',
        verbose_name='Azienda',
        help_text='Vuoto = default per tutte le aziende',
    )
    codice_causale = models.CharField(
        max_length=10,
        verbose_name='Causale',
        help_text='Codice come in Presenza (es. A, M, CIG, ALTRO, SOSP)',
    )
    etichetta = models.CharField(max_length=200, verbose_name='Descrizione')
    modalita = models.CharField(
        max_length=32,
        choices=MODALITA_CHOICES,
        default='INTERA',
        verbose_name='Modalità conteggio',
    )
    attiva = models.BooleanField(default=True)
    ordine = models.PositiveSmallIntegerField(default=0, verbose_name='Ordine')

    class Meta:
        verbose_name = 'Causale assenza non maturativa'
        verbose_name_plural = 'Causali assenze non maturative'
        ordering = ['ordine', 'id']

    def __str__(self):
        az = self.azienda.nome if self.azienda_id else 'Globale'
        return f"{self.codice_causale} ({az}) — {self.etichetta}"


class RiepilogoMensilePresenze(models.Model):
    """
    Aggregazione mensile delle presenze di un dipendente, pronta per essere
    passata al motore paga (calcola_busta_paga_mese).

    Viene popolata da aggrega_presenze_per_motore() e può essere approvata
    dall'HR prima di alimentare il cedolino reale.
    """

    STATO_CHOICES = [
        ('bozza',     'Bozza (generata automaticamente)'),
        ('revisione', 'In revisione HR'),
        ('approvata', 'Approvata'),
        ('elaborata', 'Elaborata nel cedolino'),
    ]

    class Meta:
        verbose_name = 'Riepilogo mensile presenze'
        verbose_name_plural = 'Riepiloghi mensili presenze'
        ordering = ['-anno', '-mese', 'dipendente']
        unique_together = [('dipendente', 'anno', 'mese')]

    dipendente = models.ForeignKey(
        Dipendente, on_delete=models.CASCADE,
        related_name='riepiloghi_presenze', verbose_name='Dipendente'
    )
    azienda = models.ForeignKey(Azienda, on_delete=models.CASCADE, verbose_name='Azienda')
    anno = models.PositiveSmallIntegerField(verbose_name='Anno')
    mese = models.PositiveSmallIntegerField(
        verbose_name='Mese',
        validators=[MinValueValidator(1)]
    )
    stato = models.CharField(
        max_length=12, choices=STATO_CHOICES, default='bozza',
        verbose_name='Stato'
    )

    # ── Ore presenze ─────────────────────────────────────────────────────────
    ore_ordinarie = models.DecimalField(
        max_digits=6, decimal_places=2, default=0,
        verbose_name='Ore ordinarie lavorate'
    )
    ore_domenicali = models.DecimalField(
        max_digits=6, decimal_places=2, default=0,
        verbose_name='Ore domenicali (within orario contrattuale)'
    )
    ore_festivi = models.DecimalField(
        max_digits=6, decimal_places=2, default=0,
        verbose_name='Ore festività nazionali lavorate (within orario)'
    )
    ore_straord_diurno = models.DecimalField(
        max_digits=6, decimal_places=2, default=0,
        verbose_name='Ore straordinario diurno'
    )
    ore_straord_notturno = models.DecimalField(
        max_digits=6, decimal_places=2, default=0,
        verbose_name='Ore straordinario notturno'
    )
    ore_straord_festivo = models.DecimalField(
        max_digits=6, decimal_places=2, default=0,
        verbose_name='Ore straordinario festivo (non domenica)'
    )
    ore_straord_domenica = models.DecimalField(
        max_digits=6, decimal_places=2, default=0,
        verbose_name='Ore straordinario domenicale'
    )
    ore_straord_nott_fest = models.DecimalField(
        max_digits=6, decimal_places=2, default=0,
        verbose_name='Ore straordinario notturno festivo'
    )

    # ── Assenze / godimenti ───────────────────────────────────────────────────
    giorni_ferie_godute = models.DecimalField(
        max_digits=5, decimal_places=2, default=0,
        verbose_name='Giorni ferie godute'
    )
    ore_permessi_goduti = models.DecimalField(
        max_digits=6, decimal_places=2, default=0,
        verbose_name='Ore permessi goduti (ROL)'
    )
    giorni_malattia = models.PositiveSmallIntegerField(
        default=0, verbose_name='Giorni malattia'
    )
    giorni_assenza_ingiust = models.PositiveSmallIntegerField(
        default=0, verbose_name='Giorni assenza ingiustificata'
    )
    giorni_cig = models.PositiveSmallIntegerField(
        default=0, verbose_name='Giorni CIG / sospensione'
    )

    # ── Metadati ──────────────────────────────────────────────────────────────
    generata_da = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='riepiloghi_generati',
        verbose_name='Generata da'
    )
    approvata_da = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='riepiloghi_approvati',
        verbose_name='Approvata da'
    )
    data_generazione = models.DateTimeField(auto_now_add=True)
    data_modifica = models.DateTimeField(auto_now=True)
    note = models.TextField(blank=True, verbose_name='Note')

    def __str__(self):
        return f"{self.dipendente} — {self.anno}/{self.mese:02d} [{self.get_stato_display()}]"

    def as_motore_kwargs(self) -> dict:
        """
        Restituisce i kwargs pronti per calcola_busta_paga_mese().
        Il chiamante deve aggiungere: parametro_ccnl, data_riferimento,
        paga_base, contingenza, ecc.

        Valori numerici come Decimal (non float): il motore moltiplica con Decimal
        e float × Decimal solleva TypeError.
        """

        def D(x) -> Decimal:
            if x is None:
                return Decimal('0')
            return Decimal(str(x))

        return {
            'ore_straord_diurno':    D(self.ore_straord_diurno),
            'ore_straord_notturno':  D(self.ore_straord_notturno),
            'ore_straord_festivo':   D(self.ore_straord_festivo),
            'ore_straord_domenica':  D(self.ore_straord_domenica),
            'ore_straord_nott_fest': D(self.ore_straord_nott_fest),
            'ore_domenicali':        D(self.ore_domenicali),
            'ore_festivi':           D(self.ore_festivi),
            'ore_ordinarie_retribuite': D(self.ore_ordinarie),
            'giorni_ferie_godute':   D(self.giorni_ferie_godute),
            'ore_permessi_goduti':   D(self.ore_permessi_goduti),
            'giorni_assenza_ingiust': D(self.giorni_assenza_ingiust),
            'auto_ore_domenicali_da_calendario': False,  # usiamo dati reali
        }


class ConfigurazioneOrarioAnnuale(models.Model):
    """Parametri annuali orari di lavoro azienda (base per calendario lavorativo)."""

    azienda = models.ForeignKey(
        Azienda,
        on_delete=models.CASCADE,
        related_name='configurazioni_orario_annuali',
        verbose_name='Azienda',
    )
    anno = models.PositiveIntegerField(verbose_name='Anno')
    giorni_riposo_settimanale = models.JSONField(
        default=list,
        verbose_name='Giorni riposo settimanale',
        help_text='Lista interi 0=Lun ... 6=Dom',
    )
    genera_presenze_teoriche = models.BooleanField(
        default=True,
        verbose_name='Genera presenze teoriche di default',
    )
    data_modifica = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Configurazione orario annuale'
        verbose_name_plural = 'Configurazioni orario annuali'
        unique_together = [('azienda', 'anno')]
        ordering = ['-anno', 'azienda__nome']

    def __str__(self):
        return f"{self.azienda.nome} — Orari {self.anno}"


class FasciaAperturaSettimanale(models.Model):
    """Orari apertura/chiusura giornalieri (ripetuti ogni settimana per anno)."""

    GIORNI_CHOICES = [
        (0, 'Lunedì'),
        (1, 'Martedì'),
        (2, 'Mercoledì'),
        (3, 'Giovedì'),
        (4, 'Venerdì'),
        (5, 'Sabato'),
        (6, 'Domenica'),
    ]

    configurazione = models.ForeignKey(
        ConfigurazioneOrarioAnnuale,
        on_delete=models.CASCADE,
        related_name='fasce_apertura',
        verbose_name='Configurazione annuale',
    )
    giorno_settimana = models.PositiveSmallIntegerField(choices=GIORNI_CHOICES)
    chiuso = models.BooleanField(default=False, verbose_name='Chiuso')
    ora_apertura_mattina = models.TimeField(null=True, blank=True, verbose_name='Apertura mattina')
    ora_chiusura_mattina = models.TimeField(null=True, blank=True, verbose_name='Chiusura mattina')
    ora_apertura_pomeriggio = models.TimeField(null=True, blank=True, verbose_name='Apertura pomeriggio')
    ora_chiusura_pomeriggio = models.TimeField(null=True, blank=True, verbose_name='Chiusura pomeriggio')

    class Meta:
        verbose_name = 'Fascia apertura settimanale'
        verbose_name_plural = 'Fasce apertura settimanali'
        unique_together = [('configurazione', 'giorno_settimana')]
        ordering = ['giorno_settimana']

    def clean(self):
        if self.chiuso:
            return

        has_mattina = bool(self.ora_apertura_mattina and self.ora_chiusura_mattina)
        has_pomeriggio = bool(self.ora_apertura_pomeriggio and self.ora_chiusura_pomeriggio)
        if not has_mattina and not has_pomeriggio:
            raise ValidationError('Per i giorni aperti inserire almeno una fascia (mattina o pomeriggio).')

        if has_mattina and self.ora_apertura_mattina >= self.ora_chiusura_mattina:
            raise ValidationError('La fascia mattina non è valida (apertura >= chiusura).')

        if has_pomeriggio and self.ora_apertura_pomeriggio >= self.ora_chiusura_pomeriggio:
            raise ValidationError('La fascia pomeriggio non è valida (apertura >= chiusura).')

        if has_mattina and has_pomeriggio and self.ora_apertura_pomeriggio <= self.ora_chiusura_mattina:
            raise ValidationError('La fascia pomeriggio deve iniziare dopo la chiusura della fascia mattina.')

    def save(self, *args, **kwargs):
        if self.chiuso:
            self.ora_apertura_mattina = None
            self.ora_chiusura_mattina = None
            self.ora_apertura_pomeriggio = None
            self.ora_chiusura_pomeriggio = None
        super().save(*args, **kwargs)

    def ore_standard_giornaliere(self) -> float:
        """Somma ore parziali mattina + pomeriggio in formato decimale."""
        tot_min = 0
        if self.ora_apertura_mattina and self.ora_chiusura_mattina:
            tot_min += ((self.ora_chiusura_mattina.hour * 60 + self.ora_chiusura_mattina.minute) -
                        (self.ora_apertura_mattina.hour * 60 + self.ora_apertura_mattina.minute))
        if self.ora_apertura_pomeriggio and self.ora_chiusura_pomeriggio:
            tot_min += ((self.ora_chiusura_pomeriggio.hour * 60 + self.ora_chiusura_pomeriggio.minute) -
                        (self.ora_apertura_pomeriggio.hour * 60 + self.ora_apertura_pomeriggio.minute))
        return round(max(0, tot_min) / 60.0, 4)

    def __str__(self):
        return f"{self.get_giorno_settimana_display()} — {self.configurazione}"


class TurnoLavorativoAziendale(models.Model):
    """Turni aziendali configurabili nell'orario di apertura."""

    configurazione = models.ForeignKey(
        ConfigurazioneOrarioAnnuale,
        on_delete=models.CASCADE,
        related_name='turni_lavorativi',
        verbose_name='Configurazione annuale',
    )
    nome = models.CharField(max_length=80, verbose_name='Nome turno')
    ora_inizio = models.TimeField(verbose_name='Inizio turno')
    ora_fine = models.TimeField(verbose_name='Fine turno')
    ordine = models.PositiveSmallIntegerField(default=1, verbose_name='Ordine')
    attivo = models.BooleanField(default=True)

    class Meta:
        verbose_name = 'Turno lavorativo aziendale'
        verbose_name_plural = 'Turni lavorativi aziendali'
        ordering = ['ordine', 'ora_inizio']

    def clean(self):
        if not self.ora_inizio or not self.ora_fine:
            return
        if self.ora_inizio >= self.ora_fine:
            raise ValidationError('L\'inizio turno deve essere precedente alla fine turno.')

    def __str__(self):
        return f"{self.nome} ({self.ora_inizio.strftime('%H:%M')}–{self.ora_fine.strftime('%H:%M')})"


class AssegnazioneTurnoDipendente(models.Model):
    """Assegnazione opzionale turno a dipendente."""

    turno = models.ForeignKey(
        TurnoLavorativoAziendale,
        on_delete=models.CASCADE,
        related_name='assegnazioni',
    )
    dipendente = models.ForeignKey(
        Dipendente,
        on_delete=models.CASCADE,
        related_name='assegnazioni_turno',
    )
    data_inizio = models.DateField(verbose_name='Dal')
    data_fine = models.DateField(null=True, blank=True, verbose_name='Al')
    attivo = models.BooleanField(default=True)

    class Meta:
        verbose_name = 'Assegnazione turno dipendente'
        verbose_name_plural = 'Assegnazioni turni dipendenti'
        ordering = ['-data_inizio']

    def clean(self):
        if self.data_inizio and self.data_fine and self.data_fine < self.data_inizio:
            raise ValidationError('La data fine non può essere precedente alla data inizio.')

    def __str__(self):
        return f"{self.dipendente} → {self.turno.nome}"


class ConfigurazioneOrarioMensile(models.Model):
    """Configurazione orario per singolo mese (override rispetto al profilo annuale)."""

    MESE_CHOICES = [
        (1, 'Gennaio'), (2, 'Febbraio'), (3, 'Marzo'), (4, 'Aprile'),
        (5, 'Maggio'), (6, 'Giugno'), (7, 'Luglio'), (8, 'Agosto'),
        (9, 'Settembre'), (10, 'Ottobre'), (11, 'Novembre'), (12, 'Dicembre'),
    ]

    azienda = models.ForeignKey(
        Azienda,
        on_delete=models.CASCADE,
        related_name='configurazioni_orario_mensili',
        verbose_name='Azienda',
    )
    anno = models.PositiveIntegerField(verbose_name='Anno')
    mese = models.PositiveSmallIntegerField(choices=MESE_CHOICES, verbose_name='Mese')
    giorni_riposo_settimanale = models.JSONField(
        default=list,
        verbose_name='Giorni riposo settimanale',
        help_text='Lista interi 0=Lun ... 6=Dom',
    )
    genera_presenze_teoriche = models.BooleanField(default=True)
    data_modifica = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Configurazione orario mensile'
        verbose_name_plural = 'Configurazioni orario mensili'
        unique_together = [('azienda', 'anno', 'mese')]
        ordering = ['-anno', 'mese', 'azienda__nome']

    def __str__(self):
        return f"{self.azienda.nome} — {self.anno}/{self.mese:02d}"


class FasciaAperturaMensile(models.Model):
    """Fasce apertura/chiusura giornaliere riferite a un mese specifico."""

    GIORNI_CHOICES = FasciaAperturaSettimanale.GIORNI_CHOICES

    configurazione = models.ForeignKey(
        ConfigurazioneOrarioMensile,
        on_delete=models.CASCADE,
        related_name='fasce_apertura',
        verbose_name='Configurazione mensile',
    )
    giorno_settimana = models.PositiveSmallIntegerField(choices=GIORNI_CHOICES)
    chiuso = models.BooleanField(default=False, verbose_name='Chiuso')
    ora_apertura_mattina = models.TimeField(null=True, blank=True, verbose_name='Apertura mattina')
    ora_chiusura_mattina = models.TimeField(null=True, blank=True, verbose_name='Chiusura mattina')
    ora_apertura_pomeriggio = models.TimeField(null=True, blank=True, verbose_name='Apertura pomeriggio')
    ora_chiusura_pomeriggio = models.TimeField(null=True, blank=True, verbose_name='Chiusura pomeriggio')

    class Meta:
        verbose_name = 'Fascia apertura mensile'
        verbose_name_plural = 'Fasce apertura mensili'
        unique_together = [('configurazione', 'giorno_settimana')]
        ordering = ['giorno_settimana']

    def clean(self):
        if self.chiuso:
            return

        has_mattina = bool(self.ora_apertura_mattina and self.ora_chiusura_mattina)
        has_pomeriggio = bool(self.ora_apertura_pomeriggio and self.ora_chiusura_pomeriggio)
        if not has_mattina and not has_pomeriggio:
            raise ValidationError('Per i giorni aperti inserire almeno una fascia (mattina o pomeriggio).')

        if has_mattina and self.ora_apertura_mattina >= self.ora_chiusura_mattina:
            raise ValidationError('La fascia mattina non è valida (apertura >= chiusura).')

        if has_pomeriggio and self.ora_apertura_pomeriggio >= self.ora_chiusura_pomeriggio:
            raise ValidationError('La fascia pomeriggio non è valida (apertura >= chiusura).')

        if has_mattina and has_pomeriggio and self.ora_apertura_pomeriggio <= self.ora_chiusura_mattina:
            raise ValidationError('La fascia pomeriggio deve iniziare dopo la chiusura della fascia mattina.')

    def save(self, *args, **kwargs):
        if self.chiuso:
            self.ora_apertura_mattina = None
            self.ora_chiusura_mattina = None
            self.ora_apertura_pomeriggio = None
            self.ora_chiusura_pomeriggio = None
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.get_giorno_settimana_display()} — {self.configurazione}"


class SaldoMonteDipendente(models.Model):
    """
    Saldo per tipo monte (ferie, ROL, riposi compensativi) e anno di competenza.
    Saldo effettivo = saldo_iniziale + somma(MovimentoMonte.quantita).
    """

    TIPO_MONTE_CHOICES = [
        ('FERIE_GG', 'Ferie (giorni)'),
        ('ROL_ORE', 'ROL / permessi (ore)'),
        ('RIPOSI_COMP', 'Riposi compensativi'),
    ]

    dipendente = models.ForeignKey(
        Dipendente,
        on_delete=models.CASCADE,
        related_name='saldi_monte',
        verbose_name='Dipendente',
    )
    azienda = models.ForeignKey(
        Azienda,
        on_delete=models.CASCADE,
        related_name='saldi_monte_presenze',
        verbose_name='Azienda',
    )
    tipo_monte = models.CharField(
        max_length=16,
        choices=TIPO_MONTE_CHOICES,
        verbose_name='Tipo monte',
    )
    anno_competenza = models.PositiveSmallIntegerField(verbose_name='Anno competenza')
    saldo_iniziale = models.DecimalField(
        max_digits=8,
        decimal_places=2,
        default=0,
        verbose_name='Saldo iniziale',
        help_text='Tipicamente da import ultima busta paga / migrazione.',
    )
    data_saldo_iniziale = models.DateField(
        null=True,
        blank=True,
        verbose_name='Data riferimento saldo iniziale',
    )
    note = models.TextField(blank=True, verbose_name='Note')
    data_modifica = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Saldo monte dipendente'
        verbose_name_plural = 'Saldi monte dipendenti'
        unique_together = [('dipendente', 'azienda', 'tipo_monte', 'anno_competenza')]
        ordering = ['-anno_competenza', 'dipendente_id', 'tipo_monte']

    def __str__(self):
        return f'{self.dipendente} — {self.get_tipo_monte_display()} {self.anno_competenza}'


class MovimentoMonte(models.Model):
    """
    Movimento sul libro giornale di un monte (maturazione, godimento, rettifica, import).
    quantita: positivo aumenta il disponibile a favore del dipendente, negativo lo consuma.
    """

    TIPO_MOVIMENTO_CHOICES = [
        ('MATURAZIONE', 'Maturazione'),
        ('GODIMENTO', 'Godimento'),
        ('RETTIFICA_HR', 'Rettifica HR'),
        ('IMPORT_CONSULENTE', 'Import / consulente'),
        ('ANNULLAMENTO', 'Annullamento'),
    ]
    ORIGINE_CHOICES = [
        ('PRESENZA', 'Presenza giornaliera'),
        ('RIEPILOGO_MENSILE', 'Chiusura riepilogo mensile'),
        ('MANUALE', 'Manuale'),
        ('IMPORT', 'Import massivo'),
    ]
    UNITA_CHOICES = [
        ('GG', 'Giorni'),
        ('ORE', 'Ore'),
    ]

    saldo_monte = models.ForeignKey(
        SaldoMonteDipendente,
        on_delete=models.CASCADE,
        related_name='movimenti',
        verbose_name='Saldo monte',
    )
    data_movimento = models.DateField(verbose_name='Data competenza')
    tipo_movimento = models.CharField(
        max_length=20,
        choices=TIPO_MOVIMENTO_CHOICES,
        verbose_name='Tipo movimento',
    )
    quantita = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        verbose_name='Quantità',
        help_text='Segno algebrico: + a favore dipendente, − godimento / decurtazione.',
    )
    unita = models.CharField(
        max_length=3,
        choices=UNITA_CHOICES,
        verbose_name='Unità',
    )
    origine = models.CharField(
        max_length=20,
        choices=ORIGINE_CHOICES,
        default='MANUALE',
        verbose_name='Origine',
    )
    presenza = models.ForeignKey(
        'Presenza',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='movimenti_monte',
        verbose_name='Presenza collegata',
    )
    riepilogo_mensile = models.ForeignKey(
        'RiepilogoMensilePresenze',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='movimenti_monte',
        verbose_name='Riepilogo mensile',
    )
    idempotency_key = models.CharField(
        max_length=80,
        blank=True,
        default='',
        db_index=True,
        verbose_name='Chiave idempotenza',
        help_text='Es. "rie-{anno}-{mese}-ferie" per evitare doppi movimenti in chiusura mese.',
    )
    note = models.CharField(max_length=255, blank=True, verbose_name='Note')
    registrato_da = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='movimenti_monte_registrati',
        verbose_name='Registrato da',
    )
    data_creazione = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'Movimento monte'
        verbose_name_plural = 'Movimenti monte'
        ordering = ['-data_movimento', '-data_creazione']
        indexes = [
            models.Index(fields=['saldo_monte', 'data_movimento']),
        ]

    def __str__(self):
        return f'{self.saldo_monte} {self.data_movimento} {self.quantita} {self.unita}'
