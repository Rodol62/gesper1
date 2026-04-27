from django.urls import path

from accounts.views_certificazione_firma import invia_certificazione_firma_dipendente

from . import views
from .views import lista_aziende

urlpatterns = [
        path('', views.lista_dipendenti, name='index_anagrafiche'),
    path('dipendenti/', views.lista_dipendenti, name='lista_dipendenti'),
    path('dipendenti/crea/', views.crea_dipendente, name='crea_dipendente'),
    path('dipendenti/<int:pk>/', views.dettaglio_dipendente, name='dettaglio_dipendente'),
    path(
        'dipendenti/<int:pk>/certificazione-firma/',
        invia_certificazione_firma_dipendente,
        name='invia_certificazione_firma_dipendente',
    ),
    path('dipendenti/<int:pk>/modifica/', views.modifica_dipendente, name='modifica_dipendente'),
    path('dipendenti/<int:pk>/elimina/', views.elimina_dipendente, name='elimina_dipendente'),
    path('dipendenti/<int:pk>/toggle-convalidato/', views.toggle_convalidato, name='toggle_convalidato'),
    path('api/regioni-italia/', views.api_regioni_italia, name='api_regioni_italia'),
    path('api/province-italia/', views.api_province_italia, name='api_province_italia'),
    path('api/comuni-italia/', views.api_comuni_italia, name='api_comuni_italia'),
    path('api/geocode-indirizzo/', views.api_geocode_indirizzo_anagrafiche, name='api_geocode_indirizzo_anagrafiche'),
    path('api/decodifica-cf/', views.api_decodifica_cf, name='api_decodifica_cf'),
    path(
        'dipendenti/<int:pk>/rapporti/<int:rapporto_id>/comunicazione-recesso-prova/',
        views.genera_comunicazione_recesso_prova,
        name='genera_comunicazione_recesso_prova',
    ),
    path(
        'dipendenti/<int:pk>/rapporti/<int:rapporto_id>/workflow-recesso-prova/',
        views.workflow_recesso_prova,
        name='workflow_recesso_prova',
    ),
    path('aziende/', lista_aziende, name='lista_aziende'),
    path('aziende/nuova/', views.crea_azienda, name='crea_azienda'),
    path('aziende/<int:pk>/modifica/', views.modifica_azienda, name='modifica_azienda'),
]
