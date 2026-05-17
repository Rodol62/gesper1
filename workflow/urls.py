from django.urls import path

from . import views

app_name = 'workflow'

urlpatterns = [
    path('da-approvare/', views.lista_da_approvare, name='lista_da_approvare'),
    path(
        'azione/<int:approvazione_id>/<str:azione>/',
        views.azione_approvazione,
        name='azione_approvazione',
    ),
]
