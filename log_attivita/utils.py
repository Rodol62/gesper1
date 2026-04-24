from .models import LogAttivita, _get_ip


def registra_log(utente, azienda, operazione, descrizione='', oggetto_id=None, request=None):
    """Registra un'attività utente. Chiamabile da qualunque view."""
    ip = _get_ip(request) if request else None
    LogAttivita.objects.create(
        utente=utente,
        azienda=azienda,
        operazione=operazione,
        descrizione=descrizione,
        oggetto_id=str(oggetto_id) if oggetto_id else '',
        ip_address=ip,
    )
