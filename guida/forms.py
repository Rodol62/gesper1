from django import forms

from .models import VoceGuida
from .registry import MODULI


class VoceGuidaForm(forms.ModelForm):
    class Meta:
        model = VoceGuida
        fields = '__all__'

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        choices = [(m['codice'], f"{m['codice']} — {m['titolo']}") for m in MODULI]
        cur = getattr(self.instance, 'codice_modulo', None) or ''
        if cur and not any(cur == c for c, _ in choices):
            choices.insert(0, (cur, f'{cur} (non ancora in registry — aggiornare guida/registry.py)'))
        self.fields['codice_modulo'].widget = forms.Select(choices=choices)
