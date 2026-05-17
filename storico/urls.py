from django.urls import path
from . import views

urlpatterns = [
    path('', views.storico_accessi_lista, name='storico_home'),
    path('libro_paga/', views.libro_paga_lista, name='libro_paga_lista'),
    path(
        'buste_paga/estrazione_dati/',
        views.estrazione_dati_buste_paga,
        name='estrazione_dati_buste_paga',
    ),
    path('libro_paga/svuota/', views.libro_paga_svuota, name='libro_paga_svuota'),
    path('libro_paga/ricarica/', views.libro_paga_ricarica_da_buste, name='libro_paga_ricarica_da_buste'),
    path('libro_paga/azienda/<int:azienda_id>/', views.libro_paga_azienda, name='libro_paga_azienda'),
    path('libro_paga/dipendente/<int:dipendente_id>/', views.libro_paga_dipendente, name='libro_paga_dipendente'),
    path('registro_unico/', views.registro_unico_lista, name='registro_unico_lista'),
    path('registro_unico/azienda/<int:azienda_id>/', views.registro_unico_azienda, name='registro_unico_azienda'),
    path('registro_unico/dipendente/<int:dipendente_id>/', views.registro_unico_dipendente, name='registro_unico_dipendente'),
    path('storico_accessi/', views.storico_accessi_lista, name='storico_accessi_lista'),
    path('storico_accessi/azienda/<int:azienda_id>/', views.storico_accessi_azienda, name='storico_accessi_azienda'),
    path('storico_accessi/dipendente/<int:dipendente_id>/', views.storico_accessi_dipendente, name='storico_accessi_dipendente'),
]