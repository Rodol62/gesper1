"""
URL del portale candidati — prefisso: /candidato/
Accesso pubblico: registrazione, verifica email
Accesso privato (ruolo='candidato'): dashboard, profilo, ruoli
Accesso dipendente: contratto, buste paga, presenze, richieste, documenti
"""
from django.urls import path
from .views_registration import (
    register_candidato,
    candidato_verifica_email_inviata,
    verifica_email,
    reinvia_verifica,
)
from .views_candidato import (
    candidato_dashboard,
    candidato_completa_profilo,
    candidato_dettaglio_richiesta_ricevuta,
    candidato_ruoli_disponibili,
    candidato_esprimi_interesse,
    accetta_contratto_dipendente,
    candidato_mio_contratto,
    candidato_mie_buste_paga,
    candidato_mie_presenze,
    candidato_timbratura_geo,
    candidato_mie_richieste,
    candidato_nuova_richiesta,
    candidato_miei_documenti,
)

urlpatterns = [
    # ── Pubbliche ───────────────────────────────────────────────
    path('registrati/', register_candidato, name='register_candidato'),
    path('verifica-email/inviata/', candidato_verifica_email_inviata, name='candidato_verifica_email_inviata'),
    path('verifica-email/<str:token>/', verifica_email, name='verifica_email'),
    path('reinvia-verifica/', reinvia_verifica, name='reinvia_verifica'),

    # ── Area riservata candidato ────────────────────────────────
    path('', candidato_dashboard, name='candidato_dashboard'),
    path('profilo/', candidato_completa_profilo, name='candidato_completa_profilo'),
    path('posizioni/', candidato_ruoli_disponibili, name='candidato_ruoli_disponibili'),
    path('posizioni/<int:proposta_id>/interesse/', candidato_esprimi_interesse, name='candidato_esprimi_interesse'),

    # ── Area dipendente (self-service) ──────────────────────────
    path('contratto/<int:contratto_id>/accetta/', accetta_contratto_dipendente, name='accetta_contratto_dipendente'),
    path('contratto/', candidato_mio_contratto, name='candidato_mio_contratto'),
    path('buste-paga/', candidato_mie_buste_paga, name='candidato_mie_buste_paga'),
    path('presenze/', candidato_mie_presenze, name='candidato_mie_presenze'),
    path('presenze/timbra/', candidato_timbratura_geo, name='candidato_timbratura_geo'),
    path('richieste/', candidato_mie_richieste, name='candidato_mie_richieste'),
    path('richieste/nuova/', candidato_nuova_richiesta, name='candidato_nuova_richiesta'),
    path('richieste/ricevute/<int:richiesta_id>/', candidato_dettaglio_richiesta_ricevuta, name='candidato_dettaglio_richiesta_ricevuta'),
    path('documenti/', candidato_miei_documenti, name='candidato_miei_documenti'),
]
