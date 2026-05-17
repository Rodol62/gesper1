import logging
from .models import LogErrore

logger = logging.getLogger('django')


class GesperErrorMiddleware:
    """
    Cattura le eccezioni non gestite e le salva in LogErrore.
    Da aggiungere in MIDDLEWARE dopo SessionMiddleware e AuthenticationMiddleware.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        return self.get_response(request)

    def process_exception(self, request, exception):
        """Chiamato da Django per ogni eccezione non gestita."""
        try:
            LogErrore.registra(
                messaggio=f"{type(exception).__name__}: {exception}",
                exc=exception,
                request=request,
                livello='error',
            )
        except Exception as inner:
            # Non bloccare mai l'applicazione per un errore di logging
            logger.error(f"[LOG ERRORE] Impossibile salvare il log: {inner}")
        # Restituisce None → Django continua con la gestione standard dell'errore
        return None
