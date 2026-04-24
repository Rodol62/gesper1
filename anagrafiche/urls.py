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
    path('aziende/', lista_aziende, name='lista_aziende'),
    path('aziende/nuova/', views.crea_azienda, name='crea_azienda'),
    path('aziende/<int:pk>/modifica/', views.modifica_azienda, name='modifica_azienda'),
]
