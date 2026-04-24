import logging
from django.conf import settings
from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required, user_passes_test
from django.core.mail import EmailMessage, get_connection
from django.core.paginator import Paginator
from django.db.models import Q
from django.http import Http404
from django.shortcuts import render, redirect, get_object_or_404
from django.urls import reverse
from django.utils import timezone
from .tenant import get_azienda_operativa

from .models import ConfigurazioneSistema, ProfiloCandidato, RichiestaIntegrazioneCandidato
from .outbound_uri import outbound_absolute_uri
from .utils import (
    checklist_richiesta_integrazione,
    controlla_completezza_profilo,
    get_richiesta_integrazione_attiva,
    get_ultima_richiesta_integrazione,
)

logger = logging.getLogger('django')
User = get_user_model()


def _is_hr_or_admin(user):
    return user.is_superuser or user.has_ruolo('admin') or user.has_ruolo('hr')


def _candidato_appartiene_ad_azienda(candidato_user, azienda):
    """True se il candidato è riconducibile all'azienda (profilo, utente.azienda, dipendente o proposta)."""
    if not azienda:
        return False
    if getattr(candidato_user, 'azienda_id', None) == azienda.id:
        return True
    profilo = getattr(candidato_user, 'profilo_candidato', None)
    if profilo and profilo.azienda_interesse_id == azienda.id:
        return True
    from anagrafiche.models import Dipendente
    from rapporto_di_lavoro.models import PropostaAssunzione

    if Dipendente.objects.filter(utente=candidato_user, azienda=azienda).exists():
        return True
    if PropostaAssunzione.objects.filter(azienda=azienda, dipendente__utente=candidato_user).exists():
        return True
    # Stesso criterio di lista_candidati: con un solo datore, candidati registrati senza
    # azienda_interesse sono riconducibili a quell'unica azienda (evita elenco pieno ma 404 su dettaglio/proposta).
    if profilo is not None and profilo.azienda_interesse_id is None:
        try:
            from anagrafiche.models import Azienda

            if Azienda.objects.count() == 1:
                unica = Azienda.objects.only('pk').first()
                if unica and unica.pk == azienda.pk:
                    return True
        except Exception:
            pass
    return False


def _candidato_gestionabile_da_richiedente(request, candidato_user):
    u = request.user
    if u.is_superuser:
        return True
    if u.has_ruolo('admin'):
        az = get_azienda_operativa(u, request.session)
        return _candidato_appartiene_ad_azienda(candidato_user, az) if az else False
    if u.has_ruolo('hr'):
        az = getattr(u, 'azienda', None)
        return _candidato_appartiene_ad_azienda(candidato_user, az) if az else False
    return False


def get_candidato_gestionabile_o_404(request, user_id):
    candidato = get_object_or_404(User, pk=user_id)
    if not candidato.is_candidato_portale():
        raise Http404()
    if not _candidato_gestionabile_da_richiedente(request, candidato):
        raise Http404()
    return candidato


def _invia_email_richiesta_integrazione(request, richiesta):
    config = ConfigurazioneSistema.get()
    nome_sito = config.nome_sito or 'GESPER'
    link = outbound_absolute_uri(request, reverse('candidato_completa_profilo'))
    corpo = (
        f"Gentile {richiesta.candidato.first_name} {richiesta.candidato.last_name},\n\n"
        f"l'ufficio HR ti chiede di integrare il profilo prima della convalida della candidatura su {nome_sito}.\n\n"
        f"Titolo richiesta: {richiesta.titolo}\n"
        f"Ruolo richiesto: {richiesta.ruolo_richiesto or 'non specificato'}\n\n"
        f"Istruzioni:\n{richiesta.messaggio or '- Compila/aggiorna i dati richiesti nel profilo.'}\n\n"
        f"Accedi qui per completare le integrazioni:\n{link}\n\n"
        f"Dopo il salvataggio del profilo, conferma l'integrazione direttamente dalla pagina.\n\n"
        f"— {nome_sito}"
    )
    try:
        if config.smtp_user and config.smtp_password:
            conn = get_connection(
                backend='accounts.email_backend.ConfigurazioneSistemaEmailBackend',
                host=config.smtp_host,
                port=config.smtp_port,
                username=config.smtp_user,
                password=config.smtp_password,
                use_tls=config.smtp_use_tls and not config.smtp_use_ssl,
                use_ssl=config.smtp_use_ssl,
                fail_silently=False,
            )
            msg = EmailMessage(
                subject=f"[{nome_sito}] Richiesta integrazione profilo",
                body=corpo,
                from_email=config.from_email(),
                to=[richiesta.candidato.email],
                connection=conn,
            )
        else:
            msg = EmailMessage(
                subject=f"[{nome_sito}] Richiesta integrazione profilo",
                body=corpo,
                from_email=getattr(settings, 'DEFAULT_FROM_EMAIL', 'noreply@gesper.it'),
                to=[richiesta.candidato.email],
            )
        msg.send()
        logger.info('[INTEGRAZIONE] E-mail inviata a %s', richiesta.candidato.email)
    except Exception as exc:
        logger.error('[INTEGRAZIONE] Errore invio mail a %s: %s', richiesta.candidato.email, exc)


def _notifica_hr_integrazione_completata(request, richiesta):
    config = ConfigurazioneSistema.get()
    destinatario_hr = config.email_notifiche_hr
    if not destinatario_hr:
        return

    nome_sito = config.nome_sito or 'GESPER'
    link = outbound_absolute_uri(
        request,
        reverse('candidato_admin_dettaglio', args=[richiesta.candidato_id]),
    )
    corpo = (
        f"Il candidato {richiesta.candidato.first_name} {richiesta.candidato.last_name} ha completato la richiesta di integrazione.\n\n"
        f"Titolo: {richiesta.titolo}\n"
        f"Data completamento: {timezone.localtime(richiesta.data_completamento_candidato).strftime('%d/%m/%Y %H:%M') if richiesta.data_completamento_candidato else '—'}\n\n"
        f"Note candidato:\n{richiesta.note_candidato or 'Nessuna nota'}\n\n"
        f"Verifica e approva qui:\n{link}\n\n"
        f"— {nome_sito}"
    )
    try:
        if config.smtp_user and config.smtp_password:
            conn = get_connection(
                backend='accounts.email_backend.ConfigurazioneSistemaEmailBackend',
                host=config.smtp_host,
                port=config.smtp_port,
                username=config.smtp_user,
                password=config.smtp_password,
                use_tls=config.smtp_use_tls and not config.smtp_use_ssl,
                use_ssl=config.smtp_use_ssl,
                fail_silently=False,
            )
            msg = EmailMessage(
                subject=f"[{nome_sito}] Integrazione completata da candidato",
                body=corpo,
                from_email=config.from_email(),
                to=[destinatario_hr],
                connection=conn,
            )
        else:
            msg = EmailMessage(
                subject=f"[{nome_sito}] Integrazione completata da candidato",
                body=corpo,
                from_email=getattr(settings, 'DEFAULT_FROM_EMAIL', 'noreply@gesper.it'),
                to=[destinatario_hr],
            )
        msg.send()
    except Exception as exc:
        logger.error('[INTEGRAZIONE] Errore notifica HR %s: %s', destinatario_hr, exc)


def _build_iter(candidato, profilo, proposte, ultima_richiesta=None):
    """
    Costruisce la timeline dell'iter di assunzione per un candidato.
    Restituisce lista di dict {label, data, done, active}.
    """
    from rapporto_di_lavoro.models import PropostaAssunzione, RapportoDiLavoro

    proposta = proposte.first()
    contratto = (
        RapportoDiLavoro.objects
        .filter(dipendente__utente=candidato)
        .order_by('-data_creazione')
        .first()
    )

    steps = []

    # 1. Registrazione
    steps.append({
        'step_key': 'registrazione',
        'icon': 'bi-person-plus',
        'label': 'Registrazione',
        'data': candidato.date_joined,
        'done': True,
        'active': False,
    })

    # 2. Verifica e-mail
    steps.append({
        'step_key': 'email',
        'icon': 'bi-envelope-check',
        'label': 'E-mail verificata',
        'data': None,
        'done': candidato.email_verificata,
        'active': not candidato.email_verificata,
    })

    # 3. Profilo completato
    # "done" = flag esplicito OPPURE tutti i campi obbligatori sono presenti
    # (evita disallineamento quando il candidato non ha cliccato "Conferma" ma il profilo è pieno)
    _profilo_campi_ok = bool(profilo and controlla_completezza_profilo(profilo)['completo'])
    _profilo_done = bool(profilo and profilo.profilo_completato) or _profilo_campi_ok
    steps.append({
        'step_key': 'profilo',
        'icon': 'bi-person-vcard',
        'label': 'Profilo completato',
        'data': profilo.data_completamento if profilo else None,
        'done': _profilo_done,
        'active': bool(candidato.email_verificata and profilo and not _profilo_done),
    })

    if ultima_richiesta:
        steps.append({
            'step_key': 'integrazione',
            'icon': 'bi-clipboard2-check',
            'label': 'Integrazione profilo',
            'data': (
                ultima_richiesta.data_approvazione_hr
                or ultima_richiesta.data_completamento_candidato
                or ultima_richiesta.data_invio
            ),
            'done': ultima_richiesta.stato == 'approvata_hr',
            'active': ultima_richiesta.stato in ('inviata', 'completata_candidato'),
            'richiesta_id': ultima_richiesta.pk,
        })

    # 4. Convalida HR
    steps.append({
        'step_key': 'convalida',
        'icon': 'bi-check2-circle',
        'label': 'Convalida HR',
        'data': None,
        'done': candidato.convalidato,
        'active': bool(
            _profilo_done and not candidato.convalidato
            and (not ultima_richiesta or ultima_richiesta.stato == 'approvata_hr')
        ),
    })

    # 5. Proposta inviata (canonico + legacy tramite equivalenze)
    stati_proposta_inviata = (
        set(PropostaAssunzione.stati_equivalenti('inviata_candidato'))
        | set(PropostaAssunzione.stati_equivalenti('firmata_candidato'))
        | set(PropostaAssunzione.stati_equivalenti('contratto_attivo'))
    )
    proposta_inviata = bool(proposta and proposta.stato in stati_proposta_inviata)
    steps.append({
        'step_key': 'proposta',
        'icon': 'bi-file-earmark-text',
        'label': 'Proposta inviata',
        'data': proposta.data_creazione if proposta else None,
        'done': proposta_inviata,
        'active': bool(candidato.convalidato and not proposta_inviata),
    })

    # 6. Firma digitale candidato
    steps.append({
        'step_key': 'firma',
        'icon': 'bi-pen',
        'label': 'Firma candidato',
        'data': proposta.data_firma_candidato if proposta else None,
        'done': bool(
            proposta
            and (
                proposta.stato in ('firmata_candidato', 'contratto_attivo', 'accettata_dipendente', 'convertita_in_contratto')
                or bool(proposta.data_firma_candidato)
            )
        ),
        'active': bool(proposta and proposta.is_inviata_al_candidato()),
        'proposta_id': proposta.pk if proposta else None,
    })

    # 7. Firma definitiva datore / contratto attivo
    steps.append({
        'step_key': 'contratto',
        'icon': 'bi-patch-check-fill',
        'label': 'Contratto attivo',
        'data': proposta.data_firma_datore if proposta else None,
        'done': bool(proposta and proposta.stato in PropostaAssunzione.stati_equivalenti('contratto_attivo')),
        'active': bool(
            proposta
            and proposta.is_firmata_da_candidato()
            and proposta.stato not in PropostaAssunzione.stati_equivalenti('contratto_attivo')
        ),
        'proposta_id': proposta.pk if proposta else None,
    })

    return steps


@login_required
@user_passes_test(_is_hr_or_admin)
def lista_candidati(request):
    """Lista di tutti i candidati registrati (admin/HR)."""
    from rapporto_di_lavoro.models import PropostaAssunzione

    candidati_qs = (
        User.objects
        .filter(Q(ruoli__codice='candidato') | Q(profilo_candidato__isnull=False))
        .select_related('profilo_candidato', 'profilo_candidato__azienda_interesse')
        .distinct()
        .order_by('-date_joined')
    )

    mostra_storico_assunti = request.GET.get('storico_assunti') == '1'

    # Filtri rapidi
    stato = request.GET.get('stato', '')
    if stato == 'da_verificare':
        candidati_qs = candidati_qs.filter(email_verificata=False)
    elif stato == 'verificati':
        candidati_qs = candidati_qs.filter(email_verificata=True, convalidato=False)
    elif stato == 'convalidati':
        candidati_qs = candidati_qs.filter(convalidato=True)
    elif stato == 'profilo_ok':
        candidati_qs = candidati_qs.filter(profilo_candidato__profilo_completato=True)
    elif stato == 'in_corso':
        # Ha almeno una proposta attiva
        candidati_qs = candidati_qs.filter(
            dipendente__proposte_assunzione__isnull=False
        ).distinct()

    # Admin (azienda operativa) e HR: solo candidati collegati alla propria azienda
    if not request.user.is_superuser:
        if request.user.has_ruolo('admin'):
            az_scope = get_azienda_operativa(request.user, request.session)
        elif request.user.has_ruolo('hr'):
            az_scope = getattr(request.user, 'azienda', None)
        else:
            az_scope = None
        if request.user.has_ruolo('admin') or request.user.has_ruolo('hr'):
            if az_scope is None:
                candidati_qs = candidati_qs.none()
            else:
                q_scope = (
                    Q(azienda=az_scope)
                    | Q(profilo_candidato__azienda_interesse=az_scope)
                    | Q(dipendente__azienda=az_scope)
                    | Q(dipendente__proposte_assunzione__azienda=az_scope)
                )
                # Registrazione pubblica non imposta azienda_interesse: in installazione con una
                # sola azienda i candidati «orfani» appartengono comunque a quell’unico datore.
                try:
                    from anagrafiche.models import Azienda

                    unica = Azienda.objects.only("pk").first()
                    if Azienda.objects.count() == 1 and unica and unica.pk == az_scope.pk:
                        q_scope |= Q(
                            profilo_candidato__isnull=False,
                            profilo_candidato__azienda_interesse__isnull=True,
                        )
                except Exception:
                    pass
                candidati_qs = candidati_qs.filter(q_scope).distinct()

    # Esclude anagrafiche già assunte/attive (default).
    # Opzionale: storico_assunti=1 per includerle in consultazione storica.
    if not mostra_storico_assunti:
        candidati_qs = candidati_qs.exclude(
            Q(dipendente__stato='attivo')
            | Q(dipendente__rapporti_di_lavoro__stato__in=['sottoscritto', 'sospeso'])
        ).distinct()

    # Arricchisci ogni candidato con lo step corrente dell'iter
    candidati = []
    for cand in candidati_qs:
        profilo = getattr(cand, 'profilo_candidato', None)
        proposte = PropostaAssunzione.objects.filter(
            dipendente__utente=cand
        ).order_by('-data_creazione')
        ultima_richiesta = get_ultima_richiesta_integrazione(cand)
        step_label, step_class = _step_corrente(cand, profilo, proposte, ultima_richiesta)
        completamento = controlla_completezza_profilo(profilo)
        anomalie = _diagnostica_anagrafica(cand, profilo)
        candidati.append({
            'user': cand,
            'profilo': profilo,
            'step_label': step_label,
            'step_class': step_class,
            'completamento': completamento,
            'anomalie': anomalie,
            'anomalie_count': len(anomalie),
            'storico_assunto': bool(
                getattr(getattr(cand, 'dipendente', None), 'stato', None) == 'attivo'
                or getattr(cand, 'dipendente', None)
                and cand.dipendente.rapporti_di_lavoro.filter(stato__in=['sottoscritto', 'sospeso']).exists()
            ),
        })

    if stato == 'anomalie':
        candidati = [r for r in candidati if r['anomalie_count'] > 0]

    paginator = Paginator(candidati, 20)
    page_obj = paginator.get_page(request.GET.get('page') or 1)

    ctx = {
        'candidati': page_obj,
        'page_obj': page_obj,
        'stato_filtro': stato,
        'totale': paginator.count,
        'mostra_storico_assunti': mostra_storico_assunti,
    }
    return render(request, 'accounts/lista_candidati.html', ctx)


def _step_corrente(candidato, profilo, proposte, ultima_richiesta=None):
    """Restituisce (label, css_class) dello step corrente nell'iter."""
    from rapporto_di_lavoro.models import RapportoDiLavoro

    contratto = RapportoDiLavoro.objects.filter(
        dipendente__utente=candidato, stato='sottoscritto'
    ).first()
    if contratto:
        return 'Assunto', 'success'

    proposta = proposte.first()
    if proposta:
        stato_canonico = proposta.stato_canonico
        mapping = {
            'bozza':               ('Bozza proposta', 'secondary'),
            'inviata_candidato':   ('Proposta inviata', 'primary'),
            'firmata_candidato':   ('Firmata — da approvare', 'info'),
            'contratto_attivo':    ('Contratto attivo', 'success'),
            'rifiutata_candidato': ('Rifiutata dal candidato', 'danger'),
            'rifiutata_admin':     ('Rifiutata dall\'admin', 'danger'),
            'rifiutata_dipendente': ('Proposta rifiutata', 'danger'),
        }
        return mapping.get(stato_canonico, (proposta.stato, 'secondary'))

    if candidato.convalidato:
        return 'Convalidato — attende proposta', 'info'
    if ultima_richiesta and ultima_richiesta.stato == 'inviata':
        return 'Integrazione richiesta — attende candidato', 'warning'
    if ultima_richiesta and ultima_richiesta.stato == 'completata_candidato':
        return 'Integrazione completata — attende HR', 'primary'
    _profilo_ok = bool(profilo and (
        profilo.profilo_completato or controlla_completezza_profilo(profilo)['completo']
    ))
    if _profilo_ok:
        return 'Profilo completo — da convalidare', 'warning'
    if candidato.email_verificata:
        return 'Da completare profilo', 'secondary'
    return 'E-mail non verificata', 'danger'


def _diagnostica_anagrafica(candidato, profilo):
    from accounts.sync_anagrafica import diagnostica_anagrafica_candidato

    return diagnostica_anagrafica_candidato(candidato, profilo)


@login_required
@user_passes_test(_is_hr_or_admin)
def candidato_admin_dettaglio(request, user_id):
    """Dettaglio profilo candidato per l'amministratore/HR."""
    candidato = get_candidato_gestionabile_o_404(request, user_id)
    profilo = getattr(candidato, 'profilo_candidato', None)

    from rapporto_di_lavoro.models import PropostaAssunzione, RapportoDiLavoro
    proposte = PropostaAssunzione.objects.filter(
        dipendente__utente=candidato
    ).order_by('-data_creazione')
    if not proposte.exists() and profilo and profilo.dipendente:
        proposte = PropostaAssunzione.objects.filter(
            dipendente=profilo.dipendente
        ).order_by('-data_creazione')

    contratto = RapportoDiLavoro.objects.filter(
        dipendente__utente=candidato
    ).order_by('-data_creazione').first()

    richiesta_attiva = get_richiesta_integrazione_attiva(candidato)
    ultima_richiesta = get_ultima_richiesta_integrazione(candidato)
    richiesta_riferimento = richiesta_attiva or ultima_richiesta
    checklist_integrazione = checklist_richiesta_integrazione(richiesta_riferimento, profilo)

    iter_steps = _build_iter(candidato, profilo, proposte, ultima_richiesta)

    # Flag: ha già una proposta non rifiutata/non annullata → blocca creazione nuova
    ha_proposta_attiva = proposte.exclude(
        stato__in=['rifiutata_candidato', 'rifiutata_admin',
                   'rifiutata_dipendente']  # legacy
    ).exists()

    completezza = controlla_completezza_profilo(profilo)
    puo_convalidare = bool(
        candidato.email_verificata and profilo and profilo.profilo_completato  # type: ignore[attr-defined]
        and (not ultima_richiesta or ultima_richiesta.stato == 'approvata_hr')
    )

    # Richieste aperte (non ancora approvate da HR) per gestione inline
    richieste_aperte = RichiestaIntegrazioneCandidato.objects.filter(
        candidato=candidato
    ).exclude(stato='approvata_hr').order_by('-data_invio')
    anomalie_anagrafica = _diagnostica_anagrafica(candidato, profilo)

    ctx = {
        'candidato': candidato,
        'profilo': profilo,
        'proposte': proposte,
        'contratto': contratto,
        'iter_steps': iter_steps,
        'ha_proposta_attiva': ha_proposta_attiva,
        'completezza': completezza,
        'richiesta_attiva': richiesta_attiva,
        'ultima_richiesta_integrazione': ultima_richiesta,
        'checklist_integrazione': checklist_integrazione,
        'puo_convalidare': puo_convalidare,
        'richieste_aperte': richieste_aperte,
        'anomalie_anagrafica': anomalie_anagrafica,
    }
    return render(request, 'accounts/candidato_admin_dettaglio.html', ctx)


@login_required
@user_passes_test(_is_hr_or_admin)
def riallinea_anagrafica_candidato(request, user_id):
    candidato = get_candidato_gestionabile_o_404(request, user_id)
    if request.method != 'POST':
        return redirect('candidato_admin_dettaglio', user_id=user_id)

    profilo = getattr(candidato, 'profilo_candidato', None)
    if not profilo:
        messages.error(request, 'Profilo candidato assente: impossibile riallineare anagrafica.')
        return redirect('candidato_admin_dettaglio', user_id=user_id)

    try:
        from accounts.sync_anagrafica import sincronizza_dipendente_da_profilo

        dip = sincronizza_dipendente_da_profilo(candidato, profilo, create_if_missing=True)
        if dip:
            profilo.save(update_fields=['dipendente'])
            residue = _diagnostica_anagrafica(candidato, profilo)
            if residue:
                messages.warning(
                    request,
                    'Riallineamento eseguito con anomalie residue: verifica il pannello Anomalie anagrafiche.',
                )
            else:
                messages.success(request, 'Riallineamento anagrafico completato senza anomalie.')
        else:
            messages.error(
                request,
                'Riallineamento non completato: possibile conflitto su codice fiscale con altro profilo.',
            )
    except Exception as exc:
        logger.error('[ANAGRAFICA] Riallineamento fallito user %s: %s', user_id, exc)
        messages.error(request, 'Errore tecnico durante il riallineamento anagrafico.')

    return redirect('candidato_admin_dettaglio', user_id=user_id)


@login_required
@user_passes_test(_is_hr_or_admin)
def invia_richiesta_integrazione_candidato(request, user_id):
    candidato = get_candidato_gestionabile_o_404(request, user_id)
    if request.method == 'POST':
        # Permettiamo l'invio di più richieste anche se esistono richieste aperte

        titolo = (request.POST.get('titolo') or 'Richiesta integrazione profilo').strip()
        messaggio = (request.POST.get('messaggio') or '').strip()
        ruolo_richiesto = (request.POST.get('ruolo_richiesto') or '').strip()
        flags = {
            'richiedi_documento_identita': bool(request.POST.get('richiedi_documento_identita')),
            'richiedi_codice_fiscale': bool(request.POST.get('richiedi_codice_fiscale')),
            'richiedi_curriculum': bool(request.POST.get('richiedi_curriculum')),
            'richiedi_mansione': bool(request.POST.get('richiedi_mansione')),
            'richiedi_disponibilita': bool(request.POST.get('richiedi_disponibilita')),
        }
        if not messaggio and not ruolo_richiesto and not any(flags.values()):
            messages.error(request, 'Specificare almeno un’integrazione richiesta o un messaggio HR.')
            return redirect('candidato_admin_dettaglio', user_id=user_id)

        richiesta = RichiestaIntegrazioneCandidato.objects.create(
            candidato=candidato,
            richiesta_da=request.user,
            titolo=titolo,
            messaggio=messaggio,
            ruolo_richiesto=ruolo_richiesto,
            **flags,
        )
        _invia_email_richiesta_integrazione(request, richiesta)
        messages.success(request, 'Richiesta di integrazione inviata al candidato via profilo ed e-mail.')
        return redirect('candidato_admin_dettaglio', user_id=user_id)

    return redirect('candidato_admin_dettaglio', user_id=user_id)


@login_required
@user_passes_test(_is_hr_or_admin)
def approva_richiesta_integrazione_candidato(request, user_id, richiesta_id):
    richiesta = get_object_or_404(
        RichiestaIntegrazioneCandidato,
        id=richiesta_id,
        candidato_id=user_id,
    )
    if request.method == 'POST':
        if richiesta.stato != 'completata_candidato':
            messages.error(request, 'La richiesta non è ancora stata confermata dal candidato.')
            return redirect('candidato_admin_dettaglio', user_id=user_id)

        richiesta.stato = 'approvata_hr'
        richiesta.note_hr = (request.POST.get('note_hr') or '').strip()
        richiesta.data_approvazione_hr = timezone.now()
        richiesta.save(update_fields=['stato', 'note_hr', 'data_approvazione_hr'])
        messages.success(request, 'Integrazione approvata. Ora puoi procedere con la convalida del candidato.')
        return redirect('candidato_admin_dettaglio', user_id=user_id)

    return redirect('candidato_admin_dettaglio', user_id=user_id)


@login_required
@user_passes_test(_is_hr_or_admin)
def convalida_candidato(request, user_id):
    candidato = get_candidato_gestionabile_o_404(request, user_id)
    if request.method == 'POST':
        ultima_richiesta = get_ultima_richiesta_integrazione(candidato)
        if ultima_richiesta and ultima_richiesta.stato != 'approvata_hr':
            messages.error(request, 'Prima della convalida occorre chiudere e approvare la richiesta di integrazione del profilo.')
            return redirect('candidato_admin_dettaglio', user_id=user_id)

        candidato.convalidato = True  # type: ignore[attr-defined]
        candidato.save(update_fields=['convalidato'])
        messages.success(request, f"Candidato {candidato.first_name} {candidato.last_name} convalidato.")
        logger.info(f"[CONVALIDA] {candidato.email} convalidato da {request.user.username}")
    return redirect('candidato_admin_dettaglio', user_id=user_id)


@login_required
@user_passes_test(_is_hr_or_admin)
def revoca_convalida_candidato(request, user_id):
    candidato = get_candidato_gestionabile_o_404(request, user_id)
    if request.method == 'POST':
        candidato.convalidato = False  # type: ignore[attr-defined]
        candidato.save(update_fields=['convalidato'])
        messages.warning(request, f"Convalida revocata per {candidato.first_name} {candidato.last_name}.")
    return redirect('candidato_admin_dettaglio', user_id=user_id)


@login_required
@user_passes_test(_is_hr_or_admin)
def crea_proposta_da_candidato(request, user_id):
    from rapporto_di_lavoro.models import PropostaAssunzione

    candidato = get_candidato_gestionabile_o_404(request, user_id)
    profilo = getattr(candidato, 'profilo_candidato', None)
    ultima_richiesta = get_ultima_richiesta_integrazione(candidato)

    if ultima_richiesta and ultima_richiesta.stato != 'approvata_hr':
        messages.error(request, 'La proposta può essere creata solo dopo l’approvazione HR dell’integrazione richiesta.')
        return redirect('candidato_admin_dettaglio', user_id=user_id)

    if not candidato.convalidato:  # type: ignore[attr-defined]
        messages.error(request, "Il candidato non è stato ancora convalidato. Convalidare il candidato prima di creare una proposta.")
        return redirect('candidato_admin_dettaglio', user_id=user_id)

    # Accetta anche profili con tutti i campi presenti ma flag non ancora impostato dal candidato
    _profilo_ok = bool(profilo and (
        profilo.profilo_completato or controlla_completezza_profilo(profilo)['completo']
    ))
    if not _profilo_ok:
        messages.warning(request, "Il candidato non ha ancora completato il profilo.")
        return redirect('candidato_admin_dettaglio', user_id=user_id)

    if not profilo.dipendente:
        # Tenta sync automatico
        try:
            from accounts.sync_anagrafica import sincronizza_dipendente_da_profilo
            sincronizza_dipendente_da_profilo(candidato, profilo)
            profilo.save(update_fields=['dipendente'])
        except Exception as _exc:
            logger.warning("Auto-sync dipendente fallito per user %s: %s", user_id, _exc)

    if not profilo.dipendente:
        messages.error(request, "Impossibile creare il record anagrafico. Verificare che il profilo sia completo e che il codice fiscale non sia già in uso da un altro dipendente.")
        return redirect('candidato_admin_dettaglio', user_id=user_id)

    # Blocco: dati obbligatori mancanti
    completezza = controlla_completezza_profilo(profilo)
    if not completezza['completo']:
        campi = ', '.join(label for _, label in completezza['mancanti'])
        messages.error(
            request,
            f"Impossibile creare la proposta: mancano dati obbligatori del profilo — {campi}. "
            "Chiedere al candidato di completare il profilo prima di procedere."
        )
        return redirect('candidato_admin_dettaglio', user_id=user_id)

    if completezza['doc_scaduto']:
        messages.warning(
            request,
            "Attenzione: il documento di identità del candidato risulta scaduto. "
            "Verificare con il candidato prima di procedere."
        )

    # Blocco: candidato ha già una proposta attiva
    proposta_attiva = PropostaAssunzione.objects.filter(
        dipendente=profilo.dipendente
    ).exclude(stato__in=['rifiutata_candidato', 'rifiutata_admin',
                         'rifiutata_dipendente']).first()

    if proposta_attiva:
        messages.error(
            request,
            f"Il candidato ha già una proposta attiva ({proposta_attiva.numero_proposta} — "
            f"{proposta_attiva.get_stato_display()}). "  # type: ignore[attr-defined]
            "Elimina o attendi il completamento dell'iter prima di crearne una nuova."
        )
        return redirect('candidato_admin_dettaglio', user_id=user_id)

    return redirect(
        reverse('crea_proposta_assunzione') + f"?dipendente_id={profilo.dipendente.id}"
    )  # type: ignore[attr-defined]


@login_required
@user_passes_test(_is_hr_or_admin)
def elimina_proposta_candidato(request, user_id, proposta_id):
    """Elimina una proposta duplicata o erroneamente creata per un candidato."""
    from rapporto_di_lavoro.models import PropostaAssunzione

    candidato = get_candidato_gestionabile_o_404(request, user_id)
    profilo = getattr(candidato, 'profilo_candidato', None)

    proposta = get_object_or_404(
        PropostaAssunzione,
        id=proposta_id,
        dipendente__utente=candidato,
    )

    # Blocca eliminazione se proposta è in stato avanzato
    stati_bloccati = set(PropostaAssunzione.stati_equivalenti('contratto_attivo')) | set(
        PropostaAssunzione.stati_equivalenti('firmata_candidato')
    )
    if proposta.stato in stati_bloccati:
        messages.error(
            request,
            f"Impossibile eliminare la proposta {proposta.numero_proposta}: "
            f"è già in stato '{proposta.get_stato_display()}'."  # type: ignore[attr-defined]
        )
        return redirect('candidato_admin_dettaglio', user_id=user_id)

    if request.method == 'POST':
        numero = proposta.numero_proposta
        proposta.delete()
        messages.success(request, f"Proposta {numero} eliminata.")
        logger.info(
            f"[ELIMINA_PROPOSTA] {numero} eliminata da {request.user.username} "
            f"per candidato {candidato.email}"
        )
        return redirect('candidato_admin_dettaglio', user_id=user_id)

    return render(request, 'accounts/elimina_proposta_confirm.html', {
        'candidato': candidato,
        'proposta': proposta,
    })


@login_required
@user_passes_test(_is_hr_or_admin)
def respingi_proposta_candidato(request, user_id, proposta_id):
    """
    Imposta la proposta a 'rifiutata_admin'.
    Usata sia come 'Annulla' (bozza/inviata) sia come 'Respingi' (accettata/in revisione).
    """
    from rapporto_di_lavoro.models import PropostaAssunzione

    candidato = get_candidato_gestionabile_o_404(request, user_id)
    proposta = get_object_or_404(PropostaAssunzione, id=proposta_id, dipendente__utente=candidato)

    stati_bloccati = {
        'rifiutata_admin',
        'rifiutata_candidato',
        'rifiutata_dipendente',
        *PropostaAssunzione.stati_equivalenti('contratto_attivo'),
    }
    if proposta.stato in stati_bloccati:
        messages.error(request, f"Impossibile modificare la proposta {proposta.numero_proposta}: stato '{proposta.get_stato_display()}'.")  # type: ignore[attr-defined]
        return redirect('candidato_admin_dettaglio', user_id=user_id)

    if request.method == 'POST':
        proposta.stato = 'rifiutata_admin'
        proposta.modificato_da = request.user.username
        proposta.data_modifica = timezone.now()
        proposta.save(update_fields=['stato', 'modificato_da', 'data_modifica'])
        messages.warning(request, f"Proposta {proposta.numero_proposta} impostata come rifiutata dall'amministrazione.")
        logger.info(
            f"[RESPINGI_PROPOSTA] {proposta.numero_proposta} → rifiutata_admin "
            f"da {request.user.username} per candidato {candidato.email}"
        )
        return redirect('candidato_admin_dettaglio', user_id=user_id)

    # Determina etichetta in base allo stato corrente
    if proposta.stato in ('bozza', *PropostaAssunzione.stati_equivalenti('inviata_candidato')):
        azione = 'annulla'
        titolo = 'Annulla proposta'
        messaggio = 'Vuoi annullare questa proposta? Sarà impostata come rifiutata dall\'amministrazione.'
    else:
        azione = 'respingi'
        titolo = 'Respingi proposta'
        messaggio = 'Vuoi respingere questa proposta? Sarà impostata come rifiutata dall\'amministrazione.'

    return render(request, 'accounts/respingi_proposta_confirm.html', {
        'candidato': candidato,
        'proposta': proposta,
        'azione': azione,
        'titolo': titolo,
        'messaggio': messaggio,
    })


@login_required
@user_passes_test(_is_hr_or_admin)
def riapri_proposta_candidato(request, user_id, proposta_id):
    """Riporta una proposta rifiutata a stato 'bozza' per poterla modificare e reinviare."""
    from rapporto_di_lavoro.models import PropostaAssunzione

    candidato = get_candidato_gestionabile_o_404(request, user_id)
    proposta = get_object_or_404(PropostaAssunzione, id=proposta_id, dipendente__utente=candidato)

    stati_ammessi = ['rifiutata_admin', 'rifiutata_candidato', 'rifiutata_dipendente']
    if proposta.stato not in stati_ammessi:
        messages.error(request, f"Impossibile riaprire la proposta {proposta.numero_proposta}: stato '{proposta.get_stato_display()}'.")  # type: ignore[attr-defined]
        return redirect('candidato_admin_dettaglio', user_id=user_id)

    if request.method == 'POST':
        proposta.stato = 'bozza'
        proposta.modificato_da = request.user.username
        proposta.data_modifica = timezone.now()
        proposta.save(update_fields=['stato', 'modificato_da', 'data_modifica'])
        messages.success(request, f"Proposta {proposta.numero_proposta} riaperta e riportata in bozza.")
        logger.info(
            f"[RIAPRI_PROPOSTA] {proposta.numero_proposta} → bozza "
            f"da {request.user.username} per candidato {candidato.email}"
        )
        return redirect('candidato_admin_dettaglio', user_id=user_id)

    return render(request, 'accounts/riapri_proposta_confirm.html', {
        'candidato': candidato,
        'proposta': proposta,
    })


@login_required
@user_passes_test(_is_hr_or_admin)
def assegna_proposta_candidato(request, user_id):
    from rapporto_di_lavoro.models import PropostaAssunzione
    from django.db.models import Q

    candidato = get_candidato_gestionabile_o_404(request, user_id)
    profilo = getattr(candidato, 'profilo_candidato', None)

    # Auto-crea il dipendente se mancante
    if profilo and not profilo.dipendente:
        try:
            from accounts.sync_anagrafica import sincronizza_dipendente_da_profilo
            sincronizza_dipendente_da_profilo(candidato, profilo)
            profilo.save(update_fields=['dipendente'])
        except Exception as _exc:
            logger.warning("Auto-sync dipendente fallito per user %s: %s", user_id, _exc)

    if not profilo or not profilo.dipendente:
        messages.error(request, "Il candidato non ha ancora un profilo anagrafico completo.")
        return redirect('candidato_admin_dettaglio', user_id=user_id)

    azienda_operativa = (
        get_azienda_operativa(request.user, request.session)
        if (request.user.is_superuser or request.user.has_ruolo('admin'))
        else getattr(request.user, 'azienda', None)
    )

    # Proposte in bozza disponibili: libere (dipendente senza utente) O già legate a questo candidato
    qs = PropostaAssunzione.objects.filter(
        stato='bozza',
    ).filter(
        Q(dipendente__utente__isnull=True) | Q(dipendente__utente=candidato)
    ).select_related('azienda', 'dipendente', 'tipo_contratto').order_by('-data_creazione')

    if azienda_operativa:
        qs = qs.filter(azienda=azienda_operativa)

    proposte_disponibili = qs

    if request.method == 'POST':
        proposta_id = request.POST.get('proposta_id')
        if proposta_id:
            try:
                proposta = PropostaAssunzione.objects.get(id=proposta_id, stato='bozza')
                # Sicurezza: non assegnare a un altro utente
                if (proposta.dipendente.utente is not None
                        and proposta.dipendente.utente != candidato):
                    messages.error(request, "Questa proposta è già assegnata a un altro candidato.")
                else:
                    proposta.dipendente = profilo.dipendente
                    proposta.save(update_fields=['dipendente'])
                    # Assicura che il dipendente abbia l'utente corretto
                    if not profilo.dipendente.utente:
                        profilo.dipendente.utente = candidato
                        profilo.dipendente.save(update_fields=['utente'])
                    messages.success(
                        request,
                        f"Proposta {proposta.numero_proposta} assegnata a "
                        f"{candidato.first_name} {candidato.last_name}."
                    )
            except PropostaAssunzione.DoesNotExist:
                messages.error(request, "Proposta non trovata o non più disponibile.")
        return redirect('candidato_admin_dettaglio', user_id=user_id)

    return render(request, 'accounts/assegna_proposta_candidato.html', {
        'candidato': candidato,
        'profilo': profilo,
        'proposte_disponibili': proposte_disponibili,
    })


@login_required
@user_passes_test(_is_hr_or_admin)
def modifica_profilo_candidato(request, user_id):
    """Admin/HR: legge e modifica il profilo del candidato."""
    from .forms import ProfiloCandidatoForm

    candidato = get_candidato_gestionabile_o_404(request, user_id)
    profilo = getattr(candidato, 'profilo_candidato', None)

    if not profilo:
        messages.error(request, "Il candidato non ha ancora un profilo.")
        return redirect('candidato_admin_dettaglio', user_id=user_id)

    if request.method == 'POST':
        form = ProfiloCandidatoForm(request.POST, request.FILES, instance=profilo)
        if form.is_valid():
            profilo = form.save()
            from accounts.sync_anagrafica import sincronizza_dipendente_da_profilo
            sincronizza_dipendente_da_profilo(candidato, profilo, create_if_missing=False)
            logger.info(
                '[MODIFICA_PROFILO] Profilo candidato %s modificato da %s',
                user_id, request.user.username,
            )
            messages.success(request, "Profilo aggiornato con successo.")
            return redirect('candidato_admin_dettaglio', user_id=user_id)
    else:
        form = ProfiloCandidatoForm(instance=profilo)

    return render(request, 'accounts/modifica_profilo_candidato.html', {
        'candidato': candidato,
        'profilo': profilo,
        'form': form,
    })


@login_required
@user_passes_test(_is_hr_or_admin)
def annulla_profilo_candidato(request, user_id):
    """
    Admin/HR: annulla il profilo del candidato.
    - Imposta profilo_completato=False
    - Revoca la convalida (convalidato=False)
    - Scollega il record Dipendente dal profilo (senza eliminarlo)
    """
    if request.method != 'POST':
        return redirect('candidato_admin_dettaglio', user_id=user_id)

    candidato = get_candidato_gestionabile_o_404(request, user_id)
    profilo = getattr(candidato, 'profilo_candidato', None)

    if profilo:
        profilo.profilo_completato = False
        profilo.data_completamento = None
        profilo.dipendente = None
        profilo.save(update_fields=['profilo_completato', 'data_completamento', 'dipendente'])

    candidato.convalidato = False  # type: ignore[attr-defined]
    candidato.save(update_fields=['convalidato'])

    logger.info(
        '[ANNULLA_PROFILO] Profilo candidato %s annullato da %s',
        user_id, request.user.username,
    )
    messages.warning(
        request,
        f"Profilo di {candidato.first_name} {candidato.last_name} annullato. "
        "Il candidato dovrà completare nuovamente il profilo."
    )
    return redirect('candidato_admin_dettaglio', user_id=user_id)


@login_required
@user_passes_test(_is_hr_or_admin)
def forza_tutto_candidato(request, user_id):
    """Admin/HR: sblocca tutti i passaggi in sospeso in un colpo solo."""
    candidato = get_candidato_gestionabile_o_404(request, user_id)
    if request.method == 'POST':
        forzati = []

        # 1. E-mail
        if not candidato.email_verificata:  # type: ignore[attr-defined]
            candidato.email_verificata = True  # type: ignore[attr-defined]
            forzati.append('e-mail verificata')

        # 2. Profilo
        profilo = getattr(candidato, 'profilo_candidato', None)
        if profilo and not profilo.profilo_completato:
            profilo.profilo_completato = True
            if not profilo.data_completamento:
                profilo.data_completamento = timezone.now()
            profilo.save(update_fields=['profilo_completato', 'data_completamento'])
            forzati.append('profilo completato')

        # 3. Chiudi tutte le richieste integrazione aperte
        aperte = RichiestaIntegrazioneCandidato.objects.filter(
            candidato=candidato
        ).exclude(stato='approvata_hr')
        if aperte.exists():
            aperte.update(stato='approvata_hr', data_approvazione_hr=timezone.now())
            forzati.append('richieste integrazione chiuse')

        # 4. Convalida
        if not candidato.convalidato:  # type: ignore[attr-defined]
            candidato.convalidato = True  # type: ignore[attr-defined]
            forzati.append('convalida HR')

        candidato.save(update_fields=['email_verificata', 'convalidato'])

        if forzati:
            messages.success(
                request,
                f"Sblocco completato: {', '.join(forzati)}. Il candidato è ora pronto per la proposta."
            )
            logger.info('[FORZA_TUTTO] user %s sbloccato da %s (%s)', user_id, request.user.username, ', '.join(forzati))
        else:
            messages.info(request, "Nessun blocco da rimuovere: il candidato è già nelle condizioni richieste.")
    return redirect('candidato_admin_dettaglio', user_id=user_id)


@login_required
@user_passes_test(_is_hr_or_admin)
def reset_email_verifica_candidato(request, user_id):
    """Admin/HR: rimuove la verifica e-mail (ripristina stato non verificato)."""
    candidato = get_candidato_gestionabile_o_404(request, user_id)
    if request.method == 'POST':
        candidato.email_verificata = False  # type: ignore[attr-defined]
        candidato.save(update_fields=['email_verificata'])
        messages.warning(request, f"Verifica e-mail di {candidato.first_name} {candidato.last_name} rimossa.")
        logger.info('[RESET_EMAIL] %s ripristinata non-verificata da %s', candidato.email, request.user.username)
    return redirect('candidato_admin_dettaglio', user_id=user_id)


@login_required
@user_passes_test(_is_hr_or_admin)
def forza_verifica_email_candidato(request, user_id):
    """Admin/HR: forza la verifica e-mail del candidato (bypass token)."""
    candidato = get_candidato_gestionabile_o_404(request, user_id)
    if request.method == 'POST':
        candidato.email_verificata = True  # type: ignore[attr-defined]
        candidato.save(update_fields=['email_verificata'])
        messages.success(
            request,
            f"E-mail di {candidato.first_name} {candidato.last_name} contrassegnata come verificata."
        )
        logger.info('[FORZA_EMAIL] %s verificata da %s', candidato.email, request.user.username)
    return redirect('candidato_admin_dettaglio', user_id=user_id)


@login_required
@user_passes_test(_is_hr_or_admin)
def forza_profilo_completato_candidato(request, user_id):
    """Admin/HR: forza il completamento del profilo del candidato."""
    candidato = get_candidato_gestionabile_o_404(request, user_id)
    profilo = getattr(candidato, 'profilo_candidato', None)
    if request.method == 'POST':
        if not profilo:
            messages.error(request, "Il candidato non ha ancora un profilo da completare.")
            return redirect('candidato_admin_dettaglio', user_id=user_id)
        profilo.profilo_completato = True
        if not profilo.data_completamento:
            profilo.data_completamento = timezone.now()
        profilo.save(update_fields=['profilo_completato', 'data_completamento'])
        messages.success(
            request,
            f"Profilo di {candidato.first_name} {candidato.last_name} contrassegnato come completato."
        )
        logger.info('[FORZA_PROFILO] profilo %s completato da %s', user_id, request.user.username)
    return redirect('candidato_admin_dettaglio', user_id=user_id)


@login_required
@user_passes_test(_is_hr_or_admin)
def elimina_richiesta_integrazione_candidato(request, user_id, richiesta_id):
    """Admin/HR: elimina una richiesta di integrazione profilo."""
    candidato = get_candidato_gestionabile_o_404(request, user_id)
    richiesta = get_object_or_404(RichiestaIntegrazioneCandidato, id=richiesta_id, candidato=candidato)
    if request.method == 'POST':
        richiesta.delete()
        messages.success(request, "Richiesta di integrazione eliminata.")
        logger.info(
            '[ELIMINA_RICHIESTA] richiesta %s eliminata da %s per candidato %s',
            richiesta_id, request.user.username, candidato.email,
        )
    return redirect('candidato_admin_dettaglio', user_id=user_id)


@login_required
@user_passes_test(_is_hr_or_admin)
def modifica_richiesta_integrazione_candidato(request, user_id, richiesta_id):
    """Admin/HR: modifica titolo e messaggio di una richiesta di integrazione."""
    candidato = get_candidato_gestionabile_o_404(request, user_id)
    richiesta = get_object_or_404(RichiestaIntegrazioneCandidato, id=richiesta_id, candidato=candidato)
    if request.method == 'POST':
        titolo = (request.POST.get('titolo') or richiesta.titolo).strip()
        messaggio = (request.POST.get('messaggio') or '').strip()
        richiesta.titolo = titolo
        if messaggio:
            richiesta.messaggio = messaggio
        richiesta.save(update_fields=['titolo', 'messaggio'])
        messages.success(request, "Richiesta di integrazione aggiornata.")
        logger.info(
            '[MODIFICA_RICHIESTA] richiesta %s modificata da %s',
            richiesta_id, request.user.username,
        )
    return redirect('candidato_admin_dettaglio', user_id=user_id)


@login_required
@user_passes_test(_is_hr_or_admin)
def chiudi_richiesta_integrazione_candidato(request, user_id, richiesta_id):
    """Admin/HR: chiude una richiesta di integrazione (forza stato approvata_hr)."""
    candidato = get_candidato_gestionabile_o_404(request, user_id)
    richiesta = get_object_or_404(RichiestaIntegrazioneCandidato, id=richiesta_id, candidato=candidato)
    if request.method == 'POST':
        if richiesta.stato == 'approvata_hr':
            messages.info(request, "La richiesta è già chiusa.")
            return redirect('candidato_admin_dettaglio', user_id=user_id)
        note_hr = (request.POST.get('note_hr') or '').strip()
        richiesta.stato = 'approvata_hr'
        richiesta.note_hr = note_hr
        richiesta.data_approvazione_hr = timezone.now()
        richiesta.save(update_fields=['stato', 'note_hr', 'data_approvazione_hr'])
        messages.success(request, "Richiesta chiusa. Il processo di assunzione può proseguire.")
        logger.info(
            '[CHIUDI_RICHIESTA] richiesta %s chiusa da %s per candidato %s',
            richiesta_id, request.user.username, candidato.email,
        )
    return redirect('candidato_admin_dettaglio', user_id=user_id)


@login_required
@user_passes_test(_is_hr_or_admin)
def aggiorna_campo_profilo_candidato(request, user_id):
    """Admin/HR: aggiorna rapidamente uno o più campi del profilo dalla lista candidati.

    POST: riceve i campi mancanti compilati, li salva sul profilo,
    controlla se ora il profilo è completo e aggiorna profilo_completato se necessario.
    Redirige a lista_candidati (o al dettaglio se ?next=dettaglio).
    """
    from .utils import CAMPI_OBBLIGATORI_PROPOSTA, CAMPI_CONSIGLIATI_PROPOSTA

    if request.method != 'POST':
        return redirect('lista_candidati')

    candidato = get_candidato_gestionabile_o_404(request, user_id)
    profilo = getattr(candidato, 'profilo_candidato', None)
    if not profilo:
        messages.error(request, 'Profilo non trovato.')
        return redirect('lista_candidati')

    CAMPI_DATA = {
        'data_nascita', 'data_emissione_documento',
        'scadenza_documento', 'data_disponibilita',
    }
    CAMPI_BOOLEANO = {'dichiarazione_no_condanne'}
    CAMPI_CONSENTITI = {campo for campo, _ in CAMPI_OBBLIGATORI_PROPOSTA + CAMPI_CONSIGLIATI_PROPOSTA}

    aggiornati = []
    for campo, label in CAMPI_OBBLIGATORI_PROPOSTA + CAMPI_CONSIGLIATI_PROPOSTA:
        valore_raw = request.POST.get(campo, '').strip()
        if not valore_raw:
            continue
        if campo not in CAMPI_CONSENTITI:
            continue
        try:
            if campo in CAMPI_DATA:
                from datetime import date
                valore = date.fromisoformat(valore_raw)
            elif campo in CAMPI_BOOLEANO:
                valore = valore_raw in ('on', '1', 'true', 'True')
            else:
                valore = valore_raw
            setattr(profilo, campo, valore)
            aggiornati.append(label)
        except Exception:
            messages.warning(request, f'Valore non valido per il campo "{label}" — ignorato.')

    if aggiornati:
        # Controlla se ora tutti i campi obbligatori sono presenti
        from .utils import controlla_completezza_profilo
        profilo.save()
        completezza = controlla_completezza_profilo(profilo)
        if completezza['completo'] and not profilo.profilo_completato:
            profilo.profilo_completato = True
            profilo.data_completamento = timezone.now()
            profilo.save(update_fields=['profilo_completato', 'data_completamento'])
            messages.success(
                request,
                f'Profilo di {candidato.first_name} {candidato.last_name} aggiornato e '
                f'completato automaticamente. Campi salvati: {", ".join(aggiornati)}.'
            )
        else:
            messages.success(
                request,
                f'Profilo di {candidato.first_name} {candidato.last_name} aggiornato. '
                f'Campi salvati: {", ".join(aggiornati)}.'
            )
        logger.info(
            '[AGGIORNA_CAMPO_PROFILO] candidato %s — campi aggiornati: %s — da %s',
            user_id, aggiornati, request.user.username,
        )
    else:
        messages.info(request, 'Nessun campo da aggiornare (tutti vuoti).')

    if request.POST.get('next') == 'dettaglio':
        return redirect('candidato_admin_dettaglio', user_id=user_id)
    return redirect('lista_candidati')


@login_required
@user_passes_test(_is_hr_or_admin)
def forza_convalida_candidato(request, user_id):
    """Permette ad admin/HR di forzare la convalida del candidato, anche se bloccata."""
    candidato = get_candidato_gestionabile_o_404(request, user_id)
    if request.method == 'POST':
        from django.utils import timezone
        candidato.convalidato = True  # type: ignore[attr-defined]
        candidato.save(update_fields=['convalidato'])
        # Aggiorna anche l'ultima richiesta di integrazione se esiste
        ultima_richiesta = get_ultima_richiesta_integrazione(candidato)
        if ultima_richiesta and ultima_richiesta.stato != 'approvata_hr':
            ultima_richiesta.stato = 'approvata_hr'
            ultima_richiesta.data_approvazione_hr = timezone.now()
            ultima_richiesta.save(update_fields=['stato', 'data_approvazione_hr'])
        messages.success(request, 'Convalida forzata eseguita: il candidato è ora convalidato e il processo può proseguire.')
    else:
        messages.error(request, 'Richiesta non valida.')
    return redirect('candidato_admin_dettaglio', user_id=user_id)
