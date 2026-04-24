from django import forms
from django.db.models import Max, Q
from django.utils import timezone
from decimal import Decimal, ROUND_HALF_UP

from accounts.formatting import euro_it_str
from anagrafiche.models import Dipendente
from .models import (
    AddendumContrattuale,
    PropostaAssunzione,
    ParametroCCNLTurismo,
    RapportoDiLavoro,
    TipoContratto,
    ModuloContrattuale,
    Mansione,
    MansioneLivelloCCNL,
    RegolaNormativaCCNL,
)
from accounts.models import ProfiloCandidato


class ParametroCCNLSelect(forms.Select):
    """Select parametri CCNL con data-livello / data-qualifica per filtro a cascata lato JS."""

    def __init__(self, pk_meta=None, attrs=None):
        super().__init__(attrs=attrs)
        self.pk_meta = pk_meta or {}

    def create_option(self, name, value, label, selected, index, subindex=None, attrs=None):
        option = super().create_option(name, value, label, selected, index, subindex=subindex, attrs=attrs)
        if value is None or value == '':
            return option
        # Django 5+: ModelChoiceIteratorValue; la chiave in pk_meta è str(pk).
        pk = None
        if hasattr(value, 'value'):
            pk = str(value.value)
        else:
            pk = str(value)
        meta = self.pk_meta.get(pk)
        if not meta and pk.isdigit():
            meta = self.pk_meta.get(str(int(pk)))
        if not meta:
            inst = getattr(value, 'instance', None)
            if inst is not None:
                meta = (str(inst.livello).strip(), str(inst.qualifica).strip())
        if meta:
            lv, qu = meta
            option.setdefault('attrs', {})
            option['attrs']['data-livello'] = str(lv).strip()
            option['attrs']['data-qualifica'] = str(qu).strip()
        return option


class ParametroCCNLChoiceField(forms.ModelChoiceField):
    """ModelChoiceField con etichetta leggibile per la voce tabellare (qualifica + importi)."""
    def label_from_instance(self, obj):
        lordo = obj.importo_lordo_mensile if obj.importo_lordo_mensile else obj.paga_base_mensile
        return (
            f'{obj.qualifica} — € {euro_it_str(obj.paga_base_mensile)} base · '
            f'€ {euro_it_str(lordo)} lordo'
        )


class MansioneSelect(forms.Select):
    """Select mansioni con data-livelli per filtro dinamico lato JS."""
    def __init__(self, livelli_map=None, attrs=None):
        super().__init__(attrs=attrs)
        self.livelli_map = livelli_map or {}

    def create_option(self, name, value, label, selected, index, subindex=None, attrs=None):
        option = super().create_option(name, value, label, selected, index, subindex=subindex, attrs=attrs)
        if value in (None, ''):
            return option
        key = str(value.value) if hasattr(value, 'value') else str(value)
        livelli = sorted(self.livelli_map.get(key, []))
        if livelli:
            option.setdefault('attrs', {})
            option['attrs']['data-livelli'] = '|'.join(livelli)
        return option


def _descrizione_posizione_contrattuale(parametro, tipo_contratto, mansione=None, max_len=100):
    """Testo compatto per il campo posizione (mansione + tabella CCNL + tipo orario)."""
    parti = []
    if mansione is not None:
        try:
            parti.append(f'Mansione: {mansione.nome}')
        except Exception:
            pass
    if parametro:
        parti.append(f'Qualifica CCNL: {parametro.qualifica}')
        parti.append(f'Livello: {parametro.livello}')
    if tipo_contratto is not None:
        try:
            parti.append(f'Tipo: {tipo_contratto.nome}')
        except Exception:
            pass
    s = ' · '.join(parti)
    if not s:
        return ''
    if len(s) <= max_len:
        return s
    return s[: max_len - 1].rstrip() + '…'


def _parametri_ccnl_correnti():
    """Parametri CCNL della decorrenza più recente; fallback se le date non sono valorizzate."""
    base = ParametroCCNLTurismo.objects.filter(attivo=True)
    max_dec = base.aggregate(m=Max('decorrenza_validita_da'))['m']
    if max_dec:
        qs = base.filter(decorrenza_validita_da=max_dec).order_by(
            'livello_ordinamento', 'livello', 'qualifica'
        )
        if qs.exists():
            return qs
    max_dec2 = base.exclude(decorrenza_validita_da__isnull=True).aggregate(
        m=Max('decorrenza_validita_da')
    )['m']
    if max_dec2:
        qs = base.filter(decorrenza_validita_da=max_dec2).order_by(
            'livello_ordinamento', 'livello', 'qualifica'
        )
        if qs.exists():
            return qs
    return base.order_by('ccnl', 'versione', 'sezione', 'livello_ordinamento', 'livello', 'qualifica')


class PropostaAssunzioneForm(forms.ModelForm):

    mansionario_file = forms.FileField(
        label='Mansionario allegato',
        required=False,
        help_text='Allega il mansionario specifico per la mansione (PDF, facoltativo)'
    )
    # Solo livello (prima scelta); la voce tabellare viene risolta automaticamente lato form/JS.
    ccnl_livello_scelta = forms.ChoiceField(
        label='Livello CCNL',
        required=False,
        choices=[('', '— Seleziona livello —')],
        widget=forms.Select(attrs={'class': 'form-select'}),
        help_text='Scegli il livello retributivo; la qualifica viene agganciata automaticamente.',
    )

    # Voce tabellare completa (PK); etichetta mostra qualifica + importi
    parametro_ccnl = ParametroCCNLChoiceField(
        label='Qualifica CCNL',
        queryset=ParametroCCNLTurismo.objects.none(),  # impostato in __init__
        required=False,
        empty_label='— Seleziona qualifica (dopo il livello) —',
        widget=ParametroCCNLSelect(attrs={'class': 'form-select'}),
        help_text='Voce tabellare risolta automaticamente dal livello scelto.',
    )
    ferie_annue_giorni = forms.DecimalField(
        label='Ferie annue (giorni)',
        required=False,
        decimal_places=2,
        max_digits=5,
        widget=forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01', 'readonly': 'readonly'}),
        help_text='Parametro annuo per il motore «Calcolatore ferie e ROL» (maturazione mensile e griglia presenze).',
    )
    permessi_annui_ore = forms.DecimalField(
        label='Permessi annui (ore)',
        required=False,
        decimal_places=2,
        max_digits=6,
        widget=forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01', 'readonly': 'readonly'}),
        help_text='ROL annuo in ore per lo stesso motore (rateo mensile e residui teorici in presenze).',
    )
    
    class Meta:
        model = PropostaAssunzione
        fields = [
            'dipendente',
            'modulo',
            'mansione',
            'parametro_ccnl',
            'titolo',
            'note',
            'riferimenti_normativi',
            'dichiarazione_conformita_legale',
            'tipo_contratto',
            'data_inizio_rapporto',
            'data_fine_rapporto',
            'posizione',
            'livello_ccnl',
            'qualifica',
            'stipendio_lordo_mensile',
            'paga_base_mensile',
            'contingenza_mensile',
            'edr_mensile',
            'indennita_mensile',
            'tredicesima',
            'quattordicesima',
            'giorni_ferie_annuali',
            'giorni_permesso_annuali',
            'ore_settimanali',
            'ore_mensili',
            'ore_giornaliere',
            'decorrenza_validita_da',
            'decorrenza_validita_a',
            'scatto_periodicita_mesi',
            'scatto_importo',
            'numero_scatti_massimi',
            'straordinario_diurno_maggiorazione',
            'straordinario_notturno_maggiorazione',
            'straordinario_festivo_maggiorazione',
            'riposi_compensativi_regola',
            'mansionario_file',
        ]
        widgets = {
            'data_inizio_rapporto': forms.DateInput(attrs={'type': 'date', 'class': 'form-control'}),
            'data_fine_rapporto': forms.DateInput(attrs={'type': 'date', 'class': 'form-control'}),
            'decorrenza_validita_da': forms.DateInput(attrs={'type': 'date', 'class': 'form-control'}),
            'decorrenza_validita_a': forms.DateInput(attrs={'type': 'date', 'class': 'form-control'}),
            'titolo': forms.TextInput(attrs={'class': 'form-control'}),
            'note': forms.Textarea(attrs={'class': 'form-control', 'rows': 3}),
            'riferimenti_normativi': forms.Textarea(attrs={'class': 'form-control', 'rows': 6}),
            'posizione': forms.HiddenInput(),
            'livello_ccnl': forms.HiddenInput(),
            'qualifica': forms.TextInput(attrs={'class': 'form-control', 'readonly': 'readonly'}),
            'stipendio_lordo_mensile': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01', 'readonly': 'readonly'}),
            'paga_base_mensile': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01', 'readonly': 'readonly'}),
            'contingenza_mensile': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01', 'readonly': 'readonly'}),
            'edr_mensile': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01', 'readonly': 'readonly'}),
            'indennita_mensile': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01'}),
            'giorni_ferie_annuali': forms.NumberInput(attrs={'class': 'form-control', 'step': '1'}),
            'giorni_permesso_annuali': forms.NumberInput(attrs={'class': 'form-control', 'step': '1'}),
            'ore_settimanali': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01'}),
            'ore_mensili': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01'}),
            'ore_giornaliere': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01'}),
            'scatto_periodicita_mesi': forms.NumberInput(attrs={'class': 'form-control'}),
            'scatto_importo': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01'}),
            'numero_scatti_massimi': forms.NumberInput(attrs={'class': 'form-control'}),
            'straordinario_diurno_maggiorazione': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01'}),
            'straordinario_notturno_maggiorazione': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01'}),
            'straordinario_festivo_maggiorazione': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01'}),
            'riposi_compensativi_regola': forms.Textarea(attrs={'class': 'form-control', 'rows': 3}),
        }

    def __init__(self, *args, **kwargs):
        azienda_operativa = kwargs.pop('azienda_operativa', None)
        dipendente_prefill_id = kwargs.pop('dipendente_prefill_id', None)
        super().__init__(*args, **kwargs)

        for name in ['dipendente', 'modulo', 'mansione', 'parametro_ccnl', 'tipo_contratto', 'ccnl_livello_scelta']:
            if name in self.fields and hasattr(self.fields[name].widget, 'attrs'):
                self.fields[name].widget.attrs.update({'class': 'form-select'})

        modulo_field = self.fields['modulo']
        mansione_field = self.fields['mansione']
        parametro_field = self.fields['parametro_ccnl']
        tipo_contratto_field = self.fields['tipo_contratto']
        dipendente_field = self.fields['dipendente']
        livello_scelta_field = self.fields['ccnl_livello_scelta']

        # Moduli: solo attivi, senza duplicati (deduplica per categoria + nome)
        if hasattr(modulo_field, 'queryset'):
            setattr(modulo_field, 'queryset',
                ModuloContrattuale.objects.filter(attivo=True).order_by('categoria', 'nome'))
        mansioni_qs = Mansione.objects.filter(attivo=True).order_by('ordinamento', 'nome')
        if hasattr(mansione_field, 'queryset'):
            setattr(mansione_field, 'queryset', mansioni_qs)
        # Parametri CCNL: solo la versione più recente
        pqs = _parametri_ccnl_correnti()
        parametro_field.queryset = pqs
        pk_meta = {}
        for p in pqs:
            pk_meta[str(p.pk)] = (str(p.livello).strip(), str(p.qualifica).strip())
        parametro_field.widget = ParametroCCNLSelect(
            pk_meta=pk_meta,
            attrs={'class': 'form-select'},
        )
        parametro_field.widget.choices = parametro_field.choices

        livelli_choices = [('', '— Seleziona livello —')]
        seen_l = set()
        for p in pqs.order_by('livello_ordinamento', 'livello', 'qualifica'):
            lv_key = str(p.livello).strip()
            if lv_key in seen_l:
                continue
            seen_l.add(lv_key)
            livelli_choices.append((lv_key, f'Livello {p.livello}'))
        livello_scelta_field.choices = livelli_choices

        # Mappa mansione -> livelli consentiti (da tabella ponte; fallback da qualifica CCNL).
        mappa_mansione_livelli = {}
        today = timezone.localdate()
        mapping_qs = MansioneLivelloCCNL.objects.filter(
            attivo=True,
            mansione__attivo=True,
        ).filter(
            Q(valida_da__isnull=True) | Q(valida_da__lte=today)
        ).filter(
            Q(valida_a__isnull=True) | Q(valida_a__gte=today)
        )
        sigla_ccnl = ''
        if azienda_operativa and getattr(azienda_operativa, 'ccnl_predefinito_id', None):
            sigla_ccnl = str(getattr(azienda_operativa.ccnl_predefinito, 'sigla', '') or '').strip()
        if sigla_ccnl:
            mapping_qs = mapping_qs.filter(Q(ccnl='') | Q(ccnl__icontains=sigla_ccnl))
        for rec in mapping_qs.order_by('-priorita', 'mansione__nome', 'livello'):
            key = str(rec.mansione_id)
            mappa_mansione_livelli.setdefault(key, set()).add(str(rec.livello).strip())
        if not mappa_mansione_livelli:
            qualifica_to_livelli = {}
            for p in pqs:
                qk = (str(p.qualifica or '')).strip().lower()
                if qk:
                    qualifica_to_livelli.setdefault(qk, set()).add(str(p.livello).strip())
            for m in mansioni_qs:
                qk = (str(m.nome or '')).strip().lower()
                if qk in qualifica_to_livelli:
                    mappa_mansione_livelli[str(m.id)] = set(qualifica_to_livelli[qk])
        mansione_field.widget = MansioneSelect(
            livelli_map=mappa_mansione_livelli,
            attrs={'class': 'form-select'},
        )
        mansione_field.widget.choices = mansione_field.choices

        if hasattr(tipo_contratto_field, 'queryset'):
            setattr(tipo_contratto_field, 'queryset',
                TipoContratto.objects.filter(attivo=True).order_by('nome'))

        # Dipendente: includi attivi E candidati (stato='candidato' per proposte dalla simulazione)
        # Se arriva da flusso candidato, forza inclusione di quel dipendente specifico
        if dipendente_prefill_id:
            qs = Dipendente.objects.filter(
                Q(id=dipendente_prefill_id) |
                Q(stato__in=['attivo', 'candidato'])
            ).order_by('cognome', 'nome')
            if azienda_operativa:
                qs = Dipendente.objects.filter(
                    Q(id=dipendente_prefill_id) |
                    Q(azienda=azienda_operativa, stato__in=['attivo', 'candidato'])
                ).order_by('cognome', 'nome')
        elif azienda_operativa:
            qs = Dipendente.objects.filter(azienda=azienda_operativa, stato__in=['attivo', 'candidato']).order_by('cognome', 'nome')
        else:
            qs = Dipendente.objects.filter(stato__in=['attivo', 'candidato']).order_by('cognome', 'nome')
        if hasattr(dipendente_field, 'queryset'):
            setattr(dipendente_field, 'queryset', qs)

        # Modifica: allinea livello helper e coerenza con parametro salvato
        # I campi di inquadramento sono valorizzati in clean() da parametro + tipo contratto
        for _fname in ('posizione', 'livello_ccnl', 'qualifica'):
            if _fname in self.fields:
                self.fields[_fname].required = False
        if 'qualifica' in self.fields:
            self.fields['qualifica'].label = 'Qualifica CCNL (Auto)'

        if self.instance and self.instance.pk and self.instance.parametro_ccnl_id:
            p = self.instance.parametro_ccnl
            self.fields['ccnl_livello_scelta'].initial = str(p.livello)
        elif self.initial.get('parametro_ccnl'):
            try:
                pid = self.initial['parametro_ccnl']
                p = ParametroCCNLTurismo.objects.filter(pk=pid).first()
                if p:
                    self.fields['ccnl_livello_scelta'].initial = str(p.livello)
            except Exception:
                pass

    def clean(self):
        cleaned_data = super().clean()
        parametro = cleaned_data.get('parametro_ccnl')
        tipo_contratto = cleaned_data.get('tipo_contratto')
        mansione = cleaned_data.get('mansione')
        livello_scelto = (cleaned_data.get('ccnl_livello_scelta') or '').strip()

        # Step "Qualifica CCNL scelta" rimosso in UI: se manca la voce tabellare,
        # la risolviamo automaticamente dal livello selezionato (preferendo la mansione).
        if not parametro and livello_scelto:
            pqs_livello = _parametri_ccnl_correnti().filter(livello=livello_scelto)
            mansione_nome = (getattr(mansione, 'nome', '') or '').strip()
            parametro = None
            if mansione_nome:
                parametro = (
                    pqs_livello
                    .filter(qualifica__iexact=mansione_nome)
                    .order_by('livello_ordinamento', 'qualifica', 'pk')
                    .first()
                )
            if not parametro:
                parametro = (
                    pqs_livello
                    .order_by('livello_ordinamento', 'qualifica', 'pk')
                    .first()
                )
            if parametro:
                cleaned_data['parametro_ccnl'] = parametro

        if not parametro:
            self.add_error('ccnl_livello_scelta', 'Seleziona un livello CCNL valido.')

        if not mansione:
            self.add_error('mansione', 'Seleziona la mansione (ruolo operativo).')

        if parametro and livello_scelto and str(parametro.livello).strip() != livello_scelto:
            self.add_error(
                'parametro_ccnl',
                'La qualifica selezionata non appartiene al livello CCNL scelto.',
            )

        if parametro and mansione:
            data_rif = cleaned_data.get('data_inizio_rapporto') or timezone.localdate()
            livello_mappa_qs = MansioneLivelloCCNL.objects.filter(
                attivo=True,
                mansione=mansione,
                livello=str(parametro.livello).strip(),
            ).filter(
                Q(valida_da__isnull=True) | Q(valida_da__lte=data_rif)
            ).filter(
                Q(valida_a__isnull=True) | Q(valida_a__gte=data_rif)
            ).filter(
                Q(ccnl='') | Q(ccnl__iexact=str(parametro.ccnl or '').strip())
            ).filter(
                Q(versione='') | Q(versione__iexact=str(parametro.versione or '').strip())
            ).filter(
                Q(sezione='') | Q(sezione__iexact=str(parametro.sezione or '').strip())
            ).order_by('-priorita', '-data_modifica')
            if livello_mappa_qs.exists():
                pass
            else:
                # Fallback: in assenza mappa esplicita, manteniamo il controllo su nome qualifica.
                pass
            mansione_nome = (mansione.nome or '').strip()
            qualifica_tabellare = (str(parametro.qualifica) or '').strip()
            if not livello_mappa_qs.exists() and mansione_nome and qualifica_tabellare and mansione_nome.lower() != qualifica_tabellare.lower():
                self.add_error(
                    'mansione',
                    'La mansione selezionata deve coincidere con la qualifica tabellare del livello scelto.',
                )
            if livello_mappa_qs.exists():
                qualifica_ok = any(
                    not (str(r.qualifica_tabellare or '').strip()) or
                    str(r.qualifica_tabellare).strip().lower() == qualifica_tabellare.lower()
                    for r in livello_mappa_qs
                )
                if not qualifica_ok:
                    self.add_error(
                        'mansione',
                        'La mansione e mappata al livello ma non alla qualifica tabellare selezionata.',
                    )

        if parametro:
            cleaned_data['livello_ccnl'] = parametro.livello
            cleaned_data['qualifica'] = (mansione.nome if mansione else parametro.qualifica)
            cleaned_data['posizione'] = _descrizione_posizione_contrattuale(
                parametro, tipo_contratto, mansione=mansione
            )

        # Defense-in-depth: garantisce sempre max 2 decimali sui campi ore
        # anche in assenza di normalizzazione lato JS.
        for ore_field in ('ore_settimanali', 'ore_mensili', 'ore_giornaliere'):
            val = cleaned_data.get(ore_field)
            if val in (None, ''):
                continue
            try:
                cleaned_data[ore_field] = Decimal(str(val)).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
            except Exception:
                pass

        return cleaned_data


class RapportoDiLavoroForm(forms.ModelForm):
    """Form per la modifica della bozza contratto (RapportoDiLavoro stato='proposta') da parte di HR/admin."""

    class Meta:
        model = RapportoDiLavoro
        fields = [
            'tipo_contratto',
            'data_inizio_rapporto',
            'data_fine_rapporto',
            'posizione',
            'livello_ccnl',
            'qualifica',
            'stipendio_lordo_mensile',
            'paga_base_mensile',
            'contingenza_mensile',
            'edr_mensile',
            'tredicesima',
            'quattordicesima',
            'premio_obiettivi',
            'ore_settimanali',
            'turno_tipo',
            'decorrenza_validita_da',
            'decorrenza_validita_a',
            'scatto_periodicita_mesi',
            'scatto_importo',
            'numero_scatti_massimi',
            'giorni_ferie_annuali',
            'giorni_permesso_annuali',
            'giorni_malattia_retribuiti',
            'ore_straordinario_diurno_maggiorazione',
            'ore_straordinario_notturno_maggiorazione',
            'ore_straordinario_festivo_maggiorazione',
            'riposi_compensativi_regola',
            'aliquota_tfr',
            'fondo_pensione',
        ]
        widgets = {
            'tipo_contratto': forms.Select(attrs={'class': 'form-select'}),
            'turno_tipo': forms.Select(attrs={'class': 'form-select'}),
            'data_inizio_rapporto': forms.DateInput(attrs={'type': 'date', 'class': 'form-control'}),
            'data_fine_rapporto': forms.DateInput(attrs={'type': 'date', 'class': 'form-control'}),
            'decorrenza_validita_da': forms.DateInput(attrs={'type': 'date', 'class': 'form-control'}),
            'decorrenza_validita_a': forms.DateInput(attrs={'type': 'date', 'class': 'form-control'}),
            'posizione': forms.TextInput(attrs={'class': 'form-control'}),
            'livello_ccnl': forms.TextInput(attrs={'class': 'form-control'}),
            'qualifica': forms.TextInput(attrs={'class': 'form-control'}),
            'stipendio_lordo_mensile': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01'}),
            'paga_base_mensile': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01'}),
            'contingenza_mensile': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01'}),
            'edr_mensile': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01'}),
            'premio_obiettivi': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01'}),
            'ore_settimanali': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01'}),
            'scatto_periodicita_mesi': forms.NumberInput(attrs={'class': 'form-control'}),
            'scatto_importo': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01'}),
            'numero_scatti_massimi': forms.NumberInput(attrs={'class': 'form-control'}),
            'giorni_ferie_annuali': forms.NumberInput(attrs={'class': 'form-control'}),
            'giorni_permesso_annuali': forms.NumberInput(attrs={'class': 'form-control'}),
            'giorni_malattia_retribuiti': forms.NumberInput(attrs={'class': 'form-control'}),
            'ore_straordinario_diurno_maggiorazione': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01'}),
            'ore_straordinario_notturno_maggiorazione': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01'}),
            'ore_straordinario_festivo_maggiorazione': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01'}),
            'riposi_compensativi_regola': forms.Textarea(attrs={'class': 'form-control', 'rows': 3}),
            'aliquota_tfr': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01'}),
            'fondo_pensione': forms.TextInput(attrs={'class': 'form-control'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['tipo_contratto'].queryset = TipoContratto.objects.filter(attivo=True)
        self.fields['data_fine_rapporto'].required = False
        self.fields['fondo_pensione'].required = False
        self.fields['riposi_compensativi_regola'].required = False
        self.fields['giorni_ferie_annuali'].help_text = (
            'Usati dal motore «Calcolatore ferie e ROL» per rateo ferie e saldi teorici in griglia presenze.'
        )
        self.fields['giorni_permesso_annuali'].help_text = (
            'Giorni di permesso annui sul contratto; il motore «Calcolatore ferie e ROL» in presenze usa le ore annuali '
            'da normativa CCNL e rapporto (allineate a proposta/contratto).'
        )

    def clean(self):
        cleaned_data = super().clean()
        inizio = cleaned_data.get('data_inizio_rapporto')
        fine = cleaned_data.get('data_fine_rapporto')
        if inizio and fine and fine <= inizio:
            self.add_error('data_fine_rapporto', 'La data di fine deve essere successiva alla data di inizio.')
        return cleaned_data


class IstruttoriaAssunzioneForm(forms.Form):
    """
    Wizard sintetico per avviare il flusso:
    - proposta da candidato
    - proposta/contratto diretto da dipendente attivo legacy
    """

    PERCORSO_CHOICES = [
        ('auto', 'Auto (consigliato)'),
        ('proposta', 'Proposta di assunzione'),
        ('contratto_diretto', 'Contratto diretto (modulo contratto assunzione)'),
    ]

    profilo_candidato = forms.ModelChoiceField(
        label='Profilo candidato',
        required=False,
        queryset=ProfiloCandidato.objects.none(),
        widget=forms.Select(attrs={'class': 'form-select'}),
        help_text='Candidati con profilo completato e ancora senza contratto definitivo in Gestione Rapporti.',
    )
    dipendente = forms.ModelChoiceField(
        label='Dipendente (anagrafica)',
        required=False,
        queryset=Dipendente.objects.none(),
        widget=forms.Select(attrs={'class': 'form-select'}),
        help_text='Dipendenti attivi o in stato candidato, senza contratto digitale attivo né pratiche di assunzione aperte.',
    )
    percorso = forms.ChoiceField(
        label='Percorso di generazione',
        required=True,
        choices=PERCORSO_CHOICES,
        initial='auto',
        widget=forms.Select(attrs={'class': 'form-select'}),
    )
    data_inizio_rapporto = forms.DateField(
        label='Data inizio rapporto',
        required=True,
        widget=forms.DateInput(attrs={'type': 'date', 'class': 'form-control'}),
    )
    tipo_contratto = forms.ModelChoiceField(
        label='Tipo contratto',
        required=False,
        queryset=TipoContratto.objects.none(),
        widget=forms.Select(attrs={'class': 'form-select'}),
    )
    ccnl_livello = forms.ChoiceField(
        label='Livello CCNL (vigente ora)',
        required=False,
        choices=[('', '— Auto da profilo/dipendente —')],
        widget=forms.Select(attrs={'class': 'form-select'}),
        help_text='Usa i livelli retributivi attualmente vigenti (parametri CCNL correnti).',
    )
    mansione = forms.ModelChoiceField(
        label='Mansione operativa',
        required=False,
        queryset=Mansione.objects.none(),
        widget=forms.Select(attrs={'class': 'form-select'}),
    )

    def __init__(self, *args, **kwargs):
        azienda_operativa = kwargs.pop('azienda_operativa', None)
        super().__init__(*args, **kwargs)

        self.fields['tipo_contratto'].queryset = TipoContratto.objects.filter(attivo=True).order_by('nome')
        self.fields['mansione'].queryset = Mansione.objects.filter(attivo=True).order_by('ordinamento', 'nome')

        pqs = _parametri_ccnl_correnti().order_by('livello_ordinamento', 'livello')
        seen = set()
        lvl_choices = [('', '— Auto da profilo/dipendente —')]
        for p in pqs:
            lv = str(p.livello).strip()
            if lv in seen:
                continue
            seen.add(lv)
            lvl_choices.append((lv, f'Livello {lv}'))
        self.fields['ccnl_livello'].choices = lvl_choices

        profili_qs = ProfiloCandidato.objects.select_related('user', 'dipendente').filter(
            profilo_completato=True
        )
        if azienda_operativa:
            q_az = Q(azienda_interesse=azienda_operativa)
            try:
                from anagrafiche.models import Azienda

                if Azienda.objects.count() == 1:
                    unica = Azienda.objects.only('pk').first()
                    if unica and unica.pk == azienda_operativa.pk:
                        q_az |= Q(azienda_interesse__isnull=True)
            except Exception:
                pass
            profili_qs = profili_qs.filter(q_az)
        # Escludi candidati gia assunti: proposta in stato contratto definitivo o rapporto gia sottoscritto.
        stati_contratto_def = list(PropostaAssunzione.stati_equivalenti('contratto_attivo'))
        profili_qs = (
            profili_qs.exclude(dipendente__proposte_assunzione__stato__in=stati_contratto_def)
            .exclude(dipendente__rapporti_di_lavoro__stato__in=('sottoscritto', 'sospeso'))
            .distinct()
        )
        self.fields['profilo_candidato'].queryset = profili_qs.order_by('-data_completamento', '-id')
        self.fields['profilo_candidato'].label_from_instance = (
            lambda p: f"{p.user.first_name} {p.user.last_name} — CF {p.codice_fiscale or 'n/d'}"
        )

        # Attivi e candidati: anagrafiche senza rapporto formale ancora in essere o da completare.
        dip_qs = Dipendente.objects.filter(stato__in=('attivo', 'candidato'))
        if azienda_operativa:
            dip_qs = dip_qs.filter(azienda=azienda_operativa)
        # Legacy alignment: solo dipendenti senza rapporti/proposte attive.
        dip_qs = (
            dip_qs
            .exclude(rapporti_di_lavoro__stato__in=('proposta', 'sottoscritto', 'sospeso'))
            .exclude(
                proposte_assunzione__stato__in=(
                    'bozza',
                    'inviata_candidato',
                    'firmata_candidato',
                    'contratto_attivo',
                    'inviata_al_dipendente',
                    'accettata_dipendente',
                    'in_revisione_admin',
                    'approvata_admin',
                    'convertita_in_contratto',
                )
            )
            .distinct()
        )
        self.fields['dipendente'].queryset = dip_qs.order_by('cognome', 'nome')
        self.fields['dipendente'].label_from_instance = (
            lambda d: f"{d.cognome} {d.nome} — CF {d.codice_fiscale or 'n/d'}"
        )

    def clean(self):
        cleaned_data = super().clean()
        profilo = cleaned_data.get('profilo_candidato')
        dip = cleaned_data.get('dipendente')
        if not profilo and not dip:
            self.add_error('profilo_candidato', 'Seleziona almeno un profilo candidato o un dipendente.')
            self.add_error('dipendente', 'Seleziona almeno un dipendente o un profilo candidato.')
        return cleaned_data


class AddendumContrattualeForm(forms.ModelForm):
    """Registrazione addendum / variazione su contratto gia sottoscritto (storico + opz. sincronizzazione campi)."""

    applica_valori_al_contratto = forms.BooleanField(
        required=False,
        initial=False,
        label='Aggiorna il contratto principale',
        help_text='Se attivo, copia su Rapporto di lavoro i valori economici compilati (solo campi valorizzati).',
    )

    class Meta:
        model = AddendumContrattuale
        fields = [
            'tipo',
            'data_decorrenza',
            'data_fine_rapporto_aggiornata',
            'stipendio_lordo_mensile',
            'paga_base_mensile',
            'contingenza_mensile',
            'edr_mensile',
            'ore_settimanali',
            'tipo_contratto',
            'parametro_ccnl',
            'livello_ccnl',
            'qualifica',
            'riferimento_atto',
            'note',
            'file_allegato',
        ]
        widgets = {
            'data_decorrenza': forms.DateInput(attrs={'type': 'date', 'class': 'form-control'}),
            'data_fine_rapporto_aggiornata': forms.DateInput(attrs={'type': 'date', 'class': 'form-control'}),
            'tipo': forms.Select(attrs={'class': 'form-select'}),
            'stipendio_lordo_mensile': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01'}),
            'paga_base_mensile': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01'}),
            'contingenza_mensile': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01'}),
            'edr_mensile': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01'}),
            'ore_settimanali': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01'}),
            'tipo_contratto': forms.Select(attrs={'class': 'form-select'}),
            'parametro_ccnl': forms.Select(attrs={'class': 'form-select'}),
            'livello_ccnl': forms.TextInput(attrs={'class': 'form-control'}),
            'qualifica': forms.TextInput(attrs={'class': 'form-control'}),
            'riferimento_atto': forms.TextInput(attrs={'class': 'form-control'}),
            'note': forms.Textarea(attrs={'class': 'form-control', 'rows': 4}),
            'file_allegato': forms.FileInput(attrs={'class': 'form-control'}),
        }

    def __init__(self, *args, **kwargs):
        kwargs.pop('azienda_operativa', None)
        super().__init__(*args, **kwargs)
        self.fields['tipo_contratto'].queryset = TipoContratto.objects.filter(attivo=True).order_by('nome')
        self.fields['tipo_contratto'].required = False
        self.fields['parametro_ccnl'].queryset = _parametri_ccnl_correnti()
        self.fields['parametro_ccnl'].required = False
        self.fields['data_decorrenza'].required = True
        if self.fields.get('file_allegato'):
            self.fields['file_allegato'].required = False


class ParametroCCNLTurismoForm(forms.ModelForm):
    class Meta:
        model = ParametroCCNLTurismo
        fields = [
            'ccnl',
            'versione',
            'sezione',
            'livello',
            'qualifica',
            'tipo_contratto_nazionale',
            'decorrenza_validita_da',
            'decorrenza_validita_a',
            'livello_ordinamento',
            'minimo_tabellare',
            'totale_tabellare',
            'fonte_tabella',
            'data_rilevazione_tabella',
            'importo_lordo_mensile',
            'paga_base_mensile',
            'contingenza_mensile',
            'edr_mensile',
            'indennita_mensile',
            'ore_settimanali',
            'ore_mensili',
            'ore_giornaliere',
            'scatto_periodicita_mesi',
            'scatto_importo',
            'numero_scatti_massimi',
            'straordinario_diurno_maggiorazione',
            'straordinario_notturno_maggiorazione',
            'straordinario_festivo_maggiorazione',
            'riposi_compensativi_regola',
            'note',
            'attivo',
        ]
        widgets = {
            'decorrenza_validita_da': forms.DateInput(attrs={'type': 'date', 'class': 'form-control'}),
            'decorrenza_validita_a': forms.DateInput(attrs={'type': 'date', 'class': 'form-control'}),
            'data_rilevazione_tabella': forms.DateInput(attrs={'type': 'date', 'class': 'form-control'}),
            'riposi_compensativi_regola': forms.Textarea(attrs={'class': 'form-control', 'rows': 3}),
            'note': forms.Textarea(attrs={'class': 'form-control', 'rows': 3}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for name, field in self.fields.items():
            if isinstance(field.widget, forms.Select):
                field.widget.attrs.update({'class': 'form-select'})
            elif isinstance(field.widget, forms.CheckboxInput):
                field.widget.attrs.update({'class': 'form-check-input'})
            elif not isinstance(field.widget, forms.Textarea):
                field.widget.attrs.update({'class': field.widget.attrs.get('class', 'form-control')})


class RegolaNormativaCCNLForm(forms.ModelForm):
    class Meta:
        model = RegolaNormativaCCNL
        fields = [
            'ccnl',
            'versione',
            'sezione',
            'livello',
            'decorrenza_validita_da',
            'decorrenza_validita_a',
            'ore_settimanali',
            'ore_mensili',
            'ore_giornaliere',
            'ferie_annue_giorni',
            'permessi_annui_ore',
            'scatto_periodicita_mesi',
            'scatto_importo',
            'numero_scatti_massimi',
            'note',
            'attivo',
        ]
        widgets = {
            'decorrenza_validita_da': forms.DateInput(attrs={'type': 'date', 'class': 'form-control'}),
            'decorrenza_validita_a': forms.DateInput(attrs={'type': 'date', 'class': 'form-control'}),
            'note': forms.Textarea(attrs={'class': 'form-control', 'rows': 3}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for name, field in self.fields.items():
            if isinstance(field.widget, forms.Select):
                field.widget.attrs.update({'class': 'form-select'})
            elif isinstance(field.widget, forms.CheckboxInput):
                field.widget.attrs.update({'class': 'form-check-input'})
            elif not isinstance(field.widget, forms.Textarea):
                field.widget.attrs.update({'class': field.widget.attrs.get('class', 'form-control')})