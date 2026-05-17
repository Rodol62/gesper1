from notifiche_email.models import Notifica, TipoNotifica


def crea_notifica_evento(richiesta, evento_trigger: str, destinatario=None):
    """Crea una notifica applicativa da trigger evento.

    MVP: persistenza su DB, invio email asincrono verrà aggiunto nel blocco Celery.
    """
    tipo = (
        TipoNotifica.objects.filter(evento_trigger=evento_trigger, attivo=True)
        .order_by('id')
        .first()
    )
    if not tipo:
        return None

    utente_destinatario = destinatario or richiesta.richiesta_da
    if not utente_destinatario:
        return None

    email = (
        getattr(utente_destinatario, 'email', None)
        or getattr(richiesta.dipendente, 'email', None)
    )
    if not email:
        return None

    context = {
        'dipendente_nome': f"{richiesta.dipendente.nome} {richiesta.dipendente.cognome}",
        'tipo': richiesta.get_tipo_display() if hasattr(richiesta, 'get_tipo_display') else richiesta.tipo,
        'data_inizio': richiesta.data_inizio,
        'data_fine': richiesta.data_fine,
    }

    try:
        subject = tipo.template_subject.format(**context)
        body = tipo.template_body.format(**context)
    except Exception:
        subject = tipo.template_subject
        body = tipo.template_body

    return Notifica.objects.create(
        tipo=tipo,
        azienda=richiesta.azienda,
        destinatario=utente_destinatario,
        email_destinatario=email,
        subject=subject,
        body_html=body,
        stato='pending',
    )
