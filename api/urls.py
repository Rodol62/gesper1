from django.urls import path
from . import views

urlpatterns = [
    path('me/',                 views.api_me_view,           name='api_me'),
    path('auth/login/',        views.login_view,    name='api_login'),
    path('auth/recover-username/', views.recover_username_api, name='api_recover_username'),
    path('auth/register-candidato/request-otp/', views.register_candidato_request_otp_api, name='api_register_candidato_otp'),
    path('auth/register-candidato/complete/', views.register_candidato_complete_api, name='api_register_candidato_complete'),
    path('auth/refresh/',      views.refresh_view,  name='api_refresh'),
    path('auth/portal-session/', views.portal_session_view, name='api_portal_session'),
    path('auth/otp/verify/',   views.otp_verify,    name='api_otp_verify'),
    path('auth/2fa/setup/',    views.totp_setup,    name='api_2fa_setup'),
    path('auth/2fa/enable/',   views.totp_enable,   name='api_2fa_enable'),
    path('auth/2fa/disable/',  views.totp_disable,  name='api_2fa_disable'),
    path('auth/2fa/status/',   views.totp_status,   name='api_2fa_status'),
    path('checkin/',      views.checkin_view,  name='api_checkin'),
    path('checkout/',     views.checkout_view, name='api_checkout'),
    path('checkin/stato/',views.checkin_stato, name='api_checkin_stato'),
    path('presenze/',          views.presenze_view,  name='api_presenze'),
    path('candidato/profilo/',           views.candidato_profilo_api, name='api_candidato_profilo'),
    path('documenti/',                   views.documenti_view,       name='api_documenti'),
    path('contratto-rapporto/<int:rapporto_id>/download/', views.contratto_rapporto_download, name='api_contratto_rapporto_download'),
    path('proposta/<int:proposta_id>/download/', views.proposta_download, name='api_proposta_download'),
    path('documenti/<int:doc_id>/download/', views.documento_download, name='api_documento_download'),
    path('auth/password/',     views.cambio_password,name='api_password'),
    path('ferie/',             views.ferie_view,     name='api_ferie'),
    path('profilo/',             views.profilo_view,      name='api_profilo'),
    path('notifiche/',          views.notifiche_view,    name='api_notifiche'),
    # Push notifications
    path('push/vapid-public/',  views.push_vapid_public, name='api_push_vapid'),
    path('push/subscribe/',     views.push_subscribe,    name='api_push_subscribe'),
    path('push/unsubscribe/',   views.push_unsubscribe,  name='api_push_unsubscribe'),
]
