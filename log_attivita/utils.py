from .models import LogAttivita, _client_ip_for_db


def registra_log(utente, azienda, operazione, descrizione='', oggetto_id=None, request=None):
    """Registra un'attività utente. Chiamabile da qualunque view."""
    ip = _client_ip_for_db(request)
    LogAttivita.objects.create(
        utente=utente,
        azienda=azienda,
        operazione=operazione,
        descrizione=descrizione,
        oggetto_id=str(oggetto_id) if oggetto_id else '',
        ip_address=ip,
    )
