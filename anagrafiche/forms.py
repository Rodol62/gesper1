import re
from datetime import date

from django import forms
from django.core.exceptions import ValidationError
from .models import Dipendente, Azienda
from .territorio_it import (
    regioni as regioni_it,
    province_per_regione,
    comuni_per_regione_provincia,
    paesi_istat,
)
from django.contrib.auth import get_user_model

User = get_user_model()


def date_validita_default_per_turno(turno, dipendente=None):
    """
    Periodo di validità suggerito per un aggancio turno: anno della pianificazione
    (ConfigurazioneOrarioAnnuale), con data inizio non precedente alla data assunzione
    del dipendente se nello stesso contesto.
    """
    cfg = turno.configurazione
    anno = cfg.anno
    dal = date(anno, 1, 1)
    al = date(anno, 12, 31)
    d_ass = getattr(dipendente, 'data_assunzione', None) if dipendente else None
    if d_ass and d_ass > dal:
        dal = d_ass
    return dal, al


class AssegnazioneTurnoForm(forms.ModelForm):
    """Form per un singolo aggancio turno dipendente (usato nel formset 4-slot)."""

    class Meta:
        from presenze.models import AssegnazioneTurnoDipendente
        model = AssegnazioneTurnoDipendente
        fields = ['turno', 'data_inizio', 'data_fine', 'attivo']
        widgets = {
            'turno': forms.Select(attrs={'class': 'form-select form-select-sm'}),
            'data_inizio': forms.DateInput(format='%Y-%m-%d', attrs={'type': 'date', 'class': 'form-control form-control-sm'}),
            'data_fine': forms.DateInput(format='%Y-%m-%d', attrs={'type': 'date', 'class': 'form-control form-control-sm'}),
            'attivo': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
        }
        labels = {
            'turno': 'Turno',
            'data_inizio': 'Dal',
            'data_fine': 'Al (vuoto = illimitato)',
            'attivo': 'Attivo',
        }

    def __init__(self, *args, **kwargs):
        azienda = kwargs.pop('azienda', None)
        self.dipendente = kwargs.pop('dipendente', None)
        super().__init__(*args, **kwargs)
        from presenze.models import TurnoLavorativoAziendale
        if azienda:
            self.fields['turno'].queryset = TurnoLavorativoAziendale.objects.filter(
                configurazione__azienda=azienda, attivo=True
            ).order_by('ordine', 'ora_inizio')
        else:
            self.fields['turno'].queryset = TurnoLavorativoAziendale.objects.none()
        self.fields['turno'].empty_label = '— nessuno —'
        self.fields['turno'].required = False
        self.fields['data_inizio'].required = False
        self.fields['data_fine'].required = False

    def clean(self):
        cleaned_data = super().clean()
        # Usa i valori grezzi del POST per capire se la riga è davvero vuota:
        # cleaned_data può escludere date non valide lasciando il turno valorizzato,
        # oppure stati incoerenti con empty_permitted/has_changed del formset.
        if not self.is_bound or self.data is None:
            return cleaned_data

        raw_turno = (self.data.get(self.add_prefix('turno')) or '').strip()
        raw_di = (self.data.get(self.add_prefix('data_inizio')) or '').strip()
        raw_df = (self.data.get(self.add_prefix('data_fine')) or '').strip()

        # Nessun turno e nessuna data nel POST → riga vuota (ok, anche se "Attivo" è spuntato)
        if not raw_turno and not raw_di and not raw_df:
            return cleaned_data

        if raw_turno and 'turno' in self.errors:
            return cleaned_data

        turno = cleaned_data.get('turno')
        data_inizio = cleaned_data.get('data_inizio')
        data_fine = cleaned_data.get('data_fine')

        if raw_turno:
            if not data_inizio:
                if 'data_inizio' in self.errors:
                    return cleaned_data
                from presenze.models import TurnoLavorativoAziendale
                try:
                    tid = int(raw_turno)
                except (TypeError, ValueError):
                    raise ValidationError('Selezionare un turno valido.') from None
                turno_obj = TurnoLavorativoAziendale.objects.filter(pk=tid).select_related(
                    'configurazione'
                ).first()
                if not turno_obj:
                    raise ValidationError('Selezionare un turno valido.') from None
                dal, al = date_validita_default_per_turno(turno_obj, self.dipendente)
                cleaned_data['data_inizio'] = dal
                if not raw_df and not data_fine:
                    cleaned_data['data_fine'] = al
        elif raw_di or raw_df or data_inizio or data_fine:
            raise ValidationError('Selezionare un turno se si inseriscono le date.')
        return cleaned_data


class DipendenteForm(forms.ModelForm):
    """Form per creazione/modifica dipendente"""

    utente = forms.ModelChoiceField(
        queryset=User.objects.filter(ruoli__codice='dipendente'),
        required=False,
        label='Utente collegato',
        help_text='Opzionale: seleziona un utente per collegarlo al dipendente',
    )
    comune_nascita_estero = forms.CharField(
        required=False,
        label='Citta estera di nascita',
        widget=forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Es. BUCAREST'}),
    )
    citta_residenza_estero = forms.CharField(
        required=False,
        label='Citta estera di residenza',
        widget=forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Es. LONDON'}),
    )
    domicilio_citta_estero = forms.CharField(
        required=False,
        label='Citta estera di domicilio',
        widget=forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Es. PARIS'}),
    )
    
    class Meta:
        model = Dipendente
        fields = [
            'azienda',
            'matricola',
            'codice_fiscale',
            'nome',
            'cognome',
            'data_nascita',
            'paese_nascita',
            'regione_nascita',
            'provincia_nascita',
            'comune_nascita',
            'comune_nascita_estero',
            'luogo_nascita',
            'sesso',
            'cittadinanza',
            'paese_residenza',
            'regione_residenza',
            'provincia',
            'citta',
            'citta_residenza_estero',
            'cap',
            'indirizzo',
            'domicilio_uguale_residenza',
            'paese_domicilio',
            'domicilio_regione',
            'domicilio_provincia',
            'domicilio_comune',
            'domicilio_citta_estero',
            'domicilio_cap',
            'domicilio_indirizzo',
            'email',
            'telefono',
            'data_assunzione',
            'data_cessazione',
            'ruolo',
            'livello',
            'mansione',
            'stato',
            'utente',
        ]
        widgets = {
            'data_nascita': forms.DateInput(format='%Y-%m-%d', attrs={'type': 'date', 'class': 'form-control'}),
            'data_assunzione': forms.DateInput(format='%Y-%m-%d', attrs={'type': 'date', 'class': 'form-control'}),
            'data_cessazione': forms.DateInput(format='%Y-%m-%d', attrs={'type': 'date', 'class': 'form-control'}),
            'azienda': forms.Select(attrs={'class': 'form-control'}),
            'matricola': forms.NumberInput(attrs={'class': 'form-control', 'min': 1, 'placeholder': 'Vuoto = automatico'}),
            'codice_fiscale': forms.TextInput(attrs={'class': 'form-control'}),
            'nome': forms.TextInput(attrs={'class': 'form-control'}),
            'cognome': forms.TextInput(attrs={'class': 'form-control'}),
            'cittadinanza': forms.Select(attrs={'class': 'form-control'}),
            'luogo_nascita': forms.HiddenInput(),
            'paese_nascita': forms.Select(attrs={'class': 'form-control'}),
            'regione_nascita': forms.Select(attrs={'class': 'form-control', 'data-geo-role': 'regione-nascita'}),
            'provincia_nascita': forms.Select(attrs={'class': 'form-control', 'data-geo-role': 'provincia-nascita'}),
            'comune_nascita': forms.Select(attrs={'class': 'form-control', 'data-geo-role': 'comune-nascita'}),
            'sesso': forms.Select(attrs={'class': 'form-control'}),
            'indirizzo': forms.TextInput(attrs={'class': 'form-control'}),
            'cap': forms.TextInput(attrs={'class': 'form-control'}),
            'citta': forms.Select(attrs={'class': 'form-control', 'data-geo-role': 'comune-residenza'}),
            'provincia': forms.Select(attrs={'class': 'form-control', 'data-geo-role': 'provincia-residenza'}),
            'paese_residenza': forms.Select(attrs={'class': 'form-control'}),
            'regione_residenza': forms.Select(attrs={'class': 'form-control', 'data-geo-role': 'regione-residenza'}),
            'domicilio_uguale_residenza': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
            'domicilio_indirizzo': forms.TextInput(attrs={'class': 'form-control'}),
            'domicilio_cap': forms.TextInput(attrs={'class': 'form-control'}),
            'domicilio_comune': forms.Select(attrs={'class': 'form-control', 'data-geo-role': 'comune-domicilio'}),
            'domicilio_provincia': forms.Select(attrs={'class': 'form-control', 'data-geo-role': 'provincia-domicilio'}),
            'paese_domicilio': forms.Select(attrs={'class': 'form-control'}),
            'domicilio_regione': forms.Select(attrs={'class': 'form-control', 'data-geo-role': 'regione-domicilio'}),
            'email': forms.EmailInput(attrs={'class': 'form-control'}),
            'telefono': forms.TextInput(attrs={'class': 'form-control'}),
            'ruolo': forms.TextInput(attrs={'class': 'form-control'}),
            'livello': forms.TextInput(attrs={'class': 'form-control'}),
            'mansione': forms.Select(attrs={'class': 'form-control'}),
            'stato': forms.Select(attrs={'class': 'form-control'}),
        }
        labels = {
            'paese_nascita': 'Paese di nascita (stato sul certificato)',
            'cittadinanza': 'Cittadinanza attuale',
        }

    def __init__(self, *args, **kwargs):
        azienda_operativa = kwargs.pop('azienda_operativa', None)
        for_dipendente = kwargs.pop('for_dipendente', False)
        super().__init__(*args, **kwargs)
        self._azienda_operativa = azienda_operativa
        self.fields['matricola'].required = False
        self.fields['matricola'].help_text = (
            'Numero univoco nell’azienda. Lasciare vuoto in creazione per assegnazione automatica.'
        )
        def _set_choices(field_name, choices):
            self.fields[field_name].choices = choices
            if hasattr(self.fields[field_name].widget, 'choices'):
                self.fields[field_name].widget.choices = choices

        reg_choices = [('', '— Seleziona —'), ('ESTERO', 'ESTERO')] + [(r, r.title()) for r in regioni_it()]
        paesi = paesi_istat()
        paese_choices = [('', '— Seleziona —')] + [
            (p['nome'], f"{p['nome'].title()} ({p['codice_at']})" if p.get('codice_at') else p['nome'].title())
            for p in paesi
        ]
        citt_choices = [('', '— Seleziona —'), ('ITALIANA', 'Italiana')] + [
            (p['nome'], f"{p['nome'].title()} ({p['codice_at']})" if p.get('codice_at') else p['nome'].title())
            for p in paesi if p['nome'] != 'ITALIA'
        ]
        _set_choices('cittadinanza', citt_choices)
        _set_choices('paese_nascita', paese_choices)
        _set_choices('paese_residenza', paese_choices)
        _set_choices('paese_domicilio', paese_choices)
        fonte_paesi = 'Elenco ufficiale ISTAT unita territoriali estere (allineato codici AT/Agenzia Entrate).'
        self.fields['paese_nascita'].help_text = (
            f'{fonte_paesi} Indica lo Stato risultante dall’atto di nascita '
            '(per nati in Italia: ITALIA). Non coincide sempre con la cittadinanza.'
        )
        self.fields['cittadinanza'].help_text = (
            'Cittadinanza giuridica (documento d’identità / normativa). '
            'Valore «Italiana» per cittadini italiani; altrimenti lo Stato di cittadinanza. '
            'Può differire dal paese di nascita (es. italiano nato in Francia). '
            f'Elenco stati come sopra ({fonte_paesi})'
        )
        self.fields['paese_residenza'].help_text = fonte_paesi
        self.fields['paese_domicilio'].help_text = fonte_paesi
        _set_choices('regione_nascita', reg_choices)
        _set_choices('regione_residenza', reg_choices)
        _set_choices('domicilio_regione', reg_choices)
        _set_choices('provincia', [('', '— Seleziona regione —')])
        _set_choices('citta', [('', '— Seleziona provincia —')])
        _set_choices('provincia_nascita', [('', '— Seleziona regione —')])
        _set_choices('comune_nascita', [('', '— Seleziona provincia —')])
        _set_choices('domicilio_provincia', [('', '— Seleziona regione —')])
        _set_choices('domicilio_comune', [('', '— Seleziona provincia —')])

        # Nuovo dipendente: default nascita su Palermo (Sicilia / PA).
        if not self.is_bound and not getattr(self.instance, 'pk', None):
            self.initial.setdefault('paese_nascita', 'ITALIA')
            self.initial.setdefault('cittadinanza', 'ITALIANA')
            self.initial.setdefault('regione_nascita', 'SICILIA')
            self.initial.setdefault('provincia_nascita', 'PA')
            self.initial.setdefault('comune_nascita', 'PALERMO')

        self._populate_geo_initial_choices()
        self.fields['comune_nascita_estero'].initial = getattr(self.instance, 'comune_nascita', '')
        self.fields['citta_residenza_estero'].initial = getattr(self.instance, 'citta', '')
        self.fields['domicilio_citta_estero'].initial = getattr(self.instance, 'domicilio_comune', '')

        # Se c'è un'azienda operativa, la preseleziona e rende il campo read-only
        if azienda_operativa:
            self.fields['azienda'].initial = azienda_operativa
            self.fields['azienda'].disabled = True
            self.fields['azienda'].required = False

        # Il dipendente può aggiornare solo recapiti anagrafici base.
        # I dati contrattuali restano bloccati e passano da integrazione formale.
        if for_dipendente:
            campi_modificabili = {
                'indirizzo', 'cap', 'citta', 'provincia', 'regione_residenza',
                'domicilio_uguale_residenza', 'domicilio_indirizzo', 'domicilio_cap',
                'domicilio_comune', 'domicilio_provincia', 'domicilio_regione',
                'email', 'telefono',
            }
            for nome_campo, campo in self.fields.items():
                if nome_campo not in campi_modificabili:
                    campo.disabled = True

    def _populate_geo_initial_choices(self):
        def _set_choices(field_name, choices):
            self.fields[field_name].choices = choices
            if hasattr(self.fields[field_name].widget, 'choices'):
                self.fields[field_name].widget.choices = choices

        reg_n = (self.initial.get('regione_nascita') or getattr(self.instance, 'regione_nascita', '')).strip().upper()
        prov_n = (self.initial.get('provincia_nascita') or getattr(self.instance, 'provincia_nascita', '')).strip().upper()
        if reg_n:
            if reg_n == 'ESTERO':
                _set_choices('provincia_nascita', [('', '— Non prevista per estero —')])
                _set_choices('comune_nascita', [('', '— Inserisci citta estera —')])
            else:
                provs = province_per_regione(reg_n)
                _set_choices('provincia_nascita', [('', '— Seleziona —')] + [
                    (p['sigla'] or p['nome'], f"{p['nome']} ({p['sigla']})" if p['sigla'] else p['nome']) for p in provs
                ])
                if prov_n:
                    comuni = comuni_per_regione_provincia(reg_n, prov_n)
                    _set_choices('comune_nascita', [('', '— Seleziona —')] + [
                        (c['nome'], f"{c['nome'].title()} ({c['codice_catastale']})" if c.get('codice_catastale') else c['nome'].title())
                        for c in comuni
                    ])

        reg_r = (self.initial.get('regione_residenza') or getattr(self.instance, 'regione_residenza', '')).strip().upper()
        prov_r = (self.initial.get('provincia') or getattr(self.instance, 'provincia', '')).strip().upper()
        if reg_r:
            if reg_r == 'ESTERO':
                _set_choices('provincia', [('', '— Non prevista per estero —')])
                _set_choices('citta', [('', '— Inserisci citta estera —')])
            else:
                provs = province_per_regione(reg_r)
                _set_choices('provincia', [('', '— Seleziona —')] + [
                    (p['sigla'] or p['nome'], f"{p['nome']} ({p['sigla']})" if p['sigla'] else p['nome']) for p in provs
                ])
                if prov_r:
                    comuni = comuni_per_regione_provincia(reg_r, prov_r)
                    _set_choices('citta', [('', '— Seleziona —')] + [
                        (c['nome'], f"{c['nome'].title()} ({c['codice_catastale']})" if c.get('codice_catastale') else c['nome'].title())
                        for c in comuni
                    ])

        reg_d = (self.initial.get('domicilio_regione') or getattr(self.instance, 'domicilio_regione', '')).strip().upper()
        prov_d = (self.initial.get('domicilio_provincia') or getattr(self.instance, 'domicilio_provincia', '')).strip().upper()
        if reg_d:
            if reg_d == 'ESTERO':
                _set_choices('domicilio_provincia', [('', '— Non prevista per estero —')])
                _set_choices('domicilio_comune', [('', '— Inserisci citta estera —')])
            else:
                provs = province_per_regione(reg_d)
                _set_choices('domicilio_provincia', [('', '— Seleziona —')] + [
                    (p['sigla'] or p['nome'], f"{p['nome']} ({p['sigla']})" if p['sigla'] else p['nome']) for p in provs
                ])
                if prov_d:
                    comuni = comuni_per_regione_provincia(reg_d, prov_d)
                    _set_choices('domicilio_comune', [('', '— Seleziona —')] + [
                        (c['nome'], f"{c['nome'].title()} ({c['codice_catastale']})" if c.get('codice_catastale') else c['nome'].title())
                        for c in comuni
                    ])

    def clean_matricola(self):
        m = self.cleaned_data.get('matricola')
        if m is None:
            return m
        azienda = self.cleaned_data.get('azienda')
        if azienda is None and getattr(self.instance, 'azienda_id', None):
            azienda = self.instance.azienda
        if azienda is None and self._azienda_operativa:
            azienda = self._azienda_operativa
        if azienda is None:
            return m
        qs = Dipendente.objects.filter(azienda=azienda, matricola=m)
        if self.instance.pk:
            qs = qs.exclude(pk=self.instance.pk)
        if qs.exists():
            raise ValidationError('Questa matricola è già assegnata a un altro dipendente della stessa azienda.')
        return m

    def clean(self):
        cleaned = super().clean()
        from anagrafiche.codice_fiscale_it import merge_dipendente_da_codice_fiscale

        merge_dipendente_da_codice_fiscale(cleaned)
        comune_nascita = (cleaned.get('comune_nascita') or '').strip().upper()
        provincia_nascita = (cleaned.get('provincia_nascita') or '').strip().upper()
        regione_nascita = (cleaned.get('regione_nascita') or '').strip().upper()
        comune_nascita_estero = (cleaned.get('comune_nascita_estero') or '').strip().upper()
        if regione_nascita == 'ESTERO' and comune_nascita_estero:
            cleaned['comune_nascita'] = comune_nascita_estero
            cleaned['provincia_nascita'] = ''
            cleaned['luogo_nascita'] = f"{comune_nascita_estero} ({(cleaned.get('paese_nascita') or 'ESTERO').strip().upper()})"
        elif comune_nascita:
            cleaned['luogo_nascita'] = f"{comune_nascita}{' (' + provincia_nascita + ')' if provincia_nascita else ''}"

        reg_r = (cleaned.get('regione_residenza') or '').strip().upper()
        prov_r = (cleaned.get('provincia') or '').strip().upper()
        com_r = (cleaned.get('citta') or '').strip().upper()
        if reg_r == 'ESTERO':
            estero = (cleaned.get('citta_residenza_estero') or '').strip().upper()
            if estero:
                cleaned['citta'] = estero
                cleaned['provincia'] = ''
        elif reg_r and prov_r and com_r and not (cleaned.get('cap') or '').strip():
            for item in comuni_per_regione_provincia(reg_r, prov_r):
                if (item.get('nome') or '').strip().upper() == com_r and item.get('cap'):
                    cleaned['cap'] = item['cap']
                    break

        if cleaned.get('domicilio_uguale_residenza'):
            cleaned['domicilio_indirizzo'] = cleaned.get('indirizzo', '')
            cleaned['domicilio_cap'] = cleaned.get('cap', '')
            cleaned['domicilio_comune'] = cleaned.get('citta', '')
            cleaned['domicilio_provincia'] = cleaned.get('provincia', '')
            cleaned['paese_domicilio'] = cleaned.get('paese_residenza', '')
            cleaned['domicilio_regione'] = cleaned.get('regione_residenza', '')
        else:
            reg_d = (cleaned.get('domicilio_regione') or '').strip().upper()
            if reg_d == 'ESTERO':
                estero = (cleaned.get('domicilio_citta_estero') or '').strip().upper()
                if estero:
                    cleaned['domicilio_comune'] = estero
                    cleaned['domicilio_provincia'] = ''
        return cleaned


def _compose_indirizzo_sede_legale_da_cleaned(cleaned: dict) -> str:
    """Una riga per compatibilità (campo indirizzo su Azienda)."""
    parts = []
    v = (cleaned.get('sede_legale_via') or '').strip()
    if v:
        parts.append(v)
    cap = (cleaned.get('sede_legale_cap') or '').strip()
    com = (cleaned.get('sede_legale_comune') or '').strip()
    prov = (cleaned.get('sede_legale_provincia') or '').strip().upper()
    tail_parts = []
    if cap:
        tail_parts.append(cap)
    if com:
        tail_parts.append(com.title() if com.isupper() else com)
    tail = ' '.join(tail_parts).strip()
    if prov and tail:
        tail = f'{tail} ({prov})'
    elif prov:
        tail = f'({prov})'
    if tail:
        parts.append(tail)
    out = ', '.join(parts)
    return out[:255] if out else ''


def indirizzo_sede_legale_riga_da_azienda(azienda) -> str:
    """Una riga per geocoding e ConfigurazioneSistema: campo indirizzo o pezzi sede legale."""
    if azienda is None:
        return ''
    one = (getattr(azienda, 'indirizzo', None) or '').strip()
    if one:
        return one[:255]
    composed = _compose_indirizzo_sede_legale_da_cleaned({
        'sede_legale_via': getattr(azienda, 'sede_legale_via', None) or '',
        'sede_legale_cap': getattr(azienda, 'sede_legale_cap', None) or '',
        'sede_legale_comune': getattr(azienda, 'sede_legale_comune', None) or '',
        'sede_legale_provincia': getattr(azienda, 'sede_legale_provincia', None) or '',
    })
    return (composed or '')[:255]


def query_geocode_nominatim_da_azienda(azienda) -> str:
    """
    Stessa logica di buildGeocodeQuery() in templates/anagrafiche/form_azienda.html:
    stringa inviata a Nominatim = sede lavorativa (se sufficiente) oppure
    via, CAP, comune, provincia, Italia (non la sola riga indirizzo riepilogativa).
    """
    if azienda is None:
        return ''
    lav = (getattr(azienda, 'sede_lavorativa_indirizzo', None) or '').strip()
    if len(lav) >= 5:
        if re.search(r',\s*(italia|italy)\s*$', lav, re.I):
            return lav
        return f'{lav}, Italia'
    parts = []
    via = (getattr(azienda, 'sede_legale_via', None) or '').strip()
    if via:
        parts.append(via)
    cap = (getattr(azienda, 'sede_legale_cap', None) or '').strip()
    if cap:
        parts.append(cap)
    com = (getattr(azienda, 'sede_legale_comune', None) or '').strip()
    if com:
        com = re.sub(r'\s*\([^)]*\)\s*$', '', com).strip() or com
        parts.append(com)
    prov = (getattr(azienda, 'sede_legale_provincia', None) or '').strip()
    if prov:
        parts.append(prov)
    parts.append('Italia')
    q = ', '.join(parts)
    if len(q.strip()) >= 8:
        return q
    fb = indirizzo_sede_legale_riga_da_azienda(azienda).strip()
    if len(fb) >= 5:
        if not re.search(r',\s*(italia|italy)\s*$', fb, re.I):
            fb = f'{fb}, Italia'
        return fb[:500]
    return q


class AziendaForm(forms.ModelForm):
    """Form per configurazione azienda con tipizzazione contrattuale."""

    class Meta:
        model = Azienda
        fields = [
            'nome',
            'partita_iva',
            'sede_legale_regione',
            'sede_legale_provincia',
            'sede_legale_comune',
            'sede_legale_cap',
            'sede_legale_via',
            'amministratore_pro_tempore_nome',
            'amministratore_pro_tempore_ruolo',
            'sede_lavorativa_indirizzo',
            'sede_lavorativa_lat',
            'sede_lavorativa_lon',
            'sede_lavorativa_raggio_m',
            'email',
            'telefono',
            'tipologia_dimensionale',
            'ccnl_predefinito',
            'tipo_contratto_predefinito',
            'ore_settimanali_standard',
            'ore_giornaliere_standard',
            'data_attivazione_contratto',
            'note_contrattuali',
        ]
        widgets = {
            'nome': forms.TextInput(attrs={'class': 'form-control'}),
            'partita_iva': forms.TextInput(attrs={'class': 'form-control'}),
            'sede_legale_regione': forms.Select(attrs={'class': 'form-control', 'data-geo-role': 'regione-sede-azienda'}),
            'sede_legale_provincia': forms.Select(attrs={'class': 'form-control', 'data-geo-role': 'provincia-sede-azienda'}),
            'sede_legale_comune': forms.Select(attrs={'class': 'form-control', 'data-geo-role': 'comune-sede-azienda'}),
            'sede_legale_cap': forms.TextInput(attrs={'class': 'form-control', 'maxlength': '10', 'placeholder': 'CAP'}),
            'sede_legale_via': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Via, piazza e numero civico'}),
            'amministratore_pro_tempore_nome': forms.TextInput(attrs={'class': 'form-control'}),
            'amministratore_pro_tempore_ruolo': forms.TextInput(attrs={'class': 'form-control'}),
            'sede_lavorativa_indirizzo': forms.TextInput(attrs={'class': 'form-control'}),
            'sede_lavorativa_lat': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.000001'}),
            'sede_lavorativa_lon': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.000001'}),
            'sede_lavorativa_raggio_m': forms.NumberInput(attrs={'class': 'form-control', 'min': '1', 'step': '1'}),
            'email': forms.EmailInput(attrs={'class': 'form-control'}),
            'telefono': forms.TextInput(attrs={'class': 'form-control'}),
            'tipologia_dimensionale': forms.Select(attrs={'class': 'form-select'}),
            'ccnl_predefinito': forms.Select(attrs={'class': 'form-select'}),
            'tipo_contratto_predefinito': forms.Select(attrs={'class': 'form-select'}),
            'ore_settimanali_standard': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01'}),
            'ore_giornaliere_standard': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01'}),
            'data_attivazione_contratto': forms.DateInput(attrs={'class': 'form-control', 'type': 'date'}),
            'note_contrattuali': forms.Textarea(attrs={'class': 'form-control', 'rows': 3}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        def _set_choices(field_name, choices):
            self.fields[field_name].choices = choices
            if hasattr(self.fields[field_name].widget, 'choices'):
                self.fields[field_name].widget.choices = choices

        reg_choices = [('', '— Seleziona —')] + [(r, r.title()) for r in regioni_it()]
        _set_choices('sede_legale_regione', reg_choices)
        _set_choices('sede_legale_provincia', [('', '— Seleziona regione —')])
        _set_choices('sede_legale_comune', [('', '— Seleziona provincia —')])

        if not self.is_bound and not getattr(self.instance, 'pk', None):
            self.initial.setdefault('sede_legale_regione', 'SICILIA')
            self.initial.setdefault('sede_legale_provincia', 'PA')
            self.initial.setdefault('sede_legale_comune', 'PALERMO')

        self._populate_geo_sede_legale()

    def _populate_geo_sede_legale(self):
        def _set_choices(field_name, choices):
            self.fields[field_name].choices = choices
            if hasattr(self.fields[field_name].widget, 'choices'):
                self.fields[field_name].widget.choices = choices

        reg = (self.initial.get('sede_legale_regione') or getattr(self.instance, 'sede_legale_regione', '') or '').strip().upper()
        prov = (self.initial.get('sede_legale_provincia') or getattr(self.instance, 'sede_legale_provincia', '') or '').strip().upper()
        if reg:
            provs = province_per_regione(reg)
            _set_choices('sede_legale_provincia', [('', '— Seleziona —')] + [
                (p['sigla'] or p['nome'], f"{p['nome']} ({p['sigla']})" if p['sigla'] else p['nome'])
                for p in provs
            ])
            if prov:
                comuni = comuni_per_regione_provincia(reg, prov)
                _set_choices('sede_legale_comune', [('', '— Seleziona —')] + [
                    (
                        c['nome'],
                        f"{c['nome'].title()} ({c['codice_catastale']})" if c.get('codice_catastale') else c['nome'].title(),
                    )
                    for c in comuni
                ])

    def clean(self):
        cleaned_data = super().clean()
        ore_sett = cleaned_data.get('ore_settimanali_standard')
        ore_gior = cleaned_data.get('ore_giornaliere_standard')

        if ore_sett is not None and ore_sett <= 0:
            self.add_error('ore_settimanali_standard', 'Inserire un valore positivo.')
        if ore_gior is not None and ore_gior <= 0:
            self.add_error('ore_giornaliere_standard', 'Inserire un valore positivo.')

        lat = cleaned_data.get('sede_lavorativa_lat')
        lon = cleaned_data.get('sede_lavorativa_lon')
        raggio = cleaned_data.get('sede_lavorativa_raggio_m')

        if lat is not None and not (-90 <= lat <= 90):
            self.add_error('sede_lavorativa_lat', 'Latitudine non valida (range: -90 / +90).')
        if lon is not None and not (-180 <= lon <= 180):
            self.add_error('sede_lavorativa_lon', 'Longitudine non valida (range: -180 / +180).')
        if raggio is not None and raggio <= 0:
            self.add_error('sede_lavorativa_raggio_m', 'Il raggio deve essere maggiore di zero.')

        reg = (cleaned_data.get('sede_legale_regione') or '').strip().upper()
        prov = (cleaned_data.get('sede_legale_provincia') or '').strip().upper()
        com = (cleaned_data.get('sede_legale_comune') or '').strip().upper()
        cap = (cleaned_data.get('sede_legale_cap') or '').strip()
        via = (cleaned_data.get('sede_legale_via') or '').strip()

        for key in ('sede_legale_regione', 'sede_legale_provincia', 'sede_legale_comune', 'sede_legale_cap', 'sede_legale_via'):
            v = cleaned_data.get(key)
            if isinstance(v, str):
                cleaned_data[key] = v.strip()

        if reg and prov and com and not cap:
            for item in comuni_per_regione_provincia(reg, prov):
                if (item.get('nome') or '').strip().upper() == com and item.get('cap'):
                    cleaned_data['sede_legale_cap'] = item['cap']
                    break

        composed = _compose_indirizzo_sede_legale_da_cleaned(cleaned_data)
        if not composed.strip():
            self.add_error(
                'sede_legale_via',
                'Indica via e civico, oppure CAP e comune (con regione e provincia), per comporre l’indirizzo della sede legale.',
            )

        return cleaned_data

    def save(self, commit=True):
        obj = super().save(commit=False)
        obj.indirizzo = _compose_indirizzo_sede_legale_da_cleaned(self.cleaned_data)
        if commit:
            obj.save()
        return obj
