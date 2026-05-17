from decimal import Decimal

from django import forms
from anagrafiche.models import Dipendente

from .models import (
    Presenza,
    ConfigurazioneOrarioAnnuale,
    FasciaAperturaSettimanale,
    TurnoLavorativoAziendale,
    ConfigurazioneOrarioMensile,
    FasciaAperturaMensile,
    SaldoMonteDipendente,
)


GIORNI_SETT_CHOICES = [
    (0, 'Lunedì'),
    (1, 'Martedì'),
    (2, 'Mercoledì'),
    (3, 'Giovedì'),
    (4, 'Venerdì'),
    (5, 'Sabato'),
    (6, 'Domenica'),
]


class SaldoMonteInizialeForm(forms.Form):
    """Impostazione / aggiornamento saldo iniziale per tipo monte e anno di competenza."""

    dipendente = forms.ModelChoiceField(
        queryset=Dipendente.objects.none(),
        label='Dipendente',
    )
    tipo_monte = forms.ChoiceField(choices=SaldoMonteDipendente.TIPO_MONTE_CHOICES)
    anno_competenza = forms.IntegerField(min_value=2000, max_value=2100, label='Anno competenza')
    saldo_iniziale = forms.DecimalField(
        max_digits=8,
        decimal_places=2,
        label='Saldo iniziale',
        help_text='Di solito da ultima busta o migrazione; il saldo attuale include anche i movimenti registrati.',
    )
    data_saldo_iniziale = forms.DateField(
        required=False,
        label='Data riferimento',
        widget=forms.DateInput(attrs={'type': 'date', 'class': 'form-control form-control-sm'}),
    )
    note = forms.CharField(
        required=False,
        label='Note',
        widget=forms.Textarea(attrs={'rows': 2, 'class': 'form-control form-control-sm'}),
    )

    def clean_saldo_iniziale(self):
        v = self.cleaned_data['saldo_iniziale']
        if v < Decimal('-999999.99'):
            raise forms.ValidationError('Valore fuori intervallo.')
        return v

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['dipendente'].widget.attrs.update({'class': 'form-select form-select-sm'})
        self.fields['tipo_monte'].widget.attrs.update({'class': 'form-select form-select-sm'})
        self.fields['anno_competenza'].widget.attrs.update({'class': 'form-control form-control-sm'})
        self.fields['saldo_iniziale'].widget.attrs.update(
            {'class': 'form-control form-control-sm', 'step': '0.01'},
        )


class PresenzaForm(forms.ModelForm):
    class Meta:
        model = Presenza
        fields = ['data', 'causale', 'ora_entrata', 'ora_uscita', 'ore_straordinario', 'note']
        widgets = {
            'data': forms.DateInput(attrs={'type': 'date', 'class': 'form-control form-control-sm'}),
            'causale': forms.Select(attrs={'class': 'form-select form-select-sm'}),
            'ora_entrata': forms.TimeInput(attrs={'type': 'time', 'class': 'form-control form-control-sm'}),
            'ora_uscita': forms.TimeInput(attrs={'type': 'time', 'class': 'form-control form-control-sm'}),
            'ore_straordinario': forms.NumberInput(attrs={'class': 'form-control form-control-sm', 'step': '0.5', 'min': '0'}),
            'note': forms.TextInput(attrs={'class': 'form-control form-control-sm'}),
        }


class GiornoPresenzaForm(forms.ModelForm):
    """Form compatto per la modifica di un singolo giorno nel calendario."""
    class Meta:
        model = Presenza
        fields = ['causale', 'ora_entrata', 'ora_uscita', 'ora_entrata2', 'ora_uscita2', 'ore_straordinario', 'note']
        widgets = {
            'causale': forms.Select(attrs={'class': 'form-select form-select-sm'}),
            'ora_entrata': forms.TimeInput(attrs={'type': 'time', 'class': 'form-control form-control-sm'}),
            'ora_uscita': forms.TimeInput(attrs={'type': 'time', 'class': 'form-control form-control-sm'}),
            'ore_straordinario': forms.NumberInput(attrs={
                'class': 'form-control form-control-sm', 'step': '0.5', 'min': '0',
                'placeholder': '0.0'
            }),
            'note': forms.TextInput(attrs={'class': 'form-control form-control-sm', 'placeholder': 'Note…'}),
        }


class ConfigurazioneOrarioAnnualeForm(forms.ModelForm):
    giorni_riposo_settimanale = forms.MultipleChoiceField(
        required=False,
        choices=GIORNI_SETT_CHOICES,
        widget=forms.CheckboxSelectMultiple,
        label='Giorni riposo settimanale',
    )

    class Meta:
        model = ConfigurazioneOrarioAnnuale
        fields = ['giorni_riposo_settimanale', 'genera_presenze_teoriche']
        widgets = {
            'genera_presenze_teoriche': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance and self.instance.pk:
            self.initial['giorni_riposo_settimanale'] = [
                str(x) for x in (self.instance.giorni_riposo_settimanale or [])
            ]

    def clean_giorni_riposo_settimanale(self):
        values = self.cleaned_data.get('giorni_riposo_settimanale') or []
        return sorted({int(v) for v in values})


class FasciaAperturaSettimanaleForm(forms.ModelForm):
    class Meta:
        model = FasciaAperturaSettimanale
        fields = [
            'giorno_settimana', 'chiuso',
            'ora_apertura_mattina', 'ora_chiusura_mattina',
            'ora_apertura_pomeriggio', 'ora_chiusura_pomeriggio',
        ]
        widgets = {
            'giorno_settimana': forms.HiddenInput(),
            'chiuso': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
            'ora_apertura_mattina': forms.TimeInput(attrs={'type': 'time', 'class': 'form-control form-control-sm'}),
            'ora_chiusura_mattina': forms.TimeInput(attrs={'type': 'time', 'class': 'form-control form-control-sm'}),
            'ora_apertura_pomeriggio': forms.TimeInput(attrs={'type': 'time', 'class': 'form-control form-control-sm'}),
            'ora_chiusura_pomeriggio': forms.TimeInput(attrs={'type': 'time', 'class': 'form-control form-control-sm'}),
        }


class TurnoLavorativoAziendaleForm(forms.ModelForm):
    class Meta:
        model = TurnoLavorativoAziendale
        fields = ['nome', 'ora_inizio', 'ora_fine', 'ordine', 'attivo']
        widgets = {
            'nome': forms.TextInput(attrs={'class': 'form-control form-control-sm', 'placeholder': 'Es. Pranzo'}),
            'ora_inizio': forms.TimeInput(attrs={'type': 'time', 'class': 'form-control form-control-sm'}),
            'ora_fine': forms.TimeInput(attrs={'type': 'time', 'class': 'form-control form-control-sm'}),
            'ordine': forms.NumberInput(attrs={'class': 'form-control form-control-sm', 'min': '1'}),
            'attivo': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
        }


class ConfigurazioneOrarioMensileForm(forms.ModelForm):
    giorni_riposo_settimanale = forms.MultipleChoiceField(
        required=False,
        choices=GIORNI_SETT_CHOICES,
        widget=forms.CheckboxSelectMultiple,
        label='Giorni riposo settimanale',
    )

    class Meta:
        model = ConfigurazioneOrarioMensile
        fields = ['giorni_riposo_settimanale', 'genera_presenze_teoriche']
        widgets = {
            'genera_presenze_teoriche': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance and self.instance.pk:
            self.initial['giorni_riposo_settimanale'] = [
                str(x) for x in (self.instance.giorni_riposo_settimanale or [])
            ]

    def clean_giorni_riposo_settimanale(self):
        values = self.cleaned_data.get('giorni_riposo_settimanale') or []
        return sorted({int(v) for v in values})


class FasciaAperturaMensileForm(forms.ModelForm):
    class Meta:
        model = FasciaAperturaMensile
        fields = [
            'giorno_settimana', 'chiuso',
            'ora_apertura_mattina', 'ora_chiusura_mattina',
            'ora_apertura_pomeriggio', 'ora_chiusura_pomeriggio',
        ]
        widgets = {
            'giorno_settimana': forms.HiddenInput(),
            'chiuso': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
            'ora_apertura_mattina': forms.TimeInput(attrs={'type': 'time', 'class': 'form-control form-control-sm'}),
            'ora_chiusura_mattina': forms.TimeInput(attrs={'type': 'time', 'class': 'form-control form-control-sm'}),
            'ora_apertura_pomeriggio': forms.TimeInput(attrs={'type': 'time', 'class': 'form-control form-control-sm'}),
            'ora_chiusura_pomeriggio': forms.TimeInput(attrs={'type': 'time', 'class': 'form-control form-control-sm'}),
        }
