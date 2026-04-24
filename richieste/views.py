from django.shortcuts import render, get_object_or_404, redirect
from django.http import HttpResponseForbidden
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.db.models import Exists, OuterRef
from .models import Richiesta
from anagrafiche.models import Dipendente
from anagrafiche.permissions import admin_required, hr_required, dipendente_required
from log_attivita.utils import registra_log
from django.utils import timezone
from accounts.tenant import get_azienda_operativa
from api.views import send_push_to_user
from workflow.models import RichiestaApprovazione


def _is_admin_or_hr(user):
    return user.is_superuser or user.has_ruolo('admin') or user.has_ruolo('hr')


def _azienda_scope_for_staff(user, request):
    if user.is_superuser or user.has_ruolo('admin'):
        return get_azienda_operativa(user, request.session)
    if user.has_ruolo('hr'):
        return getattr(user, 'azienda', None)
    return None


def _get_richiesta_staff_or_404(request, richiesta_id):
    azienda_scope = _azienda_scope_for_staff(request.user, request)
    if not azienda_scope:
        raise PermissionDenied("Azienda operativa non disponibile.")
    return get_object_or_404(Richiesta, id=richiesta_id, azienda=azienda_scope)


def _has_workflow_in_attesa(richiesta: Richiesta) -> bool:
    return richiesta.approvazioni_workflow.filter(stato='in_attesa').exists()


def _aggiorna_stato_richiesta(request, richiesta: Richiesta, nuovo_stato: str, nota: str = ''):
    """Punto unico per aggiornamento stato + audit log + push dipendente."""
    stati_validi = {'approvata', 'rifiutata', 'chiusa'}
    if nuovo_stato not in stati_validi:
        raise ValueError("Stato non valido.")

    richiesta.stato = nuovo_stato
    richiesta.risposta_da = request.user
    richiesta.data_risposta = timezone.now()
    richiesta.note_risposta = nota
    richiesta.save(update_fields=['stato', 'risposta_da', 'data_risposta', 'note_risposta'])

    registra_log(
        request.user,
        richiesta.azienda,
        'richiesta',
        f"Richiesta #{richiesta.id} ({richiesta.get_tipo_display()}) → {richiesta.get_stato_display()}",
        richiesta.id,
    )

    if nuovo_stato in ('approvata', 'rifiutata') and richiesta.dipendente and richiesta.dipendente.user:
        emoji = '✅' if nuovo_stato == 'approvata' else '❌'
        send_push_to_user(
            richiesta.dipendente.user,
            title=f'{emoji} Richiesta {richiesta.get_stato_display()}',
            body=f'La tua richiesta di {richiesta.get_tipo_display()} ({richiesta.data_inizio} → {richiesta.data_fine}) è stata {richiesta.get_stato_display().lower()}.',
        )


def _blocca_se_workflow_pending(request, richiesta: Richiesta) -> bool:
    """True se la richiesta è bloccata perché governata da workflow."""
    if _has_workflow_in_attesa(richiesta):
        messages.error(request, "Questa richiesta è gestita da workflow: usa la sezione approvazioni.")
        return True
    return False


@login_required
def lista_richieste(request):
    """Lista richieste — per admin/HR mostra quelle della propria azienda.
    Di default mostra solo le richieste pendenti (stato='inviata')."""
    if request.user.is_superuser or request.user.has_ruolo('admin'):
        azienda_operativa = _azienda_scope_for_staff(request.user, request)
        richieste = Richiesta.objects.filter(azienda=azienda_operativa).select_related(
            'dipendente', 'richiesta_da', 'risposta_da'
        ) if azienda_operativa else Richiesta.objects.none()
    elif request.user.has_ruolo('hr'):
        richieste = Richiesta.objects.filter(azienda=request.user.azienda).select_related(
            'dipendente', 'richiesta_da', 'risposta_da'
        )
    elif request.user.has_ruolo('dipendente'):
        richieste = Richiesta.objects.filter(dipendente__utente=request.user).select_related(
            'dipendente', 'richiesta_da', 'risposta_da'
        )
    else:
        # Mostra pagina sessione scaduta user-friendly
        return render(request, 'sessione_scaduta.html', status=401)

    # Filtro tipo e stato da querystring
    tipo_filter = request.GET.get('tipo', '')
    stato_filter = request.GET.get('stato', '')
    
    # Di default, mostra solo le richieste "inviata" (pendenti)
    if not stato_filter:
        richieste = richieste.filter(stato='inviata')
    elif stato_filter:
        richieste = richieste.filter(stato=stato_filter)
    
    if tipo_filter:
        richieste = richieste.filter(tipo=tipo_filter)

    wf_pending_sq = RichiestaApprovazione.objects.filter(
        richiesta_id=OuterRef('pk'),
        stato='in_attesa',
    )
    richieste = richieste.annotate(workflow_pending=Exists(wf_pending_sq)).order_by('-data_richiesta', '-id')

    paginator = Paginator(richieste, 25)
    page_obj = paginator.get_page(request.GET.get('page') or 1)

    return render(request, 'richieste/lista.html', {
        'page_obj': page_obj,
        'tipo_filter': tipo_filter,
        'stato_filter': stato_filter,
        'is_admin_hr': _is_admin_or_hr(request.user),
    })


@login_required
def dettaglio_richiesta(request, richiesta_id):
    """Dettaglio richiesta — admin/HR vedono tutto; dipendente solo la propria."""
    if _is_admin_or_hr(request.user):
        richiesta = _get_richiesta_staff_or_404(request, richiesta_id)
    elif request.user.has_ruolo('dipendente'):
        richiesta = get_object_or_404(Richiesta, id=richiesta_id, dipendente__utente=request.user)
    elif request.user.has_ruolo('candidato'):
        # candidato con contratto attivo (self-service)
        richiesta = get_object_or_404(Richiesta, id=richiesta_id, richiesta_da=request.user)
    else:
        return HttpResponseForbidden("Accesso negato")
    return render(request, 'richieste/dettaglio.html', {
        'richiesta': richiesta,
        'is_admin_hr': _is_admin_or_hr(request.user),
        'workflow_pending': _has_workflow_in_attesa(richiesta),
    })


@login_required
def rispondi_richiesta(request, richiesta_id):
    """Admin/HR: rispondi e cambia stato (approva/rifiuta/chiudi)."""
    if not _is_admin_or_hr(request.user):
        return HttpResponseForbidden("Accesso riservato ad admin e HR.")

    richiesta = _get_richiesta_staff_or_404(request, richiesta_id)

    if request.method == 'POST':
        nuovo_stato = request.POST.get('stato')
        nota = request.POST.get('note_risposta', '').strip()
        try:
            if _blocca_se_workflow_pending(request, richiesta):
                return redirect('dettaglio_richiesta', richiesta_id=richiesta_id)
            _aggiorna_stato_richiesta(request, richiesta, nuovo_stato, nota)
        except ValueError:
            messages.error(request, "Stato non valido.")
            return redirect('dettaglio_richiesta', richiesta_id=richiesta_id)
        messages.success(request, f"Richiesta aggiornata: {richiesta.get_stato_display()}.")
        return redirect('lista_richieste')

    return render(request, 'richieste/rispondi.html', {
        'richiesta': richiesta,
        'workflow_pending': _has_workflow_in_attesa(richiesta),
    })


@dipendente_required
def invia_richiesta(request):
    """Modulo invio richiesta per dipendenti con ruolo 'dipendente'."""
    if request.method == 'POST':
        pass
    return render(request, 'richieste/invia.html')


@hr_required
def approva_richiesta(request, richiesta_id):
    richiesta = get_object_or_404(Richiesta, id=richiesta_id, azienda=request.user.azienda)
    if richiesta.stato != 'inviata':
        return HttpResponseForbidden("Richiesta già gestita.")
    if request.method == 'POST':
        if _blocca_se_workflow_pending(request, richiesta):
            return redirect('dettaglio_richiesta', richiesta_id=richiesta_id)
        _aggiorna_stato_richiesta(request, richiesta, 'approvata', request.POST.get('note_risposta', ''))
        return redirect('lista_richieste')
    return render(request, 'richieste/approva.html', {
        'richiesta': richiesta,
        'workflow_pending': _has_workflow_in_attesa(richiesta),
    })


@hr_required
def rifiuta_richiesta(request, richiesta_id):
    richiesta = get_object_or_404(Richiesta, id=richiesta_id, azienda=request.user.azienda)
    if richiesta.stato != 'inviata':
        return HttpResponseForbidden("Richiesta già gestita.")
    if request.method == 'POST':
        if _blocca_se_workflow_pending(request, richiesta):
            return redirect('dettaglio_richiesta', richiesta_id=richiesta_id)
        _aggiorna_stato_richiesta(request, richiesta, 'rifiutata', request.POST.get('note_risposta', ''))
        return redirect('lista_richieste')
    return render(request, 'richieste/rifiuta.html', {
        'richiesta': richiesta,
        'workflow_pending': _has_workflow_in_attesa(richiesta),
    })


@login_required
def chiudi_richiesta(request, richiesta_id):
    """Chiudi una richiesta come completata — admin/HR only."""
    if not _is_admin_or_hr(request.user):
        return HttpResponseForbidden("Accesso riservato ad admin e HR.")
    
    richiesta = _get_richiesta_staff_or_404(request, richiesta_id)
    
    if request.method == 'POST':
        if _blocca_se_workflow_pending(request, richiesta):
            return redirect('dettaglio_richiesta', richiesta_id=richiesta_id)
        _aggiorna_stato_richiesta(request, richiesta, 'chiusa', request.POST.get('note_risposta', 'Chiusa da admin/HR').strip())
        messages.success(request, f"Richiesta {richiesta.id} chiusa con successo.")
        return redirect('lista_richieste')
    
    return render(request, 'richieste/chiudi.html', {
        'richiesta': richiesta,
        'workflow_pending': _has_workflow_in_attesa(richiesta),
    })


@login_required
def elimina_richiesta(request, richiesta_id):
    """Elimina una richiesta — admin/HR only."""
    if not _is_admin_or_hr(request.user):
        return HttpResponseForbidden("Accesso riservato ad admin e HR.")
    
    richiesta = _get_richiesta_staff_or_404(request, richiesta_id)
    
    if request.method == 'POST':
        richiesta_desc = f"Richiesta #{richiesta.id} ({richiesta.get_tipo_display()}) da {richiesta.dipendente}"
        richiesta.delete()
        
        registra_log(
            request.user,
            richiesta.azienda,
            'richiesta',
            f"Richiesta eliminata: {richiesta_desc}",
            richiesta_id,
        )
        messages.success(request, f"Richiesta {richiesta_id} eliminata con successo.")
        return redirect('lista_richieste')
    
    return render(request, 'richieste/elimina.html', {'richiesta': richiesta})
