"""Form per registrazione pagamenti (DARE in estratto conto) nel partitario netti."""

from __future__ import annotations

from decimal import Decimal

from django import forms
from django.core.exceptions import ValidationError
from django.db.models import Case, IntegerField, Value, When

from anagrafiche.models import Dipendente

from .constants import MESI_SCELTA
from .models import MovimentoPartitarioNettoDipendente


class AnnoContabileIntegerField(forms.IntegerField):
    """
    Anno calendario (2000–2100): accetta anche stringhe localizzate con migliaia (es. ``2.026``).
    """

    def to_python(self, value):  # type: ignore[override]
        if value in self.empty_values:
            return None
        if isinstance(value, int):
            return value
        t = str(value).strip().replace("\xa0", "").replace(" ", "")
        for ch in (".", ",", "'", "\u2019"):
            t = t.replace(ch, "")
        if not t or not t.isdigit():
            raise ValidationError(self.error_messages["invalid"], code="invalid")
        try:
            return int(t)
        except ValueError as err:
            raise ValidationError(self.error_messages["invalid"], code="invalid") from err


class PagamentoNettoDipendenteForm(forms.Form):
    dipendente = forms.ModelChoiceField(
        label="Dipendente",
        queryset=Dipendente.objects.none(),
        required=True,
    )
    data_pagamento = forms.DateField(
        label="Data pagamento",
        required=True,
        localize=False,
        widget=forms.DateInput(
            format="%Y-%m-%d",
            attrs={"type": "date", "class": "form-control form-control-sm"},
        ),
    )
    importo = forms.DecimalField(
        label="Importo pagato (€)",
        max_digits=14,
        decimal_places=2,
        min_value=Decimal("0.01"),
        required=True,
    )
    metodo_pagamento = forms.ChoiceField(
        label="Modalità di pagamento",
        choices=MovimentoPartitarioNettoDipendente.MetodoPagamento.choices,
        required=True,
    )
    anno_competenza = AnnoContabileIntegerField(
        label="Anno di competenza",
        min_value=2000,
        max_value=2100,
        required=True,
        localize=False,
        widget=forms.NumberInput(attrs={"min": 2000, "max": 2100, "step": 1}),
        help_text="Anno della busta / competenza a cui si riferisce il pagamento nel riepilogo.",
    )
    mese_competenza = forms.TypedChoiceField(
        label="Mese di competenza",
        coerce=int,
        choices=MESI_SCELTA,
        required=True,
        localize=False,
    )
    causale = forms.CharField(
        label="Causale",
        max_length=2000,
        required=True,
        strip=True,
        widget=forms.Textarea(attrs={"rows": 2, "class": "form-control form-control-sm", "placeholder": "es. Saldo netto gennaio 2026, acconto ferie, …"}),
        help_text="Motivazione contabile obbligatoria (sostituisce le vecchie note libere).",
    )
    allegato_pdf = forms.FileField(
        label="Allegato PDF (ricevuta / copia bonifico)",
        required=False,
        help_text="Opzionale: carica dopo aver compilato i dati; viene archiviato come documento aziendale.",
    )

    def __init__(
        self,
        *args,
        azienda_id: int,
        movimento: MovimentoPartitarioNettoDipendente | None = None,
        **kwargs,
    ) -> None:
        self._azienda_id = azienda_id
        self._movimento = movimento
        if movimento is not None:
            if movimento.tipo_movimento != MovimentoPartitarioNettoDipendente.TipoMovimento.PAGAMENTO:
                raise ValueError("Il form pagamento accetta solo movimenti di tipo pagamento.")
            ini = dict(kwargs.get("initial") or {})
            ini.setdefault("dipendente", movimento.dipendente_id)
            ini["data_pagamento"] = movimento.data_contabile
            ini["importo"] = movimento.importo
            ini["metodo_pagamento"] = movimento.metodo_pagamento
            ini["anno_competenza"] = movimento.anno
            ini["mese_competenza"] = movimento.mese
            ini["causale"] = (movimento.causale or "").strip()
            kwargs["initial"] = ini

        super().__init__(*args, **kwargs)

        qs = (
            Dipendente.objects.filter(azienda_id=azienda_id)
            .annotate(
                _prio=Case(
                    When(stato="attivo", then=Value(0)),
                    default=Value(1),
                    output_field=IntegerField(),
                )
            )
            .order_by("_prio", "cognome", "nome")
        )
        self.fields["dipendente"].queryset = qs

        def _label_dip(obj: Dipendente) -> str:
            base = f"{obj.cognome} {obj.nome}".strip()
            if (obj.stato or "") == "attivo":
                return base
            return f"{base} ({obj.get_stato_display()})"

        self.fields["dipendente"].label_from_instance = _label_dip

        self.fields["metodo_pagamento"].widget.attrs.setdefault("class", "form-select form-select-sm")
        self.fields["dipendente"].widget.attrs.setdefault("class", "form-select form-select-sm")
        self.fields["importo"].widget.attrs.setdefault("class", "form-control form-control-sm")
        self.fields["anno_competenza"].widget.attrs.setdefault("class", "form-control form-control-sm")
        self.fields["mese_competenza"].widget.attrs.setdefault("class", "form-select form-select-sm")
        self.fields["allegato_pdf"].widget.attrs.setdefault("class", "form-control form-control-sm")

        if movimento is not None:
            self.fields["allegato_pdf"].required = False
            self.fields["allegato_pdf"].help_text = (
                "Lascia vuoto per mantenere il PDF attuale; carica un nuovo file per sostituire la ricevuta collegata."
            )
