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


def consulente_recesso_prova_nav(request):
    """
    Conteggi per menu globale consulente: recesso in prova in verifica,
    proposte firmate (incl. stati legacy) da approvare.
    """
    zero = {'consulente_recesso_prova_nav_count': 0, 'consulente_proposte_nav_count': 0}
    u = getattr(request, 'user', None)
    if u is None or not getattr(u, 'is_authenticated', False):
        return zero
    has_ruolo = getattr(u, 'has_ruolo', None)
    if not callable(has_ruolo) or not has_ruolo('consulente'):
        return zero
    azienda = getattr(u, 'azienda', None)
    if azienda is None:
        return zero
    from anagrafiche.models import ComunicazioneRecessoProva
    from rapporto_di_lavoro.models import PropostaAssunzione

    n_recesso = (
        ComunicazioneRecessoProva.per_azienda(azienda)
        .filter(stato='in_verifica_consulente')
        .count()
    )
    firmati = PropostaAssunzione.stati_equivalenti('firmata_candidato')
    n_proposte = PropostaAssunzione.objects.filter(
        azienda_id=azienda.pk,
        stato__in=firmati,
    ).count()
    return {
        'consulente_recesso_prova_nav_count': n_recesso,
        'consulente_proposte_nav_count': n_proposte,
    }


def gesper_pwa_embed(request):
    """
    PWA: pagine aperte in iframe con ?pwa=1 — layout compattato, senza navbar/footer sito.
    (Parametro propagato con JavaScript; i link stessi vanno aggiornati in template/base.)
    """
    v = (request.GET.get('pwa') or request.GET.get('gesper_pwa') or '').lower()
    return {'gesper_pwa_embed': v in ('1', 'true', 'yes', 'y')}
