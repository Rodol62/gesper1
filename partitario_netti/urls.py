from django.urls import path

from . import views

urlpatterns = [
    path("stampa/", views.estratt_conto_stampa, name="partitario_netti_stampa"),
    path("", views.situazione_contabile_netti, name="partitario_netti_situazione"),
    path("sincronizza/", views.situazione_contabile_sincronizza, name="partitario_netti_sincronizza"),
    path("pagamento/nuovo/", views.pagamento_netto_nuovo, name="partitario_netti_pagamento_nuovo"),
    path("pagamento/<int:movimento_id>/modifica/", views.pagamento_netto_modifica, name="partitario_netti_pagamento_modifica"),
    path(
        "pagamento/<int:movimento_id>/ricevuta-contanti.pdf",
        views.ricevuta_contanti_pdf,
        name="partitario_netti_ricevuta_contanti_pdf",
    ),
    path(
        "pagamento/<int:movimento_id>/ricevuta-contanti/pubblica/",
        views.ricevuta_contanti_pubblica_dipendente,
        name="partitario_netti_ricevuta_contanti_pubblica",
    ),
    path(
        "pagamento/<int:movimento_id>/ricevuta-contanti/revoca/",
        views.ricevuta_contanti_revoca_dipendente,
        name="partitario_netti_ricevuta_contanti_revoca",
    ),
    path(
        "pagamento/<int:movimento_id>/ricevuta-contanti/invia/",
        views.ricevuta_contanti_invia,
        name="partitario_netti_ricevuta_contanti_invia",
    ),
    path("pagamento/<int:movimento_id>/elimina/", views.pagamento_netto_elimina, name="partitario_netti_pagamento_elimina"),
]
