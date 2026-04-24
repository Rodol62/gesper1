from django.urls import path

from . import views

urlpatterns = [
    path('', views.indice_guida, name='guida_indice'),
    path('m/<slug:codice_modulo>/', views.guida_modulo, name='guida_modulo'),
]
