from django.urls import path
from . import views

urlpatterns = [
    path('', views.lista_documenti, name='index_documenti'),
    path('', views.lista_documenti, name='lista_documenti'),
    path('buste-paga/', views.lista_buste_paga, name='lista_buste_paga_documenti'),
    path('f24/', views.lista_f24, name='lista_f24_documenti'),
    path('cud/', views.lista_cud, name='lista_cud_documenti'),
    path('upload/', views.upload_documento, name='upload_documento'),
    path('upload-buste-massivo/', views.upload_buste_paga_massivo, name='upload_buste_paga_massivo'),
    path('upload-personale/', views.upload_documento_personale, name='upload_documento_personale'),
    path('dipendente/<int:dipendente_id>/', views.documenti_dipendente_admin, name='documenti_dipendente_admin'),
    path('visualizza/<int:documento_id>/', views.visualizza_documento, name='visualizza_documento'),
    path('download/<int:documento_id>/', views.download_documento, name='download_documento'),
    path('elimina/<int:documento_id>/', views.elimina_documento, name='elimina_documento'),
    path('<str:legacy_filename>', views.legacy_documento_redirect, name='legacy_documento_redirect'),
]
