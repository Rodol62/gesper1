from django.urls import path
from django.views.generic import RedirectView

from . import views
from . import views_simulazione_2026
from . import views_calendario
from . import views_simulatore
from . import views_simulazione_proposta

urlpatterns = [
    path('', views.lista_proposte, name='lista_proposte_assunzione'),
    path('centro/', views.centro_rapporti_lavoro, name='centro_rapporti_lavoro'),
    path('simulazione/', views.simulazione_organico, name='simulazione_organico'),
    path('simulazione/calendario/', views.simulazione_organico_calendario, name='simulazione_organico_calendario'),
    path('simulazione/risultato/', views.simulazione_organico_risultato, name='simulazione_organico_risultato'),
    path('simulazione/<int:simulazione_id>/elimina/', views.simulazione_organico_elimina, name='simulazione_organico_elimina'),
    path('simulazione/pdf/', views.simulazione_organico_pdf, name='simulazione_organico_pdf'),
    path('simulazione/excel/', views.simulazione_organico_excel, name='simulazione_organico_excel'),
    path('simulatore-paga/', views_simulatore.simulatore_paga, name='simulatore_paga'),
    path('simulatore-paga/scenari/<int:scenario_id>/carica/', views_simulatore.carica_scenario_salvato, name='carica_scenario_paga'),
    path('simulatore-paga/scenari/<int:scenario_id>/elimina/', views_simulatore.elimina_scenario_salvato, name='elimina_scenario_paga'),
    path('simulazione-annua/', views_simulazione_2026.simulazione_2026_config, name='simulazione_2026_config'),
    path('simulazione-annua/risultato/', views_simulazione_2026.simulazione_2026_risultato, name='simulazione_2026_risultato'),
    path('simulazione-annua/excel/', views_simulazione_2026.simulazione_2026_excel, name='simulazione_2026_excel'),
    path('simulazione-annua/crea-proposte/', views_simulazione_2026.simulazione_2026_crea_proposte, name='simulazione_2026_crea_proposte'),
    # Legacy URL (bookmark / link esterni)
    path('simulazione-2026/', RedirectView.as_view(pattern_name='simulazione_2026_config', permanent=False)),
    path('simulazione-2026/risultato/', RedirectView.as_view(pattern_name='simulazione_2026_risultato', permanent=False)),
    path('simulazione-2026/excel/', RedirectView.as_view(pattern_name='simulazione_2026_excel', permanent=False)),
    path('simulazione-2026/crea-proposte/', RedirectView.as_view(pattern_name='simulazione_2026_crea_proposte', permanent=False)),
    path('economici/', views.gestione_riferimenti_economici, name='gestione_riferimenti_economici'),
    path('economici/parametri/nuovo/', views.parametro_economico_nuovo, name='parametro_economico_nuovo'),
    path('economici/parametri/<int:parametro_id>/modifica/', views.parametro_economico_modifica, name='parametro_economico_modifica'),
    path('economici/parametri/<int:parametro_id>/elimina/', views.parametro_economico_elimina, name='parametro_economico_elimina'),
    path('economici/parametri/<int:parametro_id>/dettaglio/', views.dettaglio_calcolo_economico, name='dettaglio_calcolo_economico'),
    path('economici/parametri/elimina-multipli/', views.parametri_economici_elimina_multipli, name='parametri_economici_elimina_multipli'),
    path('economici/regole/nuova/', views.regola_normativa_nuova, name='regola_normativa_nuova'),
    path('economici/regole/<int:regola_id>/modifica/', views.regola_normativa_modifica, name='regola_normativa_modifica'),
    path('economici/regole/<int:regola_id>/elimina/', views.regola_normativa_elimina, name='regola_normativa_elimina'),
    path('economici/regole/elimina-multiple/', views.regole_normative_elimina_multiple, name='regole_normative_elimina_multiple'),
    path('legacy/allineamento/', views.lista_legacy_da_allineare, name='lista_legacy_allineamento'),
    path('istruttoria/assunzione/', views.istruttoria_assunzione, name='istruttoria_assunzione'),
    path('proposte/crea/', views.crea_proposta, name='crea_proposta_assunzione'),
    path('proposte/<int:proposta_id>/modifica/', views.modifica_proposta, name='modifica_proposta_assunzione'),
    path('proposte/<int:proposta_id>/elimina/', views.elimina_proposta, name='elimina_proposta_assunzione'),
    path('proposte/<int:proposta_id>/', views.dettaglio_proposta, name='dettaglio_proposta'),
    path('proposte/<int:proposta_id>/simulazione-economica/', views_simulazione_proposta.simulazione_economica_proposta, name='simulazione_economica_proposta'),
    path('proposte/<int:proposta_id>/pdf/', views.proposta_pdf, name='proposta_pdf'),
    path('proposte/<int:proposta_id>/mansionario/', views.proposta_mansionario_file, name='proposta_mansionario_file'),
    path('addenda/<int:addendum_id>/allegato/', views.addendum_allegato_file, name='addendum_allegato_file'),
    path('proposte/<int:proposta_id>/stampa/', views.proposta_stampa, name='proposta_stampa'),
    path('contratti/scadenze/', views.lista_contratti_scadenza, name='lista_contratti_scadenza'),
    path('contratti/<int:contratto_id>/pdf/', views.contratto_pdf, name='contratto_pdf'),
    path('contratti/<int:contratto_id>/stampa/', views.contratto_stampa, name='contratto_stampa'),
    path('contratti/<int:contratto_id>/addendum/nuovo/', views.addendum_contratto_nuovo, name='addendum_contratto_nuovo'),
    path('contratti/<int:contratto_id>/modifica/', views.modifica_contratto, name='modifica_contratto'),
    path('contratti/<int:contratto_id>/', views.dettaglio_contratto, name='dettaglio_contratto'),
    # ── Workflow proposta (nuovo flusso) ────────────────────────────────────
    path('proposte/<int:proposta_id>/firma/', views.firma_proposta_candidato, name='firma_proposta_candidato'),
    path('proposte/<int:proposta_id>/rifiuta/', views.rifiuta_proposta_dipendente, name='rifiuta_proposta_dipendente'),
    path('proposte/<int:proposta_id>/invia/', views.invia_proposta_al_dipendente, name='invia_proposta_al_dipendente'),
    path('proposte/<int:proposta_id>/invia-documenti-email/', views.invia_documenti_proposta_email, name='invia_documenti_proposta_email'),
    path('proposte/<int:proposta_id>/firma-admin/', views.firma_definitiva_admin, name='firma_definitiva_admin'),
    path('proposte/<int:proposta_id>/trasforma-contratto/', views.trasforma_proposta_in_contratto, name='trasforma_proposta_in_contratto'),
    path('proposte/<int:proposta_id>/rifiuta-admin/', views.rifiuta_proposta_admin, name='rifiuta_proposta_admin'),
    # ── Legacy URLs (mantenuti per compatibilità) ────────────────────────────
    path('proposte/<int:proposta_id>/accetta/', views.accetta_proposta_dipendente, name='accetta_proposta_dipendente'),
    path('proposte/<int:proposta_id>/approva-admin/', views.approva_proposta_admin, name='approva_proposta_admin'),
    path('proposte/<int:proposta_id>/converti/', views.converti_proposta_in_contratto, name='converti_proposta_in_contratto'),
    # Calendario lavorativo aziendale
    path('calendario/', views_calendario.calendario_aziendale, name='calendario_aziendale'),
    path('calendario/<int:anno>/', views_calendario.calendario_aziendale, name='calendario_aziendale_anno'),
    path('calendario/salva-mese/', views_calendario.calendario_salva_mese, name='calendario_salva_mese'),
    path('calendario/copia-mese/', views_calendario.calendario_copia_mese, name='calendario_copia_mese'),
    path('calendario/festivita/aggiungi/', views_calendario.festivita_aziendale_aggiungi, name='festivita_aziendale_aggiungi'),
    path('calendario/festivita/<int:fest_id>/elimina/', views_calendario.festivita_aziendale_elimina, name='festivita_aziendale_elimina'),
    # API AJAX per form dinamico
    path('api/ccnl-levels/', views.api_ccnl_levels, name='api_ccnl_levels'),
    path('api/ccnl-parametri/', views.api_ccnl_parametri, name='api_ccnl_parametri'),
    path('api/presenze-simulatore/', views_simulatore.api_presenze_simulatore, name='api_presenze_simulatore'),
    path('api/prefill-simulatore/', views_simulatore.api_prefill_simulatore_form, name='api_prefill_simulatore_form'),
    path('api/mansioni-per-livello/', views.api_mansioni_per_livello, name='api_mansioni_per_livello'),
]
