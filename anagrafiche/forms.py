from datetime import date

from django import forms
from django.core.exceptions import ValidationError
from .models import Dipendente, Azienda
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
    
    class Meta:
        model = Dipendente
        fields = [
            'azienda',
            'matricola',
            'codice_fiscale',
            'nome',
            'cognome',
            'data_nascita',
            'indirizzo',
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
            'indirizzo': forms.TextInput(attrs={'class': 'form-control'}),
            'email': forms.EmailInput(attrs={'class': 'form-control'}),
            'telefono': forms.TextInput(attrs={'class': 'form-control'}),
            'ruolo': forms.TextInput(attrs={'class': 'form-control'}),
            'livello': forms.TextInput(attrs={'class': 'form-control'}),
            'mansione': forms.Select(attrs={'class': 'form-control'}),
            'stato': forms.Select(attrs={'class': 'form-control'}),
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

        # Se c'è un'azienda operativa, la preseleziona e rende il campo read-only
        if azienda_operativa:
            self.fields['azienda'].initial = azienda_operativa
            self.fields['azienda'].disabled = True
            self.fields['azienda'].required = False

        # Il dipendente può aggiornare solo recapiti anagrafici base.
        # I dati contrattuali restano bloccati e passano da integrazione formale.
        if for_dipendente:
            campi_modificabili = {'indirizzo', 'email', 'telefono'}
            for nome_campo, campo in self.fields.items():
                if nome_campo not in campi_modificabili:
                    campo.disabled = True

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


class AziendaForm(forms.ModelForm):
    """Form per configurazione azienda con tipizzazione contrattuale."""

    class Meta:
        model = Azienda
        fields = [
            'nome',
            'partita_iva',
            'indirizzo',
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
            'indirizzo': forms.TextInput(attrs={'class': 'form-control'}),
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

        return cleaned_data
