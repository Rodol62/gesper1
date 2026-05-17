from django.urls import path
from . import views

urlpatterns = [
    path('', views.lista_ruoli, name='lista_ruoli'),
    path('crea/', views.crea_ruolo, name='crea_ruolo'),
    path('modifica/<int:pk>/', views.modifica_ruolo, name='modifica_ruolo'),
    path('elimina/<int:pk>/', views.elimina_ruolo, name='elimina_ruolo'),
]