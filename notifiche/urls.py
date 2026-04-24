from django.urls import path
from . import views

urlpatterns = [
    path('', views.lista_notifiche, name='lista_notifiche'),
    path('<int:pk>/', views.dettaglio_notifica, name='dettaglio_notifica'),
]