
from django.db import models
from django.conf import settings
from anagrafiche.models import Dipendente, Azienda

from documenti.upload_paths import documento_file_upload_to

class Documento(models.Model):
    class Meta:
        verbose_name = 'Documento'
        verbose_name_plural = 'Documenti'
        ordering = ['-data_caricamento']

    TIPO_CHOICES = [
        # Documenti aziendali
        ('contratto', 'Contratto di lavoro'),
        ('privacy', 'Autorizzazione privacy'),
        ('busta_paga', 'Busta paga'),
        ('certificato', 'CUD / Certificato fiscale'),
        ('unilav', 'UniLav'),
        ('riepilogo_mensile', 'Riepilogo mensile'),
        ('carichi_famiglia', 'Comunicazione carichi di famiglia'),
        # Documenti identità (caricabili dal dipendente)
        ('documento_identita', 'Documento di identità'),
        ('permesso_soggiorno', 'Permesso di soggiorno'),
        ('codice_fiscale_doc', 'Tessera sanitaria / Codice fiscale'),
        # Documenti professionali
        ('curriculum', 'Curriculum vitae'),
        ('attestato', 'Attestato professionale'),
        ('abilitazione', 'Abilitazione tecnica'),
        ('titolo_studio', 'Titolo di studio'),
        ('certificazione', 'Certificazione / Titolo di studio'),
        ('altro', 'F24'),
    ]

    # Gruppi per visualizzazione
    TIPI_AZIENDALI = {'contratto', 'privacy', 'busta_paga', 'certificato', 'carichi_famiglia'}
    TIPI_PERSONALI = {
        'documento_identita', 'permesso_soggiorno', 'codice_fiscale_doc',
        'curriculum', 'attestato', 'abilitazione', 'titolo_studio',
        'certificazione',
    }

    azienda = models.ForeignKey(Azienda, on_delete=models.CASCADE, verbose_name='Azienda')
    dipendente = models.ForeignKey(Dipendente, on_delete=models.CASCADE, null=True, blank=True, verbose_name='Dipendente')
    tipo = models.CharField(max_length=30, choices=TIPO_CHOICES, verbose_name='Tipo documento')
    descrizione = models.CharField(max_length=200, blank=True, verbose_name='Descrizione / Titolo')
    file = models.FileField(upload_to=documento_file_upload_to, verbose_name='File')
    data_caricamento = models.DateTimeField(auto_now_add=True, verbose_name='Data caricamento')
    caricato_da = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, verbose_name='Caricato da')
    caricato_dal_dipendente = models.BooleanField(default=False, verbose_name='Caricato dal dipendente')
    visibile_al_dipendente = models.BooleanField(default=True, verbose_name='Visibile al dipendente')
    visualizzato_da_azienda = models.BooleanField(default=False, verbose_name='Visualizzato dall\'azienda',
                                                   help_text='Se True il documento caricato dal dipendente è stato acquisito dall\'azienda e non può più essere eliminato dal dipendente.')

    def nome_file(self):
        """Restituisce solo il nome del file senza path."""
        import os
        return os.path.basename(self.file.name) if self.file else ''

    def save(self, *args, **kwargs):
        """
        Forza il path upload nella sottocartella coerente con ``tipo`` per i nuovi file.
        Non modifica i record già salvati (evita di cambiare solo il riferimento DB
        senza spostare fisicamente file esistenti).
        """
        if self.file and getattr(self.file, "_committed", True) is False:
            current_name = getattr(self.file, "name", "") or "file.bin"
            self.file.name = documento_file_upload_to(self, current_name)
        super().save(*args, **kwargs)


class CedolinoMotoreV4(models.Model):
    """
    Snapshot estrazione posizionale (motore v4) collegato all'anagrafica Gesper.
    Equivale alla tabella ``cedolini`` dello schema SQLite ``schema_cedolini_v4.sql``,
    con ``id_dipendente`` → ``anagrafiche.Dipendente``.

    La verifica rispetto al PDF (conciliazione) è tracciata in ``verifica_*`` e aggiornata
    dalla pagina «Conciliazione buste»; ``pdf_bytes_sha256`` è l’impronta del file usata
    all’ultimo salvataggio estrazione.
    """

    class VerificaStato(models.TextChoices):
        PENDING = "pending", "Da verificare"
        OK = "ok", "Verificato OK"
        DIVERGENZE = "divergenze", "Divergenze"
        SENZA_REPORT = "senza_report", "PDF non letto"
        ERRORE = "errore", "Errore ricalcolo da DB"

    NATURA_BUSTA_CHOICES = [
        ("ORDINARIA", "Ordinaria"),
        ("TREDICESIMA", "Tredicesima"),
        ("QUATTORDICESIMA", "Quattordicesima"),
    ]

    class Meta:
        verbose_name = "Cedolino estrazione (motore v4)"
        verbose_name_plural = "Cedolini estrazione (motore v4)"
        constraints = [
            models.UniqueConstraint(
                fields=("dipendente", "mese", "anno", "natura_busta"),
                name="uniq_cedolino_motore_v4_dip_mese_anno_natura",
            ),
        ]
        indexes = [
            models.Index(fields=["dipendente", "anno", "mese", "natura_busta"]),
        ]

    dipendente = models.ForeignKey(
        Dipendente,
        on_delete=models.CASCADE,
        related_name="cedolini_motore_v4",
        verbose_name="Dipendente",
    )
    documento = models.ForeignKey(
        "Documento",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="estrazioni_motore_v4",
        verbose_name="Documento busta (opz.)",
    )
    mese = models.PositiveSmallIntegerField(verbose_name="Mese")
    anno = models.PositiveSmallIntegerField(verbose_name="Anno")
    natura_busta = models.CharField(
        max_length=20,
        choices=NATURA_BUSTA_CHOICES,
        default="ORDINARIA",
        verbose_name="Natura busta",
        help_text="Distinzione 13ª/14ª vs ordinario quando il periodo retributivo coincide (es. dicembre).",
    )
    foglio_n = models.CharField("Foglio N.", max_length=32, blank=True)
    file_pdf = models.CharField("Nome file PDF", max_length=512, blank=True)
    tipo_cedolino = models.CharField(max_length=64, default="ORDINARIO", blank=True)

    paga_base = models.DecimalField(max_digits=16, decimal_places=5, null=True, blank=True)
    contingenza = models.DecimalField(max_digits=16, decimal_places=5, null=True, blank=True)
    el_dis_san = models.DecimalField("EL.DIS.SAN", max_digits=14, decimal_places=2, default=0)
    el_dis_bil = models.DecimalField("EL.DIS.BIL", max_digits=16, decimal_places=5, default=0)
    # Orario: componenti €/h in «retr. paga contr.» (F1), oltre PB/CONT/EL.SAN/EL.DIS.BIL
    scatti_anz_imp = models.DecimalField(
        "Scatti anz. (€/h)",
        max_digits=16,
        decimal_places=5,
        default=0,
    )
    superminimo_imp = models.DecimalField(
        "Superminimo (€/h)",
        max_digits=16,
        decimal_places=5,
        default=0,
    )
    retr_oraria_att = models.DecimalField("Retr. oraria ATT", max_digits=16, decimal_places=5, null=True, blank=True)
    retr_giornaliera = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    retrib_di_fatto = models.DecimalField("Retrib. di fatto", max_digits=16, decimal_places=5, null=True, blank=True)
    gg_contratto = models.PositiveSmallIntegerField(null=True, blank=True)
    ore_contratto = models.DecimalField(max_digits=8, decimal_places=2, null=True, blank=True)

    totale_lordo = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    imponibile_contrib = models.DecimalField("Imponibile contrib.", max_digits=14, decimal_places=2, null=True, blank=True)
    tot_contrib_soc = models.DecimalField("Tot. contrib. sociali", max_digits=14, decimal_places=2, null=True, blank=True)
    imp_irpef_mese = models.DecimalField("Imponibile IRPEF mese", max_digits=14, decimal_places=2, null=True, blank=True)
    irpef_lorda_mese = models.DecimalField("IRPEF lorda mese", max_digits=14, decimal_places=2, null=True, blank=True)
    tot_detr_mese = models.DecimalField("Tot. detrazioni mese", max_digits=14, decimal_places=2, null=True, blank=True)
    tot_trat_irpef = models.DecimalField("Tot. trat. IRPEF", max_digits=14, decimal_places=2, null=True, blank=True)
    tot_trattenute = models.DecimalField("Tot. trattenute", max_digits=14, decimal_places=2, null=True, blank=True)
    netto_busta = models.DecimalField("Netto busta", max_digits=14, decimal_places=2, null=True, blank=True)

    irpef_erario = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    addiz_regionale = models.DecimalField("Addiz. regionale", max_digits=14, decimal_places=2, default=0)
    addiz_comunale = models.DecimalField("Addiz. comunale", max_digits=14, decimal_places=2, default=0)
    arr_prec = models.DecimalField("Arr. prec.", max_digits=14, decimal_places=2, default=0)
    arr_attuale = models.DecimalField("Arr. attuale", max_digits=14, decimal_places=2, default=0)
    conguaglio_irpef = models.DecimalField(max_digits=14, decimal_places=2, default=0)

    detr_lavoro_dip = models.DecimalField(default=0, max_digits=14, decimal_places=2)
    detr_coniuge = models.DecimalField(default=0, max_digits=14, decimal_places=2)
    detr_figli = models.DecimalField(default=0, max_digits=14, decimal_places=2)
    detr_altri = models.DecimalField(default=0, max_digits=14, decimal_places=2)
    detr_totale = models.DecimalField(default=0, max_digits=14, decimal_places=2)

    ferie_ap = models.DecimalField(default=0, max_digits=10, decimal_places=2)
    ferie_mat = models.DecimalField(default=0, max_digits=10, decimal_places=2)
    ferie_god = models.DecimalField(default=0, max_digits=10, decimal_places=2)
    ferie_res = models.DecimalField(default=0, max_digits=10, decimal_places=2)
    perm_ap = models.DecimalField(default=0, max_digits=10, decimal_places=2)
    perm_mat = models.DecimalField(default=0, max_digits=10, decimal_places=2)
    perm_god = models.DecimalField(default=0, max_digits=10, decimal_places=2)
    perm_res = models.DecimalField(default=0, max_digits=10, decimal_places=2)
    rol_ap = models.DecimalField(default=0, max_digits=10, decimal_places=2)
    rol_mat = models.DecimalField(default=0, max_digits=10, decimal_places=2)
    rol_god = models.DecimalField(default=0, max_digits=10, decimal_places=2)
    rol_res = models.DecimalField(default=0, max_digits=10, decimal_places=2)
    fest_ap = models.DecimalField(default=0, max_digits=10, decimal_places=2)
    fest_mat = models.DecimalField(default=0, max_digits=10, decimal_places=2)
    fest_god = models.DecimalField(default=0, max_digits=10, decimal_places=2)
    fest_res = models.DecimalField(default=0, max_digits=10, decimal_places=2)

    pos_sett_inps = models.PositiveIntegerField("Pos. sett. INPS", default=0)
    ore_inps = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    gg_inps = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    gg_minimi_inps = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    ore_inail = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    gg_inail = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    imponibile_inail = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    tfr_mese = models.DecimalField("TFR mese", max_digits=14, decimal_places=2, default=0)

    prog_imp_inail = models.DecimalField(default=0, max_digits=16, decimal_places=2)
    prog_imp_contrib_soc = models.DecimalField(default=0, max_digits=16, decimal_places=2)
    prog_contrib_soc = models.DecimalField(default=0, max_digits=16, decimal_places=2)
    prog_oneri_deduc = models.DecimalField(default=0, max_digits=16, decimal_places=2)
    prog_imp_irpef = models.DecimalField(default=0, max_digits=16, decimal_places=2)
    prog_irpef_lorda = models.DecimalField(default=0, max_digits=16, decimal_places=2)
    prog_tot_detr = models.DecimalField(default=0, max_digits=16, decimal_places=2)
    prog_irpef_pagata = models.DecimalField(default=0, max_digits=16, decimal_places=2)

    imp_contrib_voci = models.DecimalField(
        "Σ impon. da voci (calc.)",
        max_digits=14,
        decimal_places=2,
        default=0,
    )
    retr_oraria_calc = models.DecimalField(
        "Retr. oraria calc. (F1)",
        max_digits=16,
        decimal_places=5,
        default=0,
    )

    pdf_bytes_sha256 = models.CharField(
        "SHA-256 PDF (ultima estrazione)",
        max_length=64,
        blank=True,
        default="",
        db_index=True,
    )
    estrazione_motore = models.CharField(
        "Motore estrazione",
        max_length=32,
        blank=True,
        default="posizionale_v4",
    )
    verifica_stato = models.CharField(
        "Stato verifica vs PDF",
        max_length=20,
        choices=VerificaStato.choices,
        default=VerificaStato.PENDING,
    )
    verifica_il = models.DateTimeField("Verifica eseguita il", null=True, blank=True)
    verifica_n_diff = models.PositiveSmallIntegerField(null=True, blank=True)
    verifica_n_checks_formula_ko = models.PositiveSmallIntegerField(
        "N° formule KO (tutte F1–F9)",
        null=True,
        blank=True,
    )
    verifica_n_checks_formula_ko_bloccanti = models.PositiveSmallIntegerField(
        "N° formule KO bloccanti (esito badge)",
        null=True,
        blank=True,
        help_text="Conteggi F3–F5, F8, F9 che determinano OK/differenze; esclusi F1, F2, F7 diagnostici.",
    )

    importato_il = models.DateTimeField(auto_now=True)

    def __str__(self) -> str:
        nat = getattr(self, "natura_busta", None) or "ORDINARIA"
        return f"Ced.v4 {self.dipendente_id} {self.mese:02d}/{self.anno} ({nat})"


class VoceCedolinoMotoreV4(models.Model):
    """Righe voce collegate a :class:`CedolinoMotoreV4` (tabella ``voci_cedolino``)."""

    class Meta:
        verbose_name = "Voce cedolino (motore v4)"
        verbose_name_plural = "Voci cedolino (motore v4)"
        indexes = [
            models.Index(fields=["cedolino", "codice"]),
        ]

    cedolino = models.ForeignKey(
        CedolinoMotoreV4,
        on_delete=models.CASCADE,
        related_name="voci",
        verbose_name="Cedolino",
    )
    codice = models.CharField(max_length=16)
    descrizione = models.CharField(max_length=255)
    tipo = models.CharField(max_length=32)
    ore_gg = models.DecimalField(max_digits=12, decimal_places=4, null=True, blank=True)
    base_unitaria = models.DecimalField(max_digits=16, decimal_places=5, null=True, blank=True)
    importo = models.DecimalField(max_digits=14, decimal_places=2)
    riferimento = models.CharField(max_length=64, blank=True)
    importo_calcolato = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    delta_calc = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    esito_check = models.CharField(max_length=8, default="N/A", blank=True)


class ValidazioneCedolinoMotoreV4(models.Model):
    """Esiti controlli formula (tabella ``validazioni``)."""

    class Meta:
        verbose_name = "Validazione formula (motore v4)"
        verbose_name_plural = "Validazioni formula (motore v4)"
        indexes = [
            models.Index(fields=["cedolino"]),
        ]

    cedolino = models.ForeignKey(
        CedolinoMotoreV4,
        on_delete=models.CASCADE,
        related_name="validazioni",
        verbose_name="Cedolino",
    )
    formula = models.CharField(max_length=16)
    descrizione = models.CharField(max_length=255)
    valore_calc = models.DecimalField(max_digits=16, decimal_places=5)
    valore_letto = models.DecimalField(max_digits=16, decimal_places=5)
    delta = models.DecimalField(max_digits=16, decimal_places=5)
    esito = models.CharField(max_length=8)
    nota = models.TextField(blank=True)
    validato_il = models.DateTimeField(auto_now_add=True)
