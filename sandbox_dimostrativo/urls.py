from django.urls import path

from . import views

urlpatterns = [
    path("sessione/attiva/", views.sandbox_sessione_attiva, name="sandbox_sessione_attiva"),
    path("sessione/disattiva/", views.sandbox_sessione_disattiva, name="sandbox_sessione_disattiva"),
]
