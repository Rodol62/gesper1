"""
Accesso unificato alla gestione dati di piattaforma (lettura / modifica / eliminazione)
per superuser Django e utenti con ruolo applicativo «admin» — stesse capacità.
"""
from django.apps import apps
from django.db import models
from django.forms.models import modelform_factory
from django.forms import CheckboxInput, Select, SelectMultiple


def can_gestione_database(user) -> bool:
    if not user or not user.is_authenticated:
        return False
    if getattr(user, 'is_superuser', False):
        return True
    has_ruolo = getattr(user, 'has_ruolo', None)
    if callable(has_ruolo):
        return bool(has_ruolo('admin'))
    return False


# Panoramica: nascondi solo app interne Django poco utili all’operatore
SKIP_APPS_OVERVIEW = frozenset({'admin', 'contenttypes', 'sessions'})


def get_model_or_none(app_label: str, model_name: str):
    try:
        return apps.get_model(app_label, model_name)
    except LookupError:
        return None


def exclude_fields_for_gestione_form(model) -> list[str]:
    exclude: list[str] = []
    for f in model._meta.fields:
        if isinstance(f, models.BinaryField):
            exclude.append(f.name)
    if model._meta.label_lower == 'accounts.user':
        exclude.append('password')
    return exclude


def _formfield_callback_db_gestione(field, **kwargs):
    formfield = field.formfield(**kwargs)
    if not formfield:
        return None
    w = formfield.widget
    if isinstance(w, CheckboxInput):
        formfield.widget.attrs.setdefault('class', 'form-check-input')
        return formfield
    if isinstance(w, (Select, SelectMultiple)):
        cls = (formfield.widget.attrs.get('class') or '').strip()
        extra = 'form-select form-select-sm'
        formfield.widget.attrs['class'] = f'{cls} {extra}'.strip() if cls else extra
        return formfield
    cls = (formfield.widget.attrs.get('class') or '').strip()
    extra = 'form-control form-control-sm'
    if 'form-control' not in cls:
        formfield.widget.attrs['class'] = f'{cls} {extra}'.strip() if cls else extra
    return formfield


def gestione_modelform_factory(model):
    """ModelForm generico per modifica da interfaccia integrata."""
    return modelform_factory(
        model,
        exclude=exclude_fields_for_gestione_form(model),
        formfield_callback=_formfield_callback_db_gestione,
    )


def format_field_value(field, value) -> str:
    if value is None:
        return ''
    if hasattr(value, 'get_absolute_url'):
        try:
            return str(value)
        except Exception:
            return repr(value)
    return str(value)
