from django.urls import path
from . import views

urlpatterns = [
    path('attivita/',              views.lista_log_attivita,    name='lista_log_attivita'),
    path('errori/',                views.lista_log_errori,      name='lista_log_errori'),
    path('errori/<int:errore_id>/',views.dettaglio_errore,      name='dettaglio_errore'),
    path('errori/<int:errore_id>/risolto/', views.segna_errore_risolto, name='segna_errore_risolto'),
]
