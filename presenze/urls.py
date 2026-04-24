from django.urls import path
from . import views

urlpatterns = [
    # ── Pianificazione annuale orari lavoro ───────────────────
    path('pianificazione-orari/', views.pianificazione_orari_annuale, name='pianificazione_orari_annuale'),
    path('pianificazione-orari/genera-teoriche/', views.genera_presenze_teoriche_mese, name='genera_presenze_teoriche_mese'),
    path('pianificazione-orari/db-parametri/', views.api_parametri_orari_mese, name='api_parametri_orari_mese'),

    # ── Lista dipendenti con accesso al calendario ──────────────
    path('', views.lista_dipendenti_presenze, name='index_presenze'),
    path('', views.lista_dipendenti_presenze, name='lista_presenze'),
    path('dipendenti/', views.lista_dipendenti_presenze, name='lista_dipendenti_presenze'),

    # ── Calendario mensile per dipendente ───────────────────────
    path('dipendente/<int:dipendente_id>/', views.calendario_presenze, name='calendario_presenze'),
    path('dipendente/<int:dipendente_id>/<int:anno>/<int:mese>/', views.calendario_presenze, name='calendario_presenze_mese'),

    # ── Salva/aggiorna/elimina giorno (form POST + AJAX) ────────
    path('dipendente/<int:dipendente_id>/salva/', views.salva_giorno, name='salva_giorno_presenza'),

    # ── Salva massivo (più giorni stessa causale) ────────────────
    path('dipendente/<int:dipendente_id>/salva-multiplo/', views.salva_multiplo, name='salva_multiplo_presenze'),

    # ── Aggiorna orario singolo giorno (AJAX griglia inline) ────
    path('dipendente/<int:dipendente_id>/orario/', views.aggiorna_orario_giorno, name='aggiorna_orario_giorno'),

    # ── Applica schema orario settimanale a tutto il mese ───────
    path('dipendente/<int:dipendente_id>/schema/', views.applica_schema_mese, name='applica_schema_mese'),

    # ── Riepilogo mese tutti i dipendenti ───────────────────────
    path('riepilogo/', views.riepilogo_mese, name='riepilogo_presenze_mese'),
    path('riepilogo/estendi-orari/', views.estendi_orari_riepilogo_mese, name='estendi_orari_riepilogo_mese'),

    # ── Export Excel per consulente ─────────────────────────────
    path('export/excel/', views.export_excel_presenze, name='export_presenze_excel'),

    # ── Riepilogo mensile motore (HR/Admin) ─────────────────────
    path('motore/', views.riepilogo_mensile_motore, name='riepilogo_mensile_motore'),
    path('motore/anteprima/<int:dip_id>/<int:anno>/<int:mese>/', views.anteprima_cedolino_riepilogo, name='anteprima_cedolino_riepilogo'),

    # ── Libro monti (saldi, saldo iniziale, riconciliazione busta) ───────────
    path('monti/', views.monti_saldi, name='monti_saldi'),
    path('monti/export-riconciliazione.csv', views.export_monti_riconciliazione_csv, name='export_monti_riconciliazione_csv'),

    # ── Compatibilità con URL legacy ────────────────────────────
    path('inserisci/', views.seleziona_dipendente_presenza, name='seleziona_dipendente_presenza'),
    path('inserisci/<int:dipendente_id>/', views.inserisci_presenza, name='inserisci_presenza'),
]
