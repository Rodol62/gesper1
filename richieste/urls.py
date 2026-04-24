from django.urls import path
from . import views

urlpatterns = [
    path('', views.lista_richieste, name='index_richieste'),
    path('', views.lista_richieste, name='lista_richieste'),
    path('invia/', views.invia_richiesta, name='invia_richiesta'),
    path('<int:richiesta_id>/', views.dettaglio_richiesta, name='dettaglio_richiesta'),
    path('<int:richiesta_id>/rispondi/', views.rispondi_richiesta, name='rispondi_richiesta'),
    path('<int:richiesta_id>/approva/', views.approva_richiesta, name='approva_richiesta'),
    path('<int:richiesta_id>/rifiuta/', views.rifiuta_richiesta, name='rifiuta_richiesta'),
    path('<int:richiesta_id>/chiudi/', views.chiudi_richiesta, name='chiudi_richiesta'),
    path('<int:richiesta_id>/elimina/', views.elimina_richiesta, name='elimina_richiesta'),
]
