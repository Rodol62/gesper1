from django.db import transaction
from django.utils import timezone

from accounts.models import User
from log_attivita.utils import registra_log
from notifiche_email.services import crea_notifica_evento
from richieste.models import Richiesta

from .models import RichiestaApprovazione, RichiestaWorkflow, StepApprovazione


def _resolve_approvatore(richiesta: Richiesta, ruolo_step: str):
    """Risoluzione approvatore minimo per MVP.

    Mappa `manager` su utente HR della stessa azienda in assenza di gerarchia manageriale.
    """
    ruolo_target = 'hr' if ruolo_step == 'manager' else ruolo_step
    return (
        User.objects.filter(
            azienda=richiesta.azienda,
            ruoli__codice=ruolo_target,
            is_active=True,
        )
        .distinct()
        .order_by('id')
        .first()
    )


def inizializza_workflow_richiesta(richiesta: Richiesta):
    """Crea il primo step di approvazione per una richiesta appena inviata."""
    workflow = (
        RichiestaWorkflow.objects.filter(tipo_richiesta=richiesta.tipo, attivo=True)
        .order_by('-data_creazione')
        .first()
    )
    if not workflow:
        return None

    primo_step = workflow.steps.order_by('numero_step').first()
    if not primo_step:
        return None

    approvazione, created = RichiestaApprovazione.objects.get_or_create(
        richiesta=richiesta,
        step=primo_step,
        defaults={
            'approvatore': _resolve_approvatore(richiesta, primo_step.ruolo_approvatore),
            'stato': 'in_attesa',
        },
    )

    if created:
        crea_notifica_evento(
            richiesta,
            'richiesta_da_approvare',
            destinatario=approvazione.approvatore,
        )
        registra_log(
            richiesta.richiesta_da,
            richiesta.azienda,
            'richiesta',
            f'Workflow avviato - step {primo_step.numero_step} per richiesta {richiesta.id}',
            richiesta.id,
        )
    return approvazione


@transaction.atomic
def processa_azione_approvazione(
    approvazione: RichiestaApprovazione,
    utente,
    azione: str,
    commento: str = '',
):
    """Processa approvazione/rifiuto e avanza il workflow."""
    if approvazione.stato != 'in_attesa':
        return approvazione

    if azione not in ('approvato', 'rifiutato'):
        raise ValueError('Azione non valida')

    approvazione.stato = azione
    approvazione.commento = commento
    approvazione.data_azione = timezone.now()
    approvazione.approvatore = utente
    approvazione.save(update_fields=['stato', 'commento', 'data_azione', 'approvatore'])

    richiesta = approvazione.richiesta

    if azione == 'rifiutato':
        richiesta.stato = 'rifiutata'
        richiesta.risposta_da = utente
        richiesta.data_risposta = timezone.now()
        richiesta.note_risposta = commento
        richiesta.save(update_fields=['stato', 'risposta_da', 'data_risposta', 'note_risposta'])
        crea_notifica_evento(richiesta, 'richiesta_rifiutata')
        registra_log(utente, richiesta.azienda, 'richiesta', f'Workflow rifiutato richiesta {richiesta.id}', richiesta.id)
        return approvazione

    prossimo_step = (
        StepApprovazione.objects.filter(
            workflow=approvazione.step.workflow,
            numero_step=approvazione.step.numero_step + 1,
        )
        .order_by('numero_step')
        .first()
    )

    if prossimo_step:
        prossima_approvazione, creata = RichiestaApprovazione.objects.get_or_create(
            richiesta=richiesta,
            step=prossimo_step,
            defaults={
                'approvatore': _resolve_approvatore(richiesta, prossimo_step.ruolo_approvatore),
                'stato': 'in_attesa',
            },
        )
        if creata:
            crea_notifica_evento(
                richiesta,
                'richiesta_da_approvare',
                destinatario=prossima_approvazione.approvatore,
            )
        registra_log(
            utente,
            richiesta.azienda,
            'richiesta',
            f'Workflow avanzato allo step {prossimo_step.numero_step} richiesta {richiesta.id}',
            richiesta.id,
        )
    else:
        richiesta.stato = 'approvata'
        richiesta.risposta_da = utente
        richiesta.data_risposta = timezone.now()
        richiesta.note_risposta = commento
        richiesta.save(update_fields=['stato', 'risposta_da', 'data_risposta', 'note_risposta'])
        crea_notifica_evento(richiesta, 'richiesta_approvata')
        registra_log(utente, richiesta.azienda, 'richiesta', f'Workflow completato richiesta {richiesta.id}', richiesta.id)

    return approvazione
