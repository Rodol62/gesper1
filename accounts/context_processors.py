from .gesper_paths import api_base_path, portal_web_base_path
from .models import ConfigurazioneSistema


def config_sistema(request):
    """Rende disponibile la configurazione di sistema in tutti i template."""
    try:
        config = ConfigurazioneSistema.get()
    except Exception:
        config = None
    return {'config': config}


def gesper_browser_paths(request):
    """Path web/API per script inline (``FORCE_SCRIPT_NAME``)."""
    try:
        api_b = api_base_path()
    except Exception:
        api_b = '/api/'
    return {
        'gesper_portal_web_base': portal_web_base_path(request),
        'gesper_api_base': api_b,
    }


def gesper_pwa_embed(request):
    """
    PWA: pagine aperte in iframe con ?pwa=1 — layout compattato, senza navbar/footer sito.
    (Parametro propagato con JavaScript; i link stessi vanno aggiornati in template/base.)
    """
    v = (request.GET.get('pwa') or request.GET.get('gesper_pwa') or '').lower()
    return {'gesper_pwa_embed': v in ('1', 'true', 'yes', 'y')}
