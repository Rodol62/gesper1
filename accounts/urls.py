from django.urls import include, path, reverse_lazy
from django.views.generic import RedirectView
from django.contrib.auth import views as auth_views
from .views import (
    CustomLoginView, CustomPasswordResetView, login_email_otp, login_totp_web,
    profile, edit_profile, test_stato_utente, cambia_password_admin,
    dashboard_admin,
    admin_agenda_scadenze,
    admin_voci_cedolino_motore_v4_list,
    logout_view, contatta_sviluppatore,
)
from .views_gestione_db import (
    admin_data_overview,
    admin_table_detail,
    admin_db_record_detail,
    admin_db_record_edit,
    admin_db_record_create,
    admin_db_record_delete,
)
from .views_supervisore import dashboard_supervisore
from .views_registration import register
from .views_richieste_integrazione import lista_richieste_integrazione_candidato
from .views_admin_candidati import (
    lista_candidati,
    candidato_admin_dettaglio,
    invia_richiesta_integrazione_candidato,
    approva_richiesta_integrazione_candidato,
    convalida_candidato,
    revoca_convalida_candidato,
    crea_proposta_da_candidato,
    elimina_proposta_candidato,
    respingi_proposta_candidato,
    riapri_proposta_candidato,
    assegna_proposta_candidato,
    modifica_profilo_candidato,
    annulla_profilo_candidato,
    forza_convalida_candidato,
    forza_verifica_email_candidato,
    forza_profilo_completato_candidato,
    elimina_richiesta_integrazione_candidato,
    modifica_richiesta_integrazione_candidato,
    chiudi_richiesta_integrazione_candidato,
    forza_tutto_candidato,
    reset_email_verifica_candidato,
    aggiorna_campo_profilo_candidato,
    riallinea_anagrafica_candidato,
)
from .views_impostazioni import (
    impostazioni_sistema,
    geocode_impostazioni,
    maps_estrai_coordinate_impostazioni,
)
from .views_certificazione_firma import (
    certificazione_firma_pubblica,
    invia_certificazione_firma_candidato,
)
from .views_consulente import (
    consulente_dashboard,
    consulente_contratti,
    consulente_candidati,
    consulente_approva_proposta,
    consulente_proposta_detail,
    consulente_proposta_pdf,
    consulente_documenti,
    consulente_documenti_dipendente,
    consulente_presenze,
    consulente_presenze_export_csv,
    consulente_carica_documento,
    consulente_upload_buste_paga,
    consulente_upload_cud,
    consulente_partitario_paghe,
    consulente_riepilogo_f24_annuale,
    consulente_import_pdf_unico,
    consulente_registro_studio,
    consulente_posizione_contabile,
    consulente_posizione_quadratura,
    consulente_piano_allocazione_bonifici,
    consulente_posizione_libro,
    consulente_posizione_libro_excel,
    consulente_posizione_libro_pdf,
    consulente_posizione_pagamenti,
    consulente_pagamenti_allega_pdf_movimento,
    consulente_pagamenti_elimina_movimento,
    consulente_pagamenti_rimuovi_pdf_movimento,
    consulente_posizione_proforma,
    consulente_proforma_allega_pdf_movimento,
    consulente_proforma_rimuovi_pdf_movimento,
    consulente_report_aggancia_csv,
)

urlpatterns = [
    # ── Autenticazione ──────────────────────────────────────────
    path('login/', CustomLoginView.as_view(), name='login'),
    path('login/email-otp/', login_email_otp, name='login_email_otp'),
    path('login/totp/', login_totp_web, name='login_totp_web'),
    path('logout/', logout_view, name='logout'),
    path('password_reset/', CustomPasswordResetView.as_view(), name='password_reset'),
    path('password_change/', auth_views.PasswordChangeView.as_view(
        template_name='registration/password_change_form.html',
        success_url=reverse_lazy('profile')), name='password_change'),
    path('password_reset/done/', auth_views.PasswordResetDoneView.as_view(
        template_name='registration/password_reset_done.html'), name='password_reset_done'),
    path('reset/<uidb64>/<token>/', auth_views.PasswordResetConfirmView.as_view(
        template_name='registration/password_reset_confirm.html'), name='password_reset_confirm'),
    path('reset/done/', auth_views.PasswordResetCompleteView.as_view(
        template_name='registration/password_reset_complete.html'), name='password_reset_complete'),

    # ── Registrazione interna (HR / admin) ─────────────────────
    path('register/', register, name='register'),
    path(
        'certificazione-firma/<str:token>/',
        certificazione_firma_pubblica,
        name='certificazione_firma_pubblica',
    ),

    # ── Profilo utente ─────────────────────────────────────────
    path('profile/', profile, name='profile'),
    path('sandbox/', include('sandbox_dimostrativo.urls')),
    path('edit_profile/', edit_profile, name='edit_profile'),
    path('test_stato_utente/', test_stato_utente, name='test_stato_utente'),
    path('cambia_password_admin/', cambia_password_admin, name='cambia_password_admin'),

    # ── Dashboard ──────────────────────────────────────────────
    path('dashboard_supervisore/', dashboard_supervisore, name='dashboard_supervisore'),
    path('dashboard_admin/', dashboard_admin, name='dashboard_admin'),
    path('dashboard_admin/agenda/', admin_agenda_scadenze, name='admin_agenda_scadenze'),
    path(
        'dashboard_admin/voci-cedolino-motore/',
        admin_voci_cedolino_motore_v4_list,
        name='admin_voci_cedolino_motore_v4',
    ),
    path('dashboard_admin/dati/', admin_data_overview, name='admin_data_overview'),
    path(
        'dashboard_admin/dati/<str:app_label>/<str:model_name>/nuovo/',
        admin_db_record_create,
        name='admin_db_record_create',
    ),
    path(
        'dashboard_admin/dati/<str:app_label>/<str:model_name>/<path:pk>/elimina/',
        admin_db_record_delete,
        name='admin_db_record_delete',
    ),
    path(
        'dashboard_admin/dati/<str:app_label>/<str:model_name>/<path:pk>/modifica/',
        admin_db_record_edit,
        name='admin_db_record_edit',
    ),
    path(
        'dashboard_admin/dati/<str:app_label>/<str:model_name>/<path:pk>/',
        admin_db_record_detail,
        name='admin_db_record_detail',
    ),
    path('dashboard_admin/dati/<str:app_label>/<str:model_name>/', admin_table_detail, name='admin_table_detail'),

    # ── Impostazioni di sistema ─────────────────────────────────
    path('impostazioni/', impostazioni_sistema, name='impostazioni_sistema'),
    path('impostazioni/geocode/', geocode_impostazioni, name='impostazioni_geocode'),
    path(
        'impostazioni/maps-coordinate/',
        maps_estrai_coordinate_impostazioni,
        name='impostazioni_maps_coordinate',
    ),

    # ── Contatto sviluppatore (footer) ──────────────────────────
    path('contatta-sviluppatore/', contatta_sviluppatore, name='contatta_sviluppatore'),

    # ── Gestione candidati (admin / HR) ────────────────────────
    path('candidati/', lista_candidati, name='lista_candidati'),
    path('candidati/<int:user_id>/', candidato_admin_dettaglio, name='candidato_admin_dettaglio'),
    path('candidati/<int:user_id>/richiesta-integrazione/', invia_richiesta_integrazione_candidato, name='invia_richiesta_integrazione_candidato'),
    path('candidati/<int:user_id>/richiesta-integrazione/<int:richiesta_id>/approva/', approva_richiesta_integrazione_candidato, name='approva_richiesta_integrazione_candidato'),
    path('candidati/<int:user_id>/richieste-integrazione/', lista_richieste_integrazione_candidato, name='lista_richieste_integrazione_candidato'),
    path('candidati/<int:user_id>/convalida/', convalida_candidato, name='convalida_candidato'),
    path('candidati/<int:user_id>/forza-convalida/', forza_convalida_candidato, name='forza_convalida_candidato'),
    path('candidati/<int:user_id>/revoca/', revoca_convalida_candidato, name='revoca_convalida_candidato'),
    path('candidati/<int:user_id>/proposta/', crea_proposta_da_candidato, name='crea_proposta_da_candidato'),
    path('candidati/<int:user_id>/proposta/<int:proposta_id>/elimina/', elimina_proposta_candidato, name='elimina_proposta_candidato'),
    path('candidati/<int:user_id>/proposta/<int:proposta_id>/respingi/', respingi_proposta_candidato, name='respingi_proposta_candidato'),
    path('candidati/<int:user_id>/proposta/<int:proposta_id>/riapri/', riapri_proposta_candidato, name='riapri_proposta_candidato'),
    path('candidati/<int:user_id>/assegna/', assegna_proposta_candidato, name='assegna_proposta_candidato'),
    path('candidati/<int:user_id>/modifica-profilo/', modifica_profilo_candidato, name='modifica_profilo_candidato'),
    path('candidati/<int:user_id>/annulla-profilo/', annulla_profilo_candidato, name='annulla_profilo_candidato'),
    path('candidati/<int:user_id>/forza-email/', forza_verifica_email_candidato, name='forza_verifica_email_candidato'),
    path('candidati/<int:user_id>/forza-profilo/', forza_profilo_completato_candidato, name='forza_profilo_completato_candidato'),
    path('candidati/<int:user_id>/richiesta-integrazione/<int:richiesta_id>/elimina/', elimina_richiesta_integrazione_candidato, name='elimina_richiesta_integrazione_candidato'),
    path('candidati/<int:user_id>/richiesta-integrazione/<int:richiesta_id>/modifica/', modifica_richiesta_integrazione_candidato, name='modifica_richiesta_integrazione_candidato'),
    path('candidati/<int:user_id>/richiesta-integrazione/<int:richiesta_id>/chiudi/', chiudi_richiesta_integrazione_candidato, name='chiudi_richiesta_integrazione_candidato'),
    path('candidati/<int:user_id>/forza-tutto/', forza_tutto_candidato, name='forza_tutto_candidato'),
    path('candidati/<int:user_id>/reset-email/', reset_email_verifica_candidato, name='reset_email_verifica_candidato'),
    path('candidati/<int:user_id>/aggiorna-campi/', aggiorna_campo_profilo_candidato, name='aggiorna_campo_profilo_candidato'),
    path('candidati/<int:user_id>/riallinea-anagrafica/', riallinea_anagrafica_candidato, name='riallinea_anagrafica_candidato'),
    path(
        'candidati/<int:user_id>/certificazione-firma/',
        invia_certificazione_firma_candidato,
        name='invia_certificazione_firma_candidato',
    ),

    # ── Interfaccia Consulente del Lavoro ───────────────────────
    path('consulente/', consulente_dashboard, name='consulente_dashboard'),
    path('consulente/contratti/', consulente_contratti, name='consulente_contratti'),
    path('consulente/candidati/', consulente_candidati, name='consulente_candidati'),
    path('consulente/candidati/<int:proposta_id>/approva/', consulente_approva_proposta, name='consulente_approva_proposta'),
    path('consulente/candidati/<int:proposta_id>/proposta/', consulente_proposta_detail, name='consulente_proposta_detail'),
    path('consulente/candidati/<int:proposta_id>/pdf/', consulente_proposta_pdf, name='consulente_proposta_pdf'),
    path('consulente/documenti/', consulente_documenti, name='consulente_documenti'),
    path('consulente/documenti/<int:dipendente_id>/', consulente_documenti_dipendente, name='consulente_documenti_dipendente'),
    path('consulente/presenze/', consulente_presenze, name='consulente_presenze'),
    path('consulente/presenze/export/csv/', consulente_presenze_export_csv, name='consulente_presenze_export_csv'),
    path('consulente/carica-documento/', consulente_carica_documento, name='consulente_carica_documento'),
    path('consulente/buste-paga/', consulente_upload_buste_paga, name='consulente_upload_buste_paga'),
    path('consulente/partitario-paghe/', consulente_partitario_paghe, name='consulente_partitario_paghe'),
    path('consulente/riepilogo-f24/', consulente_riepilogo_f24_annuale, name='consulente_riepilogo_f24_annuale'),
    path('consulente/cud/', consulente_upload_cud, name='consulente_upload_cud'),
    path('consulente/import-pdf/', consulente_import_pdf_unico, name='consulente_import_pdf_unico'),
    path('consulente/posizione-contabile/', consulente_posizione_contabile, name='consulente_posizione_contabile'),
    path(
        'consulente/posizione-contabile/quadratura/',
        consulente_posizione_quadratura,
        name='consulente_posizione_quadratura',
    ),
    path(
        'consulente/posizione-contabile/piano-bonifici/',
        consulente_piano_allocazione_bonifici,
        name='consulente_piano_allocazione_bonifici',
    ),
    path('consulente/posizione-contabile/proforma/', consulente_posizione_proforma, name='consulente_posizione_proforma'),
    path(
        'consulente/posizione-contabile/proforma/allega-pdf/<int:movimento_id>/',
        consulente_proforma_allega_pdf_movimento,
        name='consulente_proforma_allega_pdf_movimento',
    ),
    path(
        'consulente/posizione-contabile/proforma/rimuovi-pdf/<int:movimento_id>/',
        consulente_proforma_rimuovi_pdf_movimento,
        name='consulente_proforma_rimuovi_pdf_movimento',
    ),
    path('consulente/posizione-contabile/pagamenti/', consulente_posizione_pagamenti, name='consulente_posizione_pagamenti'),
    path(
        'consulente/posizione-contabile/pagamenti/allega-pdf/<int:movimento_id>/',
        consulente_pagamenti_allega_pdf_movimento,
        name='consulente_pagamenti_allega_pdf_movimento',
    ),
    path(
        'consulente/posizione-contabile/pagamenti/rimuovi-pdf/<int:movimento_id>/',
        consulente_pagamenti_rimuovi_pdf_movimento,
        name='consulente_pagamenti_rimuovi_pdf_movimento',
    ),
    path(
        'consulente/posizione-contabile/pagamenti/elimina/<int:movimento_id>/',
        consulente_pagamenti_elimina_movimento,
        name='consulente_pagamenti_elimina_movimento',
    ),
    path(
        'consulente/posizione-contabile/estratto-conto/',
        RedirectView.as_view(pattern_name='consulente_posizione_pagamenti', permanent=False),
        name='consulente_posizione_estratto',
    ),
    path('consulente/posizione-contabile/libro/', consulente_posizione_libro, name='consulente_posizione_libro'),
    path(
        'consulente/posizione-contabile/libro/export-excel/',
        consulente_posizione_libro_excel,
        name='consulente_posizione_libro_excel',
    ),
    path(
        'consulente/posizione-contabile/libro/stampa-pdf/',
        consulente_posizione_libro_pdf,
        name='consulente_posizione_libro_pdf',
    ),
    path(
        'consulente/posizione-contabile/report-aggancia-csv/',
        consulente_report_aggancia_csv,
        name='consulente_report_aggancia_csv',
    ),
    path(
        'consulente/partitario-consulente/',
        consulente_registro_studio,
        name='consulente_partitario_consulente',
    ),
    path(
        'consulente/registro-studio/',
        RedirectView.as_view(pattern_name='consulente_posizione_contabile', permanent=False),
        name='consulente_registro_studio',
    ),
]
