from django.apps import apps
from django.contrib import messages
from django.contrib.auth.decorators import login_required, user_passes_test
from django.core.paginator import Paginator
from django.db import models as dj_models
from django.db.models import ProtectedError
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_http_methods

from accounts.gestione_database import (
    SKIP_APPS_OVERVIEW,
    can_gestione_database,
    format_field_value,
    gestione_modelform_factory,
    get_model_or_none,
)
from accounts.tenant import get_azienda_operativa
from log_attivita.utils import registra_log


def _log_gestione(request, op: str, descrizione: str, oggetto_id=None):
    user = request.user
    azienda = getattr(user, 'azienda', None) or get_azienda_operativa(user, request.session)
    registra_log(user, azienda, op, descrizione=descrizione, oggetto_id=oggetto_id, request=request)


def _resolve_model(app_label: str, model_name: str):
    model = get_model_or_none(app_label, model_name)
    if model is None:
        raise Http404('Modello non trovato')
    if model._meta.abstract:
        raise Http404('Modello astratto')
    return model


@login_required
@user_passes_test(can_gestione_database)
def admin_data_overview(request):
    """Panoramica tabelle applicative (stesso accesso per superuser e ruolo admin)."""
    model_rows = []
    for model in apps.get_models():
        meta = model._meta
        if meta.app_label in SKIP_APPS_OVERVIEW:
            continue
        try:
            totale = model.objects.count()
        except Exception:
            totale = 0
        model_rows.append(
            {
                'app_label': meta.app_label,
                'model_name': meta.model_name,
                'verbose_name_plural': str(meta.verbose_name_plural).title(),
                'totale': totale,
            }
        )

    model_rows.sort(key=lambda x: (x['app_label'], x['verbose_name_plural']))
    u = request.user
    return render(
        request,
        'accounts/admin_data_overview.html',
        {
            'model_rows': model_rows,
            'gestione_integrata': True,
            'django_admin_access': bool(getattr(u, 'is_active', False) and getattr(u, 'is_staff', False)),
        },
    )


@login_required
@user_passes_test(can_gestione_database)
def admin_table_detail(request, app_label: str, model_name: str):
    """Elenco record con collegamenti a dettaglio / modifica / eliminazione."""
    model = _resolve_model(app_label, model_name)

    queryset = model.objects.all()
    dip_raw = (request.GET.get('dipendente_id') or request.GET.get('dipendente') or '').strip()
    if dip_raw and any(getattr(f, 'name', None) == 'dipendente' for f in model._meta.fields):
        try:
            queryset = queryset.filter(dipendente_id=int(dip_raw))
        except (ValueError, TypeError):
            pass
    az_raw = (request.GET.get('azienda_id') or request.GET.get('azienda') or '').strip()
    if az_raw and any(getattr(f, 'name', None) == 'azienda' for f in model._meta.fields):
        try:
            queryset = queryset.filter(azienda_id=int(az_raw))
        except (ValueError, TypeError):
            pass
    queryset = queryset.order_by('-pk')
    paginator = Paginator(queryset, 50)
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)

    list_rows = []
    for obj in page_obj.object_list:
        list_rows.append(
            {
                'pk': obj.pk,
                'repr': str(obj)[:200],
            }
        )

    meta = model._meta
    filter_parts = []
    if dip_raw and any(getattr(f, 'name', None) == 'dipendente' for f in model._meta.fields):
        filter_parts.append(f'dipendente ID {dip_raw}')
    if az_raw and any(getattr(f, 'name', None) == 'azienda' for f in model._meta.fields):
        filter_parts.append(f'azienda ID {az_raw}')
    filter_note = ''
    if filter_parts:
        filter_note = 'Filtro attivo: ' + ', '.join(filter_parts) + '.'

    context = {
        'app_label': app_label,
        'model_name': model_name,
        'verbose_name_plural': str(meta.verbose_name_plural).title(),
        'verbose_name': str(meta.verbose_name).title(),
        'list_rows': list_rows,
        'page_obj': page_obj,
        'can_add': True,
        'filter_note': filter_note,
        'dipendente_filter_id': dip_raw if dip_raw else '',
        'azienda_filter_id': az_raw if az_raw else '',
    }
    return render(request, 'accounts/admin_table_detail.html', context)


@login_required
@user_passes_test(can_gestione_database)
def admin_db_record_detail(request, app_label: str, model_name: str, pk):
    model = _resolve_model(app_label, model_name)
    obj = get_object_or_404(model, pk=pk)

    field_rows = []
    for f in model._meta.fields:
        value = getattr(obj, f.name)
        is_file = isinstance(f, (dj_models.FileField, dj_models.ImageField))
        file_url = ''
        if is_file and value and hasattr(value, 'url'):
            try:
                file_url = value.url
            except Exception:
                file_url = ''
        field_rows.append(
            {
                'name': f.name,
                'verbose': f.verbose_name,
                'value': format_field_value(f, value),
                'raw': value,
                'is_file': is_file,
                'file_url': file_url,
            }
        )

    m2m_rows = []
    for f in model._meta.many_to_many:
        try:
            qs = getattr(obj, f.name).all()[:50]
            m2m_rows.append(
                {
                    'name': f.name,
                    'verbose': f.verbose_name,
                    'items': [str(x) for x in qs],
                    'truncated': getattr(obj, f.name).count() > 50,
                }
            )
        except Exception:
            m2m_rows.append(
                {
                    'name': f.name,
                    'verbose': f.verbose_name,
                    'items': [],
                    'truncated': False,
                    'error': True,
                }
            )

    return render(
        request,
        'accounts/admin_db_record_detail.html',
        {
            'app_label': app_label,
            'model_name': model_name,
            'verbose_name': str(model._meta.verbose_name).title(),
            'obj': obj,
            'field_rows': field_rows,
            'm2m_rows': m2m_rows,
        },
    )


@login_required
@user_passes_test(can_gestione_database)
@require_http_methods(['GET', 'POST'])
def admin_db_record_edit(request, app_label: str, model_name: str, pk):
    model = _resolve_model(app_label, model_name)
    obj = get_object_or_404(model, pk=pk)
    FormCls = gestione_modelform_factory(model)

    if request.method == 'POST':
        form = FormCls(request.POST, request.FILES, instance=obj)
        if form.is_valid():
            saved = form.save()
            _log_gestione(
                request,
                'gestione_db_modifica',
                f'{app_label}.{model_name} pk={saved.pk}',
                oggetto_id=str(saved.pk),
            )
            messages.success(request, 'Record aggiornato.')
            return redirect(
                'admin_db_record_detail',
                app_label=app_label,
                model_name=model_name,
                pk=saved.pk,
            )
    else:
        form = FormCls(instance=obj)

    return render(
        request,
        'accounts/admin_db_record_form.html',
        {
            'app_label': app_label,
            'model_name': model_name,
            'verbose_name': str(model._meta.verbose_name).title(),
            'form': form,
            'obj': obj,
            'is_create': False,
        },
    )


@login_required
@user_passes_test(can_gestione_database)
@require_http_methods(['GET', 'POST'])
def admin_db_record_create(request, app_label: str, model_name: str):
    model = _resolve_model(app_label, model_name)
    FormCls = gestione_modelform_factory(model)

    if request.method == 'POST':
        form = FormCls(request.POST, request.FILES)
        if form.is_valid():
            saved = form.save()
            _log_gestione(
                request,
                'gestione_db_creazione',
                f'{app_label}.{model_name} pk={saved.pk}',
                oggetto_id=str(saved.pk),
            )
            messages.success(request, 'Record creato.')
            return redirect(
                'admin_db_record_detail',
                app_label=app_label,
                model_name=model_name,
                pk=saved.pk,
            )
    else:
        form = FormCls()

    return render(
        request,
        'accounts/admin_db_record_form.html',
        {
            'app_label': app_label,
            'model_name': model_name,
            'verbose_name': str(model._meta.verbose_name).title(),
            'form': form,
            'obj': None,
            'is_create': True,
        },
    )


@login_required
@user_passes_test(can_gestione_database)
@require_http_methods(['GET', 'POST'])
def admin_db_record_delete(request, app_label: str, model_name: str, pk):
    model = _resolve_model(app_label, model_name)
    obj = get_object_or_404(model, pk=pk)

    if request.method == 'POST':
        pk_str = str(obj.pk)
        label = str(obj)
        try:
            obj.delete()
        except ProtectedError:
            messages.error(
                request,
                'Eliminazione non consentita: esistono altri record collegati a questo elemento.',
            )
            return redirect(
                'admin_db_record_detail',
                app_label=app_label,
                model_name=model_name,
                pk=pk_str,
            )
        _log_gestione(
            request,
            'gestione_db_eliminazione',
            f'{app_label}.{model_name} pk={pk_str} ({label[:120]})',
            oggetto_id=pk_str,
        )
        messages.success(request, 'Record eliminato.')
        return redirect('admin_table_detail', app_label=app_label, model_name=model_name)

    return render(
        request,
        'accounts/admin_db_record_confirm_delete.html',
        {
            'app_label': app_label,
            'model_name': model_name,
            'verbose_name': str(model._meta.verbose_name).title(),
            'obj': obj,
        },
    )
