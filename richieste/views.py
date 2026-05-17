from django.shortcuts import render, get_object_or_404, redirect
from django.http import HttpResponseForbidden
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.db.models import Exists, OuterRef
from django.utils.html import strip_tags
from django.core.mail import EmailMessage, get_connection
from .models import Richiesta, InboxEmailDipendenteAzione
from anagrafiche.models import Dipendente
from accounts.models import ConfigurazioneSistema
from anagrafiche.permissions import admin_required, hr_required, dipendente_required
from log_attivita.utils import registra_log
from django.utils import timezone
from accounts.tenant import get_azienda_operativa
from api.views import send_push_to_user
from workflow.models import RichiestaApprovazione
from email import message_from_bytes
from email.header import decode_header
from email.utils import parseaddr, parsedate_to_datetime
import imaplib


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


def _decode_mime_header(value: str) -> str:
    if not value:
        return ''
    parts = decode_header(value)
    out = []
    for text, enc in parts:
        if isinstance(text, bytes):
            out.append(text.decode(enc or 'utf-8', errors='replace'))
        else:
            out.append(str(text))
    return ''.join(out).strip()


def _extract_text_snippet(msg, max_len: int = 220) -> str:
    if msg.is_multipart():
        for part in msg.walk():
            ctype = (part.get_content_type() or '').lower()
            disp = (part.get('Content-Disposition') or '').lower()
            if ctype == 'text/plain' and 'attachment' not in disp:
                payload = part.get_payload(decode=True) or b''
                charset = part.get_content_charset() or 'utf-8'
                text = payload.decode(charset, errors='replace')
                text = ' '.join(strip_tags(text).split())
                return text[:max_len]
    payload = msg.get_payload(decode=True) or b''
    charset = msg.get_content_charset() or 'utf-8'
    text = payload.decode(charset, errors='replace')
    text = ' '.join(strip_tags(text).split())
    return text[:max_len]


def _imap_host_candidates(smtp_host: str, username: str = '') -> list[str]:
    host = (smtp_host or '').strip()
    user = (username or '').strip().lower()
    domain = user.split('@', 1)[1] if '@' in user else ''
    cands = []
    if host:
        cands.append(host)
        if host.startswith('smtp.'):
            cands.append('imap.' + host[len('smtp.'):])
        if not host.startswith('imap.'):
            cands.append('imap.' + host)
    if domain:
        cands.extend([
            f'imap.{domain}',
            f'imaps.{domain}',
        ])
    # fallback noti Aruba/PEC Aruba
    cands.extend([
        'imaps.aruba.it',
        'imap.pec.aruba.it',
        'imaps.pec.aruba.it',
    ])
    # ordine + dedup
    seen = set()
    out = []
    for h in cands:
        k = h.lower()
        if h and k not in seen:
            seen.add(k)
            out.append(h)
    return out


@login_required
def inbox_email_dipendenti(request):
    if not _is_admin_or_hr(request.user):
        return HttpResponseForbidden("Accesso riservato ad admin e HR.")
    azienda_scope = _azienda_scope_for_staff(request.user, request)
    if not azienda_scope:
        messages.error(request, "Azienda operativa non disponibile.")
        return redirect('lista_richieste')

    cfg = ConfigurazioneSistema.get()
    username = (cfg.smtp_user or '').strip()
    password = (cfg.smtp_password or '').strip()
    dip_email_filter = (request.GET.get('dip_email') or '').strip().lower()
    mailbox = (request.GET.get('mailbox') or 'INBOX').strip() or 'INBOX'
    max_rows = 80
    testo_risposta_default = (
        "Buongiorno,\n\n"
        "abbiamo ricevuto la sua e-mail e la stiamo gestendo.\n\n"
        "Cordiali saluti"
    )

    dip_qs = Dipendente.objects.filter(azienda=azienda_scope).exclude(email='').exclude(email__isnull=True)
    if dip_email_filter:
        dip_qs = dip_qs.filter(email__icontains=dip_email_filter)
    dipendenti = list(dip_qs.only('id', 'nome', 'cognome', 'email'))
    dip_by_email = {str(d.email).strip().lower(): d for d in dipendenti if d.email}

    email_rows = []
    errore_conn = ''
    host_usato = ''
    totale_esaminate = 0
    totale_match = 0

    if request.method == 'POST':
        action = (request.POST.get('action') or '').strip().lower()
        uid_email = (request.POST.get('uid_email') or '').strip()
        mittente = (request.POST.get('mittente_email') or '').strip().lower()
        oggetto = (request.POST.get('oggetto') or '').strip()
        if not uid_email:
            messages.error(request, 'UID e-mail mancante.')
            return redirect('inbox_email_dipendenti')
        azione_obj, _ = InboxEmailDipendenteAzione.objects.get_or_create(
            azienda=azienda_scope,
            mailbox=mailbox,
            uid_email=uid_email,
            defaults={'mittente_email': mittente, 'oggetto': oggetto},
        )
        if action == 'hide':
            azione_obj.nascosta = True
            azione_obj.mittente_email = mittente or azione_obj.mittente_email
            azione_obj.oggetto = oggetto or azione_obj.oggetto
            azione_obj.save(update_fields=['nascosta', 'mittente_email', 'oggetto', 'aggiornata_il'])
            messages.success(request, 'E-mail rimossa dalla vista (non eliminata dal server di posta).')
            return redirect('inbox_email_dipendenti')
        if action == 'unhide':
            azione_obj.nascosta = False
            azione_obj.save(update_fields=['nascosta', 'aggiornata_il'])
            messages.success(request, 'E-mail ripristinata in elenco.')
            return redirect('inbox_email_dipendenti')
        if action == 'reply':
            testo_risposta = (request.POST.get('testo_risposta') or '').strip()
            if not mittente:
                messages.error(request, 'Mittente non valido per la risposta.')
                return redirect('inbox_email_dipendenti')
            if not testo_risposta:
                messages.error(request, 'Inserisci il testo risposta.')
                return redirect('inbox_email_dipendenti')
            try:
                conn = get_connection(backend='accounts.email_backend.ConfigurazioneSistemaEmailBackend')
                subj = oggetto or 'Riscontro richiesta'
                if not subj.lower().startswith('re:'):
                    subj = f'Re: {subj}'
                msg = EmailMessage(
                    subject=subj,
                    body=testo_risposta,
                    from_email=None,
                    to=[mittente],
                    connection=conn,
                )
                msg.send()
                azione_obj.risposta_inviata = True
                azione_obj.data_risposta = timezone.now()
                azione_obj.risposta_testo = testo_risposta
                azione_obj.mittente_email = mittente or azione_obj.mittente_email
                azione_obj.oggetto = oggetto or azione_obj.oggetto
                azione_obj.save(update_fields=[
                    'risposta_inviata', 'data_risposta', 'risposta_testo',
                    'mittente_email', 'oggetto', 'aggiornata_il',
                ])
                messages.success(request, f'Risposta inviata a {mittente}.')
            except Exception as exc:
                messages.error(request, f'Invio risposta non riuscito: {exc}')
            return redirect('inbox_email_dipendenti')

    if not username or not password or not cfg.smtp_host:
        errore_conn = 'Configurazione email incompleta: imposta host/utente/password in Impostazioni > Email.'
    else:
        last_exc = None
        for host in _imap_host_candidates(cfg.smtp_host, username):
            try:
                with imaplib.IMAP4_SSL(host, 993) as imap:
                    imap.login(username, password)
                    imap.select(mailbox)
                    typ, data = imap.uid('search', None, 'ALL')
                    if typ != 'OK':
                        break
                    uids = (data[0] or b'').split()
                    uids = uids[-250:]  # coda recente
                    azioni_map = {
                        a.uid_email: a
                        for a in InboxEmailDipendenteAzione.objects.filter(
                            azienda=azienda_scope,
                            mailbox=mailbox,
                            uid_email__in=[u.decode('utf-8', errors='ignore') for u in uids],
                        )
                    }
                    for uid in reversed(uids):
                        if len(email_rows) >= max_rows:
                            break
                        uid_s = uid.decode('utf-8', errors='ignore')
                        az = azioni_map.get(uid_s)
                        if az and az.nascosta:
                            continue
                        typ, payload = imap.uid('fetch', uid, '(RFC822)')
                        if typ != 'OK' or not payload or not payload[0]:
                            continue
                        raw = payload[0][1]
                        msg = message_from_bytes(raw)
                        totale_esaminate += 1
                        from_name, from_addr = parseaddr(msg.get('From', ''))
                        from_addr = (from_addr or '').strip().lower()
                        if not from_addr or 'cardella' in from_addr:
                            continue
                        dip = dip_by_email.get(from_addr)
                        if not dip:
                            continue
                        subj = _decode_mime_header(msg.get('Subject', ''))
                        dt = None
                        try:
                            dt = parsedate_to_datetime(msg.get('Date', ''))
                        except Exception:
                            dt = None
                        email_rows.append({
                            'uid_email': uid_s,
                            'from_addr': from_addr,
                            'from_name': from_name,
                            'subject': subj or '(senza oggetto)',
                            'date': dt,
                            'snippet': _extract_text_snippet(msg),
                            'dipendente': dip,
                            'gia_risposta': bool(az and az.risposta_inviata),
                            'data_risposta': az.data_risposta if az else None,
                        })
                    host_usato = host
                    break
            except Exception as exc:
                last_exc = exc
                continue
        if not host_usato:
            errore_conn = f'Connessione IMAP non riuscita: {last_exc}' if last_exc else 'Connessione IMAP non riuscita.'
    totale_match = len(email_rows)

    return render(request, 'richieste/inbox_email_dipendenti.html', {
        'email_rows': email_rows,
        'dip_email_filter': dip_email_filter,
        'host_usato': host_usato,
        'mailbox': mailbox,
        'totale_esaminate': totale_esaminate,
        'totale_match': totale_match,
        'errore_conn': errore_conn,
        'testo_risposta_default': testo_risposta_default,
    })


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
    dip_email_filter = (request.GET.get('dip_email') or '').strip()
    
    # Di default, mostra solo le richieste "inviata" (pendenti)
    if not stato_filter:
        richieste = richieste.filter(stato='inviata')
    elif stato_filter:
        richieste = richieste.filter(stato=stato_filter)
    
    if tipo_filter:
        richieste = richieste.filter(tipo=tipo_filter)
    if dip_email_filter and _is_admin_or_hr(request.user):
        richieste = richieste.filter(
            dipendente__email__icontains=dip_email_filter
        )

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
        'dip_email_filter': dip_email_filter,
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
